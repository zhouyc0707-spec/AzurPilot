"""严格天赋标题模板回归，仅使用公开字形裁剪和匿名标题背景。"""

from pathlib import Path
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image

from module.exception import GameStuckError, GameTooManyClickError, RequestHumanTakeover
from module.meowfficer import assets
from module.meowfficer.scan_capture import ScanCapture, _read_rows
from module.meowfficer.scan_talent_template import read_exact_talent_title
from module.meowfficer.score import evaluate
from module.meowfficer.score_lock import lock_target


ROOT = Path(__file__).resolve().parents[1]
RESOURCE_NAME = 'TEMPLATE_MEOWFFICER_TALENT_HURRICANE_EYE'
BOX = [[0, 0], [30, 0], [30, 12], [0, 12]]


class ExactTalentTitleTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.path = ROOT / 'assets/cn/meowfficer' / f'{RESOURCE_NAME}.png'
        with Image.open(cls.path) as source:
            cls.title = np.array(source.convert('RGB'))

    def setUp(self):
        guard = patch('module.config.server.server', 'cn')
        guard.start()
        self.addCleanup(guard.stop)

    def _frame(self, x=11, y=9):
        """将完整四字放到实拍标题位置，不提交完整账号截图。"""
        image = np.full((39, 265, 3), (242, 231, 217), dtype=np.uint8)
        height, width = self.title.shape[:2]
        image[y:y + height, x:x + width] = self.title
        return image

    def test_complete_calibrated_title_is_recognized(self):
        self.assertEqual(read_exact_talent_title(self._frame()), '飓风之眼')

    def test_small_header_position_variations_are_recognized(self):
        for x, y in ((8, 5), (14, 13), (11, 9)):
            with self.subTest(x=x, y=y):
                self.assertEqual(read_exact_talent_title(self._frame(x=x, y=y)), '飓风之眼')

    def test_missing_first_character_is_not_inferred_from_remaining_three(self):
        image = self._frame()
        image[9:31, 11:32] = (242, 231, 217)
        self.assertLess(cv2.minMaxLoc(cv2.matchTemplate(
            image, self.title, cv2.TM_CCOEFF_NORMED))[1], 0.97)
        self.assertIsNone(read_exact_talent_title(image))

    def test_title_suffix_is_rejected_even_with_complete_known_prefix(self):
        image = self._frame()
        # 附加一块真实标题字形，代表未知的更长标题，不能仅认出已知子串。
        image[9:31, 95:116] = self.title[:, :21]
        self.assertIsNone(read_exact_talent_title(image))

    def test_another_title_glyph_order_is_rejected(self):
        image = self._frame()
        reordered = self.title.copy()
        reordered[:, 3:20] = self.title[:, 56:73]
        reordered[:, 56:73] = self.title[:, 3:20]
        image[9:31, 11:90] = reordered
        self.assertIsNone(read_exact_talent_title(image))

    def test_dark_overlay_outside_the_title_is_rejected(self):
        for area in ((0, 0, 5, 5), (180, 7, 195, 15), (5, 33, 20, 38)):
            with self.subTest(area=area):
                image = self._frame()
                x0, y0, x1, y1 = area
                image[y0:y1, x0:x1] = (180, 180, 180)
                self.assertIsNone(read_exact_talent_title(image))

    def test_opaque_overlay_on_the_first_character_is_rejected(self):
        image = self._frame()
        image[9:31, 11:32] = (255, 255, 255)
        self.assertIsNone(read_exact_talent_title(image))

    def test_two_complete_matches_are_ambiguous(self):
        image = self._frame()
        image[9:31, 150:229] = self.title
        self.assertIsNone(read_exact_talent_title(image))

    def test_match_far_from_title_left_edge_is_rejected(self):
        for x, y in ((30, 9), (150, 9), (11, 0), (11, 17)):
            with self.subTest(x=x, y=y):
                self.assertIsNone(read_exact_talent_title(self._frame(x=x, y=y)))

    def test_unknown_and_blank_backgrounds_are_rejected(self):
        for color in ((0, 0, 0), (150, 150, 150), (242, 231, 217), (255, 255, 255)):
            with self.subTest(color=color):
                self.assertIsNone(read_exact_talent_title(
                    np.full((39, 265, 3), color, dtype=np.uint8)))
        rng = np.random.default_rng(7119)
        self.assertIsNone(read_exact_talent_title(
            rng.integers(0, 256, size=(39, 265, 3), dtype=np.uint8)))

    def test_partial_or_invalid_title_inputs_are_rejected(self):
        for image in (None, '飓风之眼', self.title, self._frame()[:30],
                      self._frame().astype(np.float32), np.zeros((39, 265), dtype=np.uint8)):
            with self.subTest(shape=getattr(image, 'shape', None)):
                self.assertIsNone(read_exact_talent_title(image))

    def test_non_chinese_servers_do_not_use_fallback_cn_template(self):
        for name in ('en', 'jp', 'tw', ''):
            with self.subTest(server=name), patch('module.config.server.server', name):
                self.assertIsNone(read_exact_talent_title(self._frame()))

    def test_resource_has_only_the_small_title_crop_and_generator_entry(self):
        resource = getattr(assets, RESOURCE_NAME)
        self.assertEqual(resource.raw_file['cn'], f'./assets/cn/meowfficer/{RESOURCE_NAME}.png')
        for name in ('en', 'jp', 'tw'):
            self.assertFalse((ROOT / 'assets' / name / 'meowfficer' / f'{RESOURCE_NAME}.png').exists())
            self.assertEqual(resource.raw_file[name], resource.raw_file['cn'])
        with Image.open(self.path) as image:
            self.assertEqual(image.size, (79, 22))
            self.assertEqual(image.mode, 'RGB')
            self.assertEqual(image.info, {})


class _TitleOCR:
    """经真实标题预处理与 det 接口输入，只隔离离线模型输出。"""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0

    def det(self, image):
        if image.shape != (117, 795, 3):
            raise AssertionError(f'收到非标题 OCR 裁剪：{image.shape}')
        output = self.outputs[min(self.calls, len(self.outputs) - 1)]
        self.calls += 1
        if isinstance(output, Exception):
            raise output
        return output


class TalentTemplateCaptureIntegrationTests(unittest.TestCase):
    """实际行检测、模板补充、评分证据与保护规则共同执行，不替换 helper。"""

    @classmethod
    def setUpClass(cls):
        path = ROOT / 'assets/cn/meowfficer' / f'{RESOURCE_NAME}.png'
        with Image.open(path) as source:
            cls.title = np.array(source.convert('RGB'))

    def setUp(self):
        guard = patch('module.config.server.server', 'cn')
        guard.start()
        self.addCleanup(guard.stop)

    def _frame(self, hide_first_character=False):
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        image[152:588, 744:1244] = (231, 223, 222)
        image[153:240, 756:762] = (206, 231, 239)
        image[153:240, 835:842] = (206, 231, 239)
        image[156:195, 855:1120] = (242, 231, 217)
        image[165:187, 866:945] = self.title
        if hide_first_character:
            image[165:187, 866:887] = (242, 231, 217)
        return image

    def _read(self, outputs, hide_first_character=False):
        reasons = []
        ocr = _TitleOCR(outputs)
        talents, complete = _read_rows(self._frame(hide_first_character), ocr, reasons)
        return talents, complete, reasons, ocr

    def _assert_exact_template_evidence(self, talents, complete, reasons):
        self.assertTrue(complete)
        self.assertEqual(reasons, [])
        self.assertEqual(len(talents), 1)
        self.assertEqual(talents[0].name, '飓风之眼')
        self.assertEqual(talents[0].level, 3)
        self.assertEqual(talents[0].raw, '飓风之眼（图像模板）')
        self.assertFalse(talents[0].inferred)

    def test_two_ocr_variants_missing_first_character_use_full_image_evidence(self):
        outputs = [[('风之眼', BOX, 0.99)], [('风之眼', BOX, 0.95)]]
        talents, complete, reasons, ocr = self._read(outputs)
        self._assert_exact_template_evidence(talents, complete, reasons)
        self.assertEqual(ocr.calls, 2)

    def test_empty_and_misread_variants_use_the_exact_template(self):
        cases = [
            [[], [('风之眼', BOX, 0.99)]],
            [[('风之眼', BOX, 0.99)], []],
            [[], []],
        ]
        for outputs in cases:
            with self.subTest(outputs=outputs):
                talents, complete, reasons, _ocr = self._read(outputs)
                self._assert_exact_template_evidence(talents, complete, reasons)

    def test_template_evidence_survives_evaluation_and_is_not_protectively_inferred(self):
        talents, complete, reasons, _ocr = self._read([[('风之眼', BOX, 0.99)]] * 2)
        result = evaluate(talents, cat='蒂奇喵', level=30)
        capture = ScanCapture(display_name='限定蒂奇喵', talents=talents, level=30,
                              breed='蒂奇喵', rarity='SSR', complete=complete,
                              identity_confirmed=True, reasons=reasons)

        self.assertEqual(result.talents[0].raw, '飓风之眼（图像模板）')
        self.assertFalse(result.talents[0].inferred)
        target, reason = lock_target(capture, result)
        self.assertIsInstance(target, bool)
        self.assertFalse(reason.startswith('保护锁定：'), reason)

    def test_known_conflicting_ocr_title_is_not_overridden_by_the_template(self):
        cases = [
            [[('不动如山', BOX, 0.99)]] * 2,
            [[('飓风之眼', BOX, 0.99)], [('不动如山', BOX, 0.99)]],
            [[('风之眼', BOX, 0.99)], [('不动如山', BOX, 0.90)]],
        ]
        for outputs in cases:
            with self.subTest(outputs=outputs):
                talents, complete, reasons, _ocr = self._read(outputs)
                self.assertEqual(talents, [])
                self.assertFalse(complete)
                self.assertIn('同一天赋行多次识别不一致', reasons)

    def test_non_finite_confidence_is_not_replaced_by_template_success(self):
        for confidence in (float('nan'), float('inf'), -float('inf')):
            with self.subTest(confidence=confidence):
                talents, complete, reasons, _ocr = self._read([
                    [('风之眼', BOX, confidence)]])
                self.assertEqual(talents, [])
                self.assertFalse(complete)
                self.assertIn('天赋标题不是高置信度的精确已知名称', reasons)

    def test_multiple_ocr_titles_remain_ambiguous_with_exact_image_template(self):
        talents, complete, reasons, _ocr = self._read([[
            ('风之眼', BOX, 0.99), ('飓风之眼', BOX, 0.99)]])
        self.assertEqual(talents, [])
        self.assertFalse(complete)
        self.assertIn('天赋图标行与识别标题数量不一致', reasons)

    def test_ocr_execution_failure_is_not_concealed_by_the_template(self):
        talents, complete, reasons, _ocr = self._read([RuntimeError('匿名 OCR 故障')])
        self.assertEqual(talents, [])
        self.assertFalse(complete)
        self.assertIn('天赋标题 OCR 失败', reasons)

    def test_control_errors_propagate_even_when_the_full_image_template_matches(self):
        for error_type in (GameStuckError, GameTooManyClickError, RequestHumanTakeover):
            with self.subTest(error=error_type):
                error = error_type('流程控制中断')
                with self.assertRaises(error_type) as raised:
                    self._read([error])
                self.assertIs(raised.exception, error)

    def test_hidden_first_character_and_high_confidence_partial_ocr_are_rejected(self):
        talents, complete, reasons, _ocr = self._read(
            [[('风之眼', BOX, 0.99)]] * 2, hide_first_character=True)
        self.assertEqual(talents, [])
        self.assertFalse(complete)
        self.assertIn('天赋标题不是高置信度的精确已知名称', reasons)


if __name__ == '__main__':
    unittest.main()
