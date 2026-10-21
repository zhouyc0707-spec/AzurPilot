"""核对可配置的维护前演习调度，全部使用模拟时间、库存和战斗结果。"""

import datetime
import json
import os
import tempfile
import unittest
from contextlib import ExitStack, contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.config.config import AzurLaneConfig
from module.config.config_generated import GeneratedConfig
from module.exercise.exercise import Exercise


CN_TIMEZONE = datetime.timezone(datetime.timedelta(hours=8))


def cn_time(day, hour=0, minute=0, second=0):
    # 调度器实际使用本机无时区时间，先换算而不假定测试主机位于中国。
    return datetime.datetime(2026, 10, day, hour, minute, second, tzinfo=CN_TIMEZONE).astimezone().replace(tzinfo=None)


class ExerciseScheduleTests(unittest.TestCase):
    """真实执行门禁和排程，替换所有设备入口与资源消耗。"""

    def run_exercise(self, now, next_update, *, stock=10, stale_remain=0,
                     strategy='sun18', delay=1, season_hours=120,
                     server_offset=datetime.timedelta(hours=8), end_now=None,
                     maintenance_advance=True, maintenance_weekday='thu'):
        exercise = object.__new__(Exercise)
        exercise.config = SimpleNamespace(
            Scheduler_ServerUpdate='00:00,12:00,18:00',
            Exercise_ExerciseStrategy=strategy,
            Exercise_DelayUntilHoursBeforeNextUpdate=delay,
            Exercise_MaintenanceAdvance=maintenance_advance,
            Exercise_MaintenanceWeekday=maintenance_weekday,
            Exercise_OpponentChooseMode='leftmost',
            multi_set=lambda: nullcontext(),
            set_record=Mock(),
            task_delay=Mock(),
        )
        exercise.device = SimpleNamespace(image=object())
        exercise.ui_ensure = Mock()
        exercise._get_opponent_change_count = Mock(return_value=0)
        exercise.remain = stale_remain
        remaining = [stock]

        def complete_one():
            self.assertGreater(remaining[0], 0)
            remaining[0] -= 1
            return True

        exercise._exercise_once = Mock(side_effect=complete_one)
        exercise._exercise_easiest_else_exp = Mock(side_effect=complete_one)
        with ExitStack() as stack:
            stack.enter_context(patch('module.exercise.exercise.logger'))
            stack.enter_context(patch('module.exercise.exercise.current_time', return_value=now,
                                      side_effect=(now, end_now) if end_now is not None else None))
            stack.enter_context(patch('module.exercise.exercise.get_server_next_update', return_value=next_update))
            stack.enter_context(patch('module.exercise.exercise.server_timezone', return_value=server_offset))
            stack.enter_context(patch('module.exercise.exercise.OCR_PERIOD_REMAIN.ocr',
                                      return_value=datetime.timedelta(hours=season_hours)))
            read_stock = stack.enter_context(patch('module.exercise.exercise.OCR_EXERCISE_REMAIN.ocr',
                                                   side_effect=lambda image: remaining[0]))
            exercise.run()
        return exercise, remaining[0], read_stock

    def assert_skipped_until(self, exercise, remaining, read_stock, target):
        self.assertEqual(remaining, 10)
        exercise._exercise_once.assert_not_called()
        exercise._exercise_easiest_else_exp.assert_not_called()
        read_stock.assert_not_called()
        exercise.config.task_delay.assert_called_once_with(target=target)

    def test_thursday_before_nine_schedules_nine_without_consuming(self):
        for now in (cn_time(22, 0, 0, 1), cn_time(22, 8, 59, 59)):
            with self.subTest(now=now):
                result = self.run_exercise(now, cn_time(22, 12))
                self.assert_skipped_until(*result, cn_time(22, 9))

    def test_check_crossing_nine_requeues_immediately_instead_of_waiting_until_noon(self):
        now = cn_time(22, 8, 59, 59)
        end_now = cn_time(22, 9, 0, 1)
        result = self.run_exercise(now, cn_time(22, 12), end_now=end_now)
        self.assert_skipped_until(*result, end_now)

    def test_thursday_nine_consumes_only_five(self):
        exercise, remaining, read_stock = self.run_exercise(cn_time(22, 9), cn_time(22, 12))
        self.assertEqual(remaining, 5)
        self.assertEqual(exercise.remain, 5)
        self.assertEqual(exercise._exercise_once.call_count, 5)
        self.assertEqual(read_stock.call_count, 6)
        exercise.config.task_delay.assert_called_once_with(server_update=True)

    def test_late_thursday_start_still_consumes_excess(self):
        exercise, remaining, _ = self.run_exercise(cn_time(22, 9, 30), cn_time(22, 12))
        self.assertEqual(remaining, 5)
        self.assertEqual(exercise._exercise_once.call_count, 5)
        exercise.config.task_delay.assert_called_once_with(server_update=True)

    def test_thursday_five_remaining_does_not_start_a_battle(self):
        exercise, remaining, read_stock = self.run_exercise(cn_time(22, 9), cn_time(22, 12), stock=5)
        self.assertEqual(remaining, 5)
        exercise._exercise_once.assert_not_called()
        read_stock.assert_called_once()
        exercise.config.task_delay.assert_called_once_with(server_update=True)

    def test_thursday_afternoon_and_evening_keep_configured_delay(self):
        for now, update, target in (
            (cn_time(22, 12, 0, 1), cn_time(22, 18), cn_time(22, 17)),
            (cn_time(22, 18, 0, 1), cn_time(23), cn_time(22, 23)),
        ):
            with self.subTest(now=now):
                result = self.run_exercise(now, update)
                self.assert_skipped_until(*result, target)

    def test_other_weekdays_keep_eleven_oclock(self):
        for day in (19, 20, 21, 23, 24, 25):
            with self.subTest(day=day):
                result = self.run_exercise(cn_time(day, 9), cn_time(day, 12))
                self.assert_skipped_until(*result, cn_time(day, 11))

    def test_wednesday_night_first_waits_for_midnight_then_nine(self):
        exercise, remaining, _ = self.run_exercise(cn_time(21, 23), cn_time(22))
        self.assertEqual(remaining, 5)
        exercise.config.task_delay.assert_called_once_with(server_update=True)
        result = self.run_exercise(cn_time(22, 0, 0, 1), cn_time(22, 12))
        self.assert_skipped_until(*result, cn_time(22, 9))

    def test_server_thursday_applies_even_when_local_day_is_wednesday(self):
        local_timezone = datetime.timezone(datetime.timedelta(hours=-8))
        now = datetime.datetime(2026, 10, 21, 16, 59, tzinfo=local_timezone)
        update = datetime.datetime(2026, 10, 21, 20, tzinfo=local_timezone)
        expected = datetime.datetime(2026, 10, 21, 17, tzinfo=local_timezone)
        result = self.run_exercise(now, update)
        self.assert_skipped_until(*result, expected)

    def test_local_thursday_does_not_override_server_wednesday(self):
        result = self.run_exercise(
            cn_time(22, 1), cn_time(22, 4),
            server_offset=datetime.timedelta(hours=-8),
        )
        self.assert_skipped_until(*result, cn_time(22, 3))

    def test_aggressive_strategy_keeps_eleven_oclock_gate(self):
        result = self.run_exercise(cn_time(22, 9), cn_time(22, 12), strategy='aggressive')
        self.assert_skipped_until(*result, cn_time(22, 11))
        self.assertEqual(result[0].preserve, 0)

    def test_aggressive_strategy_still_spends_all_at_original_time(self):
        exercise, remaining, _ = self.run_exercise(
            cn_time(22, 11), cn_time(22, 12), strategy='aggressive',
        )
        self.assertEqual(remaining, 0)
        self.assertEqual(exercise._exercise_once.call_count, 10)
        exercise.config.task_delay.assert_called_once_with(server_update=True)

    def test_forced_season_cleanup_bypasses_nine_and_spends_all(self):
        for strategy, season_hours in (('sun18', 5), ('sun12', 10), ('sat0', 5)):
            with self.subTest(strategy=strategy, season_hours=season_hours):
                exercise, remaining, _ = self.run_exercise(
                    cn_time(22, 8), cn_time(22, 12),
                    strategy=strategy, season_hours=season_hours,
                )
                self.assertEqual(remaining, 0)
                self.assertEqual(exercise.preserve, 0)
                self.assertEqual(exercise._exercise_once.call_count, 10)
                exercise.config.task_delay.assert_called_once_with(server_update=True)

    def test_all_preserve_five_strategies_use_thursday_nine(self):
        for strategy in ('sun18', 'sun12', 'sun0', 'sat18', 'sat12', 'sat0', 'fri18'):
            with self.subTest(strategy=strategy):
                result = self.run_exercise(cn_time(22, 8), cn_time(22, 12), strategy=strategy)
                self.assert_skipped_until(*result, cn_time(22, 9))

    def test_thursday_morning_override_is_fixed_but_other_days_use_config(self):
        for delay in (0, 2, 5):
            with self.subTest(delay=delay):
                result = self.run_exercise(cn_time(22, 0, 0, 1), cn_time(22, 12), delay=delay)
                self.assert_skipped_until(*result, cn_time(22, 9))
                result = self.run_exercise(cn_time(23, 0, 0, 1), cn_time(23, 12), delay=delay)
                self.assert_skipped_until(*result, cn_time(23, 12) - datetime.timedelta(hours=delay))

    def test_skipped_task_with_stale_ten_remaining_still_schedules_nine(self):
        result = self.run_exercise(cn_time(22, 8), cn_time(22, 12), stale_remain=10)
        self.assert_skipped_until(*result, cn_time(22, 9))

    def test_disabling_maintenance_advance_restores_thursday_eleven(self):
        for hour in (0, 9):
            with self.subTest(hour=hour):
                result = self.run_exercise(
                    cn_time(22, hour), cn_time(22, 12), maintenance_advance=False,
                )
                self.assert_skipped_until(*result, cn_time(22, 11))
        exercise, remaining, _ = self.run_exercise(
            cn_time(22, 11), cn_time(22, 12), maintenance_advance=False,
        )
        self.assertEqual(remaining, 5)
        self.assertEqual(exercise._exercise_once.call_count, 5)

    def test_each_selected_weekday_schedules_nine_and_retains_five(self):
        for day, weekday in ((19, 'mon'), (20, 'tue'), (21, 'wed'),
                             (23, 'fri'), (24, 'sat'), (25, 'sun')):
            with self.subTest(weekday=weekday):
                result = self.run_exercise(
                    cn_time(day, 8), cn_time(day, 12), maintenance_weekday=weekday,
                )
                self.assert_skipped_until(*result, cn_time(day, 9))
                exercise, remaining, _ = self.run_exercise(
                    cn_time(day, 9), cn_time(day, 12), maintenance_weekday=weekday,
                )
                self.assertEqual(remaining, 5)
                self.assertEqual(exercise._exercise_once.call_count, 5)
                exercise.config.task_delay.assert_called_once_with(server_update=True)

    def test_selecting_another_weekday_stops_thursday_advance(self):
        for weekday in ('mon', 'tue', 'wed', 'fri', 'sat', 'sun'):
            with self.subTest(weekday=weekday):
                result = self.run_exercise(
                    cn_time(22, 9), cn_time(22, 12), maintenance_weekday=weekday,
                )
                self.assert_skipped_until(*result, cn_time(22, 11))

    def test_selected_weekday_afternoon_and_evening_keep_original_time(self):
        for day, weekday in enumerate(('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'), 19):
            for hour, update, target in ((12, cn_time(day, 18), cn_time(day, 17)),
                                         (18, cn_time(day + 1), cn_time(day, 23))):
                with self.subTest(weekday=weekday, hour=hour):
                    result = self.run_exercise(
                        cn_time(day, hour, 0, 1), update, maintenance_weekday=weekday,
                    )
                    self.assert_skipped_until(*result, target)

    def test_disabled_advance_uses_custom_delay_on_selected_weekday(self):
        for delay in (0, 2, 5):
            for day, weekday in ((19, 'mon'), (22, 'thu'), (25, 'sun')):
                with self.subTest(delay=delay, weekday=weekday):
                    result = self.run_exercise(
                        cn_time(day, 0, 0, 1), cn_time(day, 12), delay=delay,
                        maintenance_advance=False, maintenance_weekday=weekday,
                    )
                    self.assert_skipped_until(
                        *result, cn_time(day, 12) - datetime.timedelta(hours=delay),
                    )


class ExerciseMaintenanceConfigTests(unittest.TestCase):
    """用临时配置验证旧设置迁移与维护星期的持久化。"""

    @contextmanager
    def temporary_config(self, values=None, *, legacy=False):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'exercise_maintenance_test.json'
            template = Path(__file__).resolve().parents[1] / 'config/template.json'
            data = json.loads(template.read_text(encoding='utf-8'))
            exercise = data['Exercise']['Exercise']
            exercise.update(values or {})
            if legacy:
                exercise.pop('MaintenanceAdvance', None)
                exercise.pop('MaintenanceWeekday', None)
            path.write_text(json.dumps(data), encoding='utf-8')
            with patch.dict(os.environ, {'AZURPILOT_NTP_DISABLE': '1'}), patch(
                'module.config.config.filepath_config', return_value=str(path)
            ), patch('module.config.config_updater.filepath_config', return_value=str(path)):
                yield AzurLaneConfig('exercise_maintenance_test', task='Exercise'), path

    def test_generated_defaults_preserve_existing_thursday_behavior(self):
        self.assertIs(GeneratedConfig.Exercise_MaintenanceAdvance, True)
        self.assertEqual(GeneratedConfig.Exercise_MaintenanceWeekday, 'thu')

    def test_legacy_config_gains_defaults_without_changing_strategy_or_delay(self):
        with self.temporary_config({
            'ExerciseStrategy': 'sun18', 'DelayUntilHoursBeforeNextUpdate': 2,
        }, legacy=True) as (config, path):
            self.assertIs(config.Exercise_MaintenanceAdvance, True)
            self.assertEqual(config.Exercise_MaintenanceWeekday, 'thu')
            self.assertEqual(config.Exercise_ExerciseStrategy, 'sun18')
            self.assertEqual(config.Exercise_DelayUntilHoursBeforeNextUpdate, 2)
            # 加载只合并默认值；一次正常保存后检查迁移字段落盘。
            config.Exercise_MaintenanceAdvance = True
            saved = json.loads(path.read_text(encoding='utf-8'))['Exercise']['Exercise']
            self.assertIs(saved['MaintenanceAdvance'], True)
            self.assertEqual(saved['MaintenanceWeekday'], 'thu')
            restarted = AzurLaneConfig('exercise_maintenance_test', task='Exercise')
            self.assertIs(restarted.Exercise_MaintenanceAdvance, True)
            self.assertEqual(restarted.Exercise_MaintenanceWeekday, 'thu')
            self.assertEqual(restarted.Exercise_DelayUntilHoursBeforeNextUpdate, 2)

    def test_disabled_advance_and_selected_weekday_survive_restart(self):
        with self.temporary_config() as (config, path):
            config.Exercise_MaintenanceWeekday = 'tue'
            config.Exercise_MaintenanceAdvance = False
            saved = json.loads(path.read_text(encoding='utf-8'))['Exercise']['Exercise']
            self.assertIs(saved['MaintenanceAdvance'], False)
            self.assertEqual(saved['MaintenanceWeekday'], 'tue')
            restarted = AzurLaneConfig('exercise_maintenance_test', task='Exercise')
            self.assertIs(restarted.Exercise_MaintenanceAdvance, False)
            self.assertEqual(restarted.Exercise_MaintenanceWeekday, 'tue')
            restarted.Exercise_MaintenanceAdvance = True
            enabled = AzurLaneConfig('exercise_maintenance_test', task='Exercise')
            self.assertIs(enabled.Exercise_MaintenanceAdvance, True)
            self.assertEqual(enabled.Exercise_MaintenanceWeekday, 'tue')


if __name__ == '__main__':
    unittest.main()
