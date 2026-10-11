"""列表空岗省去领取巡检后，仍进入实际排产的离线回归。"""

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

from module.island.assets import ISLAND_POST_SELECT
from module.island.island_farm import IslandFarm
from module.island.island_fishery import IslandFishery
from module.island.island_mine_forest import IslandMineForest
from module.island.island_shop_base import IslandShopBase
from module.island.island_restaurant import IslandRestaurant
from module.island.island_manufacture import IslandManufacture
from tests.test_island_shop_production import ShopUI
from tests.test_island_fishery_quantities import FisheryUI


class EmptyPostInspectionTests(unittest.TestCase):
    def build_task(self, task_class, confirmed=True):
        task = task_class.__new__(task_class)
        task.posts = {'post1': {'button': 'post1', 'state': 'working',
                               'status': 'working', 'crop': 'old', 'runs': 12}}
        task.post_list_is_empty = Mock(return_value=confirmed)
        task.post_close = Mock()
        task.post_open = Mock(return_value=True)
        task.post_get_and_close = Mock(return_value=True)
        task.post_get_stay = Mock()
        task.device = SimpleNamespace(screenshot=Mock(), sleep=Mock(), image=None)
        task.appear = Mock(side_effect=lambda button, **kwargs: button is ISLAND_POST_SELECT)
        task.time_old = datetime(2026, 10, 1)
        return task

    def test_farm_clears_stale_crop_and_both_times_without_opening(self):
        task = self.build_task(IslandFarm)
        task.posts['ISLAND_FARM_POST1'] = task.posts.pop('post1')
        task.time_vars = {'farm': [task.time_old]}
        task.decided_lists('post1', 'ISLAND_FARM_POST1', 'farm', 'time_old')
        self.assertEqual(task.posts['ISLAND_FARM_POST1']['state'], 'idle')
        self.assertIsNone(task.posts['ISLAND_FARM_POST1']['crop'])
        self.assertEqual(task.posts['ISLAND_FARM_POST1']['runs'], 0)
        self.assertIsNone(task.time_old)
        self.assertEqual(task.time_vars['farm'], [None])
        task.post_open.assert_not_called()
        task.post_get_and_close.assert_not_called()

    def test_mine_empty_is_dispatchable_and_not_a_successful_harvest(self):
        task = self.build_task(IslandMineForest)
        collected = task.collect_and_detect_post('post1', 'post1', 'mine', 'time_old')
        self.assertFalse(collected)
        self.assertTrue(task._post_available_for_dispatch(task.posts['post1']))
        self.assertIsNone(task.time_old)
        self.assertEqual(task.posts['post1']['runs'], 0)
        task.post_open.assert_not_called()
        task.post_get_stay.assert_not_called()

    def test_fishery_empty_is_dispatchable_and_clears_time(self):
        task = self.build_task(IslandFishery)
        task.fishery_times = [task.time_old]
        self.assertFalse(task.decided_lists('post1', 'post1', 0))
        self.assertTrue(task._post_available_for_dispatch(task.posts['post1']))
        self.assertEqual(task.fishery_times, [None])
        task.post_open.assert_not_called()
        task.post_get_stay.assert_not_called()

    def test_food_and_manufacture_empty_remain_in_idle_queue(self):
        for task_class in (IslandShopBase, IslandManufacture):
            with self.subTest(task=task_class.__name__):
                task = self.build_task(task_class)
                task.post_check('post1', 'time_old')
                self.assertEqual(task.get_idle_posts(), ['post1'])
                self.assertEqual(task.posts['post1']['runs'], 0)
                self.assertIsNone(task.posts['post1']['crop'])
                self.assertIsNone(task.time_old)
                task.post_open.assert_not_called()
                task.post_get_and_close.assert_not_called()

    def test_unknown_mine_and_fishery_keep_original_detail_flow(self):
        for task_class in (IslandMineForest, IslandFishery):
            with self.subTest(task=task_class.__name__):
                task = self.build_task(task_class, confirmed=False)
                if task_class is IslandMineForest:
                    task.collect_and_detect_post('post1', 'post1', 'mine', 'time_old')
                else:
                    task.fishery_times = [task.time_old]
                    task.decided_lists('post1', 'post1', 0)
                task.post_open.assert_called_once_with('post1')
                self.assertEqual(task.posts['post1']['state'], 'idle')

    def test_food_actual_run_still_produces_after_skipping_inspection(self):
        ui = ShopUI(IslandRestaurant, [('tofu', 4)], {})
        ui.shop.post_check = IslandShopBase.post_check.__get__(ui.shop)
        ui.shop.post_list_is_empty = Mock(return_value=True)
        ui.shop.post_open = Mock()
        ui.shop.post_get_and_close = Mock()
        ui.run()
        self.assertEqual(ui.orders, [('tofu', 4, None)])
        ui.shop.post_open.assert_not_called()
        ui.shop.post_get_and_close.assert_not_called()

    def test_fishery_actual_run_still_dispatches_empty_slots(self):
        ui = FisheryUI(demand={'crayfish': 1}, fry_stock={'crayfish': 20}, positions=1)
        ui.fishery.decided_lists = IslandFishery.decided_lists.__get__(ui.fishery)
        ui.fishery.post_list_is_empty = Mock(return_value=True)
        ui.run()
        self.assertEqual(ui.orders, [('crayfish', 5)])


if __name__ == '__main__':
    unittest.main()
