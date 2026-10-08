"""通用种苗采购的实际缺口、SKU、数量及购买不确定恢复回归。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from module.exception import GameStuckError
from module.island.data import DIC_ISLAND_SHOP_RECIPE
from module.island.shop_selector import (
    ALAS_MATERIAL_SHOP_CHECK, ALAS_SHOP_BUY_CONFIRM,
    _confirm_purchase, _find_shop_card, _set_purchase_quantity,
    ensure_selected_recipe_material, material_purchase_plan,
)


class MaterialShopTests(unittest.TestCase):
    def setUp(self):
        self.main = SimpleNamespace(
            device=SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8),
                                   save_screenshot=Mock()),
            appear=Mock(return_value=True), appear_then_click=Mock(return_value=True),
            back_to_select_product_after_shop=Mock(return_value=True))

    def test_purchase_is_only_the_actual_material_deficit(self):
        plan = material_purchase_plan(101001, 1000, 50, 43)
        self.assertEqual(plan['count'], 7)
        self.assertEqual(plan['coin_cost'], 140)
        self.assertEqual(material_purchase_plan(101001, 1000, 50, 60)['count'], 0)

    def test_unknown_stock_wrong_material_and_other_currency_are_rejected(self):
        self.assertIsNone(material_purchase_plan(101001, 1000, 50, None))
        self.assertIsNone(material_purchase_plan(101001, 1001, 50, 0))
        original = DIC_ISLAND_SHOP_RECIPE[411000]
        with patch.dict(DIC_ISLAND_SHOP_RECIPE, {411000: {**original, 'resource_consume': {2: 20}}}):
            self.assertIsNone(material_purchase_plan(101001, 1000, 50, 0))
        with self.assertRaises(ValueError):
            material_purchase_plan(101001, 1000, 0, 0)

    def test_no_shop_action_when_stock_is_already_sufficient(self):
        with patch('module.island.shop_selector.read_selected_recipe_inventory', return_value={1000: {'stock': 50}}):
            with patch('module.island.shop_selector._enter_material_shop') as enter:
                self.assertTrue(ensure_selected_recipe_material(self.main, 101001, 1000, 50))
                enter.assert_not_called()
        self.main.back_to_select_product_after_shop.assert_not_called()

    def test_unknown_inventory_cannot_trigger_purchase(self):
        with patch('module.island.shop_selector.read_selected_recipe_inventory', return_value=None):
            with patch('module.island.shop_selector._enter_material_shop') as enter:
                self.assertFalse(ensure_selected_recipe_material(self.main, 101001, 1000, 50))
                enter.assert_not_called()

    def test_shop_card_needs_unique_complete_sku_name(self):
        names = [''] * 12
        names[3] = 'Wheat Seeds'
        ocr = Mock()
        ocr.ocr.return_value = names
        with patch('module.island.shop_selector.server.server', 'en'):
            with patch('module.island.shop_selector.Ocr', return_value=ocr):
                self.assertIsNotNone(_find_shop_card(self.main, 411000))
                names[3] = 'Wheat'
                self.assertIsNone(_find_shop_card(self.main, 411000))
                names[3] = names[4] = 'Wheat Seeds'
                self.assertIsNone(_find_shop_card(self.main, 411000))

    def test_quantity_uses_observed_amount_and_confirms_twice(self):
        self.main.loop = lambda **_kwargs: iter(range(3))
        ocr = Mock()
        ocr.ocr.side_effect = ['12', '2', '2']
        with patch('module.island.shop_selector.Ocr', return_value=ocr):
            self.assertTrue(_set_purchase_quantity(self.main, 2))
        clicked = self.main.appear_then_click.call_args.args[0]
        self.assertEqual(clicked.name, 'ALAS_SHOP_BUY_AMOUNT_MINUS_TEN')
        self.assertEqual(self.main.appear_then_click.call_count, 1)

    def test_unknown_purchase_result_never_clicks_buy_twice(self):
        self.main.loop = lambda **_kwargs: iter(range(4))
        self.main.appear = lambda button, **_kwargs: button is ALAS_MATERIAL_SHOP_CHECK
        with patch('module.island.shop_selector._coin_stock', return_value=1000):
            with self.assertRaises(GameStuckError):
                _confirm_purchase(self.main, {'coin_cost': 100}, 1000)
        self.main.appear_then_click.assert_called_once_with(ALAS_SHOP_BUY_CONFIRM, offset=(20, 20), interval=2)
        self.main.device.save_screenshot.assert_called_once()

    def _mock_purchase_steps(self, final_stock=50, coins=1000):
        self.enterContext(patch('module.island.shop_selector.read_selected_recipe_inventory',
                                side_effect=[{1000: {'stock': 43}}, {1000: {'stock': final_stock}}]))
        for helper in ('_enter_material_shop', '_ensure_fish_shop_tab', '_open_purchase',
                       '_set_purchase_quantity', '_confirm_purchase', 'select_manufacture_recipe'):
            self.enterContext(patch(f'module.island.shop_selector.{helper}', return_value=True))
        self.enterContext(patch('module.island.shop_selector._coin_stock', return_value=coins))

    def test_post_purchase_stock_recheck_is_required(self):
        self._mock_purchase_steps(final_stock=49)
        self.assertFalse(ensure_selected_recipe_material(self.main, 101001, 1000, 50))
        self.main.back_to_select_product_after_shop.assert_called_once()

    def test_confirmed_purchase_returns_to_same_recipe_and_true_stock(self):
        self._mock_purchase_steps(final_stock=50)
        self.assertTrue(ensure_selected_recipe_material(self.main, 101001, 1000, 50))
        self.main.back_to_select_product_after_shop.assert_called_once()

    def test_insufficient_actual_coins_do_not_open_purchase(self):
        with patch('module.island.shop_selector.read_selected_recipe_inventory', return_value={1000: {'stock': 43}}):
            with patch('module.island.shop_selector._enter_material_shop', return_value=True):
                with patch('module.island.shop_selector._ensure_fish_shop_tab', return_value=True):
                    with patch('module.island.shop_selector._coin_stock', return_value=139):
                        with patch('module.island.shop_selector._open_purchase') as open_purchase:
                            self.assertFalse(ensure_selected_recipe_material(self.main, 101001, 1000, 50))
                            open_purchase.assert_not_called()


if __name__ == '__main__':
    unittest.main()
