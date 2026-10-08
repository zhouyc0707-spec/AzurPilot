"""实际脱敏四角夹具验证选中身份，避免读到上一张订单后误操作。"""

import unittest
from pathlib import Path

import cv2
import numpy as np

from module.island.order_selection import (
    CORNER_OFFSETS, CORNER_SIZE, get_selected_order_position, is_order_selected,
)


FIXTURES = Path(__file__).parent / 'fixtures' / 'island_order_selection'


class OrderSelectionTest(unittest.TestCase):
    def setUp(self):
        self.image = np.zeros((720, 1280, 3), dtype=np.uint8)

    def paste(self, position, name='selected', missing=None):
        tile = cv2.imread(str(FIXTURES / f'{name}_corners.png'), cv2.IMREAD_GRAYSCALE)
        for index, (dx, dy) in enumerate(CORNER_OFFSETS):
            if index == missing:
                continue
            x, y = position[0] + dx, position[1] + dy
            patch = tile[68 + dy:68 + dy + CORNER_SIZE, 69 + dx:69 + dx + CORNER_SIZE]
            self.image[y:y + CORNER_SIZE, x:x + CORNER_SIZE] = patch[:, :, None]

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

    def test_corners_must_share_one_displacement(self):
        self.paste((500, 300))
        dx, dy = CORNER_OFFSETS[3]
        x, y = 500 + dx, 300 + dy
        self.image[y + 12:y + 12 + CORNER_SIZE, x:x + CORNER_SIZE] = self.image[y:y + CORNER_SIZE, x:x + CORNER_SIZE]
        self.image[y:y + 12, x:x + CORNER_SIZE] = 0
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


if __name__ == '__main__':
    unittest.main()
