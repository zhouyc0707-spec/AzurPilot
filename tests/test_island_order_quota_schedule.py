"""验证日订单耗尽后的调度，以及未知画面和未完成优先订单的复查保护。"""

import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.island.order import ALAS_ORDER_BACKGROUND, IslandOrder
from module.island_daily_order.assets import DAILY_ORDER_CHECK


def empty_orders():
    return {'urgent': [], 'regular': [], 'season': [], 'cooldown': []}


class DailyOrderQuotaScheduleTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 10, 12)
        self.daily_reset = datetime(2026, 10, 11)
        self.urgent_reset = datetime(2026, 10, 12)
        self.config = SimpleNamespace(
            IslandDailyOrder_RejectCount=7,
            IslandDailyOrder_StuckSeasonOrderId=0,
            IslandDailyOrder_UrgentDetectRefreshTime=self.urgent_reset,
            Scheduler_ServerUpdate='00:00',
            Scheduler_Enable=True,
            cross_get=Mock(side_effect=self.cross_get),
            cross_set=Mock(),
            multi_set=lambda: nullcontext(),
            task_delay=Mock(),
        )
        self.order = IslandOrder.__new__(IslandOrder)
        self.order.config = self.config
        self.order.device = SimpleNamespace(screenshot=Mock(), save_screenshot=Mock())
        self.order.ui_ensure = Mock()
        self.order.ui_goto = Mock()
        self.order._enter_daily_order = Mock()
        self.order._back_to_island_phone = Mock()
        self.order._handle_popups = Mock(return_value=False)
        self.order.appear = Mock(side_effect=lambda button, **_: button in (DAILY_ORDER_CHECK, ALAS_ORDER_BACKGROUND))
        self.order.detect_all_orders = Mock(side_effect=lambda: empty_orders())
        self.order._ocr_daily_remaining = Mock(return_value=0)
        self.order._ocr_urgent_remaining = Mock(return_value=None)
        self.order._process_order = Mock(return_value=False)
        self.order._read_time = Mock()
        self.clock = self.enterContext(patch('module.island.order.current_time', return_value=self.now))
        self.refresh = self.enterContext(patch('module.island.order.get_server_next_update',
                                                return_value=self.daily_reset))
        self.enterContext(patch('module.island.order.get_nearest_weekday_date', return_value=self.urgent_reset))
        self.enterContext(patch('module.island.order.get_menu_reserve_items', return_value={}))
        self.enterContext(patch('module.island.order.menu_reservations_known', return_value=True))
        self.planner = self.enterContext(patch('module.island.production_planner.IslandProductionPlanner'))
        self.enterContext(patch('module.island.order.logger'))

    @staticmethod
    def cross_get(path, default=None):
        if path.endswith('PlanFingerprint'):
            return 'already-planned'
        if path.endswith('HardFloorItems'):
            return '{}'
        return default

    def assert_short_recheck(self):
        self.config.task_delay.assert_called_once_with(
            target=self.now + timedelta(minutes=5), server_update=True)
        self.assertTrue(self.config.Scheduler_Enable)

    def test_two_empty_frames_with_zero_quota_keep_daily_reset_candidate(self):
        self.order.run()
        # 保存两帧确认时的刷新点，避免返回或规划跨过零点后被推到后一天。
        self.config.task_delay.assert_called_once_with(target=self.daily_reset, server_update=True)
        self.assertEqual(self.order._ocr_daily_remaining.call_count, 2)
        self.assertEqual(self.order.detect_all_orders.call_count, 2)
        self.assertEqual(self.order.device.screenshot.call_count, 3)
        self.assertEqual(self.order.next_runtime, [self.urgent_reset, self.daily_reset])
        self.assertTrue(self.config.Scheduler_Enable)
        self.assertEqual(self.config.IslandDailyOrder_RejectCount, 7)
        self.planner.assert_not_called()

    def test_unknown_then_two_zero_frames_can_confirm_in_three_frames(self):
        self.order._ocr_daily_remaining.side_effect = [None, 0, 0]
        self.order.run()
        self.config.task_delay.assert_called_once_with(target=self.daily_reset, server_update=True)
        self.assertEqual(self.order._ocr_daily_remaining.call_count, 3)
        self.assertEqual(self.order.detect_all_orders.call_count, 3)

    def test_zero_interrupted_by_unknown_is_not_complete(self):
        self.order._ocr_daily_remaining.side_effect = [0, None, 0]
        self.order.run()
        self.assert_short_recheck()
        self.assertEqual(self.order._ocr_daily_remaining.call_count, 3)

    def test_only_one_zero_frame_keeps_short_recheck(self):
        self.order._ocr_daily_remaining.side_effect = [0, None, None]
        self.order.run()
        self.assert_short_recheck()
        self.assertEqual(self.order._ocr_daily_remaining.call_count, 3)

    def test_all_unknown_frames_are_bounded_and_keep_short_recheck(self):
        self.order._ocr_daily_remaining.return_value = None
        self.order.run()
        self.assert_short_recheck()
        self.assertEqual(self.order._ocr_daily_remaining.call_count, 3)
        self.assertEqual(self.order.detect_all_orders.call_count, 3)

    def test_nonzero_quota_immediately_keeps_short_recheck(self):
        self.order._ocr_daily_remaining.return_value = 3
        self.order.run()
        self.assert_short_recheck()
        self.assertEqual(self.order._ocr_daily_remaining.call_count, 1)

    def test_crossing_daily_refresh_does_not_use_previous_day_zero(self):
        self.refresh.side_effect = [self.daily_reset, self.daily_reset + timedelta(days=1)]
        self.order.run()
        self.assert_short_recheck()
        self.assertEqual(self.refresh.call_count, 2)
        self.assertLessEqual(self.order._ocr_daily_remaining.call_count, 2)
        self.assertEqual(self.order.detect_all_orders.call_count, 2)

    def test_zero_requires_positive_empty_detail_background(self):
        self.order.appear.side_effect = lambda button, **_: button is DAILY_ORDER_CHECK
        self.order.run()
        self.assert_short_recheck()
        self.order._ocr_daily_remaining.assert_not_called()

    def test_unknown_urgent_availability_does_not_stop_for_daily_zero(self):
        self.config.IslandDailyOrder_UrgentDetectRefreshTime = None
        self.order.run()
        self.assert_short_recheck()
        self.order._ocr_daily_remaining.assert_not_called()
        self.order._ocr_urgent_remaining.assert_called_once()

    def test_confirmed_weekly_exhaustion_allows_daily_completion(self):
        self.config.IslandDailyOrder_UrgentDetectRefreshTime = None
        self.order._ocr_urgent_remaining.return_value = 0
        self.order.run()
        self.config.task_delay.assert_called_once_with(target=self.daily_reset, server_update=True)
        self.assertEqual(self.order._ocr_daily_remaining.call_count, 2)
        self.assertEqual(self.config.IslandDailyOrder_UrgentDetectRefreshTime, self.urgent_reset)

    def test_known_unfinished_season_order_keeps_short_recheck(self):
        self.config.IslandDailyOrder_StuckSeasonOrderId = 45
        self.order.run()
        self.assert_short_recheck()
        self.order._ocr_daily_remaining.assert_not_called()
        self.assertEqual(self.config.IslandDailyOrder_StuckSeasonOrderId, 45)

    def test_earlier_known_urgent_check_wins_over_daily_reset(self):
        urgent_check = self.now + timedelta(hours=2)
        self.config.IslandDailyOrder_UrgentDetectRefreshTime = urgent_check
        self.order.run()
        self.config.task_delay.assert_called_once_with(target=urgent_check, server_update=True)
        self.assertEqual(self.order.next_runtime, [urgent_check, self.daily_reset])

    def test_second_ocr_crossing_midnight_cannot_confirm_previous_day(self):
        readings = iter((0, 0))

        def read_quota():
            value = next(readings)
            if self.order._ocr_daily_remaining.call_count == 2:
                self.clock.return_value = self.daily_reset
            return value

        self.order._ocr_daily_remaining.side_effect = read_quota
        self.order.run()
        self.config.task_delay.assert_called_once_with(
            target=self.daily_reset + timedelta(minutes=5), server_update=True)
        self.assertEqual(self.order._ocr_daily_remaining.call_count, 2)
        self.assertNotIn(self.daily_reset, self.order.next_runtime)

    def test_returning_after_midnight_keeps_original_confirmed_refresh(self):
        def return_after_midnight():
            self.clock.return_value = self.daily_reset + timedelta(seconds=1)

        self.order._back_to_island_phone.side_effect = return_after_midnight
        self.order.run()
        self.config.task_delay.assert_called_once_with(target=self.daily_reset, server_update=True)
        self.assertEqual(self.order.next_runtime, [self.urgent_reset, self.daily_reset])

    def test_visible_orders_use_existing_processing_instead_of_daily_quota(self):
        for kind in ('regular', 'urgent', 'season'):
            with self.subTest(kind=kind):
                self.config.task_delay.reset_mock()
                self.order._process_order.reset_mock()
                self.order._ocr_daily_remaining.reset_mock()
                self.config.IslandDailyOrder_UrgentDetectRefreshTime = None if kind == 'urgent' else self.urgent_reset
                orders = empty_orders()
                orders[kind] = [(200, 200)]
                self.order.detect_all_orders.side_effect = lambda: orders

                def process(position, order_kind):
                    self.order._record_deadline(timedelta(minutes=20))
                    return False

                self.order._process_order.side_effect = process
                self.order.run()
                self.order._process_order.assert_called_once_with((200, 200), kind)
                self.order._ocr_daily_remaining.assert_not_called()
                self.config.task_delay.assert_called_once_with(
                    target=self.now + timedelta(minutes=20), server_update=True)

    def test_cooldown_preserves_actual_deadline_without_extra_quota_ocr(self):
        orders = empty_orders()
        orders['cooldown'] = [(200, 200)]
        self.order.detect_all_orders.side_effect = lambda: orders
        self.order._read_time.return_value = timedelta(minutes=30)
        self.order.run()
        self.order._ocr_daily_remaining.assert_not_called()
        self.config.task_delay.assert_called_once_with(
            target=self.now + timedelta(minutes=30), server_update=True)

    def test_new_order_during_confirmation_interrupts_completion(self):
        orders = empty_orders()
        orders['cooldown'] = [(200, 200)]
        self.order.detect_all_orders.side_effect = [empty_orders(), orders]
        self.order._read_time.return_value = timedelta(minutes=30)
        self.order.run()
        self.order._ocr_daily_remaining.assert_called_once()
        self.config.task_delay.assert_called_once_with(
            target=self.now + timedelta(minutes=30), server_update=True)

    def test_popup_between_zero_readings_breaks_consecutive_confirmation(self):
        page_checks = iter((True, False, True, True))

        def appear(button, **_):
            return next(page_checks) if button is DAILY_ORDER_CHECK else button is ALAS_ORDER_BACKGROUND

        self.order.appear.side_effect = appear
        self.order._handle_popups.return_value = True
        self.order._ocr_daily_remaining.side_effect = [0, 0, None]
        self.order.run()
        self.assert_short_recheck()
        self.assertEqual(self.order._ocr_daily_remaining.call_count, 3)

    def test_processed_order_resets_previous_empty_page_reading(self):
        orders = empty_orders()
        orders['regular'] = [(200, 200)]
        self.order.detect_all_orders.side_effect = [
            empty_orders(), orders, empty_orders(), empty_orders(), empty_orders()]
        self.order._process_order.return_value = True
        self.order._ocr_daily_remaining.side_effect = [0, 0, None, None]
        self.order.run()
        self.assert_short_recheck()
        self.order._process_order.assert_called_once_with((200, 200), 'regular')
        self.assertEqual(self.order._ocr_daily_remaining.call_count, 4)

    def test_completion_does_not_cache_across_runs(self):
        self.order.run()
        self.config.task_delay.reset_mock()
        self.order._ocr_daily_remaining.reset_mock()
        self.order._ocr_daily_remaining.side_effect = [0, None, None]
        self.order.run()
        self.assert_short_recheck()
        self.assertEqual(self.order._ocr_daily_remaining.call_count, 3)


if __name__ == '__main__':
    unittest.main()
