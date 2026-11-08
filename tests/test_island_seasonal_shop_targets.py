"""追加季节库存目标复用真实排产、共享扣账与产地目录，不连接游戏。"""

from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.island.data import DIC_ISLAND_RECIPE
from module.island.island import Island
from module.island.island_manufacture import IslandManufacture
from module.island.island_restaurant import IslandRestaurant
from module.island.island_season import SEASONAL_ITEMS, SeasonConfig
from module.island.island_shop_base import LEGACY_BUTTON_MATERIALS, IslandShopBase
from module.island.island_teahouse import IslandTeahouse
from module.island.item_ids import LOCAL_TO_ITEM_ID
from module.island.manufacture_selector import _recipe_ids_in_same_category, match_recipe_name
from module.island.production_recipe import recipe_for_local_name
from module.island.round_inventory import IslandRoundInventory
from module.island.round_warehouse import warehouse_catalog


NOW = datetime(2026, 11, 8, 13)


def make_shop(kind=IslandRestaurant, season='winter', targets=None, products=(), priority=False):
    values = {'IslandPlan.IslandPlan.Season': season}
    values.update({f'IslandSeasonalProduction.IslandSeason{season.capitalize()}.{name}': count
                   for name, count in (targets or {}).items()})
    config = SimpleNamespace(cross_get=lambda path, default=None: values.get(path, default),
                             task_delay=Mock())
    for prefix in ('IslandRestaurant', 'IslandTeahouse'):
        setattr(config, f'{prefix}_ChefFilter', 'WorkerJuu')
        setattr(config, f'{prefix}_PostNumber', 2)
        setattr(config, f'{prefix}NextTask_AwayCook', None)
        for index in range(1, 9):
            name, count = products[index - 1] if index <= len(products) else ('None', 0)
            setattr(config, f'{prefix}_Meal{index}', name)
            setattr(config, f'{prefix}_MealNumber{index}', count)
    config.IslandRestaurant_DoubleBambooShoots = priority
    config.IslandTeahouse_Seasonal = priority
    for line in ('WoodProcessing', 'ElectronicProcessing', 'Industrial', 'Handmade'):
        setattr(config, f'{line}_Positions', 2 if line == 'Handmade' else 0)
        setattr(config, f'{line}_Products', '')

    def initialize(shop, config, device=None, task=None):
        shop.config = config
        shop.device = Mock()
        shop.unavailable_characters = set()

    with patch.object(Island, '__init__', initialize), patch(
            'module.island.island_shop_base.get_global_season_config', side_effect=SeasonConfig):
        shop = kind(config)
    shop._test_config_values = values
    return shop


def prepare_shop(shop, stock=None):
    stocks = {}
    for product in shop.name_to_config:
        recipe = DIC_ISLAND_RECIPE[recipe_for_local_name(product)]
        for material in recipe['commission_cost']:
            stocks[material] = 10000
    for product in shop.name_to_config:
        stocks[LOCAL_TO_ITEM_ID[product]] = 0
    for name, count in (stock or {}).items():
        stocks[LOCAL_TO_ITEM_ID.get(name, name)] = count
    shop.island_round = IslandRoundInventory(stocks)
    shop._round_prepared = True
    shop._round_time_vars = []
    shop._post_time_vars = {}
    for index, (post_id, post) in enumerate(shop.posts.items(), 1):
        post.update(status='idle', crop=None, runs=0)
        time_var = f'{shop.time_prefix}{index}'
        shop._round_time_vars.append(time_var)
        shop._post_time_vars[post_id] = time_var
        setattr(shop, time_var, None)
    for method in ('goto_postmanage', 'post_manage_mode', 'post_close'):
        setattr(shop, method, Mock())
    shop.back_to_postmanage_from_dispatch = Mock(return_value=True)
    shop._prepare_recipe_page = Mock(return_value=True)
    shop.warehouse_filter = Mock(side_effect=AssertionError('本轮不得重新读仓库'))
    shop.post_check = Mock(side_effect=AssertionError('本轮不得重复收获'))
    shop.selections = []
    shop._select_round_product = Mock(side_effect=lambda name: (shop.selections.append(name) or True, True))
    shop.post_add_one = Mock()
    shop.produce_check = Mock(return_value=False)
    shop.confirm_food_dispatch = Mock(side_effect=lambda count, label: ((count, timedelta(hours=1)), NOW))
    shop._load_round_stock()
    return shop


class SeasonalFoodTargetsTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.island.island_shop_base.logger'))
        self.enterContext(patch('module.island.island_manufacture.logger'))
        self.enterContext(patch('module.island.production_recipe.ProductionRecipeMixin._open_product_page',
                                return_value=True))

    def test_regular_then_seasonal_without_changing_saved_menu(self):
        shop = prepare_shop(make_shop(targets={'green_jujube_porridge': 2}, products=[('tofu', 3)]))
        saved = deepcopy(shop._test_config_values)
        shop.run()
        self.assertEqual(shop.selections, ['tofu', 'green_jujube_porridge'])
        self.assertEqual([call.args[0] for call in shop.confirm_food_dispatch.call_args_list], [3, 2])
        self.assertEqual(shop.post_products, [('tofu', 3)])
        self.assertEqual(shop._test_config_values, saved)
        shop.warehouse_filter.assert_not_called()

    def test_seasonal_targets_do_not_consume_eight_manual_slots_and_precede_filler(self):
        products = [('tofu', 3)] * 8
        shop = prepare_shop(make_shop(targets={'green_jujube_porridge': 2}, products=products), {'tofu': 3})
        shop.config.IslandRestaurantNextTask_AwayCook = 'tofu'
        shop.run()
        self.assertEqual(shop.selections, ['green_jujube_porridge', 'tofu'])
        self.assertEqual([call.args[0] for call in shop.confirm_food_dispatch.call_args_list], [2, 12])
        self.assertEqual(shop.post_products, products)

    def test_same_manual_seasonal_dish_keeps_manual_priority_then_only_extra_deficit(self):
        shop = prepare_shop(make_shop(targets={'green_jujube_porridge': 5},
                                      products=[('green_jujube_porridge', 2)]))
        shop.run()
        self.assertEqual(shop.selections, ['green_jujube_porridge', 'green_jujube_porridge'])
        self.assertEqual([call.args[0] for call in shop.confirm_food_dispatch.call_args_list], [2, 3])
        self.assertEqual(shop.island_round.in_production[4050], 5)

    def test_shared_confirmed_production_is_counted_once_after_resume(self):
        shop = prepare_shop(make_shop(targets={'green_jujube_porridge': 9}), {'green_jujube_porridge': 2})
        shop.post_check_meal['green_jujube_porridge'] = 3
        shop.island_round.production_started('green_jujube_porridge', 2)
        shop.run()
        self.assertEqual(shop.selections, ['green_jujube_porridge'])
        shop.confirm_food_dispatch.assert_called_once()
        self.assertEqual(shop.confirm_food_dispatch.call_args.args[0], 2)
        self.assertEqual(shop.island_round.in_production[4050], 4)

    def test_unknown_finished_stock_skips_without_opening_production(self):
        shop = prepare_shop(make_shop(targets={'green_jujube_porridge': 5}))
        shop.island_round.observe('green_jujube_porridge', None)
        shop.run()
        self.assertEqual(shop.selections, [])
        shop.confirm_food_dispatch.assert_not_called()

    def test_unknown_working_item_prevents_seasonal_duplicate(self):
        shop = prepare_shop(make_shop(targets={'green_jujube_porridge': 5}))
        first = next(iter(shop.posts))
        shop.posts[first].update(status='working', crop=None)
        setattr(shop, shop._post_time_vars[first], NOW + timedelta(hours=1))
        shop.run()
        self.assertEqual(shop.selections, [])

    def test_known_shortage_skips_seasonal_and_allows_explicit_filler(self):
        shop = prepare_shop(make_shop(targets={'green_jujube_porridge': 5}), {'green_jujube': 0})
        shop.config.IslandRestaurantNextTask_AwayCook = 'tofu'
        shop.run()
        self.assertEqual(shop.selections, ['tofu', 'tofu'])

    def test_partial_receipt_consumes_actual_material_and_only_fills_remaining_goal(self):
        shop = prepare_shop(make_shop(targets={'green_jujube_porridge': 5}), {'rice': 100})
        shop.confirm_food_dispatch.side_effect = lambda count, label: ((min(count, 2), timedelta(hours=1)), NOW)
        shop.run()
        self.assertEqual([call.args[0] for call in shop.confirm_food_dispatch.call_args_list], [5, 3])
        self.assertEqual(shop.island_round.get('rice'), 80)
        self.assertEqual(shop.island_round.in_production[4050], 4)

    def test_positive_goal_suppresses_old_seasonal_priority_and_automatic_filler(self):
        shop = prepare_shop(make_shop(targets={'shiitake_chicken_soup': 2},
                                      products=[('tofu', 3)], priority=True))
        shop.run()
        self.assertEqual(shop.selections, ['tofu', 'shiitake_chicken_soup'])
        self.assertEqual([call.args[0] for call in shop.confirm_food_dispatch.call_args_list], [3, 2])
        satisfied = prepare_shop(make_shop(targets={'shiitake_chicken_soup': 2}, priority=True),
                                 {'shiitake_chicken_soup': 2})
        satisfied.run()
        self.assertEqual(satisfied.selections, [])

    def test_zero_goal_preserves_old_priority_toggle(self):
        shop = prepare_shop(make_shop(targets={'shiitake_chicken_soup': 0},
                                      products=[('tofu', 3)], priority=True))
        shop.run()
        self.assertEqual(shop.selections[:2], ['shiitake_chicken_soup', 'tofu'])

    def test_large_seasonal_goal_is_finite_even_when_deficit_equals_9999(self):
        shop = prepare_shop(make_shop(targets={'green_jujube_porridge': 9999}))
        shop.current_totals = shop._rebuild_current_totals({})
        shop._compute_base_demands()
        shop.schedule_production()
        self.assertEqual(shop.to_post_products['green_jujube_porridge'], 9989)
        self.assertEqual(shop.island_round.in_production[4050], 10)


class SeasonalCatalogTests(unittest.TestCase):
    def test_only_confirmed_seasonal_gathering_materials_allow_unknown_and_known_zero_blocks(self):
        seen = set()
        shop = make_shop()
        for categories in SEASONAL_ITEMS.values():
            for category in ('restaurant', 'teahouse', 'juu_eatery', 'handmade'):
                for name in categories.get(category, ()):
                    recipe = DIC_ISLAND_RECIPE[recipe_for_local_name(name)]
                    for item in set(recipe['commission_cost']) & LEGACY_BUTTON_MATERIALS:
                        with self.subTest(product=name, material=item):
                            seen.add(item)
                            stocks = {material: 100 for material in recipe['commission_cost']}
                            stocks.update({product: 0 for product in recipe['commission_product']})
                            shop.island_round = IslandRoundInventory(stocks)
                            shop.island_round.observe(item, None)
                            self.assertEqual(shop._round_max_producible(name, 1), 1)
                            self.assertIsNone(shop.island_round.get(item))
                            shop.island_round.observe(item, 0)
                            self.assertEqual(shop._round_max_producible(name, 1), 0)
        self.assertEqual(seen, LEGACY_BUTTON_MATERIALS)
        shop.island_round = IslandRoundInventory({'green_jujube_porridge': 0, 'rice': 100})
        self.assertEqual(shop._round_max_producible('green_jujube_porridge', 1), 0)

    def test_missing_images_register_only_requested_current_season_and_warehouse_origin(self):
        cases = ((IslandTeahouse, 'spring', 'pineapple_juice', 'teahouse', '鲜榨菠萝汁'),
                 (IslandManufacture, 'spring', 'spring_bouquet', 'factory', '春季花束'),
                 (IslandManufacture, 'summer', 'jasmine_oil', 'factory', '茉莉精油'),
                 (IslandManufacture, 'autumn', 'autumn_bouquet', 'factory', '秋季花束'))
        for kind, season, name, origin, cn_name in cases:
            with self.subTest(name=name):
                shop = make_shop(kind, season, {name: 5})
                entry = shop.name_to_config[name]
                self.assertTrue(entry['generic_recipe'])
                self.assertIn(LOCAL_TO_ITEM_ID[name], warehouse_catalog((shop,))[origin])
                self.assertIn(LOCAL_TO_ITEM_ID[name], shop.round_stock_items())
                recipe = recipe_for_local_name(name)
                self.assertEqual(match_recipe_name(cn_name, _recipe_ids_in_same_category(recipe), 'cn'), recipe)
                with patch('module.island.manufacture_selector.select_manufacture_recipe', return_value=True) as select:
                    self.assertEqual(IslandShopBase._select_round_product(shop, name), (True, False))
                    select.assert_called_once_with(shop, recipe)
                self.assertEqual(shop.post_products, [])

    def test_spring_flower_target_uses_confirmed_full_name_without_fixed_click(self):
        shop = make_shop(IslandTeahouse, 'spring', {'spring_flower_tea': 4})
        entry = shop.name_to_config['spring_flower_tea']
        self.assertTrue(entry['generic_recipe'])
        self.assertIsNone(entry['selection'])
        self.assertIsNone(shop.seasonal_high_priority_drink)

    def test_standalone_new_target_uses_warehouse_origin_and_preserves_unknown(self):
        shop = make_shop(IslandTeahouse, 'spring', {'pineapple_juice': 4})
        with patch('module.island.round_warehouse.RoundWarehouseReader') as reader:
            reader.return_value.read_group.return_value = {LOCAL_TO_ITEM_ID['apple_juice']: 3}
            IslandShopBase.get_warehouse_counts(shop)
        self.assertNotIn('pineapple_juice', shop.warehouse_counts)
        self.assertEqual(reader.return_value.read_group.call_args.args[0], 'teahouse')
        self.assertIn(LOCAL_TO_ITEM_ID['pineapple_juice'], reader.return_value.read_group.call_args.args[2])


class SeasonalManufactureTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.island.island_shop_base.logger'))
        self.enterContext(patch('module.island.island_manufacture.logger'))

    def factory(self, stock=None, targets=None):
        shop = prepare_shop(make_shop(IslandManufacture, targets=targets or {
            'plum_blossom_sachet': 2, 'winter_bouquet': 1}), stock)
        shop._load_round_stock()
        return shop

    def test_finite_seasonal_then_unlimited_original_products(self):
        shop = self.factory({'winter_bouquet': 1})
        shop.config.Handmade_Products = 'leather'
        shop.schedule_handmade()
        self.assertEqual(shop.selections, ['plum_blossom_sachet', 'leather'])
        self.assertEqual([call.args[0] for call in shop.confirm_food_dispatch.call_args_list], [2, 5])
        self.assertEqual(shop.config.Handmade_Products, 'leather')
        self.assertEqual(shop.island_round.in_production[4047], 2)

    def test_satisfied_goals_are_not_reintroduced_by_automatic_seasonal_fallback(self):
        shop = self.factory({'plum_blossom_sachet': 2, 'winter_bouquet': 1, 'leather': 0})
        shop.schedule_handmade()
        self.assertEqual(shop.selections, ['leather', 'leather'])

    def test_actual_partial_receipt_and_shared_inflight_limit_second_post(self):
        shop = self.factory({'winter_bouquet': 1, 'plum_blossom': 30},
                            {'plum_blossom_sachet': 5, 'winter_bouquet': 1})
        shop.island_round.production_started('plum_blossom_sachet', 1)
        shop.confirm_food_dispatch.side_effect = lambda count, label: ((min(count, 2), timedelta(hours=1)), NOW)
        shop._schedule_seasonal_handmade()
        self.assertEqual([call.args[0] for call in shop.confirm_food_dispatch.call_args_list], [4, 2])
        self.assertEqual(shop.island_round.get('plum_blossom'), 18)
        self.assertEqual(shop.island_round.in_production[4047], 5)

    def test_unknown_finished_or_working_product_does_not_dispatch(self):
        for unknown_work in (False, True):
            with self.subTest(unknown_work=unknown_work):
                shop = self.factory({'winter_bouquet': 1})
                if unknown_work:
                    shop._unknown_working_categories.add('handmade')
                else:
                    shop.island_round.observe('plum_blossom_sachet', None)
                shop._schedule_seasonal_handmade()
                self.assertEqual(shop.selections, [])

    def test_gray_button_keeps_stocks_and_is_not_retried_on_second_post(self):
        shop = self.factory({'winter_bouquet': 1, 'plum_blossom': 30})
        shop.produce_check.return_value = True
        shop._schedule_seasonal_handmade()
        self.assertEqual(shop.selections, ['plum_blossom_sachet'])
        shop.confirm_food_dispatch.assert_not_called()
        self.assertEqual(shop.island_round.get('plum_blossom'), 30)
        self.assertEqual(shop.island_round.in_production, {})

    def test_disabled_posts_are_not_used_for_new_goals(self):
        shop = self.factory({'winter_bouquet': 1})
        for post in shop.posts.values():
            post['round_enabled'] = False
        shop._schedule_seasonal_handmade()
        self.assertEqual(shop.selections, [])

    def test_standalone_manufacture_counts_only_actual_partial_receipts(self):
        shop = self.factory({'winter_bouquet': 1}, {'plum_blossom_sachet': 3, 'winter_bouquet': 1})
        shop.island_round = None
        shop.confirm_food_dispatch.side_effect = lambda count, label: ((min(count, 2), timedelta(hours=1)), NOW)
        shop._schedule_seasonal_handmade()
        self.assertEqual([call.args[0] for call in shop.confirm_food_dispatch.call_args_list], [3, 1])
        self.assertEqual(shop.selections, ['plum_blossom_sachet', 'plum_blossom_sachet'])
        self.assertEqual(shop.warehouse_counts['plum_blossom_sachet'], 0)

    def test_explicit_infinite_seasonal_products_remain_user_choice(self):
        shop = self.factory({'plum_blossom_sachet': 2, 'winter_bouquet': 1})
        shop.config.Handmade_Products = 'plum_blossom_sachet > leather'
        shop.schedule_handmade()
        self.assertEqual(shop.selections, ['plum_blossom_sachet', 'plum_blossom_sachet'])

    def test_standalone_explicit_missing_image_recipe_reuses_name_confirmation(self):
        shop = prepare_shop(make_shop(IslandManufacture, 'spring', {'spring_bouquet': 2}))
        shop.island_round = None
        entry = shop.name_to_config['spring_bouquet']
        post = next(iter(shop.posts))
        self.assertEqual(shop.select_product_with_material_check(post, [entry]), entry)
        self.assertEqual(shop.selections, ['spring_bouquet'])
        self.assertEqual(shop.confirm_food_dispatch.call_args.args[0], 5)


if __name__ == '__main__':
    unittest.main()
