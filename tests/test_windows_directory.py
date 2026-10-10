"""Windows 目录前置测试：COM、窗口与系统打开操作全部替换，不操作真实桌面。"""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.api import windows_directory as directory


class NativeError(Exception):
    pass


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def shell_window(path, handle):
    return SimpleNamespace(
        HWND=handle,
        Document=SimpleNamespace(Folder=SimpleNamespace(Self=SimpleNamespace(Path=str(path)))),
    )


class WindowsDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.path = Path('archive with spaces/金菜/26年10月').absolute()
        self.windows = []
        self.foreground = 999
        self.roots = {}
        self.hidden = set()
        self.minimized = set()
        self.shell = SimpleNamespace(Windows=Mock(side_effect=lambda: list(self.windows)))
        self.native = directory._WindowsApi(
            pythoncom=SimpleNamespace(CoInitializeEx=Mock(), CoUninitialize=Mock(), COINIT_APARTMENTTHREADED=2),
            client=SimpleNamespace(Dispatch=Mock(return_value=self.shell)),
            gui=SimpleNamespace(
                GetAncestor=Mock(side_effect=lambda handle, _: self.roots.get(handle, handle)),
                IsWindow=Mock(return_value=True),
                GetClassName=Mock(return_value='CabinetWClass'),
                IsWindowVisible=Mock(side_effect=lambda handle: handle not in self.hidden),
                GetForegroundWindow=Mock(side_effect=lambda: self.foreground),
                IsIconic=Mock(side_effect=lambda handle: handle in self.minimized),
                ShowWindow=Mock(),
                SetForegroundWindow=Mock(side_effect=self.focus),
                BringWindowToTop=Mock(),
                SetActiveWindow=Mock(),
            ),
            process=SimpleNamespace(
                GetWindowThreadProcessId=Mock(return_value=(22, 222)),
                AttachThreadInput=Mock(),
            ),
            api=SimpleNamespace(GetCurrentThreadId=Mock(return_value=11)),
            constants=SimpleNamespace(GA_ROOT=2, SW_RESTORE=9),
            errors=(NativeError,),
        )
        self.clock = Clock()
        self.enterContext(patch.object(directory, '_load_windows_api', return_value=self.native))
        self.open = self.enterContext(patch.object(directory.os, 'startfile', create=True))
        self.enterContext(patch.object(directory.time, 'monotonic', side_effect=self.clock.monotonic))
        self.enterContext(patch.object(directory.time, 'sleep', side_effect=self.clock.sleep))

    def focus(self, handle):
        self.foreground = handle

    def assert_com_released(self):
        self.native.pythoncom.CoInitializeEx.assert_called_once_with(2)
        self.native.pythoncom.CoUninitialize.assert_called_once_with()

    def test_existing_exact_directory_is_reused_and_confirmed_at_foreground(self):
        self.windows = [shell_window(self.path, 100)]
        directory.open_directory(self.path)
        self.assertEqual(self.foreground, 100)
        self.open.assert_not_called()
        self.native.gui.SetForegroundWindow.assert_called_once_with(100)
        self.native.process.AttachThreadInput.assert_not_called()
        self.assert_com_released()

    def test_already_foreground_directory_does_not_repeat_opening_or_focus(self):
        self.windows = [shell_window(self.path, 100)]
        self.foreground = 100
        directory.open_directory(self.path)
        self.open.assert_not_called()
        self.native.gui.SetForegroundWindow.assert_not_called()
        self.assert_com_released()

    def test_minimized_child_handle_restores_and_focuses_root_window(self):
        self.windows = [shell_window(self.path, 101)]
        self.roots[101] = 100
        self.minimized.add(100)
        directory.open_directory(self.path)
        self.native.gui.ShowWindow.assert_called_once_with(100, 9)
        self.assertEqual(self.foreground, 100)
        self.open.assert_not_called()
        self.assert_com_released()

    def test_only_exact_normalized_path_matches_not_similar_folder_name(self):
        self.windows = [shell_window(self.path.parent / '26年10月-other', 50)]
        self.open.side_effect = lambda _: self.windows.append(shell_window(self.path, 100))
        directory.open_directory(self.path)
        self.open.assert_called_once_with(str(self.path))
        self.native.gui.SetForegroundWindow.assert_called_once_with(100)
        self.assertEqual(self.foreground, 100)
        self.assert_com_released()

    def test_matching_ignores_windows_case_and_trailing_separator(self):
        self.windows = [shell_window(str(self.path).upper() + '/', 100)]
        directory.open_directory(self.path)
        self.assertEqual(self.foreground, 100)
        self.open.assert_not_called()
        self.assert_com_released()

    def test_non_explorer_window_is_not_used_even_when_path_matches(self):
        self.windows = [shell_window(self.path, 50)]
        self.native.gui.GetClassName.side_effect = lambda handle: 'Chrome_WidgetWin_1' if handle == 50 else 'CabinetWClass'
        self.open.side_effect = lambda _: self.windows.append(shell_window(self.path, 100))
        directory.open_directory(self.path)
        self.native.gui.SetForegroundWindow.assert_called_once_with(100)
        self.assert_com_released()

    def test_new_window_can_appear_after_shell_open_request(self):
        scans = 0

        def windows():
            nonlocal scans
            scans += 1
            return [shell_window(self.path, 100)] if self.open.called and scans >= 5 else []

        self.shell.Windows.side_effect = windows
        directory.open_directory(self.path)
        self.open.assert_called_once_with(str(self.path))
        self.assertEqual(self.foreground, 100)
        self.assertGreater(len(self.clock.sleeps), 0)
        self.assertLess(self.clock.now, 3)
        self.assert_com_released()

    def test_attach_is_only_fallback_and_is_detached_after_success(self):
        self.windows = [shell_window(self.path, 100)]
        attached = False

        def attach(_, __, value):
            nonlocal attached
            attached = value

        def focus(handle):
            if attached:
                self.foreground = handle
            else:
                raise NativeError('foreground denied')

        self.native.process.AttachThreadInput.side_effect = attach
        self.native.gui.SetForegroundWindow.side_effect = focus
        directory.open_directory(self.path)
        self.assertFalse(attached)
        self.assertEqual(self.native.process.AttachThreadInput.call_args_list,
                         [unittest.mock.call(11, 22, True), unittest.mock.call(11, 22, False)])
        self.assertEqual(self.foreground, 100)
        self.open.assert_not_called()
        self.assert_com_released()

    def test_attach_is_detached_when_set_foreground_raises(self):
        self.windows = [shell_window(self.path, 100)]
        self.native.gui.SetForegroundWindow.side_effect = NativeError('denied')
        with self.assertRaises(directory.DirectoryForegroundError) as raised:
            directory.open_directory(self.path)
        self.assertEqual(str(raised.exception), directory.FOREGROUND_FAILURE_MESSAGE)
        calls = self.native.process.AttachThreadInput.call_args_list
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[0], unittest.mock.call(11, 22, True))
        self.assertEqual(calls[1], unittest.mock.call(11, 22, False))
        self.assertEqual(calls[2], unittest.mock.call(11, 22, True))
        self.assertEqual(calls[3], unittest.mock.call(11, 22, False))
        self.assert_com_released()

    def test_foreground_and_target_threads_are_attached_and_detached_in_reverse_order(self):
        self.windows = [shell_window(self.path, 100)]
        self.native.process.GetWindowThreadProcessId.side_effect = lambda handle: (33 if handle == 100 else 22, 222)
        self.native.gui.SetForegroundWindow.side_effect = NativeError('denied without target input thread')
        self.native.gui.BringWindowToTop.side_effect = self.focus
        directory.open_directory(self.path)
        self.assertEqual(self.foreground, 100)
        self.native.gui.BringWindowToTop.assert_called_once_with(100)
        self.native.gui.SetActiveWindow.assert_called_once_with(100)
        self.assertEqual(self.native.process.AttachThreadInput.call_args_list,
                         [unittest.mock.call(11, 22, True), unittest.mock.call(11, 33, True),
                          unittest.mock.call(11, 33, False), unittest.mock.call(11, 22, False)])
        self.open.assert_not_called()
        self.assert_com_released()

    def test_detach_failure_still_attempts_to_release_all_attached_threads(self):
        self.windows = [shell_window(self.path, 100)]
        self.native.process.GetWindowThreadProcessId.side_effect = lambda handle: (33 if handle == 100 else 22, 222)
        self.native.gui.SetForegroundWindow.side_effect = NativeError('initial denial')

        def attach(_, thread, value):
            if thread == 33 and not value:
                raise NativeError('detach failure')

        self.native.process.AttachThreadInput.side_effect = attach
        with self.assertRaises(directory.DirectoryForegroundError):
            directory.open_directory(self.path)
        self.assertEqual(self.native.process.AttachThreadInput.call_args_list,
                         [unittest.mock.call(11, 22, True), unittest.mock.call(11, 33, True),
                          unittest.mock.call(11, 33, False), unittest.mock.call(11, 22, False)])
        self.assert_com_released()

    def test_failed_attachment_is_not_detached_but_successful_target_attachment_is(self):
        self.windows = [shell_window(self.path, 100)]
        self.native.process.GetWindowThreadProcessId.side_effect = lambda handle: (33 if handle == 100 else 22, 222)
        self.native.gui.SetForegroundWindow.side_effect = NativeError('initial denial')
        self.native.gui.BringWindowToTop.side_effect = self.focus

        def attach(_, thread, value):
            if thread == 22 and value:
                raise NativeError('cannot attach foreground thread')

        self.native.process.AttachThreadInput.side_effect = attach
        directory.open_directory(self.path)
        self.assertEqual(self.native.process.AttachThreadInput.call_args_list,
                         [unittest.mock.call(11, 22, True), unittest.mock.call(11, 33, True),
                          unittest.mock.call(11, 33, False)])
        self.assertEqual(self.foreground, 100)
        self.assert_com_released()

    def test_same_input_thread_is_never_attached_to_itself(self):
        self.windows = [shell_window(self.path, 100)]
        self.native.api.GetCurrentThreadId.return_value = 22
        self.native.gui.SetForegroundWindow.side_effect = NativeError('denied')
        with self.assertRaises(directory.DirectoryForegroundError):
            directory.open_directory(self.path)
        self.native.process.AttachThreadInput.assert_not_called()
        self.assert_com_released()

    def test_unconfirmed_window_or_false_foreground_result_times_out_in_three_seconds(self):
        self.windows = [shell_window(self.path, 100)]
        self.native.gui.SetForegroundWindow.side_effect = lambda _: None
        with self.assertRaises(directory.DirectoryForegroundError) as raised:
            directory.open_directory(self.path)
        self.assertEqual(str(raised.exception), directory.FOREGROUND_FAILURE_MESSAGE)
        self.open.assert_called_once_with(str(self.path))
        self.assertAlmostEqual(self.clock.now, 3.0)
        self.assertTrue(all(0 < duration <= 0.1 for duration in self.clock.sleeps))
        self.assert_com_released()

    def test_ambiguous_windows_11_tabs_do_not_claim_wrong_page_is_foreground(self):
        self.windows = [shell_window(self.path, 100), shell_window(self.path.parent, 100)]
        self.foreground = 100
        with self.assertRaises(directory.DirectoryForegroundError):
            directory.open_directory(self.path)
        self.open.assert_called_once_with(str(self.path))
        self.native.gui.SetForegroundWindow.assert_not_called()
        self.assert_com_released()

    def test_hidden_target_tab_is_opened_then_verified_in_visible_window(self):
        self.windows = [shell_window(self.path, 101), shell_window(self.path.parent, 102)]
        self.roots.update({101: 100, 102: 100})
        self.hidden.add(101)
        self.foreground = 100
        self.open.side_effect = lambda _: self.windows.append(shell_window(self.path, 200))
        directory.open_directory(self.path)
        self.assertEqual(self.foreground, 200)
        self.native.gui.SetForegroundWindow.assert_called_once_with(200)
        self.assert_com_released()

    def test_shell_initialization_failure_still_opens_but_reports_unconfirmed_foreground(self):
        self.native.client.Dispatch.side_effect = NativeError('COM unavailable')
        with self.assertRaises(directory.DirectoryForegroundError):
            directory.open_directory(self.path)
        self.open.assert_called_once_with(str(self.path))
        self.assert_com_released()

    def test_failed_com_initialization_is_not_uninitialized(self):
        self.native.pythoncom.CoInitializeEx.side_effect = NativeError('COM unavailable')
        with self.assertRaises(directory.DirectoryForegroundError):
            directory.open_directory(self.path)
        self.open.assert_called_once_with(str(self.path))
        self.native.pythoncom.CoUninitialize.assert_not_called()

    def test_existing_mta_com_apartment_is_used_without_uninitializing_owner(self):
        error = NativeError('already initialized with a different apartment')
        error.hresult = -2147417850
        self.native.pythoncom.CoInitializeEx.side_effect = error
        self.windows = [shell_window(self.path, 100)]
        directory.open_directory(self.path)
        self.assertEqual(self.foreground, 100)
        self.open.assert_not_called()
        self.native.pythoncom.CoInitializeEx.assert_called_once_with(2)
        self.native.pythoncom.CoUninitialize.assert_not_called()

    def test_native_open_error_remains_distinct_from_foreground_failure(self):
        self.open.side_effect = OSError('cannot open')
        with self.assertRaises(OSError):
            directory.open_directory(self.path)
        self.assert_com_released()

    def test_window_path_that_changes_during_focus_is_not_reported_as_success(self):
        self.windows = [shell_window(self.path, 100)]

        def focus(handle):
            self.foreground = handle
            self.windows = [shell_window(self.path.parent, 100)]

        self.native.gui.SetForegroundWindow.side_effect = focus
        with self.assertRaises(directory.DirectoryForegroundError):
            directory.open_directory(self.path)
        self.assert_com_released()

    def test_disappearing_shell_entry_does_not_prevent_valid_directory_detection(self):
        self.windows = [SimpleNamespace(HWND=50), shell_window(self.path, 100)]
        directory.open_directory(self.path)
        self.assertEqual(self.foreground, 100)
        self.assert_com_released()


if __name__ == '__main__':
    unittest.main()
