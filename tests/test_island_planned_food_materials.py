"""验证食品使用真实耗材计数与原套餐保留线，不改用户的手工槽位。"""

import unittest
from unittest.mock import patch

from module.exception import GameStuckError
from module.island.data import DIC_ISLAND_RECIPE
from module.island.item_ids import LOCAL_TO_ITEM_ID
from module.island.island_shop_base import IslandShopBase
from module.island.planned_dispatch import recipe_for_local_name
from module.island.production_planner import CONFIG_PREFIX
from tests.test_island_production_planner import MemoryConfig


class FoodMaterialTests(unittest.TestCase):
    def setUp(self):
        self.ui = IslandShopBase.__new__(IslandShopBase)
        self.ui.config = MemoryConfig(**{f'{CONFIG_PREFIX}.PlanFingerprint': 'valid'})
        self.ui.warehouse_counts = {}
        self.ui._planner_protection = {}
        self.ui._reserved_targets = {}
        self.ui.meal_compositions = {}
        self.ui.post_check_meal = {}
        self.ui._planned_food_dispatched = {}
        self.ui._planned_food_filler = False
        self.select = patch('module.island.manufacture_selector.select_manufacture_recipe', return_value=True)
        self.quantity = patch('module.island.manufacture_selector.set_manufacture_quantity', return_value=True)
        self.select.start()
        self.quantity_setter = self.quantity.start()
        self.addCleanup(self.select.stop)
        self.addCleanup(self.quantity.stop)

    def inventory(self, name, product=0):
        recipe = DIC_ISLAND_RECIPE[recipe_for_local_name(name)]
        result = {item: {'stock': 100, 'cost': cost, 'display_required': cost}
                  for item, cost in recipe['commission_cost'].items()}
        result[LOCAL_TO_ITEM_ID[name]] = {'stock': product, 'cost': 0, 'display_required': 0}
        self.ui.config.values[f'{CONFIG_PREFIX}.PlannerTargets'] = '{"%s":100}' % LOCAL_TO_ITEM_ID[name]
        return result

    def test_every_raw_ingredient_respects_the_explicit_protection(self):
        stocks = self.inventory('latte')
        stocks[2603]['stock'] = 7
        self.ui._planner_protection = {'milk': 3}
        with patch('module.island.manufacture_selector.read_selected_recipe_inventory', return_value=stocks):
            self.assertEqual(self.ui._limit_planned_food_batch('latte', 7), 2)
        self.quantity_setter.assert_called_once_with(self.ui, 2)

    def test_existing_stock_and_newly_dispatched_jobs_reduce_missing_food(self):
        stocks = self.inventory('latte', product=4)
        self.ui.config.values[f'{CONFIG_PREFIX}.PlannerTargets'] = '{"3007":10}'
        self.ui.post_check_meal = {'latte': 3}
        self.ui._planned_food_dispatched = {'latte': 2}
        with patch('module.island.manufacture_selector.read_selected_recipe_inventory', return_value=stocks):
            self.assertEqual(self.ui._limit_planned_food_batch('latte', 7), 1)

    def test_generic_seasonal_recipe_uses_its_actual_five_round_limit(self):
        stocks = self.inventory('pineapple_juice')
        with patch('module.island.manufacture_selector.read_selected_recipe_inventory', return_value=stocks):
            self.assertEqual(self.ui._limit_planned_food_batch('pineapple_juice', 7), 5)
        self.quantity_setter.assert_called_once_with(self.ui, 5)

    def test_recipe_capacity_replaces_the_old_seven_round_cap(self):
        for product, expected in (('latte', 12), ('fo_tiao', 8), ('pineapple_juice', 5)):
            with self.subTest(product=product):
                stocks = self.inventory(product)
                self.quantity_setter.reset_mock()
                with patch('module.island.manufacture_selector.read_selected_recipe_inventory', return_value=stocks):
                    actual = self.ui._limit_planned_food_batch(product, 30)
                self.assertEqual(actual, expected)
                self.quantity_setter.assert_called_once_with(self.ui, expected)

    def test_target_stock_and_pending_jobs_still_limit_a_batch_above_seven(self):
        stocks = self.inventory('latte', product=4)
        self.ui.config.values[f'{CONFIG_PREFIX}.PlannerTargets'] = '{"3007":17}'
        self.ui.post_check_meal = {'latte': 1}
        self.ui._planned_food_dispatched = {'latte': 2}
        with patch('module.island.manufacture_selector.read_selected_recipe_inventory', return_value=stocks):
            self.assertEqual(self.ui._limit_planned_food_batch('latte', 30), 10)
        self.quantity_setter.assert_called_once_with(self.ui, 10)

    def test_explicit_material_protection_limits_the_new_twelve_round_batch(self):
        stocks = self.inventory('latte')
        stocks[2603]['stock'] = 25
        self.ui._planner_protection = {'milk': 5}
        with patch('module.island.manufacture_selector.read_selected_recipe_inventory', return_value=stocks):
            self.assertEqual(self.ui._limit_planned_food_batch('latte', 30), 10)
        self.quantity_setter.assert_called_once_with(self.ui, 10)
        self.assertEqual(self.ui.warehouse_counts['milk'], 25)

    def test_custom_meal_reserve_remains_effective_above_seven(self):
        stocks = self.inventory('wake_up_call')
        stocks[3006]['stock'] = 20
        self.ui._planner_protection = {'cheese': 3}
        self.ui._reserved_targets = {'cheese': 11}
        self.ui.meal_compositions = {'wake_up_call': {'required': ['iced_coffee', 'cheese'], 'numbers': [1, 1]}}
        with patch('module.island.manufacture_selector.read_selected_recipe_inventory', return_value=stocks):
            self.assertEqual(self.ui._limit_planned_food_batch('wake_up_call', 30), 9)
        self.quantity_setter.assert_called_once_with(self.ui, 9)
        self.ui._sync_planned_food_materials('wake_up_call', 9)
        self.assertEqual(self.ui.warehouse_counts['cheese'], 11)
        self.assertEqual(self.ui._planned_food_dispatched['wake_up_call'], 9)

    def test_filled_target_does_not_set_a_zero_quantity_or_start_more_food(self):
        stocks = self.inventory('latte', product=8)
        self.ui.config.values[f'{CONFIG_PREFIX}.PlannerTargets'] = '{"3007":12}'
        self.ui.post_check_meal = {'latte': 4}
        with patch('module.island.manufacture_selector.read_selected_recipe_inventory', return_value=stocks):
            self.assertEqual(self.ui._limit_planned_food_batch('latte', 30), 0)
        self.quantity_setter.assert_not_called()

    def test_custom_meal_material_reserve_is_preserved_and_actual_deduction_syncs_once(self):
        stocks = self.inventory('wake_up_call')
        stocks[3006]['stock'] = 10
        self.ui._planner_protection = {'cheese': 3}
        self.ui._reserved_targets = {'cheese': 6}
        self.ui.meal_compositions = {'wake_up_call': {'required': ['iced_coffee', 'cheese'], 'numbers': [1, 1]}}
        with patch('module.island.manufacture_selector.read_selected_recipe_inventory', return_value=stocks):
            self.assertEqual(self.ui._limit_planned_food_batch('wake_up_call', 7), 4)
        self.ui._sync_planned_food_materials('wake_up_call', 4)
        self.assertEqual(self.ui.warehouse_counts['cheese'], 6)
        self.assertEqual(self.ui._planned_food_dispatched['wake_up_call'], 4)
        self.assertEqual(self.ui._planned_food_materials, {})

    def test_actual_milk_ledger_updates_child_constraint_without_double_deduction(self):
        stocks = self.inventory('latte')
        stocks[2603]['stock'] = 7
        self.ui.milk_stock = 999
        self.ui.special_materials = {'milk': 999}
        with patch('module.island.manufacture_selector.read_selected_recipe_inventory', return_value=stocks):
            self.ui._limit_planned_food_batch('latte', 3)
        self.ui._sync_planned_food_materials('latte', 3)
        self.assertEqual(self.ui.milk_stock, 1)
        self.assertEqual(self.ui.special_materials['milk'], 1)

    def test_unknown_material_counter_never_becomes_available_stock(self):
        with patch('module.island.manufacture_selector.read_selected_recipe_inventory', return_value=None):
            with self.assertRaises(GameStuckError):
                self.ui._limit_planned_food_batch('latte', 7)
        self.quantity_setter.assert_not_called()
        self.assertEqual(self.ui.warehouse_counts, {})


if __name__ == '__main__':
    unittest.main()
