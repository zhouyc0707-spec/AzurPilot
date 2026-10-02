"""岛屿任务对齐模式、旧配置兼容与全局设置保存的回归测试。"""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from module.api.config_service import ConfigService
from module.api.protocol import ApiError, ConfigChange
from module.config.config import AzurLaneConfig, name_to_function
from module.config.config_updater import ConfigUpdater


ALIGNMENT_PATH = 'IslandPlan.IslandPlan.TaskAlignment'


class IslandTaskAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 3, 10, 11, 17)
        clock = patch('module.config.config.current_time', return_value=self.now)
        clock.start()
        self.addCleanup(clock.stop)
        # 使用真实 task_delay 和待保存队列，关闭自动写盘，避免触碰用户配置。
        self.config = AzurLaneConfig.__new__(AzurLaneConfig)
        self.config.bound = {}
        self.config.modified = {}
        self.config.auto_update = False
        self.config.data = {'IslandPlan': {'IslandPlan': {'TaskAlignment': 'half_hour'}}}
        self.config.task = name_to_function('IslandFarm')

    def mode(self, value):
        self.config.data['IslandPlan']['IslandPlan']['TaskAlignment'] = value

    def scheduled(self, task='IslandFarm'):
        return self.config.modified[f'{task}.Scheduler.NextRun']

    def test_three_modes_preserve_delay_or_round_up(self):
        for mode, expected in (
            ('half_hour', datetime(2026, 10, 3, 10, 30)),
            ('hour', datetime(2026, 10, 3, 11)),
            ('disabled', datetime(2026, 10, 3, 10, 18, 17)),
        ):
            with self.subTest(mode=mode):
                self.mode(mode)
                self.config.task_delay(minute=7)
                self.assertEqual(self.scheduled(), expected)

    def test_config_without_new_setting_keeps_half_hour_behavior(self):
        self.config.data = {}
        self.config.task_delay(minute=7)
        self.assertEqual(self.scheduled(), datetime(2026, 10, 3, 10, 30))

    def test_all_schedulable_island_tasks_use_global_setting(self):
        self.mode('hour')
        args = ConfigService().args
        tasks = [name for name, groups in args.items()
                 if name.startswith('Island') and 'Scheduler' in groups]
        self.assertTrue(tasks)
        for task in tasks:
            with self.subTest(task=task):
                self.config.task = name_to_function(task)
                self.config.task_delay(minute=7)
                self.assertEqual(self.scheduled(task), datetime(2026, 10, 3, 11))

    def test_cross_task_delay_uses_destination_task_and_global_setting(self):
        self.mode('hour')
        self.config.task = name_to_function('Research')
        self.config.task_delay(minute=7, task='IslandRancher')
        self.assertEqual(self.scheduled('IslandRancher'), datetime(2026, 10, 3, 11))
        self.assertNotIn('Research.Scheduler.NextRun', self.config.modified)

    def test_non_island_tasks_keep_their_original_time(self):
        for mode in ('half_hour', 'hour', 'disabled'):
            with self.subTest(mode=mode):
                self.mode(mode)
                self.config.task_delay(minute=7, task='Commission')
                self.assertEqual(self.scheduled('Commission'), datetime(2026, 10, 3, 10, 18, 17))

    def test_boundaries_and_year_rollover(self):
        for mode, target, expected in (
            ('half_hour', '2026-10-03 10:00:00', '2026-10-03 10:00:00'),
            ('half_hour', '2026-10-03 10:30:00', '2026-10-03 10:30:00'),
            ('half_hour', '2026-10-03 10:30:01', '2026-10-03 11:00:00'),
            ('hour', '2026-10-03 10:00:00', '2026-10-03 10:00:00'),
            ('hour', '2026-10-03 10:00:01', '2026-10-03 11:00:00'),
            ('hour', '2026-10-03 10:30:00', '2026-10-03 11:00:00'),
            ('half_hour', '2026-12-31 23:59:59', '2027-01-01 00:00:00'),
            ('hour', '2026-12-31 23:59:59', '2027-01-01 00:00:00'),
            ('disabled', '2026-12-31 23:59:59', '2026-12-31 23:59:59'),
        ):
            with self.subTest(mode=mode, target=target):
                self.mode(mode)
                self.config.task_delay(target=target)
                self.assertEqual(self.scheduled(), datetime.fromisoformat(expected))

    def test_success_failure_and_server_update_use_same_alignment(self):
        self.mode('hour')
        self.config.Scheduler_SuccessInterval = 7
        self.config.Scheduler_FailureInterval = 7
        for success in (True, False):
            with self.subTest(success=success):
                self.config.task_delay(success=success)
                self.assertEqual(self.scheduled(), datetime(2026, 10, 3, 11))
        with patch('module.config.config.get_server_next_update',
                   return_value=datetime(2026, 10, 3, 12, 15)):
            self.config.task_delay(server_update='12:15')
        self.assertEqual(self.scheduled(), datetime(2026, 10, 3, 13))

    def test_multiple_delay_sources_choose_earliest_then_align(self):
        self.mode('hour')
        self.config.task_delay(minute=7, target='2026-10-03 12:15:00')
        self.assertEqual(self.scheduled(), datetime(2026, 10, 3, 11))


class IslandTaskAlignmentConfigTests(unittest.TestCase):
    def test_old_config_gets_default_and_keeps_existing_global_settings(self):
        old = {'IslandPlan': {'IslandPlan': {'Season': 'winter'}}}
        updated = ConfigUpdater().config_update(old)
        self.assertEqual(updated['IslandPlan']['IslandPlan'],
                         {'Season': 'winter', 'TaskAlignment': 'half_hour'})
        self.assertNotIn('TaskAlignment', old['IslandPlan']['IslandPlan'])

    def test_saved_modes_are_preserved_on_config_reload(self):
        for mode in ('half_hour', 'hour', 'disabled'):
            with self.subTest(mode=mode):
                old = {'IslandPlan': {'IslandPlan': {'Season': 'winter', 'TaskAlignment': mode}}}
                updated = ConfigUpdater().config_update(old)
                self.assertEqual(updated['IslandPlan']['IslandPlan'], old['IslandPlan']['IslandPlan'])

    def test_real_config_service_saves_choice_without_rescheduling(self):
        with tempfile.TemporaryDirectory() as directory:
            configs = ConfigService()
            configs.directory = Path(directory)
            path = configs.directory / 'alignment.json'
            template = json.loads(Path('config/template.json').read_text(encoding='utf-8'))
            template['IslandPlan']['IslandPlan']['Season'] = 'winter'
            template['IslandFarm']['Scheduler']['NextRun'] = '2026-10-03 10:18:17'
            path.write_text(json.dumps(template, ensure_ascii=False), encoding='utf-8')
            for mode in ('hour', 'disabled', 'half_hour'):
                with self.subTest(mode=mode):
                    configs.patch('alignment', None, [ConfigChange(path=ALIGNMENT_PATH, value=mode)])
                    saved = json.loads(path.read_text(encoding='utf-8'))
                    self.assertEqual(saved['IslandPlan']['IslandPlan']['TaskAlignment'], mode)
                    self.assertEqual(saved['IslandPlan']['IslandPlan']['Season'], 'winter')
                    self.assertEqual(saved['IslandFarm'], template['IslandFarm'])
            before = path.read_bytes()
            with self.assertRaises(ApiError):
                configs.patch('alignment', None, [ConfigChange(path=ALIGNMENT_PATH, value='invalid')])
            self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
