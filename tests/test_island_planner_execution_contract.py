"""上游规划输出通过产品名称连接手工执行，避免冬季编号串用。"""

import unittest

from module.island_planner import settings_map
from module.island_planner.data import DIC_ISLAND_ITEM as PLANNER_ITEMS
from module.island.data import DIC_ISLAND_ITEM as EXECUTION_ITEMS
from module.island.item_ids import LOCAL_TO_ITEM_ID


class PlannerExecutionContractTests(unittest.TestCase):
    def test_planned_product_names_resolve_to_same_execution_item(self):
        products = dict(settings_map.DISH_ITEM_IDS)
        for items in settings_map.CROP_ITEM_IDS.values():
            products.update(items)
        for items in settings_map.PRODUCT_ITEM_IDS.values():
            products.update(items)
        for line in settings_map.MANUFACTURE_PRODUCTS.values():
            products.update(line['products'])

        for name, item_id in products.items():
            with self.subTest(product=name):
                self.assertIn(name, LOCAL_TO_ITEM_ID)
                self.assertEqual(
                    PLANNER_ITEMS[item_id]['name']['cn'],
                    EXECUTION_ITEMS[LOCAL_TO_ITEM_ID[name]]['name']['cn'],
                )

    def test_legacy_winter_extension_does_not_override_upstream_items(self):
        self.assertEqual(PLANNER_ITEMS[4049]['name']['cn'], '青枣')
        self.assertEqual(EXECUTION_ITEMS[4049]['name']['cn'], '冬菇炖鸡')
        self.assertEqual(settings_map.CROP_ITEM_IDS['orchard']['green_jujube'], 4049)
        self.assertEqual(LOCAL_TO_ITEM_ID['green_jujube'], 2024)
        self.assertEqual(settings_map.DISH_ITEM_IDS['shiitake_chicken_soup'], 4051)
        self.assertEqual(LOCAL_TO_ITEM_ID['shiitake_chicken_soup'], 4049)

    def test_winter_bouquet_materials_match_by_name_across_data_versions(self):
        from module.island.data import DIC_ISLAND_RECIPE as execution_recipes
        from module.island_planner.data import DIC_ISLAND_RECIPE as planner_recipes

        def costs_by_name(recipe, items):
            return {items[item]['name']['cn']: count
                    for item, count in recipe['commission_cost'].items()}

        expected = {'梅花': 1, '水仙花': 4}
        self.assertEqual(costs_by_name(execution_recipes[9900030], EXECUTION_ITEMS), expected)
        self.assertEqual(costs_by_name(planner_recipes[9900032], PLANNER_ITEMS), expected)


if __name__ == '__main__':
    unittest.main()
