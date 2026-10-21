"""演习维护设置保存后的重排回归，仅修改临时实例。"""

import json
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from deploy.atomic import atomic_write
from module.api.config_service import ConfigService
from module.api.protocol import ApiError, ConfigChange
from tests.test_api import fixture


class ExerciseConfigScheduleTests(unittest.TestCase):
    """通过真实配置保存事务验证唤醒，不执行任何游戏操作。"""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.configs = ConfigService(fixture(directory.name))
        self.path = self.configs.path('testpilot')
        self.now = datetime(2026, 10, 22, 8, 30, 15, 123456)
        self.future = '2026-10-22 11:00:00'
        clock = patch('module.api.config_service.current_time', return_value=self.now)
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        self.reset_config()

    def reset_config(self, *, enabled=True, next_run=None):
        data = self.configs.read_json(self.path)
        data['Exercise']['Exercise'].update(MaintenanceAdvance=True, MaintenanceWeekday='thu')
        data['Exercise']['Scheduler'].update(Enable=enabled, NextRun=next_run or self.future)
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        self.clock.reset_mock()

    def save(self, *changes):
        return self.configs.patch('testpilot', None, [
            ConfigChange(path=path, value=value) for path, value in changes
        ])

    def scheduler(self):
        return self.configs.read_json(self.path)['Exercise']['Scheduler']

    def test_change_weekday_wakes_enabled_exercise_instead_of_waiting_for_old_schedule(self):
        result = self.save(('Exercise.Exercise.MaintenanceWeekday', 'fri'))
        self.assertEqual(self.scheduler()['NextRun'], '2026-10-22 08:30:15')
        self.assertTrue(self.scheduler()['Enable'])
        self.assertEqual(result['values']['Exercise']['Exercise']['MaintenanceWeekday'], 'fri')
        self.clock.assert_called_once_with()

    def test_disabling_special_flow_wakes_exercise_for_its_normal_time_gate(self):
        self.save(('Exercise.Exercise.MaintenanceAdvance', False))
        self.assertEqual(self.scheduler()['NextRun'], '2026-10-22 08:30:15')
        self.assertFalse(self.configs.read_json(self.path)['Exercise']['Exercise']['MaintenanceAdvance'])

    def test_enabling_special_flow_wakes_enabled_exercise(self):
        data = self.configs.read_json(self.path)
        data['Exercise']['Exercise']['MaintenanceAdvance'] = False
        self.path.write_text(json.dumps(data), encoding='utf-8')
        self.save(('Exercise.Exercise.MaintenanceAdvance', True))
        self.assertEqual(self.scheduler()['NextRun'], '2026-10-22 08:30:15')

    def test_disabled_exercise_is_neither_enabled_nor_rescheduled(self):
        self.reset_config(enabled=False)
        self.save(('Exercise.Exercise.MaintenanceWeekday', 'fri'))
        self.assertEqual(self.scheduler()['NextRun'], self.future)
        self.assertFalse(self.scheduler()['Enable'])
        self.clock.assert_not_called()

    def test_disabling_task_in_same_batch_keeps_saved_time(self):
        self.save(('Exercise.Exercise.MaintenanceWeekday', 'fri'),
                  ('Exercise.Scheduler.Enable', False))
        self.assertEqual(self.scheduler()['NextRun'], self.future)
        self.assertFalse(self.scheduler()['Enable'])
        self.clock.assert_not_called()

    def test_saving_identical_values_does_not_wake_exercise(self):
        self.save(('Exercise.Exercise.MaintenanceWeekday', 'thu'),
                  ('Exercise.Exercise.MaintenanceAdvance', True))
        self.assertEqual(self.scheduler()['NextRun'], self.future)
        self.clock.assert_not_called()

    def test_missing_legacy_fields_use_defaults_without_treating_them_as_change(self):
        data = self.configs.read_json(self.path)
        data['Exercise']['Exercise'].pop('MaintenanceAdvance')
        data['Exercise']['Exercise'].pop('MaintenanceWeekday')
        self.path.write_text(json.dumps(data), encoding='utf-8')
        self.save(('Exercise.Exercise.MaintenanceAdvance', True),
                  ('Exercise.Exercise.MaintenanceWeekday', 'thu'))
        self.assertEqual(self.scheduler()['NextRun'], self.future)
        self.clock.assert_not_called()

    def test_explicit_next_run_wins_regardless_of_change_order(self):
        manual = ('Exercise.Scheduler.NextRun', '2026-10-22 10:00:00')
        weekday = ('Exercise.Exercise.MaintenanceWeekday', 'fri')
        for changes in ((manual, weekday), (weekday, manual)):
            with self.subTest(changes=changes):
                self.reset_config()
                self.save(*changes)
                self.assertEqual(self.scheduler()['NextRun'], manual[1])
                self.clock.assert_not_called()

    def test_an_already_pending_task_is_not_moved_later(self):
        for scheduled in ('2026-10-22 07:00:00', '2026-10-22 08:30:15'):
            with self.subTest(scheduled=scheduled):
                self.reset_config(next_run=scheduled)
                self.save(('Exercise.Exercise.MaintenanceWeekday', 'fri'))
                self.assertEqual(self.scheduler()['NextRun'], scheduled)

    def test_two_maintenance_changes_share_one_write_and_one_clock_read(self):
        before = self.configs.read_json(self.path)
        with patch('module.api.config_service.atomic_write', wraps=atomic_write) as write:
            self.save(('Exercise.Exercise.MaintenanceAdvance', False),
                      ('Exercise.Exercise.MaintenanceWeekday', 'fri'))
        write.assert_called_once()
        self.clock.assert_called_once_with()
        after = self.configs.read_json(self.path)
        self.assertEqual(after['Exercise']['Scheduler']['NextRun'], '2026-10-22 08:30:15')
        self.assertEqual(after['Main']['Scheduler'], before['Main']['Scheduler'])

    def test_invalid_weekday_rejects_entire_batch_without_write(self):
        original = self.path.read_bytes()
        with patch('module.api.config_service.atomic_write', wraps=atomic_write) as write:
            with self.assertRaises(ApiError) as caught:
                self.save(('Exercise.Exercise.MaintenanceAdvance', False),
                          ('Exercise.Exercise.MaintenanceWeekday', 'holiday'))
        self.assertEqual(caught.exception.code, 'INVALID_PARAMS')
        write.assert_not_called()
        self.clock.assert_not_called()
        self.assertEqual(self.path.read_bytes(), original)

    def test_unrelated_setting_does_not_wake_exercise(self):
        self.save(('Exercise.Exercise.OpponentChooseMode', 'leftmost'))
        self.assertEqual(self.scheduler()['NextRun'], self.future)
        self.clock.assert_not_called()


if __name__ == '__main__':
    unittest.main()
