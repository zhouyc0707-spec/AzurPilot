"""完整未习得栏作为已学天赋终点的离线回归，不连接模拟器或修改账号。"""

import unittest
from unittest.mock import Mock, patch

import numpy as np

from module.exception import GameStuckError, GameTooManyClickError, RequestHumanTakeover
from module.meowfficer.scan_capture import (TalentRow, _read_empty_row, _read_row_records,
                                           capture_current_cat)
from module.meowfficer.scan_coverage import TalentCoverage, measure_talent_shift
from module.meowfficer.score import TALENT_INDEX, Talent, normalize
from tests.test_meowfficer_scan_capture import BOX, _Device
from tests.test_meowfficer_scan_coverage import (NAMES, _AnimatedDevice, _AnonymousOCR,
                                               _animated_talent_frame)


def _empty_frame(learned=3, offset=0, *, count=5):
    """保留真实行距、浅青空图标与空正文，末行仍由视口裁切。"""
    image = _animated_talent_frame(offset)
    for index in range(learned, count):
        top = 153 + index * 102 - offset

        def paint(first, last, left, right, color):
            first, last = max(152, first), min(588, last)
            if first < last:
                image[first:last, left:right] = color

        paint(top, top + 87, 744, 1244, (245, 239, 229))
        paint(top, top + 87, 756, 762, (206, 231, 239))
        paint(top, top + 87, 835, 842, (206, 231, 239))
        paint(top + 8, top + 79, 765, 833, (206, 231, 239))
        # 匿名标签笔画占图标小部分；不能只用 OCR 缺失推断为空栏。
        paint(top + 35, top + 42, 784, 811, (100, 110, 120))
    return image


class _EmptyOCR(_AnonymousOCR):
    """只替代 OCR 模型，行框、空正文、标记双变体仍走实际读取。"""

    def __init__(self, *, marker='未习得', confidence=0.99, confidences=(), marker_outputs=()):
        super().__init__()
        self.marker = marker
        self.confidence = confidence
        self.confidences = list(confidences)
        self.marker_outputs = list(marker_outputs)
        self.empty_calls = 0

    def det(self, image):
        if image.shape[:2] == (132, 204):
            index = self.empty_calls
            self.empty_calls += 1
            if self.marker_outputs:
                result = self.marker_outputs[min(index, len(self.marker_outputs) - 1)]
                if isinstance(result, Exception):
                    raise result
                return result
            confidence = (self.confidences[min(index, len(self.confidences) - 1)]
                          if self.confidences else self.confidence)
            return [(self.marker, BOX, confidence)] if self.marker else []
        return super().det(image)


class FirstEmptyCoverageTests(unittest.TestCase):
    """首空栏只缩短连续前缀，不能覆盖漏行、已学后缀或永久矛盾。"""

    @staticmethod
    def _row(index, *, empty=False, complete=True, name=None):
        ref = None if empty else TALENT_INDEX[normalize(name or NAMES[index])]
        talent = Talent(ref.name, ref.line, ref.level, ref.kind) if ref else None
        return TalentRow(153 + index * 102, 240 + index * 102,
                         talent, empty=empty, complete=complete)

    def test_first_empty_ignores_only_unconfirmed_tail_and_has_no_side_effects(self):
        coverage = TalentCoverage(153, 87)
        tail = TalentRow(561, 588)
        coverage.add([self._row(i) for i in range(3)] + [self._row(3, empty=True), tail],
                     issues=['天赋行被裁切或行框不完整'])
        before = (dict(coverage.covered), dict(coverage.pending), list(coverage.reasons))
        self.assertEqual(coverage.first_empty_end(), 3)
        self.assertEqual((coverage.covered, coverage.pending, coverage.reasons), before)
        self.assertTrue(coverage.finish(at_first_empty=True), coverage.reasons)
        self.assertEqual([talent.name for talent in coverage.talents()], list(NAMES[:3]))

    def test_default_finish_still_requires_full_tail(self):
        coverage = TalentCoverage(153, 87)
        coverage.add([self._row(i) for i in range(3)] + [self._row(3, empty=True),
                     TalentRow(561, 588)])
        self.assertEqual(coverage.first_empty_end(), 3)
        self.assertFalse(coverage.finish())
        self.assertIn('第 5 行天赋未能完整确认，不能排除漏读', coverage.reasons)

    def test_missing_prefix_probe_does_not_prevent_later_recovery(self):
        coverage = TalentCoverage(153, 87)
        coverage.add([self._row(0), self._row(2), self._row(3, empty=True)])
        self.assertIsNone(coverage.first_empty_end())
        self.assertEqual(coverage.reasons, [])
        coverage.add([self._row(1)])
        self.assertEqual(coverage.first_empty_end(), 3)
        self.assertTrue(coverage.finish(at_first_empty=True))

    def test_unknown_or_incomplete_empty_cannot_define_endpoint(self):
        for marker in (TalentRow(459, 546), self._row(3, empty=True, complete=False)):
            with self.subTest(marker=marker):
                coverage = TalentCoverage(153, 87)
                coverage.add([self._row(i) for i in range(3)] + [marker])
                self.assertIsNone(coverage.first_empty_end())
                self.assertFalse(coverage.finish(at_first_empty=True))

    def test_incomplete_known_prefix_cannot_be_ignored(self):
        coverage = TalentCoverage(153, 87)
        coverage.add([self._row(0), self._row(1, complete=False), self._row(2),
                     self._row(3, empty=True)])
        self.assertIsNone(coverage.first_empty_end())

    def test_known_learned_row_after_empty_forbids_fast_endpoint(self):
        for complete in (True, False):
            with self.subTest(complete=complete):
                coverage = TalentCoverage(153, 87)
                coverage.add([self._row(0), self._row(1, empty=True),
                             self._row(2, complete=complete)])
                self.assertIsNone(coverage.first_empty_end())
                self.assertEqual(coverage.reasons, [])

    def test_permanent_title_conflict_cannot_be_hidden_by_empty_endpoint(self):
        coverage = TalentCoverage(153, 87)
        coverage.add([self._row(i) for i in range(3)] + [self._row(3, empty=True)])
        coverage.add([self._row(0, name='熟练炮手·主力')])
        self.assertIsNone(coverage.first_empty_end())
        self.assertFalse(coverage.finish(at_first_empty=True))
        self.assertIn('同一天赋行跨截图识别结果不一致', coverage.reasons)

    def test_first_empty_without_learned_rows_is_only_endpoint_not_score_permission(self):
        coverage = TalentCoverage(153, 87)
        coverage.add([self._row(0, empty=True)])
        self.assertEqual(coverage.first_empty_end(), 0)
        self.assertTrue(coverage.finish(at_first_empty=True))
        self.assertEqual(coverage.talents(), [])


class EmptyMarkerEvidenceTests(unittest.TestCase):
    """正向标记、浅青空图标、空正文及完整几何必须同时成立。"""

    def test_empty_rows_need_two_exact_marker_reads(self):
        ocr = _EmptyOCR()
        rows = _read_row_records(_empty_frame(), ocr, [])
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(row.complete and row.talent is not None for row in rows[:3]))
        self.assertTrue(rows[3].empty)
        self.assertTrue(rows[3].complete)
        self.assertFalse(rows[4].complete)
        self.assertFalse(rows[4].empty)
        self.assertEqual(ocr.empty_calls, 2)

    def test_label_confidence_must_be_finite_and_within_acceptance_interval(self):
        for confidence in (0.89, 1.01, float('inf'), float('nan'), -1):
            with self.subTest(confidence=confidence):
                ocr = _EmptyOCR(confidence=confidence)
                self.assertFalse(_read_empty_row(_empty_frame(), 459, ocr, []))
        for confidence in (0.9, 1):
            with self.subTest(confidence=confidence):
                self.assertTrue(_read_empty_row(
                    _empty_frame(), 459, _EmptyOCR(confidence=confidence), []))

    def test_unknown_or_missing_label_is_not_empty(self):
        for marker in ('末习得', '未习', '未知', None):
            with self.subTest(marker=marker):
                rows = _read_row_records(_empty_frame(), _EmptyOCR(marker=marker), [])
                self.assertFalse(rows[3].empty)
                self.assertFalse(rows[3].complete)

    def test_one_weak_variant_cannot_be_overridden_by_strong_variant(self):
        ocr = _EmptyOCR(confidences=(0.99, 0.89))
        rows = _read_row_records(_empty_frame(), ocr, [])
        self.assertFalse(rows[3].empty)
        self.assertFalse(rows[3].complete)

    def test_missing_confidence_multiple_boxes_or_disagreeing_variant_cannot_end_prefix(self):
        cases = (
            [[{'text': '未习得', 'box': BOX}]],
            [[('未习得', BOX, 0.99), ('其他文字', BOX, 0.99)]],
            [[('未习得', BOX, 0.99)], [('未知', BOX, 0.99)]],
        )
        for outputs in cases:
            with self.subTest(outputs=outputs):
                rows = _read_row_records(_empty_frame(), _EmptyOCR(marker_outputs=outputs), [])
                coverage = TalentCoverage(153, 87)
                coverage.add(rows)
                self.assertFalse(rows[3].empty)
                self.assertFalse(rows[3].complete)
                self.assertIsNone(coverage.first_empty_end())

    def test_game_control_errors_from_marker_ocr_still_propagate(self):
        for error in (GameStuckError, GameTooManyClickError, RequestHumanTakeover):
            with self.subTest(error=error), self.assertRaises(error):
                _read_empty_row(_empty_frame(), 459,
                                _EmptyOCR(marker_outputs=[error('流程中断')]), [])

    def test_visible_effect_text_cannot_be_misclassified_by_empty_label_ocr(self):
        image = _empty_frame()
        image[494:501, 880:1030] = (70, 70, 70)
        rows = _read_row_records(image, _EmptyOCR(), [])
        self.assertFalse(rows[3].empty)
        self.assertFalse(rows[3].complete)

    def test_empty_label_without_cyan_icon_is_not_empty(self):
        image = _empty_frame()
        image[483:527, 765:833] = (90, 90, 90)
        rows = _read_row_records(image, _EmptyOCR(), [])
        self.assertFalse(rows[3].empty)
        self.assertFalse(rows[3].complete)


class FirstEmptyCaptureTests(unittest.TestCase):
    """通过真实状态稳定、上下滑动、位移和最终复核验证快路径。"""

    def setUp(self):
        page = patch('module.meowfficer.score_lock.detail_page_confirmed', return_value=True)
        self.page = page.start()
        self.addCleanup(page.stop)
        diagnostics = patch('module.meowfficer.scan_diagnostics.save_incomplete_capture')
        self.diagnostics = diagnostics.start()
        self.addCleanup(diagnostics.stop)

    @staticmethod
    def _scanner(image=None, *, top_frames=(), bottom_frames=()):
        device = _Device(_empty_frame() if image is None else image,
                         top_frames=top_frames, bottom_frames=bottom_frames)
        scanner = Mock(device=device)
        scanner._read_current_cat.return_value = ('限定蒂奇喵', 30)
        return scanner

    @staticmethod
    def _capture(scanner, ocr=None):
        return capture_current_cat(scanner, _EmptyOCR() if ocr is None else ocr,
                                   '限定蒂奇喵', 30)

    @staticmethod
    def _bottom_swipes(device):
        return [swipe for swipe in device.swipes if swipe[0][1] > swipe[1][1]]

    def test_three_learned_and_full_empty_skip_bottom_and_retry(self):
        scanner = self._scanner()
        with patch('module.meowfficer.scan_retry.retry_talent_rows') as retry:
            capture = self._capture(scanner)
        self.assertTrue(capture.complete, capture.reasons)
        self.assertTrue(capture.talents_complete)
        self.assertEqual([talent.name for talent in capture.talents], list(NAMES[:3]))
        self.assertEqual(len(scanner.device.swipes), 2)
        self.assertEqual(self._bottom_swipes(scanner.device), [])
        self.assertEqual(scanner._read_current_cat.call_count, 2)
        retry.assert_not_called()

    def test_weak_later_empty_slot_does_not_trigger_useless_retry(self):
        scanner = self._scanner(_empty_frame(learned=2))
        ocr = _EmptyOCR(confidences=(0.99, 0.99, 0.89))
        with patch('module.meowfficer.scan_retry.retry_talent_rows') as retry:
            capture = self._capture(scanner, ocr)
        self.assertTrue(capture.complete, capture.reasons)
        self.assertEqual(len(capture.talents), 2)
        self.assertEqual(self._bottom_swipes(scanner.device), [])
        retry.assert_not_called()

    def test_partial_first_empty_still_scrolls_to_complete_marker_then_stops(self):
        top, bottom = _empty_frame(learned=4), _empty_frame(learned=4, offset=75)
        self.assertEqual(measure_talent_shift(top, bottom), 75)
        scanner = self._scanner(top, bottom_frames=(bottom,))
        capture = self._capture(scanner)
        self.assertTrue(capture.complete, capture.reasons)
        self.assertEqual([talent.name for talent in capture.talents], list(NAMES[:4]))
        self.assertEqual(len(self._bottom_swipes(scanner.device)), 1)

    def test_unknown_low_or_invalid_marker_keeps_full_bottom_flow_and_protection(self):
        for kwargs in ({'marker': '未知'}, {'confidence': 0.89}, {'confidence': 1.01}):
            with self.subTest(kwargs=kwargs):
                scanner = self._scanner(bottom_frames=(_empty_frame(offset=75),))
                capture = self._capture(scanner, _EmptyOCR(**kwargs))
                self.assertFalse(capture.complete)
                self.assertFalse(capture.talents_complete)
                self.assertGreaterEqual(len(self._bottom_swipes(scanner.device)), 3)

    def test_missing_middle_row_before_empty_never_becomes_complete_prefix(self):
        frames = []
        for offset in (0, 75):
            image = _empty_frame(offset=offset)
            image[255 - offset:342 - offset, 744:1244] = (231, 223, 222)
            frames.append(image)
        scanner = self._scanner(frames[0], bottom_frames=(frames[1],))
        capture = self._capture(scanner)
        self.assertFalse(capture.complete)
        self.assertFalse(capture.talents_complete)
        self.assertIn('天赋行框间距异常，不能排除漏行', capture.reasons)
        self.assertGreaterEqual(len(self._bottom_swipes(scanner.device)), 1)

    def test_learned_row_after_empty_forces_full_read_instead_of_fast_stop(self):
        frames = []
        for offset in (0, 75):
            image, learned = _empty_frame(learned=1, offset=offset), _animated_talent_frame(offset)
            # 首空行后明确出现另一已学行；不能用排序假设忽略这条真实证据。
            first, last = 357 - offset, 444 - offset
            image[first:last, 744:1244] = learned[first:last, 744:1244]
            frames.append(image)
        scanner = self._scanner(frames[0], bottom_frames=(frames[1],))
        capture = self._capture(scanner)
        self.assertTrue(capture.complete, capture.reasons)
        self.assertEqual([talent.name for talent in capture.talents], [NAMES[0], NAMES[2]])
        self.assertEqual(len(self._bottom_swipes(scanner.device)), 3)

    def test_first_empty_does_not_excuse_unconfirmed_top(self):
        # 两次稳定的不动画面仍因首框偏移而无法正向证明已回到顶部。
        scanner = self._scanner(_empty_frame(offset=5))
        capture = self._capture(scanner)
        self.assertFalse(capture.complete)
        self.assertIn('未确认天赋列表顶部', capture.reasons)
        self.assertGreaterEqual(len(self._bottom_swipes(scanner.device)), 2)

    def test_final_identity_change_rejects_fast_complete(self):
        scanner = self._scanner()
        scanner._read_current_cat.side_effect = [('限定蒂奇喵', 30), ('约翰喵', 30)]
        capture = self._capture(scanner)
        self.assertFalse(capture.identity_confirmed)
        self.assertFalse(capture.complete)
        self.assertFalse(capture.talents_complete)
        self.assertEqual(self._bottom_swipes(scanner.device), [])

    def test_final_page_loss_rejects_fast_complete(self):
        scanner = self._scanner()
        # 初始画面、两次向顶部滑动前正常，最终复核时离开天赋页。
        self.page.side_effect = [True, True, True, False]
        capture = self._capture(scanner)
        self.assertFalse(capture.identity_confirmed)
        self.assertFalse(capture.complete)
        self.assertEqual(self._bottom_swipes(scanner.device), [])

    def test_unknown_breed_remains_protected_after_fast_talent_completion(self):
        scanner = self._scanner()
        scanner._read_current_cat.return_value = ('自定义猫名', 30)
        capture = capture_current_cat(scanner, _EmptyOCR(), '自定义猫名', 30)
        self.assertTrue(capture.talents_complete, capture.reasons)
        self.assertFalse(capture.complete)
        self.assertIn('自定义猫名未能确定原始猫种', capture.reasons)

    def test_zero_learned_fast_endpoint_does_not_permit_scoring(self):
        scanner = self._scanner(_empty_frame(learned=0))
        capture = self._capture(scanner)
        self.assertFalse(capture.complete)
        self.assertFalse(capture.talents_complete)
        self.assertEqual(capture.talents, [])

    def test_all_learned_without_empty_still_confirms_physical_bottom(self):
        device = _AnimatedDevice()
        scanner = Mock(device=device)
        scanner._read_current_cat.return_value = ('限定蒂奇喵', 30)
        capture = self._capture(scanner, _AnonymousOCR())
        self.assertTrue(capture.complete, capture.reasons)
        self.assertEqual([talent.name for talent in capture.talents], list(NAMES))
        self.assertEqual(len(self._bottom_swipes(device)), 3)


if __name__ == '__main__':
    unittest.main()
