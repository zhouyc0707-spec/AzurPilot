"""匿名猫窝图像验证位置锚点，不访问设备或用户账号。"""

import unittest

import numpy as np

from module.meowfficer.scan_list import (card_center, card_is_empty, cattery_at_top,
                                       cattery_order_unchanged, read_card_identity, selected_card)


def _image(occupied=True):
    image = np.full((720, 1280, 3), (231, 223, 222), dtype=np.uint8)
    if occupied:
        for index in range(12):
            cx, cy = card_center(index)
            image[cy - 20:cy + 22, cx - 12:cx + 30] = (30 + index * 10, 70, 110)
            image[cy + 49:cy + 77, cx - 60:cx + 60] = (255, 244, 223)
            image[cy + 27:cy + 45, cx - 43:cx - 23] = (70, 70, 70)
    return image


def _select(image, index, offset=0):
    cx, cy = card_center(index)
    for top in (-19, 3, 21):
        image[cy + top + offset:cy + top + offset + 5, cx + 52:cx + 56] = (245, 195, 80)


class _OCR:
    def __init__(self, results=None):
        self.results = iter(results or [('林德喵', 0.99)] * 2 + [('30', 0.99)] * 2)

    def det(self, _image):
        value = next(self.results)
        return [] if value is None else [(value[0], [], value[1])]


class ScanListTests(unittest.TestCase):
    def test_actual_centers_and_invalid_indices(self):
        self.assertEqual(card_center(0), (784, 185))
        self.assertEqual(card_center(11), (1174, 477))
        for index in (-1, 12, '0'):
            with self.subTest(index=index), self.assertRaises(ValueError):
                card_center(index)

    def test_unique_selected_ring_all_positions_with_small_offset(self):
        for index in range(12):
            with self.subTest(index=index):
                image = _image()
                _select(image, index, offset=3)
                self.assertEqual(selected_card(image), index)

    def test_none_multiple_or_solid_yellow_not_selected(self):
        image = _image()
        self.assertIsNone(selected_card(image))
        _select(image, 0)
        _select(image, 1)
        self.assertIsNone(selected_card(image))
        image = _image()
        image[156:225, 828:851] = (245, 195, 80)
        self.assertIsNone(selected_card(image))

    def test_card_identity_requires_two_high_confidence_agreeing_variants(self):
        self.assertEqual(read_card_identity(_image(), 0, _OCR()), ('林德喵', 30))
        cases = [[('林德喵', 0.89)], [('林德喵', 0.99), ('弗里喵', 0.99)],
                 [('林德喵', float('nan'))], [None]]
        for results in cases:
            with self.subTest(results=results):
                self.assertIsNone(read_card_identity(_image(), 0, _OCR(results)))

    def test_unknown_or_disagreeing_level_keeps_confirmed_name(self):
        for levels in ([('30', 0.99), ('29', 0.99)], [('30', 0.89)],
                       [('30', float('nan'))], [None]):
            with self.subTest(levels=levels):
                outputs = [('林德喵', 0.99)] * 2 + levels
                self.assertEqual(read_card_identity(_image(), 0, _OCR(outputs)), ('林德喵', None))

    def test_occupied_cards_are_never_empty(self):
        for index in range(12):
            self.assertFalse(card_is_empty(_image(), index))

    def test_empty_requires_face_label_and_gap_to_agree(self):
        image = _image(occupied=False)
        self.assertTrue(card_is_empty(image, 0))
        image[234:262, 724:844] = (255, 244, 223)
        self.assertFalse(card_is_empty(image, 0))
        self.assertFalse(card_is_empty(np.zeros_like(image), 0))

    def test_last_row_empty_ignores_footer_below_list(self):
        image = _image(occupied=False)
        image[550:720] = (255, 255, 255)
        self.assertTrue(card_is_empty(image, 11))

    def test_scroll_handle_confirms_top_lower_and_absent(self):
        image = _image()
        self.assertIsNone(cattery_at_top(image))
        image[142:177, 1248:1256] = (250, 200, 80)
        self.assertTrue(cattery_at_top(image))
        image[132:550, 1245:1259] = (231, 223, 222)
        image[280:315, 1248:1256] = (250, 200, 80)
        self.assertFalse(cattery_at_top(image))
        image[132:550, 1245:1259] = (250, 200, 80)
        self.assertIsNone(cattery_at_top(image))

    def test_bad_resolution_is_unknown(self):
        image = np.zeros((360, 640, 3), dtype=np.uint8)
        self.assertIsNone(selected_card(image))
        self.assertIsNone(read_card_identity(image, 0, _OCR()))
        self.assertFalse(card_is_empty(image, 0))
        self.assertFalse(cattery_order_unchanged(image, image))

    def test_selection_and_lock_changes_do_not_change_order(self):
        before = _image()
        _select(before, 0)
        after = _image()
        _select(after, 8)
        # 锁图标在头像左上角，选择指针也不位于被比较的脸部中心。
        after[136:158, 743:765] = (230, 190, 50)
        after[159:180, 745:765] = (230, 190, 50)
        after[239:244, 760:769] = (245, 195, 80)
        after[228:230, 746:749] = (245, 195, 80)
        self.assertTrue(cattery_order_unchanged(before, after))

    def test_changed_face_label_level_and_viewport_are_detected(self):
        before = _image()
        for area in ((775, 175, 797, 195), (730, 238, 750, 253), (742, 215, 750, 228)):
            after = before.copy()
            left, top, right, bottom = area
            after[top:bottom, left:right] = 180
            self.assertFalse(cattery_order_unchanged(before, after))
        self.assertFalse(cattery_order_unchanged(before, np.roll(before, 5, axis=0)))

    def test_selection_does_not_hide_reorder_of_selected_card(self):
        before = _image()
        after = before.copy()
        _select(before, 0)
        _select(after, 1)
        after[165:207, 772:814] = (120, 70, 110)
        self.assertFalse(cattery_order_unchanged(before, after))


if __name__ == '__main__':
    unittest.main()
