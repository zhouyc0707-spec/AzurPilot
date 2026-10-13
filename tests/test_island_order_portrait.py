"""实际立绘遮挡三角夹具与状态回放，验证已选中订单可安全继续。"""

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np

from module.base.utils import load_image
from module.exception import GameStuckError
from module.island.order_selection import (
    find_selected_order_positions, get_selected_order_position, is_order_selected,
)
from module.island_daily_order.assets import (
    ALAS_ORDER_ACCEPT, ALAS_ORDER_REQUIREMENTS_CHECK, ALAS_ORDER_URGENT_ACCEPT, DAILY_ORDER_CHECK,
)
from tests import test_island_alas_order as alas_tests
from tests.test_island_order_detail import TEXT_AREAS


FIXTURES = Path(__file__).parent / 'fixtures'
PORTRAIT_FIXTURE = 'urgent_1014_portrait_corners.png'
TARGET = (191, 540)
OTHER = (749, 247)


def paste_marker(image, position, fixture=PORTRAIT_FIXTURE, missing=()):
    """整体平移实际角标夹具，保持真实的103像素间距。"""
    tile = cv2.imread(str(FIXTURES / 'island_order_selection' / fixture), cv2.IMREAD_GRAYSCALE)
    if tile is None:
        raise AssertionError(f'缺少角标夹具：{fixture}')
    tile = tile.copy()
    for index in missing:
        ys = slice(0, 68) if index < 2 else slice(68, None)
        xs = slice(0, 68) if index % 2 == 0 else slice(68, None)
        tile[ys, xs] = 0
    x, y = position[0] - 68, position[1] - 68
    left, top = max(0, x), max(0, y)
    right, bottom = min(1280, x + tile.shape[1]), min(720, y + tile.shape[0])
    clipped = tile[top - y:bottom - y, left - x:right - x]
    # 仅加上白色证据，不用脱敏黑背景抹掉另一订单或详情。
    image[top:bottom, left:right][clipped > 0] = 255
    return image


class PortraitSelectionGeometryTests(unittest.TestCase):
    def setUp(self):
        self.image = np.zeros((720, 1280, 3), dtype=np.uint8)

    def test_actual_three_visible_corners_require_explicit_permission(self):
        paste_marker(self.image, TARGET)
        positions = [TARGET, OTHER, (524, 432)]
        self.assertIsNone(get_selected_order_position(self.image, positions))
        self.assertFalse(is_order_selected(self.image, TARGET, positions))
        self.assertEqual(get_selected_order_position(self.image, positions, allow_portrait_occlusion=True), TARGET)
        self.assertTrue(is_order_selected(self.image, TARGET, positions, allow_portrait_occlusion=True))
        self.assertFalse(is_order_selected(self.image, OTHER, positions, allow_portrait_occlusion=True))

    def test_permission_remains_keyword_only(self):
        with self.assertRaises(TypeError):
            get_selected_order_position(self.image, [TARGET], True)
        with self.assertRaises(TypeError):
            is_order_selected(self.image, TARGET, [TARGET], True)

    def test_center_jitter_keeps_three_corners_in_one_shared_geometry(self):
        paste_marker(self.image, TARGET)
        for position in ((188, 537), TARGET, (194, 543)):
            with self.subTest(position=position):
                self.assertEqual(get_selected_order_position(self.image, [position],
                                                             allow_portrait_occlusion=True), position)

    def test_missing_corner_outside_or_crossing_portrait_bounds_is_rejected(self):
        # 硬编码独立版面坐标：x=225的左下角跨过177；y=475的左下角跨过520。
        for position in ((300, 540), (225, 540), (191, 440), (191, 475)):
            with self.subTest(position=position):
                self.image[:] = 0
                paste_marker(self.image, position)
                self.assertIsNone(get_selected_order_position(self.image, [position],
                                                               allow_portrait_occlusion=True))

    def test_offscreen_corner_is_not_portrait_occlusion(self):
        paste_marker(self.image, (20, 540))
        self.assertIsNone(get_selected_order_position(self.image, [(20, 540)],
                                                       allow_portrait_occlusion=True))

    def test_any_additional_missing_corner_is_rejected(self):
        for missing in ((0,), (1,), (3,), (0, 1), (1, 3)):
            with self.subTest(missing=missing):
                self.image[:] = 0
                paste_marker(self.image, TARGET, missing=missing)
                self.assertIsNone(get_selected_order_position(self.image, [TARGET],
                                                               allow_portrait_occlusion=True))

    def test_other_three_corner_combinations_cannot_use_portrait_permission(self):
        for missing in (0, 1, 3):
            with self.subTest(missing=missing):
                self.image[:] = 0
                paste_marker(self.image, TARGET, 'urgent_0701_corners.png', (missing,))
                self.assertIsNone(get_selected_order_position(self.image, [TARGET],
                                                               allow_portrait_occlusion=True))

    def test_independently_displaced_visible_corner_is_rejected(self):
        paste_marker(self.image, TARGET)
        lower_right = self.image[575:610, 225:270].copy()
        self.image[575:610, 225:270] = 0
        self.image[575:610, 237:282] = lower_right
        self.assertIsNone(get_selected_order_position(self.image, [TARGET],
                                                       allow_portrait_occlusion=True))

    def test_partial_and_full_markers_are_explicitly_ambiguous(self):
        paste_marker(self.image, TARGET)
        paste_marker(self.image, OTHER, 'urgent_0701_corners.png')
        positions = [TARGET, OTHER]
        self.assertEqual(find_selected_order_positions(self.image, positions,
                                                        allow_portrait_occlusion=True), positions)
        self.assertIsNone(get_selected_order_position(self.image, positions, allow_portrait_occlusion=True))
        for target in positions:
            self.assertFalse(is_order_selected(self.image, target, positions, allow_portrait_occlusion=True))

    def test_two_portrait_markers_are_ambiguous(self):
        positions = [(191, 525), (191, 650)]
        for position in positions:
            isolated = np.zeros_like(self.image)
            paste_marker(isolated, position)
            self.assertEqual(get_selected_order_position(isolated, [position],
                                                         allow_portrait_occlusion=True), position)
            paste_marker(self.image, position)
        self.assertEqual(find_selected_order_positions(self.image, positions,
                                                        allow_portrait_occlusion=True), positions)
        self.assertIsNone(get_selected_order_position(self.image, positions, allow_portrait_occlusion=True))

    def test_duplicate_circle_detections_do_not_create_false_ambiguity(self):
        paste_marker(self.image, TARGET)
        self.assertEqual(find_selected_order_positions(self.image, [TARGET, (193, 541)],
                                                        allow_portrait_occlusion=True), [TARGET])

    def test_new_fixture_contains_only_anonymous_marker_pixels(self):
        tile = cv2.imread(str(FIXTURES / 'island_order_selection' / PORTRAIT_FIXTURE), cv2.IMREAD_GRAYSCALE)
        self.assertEqual(tile.shape, (136, 136))
        self.assertEqual(set(np.unique(tile)), {0, 255})
        self.assertLessEqual(np.count_nonzero(tile), 4 * 22 * 22)


class PortraitOrderConfirmationTests(unittest.TestCase):
    frame = staticmethod(alas_tests.AlasSubmitTests.frame)
    make_order = alas_tests.AlasSubmitTests.make_order

    def detail_frame(self, name='hermo', buttons=None, *, portrait=True, other=False):
        image = self.frame(*(buttons if buttons is not None else (
            DAILY_ORDER_CHECK, ALAS_ORDER_REQUIREMENTS_CHECK, ALAS_ORDER_URGENT_ACCEPT)))
        tile = load_image(str(FIXTURES / 'island_order_detail' / f'{name}_text.png'))
        offset = 0
        for x1, y1, x2, y2 in TEXT_AREAS:
            image[y1:y2, x1:x2] = tile[offset:offset + y2 - y1, :x2 - x1]
            offset += y2 - y1
        if portrait:
            paste_marker(image, TARGET)
        if other:
            paste_marker(image, OTHER, 'urgent_0701_corners.png')
        return image

    def selection(self, before, after):
        self.enterContext(patch('module.island.order.server.server', 'cn'))
        timeline = after if callable(after) else lambda device: after.copy()
        order, device = self.make_order(timeline)
        device.image = before.copy()
        order._order_positions = [TARGET, OTHER, (524, 432)]
        order._handle_popups = Mock(return_value=False)
        return order, device, order._order_button(TARGET)

    def assert_unconfirmed(self, order, device, button):
        self.assertEqual(order._click_order(button, 'urgent'), 'unconfirmed')
        device.save_screenshot.assert_called_once_with(genre='island_order_unknown', interval=0)
        self.assertNotIn(ALAS_ORDER_URGENT_ACCEPT.name, device.clicks)

    def test_initial_already_selected_urgent_order_confirms_without_text_change(self):
        frame = self.detail_frame()
        order, device, button = self.selection(frame, frame)
        self.assertEqual(order._click_order(button, 'urgent'), 'detail')
        self.assertEqual(device.clicks, [button.name])
        self.assertGreaterEqual(device.now - 1000, 1.5)
        self.assertLess(device.now - 1000, 2)
        device.save_screenshot.assert_not_called()

    def test_other_order_marked_selected_rejects_even_changed_right_detail(self):
        old = self.detail_frame('lucy', portrait=False, other=True)
        after = self.detail_frame('hermo', portrait=False, other=True)
        order, device, button = self.selection(old, after)
        self.assert_unconfirmed(order, device, button)

    def test_partial_target_plus_full_other_rejects_even_changed_detail(self):
        old = self.detail_frame('lucy')
        after = self.detail_frame('hermo', other=True)
        order, device, button = self.selection(old, after)
        self.assert_unconfirmed(order, device, button)

    def test_non_cn_does_not_enable_portrait_occlusion(self):
        frame = self.detail_frame()
        for region in ('en', 'jp', 'tw'):
            with self.subTest(region=region):
                order, device, button = self.selection(frame, frame)
                with patch('module.island.order.server.server', region):
                    self.assert_unconfirmed(order, device, button)

    def test_missing_page_header_does_not_confirm(self):
        frame = self.detail_frame(buttons=(ALAS_ORDER_REQUIREMENTS_CHECK, ALAS_ORDER_URGENT_ACCEPT))
        order, device, button = self.selection(frame, frame)
        with self.assertRaises(GameStuckError):
            order._click_order(button, 'urgent')
        device.save_screenshot.assert_called_once_with(genre='island_order_unknown', interval=0)

    def test_missing_requirement_or_wrong_accept_does_not_confirm(self):
        for buttons in ((DAILY_ORDER_CHECK, ALAS_ORDER_URGENT_ACCEPT),
                        (DAILY_ORDER_CHECK, ALAS_ORDER_REQUIREMENTS_CHECK, ALAS_ORDER_ACCEPT)):
            with self.subTest(buttons=[button.name for button in buttons]):
                frame = self.detail_frame(buttons=buttons)
                order, device, button = self.selection(frame, frame)
                self.assert_unconfirmed(order, device, button)

    def test_empty_commissioner_or_cargo_text_does_not_enable_portrait_permission(self):
        for areas in (TEXT_AREAS[:1], TEXT_AREAS[1:]):
            with self.subTest(areas=areas):
                frame = self.detail_frame()
                for x1, y1, x2, y2 in areas:
                    frame[y1:y2, x1:x2] = 255
                order, device, button = self.selection(frame, frame)
                self.assert_unconfirmed(order, device, button)

    def test_continuously_changing_details_clear_stability(self):
        first, second = self.detail_frame('lucy'), self.detail_frame('hermo')
        order, device, button = self.selection(
            first, lambda device: first.copy() if int(device.now * 2) % 2 else second.copy())
        self.assert_unconfirmed(order, device, button)

    def test_intermittent_marker_loss_cannot_confirm_a_static_previous_detail(self):
        marked, blank = self.detail_frame(), self.detail_frame(portrait=False)
        order, device, button = self.selection(
            marked, lambda device: marked.copy() if int(device.now * 2) % 2 else blank.copy())
        self.assert_unconfirmed(order, device, button)

    def test_popup_recovery_reestablishes_baseline_and_reclicks_target(self):
        frame = self.detail_frame()
        order, device, button = self.selection(frame, frame)
        order._handle_popups = Mock(side_effect=[True] + [False] * 30)
        self.assertEqual(order._click_order(button, 'urgent'), 'detail')
        self.assertEqual(device.clicks, [button.name] * 2)
        self.assertGreaterEqual(device.now - 1000, 2.5)
        device.save_screenshot.assert_not_called()


if __name__ == '__main__':
    unittest.main()
