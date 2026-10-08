"""离线验证真实经营 SKU、空计划、货架预留和定制换菜的衔接。"""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.exception import RequestHumanTakeover
from module.island.island_business import IslandBusiness
from module.island.item_ids import LOCAL_TO_ITEM_ID
from module.island.order_stock import (
    BUSINESS_MENU_ITEMS, PLANNER_CONFIG, get_business_menu_items, get_menu_reserve_items,
    get_order_effective_stock, get_planned_menu, get_restaurant_capacity,
    get_restaurant_settings, get_shop_menu, normalize_business_menu, save_planned_menus,
    sync_active_menu,
    menu_reservations_known,
)
from tests.test_island_business_navigation import COFFEE, ResourceDevice, business_with


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
    values = {f'{PLANNER_CONFIG}.Enabled': True, 'IslandPlan.IslandPlan.Season': 'autumn'}
    for shop, menu in menus.items():
        values[f'IslandBusiness.IslandBusinessShop{shop[1:]}.PlannedMenu'] = json.dumps(menu)
    return MemoryConfig(values)


class OrderStockTests(unittest.TestCase):
    def test_unknown_legacy_all_visible_menu_is_not_zero_reserve(self):
        config = config_with()
        self.assertFalse(menu_reservations_known(config))
        for shop in range(1, 6):
            config.cross_set(f'IslandBusiness.IslandBusinessShop{shop}.PlannedMenu', '{}')
        self.assertTrue(menu_reservations_known(config))
        config.cross_set(f'{PLANNER_CONFIG}.Enabled', False)
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

    def test_legacy_manual_menu_is_kept_without_a_generated_plan(self):
        config = config_with()
        config.cross_set('IslandBusiness.IslandBusinessShop5.Product1', 'cheese')
        config.cross_set('IslandBusiness.IslandBusinessShop5.Product2', 'cheese')
        self.assertIsNone(get_planned_menu(config, 901))
        self.assertEqual(get_shop_menu(config, 901), {'cheese': 1})
        self.assertEqual(get_menu_reserve_items(config), {LOCAL_TO_ITEM_ID['cheese']: 5})
        config.cross_set('IslandBusiness.IslandBusinessShop5.PlannedMenu', '{}')
        self.assertEqual(get_shop_menu(config, 901), {})
        self.assertEqual(get_menu_reserve_items(config), {})
        self.assertEqual(config.cross_get('IslandBusiness.IslandBusinessShop5.Product1'), 'cheese')

    def test_disabled_planner_uses_manual_menu_and_preserves_saved_plan(self):
        config = config_with(s5={'morning_light': 10})
        config.cross_set(f'{PLANNER_CONFIG}.Enabled', False)
        config.cross_set('IslandBusiness.IslandBusinessShop5.Product1', 'cheese')
        self.assertEqual(get_shop_menu(config, 901), {'cheese': 1})

    def test_out_of_season_dishes_and_disabled_shops_do_not_reserve_stock(self):
        config = config_with(s1={'double_bamboo_shoots': 30, 'persimmon_cake': 8}, s5={'cheese': 20})
        config.cross_set('IslandBusiness.IslandBusiness.Batch1Shops', [1])
        config.cross_set('IslandBusiness.IslandBusiness.Batch2Shops', [])
        self.assertEqual(get_menu_reserve_items(config), {LOCAL_TO_ITEM_ID['persimmon_cake']: 5})
        self.assertNotIn('double_bamboo_shoots', get_business_menu_items(601, 'autumn'))

    def test_empty_character_config_still_enables_existing_worker_juu(self):
        self.assertEqual(get_restaurant_settings(MemoryConfig())[603]['waitress_slots'], ('any', 'none'))

    def test_invalid_menu_never_silently_becomes_an_empty_plan(self):
        invalid = ['broken', '[]', {'fish_chip': 3}, {'cheese': -1}, {'cheese': float('nan')},
                   {'cheese': True}, {name: 1 for name in BUSINESS_MENU_ITEMS[901]}]
        for menu in invalid:
            with self.subTest(menu=menu), self.assertRaises(RequestHumanTakeover):
                normalize_business_menu(901, menu)

    def test_save_validates_all_shops_before_writing_and_records_empty_shops(self):
        config = MemoryConfig()
        with self.assertRaises(RequestHumanTakeover):
            save_planned_menus(config, {601: {'tofu_meat': 10}, 901: {'fish_chip': 3}})
        self.assertEqual(config.values, {})
        save_planned_menus(config, {901: {LOCAL_TO_ITEM_ID['cheese']: 20}})
        self.assertEqual(json.loads(config.cross_get('IslandBusiness.IslandBusinessShop5.PlannedMenu')), {'cheese': 20})
        self.assertEqual(config.cross_get('IslandBusiness.IslandBusinessShop1.PlannedMenu'), '{}')

    def test_priority_orders_exempt_both_floor_and_menu(self):
        self.assertEqual(get_order_effective_stock(20, 7, 6), 7)
        self.assertEqual(get_order_effective_stock(20, 7, 6, priority=True), 20)
        self.assertEqual(get_order_effective_stock(2, -4, -5), 2)

    def test_runtime_replacement_keeps_menu_daily_amount_and_preserves_manual_config(self):
        config = config_with(s1={'persimmon_cake': 8, 'matsutake_chicken_soup': 0.1, 'tofu_meat': 23})
        config.cross_set('IslandBusiness.IslandBusinessShop1.Product3', 'fo_tiao')
        sync_active_menu(config, 601, ['persimmon_cake', 'tofu_meat', 'fo_tiao'])
        self.assertEqual(get_planned_menu(config, 601), {'persimmon_cake': 8, 'tofu_meat': 23, 'fo_tiao': 0.1})
        self.assertEqual(config.cross_get('IslandBusiness.IslandBusinessShop1.Product3'), 'fo_tiao')
        self.assertEqual(get_menu_reserve_items(config)[LOCAL_TO_ITEM_ID['fo_tiao']], 5)


class BusinessPlannedMenuTests(unittest.TestCase):
    def business(self, config):
        business = IslandBusiness.__new__(IslandBusiness)
        business.config = config
        business.season_config = SimpleNamespace(season='autumn')
        business.shops = [{'name': '啾咖啡', 'config_key': '5'}]
        business.shop_products = {'啾咖啡': [
            {'name': 'cheese', 'button': object()}, {'name': 'morning_light', 'button': object()},
        ]}
        return business

    def test_business_uses_only_planned_real_skus_and_empty_does_not_select_all(self):
        business = self.business(config_with(s5={'morning_light': 10}))
        business._load_shop_configs()
        self.assertEqual([item['name'] for item in business.active_products['啾咖啡']], ['morning_light'])
        business.config.cross_set('IslandBusiness.IslandBusinessShop5.PlannedMenu', '{}')
        business._load_shop_configs()
        self.assertEqual(business._planned_disabled_shops, {'啾咖啡'})
        business.device = SimpleNamespace(screenshot=Mock(), sleep=Mock(), click=Mock())
        business._select_business_product('啾咖啡')
        business.device.click.assert_not_called()

    def test_business_boost_replacement_updates_shared_reserve(self):
        business = self.business(config_with(s5={'cheese': 8}))
        business._load_shop_configs()
        business._find_product_by_name = lambda shop, name: next(
            (item for item in business.shop_products[shop] if item['name'] == name), None)
        with patch('module.island.island_business.logger'):
            self.assertTrue(business._replace_product_slot('啾咖啡', 0, 'morning_light'))
        self.assertEqual(get_planned_menu(business.config, 901), {'morning_light': 8})
        self.assertEqual(get_menu_reserve_items(business.config), {LOCAL_TO_ITEM_ID['morning_light']: 5})

    def test_empty_plan_skips_blue_shop_without_clicking_or_refilling(self):
        device = ResourceDevice([(COFFEE, 'blue', 190)])
        business = business_with(device)
        business._planned_disabled_shops = {'啾咖啡'}
        business._process_shop_entry = Mock()
        with patch('module.island.island_business.logger'):
            self.assertEqual(business._run_batch([COFFEE]), set())
        self.assertEqual(device.clicks, [])
        business._process_shop_entry.assert_not_called()
        business.config.task_delay.assert_called_once_with(server_update='00:00')

    def test_empty_plan_claims_previous_sale_then_skips_restarting(self):
        device = ResourceDevice([(COFFEE, 'yellow', 190)])
        business = business_with(device)
        business._planned_disabled_shops = {'啾咖啡'}
        business._process_shop_entry = Mock()
        business._claim_business_reward = Mock(side_effect=lambda **kwargs: setattr(
            device, 'rows', [(COFFEE, 'blue', 310)]))
        with patch('module.island.island_business.logger'):
            self.assertEqual(business._run_batch([COFFEE]), set())
        business._claim_business_reward.assert_called_once_with(button_already_clicked=True)
        business._process_shop_entry.assert_not_called()
        self.assertEqual(len(device.clicks), 1)

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
