"""实际脱敏四角夹具验证选中身份，避免读到上一张订单后误操作。"""

import unittest
from itertools import combinations
from pathlib import Path

import cv2
import numpy as np

from module.island.order_selection import (
    get_selected_order_position, is_order_selected,
)


FIXTURES = Path(__file__).parent / 'fixtures' / 'island_order_selection'
FIXTURE_CENTERS = {
    'selected': (69, 68),
    'unselected': (69, 68),
    'urgent_0701': (68, 68),
    'regular_0302_occluded': (68, 68),
}


class OrderSelectionTest(unittest.TestCase):
    def setUp(self):
        self.image = np.zeros((720, 1280, 3), dtype=np.uint8)

    def paste(self, position, name='selected', missing=None):
        tile = cv2.imread(str(FIXTURES / f'{name}_corners.png'), cv2.IMREAD_GRAYSCALE)
        center_x, center_y = FIXTURE_CENTERS[name]
        if missing is not None:
            # 删除整象限制造缺角；其余角保留实际截图的原始相对位置。
            indices = (missing,) if isinstance(missing, int) else missing
            for index in indices:
                ys = slice(0, center_y) if index < 2 else slice(center_y, None)
                xs = slice(0, center_x) if index % 2 == 0 else slice(center_x, None)
                tile[ys, xs] = 0
        x, y = position[0] - center_x, position[1] - center_y
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(1280, x + tile.shape[1]), min(720, y + tile.shape[0])
        # 整块平移，不按被测常量裁角或重排，避免把真实间距改成算法假设。
        self.image[y1:y2, x1:x2] = tile[y1 - y:y2 - y, x1 - x:x2 - x, None]

    def test_real_four_corners_associate_with_selected_order_and_hough_error(self):
        self.paste((749, 247))
        positions = [(703, 92), (747, 245), (191, 541), (524, 432), (628, 597)]
        self.assertEqual(get_selected_order_position(self.image, positions), (747, 245))
        self.assertTrue(is_order_selected(self.image, (747, 245), positions))
        self.assertFalse(is_order_selected(self.image, (191, 541), positions))

    def test_different_positions_and_rearrangement_use_dynamic_circle_centers(self):
        for target in ((180, 180), (510, 350), (740, 540)):
            with self.subTest(target=target):
                self.image[:] = 0
                self.paste(target)
                positions = [(350, 170), target, (150, 540)]
                self.assertEqual(get_selected_order_position(self.image, positions), target)

    def test_each_corner_is_required(self):
        for missing in range(4):
            with self.subTest(missing=missing):
                self.image[:] = 0
                self.paste((500, 300), missing=missing)
                self.assertIsNone(get_selected_order_position(self.image, [(500, 300)]))

    def test_large_independent_corner_displacement_is_rejected(self):
        self.paste((500, 300))
        region = self.image[325:365, 525:570].copy()
        self.image[325:365, 525:570] = 0
        self.image[337:377, 525:570] = region
        self.assertIsNone(get_selected_order_position(self.image, [(500, 300)]))

    def test_two_selected_markers_are_ambiguous(self):
        self.paste((200, 200))
        self.paste((700, 400))
        self.assertIsNone(get_selected_order_position(self.image, [(200, 200), (700, 400)]))

    def test_failed_click_and_identical_requirements_cannot_select_another_order(self):
        self.paste((200, 200))
        # 模拟右侧保持上一单的相同货物详情；详情图像不参与选中身份判断。
        self.image[240:475, 884:1218] = 255
        positions = [(200, 200), (700, 400)]
        self.assertFalse(is_order_selected(self.image, (700, 400), positions))
        self.assertTrue(is_order_selected(self.image, (200, 200), positions))

    def test_blue_rings_and_green_finish_do_not_replace_selection_corners(self):
        cv2.circle(self.image, (500, 300), 48, (57, 189, 255), 6)
        cv2.circle(self.image, (500, 350), 15, (57, 210, 110), -1)
        self.assertIsNone(get_selected_order_position(self.image, [(500, 300)]))

    def test_real_unselected_corner_regions_do_not_match(self):
        self.paste((500, 300), name='unselected')
        self.assertIsNone(get_selected_order_position(self.image, [(500, 300)]))

    def test_blank_white_rectangle_and_unknown_image_are_rejected(self):
        for value in (0, 255):
            self.image[:] = value
            self.assertIsNone(get_selected_order_position(self.image, [(500, 300)]))
        self.assertIsNone(get_selected_order_position(np.zeros((360, 640, 3), dtype=np.uint8), [(250, 150)]))
        self.assertIsNone(get_selected_order_position(self.image, []))

    def test_duplicate_color_detections_of_one_circle_do_not_create_two_orders(self):
        self.paste((500, 300))
        self.assertEqual(get_selected_order_position(self.image, [(500, 300), (502, 301)]), (500, 300))

    def test_real_urgent_marker_keeps_104_pixel_vertical_spacing(self):
        """实际五帧中的完整角标间距为104px，不再被逐角重排掩盖。"""
        self.paste((362, 151), name='urgent_0701')
        positions = [(805, 79), (639, 303), (305, 307), (632, 453), (362, 151)]
        self.assertEqual(get_selected_order_position(self.image, positions), (362, 151))
        self.assertTrue(is_order_selected(self.image, (362, 151), positions))
        self.assertFalse(is_order_selected(self.image, (805, 79), positions))

    def test_both_spacing_versions_are_bounded_to_one_pixel(self):
        for extra, expected in ((-1, True), (0, True), (1, False)):
            with self.subTest(vertical_shift=extra):
                self.image[:] = 0
                self.paste((500, 300), name='urgent_0701')
                lower = self.image[325:375, 425:575].copy()
                self.image[325:375, 425:575] = 0
                self.image[325 + extra:375 + extra, 425:575] = lower
                self.assertEqual(is_order_selected(self.image, (500, 300), [(500, 300)]), expected)

    def test_real_occluded_marker_requires_explicit_keyword_permission(self):
        """靠近右上方的普通订单只有完整左侧角对，默认仍不确认。"""
        self.paste((805, 79), name='regular_0302_occluded')
        positions = [(805, 79), (639, 303), (305, 307), (632, 453)]
        self.assertIsNone(get_selected_order_position(self.image, positions))
        self.assertFalse(is_order_selected(self.image, (805, 79), positions))
        self.assertEqual(get_selected_order_position(self.image, positions, allow_right_occlusion=True), (805, 79))
        self.assertTrue(is_order_selected(self.image, (805, 79), positions, allow_right_occlusion=True))
        self.assertFalse(is_order_selected(self.image, (639, 303), positions, allow_right_occlusion=True))
        with self.assertRaises(TypeError):
            get_selected_order_position(self.image, positions, True)
        with self.assertRaises(TypeError):
            is_order_selected(self.image, (805, 79), positions, True)

    def test_occlusion_boundary_requires_whole_right_corner_patch(self):
        # 真实角标右角相对圆心为+41；831仅遮住其中21列，832才完整覆盖。
        for x, expected in ((790, False), (791, True), (792, True)):
            with self.subTest(right_corner_left=x + 41):
                self.image[:] = 0
                self.paste((x, 300), name='urgent_0701', missing=(1, 3))
                self.assertEqual(is_order_selected(self.image, (x, 300), [(x, 300)],
                                                  allow_right_occlusion=True), expected)

    def test_occluded_right_corners_do_not_allow_missing_left_corner(self):
        for missing in (0, 2):
            with self.subTest(missing=missing):
                self.image[:] = 0
                self.paste((805, 79), name='regular_0302_occluded', missing=missing)
                self.assertFalse(is_order_selected(self.image, (805, 79), [(805, 79)],
                                                   allow_right_occlusion=True))

    def test_two_arbitrary_visible_corners_never_confirm_selection(self):
        for retained in combinations(range(4), 2):
            with self.subTest(retained=retained):
                self.image[:] = 0
                self.paste((500, 300), name='urgent_0701',
                           missing=tuple(index for index in range(4) if index not in retained))
                self.assertFalse(is_order_selected(self.image, (500, 300), [(500, 300)],
                                                   allow_right_occlusion=True))

    def test_every_visible_corner_remains_required_with_occlusion_enabled(self):
        for missing in range(4):
            with self.subTest(missing=missing):
                self.image[:] = 0
                self.paste((500, 300), name='urgent_0701', missing=missing)
                self.assertIsNone(get_selected_order_position(self.image, [(500, 300)],
                                                               allow_right_occlusion=True))

    def test_offscreen_right_corner_is_not_a_known_overlay(self):
        self.paste((1220, 300), name='urgent_0701', missing=(1, 3))
        self.assertFalse(is_order_selected(self.image, (1220, 300), [(1220, 300)],
                                           allow_right_occlusion=True))

    def test_full_and_occluded_candidates_are_ambiguous(self):
        self.paste((362, 151), name='urgent_0701')
        self.paste((805, 79), name='regular_0302_occluded')
        positions = [(362, 151), (805, 79)]
        self.assertIsNone(get_selected_order_position(self.image, positions, allow_right_occlusion=True))
        for target in positions:
            self.assertFalse(is_order_selected(self.image, target, positions, allow_right_occlusion=True))

    def test_two_occluded_candidates_are_ambiguous(self):
        self.paste((805, 79), name='regular_0302_occluded')
        self.paste((805, 350), name='regular_0302_occluded')
        self.assertIsNone(get_selected_order_position(self.image, [(805, 79), (805, 350)],
                                                       allow_right_occlusion=True))

    def test_left_corners_from_different_orders_cannot_be_combined(self):
        self.paste((805, 200), name='regular_0302_occluded', missing=2)
        self.paste((805, 450), name='regular_0302_occluded', missing=0)
        self.assertIsNone(get_selected_order_position(self.image, [(805, 200), (805, 450)],
                                                       allow_right_occlusion=True))

    def test_new_fixtures_contain_only_binary_marker_pixels(self):
        for name, maximum_pixels in (('urgent_0701', 4 * 22 * 22), ('regular_0302_occluded', 2 * 22 * 22)):
            with self.subTest(name=name):
                tile = cv2.imread(str(FIXTURES / f'{name}_corners.png'), cv2.IMREAD_GRAYSCALE)
                self.assertEqual(tile.shape, (136, 136))
                self.assertEqual(set(np.unique(tile)), {0, 255})
                self.assertLessEqual(np.count_nonzero(tile), maximum_pixels)


if __name__ == '__main__':
    unittest.main()
