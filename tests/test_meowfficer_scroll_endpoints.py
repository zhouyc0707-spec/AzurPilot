"""已校准五槽端点减少重复手势，所有截图与操作仅使用匿名内存夹具。"""

import unittest
from unittest.mock import Mock, patch

import numpy as np

from module.meowfficer.scan_capture import TalentRow, capture_current_cat
from module.meowfficer.scan_coverage import TalentCoverage
from module.meowfficer.scan_scroll import CN_TALENT_SLOT_COUNT, cn_talent_top_confirmed
from module.meowfficer.score import TALENT_INDEX, Talent, normalize
from tests.test_meowfficer_empty_tail import _EmptyOCR, _empty_frame
from tests.test_meowfficer_scan_capture import _CoverageOCR, _Device, _Scanner, _coverage_frame, _frame
from tests.test_meowfficer_scan_coverage import (NAMES, _AnimatedDevice, _AnonymousOCR,
                                               _animated_talent_frame)


class CalibratedTopEvidenceTests(unittest.TestCase):
    """顶部证据来自五个连续槽的完整几何，不能只检查首行顶齐。"""

    def setUp(self):
        server = patch('module.config.server.server', 'cn')
        server.start()
        self.addCleanup(server.stop)

    def test_anonymous_five_slot_top_is_positive_even_with_icon_animation(self):
        self.assertEqual(CN_TALENT_SLOT_COUNT, 5)
        for animated in (False, True):
            with self.subTest(animated=animated):
                self.assertTrue(cn_talent_top_confirmed(
                    _animated_talent_frame(animated=animated)))

    def test_middle_bottom_and_shorter_layouts_are_not_confirmed_top(self):
        for image in (_animated_talent_frame(5), _animated_talent_frame(75),
                      _frame(3), _frame(4), _frame(5, offset=5)):
            with self.subTest(image=image.shape):
                self.assertFalse(cn_talent_top_confirmed(image))

    def test_missing_middle_frame_or_malformed_full_frame_is_not_top(self):
        for first, last in ((255, 342), (230, 240), (575, 588)):
            with self.subTest(first=first, last=last):
                image = _animated_talent_frame()
                image[first:last, 756:762] = (231, 223, 222)
                image[first:last, 835:842] = (231, 223, 222)
                self.assertFalse(cn_talent_top_confirmed(image))

    def test_unknown_server_and_resolution_never_use_calibrated_top(self):
        image = _animated_talent_frame()
        for server in ('en', 'jp', 'tw', 'unknown'):
            with self.subTest(server=server), patch('module.config.server.server', server):
                self.assertFalse(cn_talent_top_confirmed(image))
        for shape in ((360, 640, 3), (720, 1280), (720, 1280, 4)):
            with self.subTest(shape=shape):
                self.assertFalse(cn_talent_top_confirmed(np.zeros(shape, dtype=np.uint8)))


class CompleteSlotEvidenceTests(unittest.TestCase):
    """槽数仅提供正向终点，完整行、矛盾和已观测额外行仍约束结束。"""

    @staticmethod
    def _row(index, *, complete=True, empty=False):
        ref = None if empty else TALENT_INDEX[normalize(NAMES[index])]
        talent = Talent(ref.name, ref.line, ref.level, ref.kind) if ref else None
        return TalentRow(153 + index * 102, 240 + index * 102,
                         talent, empty=empty, complete=complete)

    def test_five_complete_slots_prove_endpoint_without_probe_side_effects(self):
        coverage = TalentCoverage(153, 87)
        coverage.add([self._row(index) for index in range(5)])
        before = (dict(coverage.covered), dict(coverage.pending), list(coverage.reasons))
        self.assertTrue(coverage.all_slots_complete(5))
        self.assertEqual((coverage.covered, coverage.pending, coverage.reasons), before)
        self.assertTrue(coverage.finish(slot_count=5), coverage.reasons)
        self.assertEqual([talent.name for talent in coverage.talents()], list(NAMES))

    def test_missing_or_partial_slot_cannot_be_excused_by_total_count(self):
        for missing in (0, 2, 4):
            for incomplete in (False, True):
                with self.subTest(missing=missing, incomplete=incomplete):
                    coverage = TalentCoverage(153, 87)
                    coverage.add([self._row(index, complete=index != missing)
                                  for index in range(5)
                                  if incomplete or index != missing])
                    self.assertFalse(coverage.all_slots_complete(5))
                    self.assertEqual(coverage.reasons, [])
                    self.assertFalse(coverage.finish(slot_count=5))

    def test_zero_count_invalid_row_flags_and_permanent_conflict_cannot_fast_stop(self):
        coverage = TalentCoverage(153, 87)
        coverage.add([self._row(index) for index in range(5)])
        self.assertFalse(coverage.all_slots_complete(0))
        self.assertFalse(coverage.all_slots_complete(-1))
        coverage.covered[2].empty = True
        self.assertFalse(coverage.all_slots_complete(5))
        coverage.covered[2].empty = False
        coverage.reasons.append('同一天赋行跨截图识别结果不一致')
        self.assertFalse(coverage.all_slots_complete(5))

    def test_observed_sixth_slot_disables_fixed_five_slot_finish_even_when_partial(self):
        for complete in (False, True):
            with self.subTest(complete=complete):
                coverage = TalentCoverage(153, 87)
                coverage.add([self._row(index) for index in range(5)] + [
                    TalentRow(663, 750, empty=True, complete=complete)])
                self.assertFalse(coverage.all_slots_complete(5))
                self.assertFalse(coverage.finish(slot_count=5))


class SingleGestureCaptureTests(unittest.TestCase):
    """手势后仍用实际截图、标题重叠和身份复核；完整证据立即结束。"""

    def setUp(self):
        for target, value in (('module.config.server.server', 'cn'),
                              ('module.meowfficer.score_lock.detail_page_confirmed', True)):
            handle = patch(target, value) if target.endswith('.server') else patch(target, return_value=value)
            handle.start()
            self.addCleanup(handle.stop)
        diagnostics = patch('module.meowfficer.scan_diagnostics.save_incomplete_capture')
        self.diagnostics = diagnostics.start()
        self.addCleanup(diagnostics.stop)

    @staticmethod
    def _scanner(device):
        scanner = Mock(device=device)
        scanner._read_current_cat.return_value = ('限定蒂奇喵', 30)
        return scanner

    @staticmethod
    def _directions(device):
        return ['bottom' if start[1] > end[1] else 'top'
                for start, end, _duration in device.swipes]

    @staticmethod
    def _capture(scanner, ocr=None):
        return capture_current_cat(scanner, _AnonymousOCR() if ocr is None else ocr,
                                   '限定蒂奇喵', 30)

    def test_confirmed_top_five_learned_slots_require_only_bottom_gesture(self):
        device = _AnimatedDevice()
        scanner = self._scanner(device)
        capture = self._capture(scanner)
        self.assertTrue(capture.complete, capture.reasons)
        self.assertTrue(capture.talents_complete)
        self.assertEqual([talent.name for talent in capture.talents], list(NAMES))
        self.assertEqual(self._directions(device), ['bottom'])
        self.assertEqual(scanner._read_current_cat.call_count, 2)
        self.assertGreater(device.screenshots, len(device.swipes))

    def test_starting_at_bottom_restores_top_once_then_reads_fifth_row_once(self):
        device = _Device(_animated_talent_frame(75),
                         top_frames=(_animated_talent_frame(),),
                         bottom_frames=(_animated_talent_frame(75),))
        capture = self._capture(self._scanner(device))
        self.assertTrue(capture.complete, capture.reasons)
        self.assertEqual(self._directions(device), ['top', 'bottom'])

    def test_confirmed_top_first_empty_prefix_ends_without_gestures(self):
        device = _Device(_empty_frame())
        capture = self._capture(self._scanner(device), _EmptyOCR())
        self.assertTrue(capture.complete, capture.reasons)
        self.assertEqual([talent.name for talent in capture.talents], list(NAMES[:3]))
        self.assertEqual(self._directions(device), [])

    def test_fifth_row_still_clipped_cannot_fast_stop_after_failed_bottom_motion(self):
        device = _Device(_animated_talent_frame())
        capture = self._capture(self._scanner(device))
        self.assertFalse(capture.talents_complete)
        self.assertFalse(capture.complete)
        self.assertIn('第 5 行天赋未能完整确认，不能排除漏读', capture.reasons)
        self.assertEqual(self._directions(device), ['bottom', 'bottom'])

    def test_unverified_motion_cannot_merge_fifth_slot_or_continue_more_gestures(self):
        device = _Device(_animated_talent_frame(), bottom_frames=(
            _animated_talent_frame(75, animated=True, changed_title_indices={2}),))
        capture = self._capture(self._scanner(device))
        self.assertFalse(capture.complete)
        self.assertIn('天赋滚动前后重叠位移未能确认，不能排除漏行', capture.reasons)
        self.assertEqual(self._directions(device), ['bottom'])
        _capture, frames = self.diagnostics.call_args.args
        self.assertEqual(frames[-1].stage, 'unverified')
        self.assertIsNone(frames[-1].offset)

    def test_missing_extra_row_frame_cannot_hide_its_visible_title_after_fifth_slot(self):
        # 长布局的第六行没有边框，但底部仍有实际标题；固定五槽不能忽略这些笔画。
        scanner = _Scanner(image=_coverage_frame(missing_row=5),
                           name='限定蒂奇喵', level=30,
                           bottom_frames=(_coverage_frame(offset=102, missing_row=5),
                                          _coverage_frame(offset=163, missing_row=5)))
        with patch('module.meowfficer.scan_capture.build_variants',
                   side_effect=lambda image: {'original': image.copy(), 'contrast': image.copy()}):
            capture = self._capture(scanner, _CoverageOCR(scanner.device))
        self.assertFalse(capture.complete)
        self.assertFalse(capture.talents_complete)
        self.assertIn('列表底部仍有未对应行框的内容，不能排除漏行', capture.reasons)
        self.assertGreater(self._directions(scanner.device).count('bottom'), 1)

    def test_unknown_server_keeps_bounded_physical_endpoint_verification(self):
        device = _AnimatedDevice()
        with patch('module.config.server.server', 'en'):
            capture = self._capture(self._scanner(device))
        self.assertTrue(capture.complete, capture.reasons)
        self.assertEqual(self._directions(device), ['top', 'top', 'bottom', 'bottom', 'bottom'])

    def test_unmatched_calibration_cannot_fast_stop_even_if_five_slots_are_complete(self):
        device = _AnimatedDevice()
        with patch('module.meowfficer.scan_capture.cn_talent_top_confirmed', return_value=False):
            capture = self._capture(self._scanner(device))
        self.assertTrue(capture.complete, capture.reasons)
        self.assertEqual([talent.name for talent in capture.talents], list(NAMES))
        self.assertEqual(self._directions(device), ['top', 'top', 'bottom', 'bottom', 'bottom'])

    def test_final_identity_change_still_blocks_complete_five_slot_capture(self):
        device = _AnimatedDevice()
        scanner = self._scanner(device)
        scanner._read_current_cat.side_effect = [('限定蒂奇喵', 30), ('约翰喵', 30)]
        capture = self._capture(scanner)
        self.assertFalse(capture.identity_confirmed)
        self.assertFalse(capture.complete)
        self.assertEqual(self._directions(device), ['bottom'])


if __name__ == '__main__':
    unittest.main()
