"""隔离验证模拟器系统运行时长读取和强制定时重启，不操作真实设备。"""

import unittest
from unittest.mock import Mock, PropertyMock, call, patch

from alas import AzurLaneAutoScript, WATCHDOG_CHECK_INTERVAL
from module.device.connection import Connection
from module.exception import EmulatorOpBusy


class TestEmulatorUptime(unittest.TestCase):
    def setUp(self):
        self.logger = self.enterContext(patch('module.device.connection.logger'))
        self.device = Connection.__new__(Connection)
        self.device.config = Mock(DEVICE_OVER_HTTP=False)
        self.device.serial = '127.0.0.1:16480'
        self.device.adb = Mock()

    def test_reads_first_value_through_current_device_shell(self):
        self.device.adb.shell.return_value = '173325.24 448753.33\n'

        self.assertEqual(self.device.get_emulator_uptime(), 173325.24)
        self.device.adb.shell.assert_called_once_with(
            ['cat', '/proc/uptime'], stream=False, timeout=5, rstrip=True,
        )
        self.logger.warning.assert_not_called()

    def test_zero_is_valid_and_reboot_is_not_cached(self):
        self.device.adb.shell.side_effect = ['173325.24 448753.33', '0.00 0.00', '15.25 6.00']

        self.assertEqual(self.device.get_emulator_uptime(), 173325.24)
        self.assertEqual(self.device.get_emulator_uptime(), 0)
        self.assertEqual(self.device.get_emulator_uptime(), 15.25)

    def test_invalid_values_are_skipped(self):
        for output in (
            '', '123', '123 456 extra', 'cat: /proc/uptime: Permission denied',
            'nan 0', 'inf 0', '-1 0', '12 nan', '12 inf', '12 -1',
        ):
            with self.subTest(output=output):
                self.device.adb.shell.return_value = output
                self.assertIsNone(self.device.get_emulator_uptime())
        self.assertEqual(self.logger.warning.call_count, 10)

    def test_connection_failure_only_skips_reading(self):
        self.device.adb.shell.side_effect = TimeoutError('ADB 读取超时')
        self.device.adb_reconnect = Mock()
        self.device.adb_start_server = Mock()

        self.assertIsNone(self.device.get_emulator_uptime())
        self.device.adb_reconnect.assert_not_called()
        self.device.adb_start_server.assert_not_called()
        self.logger.warning.assert_called_once()

    def test_http_device_uses_existing_shell_transport(self):
        self.device.config.DEVICE_OVER_HTTP = True
        self.device.u2 = Mock()
        self.device.u2.shell.return_value.output = '3600.50 7200.00'

        self.assertEqual(self.device.get_emulator_uptime(), 3600.5)
        self.device.u2.shell.assert_called_once_with(['cat', '/proc/uptime'], stream=False, timeout=5)
        self.device.adb.shell.assert_not_called()


class TestSchedulerUptimeReader(unittest.TestCase):
    def setUp(self):
        self.logger = self.enterContext(patch('alas.logger'))
        self.script = AzurLaneAutoScript('test')
        self.script.config = Mock()

    def test_reuses_connected_device(self):
        self.script.device = Mock()
        self.script.device.get_emulator_uptime.return_value = 7200.0
        with patch('module.device.platform.Platform') as platform:
            self.assertEqual(self.script._get_emulator_uptime(), 7200.0)
        platform.assert_not_called()

    def test_startup_does_not_initialize_full_device(self):
        with (
            patch('module.device.platform.Platform') as platform,
            patch.object(
                AzurLaneAutoScript, 'device', new_callable=PropertyMock,
                side_effect=AssertionError('不得初始化完整设备'),
            ) as device,
        ):
            platform.return_value.get_emulator_uptime.return_value = 173325.24
            self.assertEqual(self.script._get_emulator_uptime(), 173325.24)
        platform.assert_called_once_with(self.script.config, connect=False)
        device.assert_not_called()
        self.assertNotIn('device', self.script.__dict__)

    def test_lightweight_initialization_failure_is_skipped(self):
        with patch('module.device.platform.Platform', side_effect=RuntimeError('无法访问设备')):
            self.assertIsNone(self.script._get_emulator_uptime())
        self.logger.warning.assert_called_once()

    def test_reads_are_published_with_instance_serial_and_time(self):
        self.script.config.Emulator_Serial = '127.0.0.1:16480'
        self.script.device = Mock()
        self.script.device.get_emulator_uptime.side_effect = [3600, None]
        with (
            patch('module.runtime.worker_events.set_emulator_uptime') as publish,
            patch('alas.time.time', side_effect=[1000, 1001]),
        ):
            self.assertEqual(self.script._get_emulator_uptime(), 3600)
            self.assertIsNone(self.script._get_emulator_uptime())
        self.assertEqual(publish.call_args_list, [
            call('127.0.0.1:16480', 3600, 1000), call('127.0.0.1:16480', None, 1001),
        ])

    def test_closed_ui_channel_cannot_change_valid_restart_reading(self):
        self.script.device = Mock()
        self.script.device.get_emulator_uptime.return_value = 3600
        with (
            patch('module.runtime.worker_events._sink', Mock(side_effect=BrokenPipeError('通道关闭'))),
            patch('module.logger.logger.warning'),
        ):
            self.assertEqual(self.script._get_emulator_uptime(), 3600)

    def test_status_publication_does_not_initialize_missing_config(self):
        self.script.__dict__.pop('config')
        self.script.device = Mock()
        self.script.device.get_emulator_uptime.return_value = 3600
        with (
            patch.object(AzurLaneAutoScript, 'config', new_callable=PropertyMock,
                         side_effect=RuntimeError('配置不可用')) as config,
            patch('module.runtime.worker_events.set_emulator_uptime') as publish,
        ):
            self.assertEqual(self.script._get_emulator_uptime(), 3600)
        config.assert_not_called()
        publish.assert_not_called()


class TestForceScheduledUptime(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('alas.logger'))
        self.script = AzurLaneAutoScript('test')
        self.script.config = Mock(
            EmulatorManagement_ScheduledEmulatorRestart=True,
            EmulatorManagement_ForceScheduledRestart=True,
            EmulatorManagement_RestartIntervalHours=8,
            Error_WatchdogTaskEnable=False,
        )
        self.script.config.cross_get.return_value = False
        self.script._watchdog_active = True
        self.script._watchdog_task_name = 'Commission'
        self.script._watchdog_task_start = 100.0
        self.script._get_emulator_uptime = Mock(return_value=8 * 3600)
        self.script._watchdog_stop = Mock()
        self.platform = self.enterContext(patch('module.device.platform.Platform'))
        # 直接同步执行隔离后的停止方法，验证真实恢复逻辑，避免辅助线程和真实设备操作。
        self.script._emulator_op_with_timeout = Mock(side_effect=lambda func, **kwargs: func())

    def run_checks(self, count=1):
        self.script._watchdog_stop.wait.side_effect = [False] * count + [True]
        self.script._watchdog_loop()
        self.assertEqual(
            self.script._watchdog_stop.wait.call_args,
            call(WATCHDOG_CHECK_INTERVAL),
        )

    def test_threshold_is_based_on_system_uptime_and_stop_is_not_repeated(self):
        self.run_checks(2)

        self.platform.return_value.emulator_stop.assert_called_once_with()
        self.script._get_emulator_uptime.assert_called_once_with()

    def test_sensitive_task_is_protected(self):
        self.script.config.cross_get.return_value = True
        self.run_checks()

        self.platform.assert_not_called()
        self.script._get_emulator_uptime.assert_not_called()

    def test_below_threshold_and_failed_read_do_not_stop_emulator(self):
        self.script._get_emulator_uptime.side_effect = [8 * 3600 - 1, None, 10.0]
        self.run_checks(3)

        self.platform.assert_not_called()

    def test_disabled_or_idle_watchdog_does_not_read_uptime(self):
        for scheduled, force, active in ((False, True, True), (True, False, True), (True, True, False)):
            with self.subTest(scheduled=scheduled, force=force, active=active):
                self.script.config.EmulatorManagement_ScheduledEmulatorRestart = scheduled
                self.script.config.EmulatorManagement_ForceScheduledRestart = force
                self.script._watchdog_active = active
                self.run_checks()
        self.script._get_emulator_uptime.assert_not_called()
        self.platform.assert_not_called()

    def test_task_switch_during_read_does_not_stop_next_task(self):
        def change_task():
            self.script._watchdog_task_name = 'OpsiCrossMonth'
            self.script._watchdog_task_start = 200.0
            return 8 * 3600

        self.script._get_emulator_uptime.side_effect = change_task
        self.run_checks()

        self.platform.assert_not_called()

    def test_task_finish_during_read_does_not_stop_emulator(self):
        def finish_task():
            self.script._watchdog_active = False
            return 8 * 3600

        self.script._get_emulator_uptime.side_effect = finish_task
        self.run_checks()

        self.platform.assert_not_called()

    def test_failed_stop_allows_retry_in_next_check(self):
        for error in (EmulatorOpBusy('启停中'), TimeoutError('停止超时'), RuntimeError('停止失败')):
            with self.subTest(error=type(error).__name__):
                self.script._watchdog_scheduled_restart_task = None
                self.platform.return_value.emulator_stop.reset_mock()
                self.platform.return_value.emulator_stop.side_effect = [error, None]
                self.run_checks(2)
                self.assertEqual(self.platform.return_value.emulator_stop.call_count, 2)

    def test_next_task_checks_new_system_uptime(self):
        self.run_checks()
        self.script._watchdog_task_start = 200.0
        self.script._get_emulator_uptime.return_value = 60.0
        self.run_checks()

        self.assertEqual(self.script._get_emulator_uptime.call_count, 2)
        self.platform.return_value.emulator_stop.assert_called_once_with()

    def test_stop_returning_false_allows_retry(self):
        self.platform.return_value.emulator_stop.side_effect = [False, True]
        self.run_checks(2)

        self.assertEqual(self.platform.return_value.emulator_stop.call_count, 2)

    def test_failed_uptime_read_does_not_disable_task_timeout_recovery(self):
        self.script._get_emulator_uptime.return_value = None
        self.script.config.Error_WatchdogTaskEnable = True
        self.script.config.Error_WatchdogTaskTimeout = 1
        self.script._watchdog_recover = Mock()
        with patch('alas.time.monotonic', return_value=161.0):
            self.run_checks()

        self.script._watchdog_recover.assert_called_once_with(
            61.0, reason='task_timeout', task_name='Commission',
        )


if __name__ == '__main__':
    unittest.main()
