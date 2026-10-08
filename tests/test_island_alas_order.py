"""离线验证订单范围、库存保护、过滤优先级与最早冷却调度。"""

import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from module.island.data import DIC_ISLAND_ITEM
from module.island.item_ids import LOCAL_TO_ITEM_ID
from module.island.order import IslandOrder, ORDER_COLORS, detect_order_circles
from module.island.order import (
    ALAS_ORDER_ACCEPT, ALAS_ORDER_BACKGROUND, ALAS_ORDER_LEVEL_UP,
    ALAS_ORDER_REQUIREMENTS_CHECK, ALAS_ORDER_URGENT_ACCEPT,
)
from module.island.order_ocr import OrderDigitCounter, match_item_name, validate_requirements
from module.base.utils import load_image
from module.island_daily_order.assets import DAILY_ORDER_CHECK, POPUP_RESOURCE_INSUFFICIENT
from module.island.assets import ISLAND_CLICK_SAFE_AREA, ISLAND_GET
from module.exception import GameStuckError


class OrderRecognitionTests(unittest.TestCase):
    def test_circles_only_in_order_panel(self):
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        for x, y in ((200, 200), (1020, 360)):
            cv2.circle(image, (x, y), 48, ORDER_COLORS['regular'], 6)
        circles = detect_order_circles(image, ORDER_COLORS['regular'])
        self.assertEqual(len(circles), 1)
        self.assertLess(np.linalg.norm(np.subtract(circles[0], (200, 200))), 5)

    def test_bottom_cooldown_circle_is_not_cropped(self):
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        cv2.circle(image, (629, 596), 48, ORDER_COLORS['cooldown'], 6)
        circles = detect_order_circles(image, ORDER_COLORS['cooldown'])
        self.assertEqual(len(circles), 1)
        self.assertLess(np.linalg.norm(np.subtract(circles[0], (629, 596))), 5)

    def test_reverse_counter_preserves_shortage(self):
        ocr = OrderDigitCounter([])
        self.assertEqual(ocr.after_process('12/20'), (12, 20, -8))
        self.assertEqual(ocr.after_process('120/(20+5)'), (120, 25, 95))
        for text in ('', '12', '12/', '12/0', 'abc', '12/20/30'):
            self.assertIsNone(ocr.after_process(text), text)

    def test_empty_and_partial_ocr_never_satisfy_order(self):
        self.assertIsNone(validate_requirements(['', '', ''], [None] * 3, DIC_ISLAND_ITEM, 'cn'))
        self.assertIsNone(validate_requirements(['芝士', '豆腐', ''], [(50, 10, 40), None, None],
                                               DIC_ISLAND_ITEM, 'cn'))
        self.assertIsNone(validate_requirements(['芝士', '', ''], [None, (0, 10, -10), None],
                                               DIC_ISLAND_ITEM, 'cn'))

    def test_complete_one_item_order_is_valid(self):
        self.assertEqual(validate_requirements(['芝士', '', ''], [(50, 10, 40), None, None],
                                              DIC_ISLAND_ITEM, 'cn'),
                         {LOCAL_TO_ITEM_ID['cheese']: (50, 10, 40)})

    def test_unknown_name_is_not_unbounded_autocorrection(self):
        self.assertIsNone(match_item_name('完全不认识的货物名称', DIC_ISLAND_ITEM, 'cn'))
        for partial in ('芝', '纸', '鲜'):
            self.assertIsNone(match_item_name(partial, DIC_ISLAND_ITEM, 'cn'))


class OrderPolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 8, 12)
        self.order = IslandOrder.__new__(IslandOrder)
        self.order.config = SimpleNamespace(IslandDailyOrder_RejectFilter='Cheese > Tofu',
                                            Scheduler_ServerUpdate='00:00')
        self.order.device = SimpleNamespace(screenshot=Mock(), save_screenshot=Mock())
        self.order.next_runtime = []
        self.order.hard_floor = {}
        self.order.reserve = {}
        self.order._click_order = Mock(return_value='detail')
        self.order._submit_order = Mock(return_value=True)
        self.order._reject_order = Mock(return_value=True)
        self.order._check_items_for_reject = Mock(return_value=False)
        self.order.appear = Mock(return_value=True)
        self.enterContext(patch('module.island.order.current_time', return_value=self.now))
        self.enterContext(patch('module.island.order.logger'))

    def test_filter_still_rejects_when_expiry_force_applies(self):
        self.order.scan_current_order_requirements = Mock(return_value={LOCAL_TO_ITEM_ID['cheese']: (50, 10, 40)})
        with patch('module.island.order.get_server_next_update', return_value=self.now + timedelta(hours=1)):
            self.assertTrue(self.order._process_order((200, 200), 'regular'))
        self.order._submit_order.assert_not_called()
        self.order._reject_order.assert_called_once()

    def test_urgent_and_season_ignore_cheese_tofu_filter(self):
        self.order.scan_current_order_requirements = Mock(return_value={LOCAL_TO_ITEM_ID['tofu']: (50, 10, 40)})
        self.order._filter_rejects = Mock(side_effect=AssertionError('优先订单不应用过滤'))
        for kind in ('urgent', 'season'):
            self.assertTrue(self.order._process_order((200, 200), kind))
        self.assertEqual(self.order._submit_order.call_count, 2)
        self.order._reject_order.assert_not_called()

    def test_regular_protects_stock_priority_orders_can_use_it(self):
        item = LOCAL_TO_ITEM_ID['wheat']
        self.order.hard_floor = {item: 30}
        self.order.reserve = {item: 20}
        requirements = {item: (100, 60, 40)}
        self.assertFalse(self.order.is_order_satisfied(requirements, 'regular'))
        self.assertTrue(self.order.is_order_satisfied(requirements, 'regular', force=True))
        self.assertTrue(self.order.is_order_satisfied(requirements, 'urgent'))
        self.assertTrue(self.order.is_order_satisfied(requirements, 'season'))
        self.assertFalse(self.order.is_order_satisfied(None, 'urgent'))

    def test_unknown_menu_preserves_regular_order_without_claiming_shortage(self):
        self.order.reserve_known = False
        self.order.scan_current_order_requirements = Mock(return_value={LOCAL_TO_ITEM_ID['wheat']: (100, 10, 90)})
        with patch('module.island.order.get_server_next_update', return_value=self.now + timedelta(hours=12)):
            self.assertFalse(self.order._process_order((200, 200), 'regular'))
        self.order._submit_order.assert_not_called()
        self.order._reject_order.assert_not_called()
        self.assertEqual(self.order.next_runtime, [self.now + timedelta(minutes=5)])

    def test_unknown_requirements_preserve_order(self):
        self.order.scan_current_order_requirements = Mock(return_value=None)
        self.assertFalse(self.order._process_order((200, 200), 'regular'))
        self.order._submit_order.assert_not_called()
        self.order._reject_order.assert_not_called()
        self.order.device.save_screenshot.assert_called_once()
        self.assertEqual(self.order.next_runtime, [self.now + timedelta(minutes=5)])

    def test_unconfirmed_selection_does_not_read_or_change_order(self):
        self.order._click_order.return_value = 'unconfirmed'
        self.order.scan_current_order_requirements = Mock()
        self.assertFalse(self.order._process_order((200, 200), 'regular'))
        self.order.scan_current_order_requirements.assert_not_called()
        self.order._submit_order.assert_not_called()
        self.order._reject_order.assert_not_called()
        self.assertEqual(self.order.next_runtime, [self.now + timedelta(minutes=5)])

    def test_same_season_order_replans_only_after_actual_factory_stock_shortage(self):
        self.order.config.cross_get = Mock(return_value=88)
        self.order.config.cross_set = Mock()
        self.order.scan_current_order_requirements = Mock(return_value={3101: (0, 30, -30)})
        with patch('module.island.order.get_season_order_id', return_value=88), patch(
                'module.island.production_planner.manufacture_order_targets', return_value=({3101: 30}, {3101: 30})):
            self.assertFalse(self.order._process_order((200, 200), 'season'))
        self.order.config.cross_set.assert_called_once_with(
            'IslandPlan.IslandProductionPlanner.CompletedManufactureOrderId', 0)
        self.assertTrue(self.order._needs_production_plan)

    def test_unknown_submit_reenters_instead_of_rejecting(self):
        self.order.scan_current_order_requirements = Mock(return_value={LOCAL_TO_ITEM_ID['wheat']: (100, 10, 90)})
        self.order._submit_order.return_value = None
        self.order._reenter = Mock()
        with patch('module.island.order.get_server_next_update', return_value=self.now + timedelta(hours=12)):
            self.assertTrue(self.order._process_order((200, 200), 'regular'))
        self.order._reenter.assert_called_once()
        self.order._reject_order.assert_not_called()

    def test_urgent_shortage_cache_includes_next_schedule(self):
        self.order.scan_current_order_requirements = Mock(return_value={LOCAL_TO_ITEM_ID['wheat']: (0, 10, -10)})
        self.order._read_time = Mock(return_value=timedelta(hours=2))
        self.assertFalse(self.order._process_order((200, 200), 'urgent'))
        self.assertEqual(self.order.config.IslandDailyOrder_UrgentDetectRefreshTime, self.now + timedelta(hours=2))
        self.assertEqual(self.order.next_runtime, [self.now + timedelta(hours=2)])
        self.order._reject_order.assert_not_called()

    def test_run_uses_all_cooldowns_not_last_selected_one(self):
        class Config:
            IslandDailyOrder_RejectCount = 7
            IslandDailyOrder_StuckSeasonOrderId = 0
            IslandDailyOrder_UrgentDetectRefreshTime = self.now + timedelta(days=1)

            def cross_get(self, path, default=None):
                if path.endswith('PlanFingerprint'):
                    return 'already-planned'
                if path.endswith('HardFloorItems'):
                    return '{}'
                return default

            def multi_set(self):
                return nullcontext()

            def cross_set(self, path, value):
                pass

            task_delay = Mock()

        self.order.config = Config()
        self.order.ui_ensure = Mock()
        self.order.ui_goto = Mock()
        self.order._enter_daily_order = Mock()
        self.order._back_to_island_phone = Mock()
        self.order.detect_all_orders = Mock(return_value={
            'urgent': [], 'regular': [], 'season': [], 'cooldown': [(200, 200), (400, 200)]})
        self.order._read_time = Mock(side_effect=[timedelta(hours=2), timedelta(minutes=20)])
        with patch('module.island.order.get_menu_reserve_items', return_value={}):
            self.order.run()
        self.order.config.task_delay.assert_called_once_with(target=self.now + timedelta(minutes=20), server_update=True)
        self.assertEqual(self.order.config.IslandDailyOrder_RejectCount, 7)


class AlasSubmitTests(unittest.TestCase):
    @staticmethod
    def frame(*buttons):
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        for button in buttons:
            x1, y1, x2, y2 = button.area
            image[y1:y2, x1:x2] = load_image(button.file)[y1:y2, x1:x2]
        return image

    def make_order(self, timeline):
        device = SimpleNamespace(now=1000.0, clicks=[], image=self.frame(DAILY_ORDER_CHECK, ALAS_ORDER_ACCEPT),
                                 stuck_record_add=lambda button: None, save_screenshot=Mock())

        def click(button):
            device.clicks.append(button.name)

        def screenshot():
            device.now += 0.5
            device.image = timeline(device)
            return device.image

        device.click = click
        device.screenshot = screenshot
        order = IslandOrder.__new__(IslandOrder)
        order.device = device
        order.config = SimpleNamespace(BUTTON_OFFSET=30)
        order.interval_timer = {}
        order._is_preparing = Mock(return_value=False)
        self.enterContext(patch('module.base.timer.time', side_effect=lambda: device.now))
        self.enterContext(patch('module.island.order.logger'))
        return order, device

    def test_reward_and_positive_return_confirm_once(self):
        def timeline(device):
            if ISLAND_CLICK_SAFE_AREA.name in device.clicks:
                return self.frame(DAILY_ORDER_CHECK, ALAS_ORDER_BACKGROUND)
            return self.frame(DAILY_ORDER_CHECK, ISLAND_GET)
        order, device = self.make_order(timeline)
        self.assertTrue(order._submit_order(ALAS_ORDER_ACCEPT))
        self.assertEqual(device.clicks.count(ALAS_ORDER_ACCEPT.name), 1)
        self.assertLess(device.now - 1000, 5)

    def test_insufficient_popup_never_counts_as_success(self):
        def timeline(device):
            return self.frame(DAILY_ORDER_CHECK, POPUP_RESOURCE_INSUFFICIENT) if device.now < 1004 else self.frame(DAILY_ORDER_CHECK, ALAS_ORDER_ACCEPT)
        order, _ = self.make_order(timeline)
        self.assertFalse(order._submit_order(ALAS_ORDER_ACCEPT))

    def test_level_up_then_unknown_does_not_confirm_delivery(self):
        def timeline(device):
            return np.zeros((720, 1280, 3), dtype=np.uint8) if ISLAND_CLICK_SAFE_AREA.name in device.clicks else self.frame(DAILY_ORDER_CHECK, ALAS_ORDER_LEVEL_UP)
        order, _ = self.make_order(timeline)
        self.assertIsNone(order._submit_order(ALAS_ORDER_ACCEPT))
        with self.assertRaises(GameStuckError):
            order._submit_order(ALAS_ORDER_ACCEPT)
        order.device.save_screenshot.assert_called_once()


class OrderClickConfirmationTests(unittest.TestCase):
    frame = staticmethod(AlasSubmitTests.frame)
    make_order = AlasSubmitTests.make_order

    def selected_frame(self, position, fixture='selected_corners.png', buttons=None):
        image = self.frame(*(buttons if buttons is not None else (
            DAILY_ORDER_CHECK, ALAS_ORDER_ACCEPT, ALAS_ORDER_REQUIREMENTS_CHECK)))
        tile = cv2.imread(str(Path(__file__).parent / 'fixtures/island_order_selection' / fixture),
                          cv2.IMREAD_GRAYSCALE)
        # 整体贴入真实角标，保留原始间距；按生产偏移重新拼四角会掩盖几何错误。
        center_x = 69 if fixture == 'selected_corners.png' else 68
        x, y = position[0] - center_x, position[1] - 68
        height, width = tile.shape
        image[y:y + height, x:x + width] = tile[:, :, None]
        return image

    def make_click_order(self, selected):
        order, device = self.make_order(lambda device: self.selected_frame(selected))
        order._order_positions = [(200, 200), (700, 400)]
        order._handle_popups = Mock(return_value=False)
        return order, device

    def test_click_reads_details_only_after_target_four_corners(self):
        order, _ = self.make_click_order((700, 400))
        self.assertEqual(order._click_order(order._order_button((700, 400)), 'regular'), 'detail')
        order.device.save_screenshot.assert_not_called()

    def test_old_details_with_failed_click_preserve_order(self):
        order, _ = self.make_click_order((200, 200))
        self.assertEqual(order._click_order(order._order_button((700, 400)), 'regular'), 'unconfirmed')
        order.device.save_screenshot.assert_called_once_with(genre='island_order_unknown', interval=0)

    def make_logged_order(self, position, fixture, buttons=None):
        frame = self.selected_frame(position, fixture, buttons)
        order, device = self.make_order(lambda device: frame.copy())
        order._order_positions = [(200, 400), position]
        order._handle_popups = Mock(return_value=False)
        return order, device

    def test_logged_urgent_full_corners_with_104_pixel_spacing_confirm(self):
        position = (362, 151)
        order, device = self.make_logged_order(position, 'urgent_0701_corners.png', (
            DAILY_ORDER_CHECK, ALAS_ORDER_REQUIREMENTS_CHECK, ALAS_ORDER_URGENT_ACCEPT))
        self.assertEqual(order._click_order(order._order_button(position), 'urgent'), 'detail')
        self.assertGreaterEqual(device.now - 1000, 1.5)
        self.assertEqual(len(device.clicks), 1)
        device.save_screenshot.assert_not_called()

    def test_logged_right_occlusion_confirms_only_with_known_details_layout(self):
        position = (805, 79)
        order, device = self.make_logged_order(position, 'regular_0302_occluded_corners.png')
        self.assertEqual(order._click_order(order._order_button(position), 'regular'), 'detail')
        self.assertGreaterEqual(device.now - 1000, 1.5)
        device.save_screenshot.assert_not_called()

    def test_right_occlusion_requires_requirements_and_correct_accept_on_same_frame(self):
        position = (805, 79)
        for buttons in (
                (DAILY_ORDER_CHECK, ALAS_ORDER_ACCEPT),
                (DAILY_ORDER_CHECK, ALAS_ORDER_REQUIREMENTS_CHECK),
                (DAILY_ORDER_CHECK, ALAS_ORDER_REQUIREMENTS_CHECK, ALAS_ORDER_URGENT_ACCEPT)):
            with self.subTest(buttons=[button.name for button in buttons]):
                order, device = self.make_logged_order(position, 'regular_0302_occluded_corners.png', buttons)
                self.assertEqual(order._click_order(order._order_button(position), 'regular'), 'unconfirmed')
                device.save_screenshot.assert_called_once_with(genre='island_order_unknown', interval=0)

    def test_occluded_order_without_page_header_remains_unknown(self):
        position = (805, 79)
        order, device = self.make_logged_order(position, 'regular_0302_occluded_corners.png', (
            ALAS_ORDER_REQUIREMENTS_CHECK, ALAS_ORDER_ACCEPT))
        with self.assertRaises(GameStuckError):
            order._click_order(order._order_button(position), 'regular')
        device.save_screenshot.assert_called_once_with(genre='island_order_unknown', interval=0)

    def test_one_selected_frame_is_not_stable_confirmation(self):
        position = (805, 79)
        selected = self.selected_frame(position, 'regular_0302_occluded_corners.png')
        unselected = self.frame(DAILY_ORDER_CHECK, ALAS_ORDER_REQUIREMENTS_CHECK, ALAS_ORDER_ACCEPT)
        order, device = self.make_order(lambda device: selected if device.now == 1000.5 else unselected)
        order._order_positions = [(200, 400), position]
        order._handle_popups = Mock(return_value=False)
        self.assertEqual(order._click_order(order._order_button(position), 'regular'), 'unconfirmed')
        device.save_screenshot.assert_called_once()

    def test_failed_selection_saves_real_current_frame_and_delays_without_processing(self):
        from tests.test_screenshot_save import load_screenshot

        order, device = self.make_click_order((200, 200))
        order.next_runtime = []
        order.scan_current_order_requirements = Mock(side_effect=AssertionError('未知选中不得读取货物'))
        order._submit_order = Mock(side_effect=AssertionError('未知选中不得交付'))
        order._reject_order = Mock(side_effect=AssertionError('未知选中不得驳回'))
        screenshot = load_screenshot()
        now = datetime(2026, 10, 9, 7)
        with TemporaryDirectory() as folder:
            device.config = SimpleNamespace(DropRecord_SaveFolder=folder)
            device._last_save_time = {}
            device.image_save = lambda path: screenshot.Screenshot.image_save(device, path)
            device.save_screenshot = lambda **kwargs: screenshot.Screenshot.save_screenshot(device, **kwargs)
            with patch('module.island.order.current_time', return_value=now):
                self.assertFalse(order._process_order((700, 400), 'regular'))
            images = list((Path(folder) / 'island_order_unknown').glob('*.png'))
            self.assertEqual(len(images), 1)
            np.testing.assert_array_equal(load_image(str(images[0])), device.image)
        self.assertEqual(order.next_runtime, [now + timedelta(minutes=5)])
        self.assertEqual(len(device.clicks), 1)
        order.scan_current_order_requirements.assert_not_called()
        order._submit_order.assert_not_called()
        order._reject_order.assert_not_called()


if __name__ == '__main__':
    unittest.main()
