"""「温柔停止」的离线回归测试。

需求：点「停止调度器」时不要立刻杀进程，而是先通知 worker 让当前任务在安全点退出
（长任务沿用它们被高优先级任务打断时的那套逻辑），最多等 60 秒；等待期间再点一次
停止则立即强制终止；更新/重启/WebUI 清理/MCP 等入口保持原来的立即终止。

覆盖：
- 有停止事件时 `stop_by_user()` 先置位事件、不立即杀进程，等任务退出后再走收尾；
- 超时（任务一直不退出）→ 强制终止并告警；
- 等待期间再次点击 → 立即强制终止；
- 没有事件（如 MCP 启动的 worker）或显式 `soft=False` → 保持立即终止。
"""
import unittest
from unittest.mock import Mock, patch

from module.runtime.process_manager import ProcessManager
from module.runtime.setting import State


class FakeEvent:
    """只记录是否被置位的事件替身。"""

    def __init__(self):
        self.set_called = False
        self._set = False

    def set(self):
        self.set_called = True
        self._set = True

    def clear(self):
        self._set = False

    def is_set(self):
        return self._set


def alive_sequence(values):
    """按顺序返回存活状态的 alive property 替身，用完后固定为最后一个值。"""
    state = {'index': 0}

    def getter(_self):
        index = min(state['index'], len(values) - 1)
        state['index'] += 1
        return values[index]

    return property(getter)


class SoftStopTest(unittest.TestCase):
    def setUp(self):
        self.original_manager = State.manager
        State.manager = Mock()
        State.manager.Queue.return_value = Mock()
        self.manager = ProcessManager('test-soft-stop')
        self.event = FakeEvent()
        self.manager._notify_event = self.event

    def tearDown(self):
        State.manager = self.original_manager

    def test_soft_stop_signals_then_runs_action(self):
        """温柔停止：置位事件、先不杀进程，任务退出后执行收尾动作。"""
        calls = []

        with patch.object(ProcessManager, 'alive', alive_sequence([True, False])), \
                patch.object(self.manager, '_stop_immediately',
                             side_effect=lambda action=None: calls.append(action) or True):
            result = self.manager.stop_by_user('goto_main')
            self.manager._soft_stop_thread.join(timeout=5)

        self.assertTrue(result, '温柔停止应立即受理')
        self.assertTrue(self.event.set_called, '必须先置位停止事件通知 worker')
        self.assertEqual(calls, ['goto_main'], '任务退出后就该执行收尾动作')

    def test_timeout_falls_back_to_force_stop(self):
        """任务一直不退出：等满超时后强制终止。"""
        self.manager.SOFT_STOP_TIMEOUT = 0.0
        calls = []

        with patch.object(ProcessManager, 'alive', alive_sequence([True])), \
                patch.object(self.manager, '_stop_immediately',
                             side_effect=lambda action=None: calls.append(action) or True):
            self.manager.stop_by_user('close_game')
            self.manager._soft_stop_thread.join(timeout=5)

        self.assertEqual(calls, ['close_game'], '超时后仍应强制终止并执行收尾')

    def test_second_click_forces_stop(self):
        """等待期间再点一次停止：立即强制终止。"""
        self.manager._soft_stop_thread = Mock()
        self.manager._soft_stop_thread.is_alive.return_value = True

        with patch.object(self.manager, '_stop_immediately', return_value=True) as immediate:
            result = self.manager.stop_by_user('goto_main')

        immediate.assert_called_once_with('goto_main')
        self.assertTrue(self.manager._soft_stop_force.is_set(), '应置位强制停止信号')
        self.assertTrue(result)

    def test_without_event_keeps_immediate_stop(self):
        """没有停止事件（如 MCP 启动的 worker）：保持立即终止。"""
        self.manager._notify_event = None

        with patch.object(self.manager, '_stop_immediately', return_value=True) as immediate:
            result = self.manager.stop_by_user('stay_there')

        immediate.assert_called_once_with('stay_there')
        self.assertTrue(result)

    def test_soft_false_keeps_immediate_stop(self):
        """显式 soft=False（MCP 入口）：保持立即终止。"""
        with patch.object(self.manager, '_stop_immediately', return_value=True) as immediate:
            result = self.manager.stop_by_user(soft=False)

        immediate.assert_called_once()
        self.assertTrue(result)

    def test_stopping_property_tracks_wait_thread(self):
        """stopping 反映等待线程是否还在跑（前端据此显示「停止中…」）。"""
        self.assertFalse(self.manager.stopping)
        self.manager._soft_stop_thread = Mock()
        self.manager._soft_stop_thread.is_alive.return_value = True
        self.assertTrue(self.manager.stopping)

    def test_new_run_during_wait_is_not_killed(self):
        """等待期间用户重新启动：不能把新 worker 停掉、也不跑收尾动作。"""
        calls = []
        self.manager.run_id = 'old-run'

        with patch.object(ProcessManager, 'alive', alive_sequence([True, False])), \
                patch.object(self.manager, '_stop_immediately',
                             side_effect=lambda action=None: calls.append(action) or True):
            self.manager.stop_by_user('goto_main')
            # 任务退出、等待线程尚未收尾时，用户点了启动（run_id 变化）
            self.manager.run_id = 'new-run'
            self.manager._soft_stop_thread.join(timeout=5)

        self.assertEqual(calls, [], '新一轮运行不应被上一轮的停止收尾误杀')


if __name__ == '__main__':
    unittest.main()
