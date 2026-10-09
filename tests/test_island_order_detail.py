"""真实订单文字夹具与模拟点击序列，验证遮挡时不会误读上一单。"""

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from module.base.utils import load_image
from module.island.order import ALAS_ORDER_BACKGROUND, ALAS_ORDER_COOLDOWN_SPEED_UP, IslandOrder
from module.island.order_detail import get_order_detail_signature, order_detail_changed, same_order_detail
from module.island_daily_order.assets import (
    ALAS_ORDER_ACCEPT, ALAS_ORDER_REQUIREMENTS_CHECK, ALAS_ORDER_URGENT_ACCEPT, DAILY_ORDER_CHECK,
)
from tests import test_island_alas_order as alas_tests


FIXTURES = Path(__file__).parent / 'fixtures' / 'island_order_detail'
# 独立记录真实版面位置，不借用被测模块的区域定义来拼图。
TEXT_AREAS = ((944, 185, 1105, 217), (1052, 252, 1212, 280),
              (1052, 331, 1212, 359), (1052, 410, 1212, 438))


class OrderDetailConfirmationTests(unittest.TestCase):
    frame = staticmethod(alas_tests.AlasSubmitTests.frame)
    make_order = alas_tests.AlasSubmitTests.make_order

    def detail_frame(self, name, buttons=None):
        image = self.frame(*(buttons if buttons is not None else (
            DAILY_ORDER_CHECK, ALAS_ORDER_REQUIREMENTS_CHECK, ALAS_ORDER_ACCEPT)))
        tile = load_image(str(FIXTURES / f'{name}_text.png'))
        offset = 0
        for x1, y1, x2, y2 in TEXT_AREAS:
            image[y1:y2, x1:x2] = tile[offset:offset + y2 - y1, :x2 - x1]
            offset += y2 - y1
        return image

    def make_selection(self, before, after, target=(309, 559)):
        self.enterContext(patch('module.island.order.server.server', 'cn'))
        timeline = after if callable(after) else lambda device: after.copy()
        order, device = self.make_order(timeline)
        device.image = before.copy()
        order._order_positions = [(305, 307), (778, 396), (309, 559), (397, 566)]
        order._handle_popups = Mock(return_value=False)
        return order, device, order._order_button(target)

    def test_three_logged_occluded_orders_confirm_from_changed_stable_details(self):
        for previous, current, target in (
                ('poultry', 'lucy', (778, 396)), ('lucy', 'hermo', (309, 559)),
                ('hermo', 'melie', (397, 566))):
            with self.subTest(order=current):
                order, device, button = self.make_selection(
                    self.detail_frame(previous), self.detail_frame(current), target)
                self.assertEqual(order._click_order(button, 'regular'), 'detail')
                self.assertEqual(device.clicks, [button.name])
                self.assertLess(device.now - 1000, 2)
                device.save_screenshot.assert_not_called()

    def test_unchanged_real_previous_details_never_confirm_failed_click(self):
        old = self.detail_frame('lucy')
        order, device, button = self.make_selection(old, old)
        self.assertEqual(order._click_order(button, 'regular'), 'unconfirmed')
        self.assertEqual(device.clicks, [button.name] * 4)
        device.save_screenshot.assert_called_once()

    def test_center_click_area_avoids_adjacent_order(self):
        order = IslandOrder.__new__(IslandOrder)
        button = order._order_button((397, 566))
        self.assertEqual(button.area, (345, 514, 449, 618))
        self.assertEqual(button.button, (381, 550, 413, 582))

    def test_only_stock_timer_map_or_dialogue_changes_are_not_selection_evidence(self):
        old = self.detail_frame('hermo')
        changed = old.copy()
        changed[282:313, 1093:1216] = 255
        changed[427:473, 989:1101] = 255
        changed[225:590, 60:832] = 255
        changed[610:680, 177:638] = 255
        # 冷却文字属于非名称区域；第一格同样保持静态名称不变。
        changed[410:438, 1052:1212] = old[410:438, 1052:1212]
        self.assertTrue(same_order_detail(get_order_detail_signature(old), get_order_detail_signature(changed)))
        order, _, button = self.make_selection(old, changed)
        self.assertEqual(order._click_order(button, 'regular'), 'unconfirmed')

    def test_one_new_frame_then_previous_details_does_not_confirm(self):
        old = self.detail_frame('lucy')
        new = self.detail_frame('hermo')
        order, _, button = self.make_selection(old, lambda device: new.copy() if device.now == 1000.5 else old.copy())
        self.assertEqual(order._click_order(button, 'regular'), 'unconfirmed')

    def test_continuously_changing_new_details_do_not_confirm(self):
        old = self.detail_frame('poultry')
        first, second = self.detail_frame('lucy'), self.detail_frame('hermo')
        order, _, button = self.make_selection(
            old, lambda device: first.copy() if int(device.now * 2) % 2 else second.copy())
        self.assertEqual(order._click_order(button, 'regular'), 'unconfirmed')

    def test_no_header_wrong_accept_or_missing_detail_does_not_confirm(self):
        for buttons in ((ALAS_ORDER_REQUIREMENTS_CHECK, ALAS_ORDER_ACCEPT),
                        (DAILY_ORDER_CHECK, ALAS_ORDER_REQUIREMENTS_CHECK, ALAS_ORDER_URGENT_ACCEPT),
                        (DAILY_ORDER_CHECK, ALAS_ORDER_ACCEPT)):
            with self.subTest(buttons=[button.name for button in buttons]):
                after = self.detail_frame('hermo', buttons)
                order, _, button = self.make_selection(self.detail_frame('lucy'), after)
                if DAILY_ORDER_CHECK not in buttons:
                    from module.exception import GameStuckError
                    with self.assertRaises(GameStuckError):
                        order._click_order(button, 'regular')
                else:
                    self.assertEqual(order._click_order(button, 'regular'), 'unconfirmed')

    def test_incomplete_new_text_does_not_confirm(self):
        for areas in (TEXT_AREAS[:1], TEXT_AREAS[1:]):
            with self.subTest(areas=areas):
                after = self.detail_frame('hermo')
                for x1, y1, x2, y2 in areas:
                    after[y1:y2, x1:x2] = 255
                order, _, button = self.make_selection(self.detail_frame('lucy'), after)
                self.assertEqual(order._click_order(button, 'regular'), 'unconfirmed')

    def test_other_order_selected_vetoes_changed_right_details(self):
        after = self.detail_frame('hermo')
        marked = alas_tests.OrderClickConfirmationTests().selected_frame((305, 307))
        after[239:375, 237:373] = marked[239:375, 237:373]
        order, _, button = self.make_selection(self.detail_frame('lucy'), after)
        self.assertEqual(order._click_order(button, 'regular'), 'unconfirmed')

    def test_positive_empty_or_cooldown_to_details_is_a_new_selection(self):
        for state in (ALAS_ORDER_BACKGROUND, ALAS_ORDER_COOLDOWN_SPEED_UP):
            with self.subTest(state=state.name):
                order, _, button = self.make_selection(self.frame(DAILY_ORDER_CHECK, state), self.detail_frame('hermo'))
                self.assertEqual(order._click_order(button, 'regular'), 'detail')

    def test_unknown_baseline_is_not_assumed_empty(self):
        before = self.frame(DAILY_ORDER_CHECK)
        order, _, button = self.make_selection(before, self.detail_frame('hermo'))
        self.assertEqual(order._click_order(button, 'regular'), 'unconfirmed')

    def test_popup_requires_fresh_baseline_and_target_reclick(self):
        order, device, button = self.make_selection(self.detail_frame('lucy'), self.detail_frame('hermo'))
        order._handle_popups = Mock(side_effect=[True] + [False] * 30)
        self.assertEqual(order._click_order(button, 'regular'), 'unconfirmed')
        self.assertGreaterEqual(device.clicks.count(button.name), 2)

    def test_first_click_failure_can_recover_after_center_retry(self):
        old, new = self.detail_frame('lucy'), self.detail_frame('hermo')
        order, device, button = self.make_selection(old, lambda device: new.copy() if len(device.clicks) > 1 else old.copy())
        self.assertEqual(order._click_order(button, 'regular'), 'detail')
        self.assertEqual(device.clicks, [button.name] * 2)
        device.save_screenshot.assert_not_called()

    def test_small_text_noise_is_not_a_detail_change(self):
        original = get_order_detail_signature(self.detail_frame('lucy'))
        noisy = tuple(mask.copy() for mask in original)
        noisy[0][0, :5] ^= True
        self.assertTrue(same_order_detail(original, noisy))
        self.assertFalse(order_detail_changed(original, noisy))

    def test_other_servers_keep_existing_marker_confirmation(self):
        order, _, button = self.make_selection(self.detail_frame('lucy'), self.detail_frame('hermo'))
        with patch('module.island.order.server.server', 'jp'):
            self.assertEqual(order._click_order(button, 'regular'), 'unconfirmed')


if __name__ == '__main__':
    unittest.main()
