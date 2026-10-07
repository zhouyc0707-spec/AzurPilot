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


def _glyph_image():
    """用相同姓名等级的匿名深色字形验证头像仍参与位置核验。"""
    image = _image()
    for index in range(12):
        cx, cy = card_center(index)
        name = image[cy + 49:min(cy + 77, 550), cx - 60:cx + 60]
        level = image[cy + 27:cy + 45, cx - 43:cx - 23]
        name[:] = (240, 228, 220)
        level[:] = (240, 228, 220)
        # 匿名的三枚几何字形，深色笔画和浅色间隙独立于账号及真实文字。
        for left in (28, 48, 68):
            name[5:19, left:left + 2] = (86, 82, 78)
            name[5:19, left + 10:left + 12] = (86, 82, 78)
            name[5:7, left:left + 12] = (86, 82, 78)
            name[11:13, left:left + 12] = (86, 82, 78)
            name[17:19, left:left + 12] = (86, 82, 78)
        for left in (1, 11):
            level[2:16, left:left + 2] = (86, 82, 78)
            level[2:16, left + 5:left + 7] = (86, 82, 78)
            level[2:4, left:left + 7] = (86, 82, 78)
            level[8:10, left:left + 7] = (86, 82, 78)
            level[14:16, left:left + 7] = (86, 82, 78)
    return image


def _selection_glow(image, index, color=(255, 255, 251)):
    """选择虚线环换位的浅色光晕，仅改变底板并保留可辨认的字形。"""
    cx, cy = card_center(index)
    name = image[cy + 49:min(cy + 77, 550), cx - 60:cx + 60]
    level = image[cy + 27:cy + 45, cx - 43:cx - 23]
    for region in (name, level):
        halo = np.zeros(region.shape[:2], dtype=bool)
        if region.shape[1] == 120:
            halo[2:7, 12:108] = True
            halo[17:22, 12:108] = True
        else:
            halo[:4] = True
            halo[15:] = True
        background = region.min(axis=2) > 150
        region[halo & background] = color
        # 选中效果轻微改变墨色，但不移动或抹掉文字轮廓。
        ink = region.max(axis=2) < 130
        region[ink] += 4


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


class SelectionGlowOrderTests(unittest.TestCase):
    """选择光晕换位不能直接放宽名单核验，先恢复同一选中状态再比较。"""

    @staticmethod
    def _pair(color=(255, 255, 251)):
        before, after = _glyph_image(), _glyph_image()
        _select(before, 0)
        _selection_glow(before, 0, color)
        _select(after, 5)
        _selection_glow(after, 5, color)
        return before, after

    @staticmethod
    def _aligned_pair():
        """恢复相同选择状态后，基准必须先通过完整名单比较。"""
        before, after = _glyph_image(), _glyph_image()
        for image in (before, after):
            _select(image, 0)
            _selection_glow(image, 0)
        return before, after

    def test_white_and_pale_yellow_halos_require_matching_selection_before_comparison(self):
        for color in ((255, 255, 251), (253, 246, 211)):
            with self.subTest(color=color):
                before, after = self._pair(color)
                self.assertEqual(selected_card(before), 0)
                self.assertEqual(selected_card(after), 5)
                self.assertFalse(cattery_order_unchanged(before, after))
                restored = _glyph_image()
                _select(restored, 0)
                _selection_glow(restored, 0, color)
                self.assertEqual(selected_card(restored), 0)
                self.assertTrue(cattery_order_unchanged(before, restored))

    def test_same_name_same_level_different_portrait_still_rejects_selected_card(self):
        for index in (0, 5):
            with self.subTest(index=index):
                before, after = self._aligned_pair()
                self.assertTrue(cattery_order_unchanged(before, after))
                cx, cy = card_center(index)
                # 每格的名字和等级完全相同，不能借相同文字忽略头像变化。
                after[cy - 20:cy + 22, cx - 12:cx + 30] = (150, 90, 180)
                self.assertFalse(cattery_order_unchanged(before, after))

    def test_real_name_or_level_ink_change_is_rejected_on_old_and_new_selection(self):
        for index in (0, 5):
            for changed in ('name', 'level'):
                with self.subTest(index=index, changed=changed):
                    before, after = self._aligned_pair()
                    self.assertTrue(cattery_order_unchanged(before, after))
                    cx, cy = card_center(index)
                    if changed == 'name':
                        # 抹掉第一枚字形并重画为不同的笔画，不只是背景变亮。
                        after[cy + 54:cy + 68, cx - 32:cx - 20] = (240, 228, 220)
                        after[cy + 54:cy + 68, cx - 28:cx - 24] = (86, 82, 78)
                    else:
                        # 等级徽章的首位由空心轮廓改为实心深色块。
                        after[cy + 29:cy + 43, cx - 42:cx - 35] = (86, 82, 78)
                    self.assertFalse(cattery_order_unchanged(before, after))

    def test_unselected_card_text_background_change_cannot_use_glow_exception(self):
        before, after = self._aligned_pair()
        self.assertTrue(cattery_order_unchanged(before, after))
        _selection_glow(after, 6)
        self.assertFalse(cattery_order_unchanged(before, after))

    def test_viewport_displacement_is_not_treated_as_selection_glow(self):
        before, after = self._aligned_pair()
        self.assertTrue(cattery_order_unchanged(before, after))
        after[132:550, 718:1245] = np.roll(after[132:550, 718:1245], 5, axis=0)
        self.assertFalse(cattery_order_unchanged(before, after))

    def test_unknown_or_multiple_selection_rings_do_not_authorize_glow_fallback(self):
        for unknown in ('missing', 'multiple'):
            with self.subTest(unknown=unknown):
                before, after = _glyph_image(), _glyph_image()
                _select(before, 0)
                _selection_glow(before, 0)
                _selection_glow(after, 5)
                if unknown == 'multiple':
                    _select(after, 0)
                    _select(after, 5)
                self.assertIsNone(selected_card(after))
                self.assertFalse(cattery_order_unchanged(before, after))

    def test_erased_or_excessively_masked_text_cannot_confirm_unchanged_identity(self):
        for erased in ('name', 'level', 'name_dark_mask'):
            with self.subTest(erased=erased):
                before, after = self._aligned_pair()
                self.assertTrue(cattery_order_unchanged(before, after))
                cx, cy = card_center(5)
                if erased.startswith('name'):
                    after[cy + 49:min(cy + 77, 550), cx - 60:cx + 60] = (
                        (100, 100, 100) if erased == 'name_dark_mask' else (255, 255, 251))
                else:
                    after[cy + 27:cy + 45, cx - 43:cx - 23] = (255, 255, 251)
                self.assertFalse(cattery_order_unchanged(before, after))


if __name__ == '__main__':
    unittest.main()
