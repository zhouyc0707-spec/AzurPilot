"""分框标题由完整新像素证据补读，低分碎片和矛盾不能直接变成天赋。"""

import unittest
from unittest.mock import patch

import numpy as np

from module.exception import GameStuckError
from module.meowfficer.scan_capture import _read_row_records
from module.meowfficer.scan_title import split_title_may_retry, split_title_variants
from tests.test_meowfficer_scan_capture import BOX, NAMES, _frame, _OCR


TITLE = '新手观测士·主力'
LEFT = [[11, 6], [351, 6], [351, 104], [11, 105]]
RIGHT = [[305, 12], [465, 12], [465, 98], [305, 98]]
SPLIT = [('新手观测士·', LEFT, 0.89839), ('主力', RIGHT, 0.9997)]
FULL = [(TITLE, BOX, 0.98)]


class SplitTitleGeometryTests(unittest.TestCase):
    """分框只按有效同一水平行触发，两个完整天赋或无效结果不接受补读。"""

    def test_real_split_geometry_may_reread_the_whole_title(self):
        self.assertTrue(split_title_may_retry(
            [(item[0], item[2]) for item in SPLIT], [LEFT, RIGHT], (117, 795, 3)))

    def test_invalid_confidence_known_name_or_nonhorizontal_boxes_cannot_retry(self):
        cases = [
            ([(TITLE, 0.98), ('主力', 0.99)], [LEFT, RIGHT]),
            ([('新手观测士·', float('nan')), ('主力', 0.99)], [LEFT, RIGHT]),
            ([('新手观测士·', 1.1), ('主力', 0.99)], [LEFT, RIGHT]),
            ([('新手观测士·', 0.89), ('主力', 0.99)], [None, RIGHT]),
            ([('新手观测士·', 0.89), ('主力', 0.99)], [RIGHT, LEFT]),
            ([('新手观测士·', 0.89), ('主力', 0.99)], [LEFT, [[400, 70], [450, 70],
                                                                          [450, 110], [400, 110]]]),
            ([('新手观测士·', 0.89), ('主力', 0.99)], [LEFT, [[650, 12], [750, 12],
                                                                          [750, 98], [650, 98]]]),
            ([('新手观测士·', 0.89), ('主力', 0.99)], [LEFT, [[305, 12], [465, 12],
                                                                          [465, 198], [305, 198]]]),
            ([('新手观测士·', 0.89), ('主力', 0.99)], [LEFT, np.full((4, 2), np.nan)]),
        ]
        for details, boxes in cases:
            with self.subTest(details=details, boxes=boxes):
                self.assertFalse(split_title_may_retry(details, boxes, (117, 795, 3)))

    def test_glyph_variants_keep_the_entire_roi_and_different_original_pixels(self):
        crop = np.full((39, 265, 3), 230, dtype=np.uint8)
        crop[10:20, 10:40] = 100
        crop[10:20, 40:70] = 175
        variants = list(split_title_variants(crop))
        self.assertEqual([name for name, _ in variants],
                         ['whole_title_glyph_160', 'whole_title_glyph_185'])
        self.assertTrue(all(image.shape == (117, 795, 3) for _, image in variants))
        self.assertFalse(np.array_equal(variants[0][1], variants[1][1]))
        self.assertTrue(np.all(crop[10:20, 40:70] == 175))


class SplitTitleReadingTests(unittest.TestCase):
    """新完整标题双路精确一致才补全，保留失败原文并继续遵守已知冲突门槛。"""

    def setUp(self):
        template = patch('module.meowfficer.scan_capture.read_exact_talent_title', return_value=None)
        template.start()
        self.addCleanup(template.stop)

    def test_fragmented_plain_and_full_clahe_use_two_new_glyph_readings(self):
        ocr = _OCR(title_outputs=[SPLIT, FULL, FULL, FULL])
        reasons = []
        row = _read_row_records(_frame(1), ocr, reasons)[0]
        self.assertTrue(row.complete)
        self.assertEqual(row.talent.name, TITLE)
        self.assertEqual(reasons, [])
        self.assertEqual(ocr.title_calls, 4)
        self.assertEqual(row.readings[0]['results'][0],
                         {'text': '新手观测士·', 'confidence': 0.89839})
        self.assertEqual([reading['variant'] for reading in row.readings],
                         ['plain', 'clahe', 'whole_title_glyph_160', 'whole_title_glyph_185'])

    def test_both_original_variants_may_be_fragments_but_not_acceptance_evidence(self):
        ocr = _OCR(title_outputs=[SPLIT, SPLIT, FULL, FULL])
        row = _read_row_records(_frame(1), ocr, [])[0]
        self.assertTrue(row.complete)
        self.assertEqual(row.talent.name, TITLE)
        self.assertEqual(ocr.title_calls, 4)

    def test_fragments_cannot_be_concatenated_when_glyph_reads_still_fail(self):
        for output in ([], SPLIT, [(TITLE, BOX, 0.89)], [(TITLE, BOX, float('nan'))],
                       [(TITLE, BOX, 1.1)],
                       [('未收录标题', BOX, 0.99)]):
            with self.subTest(output=output):
                row = _read_row_records(_frame(1), _OCR(
                    title_outputs=[SPLIT, FULL, output, FULL]), [])[0]
                self.assertFalse(row.complete)
                self.assertIsNone(row.talent)

    def test_new_glyph_evidence_must_agree_with_original_full_known_name(self):
        other = [(NAMES[0], BOX, 0.99)]
        for outputs in ([SPLIT, other, FULL, FULL], [FULL, SPLIT, other, other],
                        [SPLIT, FULL, FULL, other]):
            with self.subTest(outputs=outputs):
                reasons = []
                row = _read_row_records(_frame(1), _OCR(title_outputs=outputs), reasons)[0]
                self.assertFalse(row.complete)
                self.assertIsNone(row.talent)
                self.assertIn('同一天赋行多次识别不一致', reasons)

    def test_invalid_original_evidence_does_not_trigger_glyph_fallback(self):
        cases = [
            [(TITLE, LEFT, 0.99), ('主力', RIGHT, 0.99)],
            [('新手观测士·', LEFT, float('nan')), ('主力', RIGHT, 0.99)],
            [(TITLE, BOX, 1.1)],
            [('新手观测士·', BOX, 0.89), ('主力', BOX, 0.99)],
            [{'text': '新手观测士·', 'box': LEFT}, {'text': '主力', 'box': RIGHT}],
            RuntimeError('模型故障'),
        ]
        for first in cases:
            with self.subTest(first=first):
                ocr = _OCR(title_outputs=[first, FULL, FULL, FULL])
                row = _read_row_records(_frame(1), ocr, [])[0]
                self.assertFalse(row.complete)
                self.assertIsNone(row.talent)
                self.assertEqual(ocr.title_calls, 1)

    def test_model_errors_after_split_cannot_be_replaced_by_new_evidence(self):
        ocr = _OCR(title_outputs=[SPLIT, RuntimeError('模型故障'), FULL, FULL])
        reasons = []
        row = _read_row_records(_frame(1), ocr, reasons)[0]
        self.assertFalse(row.complete)
        self.assertEqual(ocr.title_calls, 2)
        self.assertIn('天赋标题 OCR 失败', reasons)
        with self.assertRaises(GameStuckError):
            _read_row_records(_frame(1), _OCR(
                title_outputs=[SPLIT, FULL, GameStuckError('流程停止')]), [])

    def test_complete_regular_titles_do_not_add_ocr_work(self):
        ocr = _OCR(title_outputs=[FULL, FULL])
        self.assertTrue(_read_row_records(_frame(1), ocr, [])[0].complete)
        self.assertEqual(ocr.title_calls, 2)


if __name__ == '__main__':
    unittest.main()
