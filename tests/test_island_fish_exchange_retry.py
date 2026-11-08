"""鱼肉状态未知时延期岛屿生产，保留一次既有恢复，不连接游戏或真实配置。"""

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.config.config import AzurLaneConfig, TaskEnd
from module.exception import (
    GameNotRunningError, GamePageUnknownError, GameStuckError, GameTooManyClickError,
    IslandFishExchangeError,
)
from module.island.production_round import (
    IslandProductionRound, delay_failed_preparation, preparation_retry_deadline,
    round_config_signature, run_production_task,
)
from module.island.round_inventory import IslandRoundInventory
from tests.test_island_production_round import FakeConfig


class FishExchangeRetryTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 9, 5, 37)
        self.deadline = datetime(2026, 11, 9, 8)
        self.config = FakeConfig(self.now)
        self.config.data['IslandRancher']['Scheduler'].update(Enable=True, NextRun=self.now)
        self.config.data['IslandFarm']['Scheduler'].update(Enable=True, NextRun=self.now)
        self.config.data['Commission'] = {'Scheduler': {'Enable': True, 'NextRun': self.now}}
        self.config.data['Research'] = {'Scheduler': {'Enable': True, 'NextRun': self.now}}
        self.owner = SimpleNamespace(config=self.config, device=object())
        self.enterContext(patch('module.island.production_round.current_time', return_value=self.now))
        self.enterContext(patch('module.config.config.current_time', return_value=self.now))
        self.log = self.enterContext(patch('module.island.production_round.logger'))

    def prepared(self, error, task='IslandRancher'):
        session = IslandProductionRound.__new__(IslandProductionRound)
        session.signature = round_config_signature(self.config)
        session.started_at = self.now
        session.ready = True
        session.pending = {task, 'IslandFarm', 'IslandJuuCoffee'}
        session.inventory = IslandRoundInventory()
        session.inventory.observe(2522, 10)
        session.runners = {task: SimpleNamespace(run=Mock(side_effect=error))}
        self.owner._island_production_round = session
        return session

    def test_fish_error_discards_ledger_before_deferral_and_preserves_exception(self):
        error = IslandFishExchangeError('未能确认鱼肉分组')
        self.assertIsInstance(error, GameStuckError)
        session = self.prepared(error)
        later = self.config.cross_get('IslandJuuCoffee.Scheduler.NextRun')
        disabled = dict(self.config.data['IslandGrill']['Scheduler'])

        def postpone(**kwargs):
            self.assertIsNone(self.owner._island_production_round)
            return AzurLaneConfig.task_delay(self.config, **kwargs)

        self.config.task_delay = Mock(side_effect=postpone)
        with self.assertRaises(IslandFishExchangeError) as raised:
            run_production_task(self.owner, 'IslandRancher', object)
        self.assertIs(raised.exception, error)
        self.assertIsNone(self.owner._island_production_round)
        self.assertIn('IslandRancher', session.pending)
        self.assertEqual(self.owner._island_prepare_retry_at, self.deadline)
        for task in ('IslandRancher', 'IslandFarm'):
            self.assertEqual(self.config.cross_get(f'{task}.Scheduler.NextRun'), self.deadline)
        self.assertEqual(self.config.cross_get('IslandJuuCoffee.Scheduler.NextRun'), later)
        self.assertEqual(self.config.data['IslandGrill']['Scheduler'], disabled)
        self.assertEqual(self.config.task_delay.call_count, 2)
        self.assertIn('鱼肉兑换未确认', self.log.warning.call_args.args[0])

    def test_retry_deadline_survives_game_recovery_and_other_task_success(self):
        self.prepared(IslandFishExchangeError('确认结果未知'))
        with self.assertRaises(IslandFishExchangeError):
            run_production_task(self.owner, 'IslandRancher', object)
        # 游戏恢复和其他任务成功不会重建宿主，也不能清掉岛屿专项冷却。
        self.owner._island_production_round = None
        self.owner.consecutive_game_stuck = 0
        for task in ('IslandRancher', 'IslandFarm'):
            self.assertEqual(preparation_retry_deadline(self.owner, task), self.deadline)
        self.assertEqual(preparation_retry_deadline(self.owner, 'IslandJuuCoffee'),
                         self.now + timedelta(hours=6))
        for task in ('Commission', 'Research', 'Restart'):
            self.assertIsNone(preparation_retry_deadline(self.owner, task))
        for task in ('Commission', 'Research'):
            self.assertEqual(self.config.cross_get(f'{task}.Scheduler.NextRun'), self.now)

    def test_manual_disabled_entry_is_deferred_without_enabling_it(self):
        task = 'IslandRestaurant'
        self.config.data[task]['Scheduler'].update(Enable=False, NextRun=self.now)
        self.prepared(IslandFishExchangeError('兑换到账未知'), task=task)
        with self.assertRaises(IslandFishExchangeError):
            run_production_task(self.owner, task, object)
        self.assertFalse(self.config.cross_get(f'{task}.Scheduler.Enable'))
        self.assertEqual(self.config.cross_get(f'{task}.Scheduler.NextRun'), self.deadline)

    def test_other_dispatch_errors_do_not_gain_fish_exchange_cooldown(self):
        for error in (GameStuckError('普通派遣失败'), GameTooManyClickError('点击过多'),
                      GamePageUnknownError('页面未知'), GameNotRunningError('游戏未运行'), TaskEnd('停止')):
            with self.subTest(error=type(error).__name__):
                self.prepared(error)
                with self.assertRaises(type(error)) as raised:
                    run_production_task(self.owner, 'IslandRancher', object)
                self.assertIs(raised.exception, error)
                self.assertIsNone(self.owner._island_production_round)
                self.assertFalse(hasattr(self.owner, '_island_prepare_retry_at'))
                self.assertEqual(self.config.cross_get('IslandRancher.Scheduler.NextRun'), self.now)

    def test_preparation_error_is_deferred_once_through_original_path(self):
        error = IslandFishExchangeError('准备失败')
        with patch.object(IslandProductionRound, '_prepare', side_effect=error), \
                patch('module.island.production_round.delay_failed_preparation',
                      wraps=delay_failed_preparation) as delay:
            with self.assertRaises(IslandFishExchangeError) as raised:
                run_production_task(self.owner, 'IslandRancher', object)
        self.assertIs(raised.exception, error)
        delay.assert_called_once_with(self.owner, 'IslandRancher')
        self.assertIsNone(self.owner._island_production_round)
        self.assertEqual(self.owner._island_prepare_retry_at, self.deadline)

    def test_retry_after_deadline_builds_new_inventory(self):
        previous = self.prepared(IslandFishExchangeError('到账未知'))
        with self.assertRaises(IslandFishExchangeError):
            run_production_task(self.owner, 'IslandRancher', object)
        rebuilt = []

        def prepare(session, config, device, task):
            rebuilt.append(session)
            session.inventory.observe(2522, 25)
            session.pending = {task}
            session.runners = {task: SimpleNamespace(run=Mock())}
            session.ready = True

        with patch('module.island.production_round.current_time', return_value=self.deadline), \
                patch.object(IslandProductionRound, '_prepare', new=prepare):
            self.assertIsNone(preparation_retry_deadline(self.owner, 'IslandRancher'))
            run_production_task(self.owner, 'IslandRancher', object)
        self.assertEqual(len(rebuilt), 1)
        self.assertIsNot(rebuilt[0], previous)
        self.assertIsNot(rebuilt[0].inventory, previous.inventory)
        self.assertEqual(rebuilt[0].inventory.get(2522), 25)
        self.assertIsNone(self.owner._island_production_round)


if __name__ == '__main__':
    unittest.main()
