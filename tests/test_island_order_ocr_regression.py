"""验证订单整词错字兼容、精确匹配优先级和脱敏截图识别。"""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from module.base.utils import load_image
from module.island.data import DIC_ISLAND_ITEM
from module.island.order_ocr import OrderDigitCounter, match_item_name, validate_requirements


class OrderItemAliasTests(unittest.TestCase):
    def test_verified_full_word_aliases_resolve_to_correct_items(self):
        self.assertEqual(match_item_name('离肉', DIC_ISLAND_ITEM, 'cn'), 2602)
        self.assertEqual(match_item_name('白莱', DIC_ISLAND_ITEM, 'cn'), 2003)
        self.assertEqual(match_item_name(' 离肉 ', DIC_ISLAND_ITEM, 'cn'), 2602)

    def test_exact_names_and_filter_items_keep_original_identity(self):
        for name, item_id in (('禽肉', 2602), ('鲜肉', 2600), ('白菜', 2003),
                              ('芝士', 3006), ('豆腐', 3011)):
            with self.subTest(name=name):
                self.assertEqual(match_item_name(name, DIC_ISLAND_ITEM, 'cn'), item_id)
        custom_items = {1: {'name': {'cn': '离肉'}}, 2: {'name': {'cn': '禽肉'}}}
        self.assertEqual(match_item_name('离肉', custom_items, 'cn'), 1)

    def test_alias_requires_unique_existing_target(self):
        missing = {1: {'name': {'cn': '鲜肉'}}}
        duplicated = {1: {'name': {'cn': '禽肉'}}, 2: {'name': {'cn': '禽肉'}}}
        self.assertIsNone(match_item_name('离肉', missing, 'cn'))
        self.assertIsNone(match_item_name('离肉', duplicated, 'cn'))

    def test_alias_does_not_apply_to_other_servers(self):
        items = {1: {'name': {'cn': '鲜肉', 'tw': '鲜肉', 'en': '鲜肉', 'jp': '鲜肉'}},
                 2: {'name': {'cn': '禽肉', 'tw': '禽肉', 'en': '禽肉', 'jp': '禽肉'}}}
        for language in ('tw', 'en', 'jp'):
            with self.subTest(language=language):
                self.assertIsNone(match_item_name('离肉', items, language))
        # 日服既有唯一近似匹配仍然保留，本次不修改其判断规则。
        self.assertEqual(match_item_name('离肉', DIC_ISLAND_ITEM, 'jp'), 2602)

    def test_unverified_ambiguous_or_partial_names_remain_unknown(self):
        for name in ('禽', '肉', '芝', '豆', '白', '莫肉', '完全不认识的物品'):
            with self.subTest(name=name):
                self.assertIsNone(match_item_name(name, DIC_ISLAND_ITEM, 'cn'))

    def test_three_item_order_accepts_verified_names_with_complete_counters(self):
        names = ['离肉', '实用之木', '白莱']
        counters = [(50, 5, 45), (20, 3, 17), (40, 7, 33)]
        self.assertEqual(validate_requirements(names, counters, DIC_ISLAND_ITEM, 'cn'),
                         {2602: counters[0], 2801: counters[1], 2003: counters[2]})
        self.assertIsNone(validate_requirements(names, [counters[0], None, counters[2]],
                                               DIC_ISLAND_ITEM, 'cn'))

    def test_alias_does_not_relax_quantity_or_duplicate_validation(self):
        self.assertIsNone(validate_requirements(['离肉', '', ''], [(50, 0, 50), None, None],
                                               DIC_ISLAND_ITEM, 'cn'))
        self.assertIsNone(validate_requirements(['离肉', '禽肉', ''],
                                               [(50, 5, 45), (50, 5, 45), None],
                                               DIC_ISLAND_ITEM, 'cn'))
        self.assertEqual(validate_requirements(['离肉', '', ''], [(2, 5, -3), None, None],
                                              DIC_ISLAND_ITEM, 'cn'), {2602: (2, 5, -3)})


class OrderNameScreenshotTests(unittest.TestCase):
    def test_sanitized_name_crops_with_local_ocr(self):
        from module.ocr.al_ocr import AlOcr, OcrSettings
        from module.ocr.ocr import Ocr

        fixture_dir = Path(__file__).parent / 'fixtures' / 'island_order_ocr'
        images = [load_image(str(fixture_dir / name)) for name in
                  ('poultry_name.png', 'practical_wood_name.png', 'cabbage_name.png')]
        settings = OcrSettings(backend='onnx', device='cpu',
                               allow_vendor_execution_providers=False, model_version='alocr_cn_v3')
        model = AlOcr(name='cn', settings=settings)
        ocr = Ocr([(0, 0, 160, 28)] * 3, lang='cnocr', letter=(57, 59, 61), threshold=160,
                  name='ORDER_NAME_REGRESSION')
        # 固定本地模型与设置，避免读取用户配置或连接远端 OCR 服务。
        with patch('module.ocr.ocr.OCR_MODEL', SimpleNamespace(cnocr=model)):
            names = ocr.ocr(images, direct_ocr=True)
        counter = OrderDigitCounter([])
        counters = [counter.after_process(value) for value in ('50/5', '20/3', '40/7')]
        self.assertEqual(validate_requirements(names, counters, DIC_ISLAND_ITEM, 'cn'),
                         {2602: (50, 5, 45), 2801: (20, 3, 17), 2003: (40, 7, 33)})


if __name__ == '__main__':
    unittest.main()
