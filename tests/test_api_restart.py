"""手动重启 WebUI（module/api/restart_service.py）的行为。

重启链路：clearup() 停止任务线程/实例并把运行中的实例记进启动记忆，再置位
``State.restart_event`` 交给父监督进程重新拉起；与自动更新事务用 restart_lock 互斥。
"""

import threading
import unittest
from unittest.mock import Mock, patch

from module.api.protocol import ApiError
from module.api.restart_service import request_restart
from module.runtime.setting import State


class RestartServiceTests(unittest.TestCase):
    def setUp(self):
        self.original_event = State.restart_event
        self.original_requested = State._restart_requested
        self.addCleanup(self._restore)

    def _restore(self):
        State.restart_event = self.original_event
        State._restart_requested = self.original_requested
        try:
            if State.restart_lock.locked():
                State.restart_lock.release()
        except RuntimeError:
            # 锁由别的线程持有（测试里模拟更新事务），只能等它自己释放
            pass

    def test_without_supervisor_process_it_refuses(self):
        State.restart_event = None
        with self.assertRaises(ApiError) as ctx:
            request_restart()
        self.assertEqual('UNAVAILABLE', ctx.exception.code)
        self.assertIn('手动重启', ctx.exception.message)

    def test_update_transaction_blocks_manual_restart(self):
        """restart_lock 是 RLock，只有别的线程持锁才算更新事务在跑。"""
        State.restart_event = threading.Event()
        held, release = threading.Event(), threading.Event()

        def hold_lock():
            State.restart_lock.acquire()
            held.set()
            release.wait(5)
            State.restart_lock.release()

        thread = threading.Thread(target=hold_lock, daemon=True)
        thread.start()
        self.assertTrue(held.wait(5), '持锁线程没能启动')
        try:
            with self.assertRaises(ApiError) as ctx:
                request_restart()
            self.assertEqual('CONFLICT', ctx.exception.code)
            self.assertFalse(State.restart_event.is_set())
        finally:
            release.set()
            thread.join(5)

    def test_restart_cleans_up_and_notifies_supervisor(self):
        State.restart_event = threading.Event()
        State._restart_requested = False
        with patch('module.api.lifecycle.clearup', Mock(return_value=True)) as clearup:
            self.assertEqual({'restarting': True}, request_restart())
        clearup.assert_called_once_with()
        self.assertTrue(State.restart_event.is_set())
        self.assertTrue(State._restart_requested)
        # 锁必须在返回前释放，否则后续重启会被自己的锁挡住
        self.assertFalse(State.restart_lock.locked())

    def test_cleanup_failure_still_triggers_supervisor_restart(self):
        State.restart_event = threading.Event()
        State._restart_requested = False
        with patch('module.api.lifecycle.clearup', Mock(return_value=False)):
            self.assertEqual({'restarting': True}, request_restart())
        self.assertTrue(State.restart_event.is_set())

    def test_repeated_request_is_idempotent(self):
        State.restart_event = threading.Event()
        State._restart_requested = True
        with patch('module.api.lifecycle.clearup', Mock(return_value=True)) as clearup:
            self.assertEqual({'restarting': True}, request_restart())
        clearup.assert_not_called()
        self.assertFalse(State.restart_event.is_set())


if __name__ == '__main__':
    unittest.main()
