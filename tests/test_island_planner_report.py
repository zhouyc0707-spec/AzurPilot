"""规划详情仅保存展示事实，不把下单预计量当现货或改变生产决策。"""

import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from module.config.deep import deep_set
from module.island.planner_report import (
    get_planner_report, invalidate_planner_stocks, record_planner_dispatch, record_planner_stocks,
)
from module.island.production_planner import CONFIG_PREFIX, IslandPlanningError, IslandProductionPlanner
from tests.test_island_production_planner import MemoryConfig


class PlannerReportTests(unittest.TestCase):
    def setUp(self):
        self.config = MemoryConfig()
        self.time = datetime(2026, 10, 9, 12)
        self.enterContext(patch('module.island.planner_report.now', return_value=self.time))
        self.enterContext(patch('module.island.production_planner.now', return_value=self.time))
        self.calculator = IslandProductionPlanner(self.config).run(current_time=self.time)

    def report(self):
        return get_planner_report(self.config)

    def test_generated_details_include_targets_recipes_menus_and_no_fabricated_progress(self):
        report = self.report()
        self.assertFalse(report['legacy'])
        self.assertEqual(report['generated_at'], '2026-10-09 12:00:00')
        self.assertEqual(report['daily_profit'], self.calculator.daily_profit)
        self.assertTrue(report['production'])
        self.assertEqual(len(report['menus']), 5)
        self.assertTrue(any(menu['items'] for menu in report['menus']))
        targets = {row['id']: row['target'] for row in report['items']}
        for item, target in self.calculator.local_targets.items():
            self.assertEqual(targets[int(item)], target)
        self.assertEqual(report['observations'], {})
        self.assertEqual(report['dispatches'], {})
        self.assertIn('小麦', [row['name'] for row in report['items']])

    def test_real_zero_unknown_and_dispatch_are_separate_and_do_not_modify_targets(self):
        targets = self.config.values[f'{CONFIG_PREFIX}.PlannerTargets']
        record_planner_stocks(self.config, {2000: 0, 2001: None}, '测试配方页')
        record_planner_dispatch(self.config, {2000: 324}, '测试确认下单')
        record_planner_dispatch(self.config, {2000: 216}, '下一岗位确认下单')
        report = self.report()
        self.assertEqual(report['observations']['2000']['stock'], 0)
        self.assertNotIn('2001', report['observations'])
        self.assertEqual(report['dispatches']['2000']['amount'], 216)
        self.assertEqual(report['observations']['2000']['at'], '2026-10-09 12:00:00')
        self.assertEqual(self.config.values[f'{CONFIG_PREFIX}.PlannerTargets'], targets)

    def test_consumption_marks_observation_stale_and_later_actual_read_replaces_it(self):
        record_planner_stocks(self.config, {2000: 500}, '测试详情页')
        invalidate_planner_stocks(self.config, [2000], '订单已交付')
        self.assertTrue(self.report()['observations']['2000']['stale'])
        self.assertEqual(self.report()['observations']['2000']['stock'], 500)
        record_planner_stocks(self.config, {2000: 310}, '测试复检')
        observation = self.report()['observations']['2000']
        self.assertFalse(observation['stale'])
        self.assertEqual(observation['stock'], 310)

    def test_new_plan_clears_old_observations_and_failure_keeps_old_details(self):
        record_planner_stocks(self.config, {2000: 500}, '测试详情页')
        record_planner_dispatch(self.config, {2000: 324}, '测试确认下单')
        self.config.values[f'{CONFIG_PREFIX}.TaskTarget'] = 'not a mapping'
        before = copy.deepcopy(self.report())
        with self.assertRaises(IslandPlanningError):
            IslandProductionPlanner(self.config).run(current_time=self.time)
        self.assertEqual(self.report(), before)
        self.assertTrue(self.config.values[f'{CONFIG_PREFIX}.PlannerStatus'].startswith('规划失败'))
        self.config.values[f'{CONFIG_PREFIX}.TaskTarget'] = '{}'
        IslandProductionPlanner(self.config).run(current_time=self.time + timedelta(days=1))
        self.assertEqual(self.report()['observations'], {})
        self.assertEqual(self.report()['dispatches'], {})

    def test_legacy_plan_can_be_read_without_writes_or_scanning(self):
        self.config.values.pop(f'{CONFIG_PREFIX}.PlannerReport')
        self.config.values['IslandBusiness.IslandBusinessShop1.Grade'] = 'gold'
        raw = {}
        for path, value in self.config.values.items():
            deep_set(raw, path, copy.deepcopy(value))
        before = copy.deepcopy(raw)
        report = get_planner_report(raw)
        self.assertTrue(report['legacy'])
        self.assertEqual(report['generated_at'], '')
        self.assertIsNone(report['daily_profit'])
        self.assertEqual(report['production'], [])
        self.assertTrue(report['items'])
        self.assertEqual(raw, before)
        # 原字典适配器必须读取实际店铺等级，不能用 cross_get 缺失后的默认铜牌。
        from module.island.order_stock import get_restaurant_capacity
        self.assertEqual(report['menus'][0]['capacity'], get_restaurant_capacity(self.config, 601))

    def test_disabled_mode_retains_view_but_does_not_record_progress(self):
        self.config.values[f'{CONFIG_PREFIX}.Enabled'] = False
        report = self.report()
        self.assertFalse(report['enabled'])
        self.assertFalse(record_planner_stocks(self.config, {2000: 0}, '关闭后不记录'))
        self.assertEqual(self.report(), report)

    def test_reporting_write_failure_does_not_interrupt_game_flow(self):
        with patch.object(self.config, 'cross_set', side_effect=OSError('测试磁盘故障')):
            self.assertFalse(record_planner_stocks(self.config, {2000: 5}, '测试观测'))
        self.assertEqual(self.report()['observations'], {})

    def test_report_generation_failure_keeps_valid_plan_and_clears_old_observations(self):
        record_planner_stocks(self.config, {2000: 500}, '旧方案观测')
        with patch('module.island.planner_report.build_planner_report', side_effect=ValueError('展示格式错误')):
            calculator = IslandProductionPlanner(self.config).run(current_time=self.time)
        self.assertTrue(calculator.lp_success)
        self.assertTrue(self.config.values[f'{CONFIG_PREFIX}.PlannerStatus'].startswith('已生成'))
        self.assertEqual(json.loads(self.config.values[f'{CONFIG_PREFIX}.PlannerTargets']), calculator.local_targets)
        self.assertTrue(self.report()['legacy'])
        self.assertEqual(self.report()['observations'], {})

    def test_report_serialization_failure_is_isolated_before_any_plan_export(self):
        record_planner_stocks(self.config, {2000: 500}, '旧方案观测')
        with patch('module.island.planner_report.build_planner_report', return_value={'bad': {1, 2}}):
            calculator = IslandProductionPlanner(self.config).run(current_time=self.time)
        self.assertTrue(calculator.lp_success)
        self.assertTrue(self.config.values[f'{CONFIG_PREFIX}.PlannerStatus'].startswith('已生成'))
        self.assertEqual(json.loads(self.config.values[f'{CONFIG_PREFIX}.PlannerTargets']), calculator.local_targets)
        self.assertEqual(self.config.values[f'{CONFIG_PREFIX}.PlannerReport'], '{}')
        self.assertTrue(self.report()['legacy'])
        self.assertEqual(self.report()['observations'], {})


class PlannerReportApiTests(unittest.TestCase):
    def test_legacy_details_are_derived_only_in_response_and_revision_stays_original(self):
        from module.api.config_service import ConfigService
        from tests.test_api import fixture

        with tempfile.TemporaryDirectory(prefix='island-report-api-') as folder:
            service = ConfigService(fixture(folder))
            path = service.path('testpilot')
            data = json.loads(path.read_text(encoding='utf-8'))
            data['IslandPlan'] = {'IslandProductionPlanner': {
                'Enabled': True, 'PlanFingerprint': 'legacy-plan', 'PlannerTargets': '{"2000": 500}',
                'DailyBufferItems': '{}', 'IdleAccumulatingItems': '{}',
            }}
            path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            before = path.read_bytes()
            _, revision = service.read('testpilot')
            response = service.get('testpilot')
            report = json.loads(response['values']['IslandPlan']['IslandProductionPlanner']['PlannerReport'])
            self.assertTrue(report['legacy'])
            self.assertEqual(report['items'][0]['name'], '小麦')
            self.assertEqual(report['items'][0]['target'], 500)
            self.assertEqual(response['revision'], revision)
            self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
