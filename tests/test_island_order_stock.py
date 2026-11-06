"""离线验证手工经营菜单、货架预留与遗留自动规划缓存隔离。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.island.island_business import IslandBusiness
from module.island.item_ids import LOCAL_TO_ITEM_ID
from module.island.order_stock import (
    BUSINESS_MENU_ITEMS, get_business_menu_items, get_menu_reserve_items,
    get_order_effective_stock, get_restaurant_capacity,
    get_restaurant_settings, get_shop_menu, menu_reservations_known,
)


class MemoryConfig:
    """使用真实配置路径的内存夹具，绝不构造或保存账号配置。"""

    def __init__(self, values=None):
        self.values = dict(values or {})

    def cross_get(self, path, default=None):
        return self.values.get(path, default)

    def cross_set(self, path, value):
        self.values[path] = value

    def __getattr__(self, name):
        for path, value in self.values.items():
            if '_'.join(path.rsplit('.', 2)[-2:]) == name:
                return value
        raise AttributeError(name)


def config_with(**menus):
    values = {'IslandPlan.IslandPlan.Season': 'autumn'}
    for shop, menu in menus.items():
        for index, (name, amount) in enumerate(menu.items(), 1):
            if amount > 0:
                values[f'IslandBusiness.IslandBusinessShop{shop[1:]}.Product{index}'] = name
    return MemoryConfig(values)


class OrderStockTests(unittest.TestCase):
    def test_old_planner_cache_cannot_override_manual_menu_or_reservation(self):
        config = config_with(s5={'cheese': 1})
        config.cross_set('IslandPlan.IslandProductionPlanner.Enabled', True)
        config.cross_set('IslandPlan.IslandProductionPlanner.PlanFingerprint', 'old')
        config.cross_set('IslandBusiness.IslandBusinessShop5.PlannedMenu', '{"morning_light": 20}')
        self.assertEqual(get_shop_menu(config, 901), {'cheese': 1})
        self.assertEqual(get_menu_reserve_items(config), {LOCAL_TO_ITEM_ID['cheese']: 5})

    def test_unknown_manual_menu_is_not_replaced_by_empty_old_plan(self):
        config = config_with()
        for shop in range(1, 6):
            config.cross_set(f'IslandBusiness.IslandBusinessShop{shop}.PlannedMenu', '{}')
        self.assertFalse(menu_reservations_known(config))
        config.cross_set('IslandBusiness.Scheduler.Enable', False)
        self.assertTrue(menu_reservations_known(config))



    def test_explicit_manual_menu_is_known_without_a_plan(self):
        config = config_with()
        for shop in range(1, 6):
            config.cross_set(f'IslandBusiness.IslandBusinessShop{shop}.Product1', BUSINESS_MENU_ITEMS[
                {1: 601, 2: 602, 3: 603, 4: 604, 5: 901}[shop]][0])
        self.assertTrue(menu_reservations_known(config))

    def test_reserve_uses_full_shelf_even_for_small_daily_sales(self):
        config = config_with(s5={'cheese': 0.1, 'morning_light': 900, 'citrus_coffee': 0})
        config.cross_set('IslandBusiness.IslandBusinessShop5.Grade', 'gold')
        config.cross_set('IslandBusiness.IslandBusinessShop5.Char1', 'Cheshire')
        self.assertEqual(get_menu_reserve_items(config), {
            LOCAL_TO_ITEM_ID['cheese']: 7, LOCAL_TO_ITEM_ID['morning_light']: 7,
        })

    def test_capacity_uses_grade_and_unique_named_bonus_not_season_threshold(self):
        config = MemoryConfig({
            'IslandBusiness.IslandBusinessShop1.Grade': 'gold',
            'IslandBusiness.IslandBusinessShop1.Char1': 'ChaoHo',
            'IslandBusiness.IslandBusinessShop1.Char2': 'ChangFeng',
            'IslandBusiness.IslandBusiness.SeasonalThreshold': 99,
        })
        self.assertEqual(get_restaurant_capacity(config, 601), 8)
        config.cross_set('IslandBusiness.IslandBusinessShop1.Char2', 'ChaoHo')
        self.assertEqual(get_restaurant_capacity(config, 601), 7)



    def test_out_of_season_dishes_and_disabled_shops_do_not_reserve_stock(self):
        config = config_with(s1={'double_bamboo_shoots': 30, 'persimmon_cake': 8}, s5={'cheese': 20})
        config.cross_set('IslandBusiness.IslandBusiness.Batch1Shops', [1])
        config.cross_set('IslandBusiness.IslandBusiness.Batch2Shops', [])
        self.assertEqual(get_menu_reserve_items(config), {LOCAL_TO_ITEM_ID['persimmon_cake']: 5})
        self.assertNotIn('double_bamboo_shoots', get_business_menu_items(601, 'autumn'))

    def test_empty_character_config_still_enables_existing_worker_juu(self):
        self.assertEqual(get_restaurant_settings(MemoryConfig())[603]['waitress_slots'], ('any', 'none'))



    def test_priority_orders_exempt_both_floor_and_menu(self):
        self.assertEqual(get_order_effective_stock(20, 7, 6), 7)
        self.assertEqual(get_order_effective_stock(20, 7, 6, priority=True), 20)
        self.assertEqual(get_order_effective_stock(2, -4, -5), 2)



class BusinessManualMenuTests(unittest.TestCase):
    def test_business_ignores_old_plan_and_keeps_manual_selection(self):
        config = config_with(s5={'cheese': 1})
        config.cross_set('IslandPlan.IslandProductionPlanner.Enabled', True)
        config.cross_set('IslandBusiness.IslandBusinessShop5.PlannedMenu', '{"morning_light": 20}')
        business = self.business(config)
        business._load_shop_configs()
        self.assertEqual([item['name'] for item in business.active_products['啾咖啡']], ['cheese'])

    def business(self, config):
        business = IslandBusiness.__new__(IslandBusiness)
        business.config = config
        business.season_config = SimpleNamespace(season='autumn')
        business.shops = [{'name': '啾咖啡', 'config_key': '5'}]
        business.shop_products = {'啾咖啡': [
            {'name': 'cheese', 'button': object()}, {'name': 'morning_light', 'button': object()},
        ]}
        return business





    def test_supported_menu_items_match_real_business_click_templates(self):
        def init_without_device(business, config, **kwargs):
            business.config = config
        with patch('module.island.island_business.Island.__init__', init_without_device), \
                patch('module.island.island_business.logger'):
            business = IslandBusiness(MemoryConfig())
        for restaurant_id, supported in BUSINESS_MENU_ITEMS.items():
            shop_index = {601: 1, 602: 2, 603: 3, 604: 4, 901: 5}[restaurant_id]
            shop_name = next(shop['name'] for shop in business.shops if shop['config_key'] == str(shop_index))
            self.assertEqual(set(supported), {product['name'] for product in business.shop_products[shop_name]})
            for product in business.shop_products[shop_name]:
                self.assertTrue(product['button'].file)


if __name__ == '__main__':
    unittest.main()
