"""手工食品共享现货、套餐借用与真实材料扣减，不连接游戏。"""

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from module.island.data import DIC_ISLAND_RECIPE
from module.island.item_ids import LOCAL_TO_ITEM_ID
from module.island.island_shop_base import IslandShopBase
from module.island.production_recipe import recipe_for_local_name
from module.island.round_inventory import IslandRoundInventory


class FoodMaterialTests(unittest.TestCase):
    def setUp(self):
        self.ui = IslandShopBase.__new__(IslandShopBase)
        self.ui.config = SimpleNamespace()
        self.ui.warehouse_counts = {}
        self.ui._reserved_targets = {}
        self.ui.meal_compositions = {}
        self.ui.post_check_meal = {}
        self.ui.chef_unavailable_products = set()
        self.ui.to_post_products = {}
        self.ui.name_to_config = {}
        self.ui.posts = {'POST1': {'status': 'idle'}}
        self.ui.special_materials = {}
        for target in ('select_manufacture_recipe', 'read_selected_recipe_inventory', 'set_manufacture_quantity'):
            guard = patch(f'module.island.manufacture_selector.{target}',
                          side_effect=AssertionError('手工库存裁量不进入生产页'))
            guard.start()
            self.addCleanup(guard.stop)

    def inventory(self, name, product=0):
        recipe = DIC_ISLAND_RECIPE[recipe_for_local_name(name)]
        stocks = {item: 1000 for item in recipe['commission_cost']}
        stocks[LOCAL_TO_ITEM_ID[name]] = product
        self.ui.name_to_config[name] = {'name': name}
        self.ui.island_round = IslandRoundInventory(stocks)
        self.ui._load_round_stock()
        return self.ui.island_round

    def finish(self, name, actual):
        self.ui.finish_food_dispatch('POST1', name, 'finish', actual,
                                     (actual, timedelta(hours=1)), datetime(2026, 11, 7))

    def test_every_raw_ingredient_limits_batches_without_consuming_before_confirmation(self):
        ledger = self.inventory('latte')
        ledger.observe('milk', 7)
        self.assertEqual(self.ui._limit_food_batch('latte', 7), 3)
        self.assertEqual(ledger.get('milk'), 7)

    def test_remaining_manual_demand_is_not_reduced_by_stock_twice(self):
        self.inventory('latte', product=4)
        self.ui.to_post_products = {'latte': 1}
        self.ui.post_check_meal = {'latte': 3}
        self.assertEqual(self.ui._limit_food_batch('latte', 7), 1)

    def test_all_recipes_keep_their_individual_capacity(self):
        for name, expected in (('latte', 12), ('fo_tiao', 8), ('pineapple_juice', 5)):
            with self.subTest(name=name):
                self.inventory(name)
                self.assertEqual(self.ui._limit_food_batch(name, 30), expected)

    def test_remaining_manual_demand_can_exceed_old_seven_round_cap(self):
        self.inventory('latte', product=4)
        self.ui.to_post_products = {'latte': 10}
        self.assertEqual(self.ui._limit_food_batch('latte', 30), 10)

    def test_actual_materials_limit_twelve_round_batch(self):
        ledger = self.inventory('latte')
        ledger.observe('milk', 25)
        self.assertEqual(self.ui._limit_food_batch('latte', 30), 12)
        self.assertEqual(ledger.get('milk'), 25)

    def test_met_manual_material_target_can_be_borrowed_above_seven(self):
        ledger = self.inventory('wake_up_call')
        ledger.observe('cheese', 20)
        self.ui._reserved_targets = {'cheese': 11}
        self.assertEqual(self.ui._limit_food_batch('wake_up_call', 30), 12)
        self.finish('wake_up_call', 12)
        self.assertEqual(ledger.get('cheese'), 8)
        self.assertEqual(ledger.get('wake_up_call'), 0)
        self.assertEqual(ledger.in_production[LOCAL_TO_ITEM_ID['wake_up_call']], 12)

    def test_filled_manual_target_never_starts_another_batch(self):
        ledger = self.inventory('latte', product=8)
        self.ui.to_post_products = {'latte': 0}
        self.assertEqual(self.ui._limit_food_batch('latte', 30), 0)
        self.assertEqual(ledger.in_production, {})

    def test_actual_receipt_deducts_material_once(self):
        ledger = self.inventory('wake_up_call')
        ledger.observe('cheese', 10)
        self.ui._reserved_targets = {'cheese': 6}
        self.assertEqual(self.ui._limit_food_batch('wake_up_call', 7), 7)
        self.finish('wake_up_call', 5)
        self.assertEqual(ledger.get('cheese'), 5)
        self.assertEqual(self.ui.warehouse_counts['cheese'], 5)

    def test_met_manual_target_releases_material_for_meal(self):
        ledger = self.inventory('wake_up_call')
        ledger.observe('cheese', 10)
        self.ui._reserved_targets = {'cheese': 10}
        self.assertEqual(self.ui._limit_food_batch('wake_up_call', 7), 7)

    def test_unmet_manual_material_target_cannot_be_borrowed(self):
        ledger = self.inventory('wake_up_call')
        ledger.observe('cheese', 9)
        self.ui._reserved_targets = {'cheese': 10}
        self.assertEqual(self.ui._limit_food_batch('wake_up_call', 7), 0)

    def test_actual_receipt_updates_old_milk_constraint_without_double_deduction(self):
        ledger = self.inventory('latte')
        ledger.observe('milk', 7)
        self.ui.milk_stock = 999
        self.ui.special_materials = {'milk': 999}
        self.assertEqual(self.ui._limit_food_batch('latte', 3), 3)
        self.finish('latte', 3)
        self.assertEqual(ledger.get('milk'), 1)
        self.assertEqual(self.ui.milk_stock, 1)
        self.assertEqual(self.ui.special_materials['milk'], 1)

    def test_unknown_material_is_not_available_or_replaced_with_zero(self):
        ledger = self.inventory('latte')
        ledger.observe('milk', None)
        self.assertEqual(self.ui._limit_food_batch('latte', 7), 0)
        self.assertIsNone(ledger.get('milk'))
        self.assertEqual(ledger.in_production, {})


if __name__ == '__main__':
    unittest.main()
