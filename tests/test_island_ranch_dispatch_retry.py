"""牧场材料计数、空岗失败复查及共享轮导航的离线回归。"""

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.config.config import AzurLaneConfig, name_to_function
from module.island.island import Island, ISLAND_POSTMANAGE_CHECK, ISLAND_SELECT_PRODUCT_CHECK
from module.island.island_fishery import IslandFishery
from module.island.island_rancher import IslandRancher
from module.island.round_inventory import IslandRoundInventory
from tests.test_island_reward_state import FrameFlow


class RanchMaterialCounterTests(unittest.TestCase):
    def setUp(self):
        self.log = self.enterContext(patch('module.island.island_rancher.logger'))

    def make_flow(self, stock, counter, product_frames=1):
        flow = FrameFlow([[ISLAND_SELECT_PRODUCT_CHECK]] * product_frames + [[ISLAND_POSTMANAGE_CHECK]],
                         task_class=IslandRancher)
        task = flow.task
        task.ranch_feed_map = {'ISLAND_RANCH_POST2': 'pig_feed'}
        task.inventory_counts = {'mill': {'pig_feed': stock}}
        task.island_round = IslandRoundInventory({'pig_feed': stock})
        task._round_ranch_states = {'ISLAND_RANCH_POST2': 'idle'}
        task._round_dispatch_batches = 1
        task.ocr_select_product_material_detail = Mock(return_value=(stock, counter))
        task.back_to_postmanage_after_feed_purchase = Mock(return_value=True)
        task.is_post_detail_visible = Mock(return_value=False)
        task._item_cn = str
        return flow

    def test_plain_and_parenthesized_material_counts_reach_dispatch(self):
        for counter, required in [('62/2', 2), ('62/(2)', 2), ('62/(2+6)', 8)]:
            with self.subTest(counter=counter):
                flow = self.make_flow(62, counter)
                self.assertTrue(flow.run('ranch_post_get_and_add', 'ISLAND_RANCH_POST2'))
                task = flow.task
                task.confirm_post_add_order.assert_called_once()
                self.assertEqual(task._round_material_receipt,
                                 {'item_id': 3001, 'stock': 62, 'required': required})
                self.assertEqual(task._round_ranch_states['ISLAND_RANCH_POST2'], 'working')
                self.assertFalse(task.island_round.is_blocked('pig_feed'))

    def test_invalid_or_inconsistent_counter_blocks_without_order(self):
        for stock, counter in [(62, '62'), (62, '62/(2+)'), (62, '62/(2*3)'),
                               (62, '62/0'), (61, '62/(2)')]:
            with self.subTest(stock=stock, counter=counter):
                flow = self.make_flow(stock, counter, product_frames=4)
                self.assertFalse(flow.run('ranch_post_get_and_add', 'ISLAND_RANCH_POST2'))
                task = flow.task
                task.confirm_post_add_order.assert_not_called()
                task.back_to_postmanage_from_dispatch.assert_called_once()
                self.assertTrue(task.island_round.is_blocked('pig_feed'))
                self.assertEqual(task.island_round.get('pig_feed'), stock)
                self.assertIn(repr(counter), self.log.warning.call_args.args[0])
                self.assertEqual(task.ocr_select_product_material_detail.call_count, 3)
                self.assertEqual(flow.reads, 3)
                task.device.sleep.assert_not_called()

    def test_counter_recovers_on_second_new_frame_without_blocking_or_extra_sleep(self):
        flow = self.make_flow(62, '62/(2)', product_frames=2)
        task = flow.task
        task.ocr_select_product_material_detail.side_effect = [(0, '62'), (62, '62/(2)')]
        self.assertTrue(flow.run('ranch_post_get_and_add', 'ISLAND_RANCH_POST2'))
        self.assertEqual(task.ocr_select_product_material_detail.call_count, 2)
        self.assertEqual(flow.reads, 3)
        task.confirm_post_add_order.assert_called_once()
        self.assertFalse(task.island_round.is_blocked('pig_feed'))
        self.assertEqual(task._round_material_receipt['stock'], 62)
        task.device.sleep.assert_called_once_with(0.3)

    def test_three_different_failed_readings_stop_before_a_later_valid_frame(self):
        flow = self.make_flow(62, '62/(2)', product_frames=4)
        task = flow.task
        task.ocr_select_product_material_detail.side_effect = [
            (0, '62'), (62, '62/(2+)'), (62, '63/(2)'), (62, '62/(2)'),
        ]
        self.assertFalse(flow.run('ranch_post_get_and_add', 'ISLAND_RANCH_POST2'))
        self.assertEqual(task.ocr_select_product_material_detail.call_count, 3)
        self.assertEqual(flow.reads, 3)
        task.confirm_post_add_order.assert_not_called()
        task.device.sleep.assert_not_called()
        self.assertTrue(task.island_round.is_blocked('pig_feed'))
        self.assertEqual(task.island_round.get('pig_feed'), 62)

    def test_confirmed_insufficient_feed_is_failure_without_order(self):
        flow = self.make_flow(0, '0/(2)')
        self.assertFalse(flow.run('ranch_post_get_and_add', 'ISLAND_RANCH_POST2'))
        flow.task.confirm_post_add_order.assert_not_called()
        flow.task.back_to_postmanage_after_feed_purchase.assert_called_once()
        self.assertEqual(flow.task._round_ranch_states['ISLAND_RANCH_POST2'], 'idle')
        flow.task.ocr_select_product_material_detail.assert_called_once()
        self.assertEqual(flow.reads, 1)

    def test_unconfirmed_order_does_not_report_success_or_mark_post_working(self):
        flow = self.make_flow(62, '62/(2)')
        flow.task.confirm_post_add_order.return_value = False
        self.assertFalse(flow.run('ranch_post_get_and_add', 'ISLAND_RANCH_POST2'))
        flow.task.back_to_postmanage_from_dispatch.assert_called_once()
        self.assertEqual(flow.task._round_ranch_states['ISLAND_RANCH_POST2'], 'idle')
        self.assertEqual(flow.task.island_round.get('pig_feed'), 62)


class RanchDispatchRetryTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 7, 7, 50, 36)
        self.enterContext(patch('module.island.island_rancher.current_time', return_value=self.now))
        self.enterContext(patch('module.island.island_rancher.logger'))

    def make_runner(self, states, results):
        config = SimpleNamespace(IslandRancher_MinChicken=1000, IslandRancher_MinPork=1000,
                                 Scheduler_FailureInterval=120)

        def init(task, config):
            task.config, task.device = config, Mock()

        with patch.object(Island, '__init__', init):
            task = IslandRancher(config)
        task.island_round = IslandRoundInventory()
        task._round_ranch_states = dict(states)
        task._round_fishery = Mock()
        for index in range(1, 5):
            setattr(task, f'time_ranch{index}', None)
        for name in ('ui_ensure', 'warehouse_mill_ranch', 'goto_postmanage',
                     'post_manage_mode', 'post_close'):
            setattr(task, name, Mock())
        task.process_mill_supplements = Mock(return_value=[])
        task.check_ranch_needs = Mock(return_value=list(states))
        task.ranch_post = Mock(side_effect=lambda post, _: results[post])
        return task

    def run_times(self, task):
        task.run()
        return task._round_fishery.run.call_args.kwargs['ranch_finish_times']

    def test_prepared_round_skips_island_map_and_warehouse(self):
        task = self.make_runner({}, {})
        self.assertEqual(self.run_times(task), [self.now + timedelta(hours=6)])
        task.ui_ensure.assert_not_called()
        task.warehouse_mill_ranch.assert_not_called()
        task.goto_postmanage.assert_called_once()

    def test_idle_failure_adds_five_minute_retry_and_preserves_working_timer(self):
        task = self.make_runner({'ISLAND_RANCH_POST1': 'idle', 'ISLAND_RANCH_POST2': 'working'},
                                {'ISLAND_RANCH_POST1': False, 'ISLAND_RANCH_POST2': True})
        task.time_ranch2 = self.now + timedelta(hours=1)
        self.assertEqual(self.run_times(task), [self.now + timedelta(minutes=5),
                                              task.time_ranch2, self.now + timedelta(hours=6)])
        self.assertTrue(task._round_dispatch_retry)

    def test_existing_working_or_locked_post_failure_does_not_add_retry(self):
        for state in ('working', 'locked'):
            with self.subTest(state=state):
                task = self.make_runner({'ISLAND_RANCH_POST1': state}, {'ISLAND_RANCH_POST1': False})
                self.assertEqual(self.run_times(task), [self.now + timedelta(hours=6)])
                self.assertFalse(task._round_dispatch_retry)

    def test_successful_idle_dispatch_preserves_normal_schedule(self):
        task = self.make_runner({'ISLAND_RANCH_POST1': 'idle'}, {'ISLAND_RANCH_POST1': True})
        task.time_ranch1 = self.now + timedelta(hours=2)
        self.assertEqual(self.run_times(task), [task.time_ranch1, self.now + timedelta(hours=6)])
        self.assertFalse(task._round_dispatch_retry)

    def test_already_confirmed_working_state_is_not_retried_after_time_read_failure(self):
        task = self.make_runner({'ISLAND_RANCH_POST1': 'idle'}, {})

        def dispatch(post, _):
            task._round_ranch_states[post] = 'working'
            return False

        task.ranch_post.side_effect = dispatch
        self.assertEqual(self.run_times(task), [self.now + timedelta(hours=6)])
        self.assertFalse(task._round_dispatch_retry)

    def test_failure_retry_merges_with_real_fishery_schedule_and_aligns_to_hour(self):
        task = self.make_runner({'ISLAND_RANCH_POST1': 'idle'}, {'ISLAND_RANCH_POST1': False})
        config = AzurLaneConfig.__new__(AzurLaneConfig)
        config.bound, config.modified, config.auto_update = {}, {}, False
        config.data = {'IslandPlan': {'IslandPlan': {'TaskAlignment': 'hour'}}}
        config.task = name_to_function('IslandRancher')
        task.config = config
        fishery = IslandFishery.__new__(IslandFishery)
        fishery._round_idle_posts = []
        fishery.inventory_counts = {}
        fishery.fishery_times = [self.now + timedelta(hours=4)]
        fishery._inv_cn = str
        for name in ('check_inventory_and_prepare_list', '_remove_working_fishery_demand',
                     'goto_postmanage', 'post_manage_mode', 'post_close', 'run_fish_meat_targets'):
            setattr(fishery, name, Mock())
        task._round_fishery = fishery
        with patch('module.island.island_fishery.current_time', return_value=self.now), \
                patch('module.config.config.current_time', return_value=self.now), \
                patch('module.island.island_fishery.logger'):
            task.run()
        self.assertEqual(config.modified['IslandRancher.Scheduler.NextRun'],
                         datetime(2026, 11, 7, 8))


if __name__ == '__main__':
    unittest.main()
