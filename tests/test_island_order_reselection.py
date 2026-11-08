"""真实遮挡证据与一次有界参照导航的离线状态回归。"""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from module.config.config import AzurLaneConfig
from module.island.order import IslandOrder
from module.island.order_detail import get_order_detail_signature, order_detail_changed
from module.island.order_selection import find_selected_order_positions
from module.island_daily_order.assets import (
    DAILY_ORDER_CHECK, ALAS_ORDER_REQUIREMENTS_CHECK, ALAS_ORDER_ACCEPT, ALAS_ORDER_URGENT_ACCEPT,
)


FIXTURES = Path(__file__).parent / 'fixtures/island_order_reselection'
EXISTING_FIXTURES = Path(__file__).parent / 'fixtures'
TEXT_AREAS = ((944, 185, 1105, 217), (1052, 252, 1212, 280),
              (1052, 331, 1212, 359), (1052, 410, 1212, 438))
ANCHOR = (300, 300)
OTHER = (500, 400)


def image(path):
    return np.array(Image.open(path).convert('RGB'))


def mark(frame, position):
    tile = image(EXISTING_FIXTURES / 'island_order_selection/urgent_0701_corners.png')
    x, y = position
    frame[y-68:y+68, x-68:x+68] = tile
    return frame


def reference_frame(original, *, position=ANCHOR, changed=True):
    frame = original.copy()
    if changed:
        text = image(EXISTING_FIXTURES / 'island_order_detail/lucy_text.png')
        offset = 0
        for x1, y1, x2, y2 in TEXT_AREAS:
            frame[y1:y2, x1:x2] = text[offset:offset+y2-y1, :x2-x1]
            offset += y2-y1
    return mark(frame, position)


class OrderReselectionTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(AzurLaneConfig, '__init__',
                                      side_effect=AssertionError('禁止初始化真实配置')))
        self.enterContext(patch.object(AzurLaneConfig, 'save',
                                      side_effect=AssertionError('禁止保存真实配置')))
        self.enterContext(patch('module.island.order.server.server', 'cn'))
        self.enterContext(patch('module.island.order.logger'))

    def make_order(self, name='melie', *, anchor_kind='regular', mode='success', original_present=True,
                   only_one=False):
        original = image(FIXTURES / f'{name}.png')
        target = (701, 94) if name == 'rosa' else (700, 92)
        reference = reference_frame(original, position=OTHER if mode == 'wrong_reference' else ANCHOR,
                                    changed=mode != 'same_reference_text')
        self.assertTrue(order_detail_changed(get_order_detail_signature(original),
                                             get_order_detail_signature(reference))
                        or mode == 'same_reference_text')
        if mode == 'ambiguous':
            original = mark(mark(original.copy(), ANCHOR), OTHER)
        before = image(FIXTURES / 'peary.png') if mode == 'first_click_failed' else original
        device = SimpleNamespace(image=before.copy(), now=1000.0, clicks=[], kind='regular',
                                 save_screenshot=Mock(), reference_seen=False, popup_handled=False,
                                 visible_popup_frames=0)
        target_name = f'ORDER_AT_{target[0]}_{target[1]}'
        anchor_name = f'ORDER_AT_{ANCHOR[0]}_{ANCHOR[1]}'

        def click(button):
            device.clicks.append(button.name)
            if button.name == anchor_name:
                device.reference_seen = True

        def screenshot():
            device.now += 0.25
            if mode == 'first_click_failed':
                device.image = (original if device.clicks.count(target_name) >= 2 else before).copy()
                return
            reference_visible = device.clicks and device.clicks[-1] == anchor_name
            if reference_visible and mode != 'no_response':
                device.image, device.kind = reference.copy(), anchor_kind
            elif device.reference_seen and mode == 'return_failed':
                device.image, device.kind = reference.copy(), anchor_kind
            else:
                device.image = original.copy()
                device.kind = 'urgent' if device.reference_seen and mode == 'wrong_return_kind' else 'regular'

        def loop(**kwargs):
            # 只推进模拟截图时间；原方法自身的稳定/重试Timer使用同一时钟。
            for _ in range(32):
                screenshot()
                yield

        order = IslandOrder.__new__(IslandOrder)
        order.device = device
        device.click = click
        order.loop = loop
        positions = [target] if only_one else [target, ANCHOR, OTHER]
        order._order_positions = positions

        scans = []

        def detect():
            scans.append(True)
            orders = {'regular': [target] if original_present else [], 'urgent': [], 'season': [], 'cooldown': []}
            if len(scans) == 2 and mode in ('target_disappears_before_return', 'target_changes_kind_before_return'):
                orders['regular'] = []
                if mode == 'target_changes_kind_before_return':
                    orders['urgent'].append(target)
            if not only_one:
                orders[anchor_kind].append(ANCHOR)
                orders['season'].append(OTHER)
            order._order_positions = [position for values in orders.values() for position in values]
            return orders

        order.detect_all_orders = Mock(side_effect=detect)
        order.appear = Mock(side_effect=lambda button, **kwargs: (
            button in (DAILY_ORDER_CHECK, ALAS_ORDER_REQUIREMENTS_CHECK)
            or button is (ALAS_ORDER_URGENT_ACCEPT if device.kind == 'urgent' else ALAS_ORDER_ACCEPT)))

        def popup():
            if mode == 'popup' and device.reference_seen and not device.popup_handled:
                device.popup_handled = True
                return True
            return False

        order._handle_popups = Mock(side_effect=popup)

        def popup_visible():
            if mode == 'visible_popup' and device.reference_seen and device.visible_popup_frames < 2:
                device.visible_popup_frames += 1
                return True
            return False

        order._order_popup_visible = Mock(side_effect=popup_visible)
        order._unknown = Mock(side_effect=AssertionError('不应离开订单页面'))
        self.enterContext(patch('module.base.timer.time', side_effect=lambda: device.now))
        return order, device, order._order_button(target), target_name, anchor_name

    def test_three_real_occluded_targets_recover_once_from_distinct_stable_reference(self):
        for name in ('melie', 'peary', 'rosa'):
            with self.subTest(fixture=name):
                order, device, button, target, anchor = self.make_order(name)
                self.assertEqual(order._click_order(button, 'regular'), 'detail')
                self.assertEqual(device.clicks, [target, target, anchor, target])
                self.assertLess(device.now - 1000, 8)
                device.save_screenshot.assert_not_called()

    def test_urgent_reference_does_not_change_regular_target_accept_type(self):
        order, device, button, target, anchor = self.make_order(anchor_kind='urgent')
        self.assertEqual(order._click_order(button, 'regular'), 'detail')
        self.assertEqual(device.clicks, [target, target, anchor, target])

    def test_reference_failure_or_invalidated_evidence_never_confirms(self):
        for mode in ('no_response', 'wrong_reference', 'same_reference_text', 'popup', 'visible_popup'):
            with self.subTest(mode=mode):
                order, device, button, target, anchor = self.make_order(mode=mode)
                self.assertEqual(order._click_order(button, 'regular'), 'unconfirmed')
                self.assertEqual(device.clicks, [target, target, anchor])
                self.assertLessEqual(device.now - 1000, 8)
                device.save_screenshot.assert_called_once()

    def test_failed_return_or_wrong_accept_stays_unknown_without_second_reference(self):
        for mode in ('return_failed', 'wrong_return_kind'):
            with self.subTest(mode=mode):
                order, device, button, _, anchor = self.make_order(mode=mode)
                self.assertEqual(order._click_order(button, 'regular'), 'unconfirmed')
                self.assertEqual(device.clicks.count(anchor), 1)
                self.assertLessEqual(device.now - 1000, 8)

    def test_disappeared_target_or_only_one_visible_order_does_not_navigate(self):
        for kwargs in ({'original_present': False}, {'only_one': True}):
            with self.subTest(**kwargs):
                order, device, button, target, _ = self.make_order(**kwargs)
                self.assertEqual(order._click_order(button, 'regular'), 'unconfirmed')
                self.assertEqual(device.clicks, [target, target])
                device.save_screenshot.assert_called_once()

    def test_original_center_retry_can_recover_without_reference_navigation(self):
        order, device, button, target, anchor = self.make_order(mode='first_click_failed')
        self.assertEqual(order._click_order(button, 'regular'), 'detail')
        self.assertEqual(device.clicks, [target, target])
        order.detect_all_orders.assert_not_called()
        self.assertNotIn(anchor, device.clicks)

    def test_second_scan_missing_or_changed_target_never_clicks_old_position(self):
        for mode in ('target_disappears_before_return', 'target_changes_kind_before_return'):
            with self.subTest(mode=mode):
                order, device, button, target, anchor = self.make_order(mode=mode)
                self.assertEqual(order._click_order(button, 'regular'), 'unconfirmed')
                self.assertEqual(order.detect_all_orders.call_count, 2)
                self.assertEqual(device.clicks, [target, target, anchor])
                device.save_screenshot.assert_called_once()

    def test_multiple_selected_candidates_do_not_start_reference_navigation(self):
        order, device, button, target, anchor = self.make_order(mode='ambiguous')
        self.assertEqual(order._click_order(button, 'regular'), 'unconfirmed')
        self.assertNotIn(anchor, device.clicks)
        order.detect_all_orders.assert_not_called()
        self.assertTrue(all(name == target for name in device.clicks))

    def test_four_real_marker_fixtures_remain_unconfirmed_by_strict_geometry(self):
        for name, position, detail in (('melie', (700, 92), True), ('peary', (700, 92), True),
                                       ('rosa', (701, 94), True), ('cooldown', (778, 397), False)):
            with self.subTest(fixture=name):
                selected = find_selected_order_positions(
                    image(FIXTURES / f'{name}.png'), [position], allow_right_occlusion=detail,
                    allow_dialogue_occlusion=detail, allow_portrait_occlusion=detail)
                self.assertEqual(selected, [])


if __name__ == '__main__':
    unittest.main()
