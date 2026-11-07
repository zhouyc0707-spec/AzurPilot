"""冬季订单按完整物品名称映射，并在缺货时保留订单继续复查。"""

from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import module.config.server as server
from module.island import data as local_data
from module.island.order import ALAS_ORDER_ACCEPT, IslandOrder, get_season_order_id
from module.island.order_ocr import OrderDigitCounter, validate_requirements
from module.island.utils import get_active_island_activity_ids
from module.island_planner import data as official_data


WINTER_ACTIVITY = 990028
WINTER_ORDER_IDS = set(range(100061, 100076))
WINTER_NOW = datetime(2026, 11, 7, 12)


def named_request(order, items):
    return {items[item]['name']['cn']: quantity for item, quantity in order['request'].items()}


class WinterOrderDataTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(server, 'server', 'cn'))

    def test_fifteen_orders_preserve_official_names_quantities_and_metadata(self):
        local_orders = {code: order for code, order in local_data.DIC_ISLAND_SEASON_ORDER.items()
                        if order['activity_id'] == WINTER_ACTIVITY}
        official_orders = {code: order for code, order in official_data.DIC_ISLAND_SEASON_ORDER.items()
                           if order['activity_id'] == WINTER_ACTIVITY}
        self.assertEqual(set(local_orders), WINTER_ORDER_IDS)
        self.assertEqual(set(official_orders), WINTER_ORDER_IDS)
        for code in sorted(WINTER_ORDER_IDS):
            with self.subTest(order=code):
                local, official = local_orders[code], official_orders[code]
                self.assertEqual(named_request(local, local_data.DIC_ISLAND_ITEM),
                                 named_request(official, official_data.DIC_ISLAND_ITEM))
                self.assertEqual({key: value for key, value in local.items() if key != 'request'},
                                 {key: value for key, value in official.items() if key != 'request'})

    def test_winter_activity_preserves_dates_and_order_chain(self):
        local = local_data.DIC_ISLAND_ACTIVITY[WINTER_ACTIVITY]
        self.assertEqual(local, official_data.DIC_ISLAND_ACTIVITY[WINTER_ACTIVITY])
        self.assertEqual(local['config_data'], list(range(100061, 100076)))

    def test_cn_activity_only_matches_inside_official_window(self):
        start = datetime(2026, 11, 5)
        end = datetime(2027, 2, 1, 12)
        for when, expected in ((start - timedelta(seconds=1), False), (start, True),
                               (WINTER_NOW, True), (end - timedelta(seconds=1), True), (end, False)):
            with self.subTest(time=when):
                self.assertEqual(WINTER_ACTIVITY in get_active_island_activity_ids(when), expected)

    def test_tw_missing_activity_dates_do_not_activate_winter_orders(self):
        activity = local_data.DIC_ISLAND_ACTIVITY[WINTER_ACTIVITY]
        self.assertEqual(activity['start_time']['tw'], '')
        self.assertEqual(activity['end_time']['tw'], '')
        with patch.object(server, 'server', 'tw'):
            self.assertNotIn(WINTER_ACTIVITY, get_active_island_activity_ids(WINTER_NOW))
            self.assertIsNone(get_season_order_id({4049: (0, 5, -5)}, WINTER_NOW))

    def test_all_winter_requests_have_one_matching_current_order(self):
        for code in sorted(WINTER_ORDER_IDS):
            with self.subTest(order=code):
                request = local_data.DIC_ISLAND_SEASON_ORDER[code]['request']
                requirements = {item: (0, quantity, -quantity) for item, quantity in request.items()}
                self.assertEqual(get_season_order_id(requirements, WINTER_NOW), code)

    def test_soup_order_uses_local_soup_id_instead_of_official_id(self):
        self.assertEqual(local_data.DIC_ISLAND_SEASON_ORDER[100067]['request'], {4049: 5})
        self.assertEqual(local_data.DIC_ISLAND_ITEM[4049]['name']['cn'], '冬菇炖鸡')
        self.assertEqual(official_data.DIC_ISLAND_SEASON_ORDER[100067]['request'], {4051: 5})
        self.assertEqual(get_season_order_id({4049: (0, 5, -5)}, WINTER_NOW), 100067)

    def test_previous_autumn_orders_still_match_uniquely_during_autumn(self):
        autumn = datetime(2026, 10, 8, 12)
        for code in range(100046, 100061):
            with self.subTest(order=code):
                request = local_data.DIC_ISLAND_SEASON_ORDER[code]['request']
                requirements = {item: (0, quantity, -quantity) for item, quantity in request.items()}
                self.assertEqual(get_season_order_id(requirements, autumn), code)


class WinterOrderPolicyTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(server, 'server', 'cn'))
        self.enterContext(patch('module.island.order.current_time', return_value=WINTER_NOW))
        self.enterContext(patch('module.island.order.server_time_offset', return_value=timedelta(0)))
        self.enterContext(patch('module.island.order.logger'))

    def make_order(self, counter_text):
        counter = OrderDigitCounter([]).after_process(counter_text)
        requirements = validate_requirements(['冬菇鸡', '', ''], [counter, None, None],
                                             local_data.DIC_ISLAND_ITEM, 'cn')
        self.assertEqual(requirements, {4049: counter})
        order = IslandOrder.__new__(IslandOrder)
        order.config = SimpleNamespace(IslandDailyOrder_RejectFilter='Cheese > Tofu',
                                       Scheduler_ServerUpdate='00:00')
        order.device = SimpleNamespace(screenshot=Mock(), save_screenshot=Mock())
        order.next_runtime = []
        order.stuck_season_order_id = 0
        order.hard_floor = {}
        order.reserve = {}
        order._click_order = Mock(return_value='detail')
        order._submit_order = Mock(return_value=True)
        order._reject_order = Mock(return_value=True)
        order._filter_rejects = Mock(side_effect=AssertionError('季节订单不应用普通订单过滤'))
        order.scan_current_order_requirements = Mock(return_value=requirements)
        return order

    def test_zero_or_partial_soup_stock_keeps_order_and_rechecks_in_five_minutes(self):
        for counter_text in ('0/5', '4/5'):
            with self.subTest(counter=counter_text):
                order = self.make_order(counter_text)

                self.assertFalse(order._process_order((200, 200), 'season'))

                self.assertEqual(order.stuck_season_order_id, 100067)
                self.assertEqual(order.next_runtime, [WINTER_NOW + timedelta(minutes=5)])
                order._submit_order.assert_not_called()
                order._reject_order.assert_not_called()
                order.device.save_screenshot.assert_not_called()

    def test_five_soups_submit_only_after_stock_meets_full_requirement(self):
        order = self.make_order('5/5')

        self.assertTrue(order._process_order((200, 200), 'season'))

        order._submit_order.assert_called_once_with(ALAS_ORDER_ACCEPT, must_appear=True)
        order._reject_order.assert_not_called()
        self.assertEqual(order.next_runtime, [])

    def test_unmatched_season_request_also_keeps_order_and_rechecks_in_five_minutes(self):
        order = self.make_order('0/6')
        order.stuck_season_order_id = 100066

        self.assertFalse(order._process_order((200, 200), 'season'))

        self.assertEqual(order.stuck_season_order_id, 100066)
        self.assertEqual(order.next_runtime, [WINTER_NOW + timedelta(minutes=5)])
        order._submit_order.assert_not_called()
        order._reject_order.assert_not_called()


if __name__ == '__main__':
    unittest.main()
