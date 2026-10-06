"""当前猫身份的空间隔离回归，不加载 OCR 模型或使用真实账号截图。"""

import unittest
from types import SimpleNamespace

import numpy as np

from module.meowfficer.scan import MeowfficerScanner
from module.meowfficer.scan_utils import (CURRENT_CAT_AREA, CURRENT_CAT_LEVEL_AREA,
                                         CURRENT_CAT_NAME_AREA, _crop, pick_cat_name)
from module.meowfficer.score_ocr import _iter_det_results


NAME_COLOR = (37, 83, 129)
LEVEL_COLOR = (53, 107, 163)
FLEET_COLOR = (71, 139, 211)
NUMBER_COLOR = (89, 151, 229)
BOX = [[0, 0], [30, 0], [30, 12], [0, 12]]


def _paint(image, area, color):
    x0, y0, x1, y1 = area
    image[y0:y1, x0:x1] = color


def _identity_frame(name=True, level=True, fleet=True, numbers=True):
    """只用不同颜色代表姓名框、等级条和相邻界面文字。"""
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    if name:
        _paint(image, (202, 566, 348, 599), NAME_COLOR)
    if level:
        _paint(image, (165, 600, 440, 625), LEVEL_COLOR)
    if fleet:
        _paint(image, (360, 566, 610, 599), FLEET_COLOR)
    if numbers:
        # 与等级条在同一行，但位于等级 ROI 之外；旧宽区域会读到它们。
        _paint(image, (450, 600, 630, 618), NUMBER_COLOR)
    return image


class _SpatialOCR:
    """从实际裁剪后的颜色判断有哪些文字，避免按调用顺序伪造正确结果。"""

    def __init__(self, name='不动如山', level='LV30', fleet='第一潜艇舰队',
                 numbers=('指挥 30', '22417/70000', '5'), name_error=None, level_error=None):
        self.name = name
        self.level = level
        self.fleet = fleet
        self.numbers = numbers
        self.name_error = name_error
        self.level_error = level_error
        self.regions = []

    @staticmethod
    def _contains(image, color):
        return bool(np.any(np.all(image == color, axis=-1)))

    def det(self, image):
        markers = tuple(label for label, color in (
            ('fleet', FLEET_COLOR), ('name', NAME_COLOR),
            ('level', LEVEL_COLOR), ('numbers', NUMBER_COLOR))
                        if self._contains(image, color))
        self.regions.append((markers, image.shape))
        texts = []
        for marker in markers:
            if marker == 'fleet':
                texts.append(self.fleet)
            elif marker == 'name':
                if self.name_error:
                    raise self.name_error
                texts.append(self.name)
            elif marker == 'level':
                if self.level_error:
                    raise self.level_error
                texts.append(self.level)
            else:
                texts.extend(self.numbers)
        return [(text, BOX, 0.99) for text in texts if text]


def _scanner(image):
    # 使用真实扫描器的方法，只绕开配置和设备初始化。
    scanner = object.__new__(MeowfficerScanner)
    scanner.device = SimpleNamespace(image=image)
    return scanner


class CurrentCatIdentityTests(unittest.TestCase):

    def test_wide_crop_reproduces_fleet_name_stealing_the_identity(self):
        image = _identity_frame()
        scanner = _scanner(image)
        ocr = _SpatialOCR()
        wide = scanner._crop_scale(_crop(image, CURRENT_CAT_AREA))
        old_texts = [text for text, _score in _iter_det_results(ocr.det(wide))]

        self.assertEqual(old_texts[:2], ['第一潜艇舰队', '不动如山'])
        self.assertEqual(pick_cat_name(old_texts), '第一潜艇舰队')
        self.assertEqual(scanner._read_current_cat(ocr), ('不动如山', 30))

    def test_equal_length_fleet_text_changes_do_not_change_the_cat(self):
        scanner = _scanner(_identity_frame())
        before = _SpatialOCR(name='潜艇航速参谋', fleet='第一潜艇舰队')
        after = _SpatialOCR(name='潜艇航速参谋', fleet='潜艇航速参谋')

        # 旧宽区域在两个界面上会得到同长但不同的第一候选。
        for ocr, expected_old in ((before, '第一潜艇舰队'), (after, '潜艇航速参谋')):
            wide = scanner._crop_scale(_crop(scanner.device.image, CURRENT_CAT_AREA))
            texts = [text for text, _score in _iter_det_results(ocr.det(wide))]
            self.assertEqual(pick_cat_name(texts), expected_old)
            self.assertEqual(scanner._read_current_cat(ocr), ('潜艇航速参谋', 30))

    def test_six_and_eight_character_fleet_names_do_not_affect_identity(self):
        for fleet in ('第一潜艇舰队', '第一主力作战舰队', '第二主力作战舰队', ''):
            with self.subTest(fleet=fleet):
                ocr = _SpatialOCR(name='埃弗喵', fleet=fleet)
                self.assertEqual(_scanner(_identity_frame())._read_current_cat(ocr), ('埃弗喵', 30))

    def test_custom_names_including_talent_names_are_preserved(self):
        for name in ('不动如山', '潜艇参谋', '潜艇航速参谋', '任意猫名'):
            with self.subTest(name=name):
                ocr = _SpatialOCR(name=name)
                self.assertEqual(_scanner(_identity_frame())._read_current_cat(ocr), (name, 30))

    def test_real_cat_name_containing_fleet_is_not_filtered_by_text(self):
        ocr = _SpatialOCR(name='我的舰队', fleet='第一潜艇舰队')
        self.assertEqual(_scanner(_identity_frame())._read_current_cat(ocr), ('我的舰队', 30))

    def test_ocr_receives_only_the_two_separate_fields(self):
        ocr = _SpatialOCR()
        self.assertEqual(_scanner(_identity_frame())._read_current_cat(ocr), ('不动如山', 30))
        self.assertEqual(ocr.regions, [
            (('name',), (99, 438, 3)),
            (('level',), (75, 825, 3)),
        ])

    def test_missing_name_does_not_fall_back_to_fleet_name(self):
        ocr = _SpatialOCR(name='')
        self.assertEqual(_scanner(_identity_frame(name=False))._read_current_cat(ocr), ('', 30))

    def test_name_ocr_failure_does_not_prevent_level_read(self):
        ocr = _SpatialOCR(name_error=RuntimeError('匿名姓名识别失败'))
        self.assertEqual(_scanner(_identity_frame())._read_current_cat(ocr), ('', 30))

    def test_missing_level_keeps_name_without_reading_adjacent_numbers(self):
        ocr = _SpatialOCR()
        self.assertEqual(_scanner(_identity_frame(level=False))._read_current_cat(ocr), ('不动如山', None))

    def test_empty_level_text_keeps_name(self):
        ocr = _SpatialOCR(level='')
        self.assertEqual(_scanner(_identity_frame())._read_current_cat(ocr), ('不动如山', None))

    def test_invalid_level_keeps_name(self):
        for level in ('LV0', 'LV999', '暂无等级', '22417/70000'):
            with self.subTest(level=level):
                ocr = _SpatialOCR(level=level)
                self.assertEqual(_scanner(_identity_frame())._read_current_cat(ocr), ('不动如山', None))

    def test_level_ocr_failure_keeps_the_cat_name(self):
        ocr = _SpatialOCR(level_error=RuntimeError('匿名等级识别失败'))
        self.assertEqual(_scanner(_identity_frame())._read_current_cat(ocr), ('不动如山', None))

    def test_common_lv_and_lw_prefixes_are_supported(self):
        for level in ('LV30', 'LW30', 'Lv.30', 'IV:30', 'WV30'):
            with self.subTest(level=level):
                ocr = _SpatialOCR(level=level)
                self.assertEqual(_scanner(_identity_frame())._read_current_cat(ocr), ('不动如山', 30))

    def test_wide_region_is_retained_for_pixel_stability(self):
        self.assertEqual(CURRENT_CAT_AREA, (100, 565, 640, 618))
        self.assertEqual(CURRENT_CAT_NAME_AREA, (202, 566, 348, 599))
        self.assertEqual(CURRENT_CAT_LEVEL_AREA, (165, 600, 440, 625))
        image = _identity_frame()
        wide = _crop(image, CURRENT_CAT_AREA)
        self.assertEqual(wide.shape, (53, 540, 3))
        self.assertTrue(_SpatialOCR._contains(wide, NAME_COLOR))
        self.assertTrue(_SpatialOCR._contains(wide, FLEET_COLOR))
        self.assertTrue(_SpatialOCR._contains(wide, LEVEL_COLOR))


if __name__ == '__main__':
    unittest.main()
