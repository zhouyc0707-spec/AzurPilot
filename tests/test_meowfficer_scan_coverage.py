"""正文跑马灯与图标闪光不能掩盖第五行；位移仍须有唯一的静态标题证据。"""

import unittest
from unittest.mock import Mock, patch

import numpy as np

from module.meowfficer.scan_capture import (PANEL_AREA, TalentRow, _rgb_variants, _visible_rows,
                                           capture_current_cat)
from module.meowfficer.scan_coverage import SCROLL_AREA, TalentCoverage, measure_talent_shift
from module.meowfficer.score import Talent
from module.meowfficer.scan_utils import _crop, _mean_diff


NAMES = ('炮击新手·主力', '新人雷击士·潜艇', '装填新手·驱逐', '其徐如林', '侵略如火')
BOX = [[0, 0], [30, 0], [30, 12], [0, 12]]


def _animated_talent_frame(offset=0, *, animated=False, title_indices=None,
                           periodic=False, change_titles=False, changed_title_indices=(),
                           row_borders=True):
    """构造五张匿名卡片，滚动正文和闪光图标分别变化，标题固定。

    行距与裁切来自已确认的天赋页几何；随机笔画只标识匿名行，不模拟 OCR 输出。
    正文、图标内部不承担位移证据，避免夹具把动画当作页面切换。
    """
    rng = np.random.default_rng(202610070921)
    canvas = np.full((1300, 500, 3), (231, 223, 222), dtype=np.uint8)
    for index in range(5):
        top = 1 + index * 102
        canvas[top:top + 87] = (245, 239, 229)
        if row_borders:
            canvas[top:top + 87, 12:18] = (206, 231, 239)
            canvas[top:top + 87, 91:98] = (206, 231, 239)
        # 图标中心具有逐帧闪光，不能用大范围像素差替代图标行框。
        icon = np.full((71, 62, 3), (82, 184, 220), dtype=np.uint8)
        icon[8:59, 15:46] = (243, 243, 243) if animated else (35, 60, 85)
        canvas[top + 8:top + 79, 24:86] = icon
        if title_indices is None or index in title_indices:
            # 每行具备足够的深色笔画。周期夹具故意复用同一标题，无法证明滚动几行。
            title_rng = np.random.default_rng(42 if periodic else index + 420)
            strokes = title_rng.random((19, 116)) < 0.38
            if change_titles or index in changed_title_indices:
                strokes = np.roll(strokes, 17, axis=1)
            title = np.full((19, 116, 3), (231, 216, 196), dtype=np.uint8)
            title[strokes] = (113, 101, 84)
            canvas[top + 10:top + 29, 119:235] = title
        # 模拟正文超过行宽后的横向跑马灯，两帧相同标题下的正文会明显不同。
        body = np.full((14, 244, 3), (245, 239, 229), dtype=np.uint8)
        body_strokes = rng.random((10, 228)) < 0.30
        if animated:
            body_strokes = np.roll(body_strokes, 47, axis=1)
        body[2:12, 8:236][body_strokes] = (149, 150, 147)
        canvas[top + 55:top + 69, 116:360] = body
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[152:588, 744:1244] = canvas[offset:offset + 436]
    return image


class _AnimatedDevice:
    """每次截图改变跑马灯与图标明暗，但滑动只在顶部0和底部75间改变位置。"""

    def __init__(self, *, mutate_at_bottom=False):
        self.offset = 0
        self.animated = False
        self.image = _animated_talent_frame()
        self.mutate_at_bottom = mutate_at_bottom
        self.downward_swipes = 0
        self.swipes = []
        self.screenshots = 0

    def screenshot(self):
        self.screenshots += 1
        self.animated = not self.animated
        changes = {2} if self.mutate_at_bottom and self.downward_swipes >= 2 else ()
        self.image = _animated_talent_frame(self.offset, animated=self.animated,
                                           changed_title_indices=changes)

    def swipe(self, start, end, duration):
        self.swipes.append((start, end, duration))
        if start[1] > end[1]:
            self.downward_swipes += 1
            self.offset = 75
        else:
            self.offset = 0

    def click_record_remove(self, name):
        return 0


class _AnonymousOCR:
    """识别匿名标题像素ID并返回已知天赋名，不加载OCR模型或真实账户配置。"""

    def __init__(self):
        self.templates = []
        for offset in (0, 75):
            image = _animated_talent_frame(offset)
            for top, bottom in _visible_rows(image):
                if top <= 152 or bottom >= 588:
                    continue
                index = round((top + offset - 153) / 102)
                for prepared in _rgb_variants(_crop(image, (855, top + 3, 1120, top + 42))).values():
                    self.templates.append((prepared, NAMES[index]))

    def det(self, image):
        if image.shape[:2] == (186, 345):
            return [('SSR', BOX, 0.99)]
        for prepared, name in self.templates:
            if np.array_equal(image, prepared):
                return [(name, BOX, 0.99)]
        return []


class AnimatedTalentCoverageTests(unittest.TestCase):
    """只依据静态行标题和独立行框恢复滚动，保留缺证据保护。"""

    def test_body_marquee_and_icon_flash_do_not_hide_valid_scroll(self):
        before = _animated_talent_frame()
        for distance in (40, 75, 160):
            with self.subTest(distance=distance):
                after = _animated_talent_frame(distance, animated=True)
                self.assertEqual(measure_talent_shift(before, after), distance)

    def test_static_titles_confirm_zero_shift_despite_body_and_icon_animation(self):
        self.assertEqual(measure_talent_shift(_animated_talent_frame(),
                                             _animated_talent_frame(animated=True)), 0)

    def test_fifth_partial_row_is_completed_after_actual_scroll(self):
        before, after = _animated_talent_frame(), _animated_talent_frame(75, animated=True)
        shift = measure_talent_shift(before, after)
        self.assertEqual(shift, 75)
        coverage = TalentCoverage(origin=153, row_height=87)
        for image, offset, movement in ((before, 0, 0), (after, 75, shift)):
            rows = []
            for top, bottom in _visible_rows(image):
                # 已识别的部分第五行在第二张截图中获得完整行框和标题。
                actual_top = bottom - 87 if top == 152 else top
                index = round((actual_top + offset - 153) / 102)
                rows.append(TalentRow(top, bottom, talent=Talent(
                    name=f'匿名天赋{index + 1}', line=f'匿名天赋线{index + 1}', level=1),
                    complete=top > 152 and bottom < 588))
            coverage.add(rows, shift=movement)
        self.assertTrue(coverage.finish(), coverage.reasons)
        self.assertEqual(len(coverage.talents()), 5)
        self.assertEqual(coverage.talents()[-1].name, '匿名天赋5')

    def test_card_borders_and_animated_body_without_titles_do_not_prove_scroll(self):
        self.assertIsNone(measure_talent_shift(
            _animated_talent_frame(title_indices=set()),
            _animated_talent_frame(75, animated=True, title_indices=set())))

    def test_only_one_full_title_in_overlap_cannot_prove_scroll(self):
        self.assertIsNone(measure_talent_shift(
            _animated_talent_frame(title_indices={2}),
            _animated_talent_frame(75, animated=True, title_indices={2})))

    def test_periodic_identical_titles_cannot_prove_which_rows_moved(self):
        for distance in (40, 75, 160):
            with self.subTest(distance=distance):
                self.assertIsNone(measure_talent_shift(
                    _animated_talent_frame(periodic=True),
                    _animated_talent_frame(distance, animated=True, periodic=True)))

    def test_conflicting_title_pixels_cannot_be_excused_as_body_animation(self):
        self.assertIsNone(measure_talent_shift(
            _animated_talent_frame(),
            _animated_talent_frame(75, animated=True, change_titles=True)))

    def test_one_changed_overlap_title_rejects_two_other_matching_titles(self):
        # 三个重叠完整标题中的一个改变，不能用另外两个一致标题覆盖这条矛盾证据。
        self.assertIsNone(measure_talent_shift(
            _animated_talent_frame(),
            _animated_talent_frame(75, animated=True, changed_title_indices={2})))

    def test_one_changed_title_prevents_false_zero_shift_confirmation(self):
        self.assertIsNone(measure_talent_shift(
            _animated_talent_frame(),
            _animated_talent_frame(animated=True, changed_title_indices={2})))

    def test_animation_without_any_title_evidence_is_unknown_not_zero(self):
        self.assertIsNone(measure_talent_shift(
            _animated_talent_frame(title_indices=set()),
            _animated_talent_frame(animated=True, title_indices=set())))

    def test_titles_without_independent_row_borders_do_not_prove_scroll(self):
        self.assertIsNone(measure_talent_shift(
            _animated_talent_frame(row_borders=False),
            _animated_talent_frame(75, animated=True, row_borders=False)))

    def test_complete_capture_reads_fifth_talent_with_continuous_animation(self):
        before, animated = _animated_talent_frame(), _animated_talent_frame(animated=True)
        self.assertGreater(_mean_diff(_crop(before, PANEL_AREA), _crop(animated, PANEL_AREA)), 3)
        device = _AnimatedDevice()
        scanner = Mock(device=device)
        scanner._read_current_cat.return_value = ('限定蒂奇喵', 30)
        with patch('module.meowfficer.score_lock.detail_page_confirmed', return_value=True):
            capture = capture_current_cat(scanner, _AnonymousOCR(), '限定蒂奇喵', 30)
        self.assertTrue(capture.identity_confirmed)
        self.assertTrue(capture.talents_complete, capture.reasons)
        self.assertTrue(capture.complete, capture.reasons)
        self.assertEqual(capture.reasons, [])
        self.assertEqual([talent.name for talent in capture.talents], list(NAMES))
        self.assertEqual(len(device.swipes), 5)
        self.assertGreater(device.screenshots, len(device.swipes))

    def test_zero_shift_title_mutation_keeps_full_capture_protected(self):
        device = _AnimatedDevice(mutate_at_bottom=True)
        scanner = Mock(device=device)
        scanner._read_current_cat.return_value = ('限定蒂奇喵', 30)
        with patch('module.meowfficer.score_lock.detail_page_confirmed', return_value=True):
            capture = capture_current_cat(scanner, _AnonymousOCR(), '限定蒂奇喵', 30)
        self.assertTrue(capture.identity_confirmed)
        self.assertFalse(capture.talents_complete)
        self.assertFalse(capture.complete)
        self.assertIn('天赋滚动前后重叠位移未能确认，不能排除漏行', capture.reasons)
        self.assertIn('未确认天赋列表底部', capture.reasons)


if __name__ == '__main__':
    unittest.main()
