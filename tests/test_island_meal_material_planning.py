"""用真实排产流程验证套餐补料、库存保留与收获前后边界，不连接游戏。"""

import unittest
from unittest.mock import patch

from module.island.island_restaurant import IslandRestaurant
from tests.test_island_shop_production import ShopUI


class IslandMealMaterialPlanningTests(unittest.TestCase):
    def setUp(self):
        for target in (
            'module.island.island_shop_base.logger',
            'module.island.island_restaurant.logger',
        ):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
    def restaurant(self, *, meat=22, soup=0, working=None):
        return ShopUI(
            IslandRestaurant,
            [('tofu_meat', 14), ('tofu_combo', 14)],
            {'tofu': 100, 'tofu_meat': meat, 'cabbage_tofu': soup},
            working=working,
        )

    def test_one_idle_post_supplies_missing_soup_instead_of_available_meat(self):
        for meat in (14, 22, 27):
            with self.subTest(meat=meat):
                ui = self.restaurant(
                    meat=meat,
                    working={'ISLAND_RESTAURANT_POST1': ('salad', 1)},
                )
                ui.run()
                self.assertEqual(ui.orders, [('cabbage_tofu', 12, None)])
                self.assertEqual(ui.shop.to_post_products,
                                 {'tofu_combo': 14, 'cabbage_tofu': 2})
                self.assertEqual(ui.shop.warehouse_counts['tofu_meat'], meat)

    def test_existing_production_covers_demand_but_is_not_usable_inventory(self):
        ui = self.restaurant(
            working={'ISLAND_RESTAURANT_POST1': ('cabbage_tofu', 14)},
        )
        ui.run()
        self.assertEqual(ui.orders, [])
        self.assertEqual(ui.shop.warehouse_counts['cabbage_tofu'], 0)
        self.assertEqual(ui.shop.warehouse_counts['tofu_meat'], 22)

    def test_newly_dispatched_material_is_not_consumed_or_planned_twice(self):
        ui = self.restaurant()
        # 一岗完成全部缺料后还留一个空岗，确认不会把刚下单当作已收获。
        ui.shop.post_products = [('tofu_meat', 14), ('tofu_combo', 7)]
        ui.run()
        self.assertEqual(ui.orders, [('cabbage_tofu', 7, None)])
        self.assertEqual(ui.shop.warehouse_counts['cabbage_tofu'], 0)
        self.assertEqual(ui.shop.warehouse_counts['tofu_meat'], 22)
        self.assertEqual(len(ui.shop.get_idle_posts()), 1)

    def test_harvested_material_allows_meal_then_restores_its_target(self):
        ui = self.restaurant()
        ui.run()
        self.assertEqual(ui.orders, [
            ('cabbage_tofu', 12, None),
            ('cabbage_tofu', 2, None),
        ])

        # 模拟两岗完成并收获，下一次真实 run 应先做套餐，再补被用掉的目标。
        ui.stock = dict(ui.shop.warehouse_counts)
        ui.stock['cabbage_tofu'] = 14
        ui.orders.clear()
        ui.run()
        self.assertEqual(ui.orders, [
            ('tofu_combo', 12, None),
            ('tofu_meat', 4, None),
        ])
        self.assertEqual(ui.shop.warehouse_counts['tofu_meat'], 10)
        self.assertEqual(ui.shop.warehouse_counts['cabbage_tofu'], 2)

    def test_unmet_earlier_manual_target_keeps_its_priority(self):
        ui = self.restaurant(
            meat=13,
            working={'ISLAND_RESTAURANT_POST1': ('salad', 1)},
        )
        ui.run()
        self.assertEqual(ui.orders, [('tofu_meat', 1, None)])
        self.assertEqual(ui.shop.warehouse_counts['tofu_meat'], 13)

    def test_old_automatic_protection_does_not_take_over_manual_materials(self):
        ui = self.restaurant(soup=14)
        ui.shop._planner_protection = {'tofu_meat': 14}
        ui.run()
        self.assertEqual(ui.orders, [
            ('tofu_combo', 12, None),
            ('tofu_meat', 4, None),
        ])
        self.assertEqual(ui.shop.warehouse_counts['tofu_meat'], 10)
        self.assertEqual(ui.shop.warehouse_counts['cabbage_tofu'], 2)

    def test_missing_material_replenishment_uses_only_manual_target(self):
        ui = self.restaurant(
            soup=7,
            working={'ISLAND_RESTAURANT_POST1': ('salad', 1)},
        )
        ui.run()
        self.assertEqual(ui.orders, [('cabbage_tofu', 7, None)])
        self.assertEqual(ui.shop.warehouse_counts['cabbage_tofu'], 7)

    def test_stock_and_pending_outputs_reduce_replenishment_but_only_stock_can_be_used(self):
        ui = self.restaurant(
            soup=5,
            working={'ISLAND_RESTAURANT_POST1': ('cabbage_tofu', 7)},
        )
        ui.read_stock()
        ui.check_post('ISLAND_RESTAURANT_POST1', 'time_restaurant1')
        shop = ui.shop
        shop._reserved_targets = {'tofu_meat': 14}
        shop.current_totals = shop._rebuild_current_totals({})
        plan = shop.process_meal_requirements({'tofu_combo': 14})
        self.assertEqual(plan, {'tofu_combo': 14, 'cabbage_tofu': 2})
        self.assertEqual(shop.get_max_producible('tofu_combo', 14), 5)
        self.assertEqual(shop.warehouse_counts['cabbage_tofu'], 5)


if __name__ == '__main__':
    unittest.main()
