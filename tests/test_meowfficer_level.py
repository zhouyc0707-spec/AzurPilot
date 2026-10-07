"""当前猫等级的白字补证回归；不把空结果、字母或相邻数字猜成等级。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from module.exception import GameStuckError, GameTooManyClickError, RequestHumanTakeover
from module.meowfficer.scan_level import (LEVEL_GLYPH_AREA, _level_evidence,
                                         level_glyph_variants, read_cat_level)
from module.meowfficer.scan import MeowfficerScanner


BOX = [[0, 0], [35, 0], [35, 13], [0, 13]]


def _result(text='LV.1', confidence=0.99):
    return [(text, BOX, confidence)]


def _glyph_frame(*, height=13, width=42):
    """白色匿名笔画置于校准框内，保留完整边距，不含名字、舰队或账号数据。"""
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    left, top = 374, 604
    image[top:top + height, left:left + width] = 255
    # 两个阈值会产生不同的独立输入，不能拿同一预处理结果重复计作双路证据。
    image[top + 3:top + 6, left + 6:left + 9] = 238
    return image


class LevelEvidenceTests(unittest.TestCase):
    """单个带明确等级前缀的数字保留置信度，所有非法及多框证据拒绝。"""

    def test_exact_existing_prefixes_and_valid_range_are_confirmed(self):
        for prefix in ('LV', 'Lv.', 'LW', 'IV:', 'WV'):
            for value in (1, 9, 30, 60):
                with self.subTest(prefix=prefix, value=value):
                    self.assertEqual(_level_evidence(_result(f'{prefix}{value}')),
                                     (value, True, True))

    def test_low_confidence_keeps_original_number_for_conflict_detection(self):
        for confidence in (0, 0.5, 0.89999):
            with self.subTest(confidence=confidence):
                self.assertEqual(_level_evidence(_result('LV.1', confidence)), (1, False, True))
        self.assertEqual(_level_evidence(_result('LV.1', 0.9)), (1, True, True))

    def test_letters_empty_text_bare_numbers_and_adjacent_values_are_not_levels(self):
        for text in ('I', 'LV.I', 'LV.l', '', '1', '30', '指挥 30', '第一舰队1',
                     '1/70000', 'LV1/70000', 'value30', 'LV0', 'LV61', 'LV999'):
            with self.subTest(text=text):
                self.assertEqual(_level_evidence(_result(text)), (None, False, False))

    def test_nonfinite_invalid_missing_or_multibox_confidence_cannot_be_bypassed(self):
        invalid = (
            _result(confidence=-0.1), _result(confidence=1.1),
            _result(confidence=float('nan')), _result(confidence=float('inf')),
            _result(confidence=None), _result(confidence='invalid'),
            [{'text': 'LV.1', 'box': BOX}], [('LV.1', BOX)],
            [('LV.1', BOX, 0.99), ('LV.1', BOX, 0.99)],
            [('LV.1', BOX, 0.99), ('LV.7', BOX, 0.1)],
            RuntimeError('原模型异常'), 'LV.1',
        )
        for results in invalid:
            with self.subTest(results=results):
                self.assertEqual(_level_evidence(results), (None, False, False))


class LevelGlyphGeometryTests(unittest.TestCase):
    """固定等级框的中性白字必须完整，不扩大到舰队名、经验或静态属性。"""

    def setUp(self):
        handle = patch('module.config.server.server', 'cn')
        handle.start()
        self.addCleanup(handle.stop)

    def test_two_thresholds_add_white_padding_and_preserve_original(self):
        image = _glyph_frame()
        before = image.copy()
        variants = level_glyph_variants(image)
        self.assertEqual(LEVEL_GLYPH_AREA, (365, 600, 438, 623))
        self.assertEqual(len(variants), 2)
        self.assertTrue(all(variant.shape == (213, 363, 3) for variant in variants))
        self.assertFalse(np.array_equal(variants[0], variants[1]))
        for variant in variants:
            self.assertTrue(np.all(variant[:72] == 255))
            self.assertTrue(np.all(variant[-72:] == 255))
            self.assertTrue(np.all(variant[:, :72] == 255))
            self.assertTrue(np.all(variant[:, -72:] == 255))
            self.assertTrue(set(np.unique(variant)).issubset({0, 255}))
        np.testing.assert_array_equal(image, before)

    def test_white_pixels_touching_any_original_edge_cannot_be_hidden_with_padding(self):
        x0, y0, x1, y1 = LEVEL_GLYPH_AREA
        for edge in ('top', 'bottom', 'left', 'right'):
            with self.subTest(edge=edge):
                image = _glyph_frame()
                if edge == 'top':
                    image[y0, x0 + 9:x0 + 51] = 255
                elif edge == 'bottom':
                    image[y1 - 1, x0 + 9:x0 + 51] = 255
                elif edge == 'left':
                    image[y0 + 4:y0 + 17, x0] = 255
                else:
                    image[y0 + 4:y0 + 17, x1 - 1] = 255
                self.assertIsNone(level_glyph_variants(image))

    def test_missing_short_tall_narrow_dim_or_colored_glyphs_do_not_supply_evidence(self):
        invalid = [_glyph_frame(height=9), _glyph_frame(height=19), _glyph_frame(width=11),
                   np.zeros((720, 1280, 3), dtype=np.uint8)]
        dim = _glyph_frame()
        dim[dim == 255] = 238
        invalid.append(dim)
        colored = np.zeros((720, 1280, 3), dtype=np.uint8)
        colored[604:617, 374:416] = (255, 240, 200)
        invalid.append(colored)
        for image in invalid:
            with self.subTest(glyphs=int((image.min(axis=2) >= 245).sum())):
                self.assertIsNone(level_glyph_variants(image))

    def test_neighboring_names_experience_and_attributes_never_enter_the_glyph_input(self):
        base = _glyph_frame()
        image = base.copy()
        # 这些匿名亮纹理分别占舰队、邻近属性及等级条左侧经验区。
        image[566:599, 360:610] = 255
        image[600:623, 450:630] = 255
        image[600:623, 165:350] = 255
        for first, second in zip(level_glyph_variants(base), level_glyph_variants(image)):
            np.testing.assert_array_equal(first, second)
        image[600:623, 365:438] = 0
        self.assertIsNone(level_glyph_variants(image))

    def test_unknown_server_geometry_and_dtype_never_enable_level_glyph_fallback(self):
        for server in ('en', 'jp', 'tw', 'unknown'):
            with self.subTest(server=server), patch('module.config.server.server', server):
                self.assertIsNone(level_glyph_variants(_glyph_frame()))
        for image in (None, np.zeros((360, 640, 3), dtype=np.uint8),
                      np.zeros((720, 1280), dtype=np.uint8), _glyph_frame().astype(np.uint16)):
            with self.subTest(shape=getattr(image, 'shape', None)):
                self.assertIsNone(level_glyph_variants(image))


class ExactLevelSupplementTests(unittest.TestCase):
    """没有等级时须获取两个真实数字，原低分冲突与任何新失败仍阻止接受。"""

    def setUp(self):
        handle = patch('module.config.server.server', 'cn')
        handle.start()
        self.addCleanup(handle.stop)

    def test_regular_confirmed_level_adds_no_ocr_calls(self):
        ocr = Mock()
        self.assertEqual(read_cat_level(_glyph_frame(), ocr, _result('LV30')), 30)
        ocr.det.assert_not_called()

    def test_missing_or_weak_matching_original_is_recovered_by_two_high_exact_reads(self):
        for original in ([], None, _result('LV.1', 0.2)):
            with self.subTest(original=original):
                ocr = Mock()
                ocr.det.side_effect = [_result('IV.1', 0.90293), _result('IV1', 0.92843)]
                self.assertEqual(read_cat_level(_glyph_frame(), ocr, original), 1)
                self.assertEqual(ocr.det.call_count, 2)
                self.assertFalse(np.array_equal(ocr.det.call_args_list[0].args[0],
                                               ocr.det.call_args_list[1].args[0]))

    def test_weak_original_different_level_or_two_new_values_conflicting_are_rejected(self):
        for original, outputs in (([], [_result('LV.1'), _result('LV.7')]),
                                  (_result('LV.30', 0.2), [_result('LV.1'), _result('LV.1')]),
                                  (_result('LV.1', 0.2), [_result('LV.7'), _result('LV.7')])):
            with self.subTest(original=original, outputs=outputs):
                ocr = Mock()
                ocr.det.side_effect = outputs
                self.assertIsNone(read_cat_level(_glyph_frame(), ocr, original))
                self.assertEqual(ocr.det.call_count, 2)

    def test_invalid_original_evidence_never_triggers_supplement_or_guesses_one(self):
        for original in (_result('I'), _result('LV.I'), _result('1'), _result('LV0'),
                         _result('LV.1', float('nan')), [{'text': 'LV.1', 'box': BOX}],
                         [('LV.1', BOX, 0.99), ('LV.7', BOX, 0.1)], RuntimeError('原模型异常')):
            with self.subTest(original=original):
                ocr = Mock()
                self.assertIsNone(read_cat_level(_glyph_frame(), ocr, original))
                ocr.det.assert_not_called()

    def test_each_new_read_requires_exact_prefixed_numeric_single_high_confidence_evidence(self):
        invalid = ([], None, _result('I'), _result('IV.I'), _result('1'),
                   _result('LV.1', 0.899), _result('LV.1', float('nan')),
                   _result('LV.1', 1.1), [{'text': 'LV.1', 'box': BOX}],
                   [('LV.1', BOX, 0.99), ('LV.1', BOX, 0.99)])
        for position in (0, 1):
            for output in invalid:
                with self.subTest(position=position, output=output):
                    ocr = Mock()
                    outputs = [_result(), _result()]
                    outputs[position] = output
                    ocr.det.side_effect = outputs
                    self.assertIsNone(read_cat_level(_glyph_frame(), ocr, []))
                    self.assertEqual(ocr.det.call_count, position + 1)

    def test_invalid_pixels_or_unsupported_server_do_not_call_ocr_for_missing_original(self):
        for image in (None, np.zeros((720, 1280, 3), dtype=np.uint8),
                      np.zeros((360, 640, 3), dtype=np.uint8)):
            with self.subTest(shape=getattr(image, 'shape', None)):
                ocr = Mock()
                self.assertIsNone(read_cat_level(image, ocr, []))
                ocr.det.assert_not_called()
        with patch('module.config.server.server', 'en'):
            ocr = Mock()
            self.assertIsNone(read_cat_level(_glyph_frame(), ocr, []))
            ocr.det.assert_not_called()

    def test_supplement_model_errors_return_none_and_flow_errors_propagate(self):
        for position in (0, 1):
            for error_type in (RuntimeError, GameStuckError, GameTooManyClickError, RequestHumanTakeover):
                with self.subTest(position=position, error_type=error_type):
                    error = error_type('离线等级补读异常')
                    outputs = [_result(), _result()]
                    outputs[position] = error
                    ocr = Mock()
                    ocr.det.side_effect = outputs
                    if error_type is RuntimeError:
                        self.assertIsNone(read_cat_level(_glyph_frame(), ocr, []))
                    else:
                        with self.assertRaises(error_type) as caught:
                            read_cat_level(_glyph_frame(), ocr, [])
                        self.assertIs(caught.exception, error)
                    self.assertEqual(ocr.det.call_count, position + 1)


class CurrentIdentityLevelFallbackTests(unittest.TestCase):
    """保留既有普通等级解析，仅原来读不到时补证；异常不伪装成空检测。"""

    def setUp(self):
        handle = patch('module.config.server.server', 'cn')
        handle.start()
        self.addCleanup(handle.stop)

    @staticmethod
    def _scanner():
        scanner = object.__new__(MeowfficerScanner)
        scanner.device = SimpleNamespace(image=_glyph_frame())
        return scanner

    def test_missing_original_level_recovers_one_while_preserving_independent_cat_name(self):
        scanner = self._scanner()
        ocr = Mock()
        ocr.det.side_effect = [[('奥古喵', BOX, 0.99)], [], _result('IV.1'), _result('IV1')]
        self.assertEqual(scanner._read_current_cat(ocr), ('奥古喵', 1))
        self.assertEqual([call.args[0].shape for call in ocr.det.call_args_list],
                         [(99, 438, 3), (75, 825, 3), (213, 363, 3), (213, 363, 3)])

    def test_original_level_model_failure_does_not_trigger_supplement_on_valid_white_pixels(self):
        scanner = self._scanner()
        ocr = Mock()
        ocr.det.side_effect = [[('奥古喵', BOX, 0.99)], RuntimeError('原等级模型失败')]
        self.assertEqual(scanner._read_current_cat(ocr), ('奥古喵', None))
        self.assertEqual(ocr.det.call_count, 2)

    def test_existing_level_thirty_with_trailing_ocr_noise_keeps_original_parse_without_supplement(self):
        # 保存现场中的 LV30D 原来可解析为 30，本次只修补漏检，不能改变这条既有路径。
        scanner = self._scanner()
        ocr = Mock()
        ocr.det.side_effect = [[('奥古喵', BOX, 0.99)], _result('LV30D', 0.78478)]
        with patch('module.meowfficer.scan.read_cat_level') as supplement:
            self.assertEqual(scanner._read_current_cat(ocr), ('奥古喵', 30))
        self.assertEqual(ocr.det.call_count, 2)
        supplement.assert_not_called()

    def test_existing_low_confidence_lw_level_keeps_original_parse_without_supplement(self):
        scanner = self._scanner()
        ocr = Mock()
        ocr.det.side_effect = [[('奥古喵', BOX, 0.99)], _result('LW30', 0.2)]
        with patch('module.meowfficer.scan.read_cat_level') as supplement:
            self.assertEqual(scanner._read_current_cat(ocr), ('奥古喵', 30))
        self.assertEqual(ocr.det.call_count, 2)
        supplement.assert_not_called()

    def test_next_cat_level_one_is_not_filled_with_previous_confirmed_level_thirty(self):
        scanner = self._scanner()
        ocr = Mock()
        ocr.det.side_effect = [[('奥古喵', BOX, 0.99)], _result('LV30'),
                               [('奥古喵', BOX, 0.99)], [], _result('IV.1'), _result('IV1')]
        self.assertEqual(scanner._read_current_cat(ocr), ('奥古喵', 30))
        self.assertEqual(scanner._read_current_cat(ocr), ('奥古喵', 1))
        self.assertEqual(ocr.det.call_count, 6)

    def test_flow_errors_from_original_name_or_level_reads_propagate(self):
        for position in (0, 1):
            for error_type in (GameStuckError, GameTooManyClickError, RequestHumanTakeover):
                with self.subTest(position=position, error_type=error_type):
                    scanner = self._scanner()
                    error = error_type('原身份读取流程异常')
                    outputs = [[('奥古喵', BOX, 0.99)], _result('LV30')]
                    outputs[position] = error
                    ocr = Mock()
                    ocr.det.side_effect = outputs
                    with self.assertRaises(error_type) as caught:
                        scanner._read_current_cat(ocr)
                    self.assertIs(caught.exception, error)
                    self.assertEqual(ocr.det.call_count, position + 1)


if __name__ == '__main__':
    unittest.main()
