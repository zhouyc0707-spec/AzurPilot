"""验证五家餐饮的真实配方上限，不连接设备或读取私人配置。"""

import unittest
from unittest.mock import patch

from module.island.data import DIC_ISLAND_RECIPE, DIC_ISLAND_RESTAURANT_MENU_TO_RECIPE
from module.island.island_season import SEASONAL_ITEMS
from module.island.island_shop_base import IslandShopBase
from module.island.item_ids import ITEM_ID_TO_LOCAL
from module.island.planned_dispatch import recipe_for_local_name
from module.island.production_planner import CONFIG_PREFIX
from tests.test_island_production_planner import MemoryConfig


class FoodRecipeLimitsTests(unittest.TestCase):
    def setUp(self):
        # 上限依赖官方配方，不要求店铺构造、商品视觉资源或规划器初始化。
        self.ui = IslandShopBase.__new__(IslandShopBase)

    def test_all_five_restaurant_catalogs_use_each_official_recipe_limit(self):
        self.assertEqual(set(DIC_ISLAND_RESTAURANT_MENU_TO_RECIPE), {601, 602, 603, 604, 901})
        for place, menu in DIC_ISLAND_RESTAURANT_MENU_TO_RECIPE.items():
            for item, recipe_id in menu.items():
                with self.subTest(place=place, item=item, recipe_id=recipe_id):
                    product = ITEM_ID_TO_LOCAL[item]
                    recipe = DIC_ISLAND_RECIPE[recipe_id]
                    self.assertIn(item, recipe['commission_product'])
                    self.assertEqual(self.ui.get_product_production_limit(product), recipe['production_limit'])
        self.assertFalse(hasattr(self.ui, 'name_to_config'))

    def test_previous_and_current_season_foods_keep_their_five_round_limit(self):
        for season, groups in SEASONAL_ITEMS.items():
            for group in ('restaurant', 'teahouse'):
                for product in groups.get(group, []):
                    with self.subTest(season=season, group=group, product=product):
                        recipe_id = recipe_for_local_name(product)
                        self.assertEqual(DIC_ISLAND_RECIPE[recipe_id]['production_limit'], 5)
                        self.assertEqual(self.ui.get_product_production_limit(product), 5)

    def test_regular_food_and_buddhas_temptation_have_distinct_limits(self):
        self.assertEqual(self.ui.get_product_production_limit('cheese'), 12)
        self.assertEqual(self.ui.get_product_production_limit('omelette'), 12)
        self.assertEqual(self.ui.get_product_production_limit('wake_up_call'), 12)
        self.assertEqual(self.ui.get_product_production_limit('fo_tiao'), 8)

    def test_unknown_product_retains_the_conservative_seven_round_limit(self):
        self.assertEqual(IslandShopBase.UNKNOWN_PRODUCT_PRODUCE_LIMIT, 7)
        self.assertEqual(self.ui.get_product_production_limit('unregistered_food'), 7)

    def test_disabled_planner_still_caps_batches_without_reading_stocks(self):
        self.ui.config = MemoryConfig(**{f'{CONFIG_PREFIX}.Enabled': False})
        with patch('module.island.manufacture_selector.select_manufacture_recipe') as selector, \
                patch('module.island.manufacture_selector.read_selected_recipe_inventory') as inventory, \
                patch('module.island.manufacture_selector.set_manufacture_quantity') as quantity, \
                patch('module.island.production_planner.load_planner_targets') as targets:
            for product, expected in (('latte', 12), ('fo_tiao', 8), ('pineapple_juice', 5)):
                with self.subTest(product=product):
                    self.assertEqual(self.ui._limit_planned_food_batch(product, 30), expected)
                    self.assertEqual(self.ui._limit_planned_food_batch(product, 3), 3)
            selector.assert_not_called()
            inventory.assert_not_called()
            quantity.assert_not_called()
            targets.assert_not_called()


if __name__ == '__main__':
    unittest.main()
