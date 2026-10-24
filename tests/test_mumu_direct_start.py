"""MuMu 按实例快捷方式的目标和起始目录隐藏启动，不运行真实设备。"""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from module.device.env import IS_WINDOWS

if IS_WINDOWS:
    from module.device.platform import platform_windows
    from module.device.platform.emulator_windows import EmulatorInstance
    from module.device.platform.platform_windows import EmulatorUnknown, PlatformWindows


@unittest.skipUnless(IS_WINDOWS, 'Windows MuMu 实例启动测试')
class TestMumuDirectStart(unittest.TestCase):
    def setUp(self):
        self.platform = PlatformWindows.__new__(PlatformWindows)
        self.platform.execute = Mock()
        self.platform._mumu12_other_instance_running = Mock(return_value=False)
        self.supports_hidden = self.enterContext(patch.object(
            platform_windows, '_mumu_supports_hidden_start', return_value=True))
        self.enterContext(patch.object(platform_windows, 'logger'))

    def start(self, path, name):
        instance = EmulatorInstance(serial='127.0.0.1:16480', name=name, path=path)
        original_path = instance.path
        self.platform._emulator_start(instance)
        self.assertEqual(instance.path, original_path, '直启不能改写用于发现、停止和监视的实例路径')
        return instance

    def assert_command(self, command, cwd=None):
        self.platform.execute.assert_called_once()
        self.assertEqual(len(self.platform.execute.call_args.args), 1)
        self.assertEqual(self.platform.execute.call_args.kwargs, {} if cwd is None else {'cwd': cwd})
        self.assertEqual(self.platform.execute.call_args.args[0].replace('\\', '/'), command)

    def test_current_android15_instance_three_matches_shortcut_target_arguments_and_directory(self):
        self.start('D:/MuMu/nx_main/MuMuNxMain.exe', 'MuMuPlayer-15.0-3')

        self.assert_command('"D:/MuMu/nx_main/MuMuNxMain.exe" -v 3 --engine_series=15.0 --start-hidden',
                            cwd='D:/MuMu/nx_main')
        self.supports_hidden.assert_called_once_with('D:/MuMu/nx_main/MuMuNxMain.exe')
        self.platform._mumu12_other_instance_running.assert_called_once_with(
            'D:/MuMu/nx_main/MuMuNxMain.exe', 3)

    def test_android12_and_global_names_keep_their_engine_and_nonzero_index(self):
        cases = (
            ('MuMuPlayer-12.0-0', '12.0', 0),
            ('MuMuPlayer-12.0-7', '12.0', 7),
            ('MuMuPlayerGlobal-12.0-11', '12.0', 11),
            ('MuMuPlayerGlobal-15.0-17', '15.0', 17),
        )
        for name, engine, index in cases:
            with self.subTest(name=name):
                self.platform.execute.reset_mock()

                self.start('D:/MuMu/nx_main/MuMuNxMain.exe', name)

                self.assert_command(
                    f'"D:/MuMu/nx_main/MuMuNxMain.exe" -v {index} --engine_series={engine} --start-hidden',
                    cwd='D:/MuMu/nx_main')

    def test_executable_path_with_spaces_and_chinese_is_quoted(self):
        self.start('D:/MuMu Fixtures/模拟器/nx_main/MuMuNxMain.exe', 'MuMuPlayer-15.0-3')

        self.assert_command(
            '"D:/MuMu Fixtures/模拟器/nx_main/MuMuNxMain.exe" -v 3 --engine_series=15.0 --start-hidden',
            cwd='D:/MuMu Fixtures/模拟器/nx_main')

    def test_windows_directory_and_executable_case_do_not_disable_direct_start(self):
        self.start('D:/MuMu/NX_MAIN/MUMUNXMAIN.EXE', 'MuMuPlayer-15.0-3')

        self.assert_command('"D:/MuMu/NX_MAIN/MUMUNXMAIN.EXE" -v 3 --engine_series=15.0 --start-hidden',
                            cwd='D:/MuMu/NX_MAIN')

    def test_unsupported_hidden_flag_falls_back_to_manager(self):
        self.supports_hidden.return_value = False

        self.start('D:/MuMu/nx_main/MuMuNxMain.exe', 'MuMuPlayer-15.0-3')

        self.assert_command('"D:/MuMu/nx_main/MuMuManager.exe" api -v 3 launch_player')
        self.platform._mumu12_other_instance_running.assert_not_called()

    def test_main_start_does_not_require_device_executable(self):
        with patch.object(platform_windows.os.path, 'isfile', return_value=False):
            self.start('D:/MuMu/nx_main/MuMuNxMain.exe', 'MuMuPlayer-15.0-3')

        self.assert_command('"D:/MuMu/nx_main/MuMuNxMain.exe" -v 3 --engine_series=15.0 --start-hidden',
                            cwd='D:/MuMu/nx_main')

    def test_uppercase_main_executable_still_falls_back_to_manager_not_gui(self):
        self.platform._mumu12_other_instance_running.return_value = True

        self.start('D:/MuMu/NX_MAIN/MUMUNXMAIN.EXE', 'MuMuPlayer-15.0-3')

        self.assert_command('"D:/MuMu/NX_MAIN/MuMuManager.exe" api -v 3 launch_player')

    def test_old_player_layout_keeps_manager(self):
        self.start('D:/MuMu/shell/MuMuPlayer.exe', 'MuMuPlayer-12.0-2')

        self.assert_command('"D:/MuMu/shell/MuMuManager.exe" api -v 2 launch_player')
        self.platform._mumu12_other_instance_running.assert_not_called()

    def test_main_executable_outside_nx_main_keeps_manager(self):
        self.start('D:/MuMu/shell/MuMuNxMain.exe', 'MuMuPlayer-15.0-3')

        self.assert_command('"D:/MuMu/shell/MuMuManager.exe" api -v 3 launch_player')
        self.platform._mumu12_other_instance_running.assert_not_called()

    def test_old_player_executable_inside_nx_main_does_not_opt_into_direct_start(self):
        self.start('D:/MuMu/nx_main/MuMuPlayer.exe', 'MuMuPlayer-12.0-2')

        self.assert_command('"D:/MuMu/nx_main/MuMuManager.exe" api -v 2 launch_player')
        self.platform._mumu12_other_instance_running.assert_not_called()

    def test_recognized_other_mumu_instance_family_keeps_existing_manager_path(self):
        self.start('D:/MuMu/nx_main/MuMuNxMain.exe', 'YXArkNights-12.0-1')

        self.assert_command('"D:/MuMu/nx_main/MuMuManager.exe" api -v 1 launch_player')
        self.platform._mumu12_other_instance_running.assert_not_called()

    def test_running_other_instance_keeps_manager_queue(self):
        self.platform._mumu12_other_instance_running.return_value = True

        self.start('D:/MuMu/nx_main/MuMuNxMain.exe', 'MuMuPlayer-15.0-3')

        self.assert_command('"D:/MuMu/nx_main/MuMuManager.exe" api -v 3 launch_player')

    def test_unknown_other_instance_state_keeps_manager_queue(self):
        self.platform._mumu12_other_instance_running.return_value = None

        self.start('D:/MuMu/nx_main/MuMuNxMain.exe', 'MuMuPlayer-15.0-3')

        self.assert_command('"D:/MuMu/nx_main/MuMuManager.exe" api -v 3 launch_player')

    def test_unknown_instance_id_is_rejected_before_any_command(self):
        for name in ('unknown', 'MuMuPlayer-15.0-', 'MuMuPlayer-15.0--1'):
            with self.subTest(name=name):
                self.platform.execute.reset_mock()
                instance = EmulatorInstance(
                    serial='127.0.0.1:16480', name=name, path='D:/MuMu/nx_main/MuMuNxMain.exe')

                with self.assertRaises(EmulatorUnknown):
                    self.platform._emulator_start(instance)

                self.platform.execute.assert_not_called()
                self.platform._mumu12_other_instance_running.assert_not_called()

    def test_direct_launch_error_propagates_without_second_manager_launch(self):
        self.platform.execute.side_effect = OSError('离线夹具：启动入口不可执行')

        with self.assertRaisesRegex(OSError, '启动入口不可执行'):
            self.start('D:/MuMu/nx_main/MuMuNxMain.exe', 'MuMuPlayer-15.0-3')

        self.assert_command('"D:/MuMu/nx_main/MuMuNxMain.exe" -v 3 --engine_series=15.0 --start-hidden',
                            cwd='D:/MuMu/nx_main')

    def test_legacy_android6_keeps_its_original_player_command(self):
        self.start('D:/MuMu/emulator/nemu/EmulatorShell/NemuPlayer.exe', '')

        self.assert_command('"D:/MuMu/emulator/nemu/EmulatorShell/NemuPlayer.exe"')
        self.platform._mumu12_other_instance_running.assert_not_called()


@unittest.skipUnless(IS_WINDOWS, 'Windows 模拟器启动目录传递')
class TestMumuLaunchWorkingDirectory(unittest.TestCase):
    COMMAND = '"D:/MuMu Fixtures/模拟器/nx_main/MuMuNxMain.exe" -v 3 --engine_series=15.0 --start-hidden'
    DIRECTORY = 'D:/MuMu Fixtures/模拟器/nx_main'

    def test_async_launch_passes_directory_to_child_without_changing_parent(self):
        current = os.getcwd()
        process = Mock()
        with patch.object(platform_windows.subprocess, 'Popen', return_value=process) as spawn:
            self.assertIs(PlatformWindows.execute(self.COMMAND, cwd=self.DIRECTORY), process)

        self.assertEqual(spawn.call_args.args, (f'start "" /b {self.COMMAND}',))
        self.assertEqual(spawn.call_args.kwargs['cwd'], self.DIRECTORY)
        self.assertEqual(os.getcwd(), current)
        process.wait.assert_called_once_with(timeout=5)

    def test_synchronous_command_passes_directory_without_changing_parent(self):
        current = os.getcwd()
        result = subprocess.CompletedProcess('fixture', 0)
        with patch.object(platform_windows.subprocess, 'run', return_value=result) as run:
            self.assertIs(PlatformWindows.execute(
                self.COMMAND, wait=True, timeout=7, cwd=self.DIRECTORY), result)

        self.assertEqual(run.call_args.args, (self.COMMAND,))
        self.assertEqual(run.call_args.kwargs['cwd'], self.DIRECTORY)
        self.assertEqual(run.call_args.kwargs['timeout'], 7)
        self.assertEqual(os.getcwd(), current)

    def test_unspecified_directory_keeps_inherited_working_directory(self):
        with patch.object(platform_windows.subprocess, 'Popen') as spawn:
            PlatformWindows.execute(self.COMMAND)
        self.assertIsNone(spawn.call_args.kwargs['cwd'])

        with patch.object(platform_windows.subprocess, 'run') as run:
            PlatformWindows.execute(self.COMMAND, wait=True)
        self.assertIsNone(run.call_args.kwargs['cwd'])


@unittest.skipUnless(IS_WINDOWS, 'Windows MuMu 原生参数能力检测')
class TestMumuHiddenStartCapability(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory(
            prefix='mumu-hidden-start-', dir=r'D:\Codex\tmp'))
        self.executable = Path(self.directory) / 'MuMuNxMain.exe'

    def test_reads_native_option_without_modifying_or_retaining_the_file(self):
        content = b'MZ\x00fixture\x00start-hidden\x00starthidden\x00engine_series\x00'
        self.executable.write_bytes(content)

        self.assertTrue(platform_windows._mumu_supports_hidden_start(str(self.executable)))

        self.assertEqual(self.executable.read_bytes(), content)
        self.executable.unlink()  # Windows 上也必须已关闭文件和只读映射。
        self.assertFalse(self.executable.exists())

    def test_old_option_table_without_exact_flag_is_not_supported(self):
        self.executable.write_bytes(b'MZ\x00vmindex\x00mini\x00slience\x00starthidden\x00')

        self.assertFalse(platform_windows._mumu_supports_hidden_start(str(self.executable)))

    def test_empty_file_is_not_supported(self):
        self.executable.write_bytes(b'')

        self.assertFalse(platform_windows._mumu_supports_hidden_start(str(self.executable)))

    def test_missing_file_is_not_supported(self):
        self.assertFalse(platform_windows._mumu_supports_hidden_start(str(self.executable)))

    def test_unreadable_file_is_not_supported(self):
        with patch('builtins.open', side_effect=PermissionError('离线夹具：无读取权限')):
            self.assertFalse(platform_windows._mumu_supports_hidden_start(str(self.executable)))


if __name__ == '__main__':
    unittest.main()
