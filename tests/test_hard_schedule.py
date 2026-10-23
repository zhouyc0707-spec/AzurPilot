"""困难图按演习维护星期运行的时间边界与配置兼容性。"""

import copy
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from module.config.config import AzurLaneConfig, TaskEnd, name_to_function
from module.config.utils import read_file, write_file
from module.hard.hard import CampaignHard
from module.hard.schedule import hard_run_time


WEEKDAYS = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')


class HardRunTimeTests(unittest.TestCase):
    def test_every_weekday_only_moves_the_selected_day_to_evening(self):
        monday = date(2026, 10, 19)
        for selected, weekday in enumerate(WEEKDAYS):
            for offset in range(7):
                with self.subTest(weekday=weekday, day=offset):
                    day = monday + timedelta(days=offset)
                    target = hard_run_time(day, True, weekday)
                    self.assertEqual(target, datetime(2026, 10, 19 + offset,
                                                      18 if selected == offset else 0))
                    self.assertIsNone(target.tzinfo)

    def test_disabled_option_always_uses_midnight(self):
        for weekday in WEEKDAYS:
            for day in (date(2026, 10, 22), date(2026, 12, 31), date(2027, 1, 1)):
                with self.subTest(weekday=weekday, day=day):
                    self.assertEqual(hard_run_time(day, False, weekday),
                                     datetime.combine(day, datetime.min.time()))

    def test_datetime_input_discards_clock_and_microseconds(self):
        self.assertEqual(hard_run_time(datetime(2026, 10, 22, 23, 59, 59, 123456), True, 'thu'),
                         datetime(2026, 10, 22, 18))
        self.assertEqual(hard_run_time(datetime(2026, 10, 23, 23, 59, 59, 123456), True, 'thu'),
                         datetime(2026, 10, 23))


class HardScheduleTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 22, 12)
        self.offset = timedelta()
        for target, value in (
            ('module.hard.hard.current_time', lambda: self.now),
            ('module.config.config.current_time', lambda: self.now),
            ('module.config.utils.current_time', lambda: self.now),
            ('module.hard.hard.server_time_offset', lambda: self.offset),
            ('module.config.utils.server_time_offset', lambda: self.offset),
        ):
            self.enterContext(patch(target, side_effect=value))
        self.enterContext(patch('module.hard.hard.logger'))
        self.ocr = self.enterContext(patch('module.hard.hard.OCR_HARD_REMAIN'))
        self.ocr.ocr.return_value = 3

    def runner(self, *, maintenance_weekday='thu', maintenance_advance=True,
               exercise_enabled=True, **hard):
        config = AzurLaneConfig('template')
        config.data = config.config_update({
            'Hard': {'Hard': hard},
            'Exercise': {
                'Exercise': {'MaintenanceWeekday': maintenance_weekday,
                             'MaintenanceAdvance': maintenance_advance},
                'Scheduler': {'Enable': exercise_enabled},
            },
        })
        config.bind('Hard')
        config.task = name_to_function('Hard')
        config.task_call = Mock()
        # 运行入口和收尾会重载配置；普通时间用例仅从隔离的内存快照重载。
        config.read_file = Mock(side_effect=lambda *args, **kwargs: copy.deepcopy(config.data))
        runner = object.__new__(CampaignHard)
        runner.config = config
        runner.device = Mock()
        runner.campaign = Mock()
        runner.load_campaign = Mock()
        # 模拟出击经过的时间，仍走真实 task_delay 的时区换算与配置修改。
        runner.campaign.run.side_effect = lambda: setattr(self, 'now', self.now + timedelta(seconds=1))
        return runner

    def assert_skipped_until(self, runner, target):
        with self.assertRaises(TaskEnd):
            runner.run()
        self.assertEqual(runner.config.modified['Hard.Scheduler.NextRun'], target)
        runner.load_campaign.assert_not_called()
        self.assertEqual(runner.device.mock_calls, [])
        self.assertEqual(runner.campaign.mock_calls, [])
        self.ocr.ocr.assert_not_called()
        runner.config.task_call.assert_not_called()

    def assert_runs_until(self, runner, target, *, attempts=3):
        self.ocr.ocr.return_value = attempts
        runner.run()
        self.assertEqual(runner.campaign.run.call_count, attempts)
        runner.campaign.ensure_auto_search_exit.assert_called_once_with()
        self.assertEqual(runner.config.modified['Hard.Scheduler.NextRun'], target)
        runner.config.task_call.assert_called_once_with('Reward', force_call=False)

    def test_config_default_and_saved_choice(self):
        runner = self.runner()
        self.assertFalse(runner.config.Hard_RunAt18)
        definition = read_file('module/config/argument/args.json')['Hard']['Hard']['RunAt18']
        self.assertEqual(definition, {'type': 'checkbox', 'value': False})
        self.assertFalse(read_file('config/template.json')['Hard']['Hard']['RunAt18'])
        runner = self.runner(RunAt18=True, maintenance_weekday='fri')
        runner.config.data = runner.config.config_update(runner.config.data)
        runner.config.bind('Hard')
        self.assertTrue(runner.config.Hard_RunAt18)
        self.assertEqual(runner.config.cross_get('Exercise.Exercise.MaintenanceWeekday'), 'fri')
        for lang in ('zh-CN', 'zh-MIAO', 'en-US', 'ja-JP', 'zh-TW'):
            translation = read_file(f'module/config/i18n/{lang}.json')['Hard']['RunAt18']
            self.assertTrue(all(value and not value.startswith('Hard.RunAt18.') for value in translation.values()))

    def test_maintenance_day_before_18_defers_without_game_access(self):
        for server_now in (datetime(2026, 10, 22), datetime(2026, 10, 22, 17, 59, 59)):
            for hours in (0, -1, 16):
                with self.subTest(server_now=server_now, offset=hours):
                    self.offset = timedelta(hours=hours)
                    self.now = server_now + self.offset
                    runner = self.runner(RunAt18=True)
                    self.assert_skipped_until(runner, datetime(2026, 10, 22, 18) + self.offset)

    def test_each_selected_maintenance_weekday_blocks_its_morning(self):
        for selected, weekday in enumerate(WEEKDAYS):
            with self.subTest(weekday=weekday):
                self.now = datetime(2026, 10, 19 + selected, 8)
                runner = self.runner(RunAt18=True, maintenance_weekday=weekday)
                self.assert_skipped_until(runner, self.now.replace(hour=18))

    def test_non_maintenance_mornings_run_without_waiting_until_18(self):
        for day in (19, 20, 21, 23, 24, 25):
            for hours in (0, -1, 16):
                with self.subTest(day=day, offset=hours):
                    self.offset = timedelta(hours=hours)
                    self.now = datetime(2026, 10, day, 8) + self.offset
                    # 周三运行完毕后，周四是维护日，应直接排到周四18点。
                    target = datetime(2026, 10, day + 1, 18 if day == 21 else 0) + self.offset
                    self.assert_runs_until(self.runner(RunAt18=True), target)

    def test_at_or_after_18_runs_and_schedules_next_midnight(self):
        for server_now in (datetime(2026, 10, 22, 18), datetime(2026, 10, 22, 23, 59, 50)):
            for hours in (0, -1, 16):
                with self.subTest(server_now=server_now, offset=hours):
                    self.offset = timedelta(hours=hours)
                    self.now = server_now + self.offset
                    self.assert_runs_until(self.runner(RunAt18=True), datetime(2026, 10, 23) + self.offset)

    def test_default_still_runs_on_maintenance_morning_and_schedules_midnight(self):
        for weekday in WEEKDAYS:
            with self.subTest(weekday=weekday):
                self.now = datetime(2026, 10, 22, 8)
                self.assert_runs_until(self.runner(maintenance_weekday=weekday), datetime(2026, 10, 23))

    def test_disabled_option_does_not_delay_next_maintenance_day(self):
        self.now = datetime(2026, 10, 21, 8)
        self.assert_runs_until(self.runner(RunAt18=False), datetime(2026, 10, 22))

    def test_shared_weekday_does_not_depend_on_exercise_enable_or_advance_switch(self):
        for advance, enabled in ((False, True), (True, False), (False, False)):
            with self.subTest(maintenance_advance=advance, exercise_enabled=enabled):
                self.now = datetime(2026, 10, 23, 8)
                runner = self.runner(RunAt18=True, maintenance_weekday='fri',
                                     maintenance_advance=advance, exercise_enabled=enabled)
                self.assert_skipped_until(runner, datetime(2026, 10, 23, 18))

    def test_saved_weekday_takes_precedence_over_generated_thursday_default(self):
        self.now = datetime(2026, 10, 22, 8)
        self.assert_runs_until(self.runner(RunAt18=True, maintenance_weekday='fri'), datetime(2026, 10, 23, 18))

    def test_missing_legacy_weekday_falls_back_to_default_without_disabling_gate(self):
        self.now = datetime(2026, 10, 22, 8)
        runner = self.runner(RunAt18=True)
        runner.config.data['Exercise']['Exercise'].pop('MaintenanceWeekday')
        self.assert_skipped_until(runner, datetime(2026, 10, 22, 18))

    def test_day_before_maintenance_keeps_legal_42_hour_schedule_after_save_reload(self):
        self.now = datetime(2026, 10, 21)
        runner = self.runner(RunAt18=True)
        runner.campaign.run.side_effect = None
        target = datetime(2026, 10, 22, 18)
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'hard_schedule.json')
            runner.config.config_name = 'hard_schedule'
            runner.config._loaded_data = copy.deepcopy(runner.config.data)
            write_file(path, runner.config.data)
            # 此用例恢复真实读写，确认保存、配置迁移及重载不会夹掉合法长排程。
            runner.config.read_file = AzurLaneConfig.read_file.__get__(runner.config)
            with patch('module.config.config.filepath_config', return_value=path), patch(
                    'module.config.config_updater.filepath_config', return_value=path), patch(
                    'module.hard.hard.filepath_config', return_value=path):
                self.assert_runs_until(runner, target)
                self.assertEqual(target - self.now, timedelta(hours=42))
                runner.config.save()
                runner.config.load()
                runner.config.bind('Hard')
            self.assertEqual(runner.config.Scheduler_NextRun, target)
            self.assertTrue(runner.config.Hard_RunAt18)
            self.assertEqual(runner.config.cross_get('Exercise.Exercise.MaintenanceWeekday'), 'thu')
            self.assertEqual(runner.config.modified, {})

    def test_cross_month_and_year_schedule_uses_next_server_date(self):
        cases = (
            (datetime(2026, 10, 31, 23, 59, 50), 'sun', datetime(2026, 11, 1, 18)),
            (datetime(2026, 11, 30, 23, 59, 50), 'tue', datetime(2026, 12, 1, 18)),
            (datetime(2026, 12, 31, 23, 59, 50), 'fri', datetime(2027, 1, 1, 18)),
            (datetime(2026, 12, 31, 18), 'thu', datetime(2027, 1, 1)),
        )
        for now, weekday, target in cases:
            for hours in (0, -1, 16):
                with self.subTest(now=now, weekday=weekday, offset=hours):
                    self.offset = timedelta(hours=hours)
                    self.now = now + self.offset
                    self.assert_runs_until(self.runner(RunAt18=True, maintenance_weekday=weekday), target + self.offset)

    def test_completion_crossing_midnight_schedules_from_completion_day(self):
        self.now = datetime(2026, 10, 22, 23, 59, 59)
        self.assert_runs_until(self.runner(RunAt18=True), datetime(2026, 10, 24))

    def test_local_weekday_never_overrides_server_maintenance_weekday(self):
        # 本机周三16点，对应服务器周四8点，仍需推迟到服务器周四18点。
        self.offset = timedelta(hours=-16)
        self.now = datetime(2026, 10, 21, 16)
        self.assert_skipped_until(self.runner(RunAt18=True), datetime(2026, 10, 22, 2))
        # 本机周四8点，对应服务器周三16点，不应把本机星期当作维护日。
        self.offset = timedelta(hours=16)
        self.now = datetime(2026, 10, 22, 8)
        self.assert_runs_until(self.runner(RunAt18=True), datetime(2026, 10, 23, 10))

    def test_no_remaining_attempts_still_schedules_next_midnight(self):
        self.now = datetime(2026, 10, 22, 18, 1)
        self.assert_runs_until(self.runner(RunAt18=True), datetime(2026, 10, 23), attempts=0)


if __name__ == '__main__':
    unittest.main()
