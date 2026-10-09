"""工坊完整配方、同帧库存证据和选品数量闭环的离线回归。"""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

import module.config.server as server
from module.base.button import Button
from module.base.utils import color_mask, crop, load_image
from module.island.data import DIC_ISLAND_ITEM, DIC_ISLAND_SLOT
from module.island.manufacture_catalog import get_catalog
from module.island.recipe_groups import GROUP_TO_PLACE, SEASONAL_RECIPE_GROUPS
from module.island.manufacture_selector import (
    ManufactureIngredientCounter, _read_recipe_rows, _recipe_ids_in_same_category, _selected_recipe_row,
    match_recipe_name, read_selected_recipe_inventory, read_selected_recipe_stock,
    read_selected_recipe_quantity,
    select_manufacture_recipe, set_manufacture_quantity,
)
from module.island_manufacture.assets import (
    ALAS_RECIPE_AMOUNT_MAX, TEMPLATE_ALAS_RECIPE_ANCHOR,
)


SELECTED_CHEESE_FIXTURE = Path(__file__).parent / 'fixtures/island_manufacture/selected_cheese.png'
SELECTED_CORN_CUP_FIXTURE = SELECTED_CHEESE_FIXTURE.parent / 'selected_corn_cup.png'


class ManufactureCatalogTest(unittest.TestCase):
    def test_all_official_manufacture_formulas_and_previous_seasons_are_preserved(self):
        recipes = [entry for entries in get_catalog().values() for entry in entries]
        actual = {entry['recipe_id'] for entry in recipes}
        official = {recipe for slot in DIC_ISLAND_SLOT.values() if slot['place'] in (703, 704, 705, 706)
                    for recipe in slot['formula'] + slot['activity_formula']}
        self.assertTrue(official <= actual)
        self.assertEqual({entry['name'] for entry in recipes if entry['season'] is not None},
                         {'shepherd_purse', 'spring_bouquet', 'jasmine_oil', 'summer_bouquet',
                          'peanut_oil', 'autumn_bouquet'})
        self.assertTrue(all(entry['yield'] > 0 and entry['ingredients'] for entry in recipes))

    def test_filter_inventory_does_not_use_the_filing_cabinet_icon(self):
        recipe = next(entry for entry in get_catalog()['electronic_processing'] if entry['name'] == 'filter_element')
        self.assertIsNone(recipe['template'])
        self.assertEqual(recipe['item_id'], 3056)
        self.assertEqual(recipe['ingredients'], {2700: 1, 2705: 1, 2012: 1})
        self.assertEqual(recipe['selection_check'].name, 'SELECT_FILTER_ELEMENT_CHECK')

    def test_names_require_unique_complete_evidence(self):
        ids = _recipe_ids_in_same_category(701014)
        self.assertEqual(match_recipe_name(' P a p e r ', ids, 'en'), 701014)
        self.assertIsNone(match_recipe_name('Pape', ids, 'en'))
        self.assertIsNone(match_recipe_name('', ids, 'en'))
        self.assertIsNone(match_recipe_name('Utensils', ids, 'en'))

    def test_raw_recipe_search_is_scoped_to_its_verified_place(self):
        slot = next(slot for slot in DIC_ISLAND_SLOT.values() if slot['place'] not in (703, 704, 705, 706)
                    and slot['formula'])
        recipe_id = slot['formula'][0]
        same_place = _recipe_ids_in_same_category(recipe_id)
        expected = {recipe for candidate in DIC_ISLAND_SLOT.values() if candidate['place'] == slot['place']
                    for recipe in candidate['formula'] + candidate['activity_formula']}
        self.assertEqual(set(same_place), expected)

    def test_spring_summer_nursery_orchard_and_food_recipes_use_verified_places(self):
        for recipe_id in (9900009, 9900010, 9900017, 9900018,
                          9900001, 9900002, 9900011, 9900012,
                          9900019, 9900020, 9900013, 9900014, 9900021, 9900022):
            with self.subTest(recipe_id=recipe_id):
                place = GROUP_TO_PLACE[SEASONAL_RECIPE_GROUPS[recipe_id]]
                expected = {recipe for slot in DIC_ISLAND_SLOT.values() if slot['place'] == place
                            for recipe in slot['formula'] + slot['activity_formula']}
                expected.update(recipe for recipe, group in SEASONAL_RECIPE_GROUPS.items()
                                if GROUP_TO_PLACE[group] == place)
                actual = _recipe_ids_in_same_category(recipe_id)
                self.assertEqual(set(actual), expected)
                self.assertIn(recipe_id, actual)

    def test_regular_nursery_and_food_routes_include_other_seasons_without_crossing_places(self):
        for recipe_id, seasonal_ids, excluded in (
                (502001, {9900009, 9900010, 9900017, 9900018}, 9900021),
                (601001, {9900013, 9900014, 9900021, 9900022}, 9900017)):
            with self.subTest(recipe_id=recipe_id):
                actual = set(_recipe_ids_in_same_category(recipe_id))
                self.assertTrue(seasonal_ids <= actual)
                self.assertNotIn(excluded, actual)

    def test_summer_tomato_and_cucumber_names_can_be_matched_on_the_nursery_page(self):
        ids = _recipe_ids_in_same_category(9900017)
        self.assertEqual(match_recipe_name('番茄', ids, 'cn'), 9900017)
        self.assertEqual(match_recipe_name('黄瓜', ids, 'cn'), 9900018)


class ManufactureSelectorTest(unittest.TestCase):
    def setUp(self):
        self.main = SimpleNamespace(device=SimpleNamespace(image=np.full((720, 1280, 3), 30, dtype=np.uint8),
                                                          click=Mock(), swipe_vector=Mock(),
                                                          click_record_remove=Mock()),
                                    appear=Mock(return_value=True), appear_then_click=Mock(return_value=True))
        self.button = Button(area=(181, 114, 461, 248), color=(), button=(181, 114, 461, 248), name='TEST_RECIPE')

    def test_real_anchor_template_recovers_card_positions_without_top_assumption(self):
        original_server = server.server
        server.server = 'cn'
        try:
            template = TEMPLATE_ALAS_RECIPE_ANCHOR.image
            h, w = template.shape[:2]
            for top in (114, 263, 412):
                self.main.device.image[top + 97:top + 97 + h, 239:239 + w] = template
            paper_name = DIC_ISLAND_ITEM[3048]['name']['cn']
            ocr = Mock()
            ocr.ocr.return_value = [paper_name, paper_name, paper_name]
            with patch('module.island.manufacture_selector.Ocr', return_value=ocr):
                rows = _read_recipe_rows(self.main, _recipe_ids_in_same_category(701014))
            self.assertEqual([row.area[1] for _, row in rows], [114, 263, 412])
            self.assertEqual([recipe_id for recipe_id, _ in rows], [701014] * 3)
        finally:
            server.server = original_server

    def test_selector_does_not_accept_a_card_without_selected_border(self):
        self.main.loop = lambda **_kwargs: iter(range(2))
        with patch('module.island.manufacture_selector._read_recipe_rows', return_value=[(701014, self.button)]):
            with patch('module.island.manufacture_selector._is_selected', return_value=False):
                self.assertFalse(select_manufacture_recipe(self.main, 701014))
        self.main.device.click.assert_called()

    def test_selector_accepts_only_one_matching_selected_card(self):
        self.main.loop = lambda **_kwargs: iter(range(2))
        with patch('module.island.manufacture_selector._read_recipe_rows', return_value=[(701014, self.button)]):
            with patch('module.island.manufacture_selector._is_selected', return_value=True):
                self.assertTrue(select_manufacture_recipe(self.main, 701014))
        self.assertEqual(self.main._manufacture_selected_recipe_id, 701014)
        self.main.device.click.assert_not_called()

    def test_counter_keeps_full_stock_and_requires_the_separator(self):
        counter = ManufactureIngredientCounter()
        self.assertEqual(counter.after_process('12345678/4'), (12345678, 4))
        self.assertEqual(counter.after_process('0/(2+3)'), (0, 5))
        self.assertIsNone(counter.after_process('12345678'))
        self.assertIsNone(counter.after_process('12/0'))

    def test_inventory_is_complete_and_preserves_real_zero_product_stock(self):
        ocr = Mock()
        ocr.ocr.return_value = '0'
        counter = Mock()
        counter.ocr.return_value = [(12345678, 2), (7, 2), (2, 2)]
        with patch('module.island.manufacture_selector._selected_recipe_row', return_value=self.button):
            with patch('module.island.manufacture_selector.ManufactureIngredientCounter', return_value=counter):
                with patch('module.island.manufacture_selector.Ocr', return_value=ocr):
                    result = read_selected_recipe_inventory(self.main, 701022)
        self.assertEqual(result[2700], {'stock': 12345678, 'cost': 1, 'display_required': 2})
        self.assertEqual(result[3056]['stock'], 0)

    def test_unknown_input_or_wrong_quantity_factor_rejects_the_entire_inventory(self):
        for counters in ([(10, 1), None, (5, 1)], [(10, 1), (5, 2), (5, 1)]):
            with self.subTest(counters=counters):
                counter = Mock()
                counter.ocr.return_value = counters
                with patch('module.island.manufacture_selector._selected_recipe_row', return_value=self.button):
                    with patch('module.island.manufacture_selector.ManufactureIngredientCounter', return_value=counter):
                        self.assertIsNone(read_selected_recipe_inventory(self.main, 701022))

    def test_zero_input_recipes_read_real_stock_without_a_material_panel(self):
        with patch('module.island.manufacture_selector._selected_recipe_row', return_value=self.button):
            with patch('module.island.manufacture_selector.ManufactureIngredientCounter') as counter:
                for recipe_id, product_id in ((401001, 2700), (402001, 2800)):
                    for text, expected in (('0', 0), ('1234', 1234), ('', None)):
                        with self.subTest(recipe_id=recipe_id, text=text):
                            ocr = Mock()
                            ocr.ocr.return_value = text
                            with patch('module.island.manufacture_selector.Ocr', return_value=ocr):
                                result = read_selected_recipe_inventory(self.main, recipe_id)
                            if expected is None:
                                self.assertIsNone(result)
                            else:
                                self.assertEqual(result, {product_id: {
                                    'stock': expected, 'cost': 0, 'display_required': 0}})
                counter.assert_not_called()

    def test_stock_unknown_is_distinct_from_zero(self):
        with patch('module.island.manufacture_selector._selected_recipe_row', return_value=self.button):
            for text, expected in (('0', 0), ('12345678', 12345678), ('', None), ('1/3', None)):
                ocr = Mock()
                ocr.ocr.return_value = text
                with patch('module.island.manufacture_selector.Ocr', return_value=ocr):
                    self.assertEqual(read_selected_recipe_stock(self.main, 701014), expected)

    def test_product_stock_uses_the_single_crop_ocr_contract(self):
        with patch('module.island.manufacture_selector._selected_recipe_row', return_value=self.button):
            ocr = Mock()
            ocr.ocr.return_value = '13'
            with patch('module.island.manufacture_selector.Ocr', return_value=ocr) as constructor:
                self.assertEqual(read_selected_recipe_stock(self.main, 701014), 13)
            self.assertEqual(constructor.call_args.args[0], (393, 206, 456, 224))
            self.assertIs(ocr.ocr.call_args.args[0], self.main.device.image)
            self.assertNotIn('direct_ocr', ocr.ocr.call_args.kwargs)

    def test_product_stock_requires_complete_number_and_preserves_recognized_zero(self):
        cases = [('0', 0), ('有:0', 0), ('J:O', 0), (':13', 13), ('持有：13', 13), ('可：7', 7),
                 ('', None), ('O', None), ('1O', None), ('有:1O', None), ('2/5', None), ('有:?', None),
                 ('1:13', None), ('J:O5', None), ('J:5O', None), ('J:OO', None), ('J::0', None)]
        with patch('module.island.manufacture_selector._selected_recipe_row', return_value=self.button):
            for text, expected in cases:
                with self.subTest(text=text):
                    ocr = Mock()
                    ocr.ocr.return_value = text
                    with patch('module.island.manufacture_selector.Ocr', return_value=ocr) as constructor:
                        self.assertEqual(read_selected_recipe_stock(self.main, 603001), expected)
                    self.assertNotIn('alphabet', constructor.call_args.kwargs)

    def test_quantity_reads_existing_amount_and_uses_minus_then_confirms_two_frames(self):
        self.main._manufacture_selected_recipe_id = 701014
        self.main.loop = lambda **_kwargs: iter(range(4))
        ocr = Mock()
        ocr.ocr.side_effect = ['4', '3', '2', '2']
        with patch('module.island.manufacture_selector._selected_recipe_row', return_value=self.button):
            with patch('module.island.manufacture_selector.Ocr', return_value=ocr):
                self.assertTrue(set_manufacture_quantity(self.main, 2))
        clicked = [call.args[0].name for call in self.main.appear_then_click.call_args_list]
        self.assertEqual(clicked, ['ALAS_RECIPE_AMOUNT_MINUS'] * 2)

    def test_quantity_read_requires_selected_identity_and_complete_positive_integer(self):
        with patch('module.island.manufacture_selector._selected_recipe_row', return_value=self.button):
            for text, expected in (('12', 12), ('5', 5), ('0', None), ('', None), ('2/5', None)):
                with self.subTest(text=text):
                    ocr = Mock()
                    ocr.ocr.return_value = text
                    with patch('module.island.manufacture_selector.Ocr', return_value=ocr):
                        self.assertEqual(read_selected_recipe_quantity(self.main, 701001), expected)
        with patch('module.island.manufacture_selector._selected_recipe_row', return_value=None):
            with patch('module.island.manufacture_selector.Ocr') as ocr:
                self.assertIsNone(read_selected_recipe_quantity(self.main, 701001))
                ocr.assert_not_called()

    def test_quantity_needs_target_identity_and_cannot_succeed_on_unknown_numbers(self):
        self.assertFalse(set_manufacture_quantity(self.main, 1))
        self.main._manufacture_selected_recipe_id = 701014
        self.main.loop = lambda **_kwargs: iter(range(3))
        ocr = Mock()
        ocr.ocr.return_value = ''
        with patch('module.island.manufacture_selector._selected_recipe_row', return_value=self.button):
            with patch('module.island.manufacture_selector.Ocr', return_value=ocr):
                self.assertFalse(set_manufacture_quantity(self.main, 2))
        self.main.appear_then_click.assert_not_called()

    def test_quantity_uses_real_twelve_batch_and_seasonal_five_batch_limits(self):
        for recipe_id, limit in ((701001, 12), (9900006, 5)):
            with self.subTest(recipe_id=recipe_id):
                self.main._manufacture_selected_recipe_id = recipe_id
                self.main.loop = lambda **_kwargs: iter(range(2))
                ocr = Mock()
                ocr.ocr.return_value = str(limit)
                with patch('module.island.manufacture_selector._selected_recipe_row', return_value=self.button):
                    with patch('module.island.manufacture_selector.Ocr', return_value=ocr):
                        self.assertTrue(set_manufacture_quantity(self.main, limit))
                with self.assertRaises(ValueError):
                    set_manufacture_quantity(self.main, limit + 1)


class SelectedCornCupScreenshotTest(unittest.TestCase):
    """实际玉米杯错误帧验证模板容差、同帧身份与已选目标不重复点击。"""

    @classmethod
    def setUpClass(cls):
        from module.ocr.al_ocr import AlOcr, OcrSettings

        settings = OcrSettings(backend='onnx', device='cpu',
                               allow_vendor_execution_providers=False, model_version='alocr_cn_v3')
        cls.ocr_model = AlOcr(name='cn', settings=settings)

    def setUp(self):
        self.enterContext(patch.object(server, 'server', 'cn'))
        self.enterContext(patch('module.ocr.ocr.OCR_MODEL', SimpleNamespace(cnocr=self.ocr_model)))
        self.main = SimpleNamespace(
            device=SimpleNamespace(image=load_image(str(SELECTED_CORN_CUP_FIXTURE)),
                                   click=Mock(), swipe_vector=Mock(), click_record_remove=Mock()),
            loop=lambda **_kwargs: iter(range(2)),
        )
        # 页面和数量控件使用真实模板，避免全局 Mock(True) 掩盖像素偏移。
        self.main.appear = lambda button, offset=0: button.match(self.main.device.image, offset=offset)
        self.recipe_ids = _recipe_ids_in_same_category(603001)

    def test_real_max_button_requires_horizontal_tolerance(self):
        self.assertFalse(ALAS_RECIPE_AMOUNT_MAX.match(self.main.device.image, offset=(0, 20)))
        self.assertTrue(ALAS_RECIPE_AMOUNT_MAX.match(self.main.device.image, offset=(3, 20)))

    def test_selected_corn_cup_confirms_without_reclick_or_swipe(self):
        self.assertTrue(select_manufacture_recipe(self.main, 603001))
        self.assertEqual(self.main._manufacture_selected_recipe_id, 603001)
        self.main.device.click.assert_not_called()
        self.main.device.swipe_vector.assert_not_called()

    def test_selected_corn_cup_reads_real_quantity_and_full_inventory(self):
        self.assertEqual(read_selected_recipe_quantity(self.main, 603001), 1)
        self.assertEqual(read_selected_recipe_inventory(self.main, 603001), {
            2001: {'stock': 3005, 'cost': 3, 'display_required': 3},
            2603: {'stock': 1557, 'cost': 1, 'display_required': 1},
            3023: {'stock': 0, 'cost': 0, 'display_required': 0},
        })

    def test_selected_target_waits_without_reclick_when_quantity_page_is_not_ready(self):
        appear = self.main.appear
        self.main.appear = lambda button, offset=0: (
            False if button is ALAS_RECIPE_AMOUNT_MAX else appear(button, offset))
        self.assertFalse(select_manufacture_recipe(self.main, 603001))
        self.assertFalse(hasattr(self.main, '_manufacture_selected_recipe_id'))
        self.main.device.click.assert_not_called()
        self.main.device.swipe_vector.assert_not_called()

    def test_pixel_tolerance_does_not_confirm_other_or_ambiguous_selected_recipes(self):
        self.assertIsNone(_selected_recipe_row(self.main, 603002, self.recipe_ids))
        rows = _read_recipe_rows(self.main, self.recipe_ids)
        self.assertGreaterEqual(len(rows), 2)
        with patch('module.island.manufacture_selector._is_selected', return_value=True):
            self.assertFalse(select_manufacture_recipe(self.main, 603001))
            self.assertIsNone(_selected_recipe_row(self.main, 603001, self.recipe_ids))
        self.main.device.click.assert_not_called()


class SelectedFoodCardScreenshotTest(unittest.TestCase):
    """保留真实选中卡片，验证图标放大后仍用完整名称和唯一蓝框确认。"""

    @classmethod
    def setUpClass(cls):
        from module.ocr.al_ocr import AlOcr, OcrSettings

        settings = OcrSettings(backend='onnx', device='cpu',
                               allow_vendor_execution_providers=False, model_version='alocr_cn_v3')
        cls.ocr_model = AlOcr(name='cn', settings=settings)

    def setUp(self):
        self.enterContext(patch.object(server, 'server', 'cn'))
        # 固定本地模型，运行目录验收也不读取私人配置或连接 OCR 服务。
        self.enterContext(patch('module.ocr.ocr.OCR_MODEL', SimpleNamespace(cnocr=self.ocr_model)))
        frame = np.full((720, 1280, 3), 255, dtype=np.uint8)
        frame[55:668, 181:461] = load_image(str(SELECTED_CHEESE_FIXTURE))
        counter = SELECTED_CHEESE_FIXTURE.parent / 'cheese_milk_counter.png'
        frame[540:558, 740:842] = load_image(str(counter))
        self.main = SimpleNamespace(
            device=SimpleNamespace(image=frame, click=Mock(), swipe_vector=Mock(),
                                   click_record_remove=Mock()),
            appear=Mock(return_value=True),
            loop=lambda **_kwargs: iter(range(2)),
        )
        self.recipe_ids = _recipe_ids_in_same_category(901003)

    def test_selected_cheese_anchor_is_below_the_original_matching_threshold(self):
        """未选中卡片可匹配乘号；已选卡片的乘号因图标放大而发生位移。"""
        template = TEMPLATE_ALAS_RECIPE_ANCHOR.image
        similarities = []
        for top in (114, 263, 412):
            area = crop(self.main.device.image, (239, top + 97, 283, top + 115))
            result = cv2.matchTemplate(area, template, cv2.TM_CCOEFF_NORMED)
            similarities.append(cv2.minMaxLoc(result)[1])
        self.assertGreater(similarities[0], 0.75)
        self.assertGreater(similarities[1], 0.75)
        self.assertLess(similarities[2], 0.75)

    def test_full_blue_border_recovers_selected_row_before_real_name_ocr(self):
        rows = _read_recipe_rows(self.main, self.recipe_ids)
        self.assertEqual([(recipe_id, button.area[1]) for recipe_id, button in rows],
                         [(901001, 114), (901002, 263), (901003, 412)])
        selected = _selected_recipe_row(self.main, 901003, self.recipe_ids)
        self.assertIsNotNone(selected)
        self.assertEqual(selected.area, (181, 412, 461, 546))

    def test_confirmed_cheese_needs_no_click_or_search_swipe(self):
        self.assertTrue(select_manufacture_recipe(self.main, 901003))
        self.assertEqual(self.main._manufacture_selected_recipe_id, 901003)
        self.main.device.click.assert_not_called()
        self.main.device.swipe_vector.assert_not_called()

    def test_real_inventory_reads_selected_product_and_the_full_material_counter(self):
        self.assertEqual(read_selected_recipe_inventory(self.main, 901003), {
            2603: {'stock': 1557, 'cost': 8, 'display_required': 40},
            3006: {'stock': 13, 'cost': 0, 'display_required': 0},
        })
        self.main.device.click.assert_not_called()
        self.main.device.swipe_vector.assert_not_called()

    def test_cheese_border_does_not_confirm_another_recipe_or_unknown_name(self):
        self.assertIsNone(_selected_recipe_row(self.main, 901004, self.recipe_ids))
        self.assertFalse(select_manufacture_recipe(self.main, 901004))
        self.assertFalse(hasattr(self.main, '_manufacture_selected_recipe_id'))
        self.main.device.click.assert_not_called()
        ocr = Mock()
        ocr.ocr.return_value = ['欧姆蛋', '冰咖啡', '芝']
        with patch('module.island.manufacture_selector.Ocr', return_value=ocr):
            self.assertIsNone(_selected_recipe_row(self.main, 901003, self.recipe_ids))

    def test_blue_geometry_does_not_accept_off_color_icons_or_partial_cards(self):
        original = self.main.device.image
        variants = {}
        off_color = original.copy()
        mask = color_mask(off_color, (57, 189, 255), threshold=30)
        off_color[mask != 0] = (160, 85, 190)
        variants['偏色外框'] = off_color

        icon_only = np.full_like(original, 255)
        cv2.rectangle(icon_only, (203, 434), (294, 525), (57, 189, 255), thickness=5)
        variants['卡片内部图标'] = icon_only

        for direction in ('顶部', '底部'):
            partial = np.full_like(original, 255)
            selected_card = original[412:546, 181:461]
            if direction == '顶部':
                partial[55:144, 181:461] = selected_card[45:]
            else:
                partial[579:668, 181:461] = selected_card[:89]
            variants[f'{direction}截断卡片'] = partial

        for name, frame in variants.items():
            with self.subTest(case=name):
                self.main.device.image = frame
                # 独立验证蓝框新增的行定位，避免旧乘号锚点混入本负例。
                with patch.object(TEMPLATE_ALAS_RECIPE_ANCHOR, 'match_multi', return_value=[]):
                    with patch('module.island.manufacture_selector.Ocr') as ocr:
                        self.assertEqual(_read_recipe_rows(self.main, self.recipe_ids), [])
                ocr.assert_not_called()


if __name__ == '__main__':
    unittest.main()
