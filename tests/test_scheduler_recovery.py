"""使用真实调度循环验证恢复边界，所有设备、通知和后台服务均隔离。"""

import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, call, patch

from alas import AzurLaneAutoScript
from module.config.config import TaskEnd
from module.exception import (
    AutoSearchSetError,
    EmulatorNotRunningError,
    FleetManagementError,
    GameBugError,
    GameNotRunningError,
    GamePageUnknownError,
    GameStuckError,
    GameTooManyClickError,
    MindCalculatorScanError,
    RequestHumanTakeover,
    ScriptError,
    StorageStatisticsError,
)


class TestSchedulerRecovery(unittest.TestCase):
    def setUp(self):
        self.logger = self.enterContext(patch('alas.logger'))
        self.enterContext(patch.object(AzurLaneAutoScript, '_log_source_watermark'))
        self.notify = self.enterContext(patch('alas.handle_notify'))
        self.webui = self.enterContext(patch('alas.notify_webui'))
        self.sleep = self.enterContext(patch('alas.time.sleep'))
        self.enterContext(patch('alas.del_cached_property'))
        self.enterContext(patch('module.config.utils.is_oobe_needed', return_value=False))
        self.enterContext(patch('module.base.backup.backup'))
        self.enterContext(patch('module.runtime.preview.set_task'))
        self.enterContext(patch.dict('os.environ', {'ALAS_DEBUG_SERVER': '0'}))

    def make_script(self, *, strict=False, sensitive=False):
        script = AzurLaneAutoScript('test')
        script.is_first_task = False
        script._channel_float_done = True
        script.__dict__['config'] = Mock(
            DailySummary_Enable=False,
            EmulatorManagement_ScheduledEmulatorRestart=False,
            Scheduler_PushNotification=False,
            Error_StrictRestart=strict,
            Error_TaskRestartLimit=0,
            Error_GameStuckRestart=True,
            Error_GameStuckThreshold=3,
            Error_HandleError=True,
            Error_LlmAnalysis=False,
        )
        script.config.cross_get.return_value = sensitive
        script.__dict__['device'] = Mock()
        script.__dict__['checker'] = Mock()
        script.checker.is_recovered.return_value = False
        script.checker.is_available.return_value = True
        script.save_error_log = Mock()
        script._start_watchdog = Mock()
        script._stop_daily_summary_scheduler = Mock()
        script._record_daily_summary_task_finish = Mock()
        script._try_restart_emulator = Mock(return_value=True)
        script.handle_channel_float = Mock()
        script.restart = Mock()
        script.commission = Mock()
        return script

    def run_tasks(self, script, tasks):
        """每次循环消耗一个任务或异常，最后由停止事件结束，避免无限重试。"""
        script.get_next_task = Mock(side_effect=tasks)
        script.stop_event = Mock()
        script.stop_event.is_set.side_effect = [False] * len(tasks) + [True]
        return script.loop()

    def test_scheduled_restart_uses_uptime_even_when_scheduler_just_started(self):
        script = self.make_script()
        script.is_first_task = True
        script.config.EmulatorManagement_ScheduledEmulatorRestart = True
        script.config.EmulatorManagement_RestartIntervalHours = 8
        script._get_emulator_uptime = Mock(side_effect=[8 * 3600, 60.0])

        self.run_tasks(script, ['Restart', 'Commission'])

        script._try_restart_emulator.assert_called_once_with()
        script.config.task_call.assert_called_once_with('Restart')
        script.restart.assert_called_once_with()
        script.commission.assert_not_called()
        self.assertFalse(script.is_first_task)

    def test_scheduled_restart_skips_low_uptime_and_failed_reads(self):
        for uptime in (0.0, 8 * 3600 - 1, None):
            with self.subTest(uptime=uptime):
                script = self.make_script()
                script.config.EmulatorManagement_ScheduledEmulatorRestart = True
                script.config.EmulatorManagement_RestartIntervalHours = 8
                script._get_emulator_uptime = Mock(return_value=uptime)
                self.run_tasks(script, ['Commission'])
                script._try_restart_emulator.assert_not_called()
                script.config.task_call.assert_not_called()
                script.commission.assert_called_once_with()

    def test_scheduled_restart_waits_for_current_task_to_finish(self):
        script = self.make_script()
        script.config.EmulatorManagement_ScheduledEmulatorRestart = True
        script.config.EmulatorManagement_RestartIntervalHours = 8
        events = []
        script._get_emulator_uptime = Mock(side_effect=[8 * 3600 - 1, 8 * 3600])

        def task():
            events.append('任务开始')
            script._try_restart_emulator.assert_not_called()
            events.append('任务结束')

        script.commission.side_effect = task
        script._try_restart_emulator.side_effect = lambda: events.append('重启模拟器') or True
        self.run_tasks(script, ['Commission', 'Restart'])

        self.assertEqual(events, ['任务开始', '任务结束', '重启模拟器'])
        script._try_restart_emulator.assert_called_once_with()

    def test_failed_scheduled_restart_keeps_task_running_and_retries(self):
        script = self.make_script()
        script.config.EmulatorManagement_ScheduledEmulatorRestart = True
        script.config.EmulatorManagement_RestartIntervalHours = 8
        script._get_emulator_uptime = Mock(return_value=9 * 3600)
        script._try_restart_emulator.side_effect = [False, True]
        self.run_tasks(script, ['Commission', 'Restart'])

        self.assertEqual(script._try_restart_emulator.call_count, 2)
        script.commission.assert_called_once_with()
        script.config.task_call.assert_called_once_with('Restart')

    def test_disabled_scheduled_restart_does_not_read_uptime(self):
        script = self.make_script()
        script._get_emulator_uptime = Mock()
        self.run_tasks(script, ['Commission'])

        script._get_emulator_uptime.assert_not_called()
        script._try_restart_emulator.assert_not_called()

    def test_unhandled_task_failure_returns_false_when_recovery_is_disabled(self):
        script = self.make_script()
        script.config.Error_HandleError = False
        script.run = Mock(return_value=False)

        self.assertIs(self.run_tasks(script, ['Commission']), False)

        script._stop_daily_summary_scheduler.assert_called_once_with()
        script._try_restart_emulator.assert_not_called()
        script.config.task_call.assert_not_called()

    def test_task_errors_stop_before_recovery_when_disabled(self):
        errors = (
            GameNotRunningError, GameStuckError, GameTooManyClickError,
            GameBugError, GamePageUnknownError, ScriptError,
            EmulatorNotRunningError, RequestHumanTakeover, AutoSearchSetError,
            RuntimeError,
        )
        for error_type in errors:
            with self.subTest(error=error_type.__name__):
                script = self.make_script()
                script.config.Error_HandleError = False
                script.config.Error_GameStuckThreshold = 1
                script.commission.side_effect = error_type('测试异常')

                with self.assertRaises(SystemExit) as caught:
                    self.run_tasks(script, ['Commission'])

                self.assertEqual(caught.exception.code, 1)
                script.save_error_log.assert_called_once_with()
                script._try_restart_emulator.assert_not_called()
                script.config.task_call.assert_not_called()
                script.config.task_delay.assert_not_called()
                script.device.sleep.assert_not_called()
                script.checker.check_now.assert_not_called()
                self.assertEqual(script.task_restart_record, {})

    def test_global_errors_stop_without_recovery_when_disabled(self):
        for stage in ('selection', 'initialization', 'finalization'):
            with self.subTest(stage=stage):
                script = self.make_script()
                script.config.Error_HandleError = False
                tasks = ['Commission']
                error = RuntimeError('调度器异常')
                if stage == 'selection':
                    tasks = [error]
                elif stage == 'initialization':
                    script.device.stuck_record_clear.side_effect = error
                else:
                    script._record_daily_summary_task_finish.side_effect = error

                with self.assertRaises(SystemExit) as caught:
                    self.run_tasks(script, tasks)

                self.assertEqual(caught.exception.code, 1)
                script.save_error_log.assert_called_once_with()
                script._try_restart_emulator.assert_not_called()
                script.config.task_call.assert_not_called()
                script.config.task_delay.assert_not_called()
                self.sleep.assert_not_called()

    def test_disabled_recovery_still_stops_if_error_log_cannot_be_saved(self):
        script = self.make_script()
        script.config.Error_HandleError = False
        script.commission.side_effect = EmulatorNotRunningError('设备离线')
        script.save_error_log.side_effect = OSError('无法保存错误现场')

        with self.assertRaises(SystemExit) as caught:
            self.run_tasks(script, ['Commission'])

        self.assertEqual(caught.exception.code, 1)
        script._try_restart_emulator.assert_not_called()
        script.config.task_call.assert_not_called()

    def test_disabled_recovery_prevents_failed_result_escalation(self):
        for result in (False, 'recoverable'):
            with self.subTest(result=result):
                script = self.make_script()
                script.config.Error_HandleError = False
                script.failure_record['Commission'] = 2
                script.run = Mock(return_value=result)

                self.assertIs(self.run_tasks(script, ['Commission']), False)

                script._try_restart_emulator.assert_not_called()
                script.config.task_call.assert_not_called()
                script._stop_daily_summary_scheduler.assert_called_once_with()

    def test_watchdog_timeout_respects_recovery_switch(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                script = self.make_script()
                script.config.Error_HandleError = enabled
                script._emulator_op_with_timeout = Mock()
                with (
                    patch('alas.os._exit') as stop_worker,
                    patch('module.device.platform.Platform') as platform,
                ):
                    script._watchdog_recover(120, task_name='Commission')

                if enabled:
                    stop_worker.assert_not_called()
                    platform.assert_called_once_with(script.config, connect=False)
                    script._emulator_op_with_timeout.assert_called_once()
                else:
                    stop_worker.assert_called_once_with(1)
                    platform.assert_not_called()
                    script._emulator_op_with_timeout.assert_not_called()

    def test_scheduled_restart_is_independent_of_error_recovery_switch(self):
        script = self.make_script()
        script.config.Error_HandleError = False
        script.config.EmulatorManagement_ScheduledEmulatorRestart = True
        script.config.EmulatorManagement_RestartIntervalHours = 1
        # 本地定时重启依据 Android 实际运行时间，与调度器自身运行时长无关。
        script._get_emulator_uptime = Mock(return_value=7200)
        self.run_tasks(script, ['Commission'])

        script._get_emulator_uptime.assert_called_once_with()
        script._try_restart_emulator.assert_called_once_with()
        script.config.task_call.assert_called_once_with('Restart')
        script.get_next_task.assert_not_called()

    def arm_forced_stop(self, script, stop_result=True):
        script.config.Error_HandleError = False
        script.config.EmulatorManagement_ScheduledEmulatorRestart = True
        script.config.EmulatorManagement_ForceScheduledRestart = True
        if not script._watchdog_active:
            script._watchdog_active = True
            script._watchdog_task_name = 'Commission'
            script._watchdog_task_start = 100.0
        script._emulator_op_with_timeout = Mock(return_value=stop_result)
        script._start_emulator_after_long_wait = Mock(return_value=True)
        with patch('module.device.platform.Platform'):
            script._watchdog_recover(7200, reason='force_scheduled_restart', task_name='Commission')
        return script._watchdog_scheduled_restart_receipt

    def test_forced_restart_completes_loop_without_spending_or_resetting_business_counts(self):
        script = self.make_script()
        script.failure_record = {'Commission': 2}
        script.task_restart_record = {'Commission': 2}
        script._get_emulator_uptime = Mock(return_value=0)
        script.config.EmulatorManagement_RestartIntervalHours = 8
        script.config.Error_TaskRestartLimit = 3
        script._program_runtime = Mock()

        def stop_during_task():
            self.arm_forced_stop(script)
            raise EmulatorNotRunningError('本轮强制停机后设备离线')

        script.commission.side_effect = stop_during_task
        # 计划启动前后都会检查停止信号，使用真实事件而不是按调用次数翻转的夹具。
        script.stop_event = threading.Event()
        script.get_next_task = Mock(side_effect=['Commission', 'Restart'])
        script.restart.side_effect = script.stop_event.set
        script.loop()

        script._start_emulator_after_long_wait.assert_called_once_with(reason='强制定时重启后')
        script.config.task_call.assert_called_once_with('Restart')
        script.restart.assert_called_once_with()
        script._try_restart_emulator.assert_not_called()
        self.assertEqual(script.failure_record['Commission'], 2)
        self.assertEqual(script.task_restart_record['Commission'], 2)
        script._program_runtime.task_finished.assert_has_calls([
            call('Commission', 'recoverable'), call('Restart', True),
        ])
        self.assertIsNone(script._watchdog_scheduled_restart_receipt)

    def test_forced_restart_receipt_is_consumed_once(self):
        script = self.make_script()
        self.arm_forced_stop(script)
        error = EmulatorNotRunningError('设备离线')
        self.assertTrue(script._complete_force_scheduled_restart('commission', error))
        self.assertFalse(script._complete_force_scheduled_restart('commission', error))
        script._start_emulator_after_long_wait.assert_called_once()
        script.config.task_call.assert_called_once_with('Restart')

    def test_old_watchdog_request_cannot_stop_a_new_task(self):
        script = self.make_script()
        script._watchdog_active = True
        script._watchdog_task_name = 'Commission'
        script._watchdog_task_start = 101.0
        with patch('module.device.platform.Platform') as platform:
            script._watchdog_recover(
                7200, reason='force_scheduled_restart', task_name='Commission',
                task_token=('Commission', 100.0),
            )
        platform.assert_not_called()
        self.assertIsNone(script._watchdog_scheduled_restart_receipt)

    def test_transport_error_waits_until_the_concurrent_stop_confirms_success(self):
        script = self.make_script()
        script.config.Error_HandleError = False
        script.config.EmulatorManagement_ScheduledEmulatorRestart = True
        script.config.EmulatorManagement_ForceScheduledRestart = True
        script._watchdog_active = True
        script._watchdog_task_name = 'Commission'
        script._watchdog_task_start = 100.0
        script._start_emulator_after_long_wait = Mock(return_value=True)
        stopping, release_stop, waiting = (threading.Event() for _ in range(3))

        def stop_operation(*args, **kwargs):
            stopping.set()
            if not release_stop.wait(3):
                raise TimeoutError('测试停机等待超时')
            return True

        script._emulator_op_with_timeout = Mock(side_effect=stop_operation)
        results = []
        with patch('module.device.platform.Platform'):
            stopper = threading.Thread(target=script._watchdog_recover, kwargs={
                'elapsed': 7200, 'reason': 'force_scheduled_restart',
                'task_name': 'Commission', 'task_token': ('Commission', 100.0),
            })
            stopper.start()
            self.assertTrue(stopping.wait(3))
            receipt = script._watchdog_scheduled_restart_receipt
            original_wait = receipt.finished.wait

            def wait_for_stop(timeout):
                waiting.set()
                return original_wait(min(timeout, 3))

            receipt.finished.wait = wait_for_stop
            consumer = threading.Thread(target=lambda: results.append(
                script._complete_force_scheduled_restart('commission', EmulatorNotRunningError('设备离线'))))
            try:
                consumer.start()
                self.assertTrue(waiting.wait(3))
                script._start_emulator_after_long_wait.assert_not_called()
            finally:
                release_stop.set()
                stopper.join(3)
                consumer.join(3)
            self.assertFalse(stopper.is_alive())
            self.assertFalse(consumer.is_alive())
        self.assertEqual(results, [True])
        script._start_emulator_after_long_wait.assert_called_once()
        script.config.task_call.assert_called_once_with('Restart')

    def test_forced_restart_rejects_stale_task_disabled_plan_sensitive_task_and_stop(self):
        for change in ('task', 'start', 'inactive', 'scheduled', 'force', 'sensitive', 'stop'):
            with self.subTest(change=change):
                script = self.make_script()
                self.arm_forced_stop(script)
                if change == 'task':
                    script._watchdog_task_name = 'Research'
                elif change == 'start':
                    script._watchdog_task_start = 101.0
                elif change == 'inactive':
                    script._watchdog_active = False
                elif change == 'scheduled':
                    script.config.EmulatorManagement_ScheduledEmulatorRestart = False
                elif change == 'force':
                    script.config.EmulatorManagement_ForceScheduledRestart = False
                elif change == 'sensitive':
                    script.config.cross_get.return_value = True
                else:
                    script.stop_event = Mock(is_set=Mock(return_value=True))
                self.assertFalse(script._complete_force_scheduled_restart(
                    'commission', EmulatorNotRunningError('设备离线')))
                script._start_emulator_after_long_wait.assert_not_called()
                self.assertIsNone(script._watchdog_scheduled_restart_receipt)

    def test_failed_or_unconfirmed_forced_stop_cannot_resume(self):
        for result in (False, None):
            with self.subTest(result=result):
                script = self.make_script()
                self.arm_forced_stop(script, stop_result=result)
                script.commission.side_effect = EmulatorNotRunningError('设备离线')
                with self.assertRaises(SystemExit):
                    script.run('commission')
                script._start_emulator_after_long_wait.assert_not_called()
                script._try_restart_emulator.assert_not_called()
                script.config.task_call.assert_not_called()

    def test_forced_stop_does_not_authorize_script_or_unclassified_errors(self):
        for error in (ScriptError('代码错误'), RequestHumanTakeover('未知原因'), ValueError('业务错误')):
            with self.subTest(error=type(error).__name__):
                script = self.make_script()
                self.arm_forced_stop(script)
                script.commission.side_effect = error
                with self.assertRaises(SystemExit):
                    script.run('commission')
                script._start_emulator_after_long_wait.assert_not_called()
                script.config.task_call.assert_not_called()
                self.assertIsNone(script._watchdog_scheduled_restart_receipt)

    def test_forced_stop_keeps_fleet_manual_boundary(self):
        script = self.make_script()
        self.arm_forced_stop(script)
        script.commission.side_effect = FleetManagementError('需要人工核对')
        self.assertFalse(script.run('commission'))
        self.assertTrue(script._fleet_management_failed)
        script._start_emulator_after_long_wait.assert_not_called()
        script.config.task_call.assert_not_called()

    def test_forced_stop_accepts_only_classified_transport_cause(self):
        from adbutils.errors import AdbError
        for cause, accepted in ((ConnectionResetError('连接中断'), True),
                                (AdbError('device offline'), True),
                                (AdbError('device unauthorized'), False),
                                (ScriptError('代码错误'), False)):
            with self.subTest(cause=str(cause)):
                script = self.make_script()
                self.arm_forced_stop(script)
                error = RequestHumanTakeover('设备后端重试耗尽')
                error.__cause__ = cause
                script.commission.side_effect = error
                if accepted:
                    self.assertEqual(script.run('commission'), 'scheduled_restart')
                    script._start_emulator_after_long_wait.assert_called_once()
                else:
                    with self.assertRaises(SystemExit):
                        script.run('commission')
                    script._start_emulator_after_long_wait.assert_not_called()

    def test_forced_restart_waits_for_confirmation_and_timeout_cannot_resume_later(self):
        for confirmed in (True, False):
            with self.subTest(confirmed=confirmed):
                script = self.make_script()
                receipt = self.arm_forced_stop(script)
                receipt.stopped = False
                receipt.deadline = 20.0

                def wait(timeout):
                    self.assertEqual(timeout, 5.0)
                    script._start_emulator_after_long_wait.assert_not_called()
                    receipt.stopped = confirmed
                    return confirmed

                receipt.finished = Mock(wait=Mock(side_effect=wait))
                with patch('alas.time.monotonic', return_value=15.0):
                    self.assertEqual(script._complete_force_scheduled_restart(
                        'commission', EmulatorNotRunningError('设备离线')), confirmed)
                receipt.finished.wait.assert_called_once_with(5.0)
                receipt.stopped = True
                self.assertFalse(script._complete_force_scheduled_restart(
                    'commission', EmulatorNotRunningError('迟到异常')))
                self.assertEqual(script._start_emulator_after_long_wait.call_count, int(confirmed))

    def test_forced_restart_start_failure_stops_without_another_recovery(self):
        script = self.make_script()
        self.arm_forced_stop(script)
        script._start_emulator_after_long_wait.return_value = False
        script.commission.side_effect = EmulatorNotRunningError('设备离线')
        with self.assertRaises(SystemExit):
            script.run('commission')
        script._start_emulator_after_long_wait.assert_called_once()
        script._try_restart_emulator.assert_not_called()
        script.config.task_call.assert_not_called()
        self.assertIsNone(script._watchdog_scheduled_restart_receipt)

    def test_forced_restart_daily_summary_is_recoverable_not_failed_or_completed(self):
        script = self.make_script()
        service = Mock()
        script._get_daily_summary_service = Mock(return_value=service)
        AzurLaneAutoScript._record_daily_summary_task_finish(
            script, 'test-run', 'scheduled_restart', datetime.now(),
        )
        self.assertEqual(service.store.record_task_finish.call_args.args[3], 'recoverable')

    def test_sensitive_failure_notifications_are_preserved_when_recovery_is_disabled(self):
        script = self.make_script(strict=True, sensitive=True)
        script.config.Error_HandleError = False
        script.commission.side_effect = EmulatorNotRunningError('设备离线')

        with self.assertRaises(SystemExit) as caught:
            self.run_tasks(script, ['Commission'])

        self.assertEqual(caught.exception.code, 1)
        script.save_error_log.assert_called_once_with()
        self.notify.assert_called_once()
        self.webui.assert_called_once()
        script._try_restart_emulator.assert_not_called()
        script.config.task_call.assert_not_called()

    def test_normal_completion_is_preserved_when_recovery_is_disabled(self):
        for outcome in (None, TaskEnd()):
            with self.subTest(outcome=outcome):
                script = self.make_script()
                script.config.Error_HandleError = False
                script.commission.side_effect = [outcome]

                self.assertTrue(script.run('Commission'))

                script.save_error_log.assert_not_called()
                script._try_restart_emulator.assert_not_called()
                script.config.task_call.assert_not_called()

    def test_scan_failure_results_are_preserved_when_recovery_is_disabled(self):
        for error_type in (MindCalculatorScanError, FleetManagementError):
            with self.subTest(error=error_type.__name__):
                script = self.make_script()
                script.config.Error_HandleError = False
                script.commission.side_effect = error_type('无法确认识别结果')

                self.assertFalse(script.run('Commission'))

                script.save_error_log.assert_called_once_with()
                script._try_restart_emulator.assert_not_called()
                script.config.task_call.assert_not_called()

    def test_repeated_storage_recognition_failures_do_not_restart_or_stop_scheduler(self):
        script = self.make_script()
        script.config.Error_HandleError = False
        script.storage_statistics = Mock(side_effect=StorageStatisticsError('数量无法确认，已延后'))
        self.run_tasks(script, ['StorageStatistics'] * 3)
        self.assertEqual(script.storage_statistics.call_count, 3)
        script._try_restart_emulator.assert_not_called()
        script.config.task_call.assert_not_called()
        self.assertNotIn('StorageStatistics', script.failure_record)

    def test_initial_device_offline_is_recovered_by_scheduler(self):
        script = self.make_script()
        del script.__dict__['device']
        del script.__dict__['_try_restart_emulator']
        script.config.task.command = 'Commission'
        script.config.Error_AdbOfflineThreshold = 3
        connected_device = Mock()
        with (
            patch(
                'module.device.device.Device',
                side_effect=[EmulatorNotRunningError('设备离线'), connected_device],
            ) as device_class,
            patch('module.device.platform.Platform') as platform_class,
        ):
            self.run_tasks(script, ['Commission', 'Restart'])

        self.assertEqual(device_class.call_args_list, [
            call(config=script.config, auto_start_emulator=False),
            call(config=script.config, auto_start_emulator=False),
        ])
        platform_class.assert_called_once_with(script.config, connect=False)
        platform_class.return_value.emulator_stop.assert_called_once_with()
        platform_class.return_value.emulator_start.assert_called_once_with(deep=False, failures=0)
        script.config.task_call.assert_called_once_with('Restart')
        script.restart.assert_called_once_with()
        script.commission.assert_not_called()
        self.assertEqual(self.sleep.call_args_list, [call(5), call(20)])

    def test_mind_scan_recognition_failure_reports_cause_without_restart(self):
        script = self.make_script()
        error = MindCalculatorScanError('相邻船坞截图没有明确的重叠位移')
        script.dock_scan = Mock(side_effect=error)
        self.assertFalse(script.run('DockScan'))
        script.save_error_log.assert_called_once_with()
        script._try_restart_emulator.assert_not_called()
        script.config.task_call.assert_not_called()
        self.assertEqual(self.logger.error_context.call_args.kwargs['reason'], str(error))
        self.assertIs(self.logger.error_context.call_args.kwargs['exc'], error)

    def test_fleet_management_failure_stops_scheduler_before_retry_or_restart(self):
        for limit in (0, 3):
            with self.subTest(restart_limit=limit):
                script = self.make_script()
                script.config.Error_TaskRestartLimit = limit
                script.main = Mock(side_effect=FleetManagementError('交换结果尚未确认'))
                script.failure_record['Main'] = 2
                script._record_task_restart = Mock(return_value=False)
                runtime = Mock(mode='native')
                script.__dict__['_program_runtime'] = runtime

                self.assertIs(self.run_tasks(script, ['Main', 'Commission']), False)

                script.main.assert_called_once_with()
                script.commission.assert_not_called()
                script._record_task_restart.assert_not_called()
                script._try_restart_emulator.assert_not_called()
                script.config.task_call.assert_not_called()
                script.config.task_delay.assert_not_called()
                script.config.cross_set.assert_not_called()
                script._stop_daily_summary_scheduler.assert_called_once_with()
                runtime.task_finished.assert_called_once_with('Main', False)
                self.assertFalse(script._watchdog_active)
                self.assertEqual(script.failure_record['Main'], 2)

    def test_fleet_management_failure_returns_false_and_resets_on_manual_next_run(self):
        script = self.make_script()
        script.fleet_scan = Mock(side_effect=FleetManagementError('舰位未确认'))
        self.assertIs(script.run('FleetScan'), False)
        self.assertTrue(script._fleet_management_failed)
        script.save_error_log.assert_called_once_with()
        script._try_restart_emulator.assert_not_called()
        script.config.task_call.assert_not_called()

        self.assertTrue(script.run('Commission'))
        self.assertFalse(script._fleet_management_failed)

    def test_released_cooldown_does_not_bypass_fleet_manual_stop(self):
        """关闭普通恢复限制可解除旧冷却，但换船不确定仍须人工核对。"""
        script = self.make_script()
        deadline = datetime.now().replace(microsecond=0) + timedelta(days=1)
        script.task_restart_delays = {'Main': deadline}
        script.config.cross_get.return_value = deadline
        script.main = Mock(side_effect=FleetManagementError('交换结果尚未确认'))

        self.assertIs(self.run_tasks(script, ['Main', 'Commission']), False)

        self.assertEqual(script.task_restart_delays, {})
        script.config.cross_set.assert_called_once_with(
            'Main.Scheduler.NextRun', script.config.cross_set.call_args.args[1],
        )
        self.assertLess(script.config.cross_set.call_args.args[1], deadline)
        script.main.assert_called_once_with()
        script.commission.assert_not_called()
        script._try_restart_emulator.assert_not_called()
        script.config.task_call.assert_not_called()
        script.config.task_delay.assert_not_called()
        script._stop_daily_summary_scheduler.assert_called_once_with()
        self.assertTrue(script._fleet_management_failed)

    def test_strict_restart_policy_for_task_exceptions(self):
        errors = (
            GameNotRunningError, GameStuckError, GameTooManyClickError,
            GameBugError, GamePageUnknownError, ScriptError,
            EmulatorNotRunningError, RequestHumanTakeover, AutoSearchSetError,
            RuntimeError,
        )
        for error_type in errors:
            for strict, sensitive in ((False, False), (False, True), (True, False), (True, True)):
                with self.subTest(error=error_type.__name__, strict=strict, sensitive=sensitive):
                    script = self.make_script(strict=strict, sensitive=sensitive)
                    script.opsi_cross_month = Mock(side_effect=error_type('测试异常'))
                    if strict and sensitive:
                        with self.assertRaises(SystemExit) as caught:
                            script.run('opsi_cross_month')
                        self.assertEqual(caught.exception.code, 1)
                        script.config.task_call.assert_not_called()
                        script._try_restart_emulator.assert_not_called()
                    else:
                        self.assertEqual(script.run('opsi_cross_month'), 'recoverable')
                        script.config.task_call.assert_called_once_with('Restart')
                    if strict:
                        script.config.cross_get.assert_called_with(
                            keys='OpsiCrossMonth.Scheduler.Sensitive', default=False,
                        )

    def test_strict_restart_policy_for_failed_scheduler_result(self):
        for strict, sensitive in ((False, False), (False, True), (True, False), (True, True)):
            with self.subTest(strict=strict, sensitive=sensitive):
                script = self.make_script(strict=strict, sensitive=sensitive)
                script.run = Mock(return_value=False)
                if strict and sensitive:
                    with self.assertRaises(SystemExit):
                        self.run_tasks(script, ['OpsiCrossMonth'])
                else:
                    self.run_tasks(script, ['OpsiCrossMonth'])
                self.assertEqual(script.failure_record['OpsiCrossMonth'], 1)
                script._try_restart_emulator.assert_not_called()

    def test_game_faults_escalate_across_successful_restarts(self):
        for error_type in (GameStuckError, GameTooManyClickError, RuntimeError):
            with self.subTest(error=error_type.__name__):
                script = self.make_script()
                script.commission.side_effect = error_type('重复故障')
                self.run_tasks(script, ['Commission', 'Restart', 'Commission', 'Restart', 'Commission'])

                script._try_restart_emulator.assert_called_once_with()
                self.assertEqual(script.commission.call_count, 3)
                self.assertEqual(script.restart.call_count, 2)
                self.assertEqual(script.failure_record['Commission'], 0)

    def test_repeated_game_exits_escalate_without_deferring_business_task(self):
        script = self.make_script()
        script.config.Error_TaskRestartLimit = 1
        script.commission.side_effect = GameNotRunningError('登录后再次退出')
        self.run_tasks(script, ['Commission', 'Restart'] * 5)

        script._try_restart_emulator.assert_called_once_with()
        self.assertEqual(script.consecutive_game_not_running, 2)
        self.assertEqual(script.task_restart_record, {})
        script.config.task_delay.assert_not_called()

    def test_game_exit_recovery_respects_emulator_restart_switch(self):
        script = self.make_script()
        script.config.Error_TaskRestartLimit = 1
        script.config.Error_GameStuckRestart = False
        script.commission.side_effect = GameNotRunningError('游戏未运行')
        self.run_tasks(script, ['Commission', 'Restart'] * 5)

        script._try_restart_emulator.assert_not_called()
        script.config.task_delay.assert_not_called()
        self.assertEqual(script.config.task_call.call_count, 5)

    def test_game_exit_retries_emulator_if_previous_restart_failed(self):
        script = self.make_script()
        script.config.Error_TaskRestartLimit = 1
        script._try_restart_emulator.return_value = False
        script.commission.side_effect = GameNotRunningError('游戏未运行')
        self.run_tasks(script, ['Commission', 'Restart'] * 4)

        self.assertEqual(script._try_restart_emulator.call_count, 2)
        self.assertEqual(script.consecutive_game_not_running, 4)
        self.assertEqual(script.task_restart_record, {})
        script.config.task_delay.assert_not_called()

    def test_environment_failure_does_not_spend_or_enforce_business_task_limit(self):
        for error_type in (GameNotRunningError, EmulatorNotRunningError):
            with self.subTest(error=error_type.__name__):
                script = self.make_script()
                script.config.Error_TaskRestartLimit = 1
                script.config.Error_GameStuckThreshold = 1
                script.task_restart_record['Commission'] = 1
                script.commission.side_effect = error_type('运行环境未恢复')
                self.run_tasks(script, ['Commission'])

                script._try_restart_emulator.assert_called_once_with()
                script.config.task_delay.assert_not_called()
                self.assertEqual(script.task_restart_record, {'Commission': 1})

    def test_game_only_recovery_does_not_spend_emulator_restart_limit(self):
        for error_type in (GameBugError, GamePageUnknownError, AutoSearchSetError):
            with self.subTest(error=error_type.__name__):
                script = self.make_script()
                script.config.Error_TaskRestartLimit = 1
                script.commission.side_effect = error_type('重启游戏恢复')
                self.run_tasks(script, ['Commission', 'Restart'] * 5)

                script._try_restart_emulator.assert_not_called()
                script.config.task_delay.assert_not_called()
                self.assertEqual(script.task_restart_record, {})

    def test_last_allowed_emulator_restart_can_recover_task(self):
        script = self.make_script()
        script.config.Error_TaskRestartLimit = 1
        script.config.Error_GameStuckThreshold = 1
        script.commission.side_effect = [GameStuckError('卡死'), None]
        self.run_tasks(script, ['Commission', 'Restart', 'Commission'])

        script._try_restart_emulator.assert_called_once_with()
        script.config.task_delay.assert_not_called()
        self.assertEqual(script.task_restart_record, {})

    def test_unsuccessful_emulator_restart_does_not_spend_limit(self):
        script = self.make_script()
        script.config.Error_TaskRestartLimit = 1
        script.config.Error_GameStuckThreshold = 1
        script._try_restart_emulator.return_value = False
        script.commission.side_effect = GameStuckError('模拟器仍未恢复')
        self.run_tasks(script, ['Commission', 'Restart'] * 5)

        self.assertEqual(script._try_restart_emulator.call_count, 5)
        script.config.task_delay.assert_not_called()
        self.assertEqual(script.task_restart_record, {})

    def test_internal_navigation_and_resource_checks_do_not_create_task_cooldowns(self):
        for command in ('goto_main', 'scheduler_refresh', 'oil_control'):
            with self.subTest(command=command):
                script = self.make_script()
                script.config.Error_TaskRestartLimit = 1
                script.config.Error_GameStuckThreshold = 1
                script.__dict__[command] = Mock(side_effect=GameStuckError('恢复运行环境'))
                for _ in range(3):
                    self.assertEqual(script.run(command), 'recoverable')

                self.assertEqual(script._try_restart_emulator.call_count, 3)
                self.assertEqual(script.task_restart_record, {})
                script.config.task_delay.assert_not_called()

    def test_script_errors_still_stop_after_three_failures_with_restarts(self):
        script = self.make_script()
        script.commission.side_effect = ScriptError('重复脚本错误')
        with self.assertRaises(SystemExit) as caught:
            self.run_tasks(script, ['Commission', 'Restart', 'Commission', 'Restart', 'Commission'])

        self.assertEqual(caught.exception.code, 1)
        self.assertEqual(script.script_error_count, 3)
        script._try_restart_emulator.assert_not_called()
        self.assertEqual(script.config.task_call.call_count, 2)

    def test_success_breaks_script_error_streak(self):
        script = self.make_script()
        script.commission.side_effect = [ScriptError('故障一'), None, ScriptError('故障二'), ScriptError('故障三')]
        self.run_tasks(script, ['Commission', 'Restart', 'Commission', 'Commission', 'Restart', 'Commission'])

        self.assertEqual(script.script_error_count, 2)
        self.assertEqual(script.commission.call_count, 4)

    def test_success_and_task_end_reset_all_task_error_counters(self):
        for outcome in (None, TaskEnd()):
            with self.subTest(outcome=outcome):
                script = self.make_script()
                script.consecutive_game_stuck = 2
                script.consecutive_game_not_running = 2
                script.consecutive_adb_offline = 2
                script.consecutive_unexpected_error = 2
                script.script_error_count = 2
                script.commission.side_effect = [outcome]
                self.run_tasks(script, ['Commission'])

                self.assertEqual(script.consecutive_game_stuck, 0)
                self.assertEqual(script.consecutive_game_not_running, 0)
                self.assertEqual(script.consecutive_adb_offline, 0)
                self.assertEqual(script.consecutive_unexpected_error, 0)
                self.assertEqual(script.script_error_count, 0)

    def test_restart_success_preserves_all_task_error_counters(self):
        script = self.make_script()
        script.consecutive_game_stuck = 2
        script.consecutive_game_not_running = 2
        script.consecutive_adb_offline = 2
        script.consecutive_unexpected_error = 2
        script.script_error_count = 2
        self.run_tasks(script, ['Restart'])

        self.assertEqual(script.consecutive_game_stuck, 2)
        self.assertEqual(script.consecutive_game_not_running, 2)
        self.assertEqual(script.consecutive_adb_offline, 2)
        self.assertEqual(script.consecutive_unexpected_error, 2)
        self.assertEqual(script.script_error_count, 2)

    def test_global_backoff_increases_across_successful_restarts(self):
        script = self.make_script()
        self.run_tasks(script, [RuntimeError('故障一'), 'Restart', RuntimeError('故障二'), 'Restart', RuntimeError('故障三')])

        self.assertEqual(self.sleep.call_args_list, [call(20), call(40), call(80)])
        script.save_error_log.assert_called_once()

    def test_ordinary_task_success_resets_global_backoff(self):
        script = self.make_script()
        self.run_tasks(script, [RuntimeError('故障一'), 'Commission', RuntimeError('故障二')])

        self.assertEqual(self.sleep.call_args_list, [call(20), call(20)])

    def test_restart_defers_channel_float_handling_until_next_task(self):
        for command in ('restart', 'Restart'):
            with self.subTest(command=command):
                script = self.make_script()
                self.assertTrue(script.run(command))
                script.restart.assert_called_once_with()
                self.assertFalse(script._channel_float_done)
                script.handle_channel_float.assert_not_called()

                self.assertTrue(script.run('commission'))
                script.handle_channel_float.assert_called_once_with()
                script.commission.assert_called_once_with()

    def test_repeated_recovery_defers_only_failing_task_and_pushes_in_low_mode(self):
        script = self.make_script()
        script.config.Error_TaskRestartLimit = 3
        script.config.Error_GameStuckThreshold = 1
        script.config.Error_LowPushMode = True
        script.config.Error_OnePushConfig = 'provider: null'
        script.commission.side_effect = GameStuckError('重复故障')
        script.research = Mock()
        tomorrow = datetime.now().replace(microsecond=0) + timedelta(days=1)
        with patch('alas.get_server_next_update', return_value=tomorrow):
            self.run_tasks(script, ['Commission', 'Restart', 'Research',
                                    'Commission', 'Restart', 'Commission', 'Restart', 'Commission'])

        script.config.task_delay.assert_called_once_with(target=tomorrow, task='Commission')
        self.assertEqual(script.task_restart_delays, {'Commission': tomorrow})
        self.assertNotIn('Commission', script.task_restart_record)
        self.assertEqual(script.restart.call_count, 3)
        self.assertEqual(script._try_restart_emulator.call_count, 3)
        script.research.assert_called_once_with()
        alerts = [c for c in self.notify.call_args_list if '已达上限' in c.kwargs['title']]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].args, ('provider: null',))
        self.assertIn('重启模拟器 3 次后仍未成功', alerts[0].kwargs['content'])

    def test_success_resets_only_its_own_restart_streak(self):
        script = self.make_script()
        script.config.Error_TaskRestartLimit = 3
        script.task_restart_record = {'Commission': 2, 'Research': 2}
        self.run_tasks(script, ['Restart', 'Commission'])
        self.assertEqual(script.task_restart_record, {'Research': 2})
        script.config.task_delay.assert_not_called()

    def test_disabled_limit_keeps_existing_recovery(self):
        script = self.make_script()
        script.commission.side_effect = GameNotRunningError('未运行')
        self.run_tasks(script, ['Commission'] * 5)
        script.config.task_delay.assert_not_called()
        self.assertEqual(script.config.task_call.call_count, 5)

    def test_disabling_limit_releases_matching_cooldown(self):
        script = self.make_script()
        script.config.Error_TaskRestartLimit = 0
        tomorrow = datetime.now().replace(microsecond=0) + timedelta(days=1)
        script.task_restart_delays = {'Commission': tomorrow}
        script.config.cross_get.return_value = tomorrow

        self.run_tasks(script, ['Commission'])

        self.assertEqual(script.task_restart_delays, {})
        script.config.cross_set.assert_called_once_with(
            'Commission.Scheduler.NextRun',
            script.config.cross_set.call_args.args[1],
        )
        self.assertLess(script.config.cross_set.call_args.args[1], tomorrow)
        script.config.task_call.assert_not_called()
        script.commission.assert_called_once_with()

    def test_disabling_limit_keeps_user_rescheduled_task(self):
        script = self.make_script()
        script.config.Error_TaskRestartLimit = 0
        tomorrow = datetime.now().replace(microsecond=0) + timedelta(days=1)
        script.task_restart_delays = {'Commission': tomorrow}
        script.config.cross_get.return_value = tomorrow + timedelta(hours=3)

        self.run_tasks(script, ['Commission'])

        self.assertEqual(script.task_restart_delays, {})
        script.config.task_call.assert_not_called()
        script.config.cross_set.assert_not_called()

    def test_disabling_limit_does_not_touch_task_switch(self):
        script = self.make_script()
        script.config.Error_TaskRestartLimit = 0
        tomorrow = datetime.now().replace(microsecond=0) + timedelta(days=1)
        script.task_restart_delays = {'Commission': tomorrow}
        script.config.cross_get.return_value = tomorrow

        self.run_tasks(script, ['Commission'])

        # 直接改排期，不经过 task_call()，避免接管模式把任务塞进队列。
        script.config.task_call.assert_not_called()
        script.config.cross_set.assert_called_once()
        self.assertNotIn('Commission', script.task_restart_delays)

    def test_disabling_limit_releases_unselected_cooldowns(self):
        script = self.make_script()
        script.config.Error_TaskRestartLimit = 0
        tomorrow = datetime.now().replace(microsecond=0) + timedelta(days=1)
        script.task_restart_delays = {'Commission': tomorrow, 'Research': tomorrow}
        script.config.cross_get.return_value = tomorrow

        self.run_tasks(script, ['Commission'])

        self.assertEqual(script.task_restart_delays, {})
        keys = sorted(
            call_args.args[0]
            for call_args in script.config.cross_set.call_args_list
            if call_args.args and str(call_args.args[0]).endswith('Scheduler.NextRun')
        )
        self.assertEqual(keys, ['Commission.Scheduler.NextRun', 'Research.Scheduler.NextRun'])

    def test_disabling_limit_clears_cooldown_before_counting_failure(self):
        script = self.make_script()
        script.config.Error_TaskRestartLimit = 0
        script.task_restart_record = {'Commission': 2}
        script.task_restart_delays = {'Commission': datetime.now() + timedelta(hours=1)}

        self.assertFalse(script._record_task_restart('Commission', 'recoverable'))

        self.assertEqual(script.task_restart_record, {})
        self.assertEqual(script.task_restart_delays, {})

    def test_failed_restart_task_is_never_deferred(self):
        script = self.make_script()
        script.config.Error_TaskRestartLimit = 2
        script.restart.side_effect = GameNotRunningError('无法启动')
        self.run_tasks(script, ['Restart'] * 5)
        self.assertEqual(script.restart.call_count, 5)
        self.assertEqual(script.config.task_call.call_args_list, [call('Restart')] * 5)
        script.config.task_delay.assert_not_called()
        self.assertNotIn('Restart', script.task_restart_record)
        self.assertNotIn('Restart', script.task_restart_delays)

    def test_restart_bypasses_stale_cooldown_in_all_scheduler_modes(self):
        for mode in ('native', 'enhance', 'takeover'):
            with self.subTest(mode=mode):
                script = self.make_script()
                script.config.Error_TaskRestartLimit = 1
                script.task_restart_record = {'Restart': 3, 'Commission': 2}
                deadline = datetime.now() + timedelta(days=1)
                script.task_restart_delays = {'Restart': deadline, 'Commission': deadline}
                script.__dict__['_program_runtime'] = Mock(mode=mode)
                script.wait_until = Mock()
                self.run_tasks(script, ['Restart'])
                script.restart.assert_called_once_with()
                script.config.task_delay.assert_not_called()
                script.wait_until.assert_not_called()
                self.assertEqual(script.task_restart_record, {'Commission': 2})
                self.assertEqual(script.task_restart_delays, {'Commission': deadline})

    def test_restart_global_failures_keep_recovering_emulator_and_game(self):
        script = self.make_script()
        script.config.Error_TaskRestartLimit = 2
        script.device.stuck_record_clear.side_effect = EmulatorNotRunningError('设备离线')
        self.run_tasks(script, ['Restart'] * 4)
        self.assertEqual(script._try_restart_emulator.call_count, 4)
        self.assertEqual(script.config.task_call.call_args_list, [call('Restart')] * 4)
        script.config.task_delay.assert_not_called()
        self.assertEqual(script.task_restart_record, {})
        self.assertEqual(script.task_restart_delays, {})

    def test_restart_failed_results_still_escalate_to_emulator_recovery(self):
        script = self.make_script()
        script.config.Error_TaskRestartLimit = 1
        script.run = Mock(return_value=False)
        self.run_tasks(script, ['Restart'] * 3)
        script._try_restart_emulator.assert_called_once_with()
        script.config.task_call.assert_called_once_with('Restart')
        script.config.task_delay.assert_not_called()

    def test_custom_dispatch_cannot_bypass_cooldown_and_resumes_after_deadline(self):
        script = self.make_script()
        script.config.Error_TaskRestartLimit = 3
        now = datetime(2026, 10, 1, 12)
        deadline = datetime(2026, 10, 2)
        script.task_restart_delays['Commission'] = deadline
        runtime = script.__dict__['_program_runtime'] = Mock(mode='takeover')
        script.wait_until = Mock()
        with patch('alas.current_time', return_value=now):
            self.run_tasks(script, ['Commission'])
        script.commission.assert_not_called()
        runtime.task_finished.assert_called_once_with('Commission', False)
        script.wait_until.assert_called_once_with(now + timedelta(seconds=4))
        with patch('alas.current_time', return_value=deadline):
            self.run_tasks(script, ['Commission'])
        script.commission.assert_called_once_with()
        self.assertEqual(script.task_restart_delays, {})

    def test_global_failure_after_task_selection_counts_toward_limit(self):
        script = self.make_script()
        script.config.Error_TaskRestartLimit = 2
        script.device.stuck_record_clear.side_effect = RuntimeError('设备故障')
        tomorrow = datetime.now() + timedelta(days=1)
        with patch('alas.get_server_next_update', return_value=tomorrow):
            self.run_tasks(script, ['Commission', 'Commission', 'Commission'])
        script.config.task_delay.assert_called_once_with(target=tomorrow, task='Commission')
        self.assertEqual(script._try_restart_emulator.call_count, 2)
        self.assertEqual(script.config.task_call.call_args_list, [call('Restart')] * 3)

    def test_global_device_failure_recovers_without_deferring_business_task(self):
        script = self.make_script()
        script.config.Error_TaskRestartLimit = 1
        script.device.stuck_record_clear.side_effect = EmulatorNotRunningError('设备离线')
        recovery = Mock()
        recovery.attach_mock(script._try_restart_emulator, 'emulator')
        recovery.attach_mock(script.config.task_call, 'game')
        recovery.attach_mock(script.config.task_delay, 'defer')
        tomorrow = datetime.now() + timedelta(days=1)
        with patch('alas.get_server_next_update', return_value=tomorrow):
            self.run_tasks(script, ['Commission'])
        self.assertEqual(recovery.mock_calls, [
            call.emulator(), call.game('Restart'),
        ])
        self.assertEqual(script.task_restart_record, {})

    def test_main_continues_when_resource_observation_cannot_get_write_lock(self):
        import sqlite3
        from contextlib import closing
        from module.log_res import LogRes
        from module.persistence.write_queue import write_queue
        from module.scheduler.store import ProgramStore

        directory = Path(self.enterContext(TemporaryDirectory())) / 'config'
        store = ProgramStore(directory)
        store.database.ensure_ready()
        profile = directory / 'testpilot.json'
        profile.write_text('{}', encoding='utf-8')
        script = self.make_script()
        script.config.config_name = 'testpilot'
        script.config.task.command = 'Main'
        script.config.data = {'Dashboard': {'Oil': {'Value': 123, 'Record': None}}}
        script.config.modified = {}
        recorder = LogRes(script.config)
        stamp = datetime(2026, 11, 12, 9, 35, 10, 123456)
        script.main = lambda: recorder.record('Oil', 456, observed_at=stamp)

        with patch('module.config.utils.filepath_config', return_value=profile), \
                patch('module.statistics.resource_stats._LOCAL_DB', directory / 'azurstats_local.db'), \
                patch.object(write_queue, '_schedule'), \
                closing(sqlite3.connect(store.database.path)) as blocker:
            blocker.execute('BEGIN IMMEDIATE')
            self.assertIs(script.run('main'), True)
            self.assertFalse(write_queue.flush(timeout=0.01))
            self.assertEqual(456, script.config.modified['Dashboard.Oil.Value'])
            self.assertEqual({}, store.observations('testpilot'))
            with store.database.transaction(write=False) as connection:
                self.assertEqual(0, connection.execute('SELECT COUNT(*) FROM resource_flows').fetchone()[0])
                self.assertEqual(0, connection.execute('SELECT COUNT(*) FROM resource_snapshots').fetchone()[0])
            self.assertEqual(3, write_queue.pending())
            blocker.rollback()
            self.assertTrue(write_queue.flush(timeout=1))

        self.assertEqual(456, store.observations('testpilot')['Oil']['Value'])
        with store.database.transaction(write=False) as connection:
            snapshot = connection.execute('SELECT ts, oil FROM resource_snapshots').fetchone()
            self.assertEqual(456, snapshot['oil'])
            self.assertEqual(stamp.isoformat(), snapshot['ts'])
        script.config.task_call.assert_not_called()
        script._try_restart_emulator.assert_not_called()
        self.assertEqual({}, script.task_restart_record)

    def test_global_failure_preserves_sensitive_task_protection(self):
        for strict, sensitive in ((False, False), (False, True), (True, False), (True, True)):
            with self.subTest(strict=strict, sensitive=sensitive):
                script = self.make_script(strict=strict, sensitive=sensitive)
                script.config.Error_TaskRestartLimit = 1
                script.device.stuck_record_clear.side_effect = RuntimeError('任务初始化失败')
                tomorrow = datetime.now() + timedelta(days=1)
                with patch('alas.get_server_next_update', return_value=tomorrow):
                    if strict and sensitive:
                        with self.assertRaises(SystemExit) as caught:
                            self.run_tasks(script, ['OpsiCrossMonth'])
                        self.assertEqual(caught.exception.code, 1)
                        script._try_restart_emulator.assert_not_called()
                        script.config.task_call.assert_not_called()
                        script.config.task_delay.assert_not_called()
                    else:
                        self.run_tasks(script, ['OpsiCrossMonth'])
                        script._try_restart_emulator.assert_called_once_with()
                        script.config.task_call.assert_called_once_with('Restart')
                        script.config.task_delay.assert_not_called()
                        self.assertEqual(script.task_restart_record, {'OpsiCrossMonth': 1})

    def test_notification_failure_does_not_undo_deferral(self):
        script = self.make_script()
        script.config.Error_TaskRestartLimit = 1
        script.task_restart_record['Commission'] = 1
        self.notify.side_effect = RuntimeError('推送失败')
        tomorrow = datetime.now() + timedelta(days=1)
        with patch('alas.get_server_next_update', return_value=tomorrow):
            self.assertTrue(script._record_task_restart('Commission', 'recoverable'))
        self.assertEqual(script.task_restart_delays['Commission'], tomorrow)
        self.webui.assert_called_once()
        self.assertEqual(self.webui.call_args.args, ('test',))
        self.assertEqual(self.webui.call_args.kwargs['title'], '任务已延后至次日')

    def test_webui_notification_failure_does_not_undo_deferral_or_push(self):
        script = self.make_script()
        script.config.Error_TaskRestartLimit = 1
        script.task_restart_record['Commission'] = 1
        self.webui.side_effect = RuntimeError('WebUI 通知失败')
        tomorrow = datetime.now() + timedelta(days=1)
        with patch('alas.get_server_next_update', return_value=tomorrow):
            self.assertTrue(script._record_task_restart('Commission', 'recoverable'))
        self.assertEqual(script.task_restart_delays['Commission'], tomorrow)
        self.notify.assert_called_once()
        self.webui.assert_called_once()

    def test_failed_results_allow_full_emulator_restart_budget_and_retries(self):
        script = self.make_script()
        script.config.Error_TaskRestartLimit = 2
        script.run = Mock(return_value=False)
        tomorrow = datetime.now() + timedelta(days=1)
        with patch('alas.get_server_next_update', return_value=tomorrow):
            self.run_tasks(script, ['Commission'] * 9)
        self.assertEqual(script._try_restart_emulator.call_count, 2)
        script.config.task_delay.assert_called_once_with(target=tomorrow, task='Commission')

    def test_midnight_deferral_is_always_in_the_future(self):
        script = self.make_script()
        script.config.Error_TaskRestartLimit = 1
        script.task_restart_record['Commission'] = 1
        now = datetime(2026, 10, 1)
        with (patch('alas.current_time', return_value=now),
              patch('alas.get_server_next_update', return_value=now) as next_update):
            script._record_task_restart('Commission', False)
        next_update.assert_called_once_with('00:00')
        script.config.task_delay.assert_called_once_with(
            target=now + timedelta(days=1), task='Commission')


if __name__ == '__main__':
    unittest.main()
