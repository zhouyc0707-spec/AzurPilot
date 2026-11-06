"""选品页批量库存：真实截图、缺项未知、有界翻页和只读页面闭环。"""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

import module.config.server as server
from module.base.button import Button
from module.base.utils import load_image
from module.exception import GameStuckError
from module.island.data import DIC_ISLAND_RECIPE
from module.island.round_inventory import IslandRoundInventory
from module.island.round_recipe import (
    _open_probe_product_page, _read_recipe_stock_page, scan_post_product_stocks, scan_recipe_product_stocks,
)
from tests.test_island_round_recipe import probe_runner


def rows_for(recipes):
    return [(recipe, Button(area=(181, 114 + index * 149, 461, 248 + index * 149),
                            color=(), button=(181, 114 + index * 149, 461, 248 + index * 149),
                            name=f'RECIPE_ROW_{index}')) for index, recipe in enumerate(recipes)]


class RecipePages:
    def __init__(self, pages, start=0):
        self.pages = pages
        self.page = start
        self.frames = 0
        self.swipes = []
        self.ready = True
        self.island_round = IslandRoundInventory()
        self.device = SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8),
                                      click=Mock(), swipe_vector=self.swipe,
                                      click_record_remove=Mock())

    def appear(self, *args, **kwargs):
        return self.ready

    def loop(self, **kwargs):
        for _ in range(160):
            self.frames += 1
            yield

    def read(self, main, recipe_ids):
        values = self.pages[self.page]
        return rows_for(values), {recipe: stock for recipe, stock in values.items() if stock is not None}

    def swipe(self, vector, **kwargs):
        self.swipes.append(vector)
        step = -1 if vector[1] > 0 else 1
        self.page = max(0, min(self.page + step, len(self.pages) - 1))


class RecipePageBatchTests(unittest.TestCase):
    def setUp(self):
        self.main = SimpleNamespace(device=SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8)))

    def read(self, recipes, texts):
        ocr = Mock()
        ocr.ocr.return_value = texts
        with patch('module.island.manufacture_selector._read_recipe_rows', return_value=rows_for(recipes)), \
                patch('module.island.round_recipe.Ocr', return_value=ocr):
            result = _read_recipe_stock_page(self.main, recipes)
        return result, ocr

    def test_all_numbers_on_the_page_use_one_ocr_batch(self):
        (_, stocks), ocr = self.read([101001, 101002, 101003], ['0', '17', '有:25'])
        self.assertEqual(stocks, {101001: 0, 101002: 17, 101003: 25})
        self.assertEqual(len(ocr.ocr.call_args.args[0]), 3)
        self.assertEqual(ocr.ocr.call_args.kwargs, {'direct_ocr': True})
        ocr.ocr.assert_called_once()

    def test_empty_or_partial_number_does_not_become_zero(self):
        for invalid in ('', 'O', '1O', '12/3', '12?', None):
            with self.subTest(text=invalid):
                (_, stocks), _ = self.read([101001, 101002], ['0', invalid])
                self.assertEqual(stocks, {101001: 0})

    def test_duplicate_and_unknown_names_are_not_assigned_numbers(self):
        (_, stocks), ocr = self.read([101001, 101001, None, 101002], ['18'])
        self.assertEqual(stocks, {101002: 18})
        self.assertEqual(len(ocr.ocr.call_args.args[0]), 1)

    def test_invalid_batch_shape_does_not_shift_stock_to_another_product(self):
        for texts in (['12'], ['12', '25', '40'], '12'):
            with self.subTest(texts=texts):
                (_, stocks), _ = self.read([101001, 101002], texts)
                self.assertEqual(stocks, {})


class RecipeStockScanTests(unittest.TestCase):
    def scan(self, main, products, **kwargs):
        with patch('module.island.round_recipe._read_recipe_stock_page', side_effect=main.read):
            result = scan_recipe_product_stocks(main, products, **kwargs)
        main.device.click.assert_not_called()
        return result

    def test_visible_products_are_read_without_clicking_or_unneeded_scrolling(self):
        main = RecipePages([{101001: 0, 101002: 18, 101003: 25}])
        result = self.scan(main, ['wheat', 2001, 'pasture'])
        self.assertEqual(result, {2000: 0, 2001: 18, 2008: 25})
        self.assertEqual(main.frames, 2)
        self.assertEqual(main.swipes, [])
        self.assertEqual(main.island_round.get('wheat'), 0)
        self.assertEqual(main.island_round.in_production, {})

    def test_retained_middle_page_rewinds_then_reads_remaining_pages(self):
        main = RecipePages([{101001: 11}, {101002: 18}, {101003: 25}], start=1)
        self.assertEqual(self.scan(main, ['wheat', 'corn', 'pasture']), {2000: 11, 2001: 18, 2008: 25})
        self.assertEqual(main.swipes, [(0, 300), (0, 300), (0, -300), (0, -300)])

    def test_overlapping_pages_do_not_add_the_same_stock_twice(self):
        main = RecipePages([{101001: 11, 101002: 18}, {101002: 18, 101003: 25}])
        self.assertEqual(self.scan(main, ['wheat', 'corn', 'pasture']), {2000: 11, 2001: 18, 2008: 25})

    def test_missing_product_stays_unknown_after_end_of_list(self):
        main = RecipePages([{101001: 11}, {101002: 18}])
        result = self.scan(main, ['wheat', 'corn', 'pasture'])
        self.assertEqual(result, {2000: 11, 2001: 18})
        self.assertIsNone(main.island_round.get('pasture'))
        self.assertLess(main.frames, 160)
        self.assertEqual(main.swipes, [(0, 300), (0, -300), (0, -300)])

    def test_unknown_read_does_not_erase_existing_known_stock(self):
        main = RecipePages([{101001: None, 101002: 0}])
        main.island_round.observe('wheat', 35)
        self.assertEqual(self.scan(main, ['wheat', 'corn']), {2001: 0})
        self.assertEqual(main.island_round.get('wheat'), 35)

    def test_changing_number_needs_two_new_matching_frames(self):
        main = RecipePages([{101001: 12}])
        stable_read = main.read
        main.read = lambda *args: (rows_for([101001]), {101001: 0}) if main.frames == 1 else stable_read(*args)
        self.assertEqual(self.scan(main, ['wheat']), {2000: 12})
        self.assertEqual(main.frames, 3)

    def test_contradictory_read_on_later_page_stays_unknown(self):
        main = RecipePages([{101001: 12}, {101001: 13, 101002: 9}])
        self.assertEqual(self.scan(main, ['wheat', 'corn', 'pasture']), {2001: 9})
        self.assertIsNone(main.island_round.get('wheat'))

    def test_swipe_limit_is_finite_even_if_target_not_reached(self):
        main = RecipePages([{101001: 1}, {101002: 2}, {101003: 3}])
        self.assertEqual(self.scan(main, ['pasture'], max_swipes=1), {})
        self.assertEqual(main.swipes, [(0, 300), (0, -300)])
        self.assertIsNone(main.island_round.get('pasture'))

    def test_unready_page_never_reads_or_swipes(self):
        main = RecipePages([{101001: 12}])
        main.ready = False
        with patch('module.island.round_recipe._read_recipe_stock_page') as read:
            self.assertEqual(scan_recipe_product_stocks(main, ['wheat']), {})
        read.assert_not_called()
        self.assertEqual(main.swipes, [])

    def test_empty_request_and_invalid_limit_do_not_touch_pages(self):
        main = RecipePages([{101001: 12}])
        self.assertEqual(self.scan(main, []), {})
        self.assertEqual(main.frames, 0)
        for value in (-1, True, 1.5):
            with self.subTest(limit=value), self.assertRaises(ValueError):
                self.scan(main, ['wheat'], max_swipes=value)


class ScanPostStocksTests(unittest.TestCase):
    def test_open_scan_return_never_submits_production(self):
        main = SimpleNamespace(back_to_postmanage_from_dispatch=Mock(return_value=True),
                               confirm_post_add_order=Mock())
        with patch('module.island.round_recipe._open_probe_product_page', return_value=True) as opened, \
                patch('module.island.round_recipe.scan_recipe_product_stocks', return_value={2000: 0}) as scan:
            result = scan_post_product_stocks(main, 'post1', iter(['wheat', 'corn']), ['WorkerJuu'])
        self.assertEqual(result, {2000: 0})
        opened.assert_called_once_with(main, 'post1', 'wheat', ['WorkerJuu'])
        scan.assert_called_once_with(main, ('wheat', 'corn'))
        main.back_to_postmanage_from_dispatch.assert_called_once()
        main.confirm_post_add_order.assert_not_called()

    def test_open_failure_does_not_scan(self):
        with patch('module.island.round_recipe._open_probe_product_page', return_value=False), \
                patch('module.island.round_recipe.scan_recipe_product_stocks') as scan:
            self.assertEqual(scan_post_product_stocks(SimpleNamespace(), 'post1', ['wheat'], []), {})
        scan.assert_not_called()

    def test_failed_return_aborts_instead_of_continuing_on_selection_page(self):
        main = SimpleNamespace(back_to_postmanage_from_dispatch=Mock(return_value=False))
        with patch('module.island.round_recipe._open_probe_product_page', return_value=True), \
                patch('module.island.round_recipe.scan_recipe_product_stocks', return_value={}):
            with self.assertRaises(GameStuckError):
                scan_post_product_stocks(main, 'post1', ['wheat'], [])

    def test_unknown_post_page_cannot_enter_scanning(self):
        main = probe_runner(IslandRoundInventory())
        with patch('module.island.planned_dispatch.PlannedProductionMixin._planned_dispatch_stage',
                   return_value='unknown'), \
                patch('module.island.round_recipe.scan_recipe_product_stocks') as scan:
            self.assertEqual(scan_post_product_stocks(main, 'post1', ['wheat'], []), {})
        scan.assert_not_called()
        main.back_to_postmanage_from_dispatch.assert_called_once()

    def test_hidden_post_is_located_from_top_before_opening(self):
        main = probe_runner(IslandRoundInventory())
        main.appear.return_value = False
        calls = Mock()
        for name in ('post_manage_swipe_to_top', 'post_manage_swipe_until_appear', 'post_open'):
            calls.attach_mock(getattr(main, name), name)
        with patch('module.island.planned_dispatch.PlannedProductionMixin._planned_dispatch_stage',
                   return_value='products'):
            self.assertTrue(_open_probe_product_page(main, 'nursery1', 'pineapple', ['WorkerJuu']))
        self.assertEqual([call[0] for call in calls.mock_calls],
                         ['post_manage_swipe_to_top', 'post_manage_swipe_until_appear', 'post_open'])
        main.post_manage_swipe_until_appear.assert_called_once_with('nursery1', min_swipes=0)

    def test_visible_post_keeps_position_and_preserves_special_character_selection(self):
        main = probe_runner(IslandRoundInventory(), special_character=True,
                            select_special_character=Mock(return_value=True))
        with patch('module.island.planned_dispatch.PlannedProductionMixin._planned_dispatch_stage',
                   side_effect=['characters', 'products']):
            self.assertTrue(_open_probe_product_page(main, 'food1', 'apple_pie', ['WorkerJuu']))
        main.device.screenshot.assert_called_once()
        main.post_manage_swipe_to_top.assert_not_called()
        main.post_manage_swipe_until_appear.assert_not_called()
        main.select_special_character.assert_called_once_with('apple_pie')
        main.select_character.assert_not_called()

    def test_failed_post_location_never_attempts_open_or_character_selection(self):
        for fail_at in ('post_manage_swipe_to_top', 'post_manage_swipe_until_appear'):
            with self.subTest(fail_at=fail_at):
                main = probe_runner(IslandRoundInventory())
                main.appear.return_value = False
                getattr(main, fail_at).return_value = False
                self.assertFalse(_open_probe_product_page(main, 'forest1', 'wood', ['WorkerJuu']))
                main.post_open.assert_not_called()
                main.select_character.assert_not_called()


class RealRecipeStockScreenshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from module.ocr.al_ocr import AlOcr, OcrSettings

        cls.model = AlOcr(name='cn', settings=OcrSettings(
            backend='onnx', device='cpu', allow_vendor_execution_providers=False, model_version='alocr_cn_v3'))

    def test_actual_farm_and_food_cards_read_all_visible_stocks_without_selection(self):
        fixture_dir = Path(__file__).parent / 'fixtures' / 'island_manufacture'
        cases = [('farm_selected_wheat.png', [101001, 101002, 101003], {2000: 4335, 2001: 2984, 2008: 3792}),
                 ('selected_corn_cup.png', [603001, 603002, 603003], {3023: 0, 3009: 0, 3024: 7})]
        with patch.object(server, 'server', 'cn'), \
                patch('module.ocr.ocr.OCR_MODEL', SimpleNamespace(cnocr=self.model)):
            for fixture, recipes, expected in cases:
                with self.subTest(fixture=fixture):
                    main = RecipePages([{}])
                    main.device.image = load_image(str(fixture_dir / fixture))
                    main.appear = lambda button, offset=0: button.match(main.device.image, offset=offset)
                    products = [next(iter(DIC_ISLAND_RECIPE[recipe]['commission_product'])) for recipe in recipes]
                    self.assertEqual(scan_recipe_product_stocks(main, products), expected)
                    self.assertEqual(main.frames, 2)
                    self.assertEqual(main.swipes, [])
                    main.device.click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
