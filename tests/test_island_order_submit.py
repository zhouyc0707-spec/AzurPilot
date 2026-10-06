"""用资源图和虚拟时间验证订单交付，避免误判成功或误驳回。"""

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from module.base.utils import load_image
from module.exception import GameStuckError
from module.island.island_daily_order import (
    DAILY_ORDER_CHECK,
    DAILY_ORDER_DELIVER,
    DAILY_ORDER_LEVEL_UP,
    DAILY_ORDER_PREPARING,
    DAILY_ORDER_REJECT,
    DAILY_ORDER_RIGHT_PANEL_CHECK,
    ISLAND_CLICK_SAFE_AREA,
    ISLAND_GET,
    POPUP_RESOURCE_INSUFFICIENT,
    TEMPLATE_DAILY_ORDER_EASY,
    IslandDailyOrder,
)


def frame_with(*buttons):
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    for button in buttons:
        x1, y1, x2, y2 = button.area
        frame[y1:y2, x1:x2] = load_image(button.file)[y1:y2, x1:x2]
    return frame


ORDER = frame_with(DAILY_ORDER_CHECK, DAILY_ORDER_RIGHT_PANEL_CHECK,
                   DAILY_ORDER_DELIVER, DAILY_ORDER_REJECT)
REWARD = frame_with(DAILY_ORDER_CHECK, ISLAND_GET)
LEVEL_UP = frame_with(DAILY_ORDER_CHECK, DAILY_ORDER_LEVEL_UP)
PREPARING = frame_with(DAILY_ORDER_CHECK, DAILY_ORDER_RIGHT_PANEL_CHECK, DAILY_ORDER_PREPARING)
INSUFFICIENT = frame_with(DAILY_ORDER_CHECK, POPUP_RESOURCE_INSUFFICIENT)
UNKNOWN = np.zeros_like(ORDER)


def frame_with_remaining_order():
    frame = ORDER.copy()
    icon = load_image(TEMPLATE_DAILY_ORDER_EASY.file)
    height, width = icon.shape[:2]
    frame[170:170 + height, 180:180 + width] = icon
    return frame


class FakeDevice:
    def __init__(self, timeline):
        self.now = 1000.0
        self.start = self.now
        self.image = ORDER.copy()
        self.timeline = timeline
        self.clicks = []
        self.sleeps = []
        self.screenshot_cost = 0.5

    @property
    def elapsed(self):
        return self.now - self.start

    def screenshot(self):
        self.now += self.screenshot_cost
        self.image = self.timeline(self)
        return self.image

    def click(self, button, **kwargs):
        self.clicks.append((button.name, self.elapsed))

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def stuck_record_add(self, button):
        pass

    def count_clicks(self, button):
        return sum(name == button.name for name, _ in self.clicks)


class OrderUI(IslandDailyOrder):
    def __init__(self, device):
        self.device = device
        self.config = SimpleNamespace(BUTTON_OFFSET=30)
        self.interval_timer = {}
        self._first_right_panel_check = False
        self.reject_count = 0


class OrderSubmitTests(unittest.TestCase):
    def create_order(self, timeline):
        device = FakeDevice(timeline)
        self.enterContext(patch('module.base.timer.time', side_effect=lambda: device.now))
        self.enterContext(patch('module.island.island_daily_order.logger'))
        return OrderUI(device), device

    def test_reward_returns_early_after_positive_page_confirmation(self):
        order, device = self.create_order(
            lambda d: ORDER if d.count_clicks(ISLAND_CLICK_SAFE_AREA) else REWARD)
        self.assertTrue(order._submit_order(DAILY_ORDER_DELIVER))
        self.assertEqual(device.count_clicks(DAILY_ORDER_DELIVER), 1)
        self.assertEqual(device.count_clicks(ISLAND_CLICK_SAFE_AREA), 1)
        self.assertLess(device.elapsed, 5)
        self.assertEqual(device.sleeps, [])

    def test_slow_reward_does_not_advance_before_result(self):
        def timeline(device):
            if device.elapsed < 12:
                return ORDER
            return ORDER if device.count_clicks(ISLAND_CLICK_SAFE_AREA) else REWARD

        order, device = self.create_order(timeline)
        self.assertTrue(order._submit_order(DAILY_ORDER_DELIVER))
        self.assertGreater(device.elapsed, 13)
        self.assertEqual(device.count_clicks(DAILY_ORDER_DELIVER), 1)

    def test_level_up_after_reward_resets_stability(self):
        def timeline(device):
            clicks = device.count_clicks(ISLAND_CLICK_SAFE_AREA)
            if clicks == 0:
                return REWARD
            if device.elapsed < 1.5:
                return ORDER
            return LEVEL_UP if clicks < 2 else ORDER

        order, device = self.create_order(timeline)
        self.assertTrue(order._submit_order(DAILY_ORDER_DELIVER))
        self.assertEqual(device.count_clicks(ISLAND_CLICK_SAFE_AREA), 2)
        self.assertGreater(device.elapsed, 3.5)

    def test_delayed_insufficient_popup_is_not_success(self):
        def timeline(device):
            if device.elapsed < 3:
                return ORDER
            return INSUFFICIENT if device.elapsed < 6 else ORDER

        order, device = self.create_order(timeline)
        self.assertFalse(order._submit_order(DAILY_ORDER_DELIVER))
        self.assertGreater(device.elapsed, 7)
        self.assertEqual(device.sleeps, [])

    def test_preparing_transition_can_confirm_without_reward(self):
        order, _ = self.create_order(lambda d: PREPARING)
        self.assertTrue(order._submit_order(DAILY_ORDER_DELIVER))

    def test_preexisting_preparing_is_not_new_success(self):
        order, device = self.create_order(lambda d: PREPARING)
        device.image = PREPARING
        self.assertIsNone(order._submit_order(DAILY_ORDER_DELIVER))
        self.assertTrue(order._submit_needs_reenter)

    def test_unchanged_order_reenters_once_then_raises(self):
        order, _ = self.create_order(lambda d: ORDER)
        self.assertIsNone(order._submit_order(DAILY_ORDER_DELIVER))
        self.assertTrue(order._submit_needs_reenter)
        with self.assertRaises(GameStuckError):
            order._submit_order(DAILY_ORDER_DELIVER)

    def test_slow_screenshots_keep_confirmation_timeout_bounded(self):
        order, device = self.create_order(lambda d: ORDER)
        device.screenshot_cost = 2
        self.assertIsNone(order._submit_order(DAILY_ORDER_DELIVER))
        self.assertLessEqual(device.elapsed, 22)

    def test_reward_followed_by_unknown_page_is_not_success(self):
        order, _ = self.create_order(
            lambda d: UNKNOWN if d.count_clicks(ISLAND_CLICK_SAFE_AREA) else REWARD)
        self.assertIsNone(order._submit_order(DAILY_ORDER_DELIVER))

    def test_normal_success_stays_in_order_page(self):
        order, _ = self.create_order(lambda d: ORDER)
        order._check_items_for_reject = Mock(return_value=False)
        order._submit_order = Mock(return_value=True)
        order.appear_then_click = Mock()
        self.assertEqual(order._step_right_panel(), 'to_step3')
        order.appear_then_click.assert_not_called()

    def test_unconfirmed_result_never_rejects(self):
        order, _ = self.create_order(lambda d: ORDER)
        order._check_items_for_reject = Mock(return_value=False)
        order._submit_order = Mock(return_value=None)
        order.appear_then_click = Mock()
        self.assertEqual(order._step_right_panel(), 'reenter')
        order.appear_then_click.assert_not_called()

    def test_configured_reject_filter_still_skips_submission(self):
        order, device = self.create_order(lambda d: ORDER)
        order._check_items_for_reject = Mock(return_value=True)
        order._submit_order = Mock()
        self.assertEqual(order._step_right_panel(), 'to_step3')
        order._submit_order.assert_not_called()
        self.assertEqual(device.count_clicks(DAILY_ORDER_REJECT), 1)
        self.assertEqual(order.reject_count, 1)

    def test_confirmed_shortage_keeps_normal_rejection(self):
        order, device = self.create_order(lambda d: ORDER)
        order._check_items_for_reject = Mock(return_value=False)
        order._submit_order = Mock(return_value=False)
        self.assertEqual(order._step_right_panel(), 'to_step3')
        self.assertEqual(device.count_clicks(DAILY_ORDER_REJECT), 1)

    def test_urgent_shortage_keeps_cached_refresh_time(self):
        order, _ = self.create_order(lambda d: ORDER)
        order.config.IslandDailyOrder_UrgentDetectRefreshTime = order.DEFAULT_URGENT_REFRESH_TIME
        order._template_click_urgent = Mock(return_value=(100, 100, 40, 20))
        order._has_reject_button = Mock(return_value=False)
        order._submit_order = Mock(return_value=False)
        order._ocr_cooldown_below_urgent = Mock(return_value=7200)
        now = datetime(2026, 10, 6, 12)
        with patch('module.island.island_daily_order.current_time', return_value=now):
            self.assertEqual(order._step_urgent(), 'continue')
        self.assertEqual(order.config.IslandDailyOrder_UrgentDetectRefreshTime,
                         now + timedelta(hours=2))

    def test_empty_panel_after_delivery_checks_other_orders(self):
        order, _ = self.create_order(lambda d: frame_with(DAILY_ORDER_CHECK))
        self.assertEqual(order._step_right_panel(), 'to_step3')

    def test_empty_panel_after_reenter_still_checks_left_orders(self):
        order, device = self.create_order(lambda d: frame_with(DAILY_ORDER_CHECK))
        order._back_to_island_phone = Mock()
        order._enter_daily_order = Mock()
        order._reenter()
        self.assertEqual(order._step_right_panel(), 'to_step3')
        device.timeline = lambda d: frame_with_remaining_order()
        device.screenshot()
        self.assertEqual(order._step_challenge_easy(), 'to_step2')

    def test_unknown_page_cannot_be_mistaken_for_empty_panel(self):
        order, _ = self.create_order(lambda d: UNKNOWN)
        self.assertEqual(order._step_right_panel(), 'reenter')

    def test_two_orders_complete_without_reentering(self):
        def timeline(device):
            submitted = device.count_clicks(DAILY_ORDER_DELIVER)
            acknowledged = device.count_clicks(ISLAND_CLICK_SAFE_AREA)
            if submitted > acknowledged:
                return REWARD
            if submitted == 1:
                return frame_with_remaining_order()
            if submitted >= 2:
                return frame_with(DAILY_ORDER_CHECK)
            return ORDER

        order, device = self.create_order(timeline)
        order.config.IslandDailyOrder_UrgentDetectRefreshTime = order.DEFAULT_URGENT_REFRESH_TIME
        order.config.IslandDailyOrder_RejectFilter = ''
        order._delay_to_next_daily_run = Mock()
        order._back_to_island_phone = Mock()
        order._reenter = Mock(side_effect=AssertionError('已确认交付不应重进'))
        order._main_loop()
        self.assertEqual(device.count_clicks(DAILY_ORDER_DELIVER), 2)
        order._back_to_island_phone.assert_called_once()
        self.assertTrue(any(name == 'DAILY_ORDER_TEMP_CLICK' for name, _ in device.clicks))
        self.assertLess(device.elapsed, 15)


if __name__ == '__main__':
    unittest.main()
