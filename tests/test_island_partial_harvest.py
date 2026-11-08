"""部分收获后的岗位占用及派遣保护，不连接游戏或真实配置。"""

import unittest
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from module.base.base import ModuleBase
from module.base.utils import load_image
from module.island.assets import ISLAND_POST_SELECT, ISLAND_WORK_COMPLETE, ISLAND_WORKING
from module.island.island_manufacture import IslandManufacture
from module.island.island_restaurant import IslandRestaurant
from module.island.island_shop_base import IslandShopBase
from module.island.item_ids import ITEM_ID_TO_LOCAL, LOCAL_TO_ITEM_ID
from module.island.round_inventory import IslandRoundInventory
from tests.test_island_food_dispatch import FakeDevice, FoodUI
from tests import test_island_round_food as round_food
from tests.test_island_round_food import recipe_stocks
from tests.test_island_shop_production import ShopUI


NOW = datetime(2026, 11, 8, 18, 3)
WORKING_DETAIL = Path(__file__).parent / 'fixtures/island_partial_harvest/working_tofu_detail.png'


class HarvestUI:
    """保留真实岗位检查与计时循环，仅模拟收获交互和后续截图。"""

    def __init__(self, after, initial=ISLAND_WORK_COMPLETE):
        self.clock = 1000.0
        self.collected = False
        self.after = list(after)
        self.reads_after = 0
        self.frame = (initial, 'tofu', 6, '03:00:00')
        self.task = IslandShopBase.__new__(IslandShopBase)
        task = self.task
        task.device = SimpleNamespace(image=self.frame, screenshot=self.screenshot, sleep=self.sleep)
        task.posts = {'POST1': {'button': Mock(), 'status': 'none', 'crop': None, 'runs': 0}}
        task.post_check_meal = Counter()
        task._round_collect_all = True
        task.post_close = Mock(return_value=True)
        task.post_open = Mock(return_value=True)
        task.post_list_is_empty = Mock(return_value=False)
        task.post_product_check = Mock(side_effect=lambda: self.frame[1])
        task.post_get_stay = Mock(side_effect=self.collect)
        task.post_get_and_close = Mock(side_effect=self.collect)
        task.appear = lambda button, **kwargs: button is self.frame[0]
        task.ui_additional = Mock(return_value=False)
        task.loop = Mock(wraps=ModuleBase.loop.__get__(task, IslandShopBase))

    def collect(self):
        self.collected = True
        return True

    def screenshot(self):
        self.clock += 0.5
        if self.collected:
            self.frame = self.after[min(self.reads_after, len(self.after) - 1)]
            self.reads_after += 1
        self.task.device.image = self.frame
        return self.frame

    def sleep(self, seconds):
        self.clock += seconds


class PartialHarvestOccupationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.island.island_shop_base.logger'))
        self.enterContext(patch('module.island.island_shop_base.current_time', return_value=NOW))
        digit = self.enterContext(patch('module.island.island_shop_base.Digit'))
        digit.return_value.ocr.side_effect = lambda image: image[2]
        raw = self.enterContext(patch('module.island.island_shop_base.Ocr'))
        raw.return_value.ocr.side_effect = lambda image: image[3]

    def check(self, flow):
        with patch('module.base.timer.time', lambda: flow.clock):
            flow.task.post_check('POST1', 'time_1')
        return flow.task

    def test_partial_harvest_keeps_remaining_four_tofu_working(self):
        task = self.check(HarvestUI([(ISLAND_WORKING, 'tofu', 4, '02:37:15')]))
        self.assertEqual(task.posts['POST1']['status'], 'working')
        self.assertEqual(task.posts['POST1']['crop'], 'tofu')
        self.assertEqual(task.posts['POST1']['runs'], 4)
        self.assertEqual(task.time_1, NOW + timedelta(hours=2, minutes=37, seconds=15))
        self.assertEqual(task.post_check_meal, {'tofu': 4})
        self.assertEqual(task.get_idle_posts(), [])

    def test_full_harvest_requires_actual_idle_confirmation(self):
        task = self.check(HarvestUI([(ISLAND_POST_SELECT, None, 0, '')]))
        self.assertEqual(task.posts['POST1']['status'], 'idle')
        self.assertIsNone(task.posts['POST1']['crop'])
        self.assertEqual(task.posts['POST1']['runs'], 0)
        self.assertIsNone(task.time_1)
        self.assertEqual(task.post_check_meal, {})

    def test_unconfirmed_post_collection_frame_is_not_idle(self):
        flow = HarvestUI([(None, None, 0, '')])
        self.check(flow)
        self.assertEqual(flow.task.posts['POST1']['status'], 'unknown')
        self.assertEqual(flow.task.get_idle_posts(), [])
        self.assertIsNone(flow.task.time_1)

    def test_rechecking_same_post_replaces_its_contribution_only(self):
        flow = HarvestUI([(ISLAND_WORKING, 'tofu', 4, '02:37:15')])
        task = self.check(flow)
        task.post_check_meal['tofu'] += 3  # 另一岗位的独立在制数量。
        self.check(flow)
        self.assertEqual(task.post_check_meal, {'tofu': 7})
        flow.after = [(ISLAND_WORKING, 'tofu', 2, '01:00:00')]
        self.check(flow)
        self.assertEqual(task.post_check_meal, {'tofu': 5})
        flow.after = [(ISLAND_POST_SELECT, None, 0, '')]
        self.check(flow)
        self.assertEqual(task.post_check_meal, {'tofu': 3})

    def test_repeated_complete_marker_after_collection_is_not_proof_of_idle(self):
        task = self.check(HarvestUI([(ISLAND_WORK_COMPLETE, 'tofu', 4, '')]))
        self.assertEqual(task.posts['POST1']['status'], 'unknown')
        self.assertEqual(task.get_idle_posts(), [])

    def test_unknown_duration_does_not_count_same_working_quantity_on_every_frame(self):
        task = self.check(HarvestUI([(ISLAND_WORKING, 'tofu', 4, '02:37:1')]))
        self.assertEqual(task.posts['POST1']['status'], 'working')
        self.assertIsNone(task.time_1)
        self.assertEqual(task.post_check_meal, {'tofu': 4})

    def test_unknown_post_collection_state_gets_short_recheck_candidate(self):
        from tests.test_island_shop_working_duration import UnknownWorkingScheduleTests

        inspected = self.check(HarvestUI([(None, None, 0, '')]))
        task = UnknownWorkingScheduleTests().shop(
            (None, None), states=(inspected.posts['POST1']['status'], 'none'))
        with patch('module.island.island_shop_base.current_time', return_value=NOW):
            task.run()
        self.assertIn(NOW + timedelta(minutes=5), task.config.task_delay.call_args.kwargs['target'])

    def test_refresh_of_new_dispatch_does_not_add_it_again_to_old_in_production(self):
        flow = HarvestUI([(ISLAND_WORKING, 'tofu', 4, '01:00:00')])
        flow.collected = True
        task = flow.task
        task.posts['POST1'].update(dispatched_in_run=True, time_var_name='time_1')
        task.island_round = IslandRoundInventory({'tofu': 0})
        task.island_round.production_started('tofu', 4)
        with patch('module.base.timer.time', lambda: flow.clock):
            task.refresh_post_occupation('POST1')
            task.refresh_post_occupation('POST1')
        self.assertEqual(task.post_check_meal, {})
        self.assertEqual(task.island_round.in_production, {LOCAL_TO_ITEM_ID['tofu']: 4})
        self.assertEqual(task.island_round.get('tofu'), 0)
        task.post_get_stay.assert_not_called()
        task.post_close.assert_not_called()


class BusyPostDispatchTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.island.island_shop_base.logger'))
        self.enterContext(patch('module.island.island_manufacture.logger'))
        self.enterContext(patch('module.island.island_shop_base.current_time', return_value=NOW))

    def food(self, stages, *, product='cabbage_tofu', target=5, working_product='tofu', working_quantity=4):
        shop = ShopUI(IslandRestaurant, products=[(product, target)]).shop
        shop.post_produce = IslandRestaurant.post_produce.__get__(shop, IslandRestaurant)
        shop.island_round = IslandRoundInventory(recipe_stocks(product, batches=20))
        shop.warehouse_counts = {ITEM_ID_TO_LOCAL[item]: quantity
                                 for item, quantity in shop.island_round.stocks.items()}
        shop.device = Mock()
        shop.post_close = Mock(return_value=True)
        shop.post_open = Mock(return_value=True)
        shop._dispatch_stage = Mock(side_effect=stages)
        shop.refresh_post_occupation = Mock(side_effect=lambda post_id: shop._set_post_occupation(
            post_id, 'working', working_product, working_quantity) or 'working')
        shop.select_product = Mock(return_value=True)
        shop.produce_check = Mock(return_value=False)
        shop.post_add_one = Mock()
        shop.confirm_food_dispatch = Mock(side_effect=lambda amount, label: (
            (amount, timedelta(hours=1)), NOW))
        shop.back_to_postmanage_from_dispatch = Mock(return_value=True)
        shop.to_post_products = {product: target}
        for post in shop.posts.values():
            post.update(status='idle', round_enabled=True)
        return shop

    def run_prepared(self, shop):
        shop._round_prepared = True
        shop._round_time_vars = []
        for index, post in enumerate(shop.posts.values(), 1):
            variable = f'{shop.time_prefix}{index}'
            post['time_var_name'] = variable
            shop._round_time_vars.append(variable)
            setattr(shop, variable, None)
        shop.post_check = Mock(side_effect=AssertionError('正式执行不重复巡检或收获'))
        shop.get_warehouse_counts = Mock(side_effect=AssertionError('正式执行不重新盘点仓库'))
        shop._schedule_and_track = Mock(wraps=shop._schedule_and_track)
        shop.run()

    def test_busy_food_post_preserves_stock_and_does_not_block_product_or_try_fallback(self):
        shop = self.food(['working'])
        post = next(iter(shop.posts))
        before = dict(shop.island_round.stocks)
        self.assertEqual(shop.post_produce(post, 'cabbage_tofu', 5, 'time1', product2='tofu'), 0)
        self.assertTrue(shop._dispatch_post_busy)
        self.assertEqual(shop.posts[post]['status'], 'working')
        self.assertEqual(shop.island_round.stocks, before)
        self.assertEqual(shop.island_round.in_production, {})
        self.assertEqual(shop.island_round.blocked_products, set())
        self.assertEqual(shop.chef_unavailable_products, set())
        shop.refresh_post_occupation.assert_called_once_with(post)
        shop._dispatch_stage.assert_called_once()
        shop.select_product.assert_not_called()
        shop.post_add_one.assert_not_called()
        shop.confirm_food_dispatch.assert_not_called()

    def test_busy_first_food_post_does_not_prevent_next_idle_post_from_dispatching(self):
        shop = self.food(['working', 'products'])
        self.run_prepared(shop)
        posts = list(shop.posts)
        self.assertEqual(shop.post_open.call_count, 2)
        self.assertEqual(shop.posts[posts[0]]['crop'], 'tofu')
        self.assertEqual(shop.posts[posts[1]]['status'], 'working')
        self.assertEqual(shop.island_round.in_production[LOCAL_TO_ITEM_ID['cabbage_tofu']], 5)
        self.assertNotIn('cabbage_tofu', shop.chef_unavailable_products)
        self.assertFalse(shop.island_round.is_blocked('cabbage_tofu'))
        self.assertEqual(shop.to_post_products, {})
        shop.confirm_food_dispatch.assert_called_once()

    def test_discovered_same_product_recalculates_deficit_before_next_post(self):
        shop = self.food(['working', 'products'], product='tofu', target=10)
        self.run_prepared(shop)
        self.assertEqual(shop.post_open.call_count, 2)
        self.assertEqual(shop.confirm_food_dispatch.call_args.args[0], 6)
        self.assertEqual(shop.post_check_meal, {'tofu': 4})
        self.assertEqual(shop.island_round.in_production[LOCAL_TO_ITEM_ID['tofu']], 6)
        self.assertEqual(shop._schedule_and_track.call_args.args[0], {'tofu': 6})
        self.assertNotIn('tofu', shop.chef_unavailable_products)
        self.assertFalse(shop.island_round.is_blocked('tofu'))

    def test_two_busy_posts_do_not_mark_requested_product_unavailable(self):
        shop = self.food(['working', 'working'], product='tofu', target=10, working_quantity=2)
        self.run_prepared(shop)
        self.assertEqual(shop.post_open.call_count, 2)
        self.assertEqual(shop.post_check_meal, {'tofu': 4})
        self.assertEqual(shop.island_round.in_production, {})
        self.assertEqual(shop._schedule_and_track.call_args.args[0], {})
        self.assertNotIn('tofu', shop.chef_unavailable_products)
        self.assertFalse(shop.island_round.is_blocked('tofu'))
        shop.confirm_food_dispatch.assert_not_called()

    def test_unknown_post_collection_state_keeps_seasonal_in_production_guard(self):
        from tests.test_island_seasonal_shop_targets import make_shop, prepare_shop

        shop = prepare_shop(make_shop(targets={'green_jujube_porridge': 5}))
        first = next(iter(shop.posts))
        shop.posts[first].update(status='unknown', crop=None)
        shop.run()
        self.assertEqual(shop.selections, [])
        shop.confirm_food_dispatch.assert_not_called()

    def test_busy_manufacture_post_preserves_product_for_other_empty_posts(self):
        factory = round_food.RoundManufactureTests().factory()
        post = 'ISLAND_WOOD_PROCESSING_POST1'
        factory.posts[post]['button'] = Mock()
        factory.device = Mock()
        factory.post_close = Mock(return_value=True)
        factory.post_open = Mock(return_value=True)
        factory._prepare_recipe_page = IslandManufacture._prepare_recipe_page.__get__(factory, IslandManufacture)
        factory._dispatch_stage = Mock(return_value='working')
        factory.refresh_post_occupation = Mock(side_effect=lambda post_id: factory.posts[post_id].update(
            status='working', crop='file_cabinet', runs=4) or 'working')
        before = dict(factory.island_round.stocks)
        self.assertIsNone(factory.select_product_with_material_check(
            post, [factory.name_to_config['file_cabinet']]))
        self.assertTrue(factory._dispatch_post_busy)
        self.assertEqual(factory.island_round.stocks, before)
        self.assertEqual(factory.island_round.in_production, {})
        self.assertFalse(factory.island_round.is_blocked('file_cabinet'))
        self.assertNotIn('file_cabinet', factory.chef_unavailable_products)
        factory.select_product.assert_not_called()
        factory.confirm_food_dispatch.assert_not_called()


class PartialHarvestImageTests(unittest.TestCase):
    def test_real_remaining_tofu_details_are_busy_before_dispatch(self):
        # 原图只保留匿名岗位的商品、剩余次数、计时和追加按钮区域。
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        frame[280:587, 515:765] = load_image(str(WORKING_DETAIL))
        clock = SimpleNamespace(now=1000.0)
        device = FakeDevice(clock, timeline=lambda device: frame)
        task = FoodUI(device)
        task.ui_additional = Mock(return_value=False)
        task.appear_then_click = Mock(return_value=False)
        with patch('module.base.timer.time', lambda: clock.now):
            self.assertEqual(task._dispatch_stage(), 'working')
        self.assertLess(clock.now - 1000.0, 2)
        task.appear_then_click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
