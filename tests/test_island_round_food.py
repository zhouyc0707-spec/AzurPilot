"""共享轮食品与制造：缺料、未知复核、确认扣料和收获复用。"""

import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.exception import GameStuckError
from module.island.assets import ISLAND_WORK_COMPLETE
from module.island.data import DIC_ISLAND_RECIPE
from module.island.island_juu_coffee import IslandJuuCoffee
from module.island.island_juu_eatery import IslandJuuEatery
from module.island.island_manufacture import IslandManufacture
from module.island.island_restaurant import IslandRestaurant
from module.island.island_teahouse import IslandTeahouse
from module.island.item_ids import LOCAL_TO_ITEM_ID
from module.island.planned_dispatch import recipe_for_local_name
from module.island.round_inventory import IslandRoundInventory
from tests.test_island_shop_production import NOW, ShopUI


def recipe_inventory(product, stock=0, material_stock=100):
    """构造配方页完整实读结果，保留真实零值。"""
    recipe = DIC_ISLAND_RECIPE[recipe_for_local_name(product)]
    inventory = {item: {'stock': material_stock, 'cost': cost}
                 for item, cost in recipe['commission_cost'].items()}
    inventory[LOCAL_TO_ITEM_ID[product]] = {'stock': stock, 'cost': 0}
    return inventory


class RoundCollectionConfirmationTests(unittest.TestCase):
    def task(self, kind):
        task = kind.__new__(kind)
        task.config = SimpleNamespace()
        task.config_post_number = 'positions'
        task.device = Mock()
        task.time_prefix = 'time_'
        task.post_manage_swipe_count = 0
        task.post_buttons = {'ISLAND_WOOD_PROCESSING_POST1': Mock(), 'ISLAND_WOOD_PROCESSING_POST2': Mock()}
        task.posts = {name: {'button': button} for name, button in task.post_buttons.items()}
        task.post_check_meal = {}
        task._unknown_working_categories = set()
        task._manufacture_scheduled = {}
        task.post_list_is_empty = Mock(return_value=False)
        task.post_open = Mock(return_value=True)
        task.appear = lambda button, **kwargs: button is ISLAND_WORK_COMPLETE
        task.post_get_and_close = Mock(return_value=False)
        for name in ('goto_postmanage', 'post_manage_mode', 'post_close', 'post_manage_swipe'):
            setattr(task, name, Mock())
        return task

    def test_food_and_manufacture_prepare_abort_when_collection_is_unconfirmed(self):
        for kind in (IslandJuuCoffee, IslandManufacture):
            with self.subTest(kind=kind.__name__):
                task = self.task(kind)
                task._round_prepared = True
                with self.assertRaisesRegex(GameStuckError, '收获结果未确认'):
                    task.prepare_round()
                self.assertFalse(task._round_prepared)
                task.post_get_and_close.assert_called_once()
                task.post_open.assert_called_once()

    def test_independent_post_check_keeps_existing_failure_handling(self):
        for kind in (IslandJuuCoffee, IslandManufacture):
            with self.subTest(kind=kind.__name__):
                task = self.task(kind)
                task.post_check('ISLAND_WOOD_PROCESSING_POST1', 'time_1')
                task.post_get_and_close.assert_called_once()


class RoundFoodTests(unittest.TestCase):
    def setUp(self):
        self.log = patch('module.island.island_shop_base.logger')
        self.log.start()
        self.addCleanup(self.log.stop)

    def shop(self, kind=IslandJuuCoffee, stock=None):
        ui = ShopUI(kind)
        ui.shop.island_round = IslandRoundInventory(stock)
        ui.shop.warehouse_filter = Mock(side_effect=AssertionError('本轮不得重复进仓库'))
        return ui.shop

    def test_full_raw_material_precheck_prevents_empty_coffee_dispatch(self):
        shop = self.shop(stock={'iced_coffee': 0, 'coffee_bean': 0})
        shop.post_open = Mock()
        self.assertEqual(shop.get_max_producible('iced_coffee', 12), 0)
        shop._post_produce_round_food('POST1', 'iced_coffee', 12, 'time1')
        self.assertTrue(shop.island_round.is_blocked('iced_coffee'))
        self.assertEqual(shop._post_produce_round_food('POST2', 'iced_coffee', 12, 'time2'), 0)
        shop.post_open.assert_not_called()

    def test_special_inventory_readers_use_shared_stock_without_navigation(self):
        for kind in (IslandJuuCoffee, IslandJuuEatery, IslandTeahouse):
            with self.subTest(kind=kind):
                shop = self.shop(kind, {'milk': 9, 'cheese': 3, 'fresh_honey': 11})
                kind.get_warehouse_counts(shop)
                shop.warehouse_filter.assert_not_called()
                if hasattr(shop, 'milk_stock'):
                    self.assertEqual(shop.milk_stock, 9)
                if hasattr(shop, 'fresh_honey'):
                    self.assertEqual(shop.fresh_honey, 11)

    def test_unknown_recipe_stock_is_observed_then_real_deficit_is_clamped(self):
        shop = self.shop(IslandJuuEatery)
        shop.to_post_products = {'corn_cup': 10}
        recipe = DIC_ISLAND_RECIPE[recipe_for_local_name('corn_cup')]
        inventory = {item: {'stock': 100, 'cost': cost}
                     for item, cost in recipe['commission_cost'].items()}
        inventory[LOCAL_TO_ITEM_ID['corn_cup']] = {'stock': 7, 'cost': 0}
        with patch('module.island.manufacture_selector.select_manufacture_recipe', return_value=True), \
                patch('module.island.manufacture_selector.read_selected_recipe_inventory', return_value=inventory) as read, \
                patch('module.island.manufacture_selector.set_manufacture_quantity', return_value=True) as quantity:
            self.assertEqual(shop._limit_round_food_batch('corn_cup', 10), 3)
        quantity.assert_called_once_with(shop, 3)
        read.assert_called_once()
        self.assertEqual(shop.island_round.get('corn_cup'), 7)

    def test_confirmed_zero_raw_material_blocks_without_clicking_dispatch(self):
        shop = self.shop(IslandJuuEatery)
        recipe = DIC_ISLAND_RECIPE[recipe_for_local_name('corn_cup')]
        inventory = {item: {'stock': 0, 'cost': cost}
                     for item, cost in recipe['commission_cost'].items()}
        inventory[LOCAL_TO_ITEM_ID['corn_cup']] = {'stock': 0, 'cost': 0}
        with patch('module.island.manufacture_selector.select_manufacture_recipe', return_value=True), \
                patch('module.island.manufacture_selector.read_selected_recipe_inventory', return_value=inventory), \
                patch('module.island.manufacture_selector.set_manufacture_quantity') as quantity:
            self.assertEqual(shop._limit_round_food_batch('corn_cup', 10), 0)
        quantity.assert_not_called()
        self.assertEqual(shop.island_round.get('corn_cup'), 0)
        self.assertTrue(shop.island_round.is_blocked('corn_cup'))

    def test_unknown_finished_stock_already_meets_target_is_not_a_failure(self):
        shop = self.shop(IslandJuuEatery)
        shop.to_post_products = {'corn_cup': 5}
        recipe = DIC_ISLAND_RECIPE[recipe_for_local_name('corn_cup')]
        inventory = {item: {'stock': 100, 'cost': cost}
                     for item, cost in recipe['commission_cost'].items()}
        inventory[LOCAL_TO_ITEM_ID['corn_cup']] = {'stock': 7, 'cost': 0}
        with patch('module.island.manufacture_selector.read_selected_recipe_inventory', return_value=inventory), \
                patch('module.island.manufacture_selector.set_manufacture_quantity') as quantity:
            self.assertEqual(shop._limit_round_food_batch('corn_cup', 5), 0)
        quantity.assert_not_called()
        self.assertFalse(shop.island_round.is_blocked('corn_cup'))
        self.assertTrue(shop._round_deficit_satisfied)

    def test_unknown_meal_is_checked_before_creating_prerequisite_demand(self):
        shop = self.shop(IslandRestaurant)
        shop.post_products = [('hearty_meal', 7)]
        recipe = DIC_ISLAND_RECIPE[recipe_for_local_name('hearty_meal')]
        inventory = {item: {'stock': 0, 'cost': cost}
                     for item, cost in recipe['commission_cost'].items()}
        inventory[LOCAL_TO_ITEM_ID['hearty_meal']] = {'stock': 10, 'cost': 0}
        shop.back_to_postmanage_from_dispatch = Mock()
        with patch('module.island.planned_dispatch.PlannedProductionMixin._planned_open_product_page', return_value=True), \
                patch('module.island.round_recipe.scan_recipe_product_stocks', return_value={}), \
                patch('module.island.manufacture_selector.select_manufacture_recipe', return_value=True), \
                patch('module.island.manufacture_selector.read_selected_recipe_inventory', return_value=inventory):
            shop._observe_round_targets(next(iter(shop.posts)))
        shop.current_totals = shop._rebuild_current_totals({})
        shop._compute_base_demands()
        self.assertEqual(shop.to_post_products, {})
        self.assertEqual(shop.island_round.get('hearty_meal'), 10)

    def test_store_stock_scan_is_once_and_first_dispatch_reuses_selected_worker(self):
        shop = self.shop(IslandJuuEatery)
        post = next(iter(shop.posts))
        shop.post_products = [('corn_cup', 10)]
        shop.to_post_products = {'corn_cup': 10}
        shop.produce_check = Mock(return_value=False)
        shop.confirm_food_dispatch = Mock(return_value=((10, timedelta(hours=1)), NOW))
        shop.back_to_postmanage_from_dispatch = Mock(return_value=True)

        def scan(main, products):
            self.assertEqual(set(products), set(shop.name_to_config))
            main.island_round.observe('corn_cup', 0)
            return {LOCAL_TO_ITEM_ID['corn_cup']: 0}

        with patch('module.island.round_recipe.scan_recipe_product_stocks', side_effect=scan) as scanner, \
                patch('module.island.planned_dispatch.PlannedProductionMixin._planned_open_product_page',
                      return_value=True) as opening, \
                patch('module.island.manufacture_selector.select_manufacture_recipe', return_value=True), \
                patch('module.island.manufacture_selector.read_selected_recipe_inventory',
                      return_value=recipe_inventory('corn_cup')), \
                patch('module.island.manufacture_selector.set_manufacture_quantity', return_value=True):
            shop._observe_round_targets(post)
            self.assertEqual(shop._post_produce_round_food(post, 'corn_cup', 10, 'time1'), 10)
            shop._observe_round_targets(list(shop.posts)[1])
        scanner.assert_called_once()
        opening.assert_called_once()
        shop.back_to_postmanage_from_dispatch.assert_not_called()
        self.assertEqual(shop.island_round.get('corn_cup'), 0)
        self.assertEqual(shop.island_round.in_production[LOCAL_TO_ITEM_ID['corn_cup']], 10)

    def test_known_stock_still_reads_materials_and_does_not_subtract_product_twice(self):
        shop = self.shop(IslandJuuEatery, {'corn_cup': 7, 'corn': 100})
        shop.to_post_products = {'corn_cup': 3}
        with patch('module.island.manufacture_selector.read_selected_recipe_inventory',
                   return_value=recipe_inventory('corn_cup', stock=7)) as read, \
                patch('module.island.manufacture_selector.set_manufacture_quantity', return_value=True):
            self.assertEqual(shop._limit_round_food_batch('corn_cup', 3), 3)
        read.assert_called_once()

    def test_manual_run_uses_product_page_stock_and_never_enters_warehouse(self):
        ui = ShopUI(IslandJuuEatery, [('corn_cup', 10)])
        shop = ui.shop
        shop.island_round = IslandRoundInventory()
        shop.get_warehouse_counts = Mock(side_effect=AssertionError('共享生产不进入仓库'))
        shop.post_produce = shop._post_produce_round_food
        shop.produce_check = Mock(return_value=False)
        shop.confirm_food_dispatch = Mock(return_value=((3, timedelta(hours=1)), NOW))
        shop.back_to_postmanage_from_dispatch = Mock(return_value=True)

        def scan(main, products):
            main.island_round.observe('corn_cup', 7)
            return {LOCAL_TO_ITEM_ID['corn_cup']: 7}

        with patch('module.island.round_recipe.scan_recipe_product_stocks', side_effect=scan) as scanner, \
                patch('module.island.planned_dispatch.PlannedProductionMixin._planned_open_product_page',
                      return_value=True) as opening, \
                patch('module.island.manufacture_selector.select_manufacture_recipe', return_value=True), \
                patch('module.island.manufacture_selector.read_selected_recipe_inventory',
                      return_value=recipe_inventory('corn_cup', stock=7)), \
                patch('module.island.manufacture_selector.set_manufacture_quantity', return_value=True) as quantity:
            ui.run()
        scanner.assert_called_once()
        opening.assert_called_once()
        quantity.assert_called_once_with(shop, 3)
        shop.get_warehouse_counts.assert_not_called()
        self.assertEqual(shop.island_round.get('corn_cup'), 7)
        self.assertEqual(shop.island_round.in_production[LOCAL_TO_ITEM_ID['corn_cup']], 3)

    def test_current_material_zero_overrides_previous_stock_without_dispatch(self):
        shop = self.shop(IslandJuuEatery, {'corn_cup': 0, 'corn': 100})
        shop.to_post_products = {'corn_cup': 10}
        with patch('module.island.manufacture_selector.read_selected_recipe_inventory',
                   return_value=recipe_inventory('corn_cup', material_stock=0)), \
                patch('module.island.manufacture_selector.set_manufacture_quantity') as quantity:
            self.assertEqual(shop._limit_round_food_batch('corn_cup', 10), 0)
        quantity.assert_not_called()
        self.assertTrue(shop.island_round.is_blocked('corn_cup'))

    def test_corrected_finished_stock_recalculates_before_second_post(self):
        ui = ShopUI(IslandJuuEatery, [('corn_cup', 10)])
        shop = ui.shop
        shop.island_round = IslandRoundInventory()
        shop.post_produce = shop._post_produce_round_food
        shop.produce_check = Mock(return_value=False)
        shop.confirm_food_dispatch = Mock(side_effect=lambda amount, label: ((amount, timedelta(hours=1)), NOW))
        shop.back_to_postmanage_from_dispatch = Mock(return_value=True)

        def scan(main, products):
            main.island_round.observe('corn_cup', 0)
            return {LOCAL_TO_ITEM_ID['corn_cup']: 0}

        with patch('module.island.round_recipe.scan_recipe_product_stocks', side_effect=scan), \
                patch('module.island.planned_dispatch.PlannedProductionMixin._planned_open_product_page',
                      return_value=True), \
                patch('module.island.manufacture_selector.select_manufacture_recipe', return_value=True), \
                patch('module.island.manufacture_selector.read_selected_recipe_inventory',
                      return_value=recipe_inventory('corn_cup', stock=7)), \
                patch('module.island.manufacture_selector.set_manufacture_quantity', return_value=True) as quantity:
            ui.run()
        quantity.assert_called_once_with(shop, 3)
        self.assertEqual(shop.island_round.get('corn_cup'), 7)
        self.assertEqual(shop.island_round.in_production[LOCAL_TO_ITEM_ID['corn_cup']], 3)
        self.assertEqual(len(shop.get_idle_posts()), 1)

    def test_unreadable_target_is_blocked_before_meal_prerequisites(self):
        shop = self.shop(IslandRestaurant)
        shop.post_products = [('hearty_meal', 7)]
        with patch('module.island.round_recipe.scan_recipe_product_stocks', return_value={}), \
                patch('module.island.planned_dispatch.PlannedProductionMixin._planned_open_product_page',
                      return_value=True), \
                patch('module.island.manufacture_selector.select_manufacture_recipe', return_value=True), \
                patch('module.island.manufacture_selector.read_selected_recipe_inventory', return_value=None):
            shop._observe_round_targets(next(iter(shop.posts)))
        shop.current_totals = shop._rebuild_current_totals({})
        shop._compute_base_demands()
        self.assertEqual(shop.to_post_products, {})
        self.assertIsNone(shop.island_round.get('hearty_meal'))
        self.assertTrue(shop.island_round.is_blocked('hearty_meal'))

    def test_meal_consumption_replenishes_manual_target_once(self):
        ui = ShopUI(IslandRestaurant, [('hearty_meal', 7), ('tofu', 10)])
        shop = ui.shop
        shop.island_round = IslandRoundInventory()
        shop.post_produce = shop._post_produce_round_food
        shop.produce_check = Mock(return_value=False)
        shop.confirm_food_dispatch = Mock(side_effect=lambda amount, label: ((amount, timedelta(hours=1)), NOW))
        shop.back_to_postmanage_from_dispatch = Mock(return_value=True)

        def scan(main, products):
            for product, stock in {'hearty_meal': 0, 'tofu': 10, 'omurice': 10}.items():
                main.island_round.observe(product, stock)
            return dict(main.island_round.stocks)

        def read(main, recipe_id):
            recipe = DIC_ISLAND_RECIPE[recipe_id]
            inventory = {}
            for item, cost in recipe['commission_cost'].items():
                stock = main.island_round.get(item)
                inventory[item] = {'stock': stock if stock is not None else 1000, 'cost': cost}
            for item in recipe['commission_product']:
                inventory[item] = {'stock': main.island_round.get(item), 'cost': 0}
            return inventory

        with patch('module.island.round_recipe.scan_recipe_product_stocks', side_effect=scan), \
                patch('module.island.planned_dispatch.PlannedProductionMixin._planned_open_product_page',
                      return_value=True), \
                patch('module.island.manufacture_selector.select_manufacture_recipe', return_value=True), \
                patch('module.island.manufacture_selector.read_selected_recipe_inventory', side_effect=read), \
                patch('module.island.manufacture_selector.set_manufacture_quantity', return_value=True) as quantity:
            ui.run()
        self.assertEqual([call.args[1] for call in quantity.call_args_list], [7, 7])
        self.assertEqual(shop.island_round.get('tofu'), 3)
        self.assertEqual(shop.island_round.get('omurice'), 3)
        self.assertEqual(shop.island_round.in_production[LOCAL_TO_ITEM_ID['hearty_meal']], 7)
        self.assertEqual(shop.island_round.in_production[LOCAL_TO_ITEM_ID['tofu']], 7)
        self.assertIsNone(shop._round_recipe_post)

    def test_satisfied_target_closes_retained_scan_page_without_dispatch(self):
        ui = ShopUI(IslandJuuEatery, [('corn_cup', 10)])
        shop = ui.shop
        shop.island_round = IslandRoundInventory()
        shop.post_produce = Mock(side_effect=AssertionError('目标达标不得派遣'))
        shop.back_to_postmanage_from_dispatch = Mock(return_value=True)

        def scan(main, products):
            main.island_round.observe('corn_cup', 10)
            return {LOCAL_TO_ITEM_ID['corn_cup']: 10}

        with patch('module.island.round_recipe.scan_recipe_product_stocks', side_effect=scan), \
                patch('module.island.planned_dispatch.PlannedProductionMixin._planned_open_product_page',
                      return_value=True):
            ui.run()
        shop.back_to_postmanage_from_dispatch.assert_called_once()
        self.assertIsNone(shop._round_recipe_post)

    def test_unreadable_recipe_blocks_without_reusing_old_material_balance(self):
        shop = self.shop(IslandJuuEatery, {'corn_cup': 0, 'corn': 100})
        with patch('module.island.manufacture_selector.read_selected_recipe_inventory', return_value=None), \
                patch('module.island.manufacture_selector.set_manufacture_quantity') as quantity:
            self.assertEqual(shop._limit_round_food_batch('corn_cup', 10), 0)
        quantity.assert_not_called()
        self.assertTrue(shop.island_round.is_blocked('corn_cup'))
        self.assertEqual(shop.island_round.get('corn'), 100)

    def test_special_chef_is_reselected_after_generic_stock_scan(self):
        shop = self.shop()
        shop.special_character = True
        shop.back_to_postmanage_from_dispatch = Mock(return_value=True)
        post = next(iter(shop.posts))
        with patch('module.island.planned_dispatch.PlannedProductionMixin._planned_open_product_page',
                   return_value=True) as opening:
            self.assertTrue(shop._open_round_recipe_page(post))
            self.assertTrue(shop._open_round_recipe_page(post, 'wake_up_call'))
            self.assertTrue(shop._open_round_recipe_page(post, 'wake_up_call'))
        self.assertEqual(opening.call_count, 2)
        self.assertEqual(opening.call_args.args[-1], 'wake_up_call')
        shop.back_to_postmanage_from_dispatch.assert_called_once()

    def test_confirmed_actual_batch_consumes_once_and_output_stays_in_production(self):
        shop = self.shop(stock={'cheese': 0, 'milk': 40})
        post = next(iter(shop.posts))
        shop.deduct_materials = Mock(side_effect=AssertionError('不得重复扣旧账'))
        actual = shop.finish_food_dispatch(post, 'cheese', 'time1', 5, (3, timedelta(hours=1)), NOW)
        self.assertEqual(actual, 3)
        self.assertEqual(shop.island_round.get('milk'), 16)
        self.assertEqual(shop.milk_stock, 16)
        self.assertEqual(shop.island_round.get('cheese'), 0)
        self.assertEqual(shop.island_round.in_production[LOCAL_TO_ITEM_ID['cheese']], 3)
        other = self.shop(IslandJuuEatery)
        other.island_round = shop.island_round
        IslandJuuEatery.get_warehouse_counts(other)
        self.assertEqual(other.milk_stock, 16)

    def test_failed_prerequisite_readded_by_meal_is_not_retried(self):
        shop = self.shop(IslandRestaurant, {'tofu': 0, 'soybean': 100})
        shop.chef_unavailable_products.add('tofu')
        shop.to_post_products = {'tofu': 5}
        for post in shop.posts.values():
            post['status'] = 'idle'
        shop.post_produce = Mock()
        shop.schedule_production()
        shop.post_produce.assert_not_called()

    def test_prepare_collects_disabled_post_and_run_reuses_inspection(self):
        ui = ShopUI(IslandJuuCoffee)
        shop = ui.shop
        setattr(shop.config, shop.config_post_number, 1)
        shop.post_check = Mock(side_effect=ui.check_post)
        shop.prepare_round()
        self.assertEqual(shop.post_check.call_count, 2)
        self.assertEqual(len(shop.get_idle_posts()), 1)
        shop.island_round = IslandRoundInventory({'iced_coffee': 100})
        shop._observe_round_targets = Mock()
        ui.run()
        self.assertEqual(shop.post_check.call_count, 2)


class RoundManufactureTests(unittest.TestCase):
    def factory(self):
        factory = IslandManufacture.__new__(IslandManufacture)
        factory.island_round = IslandRoundInventory({'file_cabinet': 0})
        factory._reserved_targets = {}
        factory._planner_protection = {}
        factory.chef_unavailable_products = set()
        factory._unknown_working_categories = set()
        factory.name_to_config = {'file_cabinet': {}}
        factory.special_materials = {}
        factory._post_time_vars = {'ISLAND_WOOD_PROCESSING_POST1': 'time1'}
        factory._prepare_planned_recipe_page = Mock(return_value=True)
        factory.back_to_postmanage_from_dispatch = Mock()
        factory.produce_check = Mock(return_value=False)
        factory.confirm_food_dispatch = Mock(return_value=((2, timedelta(hours=1)), NOW))
        factory.posts = {'ISLAND_WOOD_PROCESSING_POST1': {'status': 'idle'}}
        factory.to_post_products = {}
        return factory

    def test_manufacture_uses_actual_batches_for_all_materials(self):
        factory = self.factory()
        recipe = DIC_ISLAND_RECIPE[recipe_for_local_name('file_cabinet')]
        for material, cost in recipe['commission_cost'].items():
            factory.island_round.observe(material, cost * 4)
        inventory = {item: {'stock': factory.island_round.get(item), 'cost': cost}
                     for item, cost in recipe['commission_cost'].items()}
        inventory[LOCAL_TO_ITEM_ID['file_cabinet']] = {'stock': 0, 'cost': 0}
        with patch('module.island.manufacture_selector.select_manufacture_recipe', return_value=True), \
                patch('module.island.manufacture_selector.read_selected_recipe_inventory', return_value=inventory), \
                patch('module.island.manufacture_selector.set_manufacture_quantity', return_value=True):
            result = factory.select_product_with_material_check(
                'ISLAND_WOOD_PROCESSING_POST1', [{'name': 'file_cabinet'}])
        self.assertEqual(result['name'], 'file_cabinet')
        for material, cost in recipe['commission_cost'].items():
            self.assertEqual(factory.island_round.get(material), cost * 2)
        item = LOCAL_TO_ITEM_ID['file_cabinet']
        self.assertEqual(factory.island_round.get(item), 0)
        self.assertEqual(factory.island_round.in_production[item], 2 * recipe['commission_product'][item])

    def test_known_zero_manufacture_material_avoids_opening_slot(self):
        factory = self.factory()
        recipe = DIC_ISLAND_RECIPE[recipe_for_local_name('file_cabinet')]
        for material in recipe['commission_cost']:
            factory.island_round.observe(material, 0)
        self.assertIsNone(factory.select_product_with_material_check(
            'ISLAND_WOOD_PROCESSING_POST1', [{'name': 'file_cabinet'}]))
        factory._prepare_planned_recipe_page.assert_not_called()

    def test_industrial_stock_is_scanned_before_manual_threshold_and_only_once(self):
        factory = self.factory()
        factory.manufacture = {'industrial_production': {'items': [
            {'name': 'iron_nail'}, {'name': 'cutlery'}]}}
        factory.name_to_config = {item['name']: item for item in factory.manufacture['industrial_production']['items']}
        factory.get_idle_posts_by_category = Mock(return_value=['ISLAND_INDUSTRIAL_POST1', 'ISLAND_INDUSTRIAL_POST2'])
        factory.select_product_with_material_check = Mock()

        def scan(main, products):
            self.assertEqual(products, ['iron_nail', 'cutlery'])
            main.island_round.observe('iron_nail', 20)
            main.island_round.observe('cutlery', 0)
            return {LOCAL_TO_ITEM_ID['iron_nail']: 20, LOCAL_TO_ITEM_ID['cutlery']: 0}

        with patch('module.island.round_recipe.scan_recipe_product_stocks', side_effect=scan) as scanner:
            factory.schedule_industrial_production()
            factory.schedule_industrial_production()
        scanner.assert_called_once()
        factory._prepare_planned_recipe_page.assert_called_once_with('ISLAND_INDUSTRIAL_POST1')
        self.assertEqual(factory.select_product_with_material_check.call_count, 4)
        self.assertEqual(factory.select_product_with_material_check.call_args.args[1],
                         [{'name': 'cutlery'}, {'name': 'iron_nail'}])

    def test_first_manufacture_dispatch_reuses_scan_page(self):
        factory = self.factory()
        factory.manufacture = {'wood_processing': {'items': [{'name': 'file_cabinet'}]}}
        with patch('module.island.round_recipe.scan_recipe_product_stocks', return_value={}) as scanner, \
                patch('module.island.manufacture_selector.select_manufacture_recipe', return_value=True), \
                patch('module.island.manufacture_selector.read_selected_recipe_inventory',
                      return_value=recipe_inventory('file_cabinet')), \
                patch('module.island.manufacture_selector.set_manufacture_quantity', return_value=True):
            factory._observe_round_category('wood_processing', 'ISLAND_WOOD_PROCESSING_POST1')
            factory._select_round_manufacture('ISLAND_WOOD_PROCESSING_POST1', [{'name': 'file_cabinet'}])
        scanner.assert_called_once()
        factory._prepare_planned_recipe_page.assert_called_once()
        factory.back_to_postmanage_from_dispatch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
