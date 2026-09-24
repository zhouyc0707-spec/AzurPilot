import unittest
from unittest.mock import Mock, patch

from gui import EXIT_STARTUP_FAILURE, run_webui_supervisor


class TestSupervisorExitCode(unittest.TestCase):
    """启动失败必须以非零退出码结束：启动器只区分 0 与非 0。"""

    def test_orphan_recovery_failure_returns_failure_code(self):
        with patch("gui._recover_orphaned_workers", return_value=False):
            self.assertEqual(run_webui_supervisor(), EXIT_STARTUP_FAILURE)

    def test_frontend_build_failure_returns_failure_code(self):
        # 本地定制：gui.py 的 USE_REACT_FRONTEND 由 ALAS_WEBUI 决定，未设置时不会走
        # 前端构建分支 —— 那样这个用例就绕过了被 patch 的 ensure_frontend，转而去启动
        # 真的监督进程（不会返回）。这里把开关钉住，让用例与环境变量无关。
        with patch("gui._recover_orphaned_workers", return_value=True), patch(
            "gui._prepare_dependency_sync_before_webui_start",
            return_value=(True, None, None, None),
        ), patch("gui.USE_REACT_FRONTEND", True), patch(
            "deploy.frontend.ensure_frontend", side_effect=RuntimeError("npm 不可用")
        ):
            self.assertEqual(run_webui_supervisor(), EXIT_STARTUP_FAILURE)

    def test_dependency_sync_not_ready_returns_failure_code(self):
        with patch("gui._recover_orphaned_workers", return_value=True), patch(
            "gui._prepare_dependency_sync_before_webui_start",
            return_value=(False, None, None, None),
        ):
            self.assertEqual(run_webui_supervisor(), EXIT_STARTUP_FAILURE)

    def test_keyboard_interrupt_returns_success_code(self):
        process = Mock()
        process.pid = 4242
        with patch("gui._recover_orphaned_workers", return_value=True), patch(
            "gui._prepare_dependency_sync_before_webui_start",
            return_value=(True, None, None, None),
        ), patch("deploy.frontend.ensure_frontend"), patch(
            "gui.Process", return_value=process
        ), patch("gui._wait_for_webui_ready", side_effect=KeyboardInterrupt), patch(
            "gui._stop_webui_process_tree", return_value=True
        ):
            self.assertEqual(run_webui_supervisor(), 0)


if __name__ == "__main__":
    unittest.main()
