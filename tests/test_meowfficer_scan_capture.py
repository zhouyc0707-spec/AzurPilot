"""已有猫锁定前的身份与天赋完整性回归；不连接设备、不加载真实配置。"""

import unittest
from unittest.mock import Mock, patch

import numpy as np

from module.exception import GameStuckError, GameTooManyClickError, RequestHumanTakeover
from module.meowfficer.scan_capture import (IDENTITY_AREA, ScanCapture, _exact_breed,
                                           _read_rows, _visible_rows, capture_current_cat)


NAMES = ['炮击新手·主力', '新人雷击士·潜艇', '装填新手·驱逐']
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
        self.assertIn('同一完整面板重复读取结果不一致', capture.reasons)

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
        self.assertIn('天赋需滚动读取，尚不能独立证明全部行完整', capture.reasons)
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


if __name__ == '__main__':
    unittest.main()
