"""实际脱敏四角夹具验证选中身份，避免读到上一张订单后误操作。"""

import unittest
from itertools import combinations
from pathlib import Path

import cv2
import numpy as np

from module.island.order_selection import (
    _corner_matches, _has_selection_corners, _white_mask, get_selected_order_position, is_order_selected,
)


FIXTURES = Path(__file__).parent / 'fixtures' / 'island_order_selection'
FIXTURE_CENTERS = {
    'selected': (69, 68),
    'unselected': (69, 68),
    'urgent_0701': (68, 68),
    'regular_0302_occluded': (68, 68),
    'regular_0719_complete': (68, 68),
    'regular_0719_map_noise': (68, 68),
    'regular_0719_dialogue': (68, 68),
}


def pre_0719_selection_matches(image, position, *, allow_horizontal_103=False):
    """复现旧几何及整块空白校验，证明实际整体夹具能暴露这次误拒绝。"""
    mask = (image.min(axis=2) >= 225) & (np.ptp(image, axis=2) <= 25)
    x, y = position
    widths = (103, 104) if allow_horizontal_103 else (104,)
    for dy in range(-6, 7):
        for dx in range(-6, 7):
            for width in widths:
                for height in (103, 104):
                    for index in range(4):
                        left = x - 64 + dx + (width if index % 2 else 0)
                        top = y - 63 + dy + (height if index >= 2 else 0)
                        patch = mask[top:top + 22, left:left + 22]
                        if index >= 2:
                            patch = patch[::-1]
                        if index % 2:
                            patch = patch[:, ::-1]
                        if not (patch[:6, 8:].mean() >= 0.9 and patch[8:, :6].mean() >= 0.9
                                and patch[9:, 9:].mean() <= 0.12):
                            break
                    else:
                        return True
    return False


class OrderSelectionTest(unittest.TestCase):
    def setUp(self):
        self.image = np.zeros((720, 1280, 3), dtype=np.uint8)

    def paste(self, position, name='selected', missing=None, *, overlay=False):
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
        clipped = tile[y1 - y:y2 - y, x1 - x:x2 - x]
        if overlay:
            self.image[y1:y2, x1:x2][clipped > 0] = 255
        else:
            self.image[y1:y2, x1:x2] = clipped[:, :, None]

    def paste_dialogue(self):
        panel = cv2.imread(str(FIXTURES.parent / 'island_order_dialogue/dialogue_edges.png'))[:, :, ::-1]
        height, width = panel.shape[:2]
        self.image[589:589 + height, 161:161 + width] = panel

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

    def test_horizontal_spacing_versions_are_bounded_to_one_pixel(self):
        for extra, expected in ((-2, False), (-1, True), (0, True), (1, False)):
            with self.subTest(horizontal_shift=extra):
                self.image[:] = 0
                self.paste((500, 300), name='urgent_0701')
                right = self.image[225:375, 525:575].copy()
                self.image[225:375, 525:575] = 0
                self.image[225:375, 525 + extra:575 + extra] = right
                self.assertEqual(is_order_selected(self.image, (500, 300), [(500, 300)]), expected)

    def test_real_0719_complete_marker_exposes_old_horizontal_geometry(self):
        self.paste((305, 307), name='regular_0719_complete')
        self.assertFalse(pre_0719_selection_matches(self.image, (305, 307)))
        self.assertTrue(pre_0719_selection_matches(self.image, (305, 307), allow_horizontal_103=True))
        positions = [(305, 307), (632, 453), (309, 559), (361, 152)]
        self.assertEqual(get_selected_order_position(self.image, positions), (305, 307))
        self.assertFalse(is_order_selected(self.image, (632, 453), positions))

    def test_real_0719_map_noise_requires_both_geometry_and_component_fix(self):
        self.paste((632, 453), name='regular_0719_map_noise')
        self.assertFalse(pre_0719_selection_matches(self.image, (632, 453)))
        self.assertFalse(pre_0719_selection_matches(self.image, (632, 453), allow_horizontal_103=True))
        positions = [(305, 307), (632, 453), (309, 559), (361, 151)]
        self.assertEqual(get_selected_order_position(self.image, positions), (632, 453))
        self.assertFalse(is_order_selected(self.image, (305, 307), positions))

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
        # 被遮住的宽度不能观测，103px版本也须完整在叠层内；不能只按104px推断。
        for x, expected in ((790, False), (791, False), (792, True), (793, True)):
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
        for name, maximum_pixels in (('urgent_0701', 4 * 22 * 22), ('regular_0302_occluded', 2 * 22 * 22),
                                     ('regular_0719_complete', 4 * 22 * 22), ('regular_0719_map_noise', 4 * 22 * 22)):
            with self.subTest(name=name):
                tile = cv2.imread(str(FIXTURES / f'{name}_corners.png'), cv2.IMREAD_GRAYSCALE)
                self.assertEqual(tile.shape, (136, 136))
                self.assertEqual(set(np.unique(tile)), {0, 255})
                self.assertLessEqual(np.count_nonzero(tile), maximum_pixels)


    def test_real_dialogue_occlusion_requires_visible_bottom_arms_and_full_top_pair(self):
        self.paste((309, 559), name='regular_0719_dialogue')
        bounds = (177, 605, 638, 680)
        self.assertIsNone(get_selected_order_position(self.image, [(309, 559)], allow_right_occlusion=True))
        self.assertTrue(_has_selection_corners(_white_mask(self.image), (309, 559), dialogue_bounds=bounds))
        for missing in range(4):
            with self.subTest(missing=missing):
                self.image[:] = 0
                self.paste((309, 559), name='regular_0719_dialogue', missing=missing)
                self.assertFalse(_has_selection_corners(_white_mask(self.image), (309, 559),
                                                        dialogue_bounds=bounds))

    def test_dialogue_mask_cannot_ignore_all_bottom_corner_evidence_or_round_edges(self):
        self.paste((309, 559), name='regular_0719_dialogue')
        for bounds in ((177, 600, 638, 680), (240, 605, 638, 680), (177, 605, 372, 680),
                       (177, 605, 638, 625)):
            with self.subTest(bounds=bounds):
                self.assertFalse(_has_selection_corners(_white_mask(self.image), (309, 559),
                                                        dialogue_bounds=bounds))

    def test_visible_bottom_arm_fill_or_missing_pixels_cannot_hide_behind_dialogue(self):
        for corrupted in ((247, 600, 269, 605), (350, 600, 372, 605)):
            for color in (0, 255):
                with self.subTest(corrupted=corrupted, color=color):
                    self.image[:] = 0
                    self.paste((309, 559), name='regular_0719_dialogue')
                    x1, y1, x2, y2 = corrupted
                    self.image[y1:y2, x1:x2] = color
                    self.assertFalse(_has_selection_corners(_white_mask(self.image), (309, 559),
                                                            dialogue_bounds=(177, 605, 638, 680)))

    def test_public_dialogue_path_requires_actual_frame_and_explicit_keyword(self):
        self.paste_dialogue()
        self.paste((309, 559), name='regular_0719_dialogue', overlay=True)
        positions = [(305, 307), (632, 453), (309, 559)]
        self.assertIsNone(get_selected_order_position(self.image, positions))
        self.assertEqual(get_selected_order_position(self.image, positions, allow_dialogue_occlusion=True), (309, 559))
        self.assertTrue(is_order_selected(self.image, (309, 559), positions, allow_dialogue_occlusion=True))
        self.assertFalse(is_order_selected(self.image, (632, 453), positions, allow_dialogue_occlusion=True))
        self.image[589:692, 161:654] = 0
        self.paste((309, 559), name='regular_0719_dialogue', overlay=True)
        self.assertIsNone(get_selected_order_position(self.image, positions, allow_dialogue_occlusion=True))

    def test_full_and_partial_or_two_partial_candidates_remain_ambiguous(self):
        for second in ('selected', 'partial'):
            with self.subTest(second=second):
                self.image[:] = 0
                self.paste_dialogue()
                self.paste((309, 559), name='regular_0719_dialogue', overlay=True)
                if second == 'selected':
                    self.paste((305, 307), name='regular_0719_complete', overlay=True)
                    positions = [(309, 559), (305, 307)]
                else:
                    self.paste((509, 559), name='regular_0719_dialogue', overlay=True)
                    positions = [(309, 559), (509, 559)]
                self.assertIsNone(get_selected_order_position(self.image, positions, allow_dialogue_occlusion=True))

    def test_right_and_dialogue_exceptions_cannot_combine_into_one_visible_corner(self):
        self.paste_dialogue()
        self.paste((805, 559), name='urgent_0701', missing=(1, 2), overlay=True)
        self.assertIsNone(get_selected_order_position(self.image, [(805, 559)], allow_right_occlusion=True,
                                                       allow_dialogue_occlusion=True))


class OrderCornerComponentTest(unittest.TestCase):
    def make_corner(self):
        patch = np.zeros((22, 22), dtype=bool)
        patch[:6] = True
        patch[:, :6] = True
        return patch

    def test_independent_white_map_spot_is_not_part_of_L_interior(self):
        patch = self.make_corner()
        patch[11:16, 11:16] = True
        self.assertGreater(patch[9:, 9:].mean(), 0.12)
        self.assertTrue(_corner_matches(patch, 0))
        for index in range(4):
            oriented = patch
            if index >= 2:
                oriented = oriented[::-1]
            if index % 2:
                oriented = oriented[:, ::-1]
            with self.subTest(index=index):
                self.assertTrue(_corner_matches(oriented, index))

    def test_white_fill_connected_to_L_still_fails_interior_check(self):
        patch = self.make_corner()
        patch[5:16, 5:16] = True
        self.assertFalse(_corner_matches(patch, 0))

    def test_disconnected_white_arms_cannot_form_one_L(self):
        patch = np.zeros((22, 22), dtype=bool)
        patch[:6, 8:] = True
        patch[8:, :6] = True
        self.assertEqual(patch[:6, 8:].mean(), 1)
        self.assertEqual(patch[8:, :6].mean(), 1)
        self.assertFalse(_corner_matches(patch, 0))

    def test_solid_white_rectangle_cannot_be_an_L_component(self):
        self.assertFalse(_corner_matches(np.ones((22, 22), dtype=bool), 0))


if __name__ == '__main__':
    unittest.main()
