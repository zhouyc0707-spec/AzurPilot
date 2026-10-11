"""验证岛屿订单在升级、奖励和过渡动画期间的页面恢复保护。"""

import unittest
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.exception import GameStuckError
from module.island.order import ALAS_ORDER_BACKGROUND, IslandOrder
from module.island_daily_order.assets import DAILY_ORDER_CHECK


@dataclass(frozen=True)
class OrderFrame:
    """一帧页面状态及获取该帧所需的模拟时间。"""

    name: str
    page_ready: bool = True
    popup_visible: bool = False
    popup_handled: bool = False
    elapsed: float = 0.1


def empty_orders():
    return {'urgent': [], 'regular': [], 'season': [], 'cooldown': []}


class IslandOrderPageRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 12, 12)
        self.daily_reset = datetime(2026, 10, 13)
        self.urgent_reset = datetime(2026, 10, 19)
        self.monotonic = 1000.0
        self.in_order = False
        self.frames = iter(())
        self.frame = None
        self.scanned_frames = []
        self.clicked_frames = []
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
        self.order.device = SimpleNamespace(
            screenshot=Mock(side_effect=self.screenshot), save_screenshot=Mock())
        self.order.ui_ensure = Mock()
        self.order.ui_goto = Mock()
        self.order._enter_daily_order = Mock(side_effect=self.enter_order)
        self.order._back_to_island_phone = Mock()
        self.order._handle_popups = Mock(side_effect=self.handle_popups)
        self.order._order_popup_visible = Mock(side_effect=lambda: self.frame.popup_visible)
        self.order.appear = Mock(side_effect=self.appear)
        self.order.detect_all_orders = Mock(side_effect=self.scan_orders)
        self.order._ocr_daily_remaining = Mock(return_value=0)
        self.order._ocr_urgent_remaining = Mock(return_value=None)
        self.order._process_order = Mock(return_value=False)
        self.enterContext(patch('module.base.timer.time', side_effect=lambda: self.monotonic))
        self.enterContext(patch('module.island.order.current_time', return_value=self.now))
        self.enterContext(patch('module.island.order.get_server_next_update', return_value=self.daily_reset))
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

    def enter_order(self):
        self.in_order = True

    def screenshot(self):
        if self.in_order:
            try:
                self.frame = next(self.frames)
            except StopIteration:
                self.fail('页面恢复没有在已提供的画面内结束')
            self.monotonic += self.frame.elapsed

    def appear(self, button, **_):
        if button is DAILY_ORDER_CHECK:
            return self.frame.page_ready
        return button is ALAS_ORDER_BACKGROUND

    def handle_popups(self):
        if self.frame.popup_handled:
            self.clicked_frames.append(self.frame.name)
        return self.frame.popup_handled

    def scan_orders(self):
        self.scanned_frames.append(self.frame.name)
        return empty_orders()

    def run_frames(self, frames):
        self.frames = iter(frames)
        self.order.run()

    def assert_completed(self, expected_scanned_frames):
        self.assertEqual(self.scanned_frames, expected_scanned_frames)
        self.config.task_delay.assert_called_once_with(target=self.daily_reset, server_update=True)
        self.order._back_to_island_phone.assert_called_once()
        self.order.device.save_screenshot.assert_not_called()
        self.planner.assert_not_called()

    def test_upgrade_click_then_unrecognized_animation_returns_to_order_page(self):
        self.run_frames([
            OrderFrame('升级提示', page_ready=False, popup_visible=True, popup_handled=True),
            OrderFrame('星光动画', page_ready=False),
            OrderFrame('暗幕收尾', page_ready=False),
            OrderFrame('正常订单页一'),
            OrderFrame('正常订单页二'),
        ])
        self.assert_completed(['正常订单页一', '正常订单页二'])
        self.assertEqual(self.clicked_frames, ['升级提示'])
        self.assertEqual(self.order._ocr_daily_remaining.call_count, 2)

    def test_more_than_one_hundred_animation_frames_do_not_exhaust_processing_limit(self):
        self.run_frames([
            *(OrderFrame(f'过渡帧{index}', page_ready=False, elapsed=0.04) for index in range(120)),
            OrderFrame('恢复订单页一'),
            OrderFrame('恢复订单页二'),
        ])
        self.assert_completed(['恢复订单页一', '恢复订单页二'])
        self.assertEqual(self.order.device.screenshot.call_count, 123)

    def test_visible_popup_during_click_cooldown_cannot_be_scanned_as_order_page(self):
        self.run_frames([
            # 标题透过弹窗仍可见，点击冷却时处理函数返回 False。
            OrderFrame('冷却中的升级提示', popup_visible=True),
            OrderFrame('仍在冷却的升级提示', popup_visible=True),
            OrderFrame('关闭后的动画', page_ready=False),
            OrderFrame('恢复订单页一'),
            OrderFrame('恢复订单页二'),
        ])
        self.assert_completed(['恢复订单页一', '恢复订单页二'])
        self.assertEqual(self.clicked_frames, [])

    def test_popup_is_handled_before_scanning_even_when_order_title_is_visible(self):
        self.run_frames([
            OrderFrame('标题可见的奖励弹窗', popup_visible=True, popup_handled=True),
            OrderFrame('恢复订单页一'),
            OrderFrame('恢复订单页二'),
        ])
        self.assert_completed(['恢复订单页一', '恢复订单页二'])
        self.assertEqual(self.clicked_frames, ['标题可见的奖励弹窗'])

    def test_unknown_page_timeout_saves_scene_and_raises_existing_stuck_error(self):
        self.frames = iter(OrderFrame('持续未知页', page_ready=False, elapsed=1) for _ in range(30))
        with self.assertRaisesRegex(GameStuckError, '已保存现场'):
            self.order.run()
        self.order.device.save_screenshot.assert_called_once_with(genre='island_order_unknown', interval=0)
        self.order.detect_all_orders.assert_not_called()
        self.order._ocr_daily_remaining.assert_not_called()
        self.config.task_delay.assert_not_called()
        self.order._back_to_island_phone.assert_not_called()
        self.assertGreaterEqual(self.monotonic - 1000, 15)
        self.assertLessEqual(self.monotonic - 1000, 18)

    def test_repeated_popup_clicks_do_not_extend_unknown_page_timeout_forever(self):
        self.frames = iter(
            OrderFrame('重复弹窗', popup_visible=True, popup_handled=True, elapsed=1)
            for _ in range(30))
        with self.assertRaisesRegex(GameStuckError, '已保存现场'):
            self.order.run()
        self.order.device.save_screenshot.assert_called_once_with(genre='island_order_unknown', interval=0)
        self.order.detect_all_orders.assert_not_called()
        self.assertLessEqual(self.monotonic - 1000, 18)

    def test_transition_breaks_consecutive_zero_quota_confirmation(self):
        self.run_frames([
            OrderFrame('过渡前零次数'),
            OrderFrame('无法确认页面的动画', page_ready=False),
            OrderFrame('恢复后第一次零次数'),
            OrderFrame('恢复后第二次零次数'),
        ])
        self.assert_completed(['过渡前零次数', '恢复后第一次零次数', '恢复后第二次零次数'])
        self.assertEqual(self.order._ocr_daily_remaining.call_count, 3)

    def test_recovery_deadline_restarts_only_after_a_confirmed_order_page(self):
        self.run_frames([
            *(OrderFrame('第一段动画', page_ready=False, elapsed=1) for _ in range(9)),
            OrderFrame('两段动画之间的订单页'),
            *(OrderFrame('第二段动画', page_ready=False, elapsed=1) for _ in range(9)),
            OrderFrame('最终恢复订单页一'),
            OrderFrame('最终恢复订单页二'),
        ])
        self.assert_completed(['两段动画之间的订单页', '最终恢复订单页一', '最终恢复订单页二'])
        self.assertGreater(self.monotonic - 1000, 15)

    def test_continuous_order_processing_still_has_one_hundred_iteration_limit(self):
        orders = empty_orders()
        orders['regular'] = [(200, 200)]
        self.order.detect_all_orders.side_effect = lambda: orders
        self.order._process_order.return_value = True
        self.frames = iter(OrderFrame('持续刷新订单') for _ in range(105))
        with self.assertRaisesRegex(GameStuckError, '订单连续处理次数异常'):
            self.order.run()
        self.assertEqual(self.order._process_order.call_count, 100)
        self.order.device.save_screenshot.assert_called_once_with(genre='island_order_unknown', interval=0)
        self.config.task_delay.assert_not_called()


if __name__ == '__main__':
    unittest.main()
