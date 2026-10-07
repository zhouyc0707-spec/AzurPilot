"""同位置天赋补读的边界与覆盖保护；匿名画面和假 OCR，不连接真实设备。"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from module.exception import GameStuckError, GameTooManyClickError
from module.meowfficer.scan_capture import IDENTITY_AREA, PANEL_AREA, _read_row_records, _same_panel
from module.meowfficer.scan_coverage import TalentCoverage
from module.meowfficer.scan_retry import MAX_ROW_RETRIES, retry_talent_rows
from module.meowfficer.scan_utils import _crop, _mean_diff
from tests.test_meowfficer_scan_capture import BOX, NAMES, _OCR, _frame


class _RetryDevice:
    """只支持截图；若补读错误地点击、滑动或清历史，测试会直接失败。"""

    def __init__(self, image, frames=()):
        self.image = image.copy()
        self.frames = list(frames)
        self.screenshots = 0

    def screenshot(self):
        self.screenshots += 1
        if self.frames:
            self.image = self.frames.pop(0).copy()


class RowRetryTests(unittest.TestCase):
    """补读取新稳定帧，但不放宽精确名称或置信度，也不覆盖历史强矛盾。"""

    def setUp(self):
        self.image = _frame()
        self.identity = _crop(self.image, IDENTITY_AREA).copy()
        self.device = _RetryDevice(self.image)
        self.scanner = SimpleNamespace(device=self.device)
        self.page = patch('module.meowfficer.score_lock.detail_page_confirmed', return_value=True)
        self.page.start()
        self.addCleanup(self.page.stop)

    def _read(self, outputs=None, image=None):
        issues = []
        rows = _read_row_records(self.image if image is None else image,
                                 _OCR(title_outputs=outputs), issues)
        return rows, issues

    @staticmethod
    def _outputs(failed=1, failure=None):
        if failure is None:
            failure = [(NAMES[failed], BOX, 0.89)]
        return [[(name, BOX, 0.99)] if index != failed else failure
                for index, name in enumerate(NAMES) for _ in range(2)]

    def _retry(self, rows, issues, ocr=None):
        return retry_talent_rows(self.scanner, _OCR() if ocr is None else ocr,
                                 self.image, rows, issues, self.identity)

    def _coverage(self, initial, additions):
        coverage = TalentCoverage(153, 87)
        coverage.add(initial[0], issues=initial[1])
        for _frame, rows, issues in additions:
            coverage.add(rows, issues=issues)
        return coverage

    def test_complete_frame_does_not_add_screenshots_or_ocr(self):
        rows, issues = self._read()
        ocr = _OCR()
        accepted, batches, reason = self._retry(rows, issues, ocr)
        self.assertIs(accepted, self.image)
        self.assertEqual(batches, [])
        self.assertIsNone(reason)
        self.assertEqual(self.device.screenshots, 0)
        self.assertEqual(ocr.title_calls, 0)

    def test_normal_bottom_partial_row_does_not_trigger_retry(self):
        image = _frame(5)
        issues = []
        rows = _read_row_records(image, _OCR(names=NAMES + ['其徐如林']), issues)
        self.assertFalse(rows[-1].complete)
        ocr = _OCR()
        accepted, batches, reason = retry_talent_rows(
            self.scanner, ocr, image, rows, issues, self.identity)
        self.assertIs(accepted, image)
        self.assertEqual(batches, [])
        self.assertIsNone(reason)
        self.assertEqual(self.device.screenshots, 0)
        self.assertEqual(ocr.title_calls, 0)

    def test_normal_top_partial_row_does_not_trigger_retry(self):
        image = _frame(5, offset=-75)
        issues = []
        rows = _read_row_records(image, _OCR(names=NAMES + ['其徐如林']), issues)
        self.assertFalse(rows[0].complete)
        self.assertTrue(all(row.complete for row in rows[1:]))
        _, batches, reason = retry_talent_rows(self.scanner, _OCR(), image, rows, issues, self.identity)
        self.assertEqual(batches, [])
        self.assertIsNone(reason)
        self.assertEqual(self.device.screenshots, 0)

    def test_fresh_stable_frame_recovers_low_confidence_full_row(self):
        initial = self._read(self._outputs())
        ocr = _OCR()
        accepted, batches, reason = self._retry(*initial, ocr)
        self.assertIsNone(reason)
        self.assertEqual(len(batches), 1)
        self.assertGreaterEqual(self.device.screenshots, 2)
        self.assertEqual(ocr.title_calls, 6)
        self.assertTrue(all(row.complete for row in batches[0][1]))
        coverage = self._coverage(initial, batches)
        self.assertTrue(coverage.finish())
        self.assertEqual([talent.name for talent in coverage.talents()], NAMES)
        self.assertFalse((accepted != self.image).any())

    def test_retry_limit_is_two_and_persistent_low_confidence_remains_protected(self):
        initial = self._read(self._outputs())
        # 失败行第一个变体失败后该行不再读第二个；每批实际读取五次。
        outputs = [[(NAMES[0], BOX, 0.99)], [(NAMES[0], BOX, 0.99)],
                   [(NAMES[1], BOX, 0.89)],
                   [(NAMES[2], BOX, 0.99)], [(NAMES[2], BOX, 0.99)]]
        ocr = _OCR(title_outputs=outputs * MAX_ROW_RETRIES)
        _, batches, reason = self._retry(*initial, ocr)
        self.assertIsNone(reason)
        self.assertEqual(len(batches), 2)
        self.assertEqual(ocr.title_calls, 10)
        coverage = self._coverage(initial, batches)
        self.assertFalse(coverage.finish())
        self.assertIn('第 2 行天赋未能完整确认，不能排除漏读', coverage.reasons)
        self.assertIn('天赋标题不是高置信度的精确已知名称', coverage.reasons)

    def test_second_new_frame_can_complete_after_first_failed_retry(self):
        initial = self._read(self._outputs())
        first = [[(NAMES[0], BOX, 0.99)], [(NAMES[0], BOX, 0.99)],
                 [(NAMES[1], BOX, 0.89)],
                 [(NAMES[2], BOX, 0.99)], [(NAMES[2], BOX, 0.99)]]
        second = [[(name, BOX, 0.99)] for name in NAMES for _ in range(2)]
        _, batches, reason = self._retry(*initial, _OCR(title_outputs=first + second))
        self.assertIsNone(reason)
        self.assertEqual(len(batches), 2)
        self.assertTrue(self._coverage(initial, batches).finish())

    def test_batches_keep_independent_images_for_each_accepted_read(self):
        initial = self._read(self._outputs())
        accepted, batches, reason = self._retry(*initial)
        self.assertIsNone(reason)
        saved, rows, _issues = batches[0]
        self.assertFalse(np.shares_memory(saved, accepted))
        self.assertFalse(np.shares_memory(saved, self.device.image))
        self.device.image[:] = 77
        np.testing.assert_array_equal(saved, self.image)
        self.assertTrue(all(row.complete for row in rows))

    def test_second_retry_movement_preserves_first_batch_and_returns_protection_reason(self):
        initial = self._read(self._outputs())
        moved = _frame(offset=-5)
        self.device.frames = [self.image, self.image, moved]
        failed = [[(NAMES[0], BOX, 0.99)], [(NAMES[0], BOX, 0.99)],
                  [(NAMES[1], BOX, 0.89)],
                  [(NAMES[2], BOX, 0.99)], [(NAMES[2], BOX, 0.99)]]
        ocr = _OCR(title_outputs=failed)
        accepted, batches, reason = self._retry(*initial, ocr)
        self.assertEqual(len(batches), 1)
        self.assertIn('列表位置或静态标题发生变化', reason)
        self.assertEqual(ocr.title_calls, 5)
        np.testing.assert_array_equal(accepted, self.image)
        np.testing.assert_array_equal(batches[0][0], self.image)

    def test_initial_known_title_conflict_is_never_retried(self):
        outputs = [[(NAMES[0], BOX, 0.99)], [(NAMES[1], BOX, 0.99)]]
        initial = self._read(outputs)
        _, batches, reason = self._retry(*initial)
        self.assertEqual(batches, [])
        self.assertIsNone(reason)
        self.assertEqual(self.device.screenshots, 0)
        self.assertIn('同一天赋行多次识别不一致', initial[1])

    def test_duplicate_known_talent_line_is_never_retried(self):
        initial = self._read([[(NAMES[0], BOX, 0.99)]] * 6)
        _, batches, _ = self._retry(*initial)
        self.assertEqual(batches, [])
        self.assertEqual(self.device.screenshots, 0)
        self.assertIn('不同天赋行识别为重复天赋线', initial[1])

    def test_conflict_in_retry_is_preserved_even_when_another_row_recovers(self):
        initial = self._read(self._outputs())
        different = ['熟练炮手·主力', NAMES[1], NAMES[2]]
        # 真正存在的另一等级名称必须构成跨截图强矛盾。
        from module.meowfficer.score import TALENT_INDEX, normalize
        self.assertIn(normalize(different[0]), TALENT_INDEX)
        _, batches, reason = self._retry(*initial, _OCR(names=different))
        self.assertIsNone(reason)
        self.assertEqual(len(batches), 1)
        coverage = self._coverage(initial, batches)
        self.assertFalse(coverage.finish())
        self.assertIn('同一天赋行跨截图识别结果不一致', coverage.reasons)

    def test_strong_conflict_in_retry_stops_and_cannot_be_overwritten_by_later_evidence(self):
        initial = self._read(self._outputs())
        outputs = [[(NAMES[0], BOX, 0.99)], [(NAMES[1], BOX, 0.99)]]
        _, batches, reason = self._retry(*initial, _OCR(title_outputs=outputs))
        self.assertIsNone(reason)
        self.assertEqual(len(batches), 1)
        coverage = self._coverage(initial, batches)
        self.assertFalse(coverage.finish())
        self.assertIn('同一天赋行多次识别不一致', coverage.reasons)

    def test_bad_spacing_does_not_trigger_retry(self):
        initial = self._read(self._outputs())
        initial[1].append('天赋行框间距异常，不能排除漏行')
        _, batches, reason = self._retry(*initial)
        self.assertEqual(batches, [])
        self.assertIsNone(reason)
        self.assertEqual(self.device.screenshots, 0)

    def test_full_height_failure_without_full_geometry_does_not_trigger_retry(self):
        rows, _ = self._read()
        rows[0].bottom = rows[0].top + 70
        rows[0].complete = False
        _, batches, reason = self._retry(rows, ['天赋行被裁切或行框不完整'])
        self.assertEqual(batches, [])
        self.assertIsNone(reason)
        self.assertEqual(self.device.screenshots, 0)

    def test_missing_row_detection_does_not_trigger_retry(self):
        _, batches, reason = self._retry([], ['没有确认到天赋图标行'])
        self.assertEqual(batches, [])
        self.assertIsNone(reason)
        self.assertEqual(self.device.screenshots, 0)

    def test_missing_identity_reference_stops_before_screenshot(self):
        initial = self._read(self._outputs())
        _, batches, reason = retry_talent_rows(self.scanner, _OCR(), self.image, *initial, None)
        self.assertEqual(batches, [])
        self.assertIn('缺少当前猫身份依据', reason)
        self.assertEqual(self.device.screenshots, 0)

    def test_changed_identity_before_retry_stops_before_screenshot(self):
        initial = self._read(self._outputs())
        x0, y0, x1, y1 = IDENTITY_AREA
        self.device.image[y0:y1, x0:x1] = 100
        _, batches, reason = self._retry(*initial)
        self.assertEqual(batches, [])
        self.assertIn('当前猫身份或页面发生变化', reason)
        self.assertEqual(self.device.screenshots, 0)

    def test_changed_identity_in_new_frame_is_not_read_or_merged(self):
        initial = self._read(self._outputs())
        changed = self.image.copy()
        x0, y0, x1, y1 = IDENTITY_AREA
        changed[y0:y1, x0:x1] = 100
        self.device.frames = [changed]
        ocr = _OCR()
        accepted, batches, reason = self._retry(*initial, ocr)
        self.assertIs(accepted, self.image)
        self.assertEqual(batches, [])
        self.assertIn('当前猫身份或页面发生变化', reason)
        self.assertEqual(ocr.title_calls, 0)

    def test_changed_page_is_not_read_or_merged(self):
        initial = self._read(self._outputs())
        with patch('module.meowfficer.score_lock.detail_page_confirmed', side_effect=[True, False]):
            _, batches, reason = self._retry(*initial)
        self.assertEqual(batches, [])
        self.assertIn('当前猫身份或页面发生变化', reason)

    def test_moved_list_is_not_read_or_merged(self):
        initial = self._read(self._outputs())
        self.device.frames = [_frame(offset=-5)]
        ocr = _OCR()
        _, batches, reason = self._retry(*initial, ocr)
        self.assertEqual(batches, [])
        self.assertIn('列表位置或静态标题发生变化', reason)
        self.assertEqual(ocr.title_calls, 0)

    def test_small_title_change_hidden_by_panel_average_is_not_merged(self):
        initial = self._read(self._outputs())
        changed = self.image.copy()
        changed[171:176, 880:888] = 220
        self.assertLess(_mean_diff(_crop(self.image, PANEL_AREA), _crop(changed, PANEL_AREA)), 3)
        self.assertTrue(_same_panel(self.image, changed))
        self.device.frames = [changed]
        ocr = _OCR()
        _, batches, reason = self._retry(*initial, ocr)
        self.assertEqual(batches, [])
        self.assertIn('列表位置或静态标题发生变化', reason)
        self.assertEqual(ocr.title_calls, 0)

    def test_effect_body_animation_with_unchanged_titles_can_be_retried(self):
        initial = self._read(self._outputs())
        animated = self.image.copy()
        for top in (153, 255, 357):
            animated[top + 52:top + 60, 866:1116] = 60
        self.assertGreater(_mean_diff(_crop(self.image, PANEL_AREA), _crop(animated, PANEL_AREA)), 3)
        self.assertTrue(_same_panel(self.image, animated))
        self.device.frames = [animated]
        _, batches, reason = self._retry(*initial)
        self.assertIsNone(reason)
        self.assertEqual(len(batches), 1)
        np.testing.assert_array_equal(batches[0][0], animated)
        self.assertTrue(self._coverage(initial, batches).finish())

    def test_only_one_nonempty_title_cannot_override_position_protection(self):
        image = _frame(1)
        issues = []
        rows = _read_row_records(image, _OCR(title_outputs=[[(NAMES[0], BOX, 0.89)]]), issues)
        self.device.image = image.copy()
        ocr = _OCR()
        _, batches, reason = retry_talent_rows(
            self.scanner, ocr, image, rows, issues, self.identity)
        self.assertEqual(batches, [])
        self.assertIn('列表位置或静态标题发生变化', reason)
        self.assertEqual(ocr.title_calls, 0)

    def test_unstable_frame_is_not_read_or_merged(self):
        initial = self._read(self._outputs())
        ocr = _OCR()
        with patch('module.meowfficer.scan_capture._stable_frame', return_value=(self.image, False)):
            _, batches, reason = self._retry(*initial, ocr)
        self.assertEqual(batches, [])
        self.assertIn('面板未稳定', reason)
        self.assertEqual(ocr.title_calls, 0)

    def test_device_control_error_propagates(self):
        initial = self._read(self._outputs())
        error = GameTooManyClickError('匿名设备控制错误')
        with patch.object(self.device, 'screenshot', side_effect=error):
            with self.assertRaises(GameTooManyClickError) as raised:
                self._retry(*initial)
        self.assertIs(raised.exception, error)

    def test_ocr_control_error_propagates(self):
        initial = self._read(self._outputs())
        error = GameStuckError('匿名 OCR 控制错误')
        with self.assertRaises(GameStuckError) as raised:
            self._retry(*initial, _OCR(title_outputs=[error]))
        self.assertIs(raised.exception, error)


if __name__ == '__main__':
    unittest.main()
