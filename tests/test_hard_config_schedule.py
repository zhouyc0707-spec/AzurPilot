"""主线困难图维护星期设置保存后的重排回归，仅修改临时实例。"""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from deploy.atomic import atomic_write
from module.api.config_service import ConfigService
from module.api.protocol import ApiError, ConfigChange
from tests.test_api import fixture


class HardConfigScheduleTests(unittest.TestCase):
    """经真实配置保存事务确认门禁变更能够及时生效，不执行游戏操作。"""

    def setUp(self):
        temporary_root = Path('D:/Codex/tmp')
        temporary_root.mkdir(parents=True, exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir=temporary_root)
        self.addCleanup(directory.cleanup)
        self.configs = ConfigService(fixture(directory.name))
        self.path = self.configs.path('testpilot')
        self.now = datetime(2026, 10, 22, 8, 30, 15, 123456)
        self.future = '2026-10-22 18:00:00'
        self.woken = '2026-10-22 08:30:15'
        clock = patch('module.api.config_service.current_time', return_value=self.now)
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        self.reset_config()

    def reset_config(self, *, hard_enabled=True, run_at_18=True, hard_next_run=None,
                     exercise_enabled=False, advance=True):
        data = self.configs.read_json(self.path)
        data['Hard']['Hard']['RunAt18'] = run_at_18
        data['Hard']['Scheduler'].update(Enable=hard_enabled, NextRun=hard_next_run or self.future)
        data['Exercise']['Exercise'].update(MaintenanceAdvance=advance, MaintenanceWeekday='thu')
        data['Exercise']['Scheduler'].update(Enable=exercise_enabled, NextRun=self.future)
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        self.clock.reset_mock()

    def save(self, *changes):
        return self.configs.patch('testpilot', None, [
            ConfigChange(path=path, value=value) for path, value in changes
        ])

    def scheduler(self, task='Hard'):
        return self.configs.read_json(self.path)[task]['Scheduler']

    def test_weekday_change_wakes_hard_even_when_exercise_and_advance_are_disabled(self):
        self.reset_config(exercise_enabled=False, advance=False)
        exercise_before = self.scheduler('Exercise')
        result = self.save(('Exercise.Exercise.MaintenanceWeekday', 'fri'))
        self.assertEqual(self.scheduler()['NextRun'], self.woken)
        self.assertTrue(self.scheduler()['Enable'])
        self.assertEqual(self.scheduler('Exercise'), exercise_before)
        self.assertEqual(result['values']['Exercise']['Exercise']['MaintenanceWeekday'], 'fri')
        self.clock.assert_called_once_with()

    def test_changing_run_at_18_in_either_direction_wakes_hard(self):
        for previous, target in ((False, True), (True, False)):
            with self.subTest(previous=previous, target=target):
                self.reset_config(run_at_18=previous)
                result = self.save(('Hard.Hard.RunAt18', target))
                self.assertEqual(self.scheduler()['NextRun'], self.woken)
                self.assertEqual(result['values']['Hard']['Hard']['RunAt18'], target)
                self.clock.assert_called_once_with()

    def test_weekday_change_does_not_wake_hard_when_run_at_18_is_disabled(self):
        self.reset_config(run_at_18=False)
        before = self.scheduler()
        self.save(('Exercise.Exercise.MaintenanceWeekday', 'fri'))
        self.assertEqual(self.scheduler(), before)
        self.clock.assert_not_called()

    def test_changing_advance_alone_does_not_wake_hard(self):
        for previous, target in ((False, True), (True, False)):
            with self.subTest(previous=previous, target=target):
                self.reset_config(advance=previous)
                before = self.scheduler()
                self.save(('Exercise.Exercise.MaintenanceAdvance', target))
                self.assertEqual(self.scheduler(), before)
                self.clock.assert_not_called()

    def test_disabled_hard_is_neither_enabled_nor_rescheduled(self):
        self.reset_config(hard_enabled=False)
        before = self.scheduler()
        self.save(('Exercise.Exercise.MaintenanceWeekday', 'fri'), ('Hard.Hard.RunAt18', False))
        self.assertEqual(self.scheduler(), before)
        self.clock.assert_not_called()

    def test_disabling_hard_in_same_batch_preserves_its_schedule(self):
        self.save(('Exercise.Exercise.MaintenanceWeekday', 'fri'), ('Hard.Scheduler.Enable', False))
        self.assertFalse(self.scheduler()['Enable'])
        self.assertEqual(self.scheduler()['NextRun'], self.future)
        self.clock.assert_not_called()

    def test_enabling_hard_in_same_batch_uses_final_enable_value(self):
        self.reset_config(hard_enabled=False)
        self.save(('Exercise.Exercise.MaintenanceWeekday', 'fri'), ('Hard.Scheduler.Enable', True))
        self.assertTrue(self.scheduler()['Enable'])
        self.assertEqual(self.scheduler()['NextRun'], self.woken)
        self.clock.assert_called_once_with()

    def test_saving_identical_values_does_not_wake_hard(self):
        before = self.scheduler()
        self.save(('Hard.Hard.RunAt18', True), ('Exercise.Exercise.MaintenanceWeekday', 'thu'))
        self.assertEqual(self.scheduler(), before)
        self.clock.assert_not_called()

    def test_missing_legacy_run_at_18_uses_false_default_without_spurious_wake(self):
        data = self.configs.read_json(self.path)
        data['Hard']['Hard'].pop('RunAt18')
        self.path.write_text(json.dumps(data), encoding='utf-8')
        self.save(('Hard.Hard.RunAt18', False), ('Exercise.Exercise.MaintenanceWeekday', 'fri'))
        self.assertEqual(self.scheduler()['NextRun'], self.future)
        self.clock.assert_not_called()

    def test_explicit_hard_next_run_wins_regardless_of_change_order(self):
        manual = ('Hard.Scheduler.NextRun', '2026-10-23 12:00:00')
        weekday = ('Exercise.Exercise.MaintenanceWeekday', 'fri')
        for changes in ((manual, weekday), (weekday, manual)):
            with self.subTest(changes=changes):
                self.reset_config()
                self.save(*changes)
                self.assertEqual(self.scheduler()['NextRun'], manual[1])
                self.clock.assert_not_called()

    def test_already_due_hard_is_not_moved_later(self):
        for scheduled in ('2026-10-22 00:00:00', self.woken):
            with self.subTest(scheduled=scheduled):
                self.reset_config(hard_next_run=scheduled)
                self.save(('Exercise.Exercise.MaintenanceWeekday', 'fri'))
                self.assertEqual(self.scheduler()['NextRun'], scheduled)

    def test_invalid_saved_next_run_is_repaired_when_gate_changes(self):
        self.reset_config(hard_next_run='invalid old value')
        self.save(('Hard.Hard.RunAt18', False))
        self.assertEqual(self.scheduler()['NextRun'], self.woken)

    def test_multiple_gate_changes_use_final_values_and_share_one_write(self):
        weekday = ('Exercise.Exercise.MaintenanceWeekday', 'fri')
        run_at_18 = ('Hard.Hard.RunAt18', True)
        for changes in ((weekday, run_at_18), (run_at_18, weekday)):
            with self.subTest(changes=changes):
                self.reset_config(run_at_18=False)
                before = self.configs.read_json(self.path)
                with patch('module.api.config_service.atomic_write', wraps=atomic_write) as write:
                    self.save(*changes)
                write.assert_called_once()
                self.clock.assert_called_once_with()
                after = self.configs.read_json(self.path)
                self.assertEqual(after['Hard']['Scheduler']['NextRun'], self.woken)
                self.assertTrue(after['Hard']['Hard']['RunAt18'])
                self.assertEqual(after['Exercise']['Exercise']['MaintenanceWeekday'], 'fri')
                for task, groups in before.items():
                    if task != 'Hard' and isinstance(groups, dict) and 'Scheduler' in groups:
                        self.assertEqual(after[task]['Scheduler'], groups['Scheduler'], task)

    def test_hard_and_exercise_wake_in_same_atomic_write_and_share_one_clock(self):
        self.reset_config(exercise_enabled=True)
        before = self.configs.read_json(self.path)
        with patch('module.api.config_service.atomic_write', wraps=atomic_write) as write:
            self.save(('Exercise.Exercise.MaintenanceAdvance', False),
                      ('Exercise.Exercise.MaintenanceWeekday', 'fri'),
                      ('Hard.Hard.RunAt18', False))
        write.assert_called_once()
        self.clock.assert_called_once_with()
        after = self.configs.read_json(self.path)
        self.assertEqual(after['Hard']['Scheduler']['NextRun'], self.woken)
        self.assertEqual(after['Exercise']['Scheduler']['NextRun'], self.woken)
        for task, groups in before.items():
            if task not in ('Hard', 'Exercise') and isinstance(groups, dict) and 'Scheduler' in groups:
                self.assertEqual(after[task]['Scheduler'], groups['Scheduler'], task)

    def test_manual_next_run_for_one_task_does_not_prevent_other_task_waking(self):
        for manual_task, woken_task in (('Hard', 'Exercise'), ('Exercise', 'Hard')):
            with self.subTest(manual_task=manual_task):
                self.reset_config(exercise_enabled=True)
                manual = '2026-10-23 12:00:00'
                self.save((f'{manual_task}.Scheduler.NextRun', manual),
                          ('Exercise.Exercise.MaintenanceWeekday', 'fri'))
                self.assertEqual(self.scheduler(manual_task)['NextRun'], manual)
                self.assertEqual(self.scheduler(woken_task)['NextRun'], self.woken)
                self.clock.assert_called_once_with()

    def test_invalid_weekday_rejects_entire_batch_without_write(self):
        original = self.path.read_bytes()
        with patch('module.api.config_service.atomic_write', wraps=atomic_write) as write:
            with self.assertRaises(ApiError) as caught:
                self.save(('Hard.Hard.RunAt18', False),
                          ('Exercise.Exercise.MaintenanceWeekday', 'holiday'))
        self.assertEqual(caught.exception.code, 'INVALID_PARAMS')
        write.assert_not_called()
        self.clock.assert_not_called()
        self.assertEqual(self.path.read_bytes(), original)

    def test_unrelated_setting_does_not_wake_hard_or_exercise(self):
        self.reset_config(exercise_enabled=True)
        before = self.configs.read_json(self.path)
        self.save(('Hard.Hard.HardStage', '12-4'))
        after = self.configs.read_json(self.path)
        for task, groups in before.items():
            if isinstance(groups, dict) and 'Scheduler' in groups:
                self.assertEqual(after[task]['Scheduler'], groups['Scheduler'], task)
        self.clock.assert_not_called()


if __name__ == '__main__':
    unittest.main()
