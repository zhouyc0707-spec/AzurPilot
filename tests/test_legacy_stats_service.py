"""旧版统计页整页数据（module/api/legacy_stats_service.py）的面板口径。

锁定的是「口径必须与旧界面一致」这件事：行序、取整、占比分母、正负着色、
占位符，以及委托收益一条记录只给一张截图。数据源全部打桩，不读真实库。
"""

from contextlib import ExitStack, contextmanager
from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from module.api.legacy_stats_service import (
    DASH,
    _ap_panel,
    _commission_recent,
    _meow_loot_panel,
    _opsi_panel,
    _parse_month,
    _ship_panel,
    _sign,
)
from module.api.protocol import ApiError


class ParseMonthTests(unittest.TestCase):
    def test_invalid_month_rejected(self):
        with self.assertRaises(ApiError):
            _parse_month('2026-13')

    def test_out_of_range_rejected(self):
        with self.assertRaises(ApiError):
            _parse_month('2019-12')

    def test_missing_month_defaults_to_current(self):
        now = datetime.now()
        self.assertEqual(_parse_month(None), (now.year, now.month, f'{now.year:04d}-{now.month:02d}'))


class SignTests(unittest.TestCase):
    def test_positive_is_gain_and_negative_is_loss(self):
        self.assertEqual(_sign(12), 'gain')
        self.assertEqual(_sign(-3.5), 'loss')
        # 循环效率是带百分号的字符串
        self.assertEqual(_sign('35.26%'), 'gain')

    def test_zero_and_placeholder_are_not_colored(self):
        self.assertEqual(_sign(0), '')
        self.assertEqual(_sign(DASH), '')


def patch_sources(ship_exp=None, meow=None, summary=None, ap_bought=0, ap_rows=None, coin_rows=None,
                  meow_raises=False):
    """装配面板依赖的打桩集合；返回 patch 列表与 cl1_db 假实例。

    体力序列同时取上月与本月，这里只在当前月给出打桩数据，避免同一条记录被算两次。
    """
    meow = meow or {}
    summary = summary or {'month': '2026-09', 'total_battles': 0}
    now = datetime.now()
    exp_stats = ship_exp or SimpleNamespace(
        get_average_battle_time=lambda: 0.0, get_average_round_time=lambda: 0.0)
    cl1_db = MagicMock()
    if meow_raises:
        cl1_db.get_meow_stats.side_effect = RuntimeError('读取失败')
    else:
        cl1_db.get_meow_stats.side_effect = lambda instance, year, month, hazard_level=None: meow.get(hazard_level, {})

    def current_month_rows(rows):
        return lambda year, month, instance: rows if (year, month) == (now.year, now.month) else []

    patches = [
        patch('module.statistics.opsi_month.get_opsi_stats',
              lambda instance_name=None: SimpleNamespace(summary=lambda: summary)),
        patch('module.statistics.opsi_month.compute_monthly_cl1_akashi_ap', lambda **kwargs: ap_bought),
        patch('module.statistics.opsi_month.get_ap_timeline', current_month_rows(ap_rows or [])),
        patch('module.statistics.opsi_month.get_coins_timeline', current_month_rows(coin_rows or [])),
        patch('module.statistics.ship_exp_stats.get_ship_exp_stats', lambda instance_name=None: exp_stats),
        patch('module.statistics.cl1_database.db', cl1_db),
    ]
    return patches, cl1_db


@contextmanager
def patched(**kwargs):
    """进入全部数据源打桩，产出 cl1_db 假实例。"""
    patches, cl1_db = patch_sources(**kwargs)
    with ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        yield cl1_db


class OpsiPanelTests(unittest.TestCase):
    def test_rows_are_ordered_one_five_three_and_derivations_match_old_panel(self):
        summary = {'month': '2026-09', 'total_battles': 101, 'akashi_encounters': 10,
                   'siren_research_devices': 5}
        meow = {
            3: {'battle_count': 40, 'effective_rounds': 20.4, 'akashi_encounters': 4,
                'akashi_ap': 200, 'siren_research_devices': 2,
                'avg_battle_time': 12.34, 'avg_round_time': 30.0},
            5: {'battle_count': 60, 'effective_rounds': 30.6, 'akashi_encounters': 6,
                'akashi_ap': 300, 'siren_research_devices': 3,
                'avg_battle_time': 0.0, 'avg_round_time': 0.0},
        }
        with patched(ship_exp=SimpleNamespace(
                get_average_battle_time=lambda: 11.66, get_average_round_time=lambda: 35.44),
                meow=meow, summary=summary, ap_bought=1500):
            panel = _opsi_panel('alas')

        self.assertEqual([row[1] for row in panel['rows']], [1, 5, 3])
        row_one, row_five, row_three = panel['rows']
        # 侵蚀1：轮次 = (战斗场次 + 1) // 2，消耗 = 轮次 × 5，占比分母是轮次
        self.assertEqual(row_one[2], 101)
        self.assertEqual(row_one[3], 51)
        self.assertEqual(row_one[4], 255)
        self.assertEqual(row_one[6], round(10 / 51 * 100, 2))
        self.assertEqual(row_one[7], int(1500 / 10 + 0.5))
        self.assertEqual(row_one[9], round(5 / 51 * 100, 2))
        self.assertEqual(row_one[10:], [11.7, 35.4])
        # 侵蚀5：出击轮次展示取整，但出击消耗按未取整的 effective_rounds 算
        self.assertEqual(row_five[3], int(round(30.6)))
        self.assertEqual(row_five[4], int(round(30.6 * 30)))
        # 侵蚀3：无数据的列给占位符
        self.assertEqual(row_three[3], int(round(20.4)))
        self.assertEqual(row_three[4], int(round(20.4 * 15)))
        self.assertEqual(row_three[10], 12.3)
        self.assertEqual(row_five[10], DASH)
        # 汇总行：净赚 = 购买 − 消耗、循环效率 = 净赚 / 消耗，只有后两项带正负色
        summary_items = {item['key'].split('.')[-1]: item for item in panel['summary']}
        self.assertEqual(summary_items['MonthlyPurchasedAP']['value'], 1500)
        self.assertEqual(summary_items['MonthlySortieCost']['value'], 255)
        self.assertEqual(summary_items['MonthlyNetAP']['value'], 1245)
        self.assertEqual(summary_items['MonthlyNetAP']['sign'], 'gain')
        self.assertEqual(summary_items['MonthlyLoopEfficiency']['sign'], 'gain')
        self.assertEqual(summary_items['MonthlyPurchasedAP']['sign'], '')

    def test_zero_filled_meow_data_keeps_zeros_like_old_panel(self):
        """数据库返回零值字典时旧界面照样显示 0（只有占比与均值是占位符）。"""
        with patched(summary={'month': '2026-09', 'total_battles': 0}):
            panel = _opsi_panel('alas')
        self.assertEqual([row[1] for row in panel['rows']], [1, 5, 3])
        for row in panel['rows'][1:]:
            self.assertEqual(row[2], 0)   # 战斗场次
            self.assertEqual(row[3], 0)   # 出击轮次（0 也显示 0）
            self.assertEqual(row[4], DASH)  # 出击消耗：轮次为 0 时给占位符
            self.assertEqual(row[5], 0)   # 遇见明石次数
            self.assertEqual(row[7], DASH)  # 平均体力：次数为 0
            self.assertEqual(row[8], 0)   # 吊机次数

    def test_meow_read_failure_falls_back_to_dash(self):
        """读不到耄耋相接数据时整行占位符，不把异常透出去。"""
        with patched(summary={'month': '2026-09', 'total_battles': 0}, meow_raises=True):
            panel = _opsi_panel('alas')
        self.assertEqual([row[1] for row in panel['rows']], [1, 5, 3])
        self.assertTrue(all(value == DASH for value in panel['rows'][1][2:]))
        self.assertTrue(all(value == DASH for value in panel['rows'][2][2:]))


class ApPanelTests(unittest.TestCase):
    def test_series_keys_and_zero_purple_coins_filtered(self):
        ap_rows = [
            {'ts': '2026-09-01 10:00:00', 'ap_total': 100, 'asset': 900, 'distance': 12},
            {'ts': '2026-09-01 11:00:00', 'ap': 120, 'asset': 950, 'distance': 15},
        ]
        coin_rows = [
            {'ts': '2026-09-01 10:00:00', 'yellow_coins': 5, 'purple_coins': 0},
            {'ts': '2026-09-01 11:00:00', 'yellow_coins': 7, 'purple_coins': 3},
        ]
        with patched(ap_rows=ap_rows, coin_rows=coin_rows):
            panel = _ap_panel('alas')
        series = {item['key']: item for item in panel['series']}
        self.assertEqual(list(series), ['ap', 'yellow_coins', 'purple_coins', 'distance', 'asset'])
        # ap_total 缺失时回落到 ap 字段
        self.assertEqual([point['value'] for point in series['ap']['points']], [100, 120])
        # 紫币只保留大于 0 的点
        self.assertEqual([point['value'] for point in series['purple_coins']['points']], [3])
        self.assertEqual(len(series['yellow_coins']['points']), 2)


class ShipPanelTests(unittest.TestCase):
    def test_summary_and_rows_use_today_battles(self):
        stats = MagicMock()
        stats.data = {'ships': [{'position': 1}], 'target_level': 125,
                      'last_check_time': '2026-09-24 09:00:00'}
        stats.get_exp_per_hour.return_value = 1234.6
        stats.get_today_stats.return_value = {'battle_count': 7, 'total_exp_gained': 9999,
                                              'total_run_time': 3600}
        stats.calculate_progress.return_value = {
            'position': 1, 'level': 120, 'current_exp': 10, 'total_exp': 20, 'target_exp': 30,
            'exp_needed': 5, 'battles_needed': 2, 'time_needed': '1小时20分钟'}
        with patched(ship_exp=stats):
            panel = _ship_panel('alas')
        self.assertTrue(panel['hasData'])
        self.assertEqual(panel['expPerHour'], 1235)
        self.assertEqual(panel['todayRunMinutes'], 60)
        self.assertEqual(panel['rows'], [[1, 120, 10, 20, 30, 7, 5, 2, '1小时20分钟']])

    def test_missing_ships_is_empty_state(self):
        stats = MagicMock()
        stats.data = {}
        with patched(ship_exp=stats):
            panel = _ship_panel('alas')
        self.assertFalse(panel['hasData'])
        self.assertEqual(panel['rows'], [])


class MeowLootPanelTests(unittest.TestCase):
    def test_rows_use_monthly_drops_and_cumulative_columns(self):
        azurstats = MagicMock()
        azurstats.get_meow_loot_monthly_totals.return_value = {
            3: {'Plate': 4, 'GearDesignPlanT5': 1, 'OrdnanceTestingReportT4': 2,
                'CoordinateObscure': 3, 'CoordinateAbyssal': 0, 'CatT3': 1},
            5: {},
        }
        azurstats.get_meow_loot_available_months.return_value = [(2026, 8), (2026, 7)]
        # 累计 CSV：侵蚀等级, 时间戳, 有效轮数, 平均黄币/轮, 平均金菜/轮, 平均深渊/轮, 平均隐秘/轮
        azurstats.load_meowofficer_farming.return_value = [
            [3, datetime(2026, 9, 20, 12).timestamp(), 3224.4, 0.0753721, 0.002, 0.004, 0.006],
            [5, datetime(2026, 9, 21, 12).timestamp(), 3329.0, 0.117741, 0.003, 0.005, 0.007],
            [7, datetime(2026, 9, 21, 12).timestamp(), 0, 0, 0, 0, 0],
        ]
        cl1_db = MagicMock()
        cl1_db.get_meow_stats.side_effect = lambda instance, year, month, hazard_level=None: {
            3: {'effective_rounds': 20.4}, 5: {'effective_rounds': 30.6}}[hazard_level]
        with patch('module.statistics.azurstats.AzurStats', azurstats), \
                patch('module.statistics.cl1_database.db', cl1_db):
            panel = _meow_loot_panel('alas', 2026, 9)

        self.assertEqual(panel['month'], '2026-09')
        self.assertEqual(panel['availableMonths'], ['2026-08', '2026-07'])
        # 掉落与可用月份都不带实例过滤：库里历史记录的 instance 列是 NULL，
        # 按实例查会把它们全滤掉（面板看起来像数据没了）
        azurstats.get_meow_loot_monthly_totals.assert_called_once_with(year=2026, month=9)
        azurstats.get_meow_loot_available_months.assert_called_once_with()
        self.assertEqual(panel['lastRecord'], '2026-09-21 12:00:00')
        self.assertEqual([row[1] for row in panel['rows']], [3, 5])
        first, second = panel['rows']
        # 出击轮次展示取整；掉落为 int；累计轮数取整、四个平均值保留 6 位小数
        self.assertEqual(first[2], 20)
        self.assertEqual(first[3:9], [4, 1, 2, 3, 0, 1])
        self.assertEqual(first[9], 3224)
        self.assertEqual(first[10:], ['0.075372', '0.002000', '0.004000', '0.006000'])
        self.assertEqual(second[3:9], [0] * 6)
        # 有效轮数为 0 的累计行被丢弃，因此侵蚀等级 7 不会出现在表里
        self.assertEqual(len(panel['rows']), 2)


class CommissionRecentTests(unittest.TestCase):
    def test_only_tracked_items_and_single_screenshot(self):
        entries = [
            {'ts': '2026-09-24T09:56:00', 'commission_count': 1,
             'items': {'Gem': 10, 'Oil': 0, 'Unknown': 5},
             'screenshots': ['alas/2026-09/a_0.png', 'alas/2026-09/a_1.png']},
            {'ts': 'bad-timestamp', 'items': {}, 'screenshots': []},
        ]
        with patch('module.statistics.commission_income_stats.get_recent_commission_entries',
                   lambda instance, limit=None: entries):
            panel = _commission_recent('alas')

        first, second = panel['rows']
        self.assertEqual(first['time'], '09-24 09:56')
        # 0 值与未统计的物品都不显示
        self.assertEqual([item['name'] for item in first['items']], ['Gem'])
        self.assertEqual(first['items'][0]['icon'], 1)
        # 一条记录只给第一张截图，且路径前缀与旧界面一致
        self.assertEqual(first['screenshot'], '/static/commission_rewards/alas/2026-09/a_0.png')
        self.assertEqual(second['time'], 'bad-timestamp')
        self.assertIsNone(second['screenshot'])
        self.assertEqual(panel['pageSize'], 10)
        self.assertEqual(panel['maxPages'], 5)


if __name__ == '__main__':
    unittest.main()
