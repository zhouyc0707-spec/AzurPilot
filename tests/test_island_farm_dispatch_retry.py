"""农田局部派遣失败的复查时间和共享轮页面衔接，不连接游戏。"""

from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.config.config import AzurLaneConfig
from module.island.island import ISLAND_SELECT_PRODUCT_CHECK, MATERIAL_INSUFFICIENT, MATERIAL_READY
from module.island.island_farm import IslandFarm
from module.island.round_inventory import IslandRoundInventory


class IslandFarmDispatchRetryTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 7, 7, 49, 54)
        self.enterContext(patch('module.island.island_farm.current_time', return_value=self.now))
        self.enterContext(patch('module.config.config.current_time', return_value=self.now))
        self.logger = self.enterContext(patch('module.island.island_farm.logger'))

    def make_farm(self, *, shared=True, crops=(), results=(), working_until=None, alignment='hour'):
        farm = object.__new__(IslandFarm)
        farm.device = Mock()
        farm.config = SimpleNamespace(
            Scheduler_FailureInterval=120,
            IslandPlan_TaskAlignment=alignment,
            task=SimpleNamespace(command='IslandFarm'),
            cross_get=lambda key, default=None: alignment,
            cross_set=Mock(),
        )
        farm.config.task_delay = Mock(
            side_effect=lambda **kwargs: AzurLaneConfig.task_delay(farm.config, **kwargs))
        if shared:
            farm.island_round = IslandRoundInventory({'wheat': 0, 'corn': 0})
        farm.farm_positions, farm.orchard_positions, farm.nursery_positions = 2, 0, 0
        farm.posts = {
            'ISLAND_FARM_POST1': {'button': Mock(), 'crop': None, 'state': 'idle'},
            'ISLAND_FARM_POST2': {'button': Mock(), 'crop': None, 'state': 'idle'},
        }
        farm.inventory_counts = {'farm': {'wheat': 0, 'corn': 0}, 'orchard': {}, 'nursery': {}}
        farm.to_plant_lists = {'farm': list(crops), 'orchard': [], 'nursery': []}
        farm.time_vars = {'farm': [working_until, None], 'orchard': [], 'nursery': []}
        farm._round_idle_posts = {
            'farm': [
                {'post_id': post_id, 'button': info['button'], 'time_var_name': f'farm_time_{index}'}
                for index, (post_id, info) in enumerate(farm.posts.items())
            ],
            'orchard': [], 'nursery': [],
        }
        farm.plant_config = {'farm': {'plant_default': 0, 'default_crop': 'wheat'}}
        farm._resolve_default_crop = Mock(return_value='wheat')
        farm._inv_cn = str
        for name in ('ui_ensure', 'goto_postmanage', 'post_manage_mode', 'post_close',
                     'check_inventory_and_prepare_lists', 'prepare_round'):
            setattr(farm, name, Mock())
        farm.post_plant = Mock(side_effect=list(results))
        return farm

    def next_run(self, farm):
        farm.config.cross_set.assert_called_once()
        path, value = farm.config.cross_set.call_args.args
        self.assertEqual(path, 'IslandFarm.Scheduler.NextRun')
        return value

    def test_shared_round_reuses_post_page_without_returning_to_island(self):
        farm = self.make_farm()
        farm.run()
        farm.ui_ensure.assert_not_called()
        farm.prepare_round.assert_not_called()
        farm.goto_postmanage.assert_called_once()

    def test_standalone_keeps_island_entry_and_post_preparation(self):
        farm = self.make_farm(shared=False)
        farm.run()
        farm.ui_ensure.assert_called_once()
        farm.prepare_round.assert_called_once_with(collect_all=False)

    def test_failed_planned_crop_retries_after_five_minutes_then_aligns_to_hour(self):
        farm = self.make_farm(crops=['wheat'], results=[False])
        farm.run()
        self.assertEqual(self.next_run(farm), datetime(2026, 11, 7, 8))
        self.assertIn(self.now + timedelta(minutes=5), farm.config.task_delay.call_args.kwargs['target'])
        self.assertEqual(farm.config.Scheduler_FailureInterval, 120)

    def test_failed_crop_keeps_exact_five_minute_retry_when_alignment_disabled(self):
        farm = self.make_farm(crops=['wheat'], results=[False], alignment='none')
        farm.run()
        self.assertEqual(self.next_run(farm), self.now + timedelta(minutes=5))

    def test_partial_failure_is_not_hidden_by_another_successful_crop(self):
        farm = self.make_farm(crops=['wheat', 'corn'], results=[False, True])
        farm.run()
        self.assertEqual(farm.to_plant_lists['farm'], ['wheat'])
        self.assertEqual(self.next_run(farm), datetime(2026, 11, 7, 8))

    def test_locked_posts_do_not_schedule_failure_retry(self):
        farm = self.make_farm(crops=['wheat'], results=[None, None])
        farm.run()
        self.assertEqual(self.next_run(farm), datetime(2026, 11, 7, 14))

    def test_idle_posts_without_demand_keep_normal_schedule(self):
        farm = self.make_farm()
        farm.run()
        farm.post_plant.assert_not_called()
        self.assertEqual(self.next_run(farm), datetime(2026, 11, 7, 14))

    def test_working_posts_keep_finish_time_without_failure_retry(self):
        finish = self.now + timedelta(minutes=40)
        farm = self.make_farm(crops=['wheat'], working_until=finish)
        for info in farm.posts.values():
            info.update(state='working', crop='wheat')
        farm._round_idle_posts['farm'] = []
        farm.run()
        farm.post_plant.assert_not_called()
        self.assertEqual(self.next_run(farm), datetime(2026, 11, 7, 9))

    def test_successful_attempt_does_not_keep_previous_run_failure(self):
        farm = self.make_farm(crops=['wheat'], results=[False, True])
        farm.run()
        self.assertTrue(farm._plant_retry_needed)
        farm.config.cross_set.reset_mock()
        farm.run()
        self.assertFalse(farm._plant_retry_needed)
        self.assertEqual(self.next_run(farm), datetime(2026, 11, 7, 14))

    def test_seed_check_failure_is_not_reported_as_proven_seed_shortage(self):
        farm = self.make_farm()
        # 使用真实播种入口，只模拟选品页和材料检查失败。
        del farm.post_plant
        farm.post_open = Mock(return_value=True)
        farm.loop = lambda **kwargs: iter([None])
        farm.appear_then_click = Mock(return_value=False)
        farm.appear = lambda button, **kwargs: button is ISLAND_SELECT_PRODUCT_CHECK
        farm.select_product = Mock(return_value=True)
        farm.name_to_config = {'wheat': {'selection': Mock(), 'selection_check': Mock(),
                                        'shop': Mock(), 'seed_number': 99}}
        farm._item_cn = lambda value: '小麦'
        farm.ensure_select_product_material = Mock(return_value=MATERIAL_INSUFFICIENT)
        farm.confirm_post_add_order = Mock()
        farm.back_to_postmanage_from_dispatch = Mock()
        result = farm.post_plant(farm.posts['ISLAND_FARM_POST1']['button'], 'wheat', 'farm', 'farm_time_0')
        self.assertFalse(result)
        farm.confirm_post_add_order.assert_not_called()
        self.logger.warning.assert_called_once_with('[岛屿-农田] 小麦种子数量未确认或补货不足，跳过本次种植')

    def make_material_read_flow(self, outcomes):
        farm = self.make_farm()
        del farm.post_plant
        farm.post_open = Mock(return_value=True)
        farm.appear_then_click = Mock(return_value=False)
        farm.appear = lambda button, **kwargs: button is ISLAND_SELECT_PRODUCT_CHECK
        farm.select_product = Mock(return_value=True)
        farm.name_to_config = {'wheat': {'selection': Mock(), 'selection_check': Mock(),
                                        'shop': Mock(), 'seed_number': 99}}
        farm._item_cn = lambda value: '小麦'
        farm.confirm_post_add_order = Mock(return_value=True)
        farm.back_to_postmanage_from_dispatch = Mock()
        farm.goto_shop_from_select_product = Mock()
        farm.buy_shop_item = Mock()
        farm._round_dispatch_batches = 1
        farm.material_read_frames = []
        outcomes = iter(outcomes)

        def screenshot_loop(**kwargs):
            self.assertFalse(kwargs['skip_first'])
            for frame in range(8):
                farm.device.screenshot()
                farm.device.image = frame
                yield frame

        def read_material(**kwargs):
            farm.material_read_frames.append(farm.device.image)
            material, farm._round_material_unconfirmed = next(outcomes)
            return material

        farm.loop = screenshot_loop
        farm.ensure_select_product_material = Mock(side_effect=read_material)
        duration = self.enterContext(patch('module.island.island_farm.Duration'))
        duration.return_value.ocr.return_value = timedelta(hours=1)
        return farm

    def plant_wheat(self, farm):
        return farm.post_plant(farm.posts['ISLAND_FARM_POST1']['button'],
                               'wheat', 'farm', 'farm_time_0')

    def test_unknown_seed_count_recovers_on_next_frame_and_confirms_once(self):
        farm = self.make_material_read_flow([
            (MATERIAL_INSUFFICIENT, True), (MATERIAL_READY, False),
        ])
        self.assertTrue(self.plant_wheat(farm))
        self.assertEqual(farm.material_read_frames, [0, 1])
        farm.confirm_post_add_order.assert_called_once_with('wheat种植派遣')
        farm.back_to_postmanage_from_dispatch.assert_not_called()
        farm.buy_shop_item.assert_not_called()
        self.assertEqual(farm.posts['ISLAND_FARM_POST1']['state'], 'working')

    def test_three_unknown_seed_frames_stop_without_purchase_or_dispatch(self):
        farm = self.make_material_read_flow([(MATERIAL_INSUFFICIENT, True)] * 3)
        self.assertFalse(self.plant_wheat(farm))
        self.assertEqual(farm.material_read_frames, [0, 1, 2])
        self.assertEqual(farm.ensure_select_product_material.call_count, 3)
        farm.confirm_post_add_order.assert_not_called()
        farm.goto_shop_from_select_product.assert_not_called()
        farm.buy_shop_item.assert_not_called()
        farm.back_to_postmanage_from_dispatch.assert_called_once()
        self.assertEqual(farm.posts['ISLAND_FARM_POST1']['state'], 'idle')

    def test_confirmed_seed_shortage_exits_without_spending_unknown_retry_budget(self):
        farm = self.make_material_read_flow([(MATERIAL_INSUFFICIENT, False)])
        self.assertFalse(self.plant_wheat(farm))
        self.assertEqual(farm.material_read_frames, [0])
        farm.ensure_select_product_material.assert_called_once()
        farm.confirm_post_add_order.assert_not_called()
        farm.back_to_postmanage_from_dispatch.assert_called_once()

    def test_unknown_read_limit_is_local_to_each_plant_attempt(self):
        farm = self.make_material_read_flow([(MATERIAL_INSUFFICIENT, True)] * 6)
        self.assertFalse(self.plant_wheat(farm))
        self.assertFalse(self.plant_wheat(farm))
        self.assertEqual(farm.material_read_frames, [0, 1, 2, 0, 1, 2])
        self.assertEqual(farm.ensure_select_product_material.call_count, 6)
        farm.confirm_post_add_order.assert_not_called()


if __name__ == '__main__':
    unittest.main()
