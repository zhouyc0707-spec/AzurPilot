"""原图已确认的整标题由新字形证据补全，误字不做别名也不放宽评分保护。"""

import unittest
from unittest.mock import Mock, patch

import numpy as np

from module.exception import GameStuckError, GameTooManyClickError, RequestHumanTakeover
from module.meowfficer.scan_capture import _read_row_records, _rgb_variants, capture_current_cat
from module.meowfficer.scan_title import padded_title_variants
from module.meowfficer.scan_utils import _crop
from tests.test_meowfficer_empty_tail import _EmptyOCR, _empty_frame
from tests.test_meowfficer_scan_capture import BOX, NAMES, _Device, _OCR, _frame


TITLE = '一发入魂'
FULL = [(TITLE, BOX, 0.9992)]
UNKNOWN = [('一发入动魂', BOX, 0.92724)]
OTHER = [(NAMES[0], BOX, 0.99)]


class PaddedWholeTitlePixelTests(unittest.TestCase):
    """补白保留完整原始标题范围，原始字形触边时不得伪装为完整标题。"""

    @staticmethod
    def _crop():
        image = np.full((39, 265, 3), 230, dtype=np.uint8)
        image[10:24, 14:54] = 110
        image[10:24, 60:95] = 175
        return image

    def test_two_thresholds_keep_complete_pixels_and_add_independent_white_padding(self):
        original = self._crop()
        before = original.copy()
        variants = list(padded_title_variants(original))
        self.assertEqual([name for name, _image in variants],
                         ['whole_title_padded_glyph_160', 'whole_title_padded_glyph_185'])
        self.assertTrue(all(image.shape == (165, 843, 3) for _name, image in variants))
        self.assertFalse(np.array_equal(variants[0][1], variants[1][1]))
        for _name, image in variants:
            self.assertTrue(np.all(image[:24] == 255))
            self.assertTrue(np.all(image[-24:] == 255))
            self.assertTrue(np.all(image[:, :24] == 255))
            self.assertTrue(np.all(image[:, -24:] == 255))
        np.testing.assert_array_equal(original, before)

    def test_original_dark_pixels_touching_any_edge_cannot_be_hidden_with_padding(self):
        for edge in ('top', 'bottom', 'left', 'right'):
            with self.subTest(edge=edge):
                image = self._crop()
                if edge == 'top':
                    image[0, 14:54] = 110
                elif edge == 'bottom':
                    image[-1, 14:54] = 110
                elif edge == 'left':
                    image[10:24, 0] = 110
                else:
                    image[10:24, -1] = 110
                self.assertEqual(list(padded_title_variants(image)), [])

    def test_blank_original_crop_cannot_produce_title_evidence(self):
        self.assertEqual(list(padded_title_variants(np.full((39, 265, 3), 230, dtype=np.uint8))), [])

    def test_unknown_geometry_or_dtype_cannot_produce_padded_title_evidence(self):
        for image in (np.full((39, 264, 3), 100, dtype=np.uint8),
                      np.full((38, 265, 3), 100, dtype=np.uint8),
                      np.full((39, 265), 100, dtype=np.uint8),
                      self._crop().astype(np.uint16)):
            with self.subTest(shape=image.shape, dtype=image.dtype):
                self.assertEqual(list(padded_title_variants(image)), [])


class FullTitleSupplementReadingTests(unittest.TestCase):
    """只有原图精确已知且增强图有效未知时补读，新完整标题须双路精确一致。"""

    def setUp(self):
        template = patch('module.meowfficer.scan_capture.read_exact_talent_title', return_value=None)
        template.start()
        self.addCleanup(template.stop)

    @staticmethod
    def _read(outputs, image=None):
        ocr = _OCR(title_outputs=outputs)
        reasons = []
        rows = _read_row_records(_frame(1) if image is None else image, ocr, reasons)
        return rows[0], ocr, reasons

    def test_unknown_enhanced_typo_is_recovered_only_by_two_new_exact_whole_readings(self):
        row, ocr, reasons = self._read([FULL, UNKNOWN, FULL, FULL])
        self.assertTrue(row.complete, reasons)
        self.assertEqual(row.talent.name, TITLE)
        self.assertEqual(ocr.title_calls, 4)
        self.assertEqual(reasons, [])
        self.assertEqual([reading['variant'] for reading in row.readings],
                         ['plain', 'clahe', 'whole_title_padded_glyph_160',
                          'whole_title_padded_glyph_185'])
        self.assertEqual(row.readings[1]['results'],
                         [{'text': '一发入动魂', 'confidence': 0.92724}])

    def test_two_regular_exact_reads_add_no_supplemental_work(self):
        row, ocr, reasons = self._read([FULL, FULL])
        self.assertTrue(row.complete, reasons)
        self.assertEqual(ocr.title_calls, 2)
        self.assertEqual(len(row.readings), 2)

    def test_two_known_original_names_conflicting_cannot_trigger_supplement(self):
        row, ocr, reasons = self._read([FULL, OTHER, FULL, FULL])
        self.assertFalse(row.complete)
        self.assertIsNone(row.talent)
        self.assertEqual(ocr.title_calls, 2)
        self.assertIn('同一天赋行多次识别不一致', reasons)

    def test_unknown_plain_and_known_enhanced_are_not_the_same_retry_condition(self):
        row, ocr, _reasons = self._read([UNKNOWN, FULL, FULL, FULL])
        self.assertFalse(row.complete)
        self.assertIsNone(row.talent)
        self.assertEqual(ocr.title_calls, 1)

    def test_invalid_weak_missing_or_multibox_original_evidence_cannot_be_bypassed(self):
        invalid = (
            [],
            [(TITLE, BOX, 0.89)],
            [(TITLE, BOX, -0.1)],
            [(TITLE, BOX, 1.1)],
            [(TITLE, BOX, float('nan'))],
            [(TITLE, BOX, float('inf'))],
            [{'text': TITLE, 'box': BOX}],
            [(TITLE, BOX, 0.99), ('未知', BOX, 0.99)],
            RuntimeError('原图模型失败'),
        )
        for output in invalid:
            with self.subTest(output=output):
                row, ocr, _reasons = self._read([output, UNKNOWN, FULL, FULL])
                self.assertFalse(row.complete)
                self.assertIsNone(row.talent)
                self.assertEqual(ocr.title_calls, 1)

    def test_invalid_weak_missing_or_multibox_enhanced_evidence_cannot_be_bypassed(self):
        invalid = (
            [],
            [('一发入动魂', BOX, 0.89)],
            [('一发入动魂', BOX, -0.1)],
            [('一发入动魂', BOX, 1.1)],
            [('一发入动魂', BOX, float('nan'))],
            [('一发入动魂', BOX, float('inf'))],
            [{'text': '一发入动魂', 'box': BOX}],
            [('一发入动魂', BOX, 0.99), ('未知', BOX, 0.99)],
            RuntimeError('增强模型失败'),
        )
        for output in invalid:
            with self.subTest(output=output):
                row, ocr, _reasons = self._read([FULL, output, FULL, FULL])
                self.assertFalse(row.complete)
                self.assertIsNone(row.talent)
                self.assertEqual(ocr.title_calls, 2)

    def test_each_new_reading_still_requires_single_exact_known_high_confidence_name(self):
        invalid = (
            [],
            UNKNOWN,
            [(TITLE, BOX, 0.89)],
            [(TITLE, BOX, 1.1)],
            [(TITLE, BOX, float('nan'))],
            [{'text': TITLE, 'box': BOX}],
            [(TITLE, BOX, 0.99), ('未知', BOX, 0.99)],
            RuntimeError('补读模型失败'),
        )
        for position in (2, 3):
            for output in invalid:
                with self.subTest(position=position, output=output):
                    sequence = [FULL, UNKNOWN, FULL, FULL]
                    sequence[position] = output
                    row, ocr, _reasons = self._read(sequence)
                    self.assertFalse(row.complete)
                    self.assertIsNone(row.talent)
                    self.assertEqual(ocr.title_calls, position + 1)

    def test_new_known_names_must_agree_with_each_other_and_original_plain(self):
        for first, second in ((FULL, OTHER), (OTHER, FULL), (OTHER, OTHER)):
            with self.subTest(first=first, second=second):
                row, ocr, reasons = self._read([FULL, UNKNOWN, first, second])
                self.assertFalse(row.complete)
                self.assertIsNone(row.talent)
                self.assertEqual(ocr.title_calls, 4)
                self.assertIn('同一天赋行多次识别不一致', reasons)

    def test_cropped_original_word_does_not_gain_completeness_from_white_padding(self):
        image = _frame(1)
        image[156:166, 855] = (70, 70, 70)
        row, ocr, _reasons = self._read([FULL, UNKNOWN, FULL, FULL], image)
        self.assertFalse(row.complete)
        self.assertIsNone(row.talent)
        self.assertEqual(ocr.title_calls, 2)

    def test_flow_errors_in_new_reads_propagate_instead_of_protective_fallback(self):
        for position in (2, 3):
            for error_type in (GameStuckError, GameTooManyClickError, RequestHumanTakeover):
                with self.subTest(position=position, error_type=error_type):
                    error = error_type('补读期间流程异常')
                    outputs = [FULL, UNKNOWN, FULL, FULL]
                    outputs[position] = error
                    with self.assertRaises(error_type) as caught:
                        self._read(outputs)
                    self.assertIs(caught.exception, error)


class _ThirdTalentTypoOCR(_EmptyOCR):
    """仅第三行增强标题含实况误字，补读按新的完整字形输入给出独立结果。"""

    def __init__(self, image, supplemental_outputs=(FULL, FULL)):
        super().__init__()
        self.originals = list(_rgb_variants(_crop(image, (855, 360, 1120, 399))).values())
        self.supplemental_outputs = list(supplemental_outputs)
        self.supplemental_calls = 0

    def det(self, image):
        if image.shape == (165, 843, 3):
            index = self.supplemental_calls
            self.supplemental_calls += 1
            value = self.supplemental_outputs[min(index, len(self.supplemental_outputs) - 1)]
            if isinstance(value, Exception):
                raise value
            return value
        for index, original in enumerate(self.originals):
            if np.array_equal(image, original):
                return FULL if index == 0 else UNKNOWN
        return super().det(image)


class CompleteEmptyPrefixAfterTitleRecoveryTests(unittest.TestCase):
    """三个已学天赋补全后直接评分，不因第三行增强错字下移到完整未习得栏。"""

    def setUp(self):
        for handle in (patch('module.config.server.server', 'cn'),
                       patch('module.meowfficer.score_lock.detail_page_confirmed', return_value=True),
                       patch('module.meowfficer.scan_diagnostics.save_incomplete_capture')):
            handle.start()
            self.addCleanup(handle.stop)

    def test_third_title_recovery_and_complete_fourth_empty_end_with_one_top_no_bottom(self):
        image = _empty_frame()
        scanner = Mock(device=_Device(image))
        scanner._read_current_cat.return_value = ('约翰喵', 30)
        ocr = _ThirdTalentTypoOCR(image)
        with patch('module.meowfficer.scan_retry.retry_talent_rows') as retry:
            capture = capture_current_cat(scanner, ocr, '约翰喵', 30)
        self.assertTrue(capture.complete, capture.reasons)
        self.assertTrue(capture.talents_complete)
        self.assertEqual([talent.name for talent in capture.talents], [NAMES[0], NAMES[1], TITLE])
        self.assertEqual(len(scanner.device.swipes), 1)
        start, end, _duration = scanner.device.swipes[0]
        self.assertLess(start[1], end[1])
        self.assertEqual(ocr.supplemental_calls, 2)
        self.assertEqual(scanner._read_current_cat.call_count, 2)
        retry.assert_not_called()


if __name__ == '__main__':
    unittest.main()
