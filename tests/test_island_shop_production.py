"""用假 UI 验证店铺共用排产流程，不连接配置文件或游戏。"""

import unittest
from collections import Counter
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.exception import GameBugError
from module.island.island import Island
from module.island.island_grill import IslandGrill
from module.island.island_juu_coffee import IslandJuuCoffee
from module.island.island_juu_eatery import IslandJuuEatery
from module.island.island_restaurant import IslandRestaurant
from module.island.island_season import SeasonConfig
from module.island.island_teahouse import IslandTeahouse


NOW = datetime(2026, 9, 19, 12)


class ShopUI:
    """保留真实配方和排产，只模拟岗位状态、库存读取及下单结果。"""

    def __init__(self, shop_class, products=(), stock=None, *, seasonal=False,
                 season='spring', away=None, working=None):
        config = SimpleNamespace(
            IslandRestaurant_ChefFilter='WorkerJuu',
            IslandTeahouse_ChefFilter='WorkerJuu',
            IslandGrill_ChefFilter='WorkerJuu',
            IslandJuuCoffee_ChefFilter='WorkerJuu',
            IslandJuuCoffee_Friedrich=False,
            IslandJuuEatery_ChefFilter='WorkerJuu',
            IslandRestaurant_DoubleBambooShoots=seasonal,
            IslandTeahouse_Seasonal=seasonal,
            cross_get=Mock(return_value=season),
            task_delay=Mock(),
        )

        def init_island(shop, config, device=None, task=None):
            shop.config = config
            shop.device = device
            shop.unavailable_characters = set()

        with patch.object(Island, '__init__', init_island), patch(
            'module.island.island_shop_base.get_global_season_config',
            side_effect=SeasonConfig,
        ):
            self.shop = shop_class(config)
        shop = self.shop
        shop.post_products = list(products)
        setattr(config, shop.config_away_cook, away)
        self.stock = dict(stock or {})
        self.working = dict(working or {})
        self.orders = []
        self.rejected = set()
        self.actual_limit = None
        for name in ('goto_postmanage', 'post_manage_mode', 'post_close', 'post_manage_swipe'):
            setattr(shop, name, Mock())
        shop.get_warehouse_counts = self.read_stock
        shop.post_check = self.check_post
        shop.post_produce = self.produce

    def read_stock(self):
        self.shop.warehouse_counts = dict(self.stock)
        if isinstance(self.shop, IslandTeahouse):
            self.shop.fresh_honey = self.stock.get('fresh_honey', 0)
        return self.shop.warehouse_counts

    def check_post(self, post_id, time_var_name):
        shop = self.shop
        if post_id in self.working:
            product, number = self.working[post_id]
            shop.posts[post_id]['status'] = 'working'
            shop.post_check_meal[product] = shop.post_check_meal.get(product, 0) + number
            setattr(shop, time_var_name, NOW + timedelta(hours=2))
        else:
            shop.posts[post_id]['status'] = 'idle'

    def produce(self, post_id, product, number, time_var_name, product2=None):
        if product in self.rejected:
            return 0
        number = min(number, self.shop.get_product_production_limit(product))
        if self.actual_limit is not None:
            number = min(number, self.actual_limit)
        self.orders.append((product, number, product2))
        self.shop.posts[post_id]['status'] = 'working'
        self.shop.deduct_materials(product, number)
        setattr(self.shop, time_var_name, NOW + timedelta(hours=1))
        return number

    def run(self):
        with patch('module.island.island_shop_base.current_time', return_value=NOW):
            self.shop.run()


class IslandShopProductionTests(unittest.TestCase):
    def setUp(self):
        self.log_patch = patch('module.island.island_shop_base.logger')
        self.log_patch.start()
        self.addCleanup(self.log_patch.stop)

    def test_restaurant_replenishes_material_consumed_by_meal(self):
        ui = ShopUI(IslandRestaurant, [('hearty_meal', 7), ('tofu', 10)],
                    {'tofu': 10, 'omurice': 10})
        ui.run()
        self.assertEqual(ui.orders, [('hearty_meal', 7, None), ('tofu', 7, None)])
        self.assertEqual(ui.shop.warehouse_counts['tofu'], 3)

    def test_teahouse_replenishes_material_consumed_by_meal(self):
        ui = ShopUI(IslandTeahouse, [('floral_fruity', 7), ('apple_juice', 10)],
                    {'lavender_tea': 10, 'apple_juice': 10})
        ui.run()
        self.assertEqual(ui.orders, [('floral_fruity', 7, None), ('apple_juice', 7, None)])
        self.assertEqual(ui.shop.warehouse_counts['apple_juice'], 3)

    def test_restaurant_replenishes_special_material_after_deduction(self):
        ui = ShopUI(IslandRestaurant, [('tofu_meat', 3), ('tofu', 10)], {'tofu': 10})
        ui.run()
        self.assertEqual(ui.orders, [('tofu_meat', 3, None), ('tofu', 6, None)])

    def test_seasonal_production_precedes_base_demands(self):
        cases = [
            (IslandRestaurant, 'tofu', 'double_bamboo_shoots', 'spring'),
            (IslandRestaurant, 'tofu', 'matsutake_chicken_soup', 'autumn'),
            (IslandTeahouse, 'apple_juice', 'spring_flower_tea', 'spring'),
            (IslandTeahouse, 'apple_juice', 'chrysanthemum_tea', 'autumn'),
        ]
        for cls, basic, priority, season in cases:
            with self.subTest(shop=cls.__name__, season=season):
                ui = ShopUI(cls, [(basic, 3)], seasonal=True, season=season)
                ui.run()
                self.assertEqual(ui.orders, [(priority, 5, None), (basic, 3, None)])

    def test_restaurant_seasonal_extra_batch_preserves_quantity_rule(self):
        ui = ShopUI(IslandRestaurant, [('double_bamboo_shoots', 3)], seasonal=True)
        ui.run()
        self.assertEqual(ui.orders, [('double_bamboo_shoots', 5, None),
                                     ('double_bamboo_shoots', 3, None)])

    def test_teahouse_seasonal_batch_counts_toward_base_target(self):
        ui = ShopUI(IslandTeahouse, [('spring_flower_tea', 5), ('apple_juice', 3)],
                    seasonal=True)
        ui.run()
        self.assertEqual(ui.orders, [('spring_flower_tea', 5, None), ('apple_juice', 3, None)])

    def test_partial_seasonal_order_tracks_actual_quantity(self):
        ui = ShopUI(IslandTeahouse, [('spring_flower_tea', 10)], seasonal=True,
                    working={'ISLAND_TEAHOUSE_POST1': ('spring_flower_tea', 4)})
        ui.actual_limit = 3
        ui.run()
        self.assertEqual(ui.orders, [('spring_flower_tea', 3, None)])
        self.assertEqual(ui.shop.to_post_products, {'spring_flower_tea': 3})
        self.assertNotIn('spring_flower_tea', ui.shop.warehouse_counts)

    def test_seasonal_special_food_does_not_fill_remaining_posts(self):
        for cls in (IslandRestaurant, IslandTeahouse):
            with self.subTest(shop=cls.__name__):
                ui = ShopUI(cls, seasonal=True)
                ui.run()
                self.assertEqual(len(ui.orders), 1)
                self.assertEqual(len(ui.shop.get_idle_posts()), 1)

    def test_seasonal_then_away_cook_fills_remaining_post(self):
        for cls, away in ((IslandRestaurant, 'tofu'), (IslandTeahouse, 'apple_juice')):
            with self.subTest(shop=cls.__name__):
                ui = ShopUI(cls, seasonal=True, away=away)
                ui.run()
                self.assertEqual(len(ui.orders), 2)
                self.assertEqual(ui.orders[-1], (away, 12, None))

    def test_run_resets_unavailable_characters_and_products(self):
        for cls, product in ((IslandRestaurant, 'tofu'), (IslandTeahouse, 'apple_juice')):
            with self.subTest(shop=cls.__name__):
                ui = ShopUI(cls, [(product, 1)])
                ui.shop.chef_unavailable_products.add(product)
                ui.shop.unavailable_characters.add('WorkerJuu')
                ui.run()
                self.assertEqual(ui.orders, [(product, 1, None)])
                self.assertEqual(ui.shop.unavailable_characters, set())

    def test_repeated_run_counts_existing_work_once(self):
        ui = ShopUI(IslandRestaurant, [('tofu', 5)],
                    working={'ISLAND_RESTAURANT_POST1': ('tofu', 3)})
        ui.run()
        self.assertEqual(ui.orders, [('tofu', 2, None)])
        ui.orders.clear()
        ui.run()
        self.assertEqual(ui.orders, [('tofu', 2, None)])
        self.assertEqual(ui.shop.post_check_meal, {'tofu': 3})

    def test_existing_work_cannot_be_consumed_as_meal_material(self):
        ui = ShopUI(IslandRestaurant, [('hearty_meal', 7)], {'omurice': 7},
                    working={'ISLAND_RESTAURANT_POST1': ('tofu', 7)})
        ui.run()
        self.assertEqual(ui.orders, [])

    def test_strict_retry_preserves_all_previously_skipped_products(self):
        for cls, names in ((IslandRestaurant, ('tofu', 'omurice', 'salad')),
                           (IslandTeahouse, ('apple_juice', 'banana_mango', 'lavender_tea'))):
            with self.subTest(shop=cls.__name__):
                ui = ShopUI(cls, [(name, 1) for name in names])
                schedule = ui.shop.schedule_production
                attempts = Counter()

                def stalled_schedule():
                    # 模拟排产无进展且未标记角色不可用，触发严格扫描的兜底路径。
                    product = next(iter(ui.shop.to_post_products))
                    attempts[product] += 1
                    if product == names[-1]:
                        schedule()

                ui.shop.schedule_production = stalled_schedule
                ui.run()
                self.assertEqual(ui.orders, [(names[-1], 1, None)])
                self.assertEqual(attempts[names[0]], 3)
                self.assertEqual(attempts[names[1]], 2)

    def test_other_shops_keep_base_then_away_cook_and_delay(self):
        for cls, product in ((IslandGrill, 'roasted_skewer'),
                             (IslandJuuCoffee, 'iced_coffee'),
                             (IslandJuuEatery, 'apple_pie')):
            with self.subTest(shop=cls.__name__):
                ui = ShopUI(cls, [(product, 3)], away=product)
                ui.run()
                self.assertEqual(ui.orders, [(product, 3, None), (product, 12, None)])
                self.assertEqual(ui.shop.config.task_delay.call_args.kwargs['target'],
                                 [NOW + timedelta(hours=1), NOW + timedelta(hours=1),
                                  NOW + timedelta(hours=6)])

    def test_full_posts_skip_warehouse_and_preserve_delay(self):
        ui = ShopUI(IslandRestaurant, working={
            'ISLAND_RESTAURANT_POST1': ('tofu', 7),
            'ISLAND_RESTAURANT_POST2': ('omurice', 7),
        })
        ui.shop.get_warehouse_counts = Mock(side_effect=AssertionError('满岗不应访问仓库'))
        ui.run()
        self.assertEqual(ui.orders, [])
        self.assertEqual(ui.shop.config.task_delay.call_args.kwargs['target'],
                         [NOW + timedelta(hours=2), NOW + timedelta(hours=2),
                          NOW + timedelta(hours=6)])

    def test_manual_demand_above_seven_uses_twelve_then_the_remaining_need(self):
        for cls, product in ((IslandRestaurant, 'tofu'), (IslandTeahouse, 'apple_juice'),
                             (IslandGrill, 'roasted_skewer'), (IslandJuuCoffee, 'iced_coffee'),
                             (IslandJuuEatery, 'apple_pie')):
            with self.subTest(shop=cls.__name__):
                ui = ShopUI(cls, [(product, 20)])
                ui.run()
                self.assertEqual(ui.orders, [(product, 12, None), (product, 8, None)])
                self.assertEqual(ui.shop.to_post_products, {})

    def test_recipe_limits_include_ordinary_buddhas_temptation_and_seasonal_food(self):
        ui = ShopUI(IslandRestaurant)
        for product, limit in (('tofu', 12), ('fo_tiao', 8), ('double_bamboo_shoots', 5)):
            with self.subTest(product=product):
                self.assertEqual(ui.shop.get_product_production_limit(product), limit)
                self.assertEqual(ui.shop.get_max_producible(product, 99), limit)

    def test_same_product_slots_and_existing_work_do_not_duplicate_large_dispatch(self):
        ui = ShopUI(IslandRestaurant, [('tofu', 17), ('tofu', 17)], {'tofu': 5},
                    working={'ISLAND_RESTAURANT_POST1': ('tofu', 2)})
        ui.run()
        self.assertEqual(ui.orders, [('tofu', 10, None)])
        self.assertEqual(ui.shop.post_check_meal, {'tofu': 2})
        self.assertEqual(ui.shop.to_post_products, {})

    def test_partial_large_dispatch_uses_actual_quantity_without_inflating_progress(self):
        ui = ShopUI(IslandRestaurant, [('tofu', 20)])
        ui.actual_limit = 9
        ui.run()
        self.assertEqual(ui.orders, [('tofu', 9, None), ('tofu', 9, None)])
        self.assertEqual(ui.shop.to_post_products, {'tofu': 2})

    def test_large_meal_dispatch_replenishes_the_material_actually_consumed(self):
        ui = ShopUI(IslandRestaurant, [('hearty_meal', 12), ('tofu', 20)],
                    {'tofu': 20, 'omurice': 20})
        ui.run()
        self.assertEqual(ui.orders, [('hearty_meal', 12, None), ('tofu', 12, None)])
        self.assertEqual(ui.shop.warehouse_counts['tofu'], 8)
        self.assertEqual(ui.shop.warehouse_counts['omurice'], 8)

    def test_away_cook_schedule_fills_each_idle_post_at_its_recipe_limit(self):
        for cls, product, limit in ((IslandRestaurant, 'tofu', 12),
                                    (IslandTeahouse, 'spring_flower_tea', 5)):
            with self.subTest(shop=cls.__name__):
                ui = ShopUI(cls, away=product, seasonal=(limit == 5))
                for post in ui.shop.posts.values():
                    post['status'] = 'idle'
                ui.shop.process_away_cook()
                ui.shop.schedule_production()
                self.assertEqual(ui.orders, [(product, limit, None), (product, limit, None)])

    def test_seasonal_priority_counts_against_target_across_both_posts(self):
        ui = ShopUI(IslandTeahouse, [('spring_flower_tea', 8)], seasonal=True)
        ui.run()
        self.assertEqual(ui.orders, [('spring_flower_tea', 5, None),
                                     ('spring_flower_tea', 3, None)])
        self.assertEqual(ui.shop.to_post_products, {})

    def test_large_batches_preserve_milk_and_honey_constraints_and_deduction(self):
        cases = ((IslandJuuCoffee, 'cheese', 'milk', 80),
                 (IslandTeahouse, 'honey_lemon', 'fresh_honey', 10))
        for cls, product, material, stock in cases:
            with self.subTest(shop=cls.__name__):
                ui = ShopUI(cls, [(product, 20)], {material: stock})
                if isinstance(ui.shop, IslandJuuCoffee):
                    ui.shop.milk_stock = stock
                    ui.shop.special_materials['milk'] = stock
                ui.run()
                self.assertEqual(ui.orders, [(product, 10, None)])
                self.assertEqual(ui.shop.warehouse_counts[material], 0)

    def test_island_error_still_raises_after_setting_delay(self):
        ui = ShopUI(IslandTeahouse)
        ui.shop.goto_postmanage.side_effect = lambda: setattr(ui.shop, 'island_error', True)
        with self.assertRaises(GameBugError):
            ui.run()
        ui.shop.config.task_delay.assert_called_once()


if __name__ == '__main__':
    unittest.main()
