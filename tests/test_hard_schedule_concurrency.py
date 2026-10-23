"""困难任务运行期间修改维护设置的排程回归，仅操作 D 盘临时实例。"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from module.api.config_service import ConfigService
from module.api.protocol import ConfigChange
from module.config.config import AzurLaneConfig, TaskEnd
from module.hard.hard import CampaignHard
from tests.test_api import fixture


class HardScheduleConcurrencyTests(unittest.TestCase):
    """真实配置事务及加载保存配合模拟战斗，不访问设备或账号。"""

    def setUp(self):
        temporary_root = Path('D:/Codex/tmp')
        temporary_root.mkdir(parents=True, exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir=temporary_root)
        self.addCleanup(directory.cleanup)
        self.configs = ConfigService(fixture(directory.name))
        self.path = self.configs.path('testpilot')
        self.now = datetime(2026, 10, 22, 8)
        for target in ('module.hard.hard.current_time', 'module.config.config.current_time',
                       'module.config.utils.current_time', 'module.api.config_service.current_time'):
            self.enterContext(patch(target, side_effect=lambda: self.now))
        for target in ('module.hard.hard.filepath_config', 'module.config.config.filepath_config',
                       'module.config.config_updater.filepath_config'):
            self.enterContext(patch(target, return_value=str(self.path)))
        for target in ('module.hard.hard.server_time_offset', 'module.config.utils.server_time_offset'):
            self.enterContext(patch(target, return_value=timedelta()))
        self.enterContext(patch('module.hard.hard.logger'))
        self.ocr = self.enterContext(patch('module.hard.hard.OCR_HARD_REMAIN'))
        self.ocr.ocr.return_value = 3

    def runner(self, *, run_at_18=True, weekday='fri'):
        data = self.configs.read_json(self.path)
        data['Hard']['Hard']['RunAt18'] = run_at_18
        data['Hard']['Scheduler'].update(Enable=True, NextRun='2026-10-22 00:00:00')
        data['Exercise']['Exercise'].update(MaintenanceAdvance=False, MaintenanceWeekday=weekday)
        data['Exercise']['Scheduler']['Enable'] = False
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        config = AzurLaneConfig('testpilot', task='Hard')
        config.task_call = Mock()
        runner = object.__new__(CampaignHard)
        runner.config = config
        runner.device = Mock()
        runner.campaign = Mock()
        runner.load_campaign = Mock()
        return runner

    def save(self, *changes):
        return self.configs.patch('testpilot', None, [
            ConfigChange(path=path, value=value) for path, value in changes
        ])

    def run_with_edit(self, runner, *changes):
        """把前端保存插在第一场战斗内，保留真实运行器加载和排程写入。"""
        during_battle = []

        def first_battle():
            if not during_battle:
                result = self.save(*changes)
                during_battle.append(result['values'])

        runner.campaign.run.side_effect = first_battle
        runner.run()
        self.assertEqual(runner.campaign.run.call_count, 3)
        runner.campaign.ensure_auto_search_exit.assert_called_once_with()
        runner.config.task_call.assert_called_once_with('Reward', force_call=False)
        self.assertEqual(len(during_battle), 1)
        return during_battle[0], self.configs.get('testpilot')['values']

    def test_running_weekday_change_removes_old_tomorrow_evening(self):
        during, after = self.run_with_edit(self.runner(weekday='fri'),
                                          ('Exercise.Exercise.MaintenanceWeekday', 'sat'))
        # 已到期时间不后移；收尾仍须按新的星期重算，不能沿用入口的周五。
        self.assertEqual(during['Hard']['Scheduler']['NextRun'], '2026-10-22 00:00:00')
        self.assertEqual(after['Exercise']['Exercise']['MaintenanceWeekday'], 'sat')
        self.assertEqual(after['Hard']['Scheduler']['NextRun'], '2026-10-23 00:00:00')

    def test_running_weekday_change_adds_new_tomorrow_evening(self):
        _, after = self.run_with_edit(self.runner(weekday='sat'),
                                     ('Exercise.Exercise.MaintenanceWeekday', 'fri'))
        self.assertEqual(after['Exercise']['Exercise']['MaintenanceWeekday'], 'fri')
        self.assertEqual(after['Hard']['Scheduler']['NextRun'], '2026-10-23 18:00:00')

    def test_disabling_evening_during_run_uses_next_midnight(self):
        during, after = self.run_with_edit(self.runner(run_at_18=True), ('Hard.Hard.RunAt18', False))
        self.assertEqual(during['Hard']['Scheduler']['NextRun'], '2026-10-22 00:00:00')
        self.assertFalse(after['Hard']['Hard']['RunAt18'])
        self.assertEqual(after['Hard']['Scheduler']['NextRun'], '2026-10-23 00:00:00')

    def test_enabling_evening_during_run_uses_new_maintenance_day(self):
        during, after = self.run_with_edit(self.runner(run_at_18=False), ('Hard.Hard.RunAt18', True))
        self.assertEqual(during['Hard']['Scheduler']['NextRun'], '2026-10-22 00:00:00')
        self.assertTrue(after['Hard']['Hard']['RunAt18'])
        self.assertEqual(after['Hard']['Scheduler']['NextRun'], '2026-10-23 18:00:00')

    def test_explicit_next_run_saved_during_battle_is_preserved(self):
        manual = '2026-10-25 06:30:00'
        _, after = self.run_with_edit(self.runner(), ('Hard.Scheduler.NextRun', manual))
        self.assertEqual(after['Hard']['Scheduler']['NextRun'], manual)

    def test_explicit_next_run_and_weekday_saved_together_are_preserved(self):
        manual = '2026-10-25 06:30:00'
        _, after = self.run_with_edit(self.runner(),
                                     ('Exercise.Exercise.MaintenanceWeekday', 'sat'),
                                     ('Hard.Scheduler.NextRun', manual))
        self.assertEqual(after['Exercise']['Exercise']['MaintenanceWeekday'], 'sat')
        self.assertEqual(after['Hard']['Scheduler']['NextRun'], manual)

    def test_entry_gate_refreshes_weekday_changed_after_worker_loaded(self):
        runner = self.runner(weekday='fri')
        self.save(('Exercise.Exercise.MaintenanceWeekday', 'thu'))
        with self.assertRaises(TaskEnd):
            runner.run()
        self.assertEqual(self.configs.get('testpilot')['values']['Hard']['Scheduler']['NextRun'],
                         '2026-10-22 18:00:00')
        runner.load_campaign.assert_not_called()
        runner.device.screenshot.assert_not_called()
        runner.campaign.run.assert_not_called()
        runner.config.task_call.assert_not_called()

    def test_entry_gate_refreshes_evening_switch_changed_after_worker_loaded(self):
        runner = self.runner(run_at_18=False, weekday='thu')
        self.save(('Hard.Hard.RunAt18', True))
        with self.assertRaises(TaskEnd):
            runner.run()
        self.assertEqual(self.configs.get('testpilot')['values']['Hard']['Scheduler']['NextRun'],
                         '2026-10-22 18:00:00')
        runner.load_campaign.assert_not_called()
        runner.device.screenshot.assert_not_called()
        runner.campaign.run.assert_not_called()
        runner.config.task_call.assert_not_called()


if __name__ == '__main__':
    unittest.main()
