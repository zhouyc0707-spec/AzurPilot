"""共享仓库账下的食品与制造快派遣，不连接配置文件或游戏。"""

import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.base.button import Button
from module.base.utils import load_image
from module.exception import GameStuckError
from module.island.assets import ISLAND_WORK_COMPLETE, POST_ADD_ORDER
from module.island.data import DIC_ISLAND_RECIPE
from module.island.island_juu_coffee import IslandJuuCoffee
from module.island.island_juu_eatery import IslandJuuEatery
from module.island.island_manufacture import IslandManufacture
from module.island.island_restaurant import IslandRestaurant
from module.island.island_shop_base import IslandShopBase
from module.island.island_teahouse import IslandTeahouse
from module.island.item_ids import LOCAL_TO_ITEM_ID
from module.island.production_recipe import recipe_for_local_name
from module.island.round_inventory import IslandRoundInventory
from module.island_manufacture.assets import ALAS_RECIPE_AMOUNT
from tests.test_island_food_dispatch import (
    DISPATCH_PRODUCT_NUMBER, MANAGEMENT, PRODUCT, REWARD, UNKNOWN, FakeDevice, FoodUI,
)
from tests.test_island_shop_production import NOW, ShopUI


def recipe_stocks(product, stock=0, batches=100):
    """构造中央仓库已确认的配方现货。"""
    recipe = DIC_ISLAND_RECIPE[recipe_for_local_name(product)]
    stocks = {item: cost * batches for item, cost in recipe['commission_cost'].items()}
    stocks[LOCAL_TO_ITEM_ID[product]] = stock
    return stocks


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


class WarehouseRoundTestCase(unittest.TestCase):
    def setUp(self):
        for target in ('module.island.round_recipe.scan_recipe_product_stocks',
                       'module.island.round_recipe.observe_selected_recipe',
                       'module.island.manufacture_selector.read_selected_recipe_inventory'):
            guard = patch(target, side_effect=AssertionError('生产页不得读取库存'))
            guard.start()
            self.addCleanup(guard.stop)
        for target in ('module.island.manufacture_selector.select_manufacture_recipe',
                       'module.island.manufacture_selector.set_manufacture_quantity'):
            guard = patch(target, side_effect=AssertionError('旧模板不得进入通用慢调量'))
            guard.start()
            self.addCleanup(guard.stop)
        log = patch('module.island.island_shop_base.logger')
        log.start()
        self.addCleanup(log.stop)
        opening = patch('module.island.production_recipe.ProductionRecipeMixin._open_product_page',
                        return_value=True)
        self.opening = opening.start()
        self.addCleanup(opening.stop)


class RoundFoodTests(WarehouseRoundTestCase):
    def ui(self, kind=IslandJuuCoffee, stocks=None, products=(), seasonal=False, season='spring'):
        ui = ShopUI(kind, products=products, seasonal=seasonal, season=season)
        shop = ui.shop
        shop.island_round = IslandRoundInventory(stocks)
        shop.device = Mock()
        shop.warehouse_filter = Mock(side_effect=AssertionError('本轮不得重复进入仓库'))
        shop.post_produce = kind.post_produce.__get__(shop, kind)
        shop.select_product = Mock(return_value=True)
        shop.post_add_one = Mock()
        shop.produce_check = Mock(return_value=False)
        shop.back_to_postmanage_from_dispatch = Mock(return_value=True)
        shop.confirm_food_dispatch = Mock(side_effect=lambda amount, label: ((amount, timedelta(hours=1)), NOW))
        return ui

    def test_iced_coffee_actual_entry_uses_original_fast_selection(self):
        shop = self.ui(stocks={'iced_coffee': 0, 'coffee_bean': 324}).shop
        post = next(iter(shop.posts))
        self.assertEqual(shop.post_produce(post, 'iced_coffee', 14, 'time1'), 12)
        info = shop.name_to_config['iced_coffee']
        shop.select_product.assert_called_once_with(info['selection'], info['selection_check'])
        shop.post_add_one.assert_called_once_with(11)
        self.assertEqual(shop.island_round.get('coffee_bean'), 300)
        self.assertEqual(shop.island_round.get('iced_coffee'), 0)
        self.assertEqual(shop.island_round.in_production[LOCAL_TO_ITEM_ID['iced_coffee']], 12)
        self.assertEqual(shop.posts[post]['status'], 'working')

    def test_zero_or_unknown_stock_stops_before_opening_production(self):
        for stocks in ({'iced_coffee': 0, 'coffee_bean': 0},
                       {'iced_coffee': 0}, {'coffee_bean': 324}):
            with self.subTest(stocks=stocks):
                shop = self.ui(stocks=stocks).shop
                self.assertEqual(shop.post_produce(next(iter(shop.posts)), 'iced_coffee', 12, 'time1'), 0)
                self.assertTrue(shop.island_round.is_blocked('iced_coffee'))
                shop.post_add_one.assert_not_called()
        self.opening.assert_not_called()

    def test_batch_limit_reads_ledger_without_subtracting_finished_stock_twice(self):
        shop = self.ui(IslandJuuEatery, recipe_stocks('corn_cup', stock=7)).shop
        shop.to_post_products = {'corn_cup': 3}
        self.assertEqual(shop._limit_round_food_batch('corn_cup', 10), 3)
        shop.post_add_one.assert_not_called()
        self.opening.assert_not_called()

    def test_known_material_balance_limits_fast_clicks_before_dispatch(self):
        shop = self.ui(stocks={'iced_coffee': 0, 'coffee_bean': 7}).shop
        self.assertEqual(shop.post_produce(next(iter(shop.posts)), 'iced_coffee', 12, 'time1'), 3)
        shop.post_add_one.assert_called_once_with(2)
        self.assertEqual(shop.island_round.get('coffee_bean'), 1)

    def test_failed_primary_uses_fallback_and_deducts_actual_product(self):
        shop = self.ui(stocks={'iced_coffee': 0, 'coffee_bean': 0, 'omelette': 0, 'egg': 20}).shop
        self.assertEqual(shop.post_produce(next(iter(shop.posts)), 'iced_coffee', 2, 'time1',
                                          product2='omelette'), 2)
        shop.post_add_one.assert_called_once_with(1)
        self.assertTrue(shop.island_round.is_blocked('iced_coffee'))
        self.assertEqual(shop.island_round.get('egg'), 18)
        self.assertEqual(shop.island_round.in_production[LOCAL_TO_ITEM_ID['omelette']], 2)

    def test_gray_produce_button_blocks_retries_without_deducting(self):
        shop = self.ui(stocks={'iced_coffee': 0, 'coffee_bean': 324}).shop
        shop.produce_check.return_value = True
        post = next(iter(shop.posts))
        self.assertEqual(shop.post_produce(post, 'iced_coffee', 12, 'time1'), 0)
        self.assertEqual(shop.post_produce(post, 'iced_coffee', 12, 'time1'), 0)
        self.opening.assert_called_once()
        shop.post_add_one.assert_not_called()
        shop.confirm_food_dispatch.assert_not_called()
        self.assertEqual(shop.island_round.get('coffee_bean'), 324)

    def test_unknown_seasonal_food_material_uses_button_and_deducts_known_stock(self):
        cases = (
            (IslandRestaurant, 'double_bamboo_shoots', 'spring', 4015, {4019: 3}, 3),
            (IslandRestaurant, 'shiitake_chicken_soup', 'winter', 4043, {2602: 3}, 3),
            (IslandJuuEatery, 'water_chestnut_cake', 'winter', 4044, {}, 5),
        )
        for kind, product, season, raw, known, planned in cases:
            with self.subTest(product=product):
                shop = self.ui(kind, {product: 0, **known}, season=season).shop
                shop.confirm_food_dispatch.return_value = ((2, timedelta(hours=1)), NOW)
                shop.confirm_food_dispatch.side_effect = None
                post = next(iter(shop.posts))
                self.assertEqual(shop.post_produce(post, product, 5, 'time1'), 2)
                shop.produce_check.assert_called_once()
                shop.confirm_food_dispatch.assert_called_once()
                self.assertEqual(shop.confirm_food_dispatch.call_args.args[0], planned)
                shop.post_add_one.assert_called_once_with(planned - 1)
                for item in known:
                    self.assertEqual(shop.island_round.get(item), 1)
                self.assertIsNone(shop.island_round.get(raw))
                self.assertEqual(shop.island_round.get(product), 0)
                self.assertEqual(shop.island_round.in_production[LOCAL_TO_ITEM_ID[product]], 2)
                self.assertNotIn(raw, shop.round_stock_items())
                self.assertTrue({*known, LOCAL_TO_ITEM_ID[product]}.issubset(shop.round_stock_items()))

    def test_unknown_other_ingredient_still_stops_seasonal_recipe(self):
        for product, season in (('double_bamboo_shoots', 'spring'), ('shiitake_chicken_soup', 'winter')):
            with self.subTest(product=product):
                shop = self.ui(IslandRestaurant, {product: 0}, season=season).shop
                self.assertEqual(shop.post_produce(next(iter(shop.posts)), product, 5, 'time1'), 0)
                shop.produce_check.assert_not_called()
                shop.confirm_food_dispatch.assert_not_called()
        self.opening.assert_not_called()

    def test_gray_button_blocks_unknown_seasonal_food_without_consuming(self):
        cases = (
            (IslandRestaurant, 'double_bamboo_shoots', 'spring', 4015, {4019: 3}),
            (IslandRestaurant, 'shiitake_chicken_soup', 'winter', 4043, {2602: 3}),
            (IslandJuuEatery, 'water_chestnut_cake', 'winter', 4044, {}),
        )
        for kind, product, season, raw, known in cases:
            with self.subTest(product=product):
                shop = self.ui(kind, {product: 0, **known}, season=season).shop
                shop.produce_check.return_value = True
                before = dict(shop.island_round.stocks)
                for _ in range(2):
                    self.assertEqual(shop.post_produce(next(iter(shop.posts)), product, 5, 'time1'), 0)
                shop.produce_check.assert_called_once()
                shop.confirm_food_dispatch.assert_not_called()
                shop.post_add_one.assert_not_called()
                self.assertEqual(shop.island_round.stocks, before)
                self.assertEqual(shop.island_round.in_production, {})
                self.assertIsNone(shop.island_round.get(raw))
                self.assertTrue(shop.island_round.is_blocked(product))

    def test_known_winter_food_material_still_limits_or_stops_production(self):
        for kind, product, raw, other in (
                (IslandRestaurant, 'shiitake_chicken_soup', 4043, {2602: 10}),
                (IslandJuuEatery, 'water_chestnut_cake', 4044, {})):
            for stock, expected in ((0, 0), (5, 2)):
                with self.subTest(product=product, stock=stock):
                    shop = self.ui(kind, {product: 0, raw: stock, **other}, season='winter').shop
                    self.assertEqual(shop.post_produce(next(iter(shop.posts)), product, 5, 'time1'), expected)
                    self.assertEqual(shop.island_round.get(raw), stock - 2 * expected)
                    if expected:
                        shop.post_add_one.assert_called_once_with(1)
                        shop.confirm_food_dispatch.assert_called_once()
                    else:
                        shop.produce_check.assert_not_called()
                        shop.confirm_food_dispatch.assert_not_called()

    def test_generic_fallback_selects_and_sets_quantity_without_reading_stock(self):
        shop = self.ui(stocks=recipe_stocks('iced_coffee')).shop
        shop.name_to_config['iced_coffee'] = {'name': 'iced_coffee', 'generic_recipe': 901002}
        with patch('module.island.manufacture_selector.select_manufacture_recipe', return_value=True) as select, \
                patch('module.island.manufacture_selector.set_manufacture_quantity', return_value=True) as quantity:
            self.assertEqual(shop.post_produce(next(iter(shop.posts)), 'iced_coffee', 3, 'time1'), 3)
        select.assert_called_once_with(shop, 901002)
        quantity.assert_called_once_with(shop, 3)
        shop.select_product.assert_not_called()
        shop.post_add_one.assert_not_called()

    def test_generic_failed_quantity_never_confirms_or_deducts(self):
        shop = self.ui(stocks=recipe_stocks('iced_coffee')).shop
        before = dict(shop.island_round.stocks)
        shop.name_to_config['iced_coffee'] = {'name': 'iced_coffee', 'generic_recipe': 901002}
        with patch('module.island.manufacture_selector.select_manufacture_recipe', return_value=True), \
                patch('module.island.manufacture_selector.set_manufacture_quantity', return_value=False):
            with self.assertRaises(GameStuckError):
                shop.post_produce(next(iter(shop.posts)), 'iced_coffee', 3, 'time1')
        shop.confirm_food_dispatch.assert_not_called()
        self.assertEqual(shop.island_round.stocks, before)

    def test_teahouse_seasonal_fixed_position_keeps_fast_path(self):
        shop = self.ui(IslandTeahouse, recipe_stocks('spring_flower_tea'), seasonal=True).shop
        self.assertEqual(shop.post_produce(next(iter(shop.posts)), 'spring_flower_tea', 12, 'time1'), 5)
        shop.device.click.assert_called_once_with(shop.seasonal_high_priority_drink['selection'])
        shop.select_product.assert_not_called()
        shop.post_add_one.assert_called_once_with(4)

    def test_special_chef_receives_actual_product_without_inventory_scan(self):
        shop = self.ui(stocks=recipe_stocks('wake_up_call')).shop
        shop.special_character = True
        post = next(iter(shop.posts))
        shop.post_produce(post, 'wake_up_call', 2, 'time1')
        self.opening.assert_called_once_with(shop, post, shop.chef_config, 'wake_up_call')

    def test_unknown_meal_does_not_create_prerequisite_demand(self):
        shop = self.ui(IslandRestaurant, {'tofu': 0, 'omurice': 0}, [('hearty_meal', 7)]).shop
        shop._observe_round_targets(next(iter(shop.posts)))
        shop.current_totals = shop._rebuild_current_totals({})
        shop._compute_base_demands()
        self.assertEqual(shop.to_post_products, {})
        self.assertIsNone(shop.island_round.get('hearty_meal'))
        self.opening.assert_not_called()

    def test_manual_run_uses_warehouse_stock_and_dispatches_only_deficit(self):
        ui = self.ui(IslandJuuEatery, recipe_stocks('corn_cup', stock=7), [('corn_cup', 10)])
        ui.run()
        ui.shop.post_add_one.assert_called_once_with(2)
        self.assertEqual(ui.shop.island_round.get('corn_cup'), 7)
        self.assertEqual(ui.shop.island_round.in_production[LOCAL_TO_ITEM_ID['corn_cup']], 3)
        self.opening.assert_called_once()

    def test_two_posts_share_consumption_and_do_not_repeat_full_target(self):
        ui = self.ui(stocks={'iced_coffee': 0, 'coffee_bean': 324}, products=[('iced_coffee', 14)])
        ui.run()
        self.assertEqual([call.args[0] for call in ui.shop.post_add_one.call_args_list], [11, 1])
        self.assertEqual(ui.shop.island_round.get('coffee_bean'), 296)
        self.assertEqual(ui.shop.island_round.in_production[LOCAL_TO_ITEM_ID['iced_coffee']], 14)

    def test_meal_consumption_replenishes_manual_target_once(self):
        stocks = recipe_stocks('tofu')
        stocks.update({'hearty_meal': 0, 'tofu': 10, 'omurice': 10})
        ui = self.ui(IslandRestaurant, stocks, [('hearty_meal', 7), ('tofu', 10)])
        ui.run()
        self.assertEqual([call.args[0] for call in ui.shop.post_add_one.call_args_list], [6, 6])
        self.assertEqual(ui.shop.island_round.get('tofu'), 3)
        self.assertEqual(ui.shop.island_round.get('omurice'), 3)
        self.assertEqual(ui.shop.island_round.in_production[LOCAL_TO_ITEM_ID['tofu']], 7)

    def test_satisfied_target_does_not_open_production_for_inventory(self):
        ui = self.ui(IslandJuuEatery, recipe_stocks('corn_cup', stock=10), [('corn_cup', 10)])
        ui.run()
        self.opening.assert_not_called()
        ui.shop.post_add_one.assert_not_called()

    def test_actual_batch_consumes_once_and_output_stays_in_production(self):
        shop = self.ui(stocks={'cheese': 0, 'milk': 40}).shop
        shop.deduct_materials = Mock(side_effect=AssertionError('不得重复扣旧账'))
        shop.confirm_food_dispatch.return_value = ((3, timedelta(hours=1)), NOW)
        shop.confirm_food_dispatch.side_effect = None
        self.assertEqual(shop.post_produce(next(iter(shop.posts)), 'cheese', 5, 'time1'), 3)
        self.assertEqual(shop.island_round.get('milk'), 16)
        self.assertEqual(shop.milk_stock, 16)
        self.assertEqual(shop.island_round.get('cheese'), 0)
        self.assertEqual(shop.island_round.in_production[LOCAL_TO_ITEM_ID['cheese']], 3)

    def test_special_inventory_readers_only_load_shared_warehouse_values(self):
        for kind in (IslandJuuCoffee, IslandJuuEatery, IslandTeahouse):
            with self.subTest(kind=kind):
                shop = self.ui(kind, {'milk': 9, 'cheese': 3, 'fresh_honey': 11}).shop
                kind.get_warehouse_counts(shop)
                shop.warehouse_filter.assert_not_called()
                if hasattr(shop, 'milk_stock'):
                    self.assertEqual(shop.milk_stock, 9)
                if hasattr(shop, 'fresh_honey'):
                    self.assertEqual(shop.fresh_honey, 11)

    def test_failed_prerequisite_readded_by_meal_is_not_retried(self):
        shop = self.ui(IslandRestaurant, {'tofu': 0, 'soybean': 100}).shop
        shop.chef_unavailable_products.add('tofu')
        shop.to_post_products = {'tofu': 5}
        for post in shop.posts.values():
            post['status'] = 'idle'
        shop.post_produce = Mock()
        shop.schedule_production()
        shop.post_produce.assert_not_called()

    def test_prepare_collects_disabled_post_and_run_reuses_inspection(self):
        ui = self.ui(stocks={'iced_coffee': 100})
        shop = ui.shop
        setattr(shop.config, shop.config_post_number, 1)
        shop.post_check = Mock(side_effect=ui.check_post)
        shop.prepare_round()
        self.assertEqual(shop.post_check.call_count, 2)
        self.assertEqual(len(shop.get_idle_posts()), 1)
        ui.run()
        self.assertEqual(shop.post_check.call_count, 2)


class RoundDispatchQuantityTests(WarehouseRoundTestCase):
    """保留真实确认循环，证明快加之后仍在订单提交前限制数量。"""

    def setUp(self):
        super().setUp()
        self.clock = SimpleNamespace(now=1000.0)
        self.enterContext(patch('module.base.timer.time', side_effect=lambda: self.clock.now))
        self.enterContext(patch('module.island.island_shop_base.current_time', return_value=NOW))

    def shop(self, amount=12, duration='02:00:00'):
        device = FakeDevice(self.clock)
        device.amount = amount
        device.duration = duration
        self.enterContext(patch('module.island.island_shop_base.Digit', side_effect=lambda button, **kwargs:
                                SimpleNamespace(ocr=lambda image: device.amount if button == DISPATCH_PRODUCT_NUMBER
                                                else device.post_amount)))
        self.enterContext(patch('module.island.island_shop_base.Ocr', side_effect=lambda *args, **kwargs:
                                SimpleNamespace(ocr=lambda image: device.duration)))
        self.enterContext(patch('module.island.island_shop_base.Duration', side_effect=lambda *args, **kwargs:
                                SimpleNamespace(ocr=lambda image: device.post_duration)))
        shop = FoodUI(device)
        shop.name_to_config = {'iced_coffee': {'selection': POST_ADD_ORDER, 'selection_check': POST_ADD_ORDER}}
        shop.island_round = IslandRoundInventory({'iced_coffee': 0, 'coffee_bean': 324})
        shop._reserved_targets = {}
        shop.to_post_products = {'iced_coffee': 14}
        return shop, device

    def test_iced_coffee_real_confirm_safely_commits_fast_count(self):
        shop, device = self.shop()
        self.assertEqual(shop.post_produce('POST1', 'iced_coffee', 14, 'finish_time'), 12)
        shop.post_add_one.assert_called_once_with(11)
        self.assertEqual(sum(name == 'POST_ADD_ORDER' for name, _ in device.clicks), 1)
        self.assertEqual(shop.island_round.get('coffee_bean'), 300)
        self.assertEqual(shop.island_round.in_production[LOCAL_TO_ITEM_ID['iced_coffee']], 12)

    def test_unknown_shiitake_partial_dispatch_rechecks_post_and_deducts_actual_chicken(self):
        shop, device = self.shop(amount=2)
        product = 'shiitake_chicken_soup'
        shop.name_to_config = {product: {'selection': POST_ADD_ORDER, 'selection_check': POST_ADD_ORDER}}
        shop.island_round = IslandRoundInventory({product: 0, 2602: 3})
        shop.to_post_products = {product: 5}
        device.post_amount = 2
        self.assertEqual(shop.post_produce('POST1', product, 5, 'finish_time'), 2)
        # 已知鸡肉先将五份需求限制为三份；少产后的最终数量以岗位复检为准。
        shop.post_add_one.assert_called_once_with(2)
        self.assertEqual(sum(name == 'POST_ADD_ORDER' for name, _ in device.clicks), 1)
        shop.post_open.assert_called_once()
        shop.post_close.assert_called_once()
        self.assertEqual(shop.posts['POST1']['status'], 'working')
        self.assertEqual(shop.island_round.get(2602), 1)
        self.assertIsNone(shop.island_round.get(4043))
        self.assertEqual(shop.island_round.get(product), 0)
        self.assertEqual(shop.island_round.in_production[LOCAL_TO_ITEM_ID[product]], 2)

    def test_unknown_or_excess_count_never_submits_an_order(self):
        for amount in (None, 0, 13):
            with self.subTest(amount=amount):
                shop, device = self.shop(amount)
                with self.assertRaises(GameStuckError):
                    shop.post_produce('POST1', 'iced_coffee', 14, 'finish_time')
                self.assertEqual(device.clicks, [])
                self.assertEqual(shop.island_round.get('coffee_bean'), 324)
                self.assertEqual(shop.island_round.in_production, {})

    def test_quantity_requires_consecutive_equal_frames(self):
        shop, device = self.shop()
        samples = []

        def observed_number():
            samples.append(self.clock.now)
            return 11 if len(samples) % 2 else 12

        shop.read_food_dispatch_number = observed_number
        with self.assertRaises(GameStuckError):
            shop.post_produce('POST1', 'iced_coffee', 14, 'finish_time')
        self.assertGreater(len(samples), 3)
        self.assertEqual(device.clicks, [])

    def test_interrupted_page_discards_previous_number_and_preview_samples(self):
        without_order = PRODUCT.copy()
        x1, y1, x2, y2 = POST_ADD_ORDER.area
        without_order[y1:y2, x1:x2] = 0
        interruptions = [(kind, frame) for kind in ('page', 'button', 'popup', 'reward') for frame in (4, 5)]
        for interruption, interrupted_frame in interruptions:
            with self.subTest(interruption=interruption, frame=interrupted_frame):
                shop, device = self.shop()
                frames = []
                preview_frames = []

                def timeline(device):
                    frames.append(len(frames) + 1)
                    if device.clicked_at is not None:
                        return MANAGEMENT
                    if len(frames) == interrupted_frame:
                        return {'page': UNKNOWN, 'button': without_order,
                                'popup': PRODUCT, 'reward': REWARD}[interruption]
                    return PRODUCT

                original_preview = shop.read_food_dispatch_preview

                def read_preview(*args, **kwargs):
                    preview_frames.append(len(frames))
                    return original_preview(*args, **kwargs)

                device.timeline = timeline
                shop.handle_popup_confirm.side_effect = lambda context: (
                    interruption == 'popup' and len(frames) == interrupted_frame)
                shop.read_food_dispatch_preview = read_preview
                self.assertEqual(shop.post_produce('POST1', 'iced_coffee', 14, 'finish_time'), 12)
                # 中断后必须重新首读次数；已有的次数、预览和采样次数都不能沿用。
                self.assertEqual(preview_frames, [6, 7] if interrupted_frame == 4 else [4, 7, 8])
                self.assertEqual(sum(name == 'POST_ADD_ORDER' for name, _ in device.clicks), 1)

    def test_known_safe_count_with_unknown_duration_keeps_post_receipt(self):
        shop, device = self.shop(amount=4, duration='bad')
        self.assertEqual(shop.post_produce('POST1', 'iced_coffee', 14, 'finish_time'), 4)
        self.assertEqual(sum(name == 'POST_ADD_ORDER' for name, _ in device.clicks), 1)
        self.assertEqual(shop.island_round.get('coffee_bean'), 316)
        self.assertEqual(shop.island_round.in_production[LOCAL_TO_ITEM_ID['iced_coffee']], 4)
        shop.post_open.assert_called_once()

    def test_all_servers_keep_safe_dispatch_and_existing_time_receipt(self):
        for server in ('cn', 'en', 'jp', 'tw'):
            with self.subTest(server=server):
                shop, device = self.shop()
                shop.config.SERVER = server
                actual = shop.post_produce('POST1', 'iced_coffee', 14, 'finish_time')
                expected = 12 if server == 'cn' else device.post_amount
                self.assertEqual(actual, expected)
                self.assertEqual(sum(name == 'POST_ADD_ORDER' for name, _ in device.clicks), 1)
                self.assertEqual(shop.island_round.get('coffee_bean'), 324 - 2 * expected)


class RoundDispatchServerOCRTests(unittest.TestCase):
    def test_existing_server_number_regions_recognize_their_real_assets(self):
        """EN 使用自己的较宽区域，JP/TW 沿用现有资源声明的 CN 数字区。"""
        from module.ocr.al_ocr import AlOcr, OcrSettings

        settings = OcrSettings(backend='onnx', device='cpu',
                               allow_vendor_execution_providers=False, model_version='alocr_cn_v3')
        model = AlOcr(name='cn', settings=settings)
        self.assertEqual(DISPATCH_PRODUCT_NUMBER.raw_area, ALAS_RECIPE_AMOUNT.raw_area)
        with patch('module.ocr.ocr.OCR_MODEL', SimpleNamespace(cnocr=model)):
            for server in ('cn', 'en', 'jp', 'tw'):
                with self.subTest(server=server):
                    region = ALAS_RECIPE_AMOUNT.raw_area[server]
                    button = Button(area=region, color=(), button=region, name='DISPATCH_PRODUCT_NUMBER')
                    shop = IslandShopBase.__new__(IslandShopBase)
                    shop.config = SimpleNamespace(SERVER=server)
                    shop.device = SimpleNamespace(image=load_image(ALAS_RECIPE_AMOUNT.raw_file[server]))
                    with patch('module.island.island_shop_base.DISPATCH_PRODUCT_NUMBER', button):
                        self.assertEqual(shop.read_food_dispatch_number(), 1)


class RoundManufactureTests(WarehouseRoundTestCase):
    def factory(self, product='file_cabinet', stocks=None):
        factory = IslandManufacture.__new__(IslandManufacture)
        factory.config = SimpleNamespace()
        factory.island_round = IslandRoundInventory(recipe_stocks(product, batches=4) if stocks is None else stocks)
        factory._reserved_targets = {}
        factory.chef_unavailable_products = set()
        factory._unknown_working_categories = set()
        factory.name_to_config = {product: {
            'name': product, 'selection': Mock(area=(1, 2, 3, 4)),
            'selection_check': Mock(area=(1, 2, 3, 4))}}
        factory.special_materials = {}
        factory._post_time_vars = {'ISLAND_WOOD_PROCESSING_POST1': 'time1'}
        factory._prepare_recipe_page = Mock(return_value=True)
        factory.back_to_postmanage_from_dispatch = Mock(return_value=True)
        factory.select_product = Mock(return_value=True)
        factory.post_add_one = Mock()
        factory.produce_check = Mock(return_value=False)
        factory.confirm_food_dispatch = Mock(return_value=((2, timedelta(hours=1)), NOW))
        factory.posts = {'ISLAND_WOOD_PROCESSING_POST1': {'status': 'idle'}}
        factory.to_post_products = {}
        return factory

    def test_unknown_legacy_manufacture_materials_use_button_without_inventing_stock(self):
        for product, materials in (
                ('peanut_oil', (4003,)), ('shepherd_purse', (4016,)),
                ('plum_blossom_sachet', (4045,)), ('winter_bouquet', (4045, 4046))):
            with self.subTest(product=product):
                factory = self.factory(product, {product: 0})
                item = factory.name_to_config[product]
                self.assertEqual(factory.select_product_with_material_check('ISLAND_WOOD_PROCESSING_POST1', [item]), item)
                factory.produce_check.assert_called_once()
                factory.confirm_food_dispatch.assert_called_once()
                factory.post_add_one.assert_called_once_with(4)
                for raw in materials:
                    self.assertIsNone(factory.island_round.get(raw))
                    self.assertNotIn(raw, factory.round_stock_items())
                self.assertIn(LOCAL_TO_ITEM_ID[product], factory.round_stock_items())
                self.assertEqual(factory.island_round.get(product), 0)
                self.assertEqual(factory.island_round.in_production[LOCAL_TO_ITEM_ID[product]], 2)

    def test_gray_button_blocks_unknown_legacy_manufacture_material_once(self):
        for product in ('peanut_oil', 'shepherd_purse', 'plum_blossom_sachet', 'winter_bouquet'):
            with self.subTest(product=product):
                factory = self.factory(product, {product: 0})
                factory.produce_check.return_value = True
                item = factory.name_to_config[product]
                for _ in range(2):
                    self.assertIsNone(factory.select_product_with_material_check('ISLAND_WOOD_PROCESSING_POST1', [item]))
                factory._prepare_recipe_page.assert_called_once()
                factory.confirm_food_dispatch.assert_not_called()
                factory.post_add_one.assert_not_called()
                self.assertEqual(factory.island_round.stocks, {LOCAL_TO_ITEM_ID[product]: 0})
                self.assertEqual(factory.island_round.in_production, {})
                self.assertTrue(factory.island_round.is_blocked(product))

    def test_known_legacy_material_still_limits_and_deducts_actual_manufacture_cost(self):
        for product, raw, cost in (('peanut_oil', 4003, 8), ('shepherd_purse', 4016, 2),
                                   ('plum_blossom_sachet', 4045, 3),
                                   ('winter_bouquet', 4045, 1), ('winter_bouquet', 4046, 4)):
            with self.subTest(product=product, raw=raw):
                factory = self.factory(product, {product: 0, raw: cost * 3})
                item = factory.name_to_config[product]
                self.assertEqual(factory.select_product_with_material_check('ISLAND_WOOD_PROCESSING_POST1', [item]), item)
                factory.post_add_one.assert_called_once_with(2)
                self.assertEqual(factory.island_round.get(raw), cost)
                # 花束另一种原料仍未知；仅这一项确认有量也必须严格限量和扣减。
                for material in DIC_ISLAND_RECIPE[recipe_for_local_name(product)]['commission_cost']:
                    if material != raw:
                        self.assertIsNone(factory.island_round.get(material))
                factory = self.factory(product, {product: 0, raw: 0})
                self.assertIsNone(factory.select_product_with_material_check('ISLAND_WOOD_PROCESSING_POST1', [item]))
                factory._prepare_recipe_page.assert_not_called()

    def test_manufacture_uses_old_selection_and_actual_batches_for_all_materials(self):
        factory = self.factory()
        item = factory.name_to_config['file_cabinet']
        self.assertEqual(factory.select_product_with_material_check('ISLAND_WOOD_PROCESSING_POST1', [item]), item)
        factory.select_product.assert_called_once_with(item['selection'], item['selection_check'])
        factory.post_add_one.assert_called_once_with(3)
        recipe = DIC_ISLAND_RECIPE[recipe_for_local_name('file_cabinet')]
        for material, cost in recipe['commission_cost'].items():
            self.assertEqual(factory.island_round.get(material), cost * 2)
        product = LOCAL_TO_ITEM_ID['file_cabinet']
        self.assertEqual(factory.island_round.get(product), 0)
        self.assertEqual(factory.island_round.in_production[product], 2 * recipe['commission_product'][product])

    def test_zero_or_unknown_manufacture_material_avoids_opening_slot(self):
        for stock in (0, None):
            with self.subTest(stock=stock):
                factory = self.factory()
                recipe = DIC_ISLAND_RECIPE[recipe_for_local_name('file_cabinet')]
                for material in recipe['commission_cost']:
                    factory.island_round.observe(material, stock)
                self.assertIsNone(factory.select_product_with_material_check(
                    'ISLAND_WOOD_PROCESSING_POST1', [factory.name_to_config['file_cabinet']]))
                factory._prepare_recipe_page.assert_not_called()

    def test_industrial_threshold_uses_shared_warehouse_without_scanning(self):
        factory = self.factory()
        factory.manufacture = {'industrial_production': {'items': [
            {'name': 'iron_nail'}, {'name': 'cutlery'}]}}
        factory.name_to_config = {item['name']: item for item in factory.manufacture['industrial_production']['items']}
        factory.island_round.observe('iron_nail', 20)
        factory.island_round.observe('cutlery', 0)
        factory.get_idle_posts_by_category = Mock(return_value=['ISLAND_INDUSTRIAL_POST1'])
        factory.select_product_with_material_check = Mock()
        factory.schedule_industrial_production()
        factory._prepare_recipe_page.assert_not_called()
        self.assertEqual(factory.select_product_with_material_check.call_args.args[1],
                         [{'name': 'cutlery'}, {'name': 'iron_nail'}])


if __name__ == '__main__':
    unittest.main()
