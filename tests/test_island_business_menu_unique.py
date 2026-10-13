"""经营菜单保序去重与季节替换回归，不读取真实配置或连接游戏。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from module.island.island_business import IslandBusiness, SEASONAL_DRINK_MAP


def product(name):
    return {'name': name, 'button': None}


class BusinessMenuUniqueTests(unittest.TestCase):
    def setUp(self):
        self.business = IslandBusiness.__new__(IslandBusiness)
        self.business.config = SimpleNamespace(Fallback='sunny_honey')
        self.business.season = 'autumn'
        self.business.seasonal_replace_enabled = True
        self.business.seasonal_threshold = 7
        self.business.season_config = SimpleNamespace(season='autumn')
        self.business.shops = [{'name': '白熊饮品', 'config_key': '2'}]
        self.business.shop_products = {'白熊饮品': [
            product('strawberry_lemon'), product('chrysanthemum_tea'),
            product('sunny_honey'), product('floral_fruity'),
        ]}
        self.business.active_products = {}
        self.business.device = SimpleNamespace(screenshot=Mock(), image=np.zeros((720, 1280, 3), dtype=np.uint8))
        self.business.goto_warehouse_within_postmanage = Mock()
        self.business._get_seasonal_warehouse_template = Mock(return_value=object())
        self.business._sync_planned_shop_menu = Mock()
        self.business._item_cn = lambda name: name
        self.enterContext(patch('module.island.island_business.logger'))
        self.warehouse = self.enterContext(patch('module.island.warehouse.WarehouseOCR'))
        self.warehouse.return_value.ocr_item_quantity.return_value = 4

    def replace(self, names):
        self.business.active_products['白熊饮品'] = [product(name) for name in names]
        return self.business._check_seasonal_product_quantity_and_replace(
            '白熊饮品', SEASONAL_DRINK_MAP, 'Fallback')

    def names(self):
        return [p['name'] for p in self.business.active_products['白熊饮品']]

    def test_existing_fallback_keeps_its_original_position(self):
        self.assertTrue(self.replace([
            'chrysanthemum_tea', 'strawberry_lemon', 'sunny_honey', 'floral_fruity']))
        self.assertEqual(self.names(), ['strawberry_lemon', 'sunny_honey', 'floral_fruity'])
        self.business._sync_planned_shop_menu.assert_called_once_with('白熊饮品')
        self.assertEqual(self.business.config.Fallback, 'sunny_honey')

    def test_existing_fallback_before_seasonal_product_is_not_duplicated(self):
        self.assertTrue(self.replace(['sunny_honey', 'chrysanthemum_tea', 'strawberry_lemon']))
        self.assertEqual(self.names(), ['sunny_honey', 'strawberry_lemon'])

    def test_new_fallback_replaces_only_the_seasonal_slot(self):
        self.assertTrue(self.replace(['strawberry_lemon', 'chrysanthemum_tea', 'floral_fruity']))
        self.assertEqual(self.names(), ['strawberry_lemon', 'sunny_honey', 'floral_fruity'])

    def test_sufficient_stock_preserves_menu(self):
        self.warehouse.return_value.ocr_item_quantity.return_value = 7
        self.assertFalse(self.replace(['chrysanthemum_tea', 'sunny_honey']))
        self.assertEqual(self.names(), ['chrysanthemum_tea', 'sunny_honey'])
        self.business._sync_planned_shop_menu.assert_not_called()

    def test_missing_fallback_preserves_existing_menu(self):
        self.business.config.Fallback = 'unknown'
        self.assertFalse(self.replace(['chrysanthemum_tea', 'strawberry_lemon']))
        self.assertEqual(self.names(), ['chrysanthemum_tea', 'strawberry_lemon'])
        self.business._sync_planned_shop_menu.assert_not_called()

    def test_seasonal_product_as_its_own_fallback_keeps_original_menu(self):
        self.business.config.Fallback = 'chrysanthemum_tea'
        self.assertFalse(self.replace(['chrysanthemum_tea', 'sunny_honey']))
        self.assertEqual(self.names(), ['chrysanthemum_tea', 'sunny_honey'])
        self.business._sync_planned_shop_menu.assert_not_called()

    def test_unique_menu_keeps_first_recognition_resource(self):
        first, second = product('sunny_honey'), product('sunny_honey')
        other = product('strawberry_lemon')
        result = IslandBusiness._unique_business_products([first, other, second])
        self.assertEqual(result, [first, other])
        self.assertIs(result[0], first)

    def test_manual_duplicate_config_remains_saved_but_is_selected_once(self):
        self.business.config.IslandBusinessShop2_Product1 = 'sunny_honey'
        self.business.config.IslandBusinessShop2_Product2 = 'sunny_honey'
        self.business.config.IslandBusinessShop2_Product3 = 'strawberry_lemon'
        with patch('module.island.island_business.get_planned_menu', return_value=None):
            self.business._load_shop_configs()
        self.assertEqual(self.names(), ['sunny_honey', 'strawberry_lemon'])
        self.assertEqual(self.business.config.IslandBusinessShop2_Product2, 'sunny_honey')

    def test_planned_duplicate_menu_is_loaded_once_in_order(self):
        with patch('module.island.island_business.get_planned_menu',
                   return_value=['sunny_honey', 'strawberry_lemon', 'sunny_honey']):
            self.business._load_shop_configs()
        self.assertEqual(self.names(), ['sunny_honey', 'strawberry_lemon'])

    def test_empty_planned_menu_still_disables_reopening(self):
        with patch('module.island.island_business.get_planned_menu', return_value=[]):
            self.business._load_shop_configs()
        self.assertEqual(self.names(), [])
        self.assertEqual(self.business._planned_disabled_shops, {'白熊饮品'})

    def test_selection_does_not_toggle_the_same_product_twice(self):
        button = SimpleNamespace(
            file='assets/cn/island_business/TEMPLATE_BUSINESS_PRODUCT_TEAHOUSE_SUNNY_HONEY.png',
            match_result=Mock(return_value=(0.99, SimpleNamespace(area=(10, 10, 30, 30)))),
        )
        selected = {'name': 'sunny_honey', 'button': button}
        self.business.active_products['白熊饮品'] = [selected, selected]
        self.business.device.click = Mock()
        self.business.device.sleep = Mock()
        self.business._select_business_product('白熊饮品')
        self.business.device.click.assert_called_once()
        self.business.device.screenshot.assert_called_once()
        button.match_result.assert_called_once()


if __name__ == '__main__':
    unittest.main()
