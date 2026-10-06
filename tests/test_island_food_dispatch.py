"""用真实选品页局部截图及虚拟状态序列验证食品派遣计时快路径。"""

import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from module.base.utils import load_image
from module.exception import GameBugError, GameStuckError
from module.island.assets import ERROR1, ISLAND_GET, ISLAND_POST_SAFE_AREA, ISLAND_WORKING, POST_ADD_ORDER
from module.island.island_shop_base import (
    DISPATCH_PRODUCT_DURATION,
    DISPATCH_PRODUCT_NUMBER,
    IslandShopBase,
)
from module.island.island_teahouse import IslandTeahouse
from module.ocr.ocr import Digit, Ocr
from module.ui.page import page_island_postmanage


FIXTURE = Path(__file__).parent / 'fixtures/island_food_dispatch/selection_amount_4_duration_2h.png'
PRODUCT = load_image(str(FIXTURE))


def frame_with(*buttons):
    """只保留资源按钮区域，不包含账号或私人配置。"""
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    for button in buttons:
        x1, y1, x2, y2 = button.area
        frame[y1:y2, x1:x2] = load_image(button.file)[y1:y2, x1:x2]
    return frame


MANAGEMENT = frame_with(page_island_postmanage.check_button)
WORKING = frame_with(ISLAND_WORKING)
REWARD = frame_with(ISLAND_GET)
ERROR = frame_with(ERROR1)
UNKNOWN = np.zeros_like(PRODUCT)
NOW = datetime(2026, 10, 6, 12)


class FakeDevice:
    def __init__(self, clock, timeline=None, *, ignored_clicks=0):
        self.clock = clock
        self.timeline = timeline
        self.image = PRODUCT
        self.phase = 'product'
        self.clicked_at = None
        self.ignored_clicks = ignored_clicks
        self.clicks = []
        self.amount = 7
        self.duration = '02:00:00'
        self.post_amount = 4
        self.post_duration = timedelta(hours=1)
        self.screenshot_seconds = 0.25

    def screenshot(self):
        self.clock.now += self.screenshot_seconds
        if self.timeline is not None:
            self.image = self.timeline(self)
        else:
            self.image = {'product': PRODUCT, 'management': MANAGEMENT, 'working': WORKING}[self.phase]
        return self.image

    def click(self, button, control_check=True):
        self.clicks.append((button.name, self.clock.now))
        self.clock.now += 0.1
        if button == POST_ADD_ORDER:
            if self.ignored_clicks:
                self.ignored_clicks -= 1
            else:
                self.clicked_at = self.clock.now
                self.phase = 'management'

    def sleep(self, seconds):
        self.clock.now += seconds

    def stuck_record_add(self, button):
        pass


class FoodUI(IslandShopBase):
    """跳过配置和模拟器连接，保留真实页面识别与确认状态循环。"""
    def __init__(self, device):
        self.device = device
        self.config = SimpleNamespace(BUTTON_OFFSET=30, SERVER='cn')
        self.interval_timer = {}
        self.island_error = False
        self.posts = {'POST1': {'button': POST_ADD_ORDER, 'status': 'idle'}}
        self.post_manage_swipe_count = 1
        self.special_character = False
        self.special_food = None
        self.chef_config = 'WorkerJuu'
        self.chef_unavailable_products = set()
        self.name_to_config = {
            name: {'selection': POST_ADD_ORDER, 'selection_check': POST_ADD_ORDER}
            for name in ('tea', 'fallback')
        }
        self.post_close = Mock(return_value=True)
        self.post_open = Mock(side_effect=self.open_post)
        self.post_manage_swipe = Mock()
        self.select_product = Mock(return_value=True)
        self.produce_check = Mock(return_value=False)
        self.post_add_one = Mock()
        self.deduct_materials = Mock()
        self.handle_popup_confirm = Mock(return_value=False)

    def open_post(self, button):
        if self.post_open.call_count > 1:
            self.device.phase = 'working'
        return True


class SeasonalFoodUI(FoodUI):
    post_produce = IslandTeahouse.post_produce

    def __init__(self, device):
        super().__init__(device)
        self.seasonal_high_priority_drink = {'name': 'tea', 'selection': DISPATCH_PRODUCT_NUMBER}


class FoodDispatchTests(unittest.TestCase):
    def setUp(self):
        self.clock = SimpleNamespace(now=1000.0)
        self.enterContext(patch('module.base.timer.time', side_effect=lambda: self.clock.now))
        self.enterContext(patch('module.island.island_shop_base.current_time', side_effect=self.now))
        self.enterContext(patch('module.island.island_shop_base.logger'))

    def now(self):
        return NOW + timedelta(seconds=self.clock.now - 1000)

    def mock_ocr(self, device):
        def number_ocr(button, **kwargs):
            return SimpleNamespace(ocr=lambda image: (
                device.amount if button == DISPATCH_PRODUCT_NUMBER else device.post_amount))
        self.enterContext(patch('module.island.island_shop_base.Digit', side_effect=number_ocr))
        self.enterContext(patch('module.island.island_shop_base.Ocr', side_effect=lambda *args, **kwargs:
                                SimpleNamespace(ocr=lambda image: device.duration)))
        self.enterContext(patch('module.island.island_shop_base.Duration', side_effect=lambda *args, **kwargs:
                                SimpleNamespace(ocr=lambda image: device.post_duration)))

    def produce(self, *, amount=7, duration='02:00:00', timeline=None, seasonal=False, ignored_clicks=0):
        device = FakeDevice(self.clock, timeline, ignored_clicks=ignored_clicks)
        device.amount = amount
        device.duration = duration
        self.mock_ocr(device)
        shop = SeasonalFoodUI(device) if seasonal else FoodUI(device)
        result = shop.post_produce('POST1', 'tea', 7, 'finish_time')
        return result, shop, device

    def test_fast_dispatch_uses_observed_quantity_without_reopening(self):
        result, shop, device = self.produce(amount=7)
        self.assertEqual(result, 7)
        self.assertEqual(shop.post_open.call_count, 1)
        shop.post_manage_swipe.assert_not_called()
        shop.deduct_materials.assert_called_once_with('tea', 7)
        self.assertEqual(shop.posts['POST1']['status'], 'working')
        click_time = next(stamp for name, stamp in device.clicks if name == 'POST_ADD_ORDER')
        self.assertEqual(shop.finish_time, NOW + timedelta(seconds=click_time - 1000, hours=2))

    def test_material_limited_partial_quantity_is_rechecked_and_recorded(self):
        result, shop, _ = self.produce(amount=4)
        self.assertEqual(result, 4)
        shop.deduct_materials.assert_called_once_with('tea', 4)
        self.assertEqual(shop.post_open.call_count, 2)

    def test_invalid_time_falls_back_to_actual_post_quantity_and_time(self):
        result, shop, _ = self.produce(duration='02:00:00:1')
        self.assertEqual(result, 4)
        self.assertEqual(shop.post_open.call_count, 2)
        shop.post_manage_swipe.assert_called_once_with(1)
        shop.deduct_materials.assert_called_once_with('tea', 4)
        self.assertGreaterEqual(shop.finish_time, NOW + timedelta(hours=1))
        self.assertLess(shop.finish_time, NOW + timedelta(hours=1, seconds=10))

    def test_unstable_preview_falls_back_instead_of_guessing(self):
        durations = iter((timedelta(hours=2), timedelta(hours=3), timedelta(hours=4)))
        device = FakeDevice(self.clock)
        shop = FoodUI(device)
        self.mock_ocr(device)
        shop.read_food_dispatch_preview = Mock(side_effect=lambda requested: (
            7, next(durations)))
        self.assertEqual(shop.post_produce('POST1', 'tea', 7, 'finish_time'), 4)
        self.assertEqual(shop.read_food_dispatch_preview.call_count, 3)
        self.assertEqual(shop.post_open.call_count, 2)

    def test_invalid_middle_frame_cannot_make_nonconsecutive_preview_look_stable(self):
        device = FakeDevice(self.clock)
        shop = FoodUI(device)
        self.mock_ocr(device)
        valid_preview = (7, timedelta(hours=2))
        shop.read_food_dispatch_preview = Mock(side_effect=(valid_preview, None, valid_preview))
        self.assertEqual(shop.post_produce('POST1', 'tea', 7, 'finish_time'), 4)
        self.assertEqual(shop.read_food_dispatch_preview.call_count, 3)
        self.assertEqual(shop.post_open.call_count, 2)

    def test_slow_quantity_update_gets_three_frames_before_post_recheck(self):
        device = FakeDevice(self.clock)
        self.mock_ocr(device)
        shop = FoodUI(device)
        shop.read_food_dispatch_preview = Mock(side_effect=(None, (7, timedelta(hours=2)),
                                                           (7, timedelta(hours=2))))
        self.assertEqual(shop.post_produce('POST1', 'tea', 7, 'finish_time'), 7)
        self.assertEqual(shop.read_food_dispatch_preview.call_count, 3)
        self.assertEqual(shop.post_open.call_count, 1)

    def test_slow_transition_and_reward_use_fresh_frames_without_duplicate_order(self):
        def timeline(device):
            if device.clicked_at is None:
                return PRODUCT
            elapsed = self.clock.now - device.clicked_at
            if elapsed < 0.75:
                return UNKNOWN
            if elapsed < 1.25:
                return REWARD
            return MANAGEMENT
        result, shop, device = self.produce(timeline=timeline)
        self.assertEqual(result, 7)
        self.assertEqual(sum(name == 'POST_ADD_ORDER' for name, _ in device.clicks), 1)
        self.assertEqual(shop.post_open.call_count, 1)

    def test_missed_confirmation_retries_after_three_seconds_and_uses_last_start_time(self):
        result, shop, device = self.produce(ignored_clicks=1)
        self.assertEqual(result, 7)
        times = [stamp for name, stamp in device.clicks if name == 'POST_ADD_ORDER']
        self.assertEqual(len(times), 2)
        self.assertGreaterEqual(times[1] - times[0], 3)
        self.assertEqual(shop.finish_time, NOW + timedelta(seconds=times[-1] - 1000, hours=2))

    def test_reward_popup_clicks_wait_two_seconds_until_a_fresh_page_is_seen(self):
        def timeline(device):
            if device.clicked_at is None:
                return PRODUCT
            if self.clock.now - device.clicked_at < 4.5:
                return REWARD
            return MANAGEMENT
        result, shop, device = self.produce(timeline=timeline)
        self.assertEqual(result, 7)
        reward_clicks = [stamp for name, stamp in device.clicks if name == ISLAND_POST_SAFE_AREA.name]
        self.assertGreaterEqual(len(reward_clicks), 2)
        self.assertTrue(all(later - earlier >= 2 for earlier, later in zip(reward_clicks, reward_clicks[1:])))
        self.assertEqual(sum(name == 'POST_ADD_ORDER' for name, _ in device.clicks), 1)
        self.assertEqual(shop.post_open.call_count, 1)

    def test_unknown_page_after_order_does_not_update_inventory_or_claim_success(self):
        device = FakeDevice(self.clock, lambda device: PRODUCT if device.clicked_at is None else UNKNOWN)
        self.mock_ocr(device)
        shop = FoodUI(device)
        with self.assertRaises(GameStuckError):
            shop.post_produce('POST1', 'tea', 7, 'finish_time')
        self.assertEqual(shop.posts['POST1']['status'], 'idle')
        self.assertFalse(hasattr(shop, 'finish_time'))
        shop.deduct_materials.assert_not_called()
        self.assertEqual(sum(name == 'POST_ADD_ORDER' for name, _ in device.clicks), 1)

    def test_error_popup_is_preserved_for_game_recovery(self):
        device = FakeDevice(self.clock, lambda device: PRODUCT if device.clicked_at is None else ERROR)
        self.mock_ocr(device)
        shop = FoodUI(device)
        with self.assertRaises(GameBugError):
            shop.post_produce('POST1', 'tea', 7, 'finish_time')
        self.assertTrue(shop.island_error)
        shop.deduct_materials.assert_not_called()

    def test_seasonal_fixed_selection_uses_same_confirmed_fast_path(self):
        result, shop, device = self.produce(amount=7, seasonal=True)
        self.assertEqual(result, 7)
        self.assertIn('DISPATCH_PRODUCT_NUMBER', [name for name, _ in device.clicks])
        self.assertEqual(shop.post_open.call_count, 1)
        shop.select_product.assert_not_called()
        shop.deduct_materials.assert_called_once_with('tea', 7)

    def test_special_food_fallback_deducts_the_product_actually_ordered(self):
        device = FakeDevice(self.clock)
        self.mock_ocr(device)
        shop = FoodUI(device)
        shop.special_food = 'tea'
        shop.produce_check.side_effect = (True, False)
        self.assertEqual(shop.post_produce('POST1', 'tea', 7, 'finish_time', product2='fallback'), 7)
        shop.deduct_materials.assert_called_once_with('fallback', 7)

    def test_failed_post_recheck_never_deducts_guessed_quantity(self):
        device = FakeDevice(self.clock)
        device.duration = 'bad'
        device.post_duration = timedelta()
        self.mock_ocr(device)
        shop = FoodUI(device)
        with self.assertRaises(GameStuckError):
            shop.post_produce('POST1', 'tea', 7, 'finish_time')
        shop.deduct_materials.assert_not_called()

    def test_returning_to_management_without_click_is_not_success(self):
        device = FakeDevice(self.clock, lambda device: MANAGEMENT)
        self.mock_ocr(device)
        shop = FoodUI(device)
        with self.assertRaises(GameStuckError):
            shop.confirm_food_dispatch(7, '测试派遣')
        self.assertEqual(device.clicks, [])

    def test_invalid_preview_values_require_post_recheck(self):
        device = FakeDevice(self.clock)
        self.mock_ocr(device)
        shop = FoodUI(device)
        for amount, duration in ((0, '02:00:00'), (8, '02:00:00'), (4, '00:70:00'),
                                 (4, '000200'), (4, '00:00:00')):
            with self.subTest(amount=amount, duration=duration):
                device.amount, device.duration = amount, duration
                self.assertIsNone(shop.read_food_dispatch_preview(7))

    def test_quantity_below_plan_never_uses_potentially_delayed_preview(self):
        device = FakeDevice(self.clock)
        device.amount = 4
        self.mock_ocr(device)
        shop = FoodUI(device)
        self.assertIsNone(shop.read_food_dispatch_preview(7))
        self.assertEqual(shop.read_food_dispatch_preview(4), (4, timedelta(hours=2)))

    def test_preview_ocr_errors_fall_back_without_preventing_confirmation(self):
        for error in (ValueError('文本'), TypeError('数值'), RuntimeError('模型'), OSError('模型文件')):
            with self.subTest(error=type(error).__name__):
                device = FakeDevice(self.clock)
                shop = FoodUI(device)
                self.mock_ocr(device)
                with patch('module.island.island_shop_base.Ocr', side_effect=error):
                    result = shop.post_produce('POST1', 'tea', 7, 'finish_time')
                self.assertEqual(result, 4)
                self.assertEqual(shop.post_open.call_count, 2)
                shop.deduct_materials.assert_called_once_with('tea', 4)

    def test_game_recovery_exceptions_are_not_swallowed_as_ocr_failures(self):
        device = FakeDevice(self.clock)
        self.mock_ocr(device)
        shop = FoodUI(device)
        with patch('module.island.island_shop_base.Ocr', side_effect=GameStuckError('截图故障')):
            with self.assertRaises(GameStuckError):
                shop.post_produce('POST1', 'tea', 7, 'finish_time')
        shop.deduct_materials.assert_not_called()
        self.assertEqual(device.clicks, [])

    def test_unverified_server_layouts_keep_original_post_recheck(self):
        for server in ('en', 'jp', 'tw'):
            with self.subTest(server=server):
                device = FakeDevice(self.clock)
                self.mock_ocr(device)
                shop = FoodUI(device)
                shop.config.SERVER = server
                # 非 CN 不调用预览模型；页面识别仍使用本轮加载的 CN 测试资源。
                with patch('module.island.island_shop_base.Ocr') as preview_ocr:
                    self.assertEqual(shop.post_produce('POST1', 'tea', 7, 'finish_time'), 4)
                preview_ocr.assert_not_called()
                self.assertEqual(shop.post_open.call_count, 2)

    def test_slow_screenshots_do_not_multiply_confirmation_timeout(self):
        device = FakeDevice(self.clock, lambda device: UNKNOWN)
        device.screenshot_seconds = 2
        self.mock_ocr(device)
        shop = FoodUI(device)
        started = self.clock.now
        with self.assertRaises(GameStuckError):
            shop.confirm_food_dispatch(7, '慢截图测试')
        self.assertGreaterEqual(self.clock.now - started, 15)
        self.assertLessEqual(self.clock.now - started, 17)
        self.assertEqual(device.clicks, [])

    def test_slow_screenshots_do_not_multiply_failed_post_recheck_timeout(self):
        device = FakeDevice(self.clock)
        device.phase = 'working'
        device.post_duration = timedelta()
        device.screenshot_seconds = 2
        self.mock_ocr(device)
        shop = FoodUI(device)
        started = self.clock.now
        with self.assertRaises(GameStuckError):
            shop.finish_food_dispatch('POST1', 'tea', 'finish_time', 7, None, NOW)
        self.assertGreaterEqual(self.clock.now - started, 5)
        self.assertLessEqual(self.clock.now - started, 7)
        shop.deduct_materials.assert_not_called()

    def test_post_quantity_above_plan_is_not_claimed_as_success(self):
        device = FakeDevice(self.clock)
        device.phase = 'working'
        device.post_amount = 5
        self.mock_ocr(device)
        shop = FoodUI(device)
        with self.assertRaises(GameStuckError):
            shop.finish_food_dispatch('POST1', 'tea', 'finish_time', 3, None, NOW)
        shop.deduct_materials.assert_not_called()
        self.assertFalse(hasattr(shop, 'finish_time'))


class DispatchScreenshotOCRTests(unittest.TestCase):
    def test_real_quantity_and_full_button_time_can_be_read_without_post_details(self):
        """夹具来自已有菊花茶选品页，仅保存页头、次数及确认按钮三个区域。"""
        number = Digit(DISPATCH_PRODUCT_NUMBER, lang='cnocr', letter=(80, 80, 80),
                       threshold=160, alphabet='0123456789').ocr(PRODUCT)
        duration = Ocr(DISPATCH_PRODUCT_DURATION, lang='cnocr', letter=(255, 255, 255),
                       threshold=128, alphabet='0123456789:').ocr(PRODUCT)
        self.assertEqual(number, 4)
        self.assertEqual(duration, '02:00:00')


if __name__ == '__main__':
    unittest.main()
