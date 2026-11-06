"""验证统一准备、跨委托科研续接和各任务独立调度，不连接游戏。"""

import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.island.production_round import (
    PRODUCTION_TASKS, IslandProductionRound, collection_config, invalidates_round,
    preparation_retry_deadline, run_production_task, select_unfinished_task,
)
from module.config.config import AzurLaneConfig, TaskEnd
from module.exception import GameNotRunningError, GamePageUnknownError, GameStuckError, GameTooManyClickError


class FakeConfig:
    Scheduler_FailureInterval = 120
    IslandPlan_TaskAlignment = 'hour'
    task_delay = AzurLaneConfig.task_delay

    def __init__(self, now):
        self.data = {task: {'Scheduler': {'Enable': task in ('IslandFarm', 'IslandJuuCoffee'),
                                       'NextRun': now + timedelta(hours=6)}} for task in PRODUCTION_TASKS}
        self.data['IslandFarm']['Scheduler']['NextRun'] = now
        self.data['IslandPlan'] = {'IslandPlan': {'TaskAlignment': 'hour'}}
        self.bound, self.modified, self.overridden = {}, {}, {}
        self.stop_event = None

    def bind(self, task):
        self.bound.clear()
        self.bound['task_name'] = getattr(task, 'command', task)

    def multi_set(self):
        return nullcontext()

    def cross_set(self, path, value):
        task, group, key = path.split('.')
        self.data[task][group][key] = value

    def cross_get(self, path, default=None):
        task, group, key = path.split('.')
        return self.data.get(task, {}).get(group, {}).get(key, default)


class ProductionRoundTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 6, 10)
        self.events = []
        self.config = FakeConfig(self.now)
        events = self.events

        class Runner:
            def __init__(self, config, device):
                self.config, self.device = config, device
                self.task = config.task.command
                self.posts = {'1': {'state': 'idle'}}

            def prepare_round(self):
                events.append(('harvest', self.task))

            def round_stock_items(self):
                return {'wheat', 'coffee_bean', 'iced_coffee'}

            def run(self):
                events.append(('run', self.task))
                self.island_round.consume({'coffee_bean': 2})
                self.config.cross_set(f'{self.task}.Scheduler.NextRun', datetime(2026, 11, 6, 16))

        self.runner_cls = Runner
        self.enterContext(patch('module.island.production_round.current_time', return_value=self.now))
        self.enterContext(patch('module.island.production_round.import_module',
                                return_value=SimpleNamespace(**{cls: Runner for _, cls in PRODUCTION_TASKS.values()})))
        self.scan = self.enterContext(patch('module.island.stock_probe.read_item_stocks',
                                          side_effect=lambda *a, **kw: self._scan()))
        self.enterContext(patch('module.island.production_planner.planner_enabled', return_value=False))
        self.owner = SimpleNamespace(config=self.config, device=object())

    def _scan(self):
        self.events.append(('scan', None))
        return {2000: 30, 2009: 10, 3005: 0}

    def test_harvest_all_before_one_inventory_pass_then_separate_tasks(self):
        run_production_task(self.owner, 'IslandFarm', self.runner_cls)
        session = self.owner._island_production_round
        self.assertEqual([e[0] for e in self.events], ['harvest'] * len(PRODUCTION_TASKS) + ['scan', 'run'])
        self.assertEqual(session.pending, {'IslandJuuCoffee'})
        self.assertEqual(session.inventory.get('coffee_bean'), 8)
        self.assertEqual(self.config.data['IslandJuuCoffee']['Scheduler']['NextRun'], self.now)
        self.assertFalse(self.config.data['IslandGrill']['Scheduler']['Enable'])
        self.assertEqual(self.config.data['IslandGrill']['Scheduler']['NextRun'], self.now + timedelta(hours=6))
        run_production_task(self.owner, 'IslandJuuCoffee', self.runner_cls)
        self.assertEqual(session.inventory.get('coffee_bean'), 6)
        self.scan.assert_called_once()
        self.assertIsNone(self.owner._island_production_round)

    def test_failed_preparation_defers_all_enabled_producers_and_preserves_later_schedule(self):
        self.config.data['IslandMineForest']['Scheduler'].update(Enable=True, NextRun=self.now)
        self.config.data['Commission'] = {'Scheduler': {'Enable': True, 'NextRun': self.now}}
        self.config.data['Research'] = {'Scheduler': {'Enable': True, 'NextRun': self.now}}
        before_disabled = dict(self.config.data['IslandGrill']['Scheduler'])
        failure_time = self.now + timedelta(minutes=5)
        deadline = self.now + timedelta(hours=3)
        error = GameStuckError('详情未安全关闭')
        self.scan.side_effect = error
        with patch('module.island.production_round.current_time', return_value=failure_time), \
                patch('module.config.config.current_time', return_value=failure_time):
            with self.assertRaises(GameStuckError) as raised:
                run_production_task(self.owner, 'IslandFarm', self.runner_cls)
        self.assertIs(raised.exception, error)
        self.assertIsNone(self.owner._island_production_round)
        self.assertEqual(self.owner._island_prepare_retry_at, deadline)
        for name in ('IslandFarm', 'IslandMineForest'):
            self.assertEqual(self.config.cross_get(f'{name}.Scheduler.NextRun'), deadline)
        self.assertEqual(self.config.cross_get('IslandJuuCoffee.Scheduler.NextRun'), self.now + timedelta(hours=6))
        self.assertEqual(self.config.data['IslandGrill']['Scheduler'], before_disabled)
        for name in ('Commission', 'Research'):
            self.assertEqual(self.config.cross_get(f'{name}.Scheduler.NextRun'), self.now)
            self.assertIsNone(preparation_retry_deadline(self.owner, name))
        self.assertIsNone(preparation_retry_deadline(self.owner, 'Restart'))
        self.assertEqual(preparation_retry_deadline(self.owner, 'IslandMineForest'), deadline)
        self.assertEqual(preparation_retry_deadline(self.owner, 'IslandJuuCoffee'), self.now + timedelta(hours=6))
        with patch('module.island.production_round.current_time', return_value=deadline):
            self.assertIsNone(preparation_retry_deadline(self.owner, 'IslandMineForest'))

    def test_harvest_and_detail_failures_share_cooldown(self):
        for error in (GameStuckError('收获未确认'), GameTooManyClickError('重复点击'),
                      GamePageUnknownError('无法确认页面')):
            with self.subTest(error=type(error).__name__), \
                    patch.object(self.runner_cls, 'prepare_round', side_effect=error), \
                    patch('module.island.production_round.delay_failed_preparation') as delay:
                with self.assertRaises(type(error)):
                    run_production_task(self.owner, 'IslandFarm', self.runner_cls)
                delay.assert_called_once_with(self.owner, 'IslandFarm')

    def test_manual_disabled_entry_with_later_schedule_keeps_failure_interval(self):
        for values in self.config.data.values():
            if 'Scheduler' in values:
                values['Scheduler'].update(Enable=False, NextRun=self.now + timedelta(days=1))
        self.scan.side_effect = GameStuckError('详情未安全关闭')
        with patch('module.config.config.current_time', return_value=self.now):
            with self.assertRaises(GameStuckError):
                run_production_task(self.owner, 'IslandFarm', self.runner_cls)
        self.assertEqual(self.owner._island_prepare_retry_at, self.now + timedelta(hours=2))
        self.assertFalse(self.config.cross_get('IslandFarm.Scheduler.Enable'))
        self.assertEqual(self.config.cross_get('IslandFarm.Scheduler.NextRun'), self.now + timedelta(days=1))

    def test_stop_and_device_recovery_do_not_delay_production_group(self):
        for error in (TaskEnd('用户停止'), GameNotRunningError('游戏未运行'), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__), \
                    patch.object(self.runner_cls, 'prepare_round', side_effect=error), \
                    patch('module.island.production_round.delay_failed_preparation') as delay:
                with self.assertRaises(type(error)):
                    run_production_task(self.owner, 'IslandFarm', self.runner_cls)
                delay.assert_not_called()
                self.assertIsNone(self.owner._island_production_round)

    def test_commission_research_interruptions_keep_stock_even_across_next_hour(self):
        run_production_task(self.owner, 'IslandFarm', self.runner_cls)
        session = self.owner._island_production_round
        for task in ('Commission', 'Research'):
            self.assertFalse(invalidates_round(task))
            # 与主任务入口相同的失效判断；非岛屿任务没有权改岛屿账。
            if invalidates_round(task):
                self.owner._island_production_round = None
        with patch('module.island.production_round.current_time', return_value=self.now + timedelta(hours=2)):
            run_production_task(self.owner, 'IslandJuuCoffee', self.runner_cls)
        self.scan.assert_called_once()
        self.assertEqual(self.events.count(('run', 'IslandFarm')), 1)
        self.assertEqual(session.inventory.get('coffee_bean'), 6)

    def test_mutating_island_tasks_and_restart_invalidate(self):
        for task in ('Restart', 'IslandDailyOrder', 'IslandBusiness', 'IslandCargoPreparation'):
            self.assertTrue(invalidates_round(task))

    def test_completed_task_due_again_cannot_replace_unfinished_round(self):
        run_production_task(self.owner, 'IslandFarm', self.runner_cls)
        session = self.owner._island_production_round
        self.config.pending_task = [SimpleNamespace(command=name)
                                    for name in ('IslandFarm', 'IslandJuuCoffee')]
        self.assertEqual(select_unfinished_task(self.config, 'Commission', session), 'Commission')
        self.assertEqual(select_unfinished_task(self.config, 'Research', session), 'Research')
        selected = select_unfinished_task(self.config, 'IslandFarm', session)
        self.assertEqual(selected, 'IslandJuuCoffee')
        run_production_task(self.owner, selected, self.runner_cls)
        self.scan.assert_called_once()
        self.assertEqual(self.events.count(('run', 'IslandFarm')), 1)

    def test_real_scheduler_binds_unfinished_task_identity(self):
        from alas import AzurLaneAutoScript
        run_production_task(self.owner, 'IslandFarm', self.runner_cls)
        script = AzurLaneAutoScript.__new__(AzurLaneAutoScript)
        script.config = self.config
        script._island_production_round = self.owner._island_production_round
        script._program_runtime = SimpleNamespace(mode='native', oil_control=SimpleNamespace(running_task=object()))
        script._get_next_task = Mock(return_value='IslandFarm')
        self.config.pending_task = [SimpleNamespace(command=name)
                                    for name in ('IslandFarm', 'IslandJuuCoffee')]
        self.assertEqual(script.get_next_task(), 'IslandJuuCoffee')
        self.assertEqual(script.config.task.command, 'IslandJuuCoffee')
        self.assertEqual(script.config.bound['task_name'], 'IslandJuuCoffee')

    def test_real_task_boundary_preserves_commission_research_but_invalidates_restart(self):
        from alas import AzurLaneAutoScript
        script = AzurLaneAutoScript.__new__(AzurLaneAutoScript)
        script.config_name = 'offline_round'
        session = script._island_production_round = object()
        script._run = Mock(return_value=True)
        with patch('module.statistics.resource_flow.task_session', return_value=nullcontext()), \
                patch('module.campaign.recovery.campaign_recovery_scope', return_value=nullcontext()):
            for command in ('commission', 'research'):
                self.assertTrue(script.run(command))
                self.assertIs(script._island_production_round, session)
            self.assertTrue(script.run('restart'))
        self.assertIsNone(script._island_production_round)

    def test_config_change_rebuilds_round_without_reusing_stocks(self):
        run_production_task(self.owner, 'IslandFarm', self.runner_cls)
        self.config.data['IslandPlan']['IslandPlan']['Season'] = 'winter'
        run_production_task(self.owner, 'IslandJuuCoffee', self.runner_cls)
        self.assertEqual(self.scan.call_count, 2)

    def test_failed_dispatch_drops_session(self):
        session = IslandProductionRound(self.config, object(), 'IslandFarm')
        session.runners['IslandFarm'].run = Mock(side_effect=GameStuckError('确认结果不明'))
        self.owner._island_production_round = session
        with self.assertRaises(GameStuckError):
            run_production_task(self.owner, 'IslandFarm', self.runner_cls)
        self.assertIsNone(self.owner._island_production_round)
        self.assertFalse(hasattr(self.owner, '_island_prepare_retry_at'))

    def test_collection_binding_never_reads_or_saves_user_config(self):
        config = AzurLaneConfig.__new__(AzurLaneConfig)
        config.data = {'IslandFarm': {'IslandFarm': {'DefaultCrop': 'wheat'}}}
        config.bound, config.modified, config.overridden = {}, {}, {}
        with patch.object(AzurLaneConfig, 'load') as read, patch.object(AzurLaneConfig, 'save') as save:
            child = collection_config(config, 'IslandFarm')
        self.assertEqual(child.task.command, 'IslandFarm')
        self.assertIsNot(child.data, config.data)
        self.assertEqual(config.bound, {})
        read.assert_not_called()
        save.assert_not_called()


if __name__ == '__main__':
    unittest.main()
