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

    def test_daily_plan_is_checked_before_config_reload_and_game_navigation(self):
        exercise = object.__new__(Exercise)
        exercise.config = SimpleNamespace(SERVER='cn', data={}, config_name='exercise_fixture',
                                          update=Mock(), cross_get=Mock(return_value=None))
        exercise.ui_ensure = Mock(side_effect=RuntimeError('已到达导航入口'))
        calls = Mock()
        calls.attach_mock(exercise.config.update, 'reload')
        calls.attach_mock(exercise.ui_ensure, 'navigate')
        with patch('module.maintenance.get_maintenance_snapshot', return_value={}) as refresh:
            calls.attach_mock(refresh, 'refresh')
            with self.assertRaisesRegex(RuntimeError, '已到达导航入口'):
                exercise.run()
        self.assertEqual([item[0] for item in calls.mock_calls], ['refresh', 'reload', 'navigate'])
        self.assertTrue(refresh.call_args.kwargs['refresh'])

    def run_exercise(self, now, next_update, *, stock=10, stale_remain=0,
                     strategy='sun18', delay=1, season_hours=120,
                     server_offset=datetime.timedelta(hours=8), end_now=None,
                     maintenance_advance=True, maintenance_date=22, maintenance_start=None, package='auto'):
        exercise = object.__new__(Exercise)
        exercise.config = SimpleNamespace(
            Scheduler_ServerUpdate='00:00,12:00,18:00',
            Exercise_ExerciseStrategy=strategy,
            Exercise_DelayUntilHoursBeforeNextUpdate=delay,
            Exercise_MaintenanceAdvance=maintenance_advance,
            SERVER='cn', config_name='exercise_fixture',
            data={'Alas': {'Emulator': {'PackageName': package}}},
            Exercise_OpponentChooseMode='leftmost',
            multi_set=lambda: nullcontext(),
            set_record=Mock(),
            update=Mock(),
            cross_get=Mock(return_value=None),
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
            maintenance = maintenance_start or (datetime.datetime(2026, 10, maintenance_date, 10, tzinfo=CN_TIMEZONE)
                           if maintenance_date is not None else None)
            plan = stack.enter_context(patch('module.maintenance.get_maintenance_start', return_value=maintenance))
            refresh = stack.enter_context(patch('module.maintenance.get_maintenance_snapshot', return_value={}))
            stack.enter_context(patch('module.exercise.exercise.current_time', return_value=now,
                                      side_effect=(now, end_now) if end_now is not None else None))
            stack.enter_context(patch('module.exercise.exercise.get_server_next_update', return_value=next_update))
            stack.enter_context(patch('module.exercise.exercise.server_timezone', return_value=server_offset))
            stack.enter_context(patch('module.exercise.exercise.OCR_PERIOD_REMAIN.ocr',
                                      return_value=datetime.timedelta(hours=season_hours)))
            read_stock = stack.enter_context(patch('module.exercise.exercise.OCR_EXERCISE_REMAIN.ocr',
                                                   side_effect=lambda image: remaining[0]))
            exercise.run()
            expected_server = 'jp' if package == 'com.YoStarJP.AzurLane' else 'cn'
            self.assertEqual(refresh.call_args.args[0], expected_server)
            if maintenance_advance and exercise.preserve == 5:
                self.assertEqual(plan.call_args.args[0], expected_server)
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

    def test_each_announced_date_schedules_nine_and_retains_five(self):
        for day, weekday in ((19, 'mon'), (20, 'tue'), (21, 'wed'),
                             (23, 'fri'), (24, 'sat'), (25, 'sun')):
            with self.subTest(weekday=weekday):
                result = self.run_exercise(
                    cn_time(day, 8), cn_time(day, 12), maintenance_date=day,
                )
                self.assert_skipped_until(*result, cn_time(day, 9))
                exercise, remaining, _ = self.run_exercise(
                    cn_time(day, 9), cn_time(day, 12), maintenance_date=day,
                )
                self.assertEqual(remaining, 5)
                self.assertEqual(exercise._exercise_once.call_count, 5)
                exercise.config.task_delay.assert_called_once_with(server_update=True)

    def test_other_announced_dates_do_not_advance_thursday(self):
        for day in (19, 20, 21, 23, 24, 25):
            with self.subTest(maintenance_date=day):
                result = self.run_exercise(
                    cn_time(22, 9), cn_time(22, 12), maintenance_date=day,
                )
                self.assert_skipped_until(*result, cn_time(22, 11))

    def test_announced_date_afternoon_and_evening_keep_original_time(self):
        for day, weekday in enumerate(('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'), 19):
            for hour, update, target in ((12, cn_time(day, 18), cn_time(day, 17)),
                                         (18, cn_time(day + 1), cn_time(day, 23))):
                with self.subTest(weekday=weekday, hour=hour):
                    result = self.run_exercise(
                        cn_time(day, hour, 0, 1), update, maintenance_date=day,
                    )
                    self.assert_skipped_until(*result, target)

    def test_disabled_advance_uses_custom_delay_on_announced_date(self):
        for delay in (0, 2, 5):
            for day, weekday in ((19, 'mon'), (22, 'thu'), (25, 'sun')):
                with self.subTest(delay=delay, weekday=weekday):
                    result = self.run_exercise(
                        cn_time(day, 0, 0, 1), cn_time(day, 12), delay=delay,
                        maintenance_advance=False, maintenance_date=day,
                    )
                    self.assert_skipped_until(
                        *result, cn_time(day, 12) - datetime.timedelta(hours=delay),
                    )

    def test_afternoon_maintenance_moves_only_the_corresponding_cycle(self):
        maintenance = datetime.datetime(2026, 10, 22, 16, tzinfo=CN_TIMEZONE)
        result = self.run_exercise(cn_time(22, 14), cn_time(22, 18), maintenance_start=maintenance)
        self.assert_skipped_until(*result, cn_time(22, 15))
        exercise, remaining, _ = self.run_exercise(cn_time(22, 15), cn_time(22, 18), maintenance_start=maintenance)
        self.assertEqual(remaining, 5)
        exercise.config.task_delay.assert_called_once_with(server_update=True)

    def test_midnight_maintenance_can_advance_the_previous_evening(self):
        maintenance = datetime.datetime(2026, 10, 23, 0, 30, tzinfo=CN_TIMEZONE)
        result = self.run_exercise(cn_time(22, 22), cn_time(23), maintenance_start=maintenance)
        self.assert_skipped_until(*result, cn_time(22, 23, 30))
        exercise, remaining, _ = self.run_exercise(cn_time(22, 23, 30), cn_time(23), maintenance_start=maintenance)
        self.assertEqual(remaining, 5)
        exercise.config.task_delay.assert_called_once_with(server_update=True)
        result = self.run_exercise(cn_time(23, 1), cn_time(23, 12), maintenance_start=maintenance)
        self.assert_skipped_until(*result, cn_time(23, 11))

    def test_no_announcement_uses_configured_normal_delay(self):
        result = self.run_exercise(cn_time(22, 9), cn_time(22, 12), maintenance_date=None)
        self.assert_skipped_until(*result, cn_time(22, 11))

    def test_japan_package_uses_japan_cycles_while_global_server_is_china(self):
        japan = datetime.timezone(datetime.timedelta(hours=9))
        maintenance = datetime.datetime(2026, 10, 22, 14, tzinfo=japan)
        with patch('module.config.server.server', 'cn'):
            # 当前日服已补过中午次数，国服尚未补，不能误取国服所在周期。
            result = self.run_exercise(
                cn_time(22, 11, 30), cn_time(22, 12), maintenance_start=maintenance,
                package='com.YoStarJP.AzurLane',
            )
            self.assert_skipped_until(*result, cn_time(22, 12))
            exercise, remaining, _ = self.run_exercise(
                cn_time(22, 12, 30), cn_time(22, 18), maintenance_start=maintenance,
                package='com.YoStarJP.AzurLane',
            )
        self.assertEqual(remaining, 5)
        # 日服18点对应国服17点，完成后需等待此补次数节点。
        exercise.config.task_delay.assert_called_once_with(target=cn_time(22, 17))


class ExerciseMaintenanceConfigTests(unittest.TestCase):
    """用临时配置验证旧设置迁移与维护提前开关的持久化。"""

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

    def test_generated_default_keeps_advance_switch_and_removes_manual_weekday(self):
        self.assertIs(GeneratedConfig.Exercise_MaintenanceAdvance, True)
        self.assertFalse(hasattr(GeneratedConfig, 'Exercise_MaintenanceWeekday'))

    def test_legacy_config_gains_advance_default_and_keeps_strategy_and_delay(self):
        with self.temporary_config({
            'ExerciseStrategy': 'sun18', 'DelayUntilHoursBeforeNextUpdate': 2,
        }, legacy=True) as (config, path):
            self.assertIs(config.Exercise_MaintenanceAdvance, True)
            self.assertEqual(config.Exercise_ExerciseStrategy, 'sun18')
            self.assertEqual(config.Exercise_DelayUntilHoursBeforeNextUpdate, 2)
            config.Exercise_MaintenanceAdvance = False
            restarted = AzurLaneConfig('exercise_maintenance_test', task='Exercise')
            self.assertFalse(restarted.Exercise_MaintenanceAdvance)
            self.assertEqual(restarted.Exercise_DelayUntilHoursBeforeNextUpdate, 2)


if __name__ == '__main__':
    unittest.main()
