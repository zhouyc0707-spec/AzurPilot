"""「温柔停止」的离线回归测试。

需求：点「停止调度器」时不要立刻杀进程，而是先通知 worker 让当前任务在安全点退出
（长任务沿用它们被高优先级任务打断时的那套逻辑），最多等 5 分钟；等待期间再点一次
停止则立即强制终止；更新/重启/WebUI 清理/MCP 等入口保持原来的立即终止。

覆盖：
- 有停止事件时 `stop_by_user()` 先置位事件、不立即杀进程，等任务退出后再走收尾；
- 超时（任务一直不退出）→ 强制终止并告警；
- 等待期间再次点击 → 立即强制终止；
- 没有事件（如 MCP 启动的 worker）或显式 `soft=False` → 保持立即终止。
- 用户停止在安全点自然退出后仍记为手动停止，真正更新的通用停止保留更新状态。
"""
import queue
import threading
import unittest
from unittest.mock import Mock, patch

from module.runtime.process_manager import ProcessManager
from module.runtime.setting import State
from module.runtime.worker_events import ExitEvent, WorkerResult


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
        State.manager.Queue.side_effect = queue.Queue
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
        self.manager.run_id = 'old-run'

        def restart_after_exit(manager):
            manager.run_id = 'new-run'
            manager.exit_result = None
            manager.current_task = 'Commission'
            return False

        with patch.object(ProcessManager, 'alive', property(restart_after_exit)), \
                patch.object(self.manager, '_stop_immediately') as immediate:
            self.manager._soft_stop_worker('goto_main', 'old-run')

        immediate.assert_not_called()
        self.assertIsNone(self.manager.exit_result)
        self.assertEqual(self.manager.current_task, 'Commission')

    def test_safe_exit_update_result_becomes_manual_stop(self):
        """安全点退出后登记已消失，也必须解除前端的更新中禁用状态。"""
        self.manager.run_id = 'soft-run'
        self.manager.exit_result = WorkerResult.UPDATE
        self.manager.current_task = 'Island'

        with patch.object(self.manager, '_registered_worker', return_value=(None, None, True)), \
                patch.object(self.manager, '_registered_pid', return_value=(None, True)), \
                patch.object(self.manager, '_unregister_process', return_value=True), \
                patch.object(self.manager, '_run_manual_stop_action_locked') as cleanup:
            self.assertEqual(self.manager.state, 4)
            self.manager._soft_stop_worker('stay_there', 'soft-run')
            # 迟到的退出事件同样不能把已确认的用户停止改回更新中。
            self.manager._renderable_queue.put(ExitEvent('soft-run', WorkerResult.UPDATE))
            self.assertEqual(self.manager.state, 2)

        self.assertEqual(self.manager.exit_result, WorkerResult.MANUAL_STOP)
        self.assertIsNone(self.manager.current_task)
        cleanup.assert_not_called()

    def test_failed_stop_does_not_confirm_manual_stop(self):
        """停止未通过身份验证时，不把未确认结束的 worker 显示为已停止。"""
        self.manager.run_id = 'soft-run'
        self.manager.exit_result = WorkerResult.UPDATE
        with patch.object(self.manager, '_registered_worker', return_value=(12345, None, False)), \
                patch.object(self.manager, '_registered_pid', return_value=(12345, False)), \
                patch.object(self.manager, '_unregister_process') as unregister:
            self.manager._soft_stop_worker('stay_there', 'soft-run')
            self.assertEqual(self.manager.state, 4)

        self.assertEqual(self.manager.exit_result, WorkerResult.UPDATE)
        unregister.assert_not_called()

    def test_update_stop_keeps_update_result(self):
        """更新清理使用通用 stop，已自然退出的更新 worker 仍为更新状态。"""
        self.manager.run_id = 'update-run'
        self.manager.exit_result = WorkerResult.UPDATE
        with patch.object(self.manager, '_registered_worker', return_value=(None, None, True)), \
                patch.object(self.manager, '_registered_pid', return_value=(None, True)), \
                patch.object(self.manager, '_unregister_process', return_value=True):
            self.assertTrue(self.manager.stop())
            self.assertEqual(self.manager.state, 4)

        self.assertEqual(self.manager.exit_result, WorkerResult.UPDATE)

    def test_result_confirmation_blocks_new_run_until_manual_stop_recorded(self):
        """校验轮次到确认停止期间不能插入新启动，新轮结果不得被覆盖。"""
        self.manager.run_id = 'soft-run'
        self.manager.exit_result = WorkerResult.UPDATE
        lock = ProcessManager._get_lifecycle_lock(self.manager.config_name)
        attempting = threading.Event()
        acquired = threading.Event()
        observed = []

        def start_next_run():
            attempting.set()
            with lock:
                observed.append(self.manager.exit_result)
                self.manager.run_id = 'next-run'
                self.manager.exit_result = None
                self.manager.current_task = 'Commission'
                acquired.set()

        starter = threading.Thread(target=start_next_run)

        def stopped(_action):
            starter.start()
            self.assertTrue(attempting.wait(timeout=1))
            self.assertFalse(acquired.wait(timeout=0.05), '停止结果确认前不能释放生命周期锁')
            return True

        with patch.object(ProcessManager, 'alive', alive_sequence([False])), \
                patch.object(self.manager, '_stop_immediately', side_effect=stopped):
            self.manager._soft_stop_worker('stay_there', 'soft-run')
        starter.join(timeout=1)

        self.assertFalse(starter.is_alive())
        self.assertEqual(observed, [WorkerResult.MANUAL_STOP])
        self.assertEqual(self.manager.run_id, 'next-run')
        self.assertIsNone(self.manager.exit_result)
        self.assertEqual(self.manager.current_task, 'Commission')

    def test_timeout_is_five_minutes(self):
        """等待上限固定为 5 分钟（用户要求；等待期间可再点一次强制停止）。"""
        self.assertEqual(ProcessManager.SOFT_STOP_TIMEOUT, 300)
        self.assertLessEqual(ProcessManager.SOFT_STOP_POLL_INTERVAL, 1)


if __name__ == '__main__':
    unittest.main()
