"""打开指定 Windows 目录，并核验对应资源管理器窗口已经到前台。"""
import ntpath
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


FOREGROUND_FAILURE_MESSAGE = '文件夹已打开，但未能切换到前台，请从任务栏查看'
_WAIT_SECONDS = 3.0
_POLL_SECONDS = 0.1
_RPC_E_CHANGED_MODE = -2147417850


class DirectoryForegroundError(RuntimeError):
    """目录已交给系统打开，但无法确认正确的目录窗口已到前台。"""


@dataclass(frozen=True)
class _WindowsApi:
    pythoncom: Any
    client: Any
    gui: Any
    process: Any
    api: Any
    constants: Any
    errors: tuple[type[Exception], ...]


def _load_windows_api() -> _WindowsApi:
    # 延迟导入，非 Windows 服务无需安装或加载这些模块。
    import pythoncom
    import pywintypes
    import win32api
    import win32com.client
    import win32con
    import win32gui
    import win32process

    return _WindowsApi(pythoncom, win32com.client, win32gui, win32process,
                       win32api, win32con, (pythoncom.com_error, pywintypes.error))


def _normalized_path(path: str | Path) -> str:
    value = ntpath.normcase(ntpath.normpath(os.path.abspath(os.fspath(path))))
    if value.startswith('\\\\?\\unc\\'):
        return '\\\\' + value[8:]
    if value.startswith('\\\\?\\'):
        return value[4:]
    return value


def _matching_roots(shell: Any, native: _WindowsApi, target: str) -> list[int]:
    """只接受完整路径匹配且当前可见页面明确的资源管理器根窗口。"""
    errors = native.errors + (AttributeError, TypeError, ValueError, OSError)
    visible_paths: dict[int, set[str]] = {}
    try:
        windows = shell.Windows()
        for window in windows:
            try:
                path = _normalized_path(window.Document.Folder.Self.Path)
                handle = int(window.HWND)
                root = native.gui.GetAncestor(handle, native.constants.GA_ROOT) or handle
                if not native.gui.IsWindow(root):
                    continue
                if native.gui.GetClassName(root) not in ('CabinetWClass', 'ExploreWClass'):
                    continue
                if native.gui.IsWindowVisible(handle):
                    visible_paths.setdefault(root, set()).add(path)
            except errors:
                # 正在关闭或加载的 Shell 页面可能暂时没有 Document，下一次枚举再确认。
                continue
    except errors:
        return []
    # Windows 11 同一根窗口可能代表多个标签页；不能将其他页前置后宣称目标页已打开。
    return [root for root, paths in visible_paths.items() if paths == {target}]


def _foreground_matches(shell: Any, native: _WindowsApi, target: str) -> bool:
    return native.gui.GetForegroundWindow() in _matching_roots(shell, native, target)


def _bring_to_foreground(handle: int, native: _WindowsApi) -> None:
    """恢复窗口并短暂关联前台输入线程；所有关联均在 finally 中解除。"""
    try:
        if native.gui.IsIconic(handle):
            native.gui.ShowWindow(handle, native.constants.SW_RESTORE)
        native.gui.SetForegroundWindow(handle)
    except native.errors:
        pass
    if native.gui.GetForegroundWindow() == handle:
        return

    foreground = native.gui.GetForegroundWindow()
    current_thread = native.api.GetCurrentThreadId()
    threads = []
    for window in (foreground, handle):
        if window:
            thread = native.process.GetWindowThreadProcessId(window)[0]
            if thread != current_thread and thread not in threads:
                threads.append(thread)
    attached = []
    try:
        # 除当前前台线程外，还需要关联目标窗口线程，才能可靠恢复 Explorer 的活动窗口。
        for thread in threads:
            try:
                native.process.AttachThreadInput(current_thread, thread, True)
                attached.append(thread)
            except native.errors:
                continue
        for activate in (native.gui.BringWindowToTop, native.gui.SetActiveWindow, native.gui.SetForegroundWindow):
            try:
                activate(handle)
            except native.errors:
                continue
    finally:
        detach_error = None
        for thread in reversed(attached):
            try:
                native.process.AttachThreadInput(current_thread, thread, False)
            except native.errors as error:
                # 一个分离失败也不能阻止其他已建立的关联被解除。
                detach_error = error
        if detach_error is not None:
            raise detach_error


def open_directory(path: Path) -> None:
    """复用或打开指定目录，最多轮询三秒确认正确的资源管理器窗口到前台。

    该函数不发送按键、不修改系统前台策略，也不将窗口永久置顶。调用线程自身
    初始化 COM，避免 WebUI 后台工作线程依赖主线程的初始化状态。
    """
    try:
        native = _load_windows_api()
    except ImportError as error:
        os.startfile(str(path))
        raise DirectoryForegroundError(FOREGROUND_FAILURE_MESSAGE) from error

    initialized = False
    opened = False
    shell = None
    deadline = time.monotonic() + _WAIT_SECONDS
    target = _normalized_path(path)
    try:
        try:
            native.pythoncom.CoInitializeEx(native.pythoncom.COINIT_APARTMENTTHREADED)
            initialized = True
        except native.errors as error:
            # 已有 MTA 初始化的工作线程仍能使用 Shell，但不应撤销其他组件的 COM 初始化。
            if getattr(error, 'hresult', None) != _RPC_E_CHANGED_MODE:
                raise
        shell = native.client.Dispatch('Shell.Application')
        if _foreground_matches(shell, native, target):
            return
        for handle in _matching_roots(shell, native, target):
            opened = True
            _bring_to_foreground(handle, native)
            if _foreground_matches(shell, native, target):
                return

        # 不可见标签页或尚未存在的目录先交给 Shell 打开，再按实际路径确认。
        os.startfile(str(path))
        opened = True
        attempted: set[int] = set()
        while True:
            if _foreground_matches(shell, native, target):
                return
            for handle in _matching_roots(shell, native, target):
                if handle not in attempted:
                    attempted.add(handle)
                    _bring_to_foreground(handle, native)
                    if _foreground_matches(shell, native, target):
                        return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(_POLL_SECONDS, remaining))
    except native.errors as error:
        if not opened:
            os.startfile(str(path))
        raise DirectoryForegroundError(FOREGROUND_FAILURE_MESSAGE) from error
    finally:
        shell = None
        if initialized:
            native.pythoncom.CoUninitialize()
    raise DirectoryForegroundError(FOREGROUND_FAILURE_MESSAGE)
