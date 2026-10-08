"""工坊完整配方、同帧库存证据和选品数量闭环的离线回归。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

import module.config.server as server
from module.base.button import Button
from module.island.data import DIC_ISLAND_ITEM, DIC_ISLAND_SLOT
from module.island.manufacture_catalog import get_catalog
from module.island.recipe_groups import GROUP_TO_PLACE, SEASONAL_RECIPE_GROUPS
from module.island.manufacture_selector import (
    ManufactureIngredientCounter, _read_recipe_rows, _recipe_ids_in_same_category,
    match_recipe_name, read_selected_recipe_inventory, read_selected_recipe_stock,
    read_selected_recipe_quantity,
    select_manufacture_recipe, set_manufacture_quantity,
)
from module.island_manufacture.assets import TEMPLATE_ALAS_RECIPE_ANCHOR


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


if __name__ == '__main__':
    unittest.main()
