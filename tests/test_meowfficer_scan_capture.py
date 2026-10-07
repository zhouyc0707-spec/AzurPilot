"""已有猫锁定前的身份与天赋完整性回归；不连接设备、不加载真实配置。"""

import unittest
from unittest.mock import Mock, patch

import numpy as np

from module.exception import GameStuckError, GameTooManyClickError, RequestHumanTakeover
from module.meowfficer.scan_capture import (IDENTITY_AREA, ScanCapture, _exact_breed,
                                           _read_rarity, _read_rows, _rgb_variants, _visible_rows,
                                           capture_current_cat)
from module.meowfficer.scan_coverage import SCROLL_AREA, measure_talent_shift


NAMES = ['炮击新手·主力', '新人雷击士·潜艇', '装填新手·驱逐']
FULL_NAMES = NAMES + ['其徐如林', '侵略如火', '不动如山']
BOX = [[0, 0], [30, 0], [30, 12], [0, 12]]


def _frame(count=3, offset=0, variant=0):
    """用匿名的行框几何画面验证独立行计数，不保存真实账号截图。"""
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[152:588, 744:1244] = (231, 223, 222)
    if variant:
        # 页面内容移动时，变化来自图像而非 OCR 返回的名字。
        image[152:588, 1100:1244] = (50, 50, 50)
    for index in range(count):
        top = 153 + 102 * index + offset
        bottom = top + 87
        start, end = max(152, top), min(588, bottom)
        if start < end:
            image[start:end, 756:762] = (206, 231, 239)
            image[start:end, 835:842] = (206, 231, 239)
            # 已学行具有可见的匿名标题笔画，不能被新增的空白正文分支识别为空位。
            text_top, text_bottom = max(152, top + 18), min(588, top + 23)
            if text_top < text_bottom:
                image[text_top:text_bottom, 880:920] = (70, 70, 70)
    return image


class _OCR:
    """按 OCR 输入区域吐出品质或标题，保留真实 det 的三元组结构。"""

    def __init__(self, rarity='SSR', names=None, title_outputs=None, rarity_outputs=None):
        self.rarity = rarity
        self.names = list(NAMES if names is None else names)
        self.title_outputs = list(title_outputs) if title_outputs is not None else None
        self.rarity_outputs = list(rarity_outputs) if rarity_outputs is not None else None
        self.title_calls = 0
        self.rarity_calls = 0

    def det(self, image):
        if image.shape[:2] == (186, 345):
            index = self.rarity_calls
            self.rarity_calls += 1
            if self.rarity_outputs is not None:
                value = self.rarity_outputs[min(index, len(self.rarity_outputs) - 1)]
                if isinstance(value, Exception):
                    raise value
                return value
            return [(self.rarity, BOX, 0.99)] if self.rarity else []
        index = self.title_calls
        self.title_calls += 1
        if self.title_outputs is not None:
            value = self.title_outputs[min(index, len(self.title_outputs) - 1)]
            if isinstance(value, Exception):
                raise value
            return value
        name = self.names[(index // 2) % len(self.names)]
        return [(name, BOX, 0.99)]


class _Device:
    """截图与滑动都只改变内存里的画面。"""

    def __init__(self, frame, top_frames=(), bottom_frames=()):
        self.image = frame.copy()
        self.top_frames = list(top_frames)
        self.bottom_frames = list(bottom_frames)
        self.swipes = []
        self.screenshots = 0
        self.pending = None

    def screenshot(self):
        self.screenshots += 1
        if self.pending is not None:
            self.image = self.pending.copy()
            self.pending = None

    def click_record_remove(self, button):
        """普通图像夹具不模拟历史，真实队列回归在专用模块中覆盖。"""
        return 0

    def swipe(self, start, end, duration):
        self.swipes.append((start, end, duration))
        frames = self.bottom_frames if start[1] > end[1] else self.top_frames
        if frames:
            self.pending = frames.pop(0)


class _Scanner:
    """只提供扫描器现有的读取身份和设备接口。"""

    def __init__(self, image=None, name='林德喵', level=5, top_frames=(), bottom_frames=()):
        self.device = _Device(_frame() if image is None else image, top_frames, bottom_frames)
        self._read_current_cat = Mock(return_value=(name, level))


def _coverage_frame(count=6, offset=0, missing_row=None, misplaced_row=None):
    """对同一匿名列表做真实窗口裁剪，重叠像素可独立证明滚动位移。"""
    rng = np.random.default_rng(20261007)
    # 纵向纹理不重复，避免只凭相同边框行距猜出位移。
    canvas = np.repeat(rng.integers(200, 215, size=(1200, 500, 1), dtype=np.uint8), 3, axis=2)
    for index in range(count):
        top = 1 + 102 * index + (7 if index == misplaced_row else 0)
        bottom = top + 87
        if index != missing_row:
            canvas[top:bottom, 12:18] = (206, 231, 239)
            canvas[top:bottom, 91:98] = (206, 231, 239)
        # 标题仅用匿名灰阶 ID，假 OCR 会返回对应的已知原名。
        canvas[top + 3:top + 42, 111:376] = 20 + index * 15
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[152:588, 744:1244] = canvas[offset:offset + 436]
    image[0, 0, 0] = offset % 256
    image[0, 1, 0] = 1
    return image


class _CoverageOCR:
    """从原始匿名行 ID 读取标题，双变体都经同一公开 OCR 接口。"""

    def __init__(self, device, override=None):
        self.device = device
        self.override = override
        self.title_calls = 0

    def det(self, image):
        if image.shape[:2] in ((62, 115), (186, 345)):
            return [('SSR', BOX, 0.99)]
        code = int(round(float(np.median(image))))
        index = round((code - 20) / 15)
        if index < 0 or index >= len(FULL_NAMES) or abs(code - (20 + index * 15)) > 1:
            return []
        self.title_calls += 1
        name = FULL_NAMES[index]
        confidence = 0.99
        if self.override is not None:
            changed = self.override(index, int(self.device.image[0, 0, 0]), self.title_calls)
            if isinstance(changed, Exception):
                raise changed
            if changed is None:
                return []
            name, confidence = changed
        return [(name, BOX, confidence)]


class RowCompletenessTests(unittest.TestCase):
    """行框独立于 OCR，持续漏掉的行也不能当成完整。"""

    def test_anonymous_frames_count_every_row_without_ocr(self):
        self.assertEqual(_visible_rows(_frame()), [(153, 240), (255, 342), (357, 444)])

    def test_partial_fifth_row_is_counted_and_blocks_completeness(self):
        frame = _frame(5)
        self.assertEqual(_visible_rows(frame)[-1], (561, 588))
        reasons = []
        talents, complete = _read_rows(frame, _OCR(names=NAMES + ['其徐如林']), reasons)
        self.assertEqual(len(talents), 4)
        self.assertFalse(complete)
        self.assertIn('天赋行被裁切或行框不完整', reasons)

    def test_exact_titles_match_every_independent_row(self):
        reasons = []
        talents, complete = _read_rows(_frame(), _OCR(), reasons)
        self.assertTrue(complete)
        self.assertEqual([talent.name for talent in talents], NAMES)
        self.assertEqual(reasons, [])

    def test_blank_title_despite_visible_icon_blocks_completeness(self):
        reasons = []
        talents, complete = _read_rows(_frame(), _OCR(title_outputs=[[]]), reasons)
        self.assertEqual(talents, [])
        self.assertFalse(complete)
        self.assertIn('天赋图标行与识别标题数量不一致', reasons)

    def test_no_icon_rows_does_not_mean_complete_zero_talents(self):
        reasons = []
        talents, complete = _read_rows(_frame(0), _OCR(), reasons)
        self.assertEqual(talents, [])
        self.assertFalse(complete)

    def test_fuzzy_low_confidence_unknown_and_multiple_titles_are_rejected(self):
        cases = [
            [('新人炮击土·主力X', BOX, 0.99)],
            [(NAMES[0], BOX, 0.89)],
            [('没有收录的天赋', BOX, 0.99)],
            [(NAMES[0], BOX, 0.99), (NAMES[1], BOX, 0.99)],
            [(NAMES[0], BOX, float('nan'))],
        ]
        for results in cases:
            with self.subTest(results=results):
                reasons = []
                _, complete = _read_rows(_frame(1), _OCR(title_outputs=[results]), reasons)
                self.assertFalse(complete)
                self.assertTrue(reasons)

    def test_two_ocr_variants_must_agree(self):
        reasons = []
        outputs = [[(NAMES[0], BOX, 0.99)], [(NAMES[1], BOX, 0.99)]]
        talents, complete = _read_rows(_frame(1), _OCR(title_outputs=outputs), reasons)
        self.assertFalse(complete)
        self.assertEqual(talents, [])
        self.assertIn('同一天赋行多次识别不一致', reasons)

    def test_duplicate_talent_lines_cannot_cover_two_real_rows(self):
        reasons = []
        _, complete = _read_rows(_frame(2), _OCR(names=[NAMES[0], NAMES[0]]), reasons)
        self.assertFalse(complete)
        self.assertIn('不同天赋行识别为重复天赋线', reasons)

    def test_missing_middle_icon_cannot_silently_reduce_the_expected_row_count(self):
        frame = _frame()
        frame[255:342, 756:762] = (231, 223, 222)
        frame[255:342, 835:842] = (231, 223, 222)
        reasons = []
        _, complete = _read_rows(frame, _OCR(names=[NAMES[0], NAMES[2]]), reasons)
        self.assertFalse(complete)
        self.assertIn('天赋行框间距异常，不能排除漏行', reasons)

    def test_ocr_error_marks_incomplete_but_control_errors_propagate(self):
        reasons = []
        _, complete = _read_rows(_frame(1), _OCR(title_outputs=[RuntimeError('离线 OCR 故障')]), reasons)
        self.assertFalse(complete)
        self.assertIn('天赋标题 OCR 失败', reasons)
        for error in (GameStuckError, GameTooManyClickError, RequestHumanTakeover):
            with self.subTest(error=error):
                with self.assertRaises(error):
                    _read_rows(_frame(1), _OCR(title_outputs=[error('流程中断')]), [])


class CaptureSafetyTests(unittest.TestCase):
    """完整单屏可自动决策，身份/品质/滚动的不确定性都必须显式保留。"""

    def setUp(self):
        # 标题模板另有真实资源测试；这里聚焦读取、身份与滑动的时序。
        self.page_guard = patch('module.meowfficer.score_lock.detail_page_confirmed', return_value=True)
        self.guard = self.page_guard.start()
        self.addCleanup(self.page_guard.stop)

    def test_complete_initial_cat_confirms_both_boundaries_and_identity(self):
        scanner = _Scanner()
        capture = capture_current_cat(scanner, _OCR(), '林德喵', 5)
        self.assertIsInstance(capture, ScanCapture)
        self.assertTrue(capture.complete)
        self.assertTrue(capture.identity_confirmed)
        self.assertEqual(capture.rarity, 'SSR')
        self.assertEqual(capture.breed, '林德喵')
        self.assertEqual([talent.name for talent in capture.talents], NAMES)
        self.assertEqual(capture.reasons, [])
        self.assertEqual(len(scanner.device.swipes), 4)
        self.assertEqual(capture.identity_image.shape, (60, 295, 3))

    def test_complete_purple_initial_cat_uses_exact_sr_marker(self):
        scanner = _Scanner(image=_frame(2), name='莫赫喵')
        capture = capture_current_cat(scanner, _OCR(rarity='SR', names=NAMES[:2]), '莫赫喵', 5)
        self.assertTrue(capture.complete)
        self.assertEqual(capture.rarity, 'SR')
        self.assertEqual(len(capture.talents), 2)

    def test_explicit_limited_original_name_is_exact_but_custom_names_are_not(self):
        self.assertEqual(_exact_breed('限定蒂奇喵'), '蒂奇喵')
        self.assertEqual(_exact_breed('蒂 奇 喵'), '蒂奇喵')
        for name in ('风帆', '蒂奇喵喵', '我的蒂奇喵', '林德猫', '限定风帆'):
            with self.subTest(name=name):
                self.assertIsNone(_exact_breed(name))
        scanner = _Scanner(name='风帆')
        capture = capture_current_cat(scanner, _OCR(), '风帆', 5)
        self.assertFalse(capture.complete)
        self.assertIsNone(capture.breed)
        self.assertIn('自定义猫名未能确定原始猫种', capture.reasons)
        self.assertTrue(capture.talents_complete)

    def test_continuous_capture_defers_history_cleanup_to_accepted_comparison(self):
        scanner = _Scanner()
        scanner.device.click_record_remove = Mock()
        capture = capture_current_cat(scanner, _OCR(), '林德喵', 5, reset_history=False)
        self.assertTrue(capture.talents_complete)
        self.assertTrue(capture.complete)
        scanner.device.click_record_remove.assert_not_called()

    def test_known_blue_skips_all_talent_reads_and_scrolls(self):
        scanner = _Scanner(name='乔治喵')
        ocr = _OCR(rarity='R')
        capture = capture_current_cat(scanner, ocr, '乔治喵', 5)
        self.assertEqual(capture.rarity, 'R')
        self.assertTrue(capture.identity_confirmed)
        self.assertEqual(capture.talents, [])
        self.assertEqual(ocr.title_calls, 0)
        self.assertEqual(scanner.device.swipes, [])

    def test_known_gold_cannot_be_treated_as_blue_due_to_wrong_rarity_ocr(self):
        capture = capture_current_cat(_Scanner(), _OCR(rarity='R'), '林德喵', 5)
        self.assertIsNone(capture.rarity)
        self.assertFalse(capture.complete)
        self.assertIn('品质标记与已知猫种不一致', capture.reasons)

    def test_unknown_or_conflicting_rarity_is_never_assumed_blue(self):
        cases = [
            _OCR(rarity=None),
            _OCR(rarity='5R'),
            _OCR(rarity_outputs=[[('SSR', BOX, 0.99)], [('SR', BOX, 0.99)]]),
            _OCR(rarity_outputs=[[('R', BOX, 0.4)]]),
        ]
        for ocr in cases:
            with self.subTest(rarity=ocr.rarity, outputs=ocr.rarity_outputs):
                capture = capture_current_cat(_Scanner(), ocr, '林德喵', 5)
                self.assertIsNone(capture.rarity)
                self.assertFalse(capture.complete)
                self.assertGreater(ocr.title_calls, 0)

    def test_wrong_or_missing_name_and_level_do_not_even_start_scrolling(self):
        for name, level in [('克雷喵', 5), ('', 5), ('林德喵', 10), ('林德喵', None)]:
            with self.subTest(name=name, level=level):
                scanner = _Scanner(name=name, level=level)
                capture = capture_current_cat(scanner, _OCR(), '林德喵', 5)
                self.assertFalse(capture.identity_confirmed)
                self.assertFalse(capture.complete)
                self.assertEqual(scanner.device.swipes, [])

    def test_failed_cat_cannot_contaminate_the_next_complete_capture(self):
        scanner = _Scanner(name='上一只')
        failed = capture_current_cat(scanner, _OCR(), '林德喵', 5)
        scanner._read_current_cat.return_value = ('林德喵', 5)
        complete = capture_current_cat(scanner, _OCR(), '林德喵', 5)
        self.assertFalse(failed.identity_confirmed)
        self.assertTrue(complete.complete)
        self.assertEqual(complete.reasons, [])

    def test_repeated_ocr_of_the_same_complete_panel_must_agree(self):
        top = [[(name, BOX, 0.99)] for name in NAMES for _ in range(2)]
        changed = [[('其徐如林', BOX, 0.99)]] * 6
        capture = capture_current_cat(_Scanner(), _OCR(title_outputs=top + changed), '林德喵', 5)
        self.assertFalse(capture.complete)
        self.assertIn('同一天赋行跨截图识别结果不一致', capture.reasons)

    def test_name_or_identity_image_change_during_capture_is_not_usable(self):
        scanner = _Scanner()
        scanner._read_current_cat.side_effect = [('林德喵', 5), ('克雷喵', 5)]
        capture = capture_current_cat(scanner, _OCR(), '林德喵', 5)
        self.assertFalse(capture.identity_confirmed)
        self.assertFalse(capture.complete)
        final = _frame()
        x0, y0, x1, y1 = IDENTITY_AREA
        final[y0:y1, x0:x1] = 40
        scanner = _Scanner(bottom_frames=[final])
        with self.assertRaises(RequestHumanTakeover):
            capture_current_cat(scanner, _OCR(), '林德喵', 5)
        self.assertEqual(len(scanner.device.swipes), 3)

    def test_top_scroll_restores_a_previously_scrolled_single_screen(self):
        scanner = _Scanner(image=_frame(offset=-35), top_frames=[_frame()])
        capture = capture_current_cat(scanner, _OCR(), '林德喵', 5)
        self.assertTrue(capture.complete)
        self.assertEqual(len(scanner.device.swipes), 5)

    def test_scrolling_despite_same_ocr_names_cannot_be_false_complete(self):
        scanner = _Scanner(bottom_frames=[_frame(variant=1)])
        capture = capture_current_cat(scanner, _OCR(), '林德喵', 5)
        self.assertFalse(capture.complete)
        self.assertTrue(any('位移' in reason or '覆盖' in reason for reason in capture.reasons), capture.reasons)
        self.assertEqual(len(capture.talents), 3)

    def test_clipped_extra_row_remains_incomplete_even_if_the_four_titles_are_known(self):
        scanner = _Scanner(image=_frame(5))
        capture = capture_current_cat(scanner, _OCR(names=NAMES + ['其徐如林']), '林德喵', 5)
        self.assertFalse(capture.complete)
        self.assertEqual(len(capture.talents), 4)
        self.assertIn('天赋行被裁切或行框不完整', capture.reasons)

    def test_scroll_limit_is_bounded_and_cannot_confirm_an_unreached_bottom(self):
        frames = []
        for index in range(8):
            frame = _frame()
            frame[152:588, 1100:1244] = (index * 20, index * 20, index * 20)
            frames.append(frame)
        scanner = _Scanner(bottom_frames=frames)
        capture = capture_current_cat(scanner, _OCR(), '林德喵', 5)
        self.assertFalse(capture.complete)
        self.assertLessEqual(len(scanner.device.swipes), 8)
        self.assertIn('未确认天赋列表底部', capture.reasons)

    def test_device_control_exception_is_not_converted_into_partial_success(self):
        scanner = _Scanner()
        scanner.device.swipe = Mock(side_effect=GameStuckError('离线设备卡住'))
        with self.assertRaises(GameStuckError):
            capture_current_cat(scanner, _OCR(), '林德喵', 5)
        scanner = _Scanner()
        scanner.device.screenshot = Mock(side_effect=RequestHumanTakeover('截图不可用'))
        with self.assertRaises(RequestHumanTakeover):
            capture_current_cat(scanner, _OCR(), '林德喵', 5)

    def test_initial_unknown_page_and_mid_scan_page_loss_stop_before_another_swipe(self):
        scanner = _Scanner()
        self.guard.return_value = False
        with self.assertRaises(RequestHumanTakeover):
            capture_current_cat(scanner, _OCR(), '林德喵', 5)
        self.assertEqual(scanner.device.swipes, [])
        scanner = _Scanner()
        self.guard.side_effect = [True, True, False]
        with self.assertRaises(RequestHumanTakeover):
            capture_current_cat(scanner, _OCR(), '林德喵', 5)
        self.assertEqual(len(scanner.device.swipes), 1)

    def test_bad_screenshot_geometry_and_failed_top_confirmation_are_protected(self):
        scanner = _Scanner(image=np.zeros((360, 640, 3), dtype=np.uint8))
        capture = capture_current_cat(scanner, _OCR(), '林德喵', 5)
        self.assertFalse(capture.complete)
        self.assertFalse(capture.identity_confirmed)
        scanner = _Scanner(image=_frame(offset=20))
        capture = capture_current_cat(scanner, _OCR(), '林德喵', 5)
        self.assertFalse(capture.complete)
        self.assertIn('未确认天赋列表顶部', capture.reasons)


class MultiFrameCoverageTests(unittest.TestCase):
    """顶部到底部逐行覆盖才能完整，未知位移和矛盾识别不能取最高级掩盖。"""

    def setUp(self):
        guard = patch('module.meowfficer.score_lock.detail_page_confirmed',
                      side_effect=lambda image: bool(image[0, 1, 0]))
        guard.start()
        self.addCleanup(guard.stop)
        # 只替换 OCR 预处理，不替换行框检测、位移估计或覆盖证明。
        variants = patch('module.meowfficer.scan_capture.build_variants',
                         side_effect=lambda image: {'original': image.copy(), 'contrast': image.copy()})
        variants.start()
        self.addCleanup(variants.stop)

    @staticmethod
    def _scanner(count=6, offsets=(102, 163), **frame_kwargs):
        return _Scanner(image=_coverage_frame(count=count, **frame_kwargs),
                        bottom_frames=[_coverage_frame(count=count, offset=offset, **frame_kwargs)
                                       for offset in offsets])

    @staticmethod
    def _capture(scanner, override=None):
        return capture_current_cat(scanner, _CoverageOCR(scanner.device, override), '林德喵', 5)

    def test_six_real_rows_complete_after_overlapping_scroll_and_partial_edge_recovery(self):
        scanner = self._scanner()
        top_rows = _visible_rows(scanner.device.image)
        self.assertEqual(top_rows[-1], (561, 588))
        capture = self._capture(scanner)
        self.assertTrue(capture.complete, capture.reasons)
        self.assertTrue(capture.identity_confirmed)
        self.assertEqual([talent.name for talent in capture.talents], FULL_NAMES)
        self.assertEqual(capture.reasons, [])
        self.assertLessEqual(len(scanner.device.swipes), 8)

    def test_five_rows_complete_with_top_and_bottom_partial_rows_seen_fully_elsewhere(self):
        scanner = self._scanner(count=5, offsets=(61,))
        capture = self._capture(scanner)
        self.assertTrue(capture.complete, capture.reasons)
        self.assertEqual([talent.name for talent in capture.talents], FULL_NAMES[:5])
        self.assertEqual(capture.reasons, [])

    def test_temporarily_unknown_full_row_title_can_be_confirmed_in_overlap(self):
        def title(index, offset, call):
            return None if index == 3 and offset == 0 else (FULL_NAMES[index], 0.99)

        capture = self._capture(self._scanner(), title)
        self.assertTrue(capture.complete, capture.reasons)
        self.assertEqual([talent.name for talent in capture.talents], FULL_NAMES)

    def test_persistent_unknown_or_low_confidence_title_keeps_that_row_uncovered(self):
        for failure in ('unknown', 'low_confidence'):
            with self.subTest(failure=failure):
                def title(index, offset, call):
                    if index == 3:
                        return ('尚未收录的天赋', 0.99) if failure == 'unknown' else (FULL_NAMES[index], 0.7)
                    return FULL_NAMES[index], 0.99

                capture = self._capture(self._scanner(), title)
                self.assertFalse(capture.complete)
                self.assertTrue(any('第 4 行' in reason or '未能完整' in reason
                                    for reason in capture.reasons), capture.reasons)

    def test_same_global_row_level_conflict_is_not_hidden_by_taking_maximum(self):
        def title(index, offset, call):
            name = '熟练雷击士·潜艇' if index == 1 and offset > 0 else FULL_NAMES[index]
            return name, 0.99

        capture = self._capture(self._scanner(), title)
        self.assertFalse(capture.complete)
        self.assertTrue(any('跨截图' in reason and '不一致' in reason for reason in capture.reasons),
                        capture.reasons)
        line = next(talent for talent in capture.talents if talent.line == '新人雷击士·潜艇')
        self.assertEqual(line.level, 1)

    def test_same_global_row_known_name_conflict_is_not_an_extra_covered_row(self):
        def title(index, offset, call):
            name = '其疾如风' if index == 2 and offset > 0 else FULL_NAMES[index]
            return name, 0.99

        capture = self._capture(self._scanner(), title)
        self.assertFalse(capture.complete)
        self.assertTrue(any('跨截图' in reason and '不一致' in reason for reason in capture.reasons),
                        capture.reasons)

    def test_high_confidence_variant_name_or_level_conflict_cannot_be_hidden_by_prior_coverage(self):
        for conflicting in ('其疾如风', '熟练装填手·驱逐'):
            with self.subTest(conflicting=conflicting):
                observed = []

                def title(index, offset, call):
                    if index == 2 and offset >= 163:
                        name = conflicting if call % 2 == 0 else FULL_NAMES[index]
                        observed.append(name)
                        return name, 0.99
                    return FULL_NAMES[index], 0.99

                capture = self._capture(self._scanner(), title)
                self.assertIn(FULL_NAMES[2], observed)
                self.assertIn(conflicting, observed)
                self.assertFalse(capture.complete)
                self.assertTrue(capture.identity_confirmed)
                self.assertTrue(any('多次识别不一致' in reason for reason in capture.reasons),
                                capture.reasons)
                self.assertNotIn(conflicting, [talent.name for talent in capture.talents])

    def test_missing_middle_icon_or_misaligned_row_never_proves_full_coverage(self):
        for kwargs in ({'missing_row': 2}, {'misplaced_row': 3}):
            with self.subTest(kwargs=kwargs):
                capture = self._capture(self._scanner(**kwargs))
                self.assertFalse(capture.complete)
                self.assertTrue(any('漏' in reason or '位置' in reason or '间距' in reason
                                    for reason in capture.reasons), capture.reasons)

    def test_large_jump_without_verifiable_overlap_remains_incomplete(self):
        scanner = self._scanner(offsets=(300,))
        capture = self._capture(scanner)
        self.assertFalse(capture.complete)
        self.assertTrue(any('位移' in reason for reason in capture.reasons), capture.reasons)
        self.assertLessEqual(len(scanner.device.swipes), 8)

    def test_uncompleted_bottom_partial_row_is_not_hidden_by_known_visible_titles(self):
        scanner = self._scanner(offsets=())
        capture = self._capture(scanner)
        self.assertFalse(capture.complete)
        self.assertTrue(any('未能完整' in reason or '裁切' in reason for reason in capture.reasons),
                        capture.reasons)

    def test_missing_last_row_frame_cannot_hide_visible_remaining_title(self):
        scanner = self._scanner(missing_row=5)
        capture = self._capture(scanner)
        self.assertFalse(capture.complete)
        self.assertTrue(any('底部' in reason and '未对应行框' in reason for reason in capture.reasons),
                        capture.reasons)

    def test_identity_or_page_loss_after_first_scroll_stops_before_next_gesture(self):
        for failure in ('identity', 'page'):
            with self.subTest(failure=failure):
                frame = _coverage_frame(offset=102)
                if failure == 'identity':
                    x0, y0, x1, y1 = IDENTITY_AREA
                    frame[y0:y1, x0:x1] = 40
                else:
                    frame[0, 1, 0] = 0
                scanner = _Scanner(image=_coverage_frame(), bottom_frames=[frame])
                with self.assertRaises(RequestHumanTakeover):
                    self._capture(scanner)
                self.assertLessEqual(len(scanner.device.swipes), 3)

    def test_control_error_in_overlap_ocr_still_propagates(self):
        error = GameStuckError('匿名覆盖 OCR 控制异常')

        def title(index, offset, call):
            return error if offset > 0 else (FULL_NAMES[index], 0.99)

        with self.assertRaises(GameStuckError) as raised:
            self._capture(self._scanner(), title)
        self.assertIs(raised.exception, error)


class CaptureColorAndShiftTests(unittest.TestCase):
    """识别输入的颜色边界及重复空位滚动的歧义保护，不加载 OCR 模型。"""

    def test_rgb_crop_is_copied_as_contiguous_bgr_at_preprocessing_boundary(self):
        image = np.array([[(239, 77, 22), (12, 154, 241)],
                          [(42, 231, 98), (167, 31, 208)]], dtype=np.uint8)
        original = image.copy()
        outputs = {'plain': object(), 'clahe': object()}
        with patch('module.meowfficer.scan_capture.build_variants', return_value=outputs) as prepare:
            self.assertIs(_rgb_variants(image), outputs)
        actual = prepare.call_args.args[0]
        np.testing.assert_array_equal(actual, original[..., ::-1])
        self.assertTrue(actual.flags.c_contiguous)
        self.assertFalse(np.shares_memory(actual, image))
        np.testing.assert_array_equal(image, original)

    def test_real_plain_preprocessing_preserves_bgr_color_without_model_or_source_mutation(self):
        image = np.full((24, 32, 3), (239, 77, 22), dtype=np.uint8)
        original = image.copy()
        variants = _rgb_variants(image)
        self.assertEqual(set(variants), {'plain', 'clahe'})
        np.testing.assert_array_equal(variants['plain'][30, 30], (22, 77, 239))
        gray = variants['clahe']
        np.testing.assert_array_equal(gray[..., 0], gray[..., 1])
        np.testing.assert_array_equal(gray[..., 1], gray[..., 2])
        np.testing.assert_array_equal(image, original)

    def test_periodic_empty_row_visuals_cannot_prove_which_scroll_distance_occurred(self):
        left, top, right, bottom = SCROLL_AREA
        width, height = right - left, bottom - top
        # 匿名空位每 102 像素重复；没有唯一标题或纹理可区分移动了几行。
        row = np.full((102, width, 3), (231, 223, 222), dtype=np.uint8)
        row[1:88, 8:14] = (206, 231, 239)
        row[1:88, 87:94] = (206, 231, 239)
        row[25:68, 17:85] = (206, 231, 239)
        row[34:45, 30:70] = (120, 120, 120)
        canvas = np.tile(row, (12, 1, 1))
        before = np.zeros((720, 1280, 3), dtype=np.uint8)
        before[top:bottom, left:right] = canvas[:height]
        for offset in (40, 75, 160):
            with self.subTest(offset=offset):
                after = before.copy()
                after[top:bottom, left:right] = canvas[offset:offset + height]
                difference = np.abs(before[top:bottom, left:right].astype(np.int16)
                                    - after[top:bottom, left:right].astype(np.int16)).mean()
                self.assertGreater(difference, 1)
                self.assertIsNone(measure_talent_shift(before, after))


class RarityFallbackTests(unittest.TestCase):
    """品质补读保留原始强证据，两份颜色字形均须精确且达到原置信度门槛。"""

    @staticmethod
    def _text(name='SSR', confidence=0.99):
        return [(name, BOX, confidence)]

    @staticmethod
    def _read(outputs):
        ocr = _OCR(rarity_outputs=outputs)
        reasons = []
        rarity = _read_rarity(_frame(), ocr, reasons)
        return rarity, reasons, ocr

    def test_two_high_confidence_original_reads_do_not_invoke_glyph_fallback(self):
        for name in ('SSR', 'SR', 'R'):
            with self.subTest(name=name):
                with patch('module.meowfficer.scan_capture._rarity_glyph_variants') as fallback:
                    rarity, reasons, ocr = self._read([self._text(name), self._text(name, 0.9)])
                self.assertEqual(rarity, name)
                self.assertEqual(reasons, [])
                self.assertEqual(ocr.rarity_calls, 2)
                fallback.assert_not_called()

    def test_one_strong_original_and_one_weak_read_require_two_consistent_glyph_reads(self):
        for name in ('SSR', 'SR', 'R'):
            for strong_first in (True, False):
                with self.subTest(name=name, strong_first=strong_first):
                    base = [self._text(name), self._text(name, 0.85)]
                    if not strong_first:
                        base.reverse()
                    rarity, reasons, ocr = self._read(base + [self._text(name), self._text(name, 0.9)])
                    self.assertEqual(rarity, name)
                    self.assertEqual(reasons, [])
                    self.assertEqual(ocr.rarity_calls, 4)

    def test_one_empty_original_still_needs_two_exact_glyph_reads_and_another_strong_original(self):
        for strong_first in (True, False):
            with self.subTest(strong_first=strong_first):
                base = [self._text(), []] if strong_first else [[], self._text()]
                rarity, reasons, ocr = self._read(base + [self._text(), self._text()])
                self.assertEqual(rarity, 'SSR')
                self.assertEqual(reasons, [])
                self.assertEqual(ocr.rarity_calls, 4)

    def test_glyph_low_confidence_unknown_conflict_multiple_or_nan_is_rejected(self):
        invalid = [self._text(confidence=0.89), self._text('SK'), self._text('SR'),
                   self._text() + self._text('R'), self._text(confidence=float('nan'))]
        for bad in invalid:
            for invalid_first in (True, False):
                with self.subTest(bad=bad, invalid_first=invalid_first):
                    glyphs = [bad, self._text()] if invalid_first else [self._text(), bad]
                    rarity, reasons, ocr = self._read([self._text(), self._text(confidence=0.85)] + glyphs)
                    self.assertIsNone(rarity)
                    self.assertTrue(reasons)
                    self.assertEqual(ocr.rarity_calls, 3 if invalid_first else 4)

    def test_only_one_successful_glyph_read_never_confirms_rarity(self):
        for valid_first in (True, False):
            with self.subTest(valid_first=valid_first):
                glyphs = [self._text(), []] if valid_first else [[], self._text()]
                rarity, reasons, ocr = self._read([self._text(), self._text(confidence=0.85)] + glyphs)
                self.assertIsNone(rarity)
                self.assertTrue(reasons)
                self.assertEqual(ocr.rarity_calls, 4 if valid_first else 3)

    def test_strong_unknown_conflicting_multiple_or_nan_originals_cannot_be_overridden(self):
        invalid = [self._text('SK'), self._text('SR'), self._text() + self._text('R'),
                   self._text(confidence=float('nan')), self._text(confidence='无效置信度')]
        for bad in invalid:
            with self.subTest(bad=bad):
                with patch('module.meowfficer.scan_capture._rarity_glyph_variants') as fallback:
                    rarity, reasons, ocr = self._read([self._text(), bad])
                self.assertIsNone(rarity)
                self.assertTrue(reasons)
                self.assertEqual(ocr.rarity_calls, 2)
                fallback.assert_not_called()

    def test_no_strong_original_evidence_never_triggers_glyph_fallback(self):
        bases = [[self._text(confidence=0.85), self._text(confidence=0.89)], [[], []],
                 [self._text(confidence=0.85), []]]
        for base in bases:
            with self.subTest(base=base):
                with patch('module.meowfficer.scan_capture._rarity_glyph_variants') as fallback:
                    rarity, reasons, ocr = self._read(base)
                self.assertIsNone(rarity)
                self.assertTrue(reasons)
                self.assertEqual(ocr.rarity_calls, 2)
                fallback.assert_not_called()

    def test_glyph_control_error_propagates_without_partial_confirmation(self):
        error = GameStuckError('品质字形 OCR 控制异常')
        with self.assertRaises(GameStuckError) as raised:
            self._read([self._text(), self._text(confidence=0.85), error])
        self.assertIs(raised.exception, error)


if __name__ == '__main__':
    unittest.main()
