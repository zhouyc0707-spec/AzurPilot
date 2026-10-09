"""只读规划详情的观测口径、配置组挂载及翻译完整性。"""

import copy
import json
import threading
import unittest
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.webui.app_island_planner_report import (
    TEXT,
    IslandPlannerReportMixin,
    build_island_planner_report_html,
)
from module.webui.app_task_config import TaskConfigMixin

import pywebio.session as pywebio_session
from pywebio.output import put_text
from pywebio.session import local
from pywebio.session.threadbased import ThreadBasedSession


class TableReader(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.rows = []
        self.row = []
        self.cell = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.row = []
        elif tag == 'td':
            self.cell = ''

    def handle_data(self, data):
        if self.cell is not None:
            self.cell += data

    def handle_endtag(self, tag):
        if tag == 'td' and self.cell is not None:
            self.row.append(self.cell)
            self.cell = None
        elif tag == 'tr' and self.row:
            self.rows.append(self.row)


def make_report():
    return {
        'version': 1, 'enabled': True, 'legacy': False,
        'generated_at': '2026-10-09 12:30:00', 'daily_revenue': 2200, 'daily_profit': 1000.5,
        'items': [{'id': 100, 'name': '测试物品', 'target': 100, 'floor': 5,
                   'buffer': 10, 'reserve': 20, 'demand': 30, 'idle_per_day': 3.5}],
        'production': [{'recipe_id': 10, 'name': '测试配方', 'place': '测试作坊', 'batches_per_day': .00125}],
        'menus': [{'id': 1, 'name': '测试店铺', 'capacity': 80,
                   'items': [{'id': 100, 'name': '测试物品', 'per_day': 25}]}],
        'observations': {'100': {'stock': 20, 'at': '2026-10-09 12:31:00', 'source': '库存巡检'}},
        'dispatches': {'100': {'amount': 500, 'at': '2026-10-09 12:32:00', 'source': '作坊下单'}},
    }


class TestIslandPlannerReportRendering(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.webui.app_island_planner_report.t', side_effect=lambda key: key))

    def test_targets_and_last_order_never_deduct_observed_shortfall(self):
        report = make_report()
        before = copy.deepcopy(report)

        html = build_island_planner_report_html(report)
        rows = TableReader(html).rows

        self.assertEqual(rows[0], ['测试物品 (100)', '100', '5', '10', '20', '30', '3.5'])
        self.assertEqual(rows[1], ['测试物品 (100)', '20', '2026-10-09 12:31:00', '库存巡检',
                                   '80', '500', '2026-10-09 12:32:00', '作坊下单'])
        self.assertEqual(rows[2], ['测试配方 (10)', '测试作坊', '0.00125'])
        self.assertEqual(rows[3], ['测试店铺 (1)', '80', '测试物品 (100)', '25'])
        self.assertIn('最近下单预计产出', html)
        self.assertIn('不抵扣现货缺口', html)
        self.assertNotIn('已生产', html)
        self.assertNotIn('当前在产', html)
        self.assertEqual(report, before)

    def test_zero_is_confirmed_but_missing_invalid_or_stale_stock_needs_inspection(self):
        for observation, expected_stock, expected_shortfall in (
            ({'stock': 0, 'at': '2026-10-09 12:31:00'}, '0', '100'),
            (None, '待巡检', '待巡检'),
            ({'stock': 999, 'at': '2026-10-09 12:31:00', 'stale': True}, '待复核', '待复核'),
            ({'stock': None, 'at': '2026-10-09 12:31:00'}, '待复核', '待复核'),
            ({'stock': -1, 'at': '2026-10-09 12:31:00'}, '待复核', '待复核'),
            ({'stock': 20, 'at': ''}, '待复核', '待复核'),
        ):
            with self.subTest(observation=observation):
                report = make_report()
                report['observations'] = {'100': observation} if observation is not None else {}
                row = next(row for row in TableReader(build_island_planner_report_html(report)).rows if len(row) == 8)
                self.assertEqual((row[1], row[4]), (expected_stock, expected_shortfall))
                self.assertEqual(row[5], '500')

    def test_legacy_disabled_and_empty_report_explain_missing_detail_without_inventing_values(self):
        report = make_report()
        report.update(legacy=True, enabled=False, generated_at='', daily_revenue=None,
                      daily_profit=None, production=[], observations={}, dispatches={})
        html = build_island_planner_report_html(report)
        self.assertIn('自动规划已关闭', html)
        self.assertIn('旧计划仅能恢复目标和菜单', html)
        self.assertIn('理论日均收入：-', html)
        self.assertIn('待巡检', html)
        self.assertIn('暂无下单记录', html)
        self.assertNotIn('测试配方', html)
        self.assertIn('暂无规划详情', build_island_planner_report_html(None))
        self.assertIn('暂无规划详情', build_island_planner_report_html({}))

    def test_report_text_is_escaped_and_partial_optional_sections_are_safe(self):
        report = make_report()
        report['items'][0]['name'] = '<script>unsafe</script>'
        report['observations']['100']['source'] = '<b>source</b>'
        report.update(production=None, menus=None, dispatches=[])
        html = build_island_planner_report_html(report)
        self.assertIn('&lt;script&gt;unsafe&lt;/script&gt;', html)
        self.assertIn('&lt;b&gt;source&lt;/b&gt;', html)
        self.assertNotIn('<script>', html)

    def test_raw_config_report_is_read_without_writes_and_built_as_closed_collapse(self):
        report = make_report()
        config = {'IslandPlan': {'IslandProductionPlanner': {
            'Enabled': True, 'PlanFingerprint': 'fixture', 'PlannerReport': json.dumps(report),
        }}}
        before = copy.deepcopy(config)
        view = object.__new__(IslandPlannerReportMixin)
        with patch('module.webui.app_island_planner_report.put_html') as put_html, \
                patch('module.webui.app_island_planner_report.put_collapse') as collapse:
            view._build_island_planner_details(config)
        self.assertEqual(collapse.call_args.kwargs['title'], '查看规划详情')
        self.assertFalse(collapse.call_args.kwargs['open'])
        self.assertEqual(collapse.call_args.kwargs['content'], [put_html.return_value])
        self.assertIn('测试物品', put_html.call_args.args[0])
        self.assertEqual(config, before)


class TestIslandPlannerReportMount(unittest.TestCase):
    def test_real_output_tree_keeps_summary_and_collapse_in_one_batch_without_detail_pins(self):
        commands, watchers = [], []
        closed = threading.Event()
        previous_classes = pywebio_session._active_session_cls.copy()

        def collect_commands(session):
            batch = session.get_task_commands()
            commands.extend(batch if isinstance(batch, list) else [batch])

        def render():
            view = object.__new__(TaskConfigMixin)
            local.gui = view
            view.alas_name = 'fixture'
            config = {'IslandPlan': {'IslandProductionPlanner': {
                'Enabled': True, 'PlanFingerprint': 'fixture', 'PlannerStatus': '已生成摘要',
                'PlannerReport': json.dumps(make_report()),
            }}}
            view.alas_config = SimpleNamespace(read_file=lambda _: config)
            view.ALAS_ARGS = {'IslandPlan': {'IslandProductionPlanner': {
                'PlannerStatus': {'type': 'state', 'value': ''},
                'PlannerReport': {'type': 'textarea', 'value': '{}', 'display': 'hide', 'persist': True},
            }}}
            view.init_menu = lambda name: None
            view.set_title = lambda text: None
            view._bind_config_watcher = watchers.append
            view._build_navigator = lambda group: put_text(group[0])
            view.alas_set_group('IslandPlan')

        pywebio_session._active_session_cls[:] = [ThreadBasedSession]
        try:
            with patch('module.webui.app_task_config.t',
                       side_effect=lambda key, *args: '' if key.endswith('.help') else key), \
                    patch('module.webui.app_island_planner_report.t', side_effect=lambda key: key):
                ThreadBasedSession(render, session_info=SimpleNamespace(),
                                   on_task_command=collect_commands, on_session_close=closed.set)
                self.assertTrue(closed.wait(timeout=3))
        finally:
            pywebio_session._active_session_cls[:] = previous_classes

        outputs = [command for command in commands if command.get('command') == 'output']
        self.assertEqual(len(outputs), 1)
        serialized = json.dumps(outputs[0]['spec'], ensure_ascii=False)
        self.assertIn('已生成摘要', serialized)
        self.assertIn('查看规划详情', serialized)
        self.assertIn('collapse', serialized)
        self.assertIn('测试物品', serialized)
        self.assertNotIn('IslandPlan_IslandProductionPlanner_PlannerReport', serialized)
        self.assertEqual(watchers, [['IslandPlan', 'IslandProductionPlanner', 'PlannerStatus']])

    def test_only_island_summary_has_details_without_new_watchers_or_config_fields(self):
        summary = Mock(name='existing_summary')
        details = Mock(name='readonly_details')
        group_schema = {'PlannerStatus': {'type': 'state', 'value': ''},
                        'PlannerReport': {'type': 'textarea', 'value': '{}', 'display': 'hide', 'persist': True}}
        config = {'IslandPlan': {'IslandProductionPlanner': {'PlannerStatus': '已生成摘要', 'PlannerReport': '{}'}}}
        view = object.__new__(TaskConfigMixin)
        view.ALAS_ARGS = {'IslandPlan': {'IslandProductionPlanner': group_schema}}
        view._translated_text = Mock(side_effect=lambda key, fallback: fallback)
        view._build_island_planner_details = Mock(return_value=details)
        with patch('module.webui.app_task_config.put_output', return_value=summary) as put_output, \
                patch('module.webui.app_task_config.put_scope') as put_scope, \
                patch('module.webui.app_task_config.put_html'), \
                patch('module.webui.app_task_config.put_text'), \
                patch('module.webui.app_task_config.t', return_value=''):
            _, watchers, count = view._build_config_group(['IslandProductionPlanner'], group_schema, config, 'IslandPlan')
            field_content = put_scope.call_args_list[0].kwargs['content']
            self.assertEqual(field_content, [summary, details])
            self.assertEqual(watchers, [['IslandPlan', 'IslandProductionPlanner', 'PlannerStatus']])
            self.assertEqual(count, 1)
            self.assertEqual(put_output.call_count, 1)
            self.assertEqual(put_output.call_args.args[0]['value'], '已生成摘要')
            view._build_island_planner_details.assert_called_once_with(config)

            view._build_island_planner_details.reset_mock()
            view._build_config_group(['IslandProductionPlanner'], group_schema, config, 'OtherTask')
            view._build_island_planner_details.assert_not_called()

    def test_all_report_labels_and_parameter_help_are_translated_in_five_languages(self):
        root = Path(__file__).resolve().parents[1] / 'module' / 'config' / 'i18n'
        for language in ('zh-CN', 'zh-MIAO', 'en-US', 'ja-JP', 'zh-TW'):
            with self.subTest(language=language):
                data = json.loads((root / f'{language}.json').read_text(encoding='utf-8'))
                labels = data['Gui']['IslandPlannerReport']
                self.assertEqual(set(labels), set(TEXT))
                self.assertTrue(all(value and not value.startswith('Gui.') for value in labels.values()))
                parameter = data['IslandProductionPlanner']['PlannerReport']
                self.assertTrue(all(parameter[key] and not parameter[key].startswith('IslandProductionPlanner.')
                                    for key in ('name', 'help')))


if __name__ == '__main__':
    unittest.main()
