"""用隔离配置文件验证共享轮跨真实任务边界与保存重载的生命周期。"""

import json
import tempfile
import unittest
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from alas import AzurLaneAutoScript
from module.config.config import AzurLaneConfig, TaskEnd
from module.config.deep import deep_set
from module.island.production_round import (
    PRODUCTION_TASKS, IslandProductionRound, round_config_signature,
    run_production_task, select_unfinished_task,
)


class RoundLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 8, 18, 58)
        self.directory = self.enterContext(tempfile.TemporaryDirectory(prefix='island-round-'))
        self.path = Path(self.directory) / 'offline_round.json'
        data = json.loads(Path('config/template.json').read_text(encoding='utf-8'))
        for task in PRODUCTION_TASKS:
            data[task]['Scheduler'].update(
                Enable=task in ('IslandRestaurant', 'IslandJuuEatery'),
                NextRun=str(self.now + timedelta(hours=6)))
        data['IslandRestaurant']['Scheduler']['NextRun'] = str(self.now)
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        for module in ('module.config.config', 'module.config.config_updater'):
            self.enterContext(patch(f'{module}.filepath_config', return_value=str(self.path)))
        self.enterContext(patch('module.config.config.current_time', return_value=self.now))
        self.clock = self.enterContext(patch('module.island.production_round.current_time', return_value=self.now))
        self.log = self.enterContext(patch('module.island.production_round.logger'))
        self.enterContext(patch('module.config.config.logger'))
        self.enterContext(patch('alas.logger'))
        self.enterContext(patch('module.runtime.preview.set_task'))
        self.enterContext(patch('module.statistics.resource_flow.task_session', return_value=nullcontext()))
        self.enterContext(patch('module.campaign.recovery.campaign_recovery_scope', return_value=nullcontext()))
        self.events = []
        events = self.events

        class Runner:
            def __init__(self, config, device):
                self.config, self.device = config, device
                self.task = config.task.command
                self.posts = {'1': {'state': 'idle'}}

            def prepare_round_start(self):
                return ()

            def prepare_round_finish(self):
                events.append(('harvest', self.task))

            def prepare_round(self):
                self.prepare_round_finish()

            def goto_postmanage(self):
                pass

            def post_manage_mode(self, mode):
                pass

            def post_close(self):
                return True

            def round_stock_items(self):
                return {'coffee_bean'}

            def run(self):
                events.append(('run', self.task))
                self.island_round.consume({'coffee_bean': 2})
                self.config.task_delay(target=datetime(2026, 11, 8, 21))

        self.runner_cls = Runner
        self.enterContext(patch('module.island.production_round.import_module', return_value=SimpleNamespace(
            **{name: Runner for _, name in PRODUCTION_TASKS.values()})))
        self.warehouse = self.enterContext(patch(
            'module.island.round_warehouse.read_round_warehouse_stocks', return_value={2009: 10}))
        self.script = AzurLaneAutoScript.__new__(AzurLaneAutoScript)
        self.script.config_name = 'offline_round'
        self.script.device = SimpleNamespace(screenshot=Mock())
        self.script._channel_float_done = True
        self.script.island_restaurant = lambda: run_production_task(
            self.script, 'IslandRestaurant', Runner)
        self.script.island_juu_eatery = lambda: run_production_task(
            self.script, 'IslandJuuEatery', Runner)
        self.reload('IslandRestaurant')

    def reload(self, task):
        self.script.config = AzurLaneConfig('offline_round', task=task)

    def start(self):
        self.assertTrue(self.script.run('island_restaurant'))
        session = self.script._island_production_round
        self.assertEqual(session.pending, {'IslandJuuEatery'})
        self.assertEqual(session.inventory.get('coffee_bean'), 8)
        return session

    def test_real_save_reload_and_normal_return_keep_snapshot_across_hour(self):
        session = self.start()
        # 第一任务已真实保存 NextRun；另存运行记录也不改变生产输入。
        self.script.config.cross_set('IslandJuuEatery.Storage.TestRecord', {'value': 'runtime only'})
        self.clock.return_value = self.now + timedelta(minutes=5)
        self.reload('IslandJuuEatery')
        self.assertTrue(session.valid(self.script.config))
        self.assertEqual(select_unfinished_task(self.script.config, 'IslandJuuEatery', session), 'IslandJuuEatery')
        self.script.config.pending_task = [SimpleNamespace(command=name)
                                           for name in ('IslandRestaurant', 'IslandJuuEatery')]
        self.assertEqual(select_unfinished_task(self.script.config, 'IslandRestaurant', session), 'IslandJuuEatery')
        self.assertTrue(self.script.run('island_juu_eatery'))
        self.warehouse.assert_called_once()
        self.assertEqual(session.inventory.get('coffee_bean'), 6)
        self.assertIsNone(self.script._island_production_round)

    def test_unexecuted_daily_gather_settings_do_not_rebuild_production_round(self):
        session = self.start()
        path = 'IslandDailyGather.Scheduler.Enable'
        self.script.config.cross_set(path, not self.script.config.cross_get(path))
        self.reload('IslandJuuEatery')
        self.assertTrue(session.valid(self.script.config))
        self.assertTrue(self.script.run('island_juu_eatery'))
        self.warehouse.assert_called_once()
        self.assertEqual(session.inventory.get('coffee_bean'), 6)

    def test_changed_production_target_rebuilds_and_logs_path_without_value(self):
        self.start()
        path = 'IslandJuuEatery.IslandJuuEatery.MealNumber1'
        self.script.config.cross_set(path, self.script.config.cross_get(path) + 123456)
        self.reload('IslandJuuEatery')
        self.assertTrue(self.script.run('island_juu_eatery'))
        self.assertEqual(self.warehouse.call_count, 2)
        messages = '\n'.join(str(call.args[0]) for call in self.log.info.call_args_list)
        self.assertIn(path, messages)
        self.assertNotIn('123456', messages)

    def test_production_dependencies_remain_part_of_signature(self):
        initial = round_config_signature(self.script.config)
        for path, value in (
                ('IslandPlan.IslandPlan.Season', 'unrecognized-season'),
                ('IslandFarm.Scheduler.Enable', True),
                ('IslandRancher.IslandFishery.MinFreshwaterFishMeat', 987654),
                ('IslandBusiness.IslandBusinessShop1.Product1', 'changed-menu'),
                ('IslandDailyOrder.IslandDailyOrder.StuckSeasonOrderId', 987654),
                ('IslandSeasonalProduction.IslandSeasonWinter.green_jujube_porridge', 987654)):
            with self.subTest(path=path):
                data = deepcopy(self.script.config.data)
                deep_set(data, path, value)
                self.assertNotEqual(initial, round_config_signature(SimpleNamespace(data=data)))

    def test_target_changed_during_preparation_is_not_hidden_by_resigning(self):
        def changed_during_scan(*args):
            self.script.config.cross_set('IslandJuuEatery.IslandJuuEatery.MealNumber1', 987654)
            return {2009: 10}

        self.warehouse.side_effect = changed_during_scan
        session = self.start()
        self.assertIn('IslandJuuEatery.IslandJuuEatery.MealNumber1', session.invalid_reason(self.script.config))

    def test_real_commission_boundary_preserves_round_but_other_island_task_drops_it(self):
        session = self.start()
        self.script.commission = lambda: None
        self.script.research = lambda: None
        self.script.island_daily_gather = lambda: None
        for task in ('commission', 'research'):
            self.assertTrue(self.script.run(task))
            self.assertIs(self.script._island_production_round, session)
        self.assertTrue(self.script.run('island_daily_gather'))
        self.assertIsNone(self.script._island_production_round)

    def test_task_end_during_production_drops_round_even_when_outer_task_returns_true(self):
        session = self.start()
        session.runners['IslandJuuEatery'].run = Mock(side_effect=TaskEnd('private stop message'))
        self.reload('IslandJuuEatery')
        self.assertTrue(self.script.run('island_juu_eatery'))
        self.assertIsNone(self.script._island_production_round)
        messages = '\n'.join(str(call.args[0]) for call in self.log.info.call_args_list)
        self.assertIn('TaskEnd', messages)
        self.assertNotIn('private stop message', messages)

    def test_invalid_reason_limits_paths_and_does_not_expand_parameter_contents(self):
        session = IslandProductionRound.__new__(IslandProductionRound)
        data = {'IslandRestaurant': {'IslandRestaurant': {f'Meal{i}': 'before' for i in range(10)}}}
        session.signature = round_config_signature(SimpleNamespace(data=data))
        session.ready, session.pending = True, {'IslandRestaurant'}
        changed = {'IslandRestaurant': {'IslandRestaurant': {f'Meal{i}': {'secret': 'after'} for i in range(10)}}}
        reason = session.invalid_reason(SimpleNamespace(data=changed))
        self.assertEqual(reason.count('IslandRestaurant.IslandRestaurant.'), 8)
        self.assertIn('另 2 项省略', reason)
        for value in ('before', 'after', 'secret'):
            self.assertNotIn(value, reason)
        session.signature = round_config_signature(SimpleNamespace(data=changed))
        self.assertIn('不在本轮', session.invalid_reason(SimpleNamespace(data=changed), 'IslandFarm'))
        session.ready = False
        self.assertIn('准备尚未完成', session.invalid_reason(SimpleNamespace(data=changed)))
        session.ready, session.pending = True, set()
        self.assertIn('全部完成', session.invalid_reason(SimpleNamespace(data=changed)))


if __name__ == '__main__':
    unittest.main()
