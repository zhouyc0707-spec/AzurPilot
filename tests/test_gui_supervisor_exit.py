import logging
import subprocess
import tempfile
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gui import (
    EXIT_DEPENDENCY_SYNC_FAILURE,
    EXIT_FRONTEND_BUILD_FAILURE,
    EXIT_STARTUP_FAILURE,
    EXIT_WORKER_CLEANUP_FAILURE,
    main,
    func,
    run_webui_supervisor,
    _ensure_frontend_if_needed,
)
from module.logger import logger
from module.persistence.migration import MigrationError


class TestFrontendPreflight(unittest.TestCase):
    def test_supervisor_uses_updated_builder_despite_cached_old_modules(self):
        """旧监督器的构建器及事务接口缓存不能影响磁盘上的新版预检。"""
        stale_builder = Mock(side_effect=AssertionError('不应执行已缓存的旧构建器'))
        stale_transaction = Mock(side_effect=AssertionError('不应复用旧事务接口'))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            deploy = root / 'deploy'
            deploy.mkdir()
            (deploy / '__init__.py').touch()
            (deploy / 'config_transaction.py').write_text(
                'from contextlib import contextmanager\n'
                '@contextmanager\n'
                'def config_transaction(path, *, timeout):\n'
                '    yield\n', encoding='utf-8',
            )
            (deploy / 'frontend.py').write_text(
                'from pathlib import Path\n'
                'from deploy.config_transaction import config_transaction\n'
                'with config_transaction("build", timeout=10):\n'
                '    Path("checked").write_text("updated", encoding="utf-8")\n',
                encoding='utf-8',
            )
            with patch('gui.USE_REACT_FRONTEND', True), patch('gui.__file__', str(root / 'gui.py')), patch.dict(
                sys.modules, {
                    'deploy.frontend': SimpleNamespace(ensure_frontend=stale_builder),
                    'deploy.config_transaction': SimpleNamespace(config_transaction=stale_transaction),
                },
            ):
                _ensure_frontend_if_needed(fresh_process=True)
            self.assertEqual('updated', (root / 'checked').read_text(encoding='utf-8'))
        stale_builder.assert_not_called()
        stale_transaction.assert_not_called()

    def test_interrupted_preflight_stops_its_process_tree(self):
        process = Mock(pid=1234)
        process.wait.side_effect = [KeyboardInterrupt, 0]
        process.poll.return_value = None
        with patch('gui.USE_REACT_FRONTEND', True), patch(
            'gui.subprocess.Popen', return_value=process,
        ), patch('gui.process_created_at', return_value=123.0), patch(
            'gui.stop_process_tree', return_value=True,
        ) as stop_tree:
            with self.assertRaises(KeyboardInterrupt):
                _ensure_frontend_if_needed(fresh_process=True)
        stop_tree.assert_called_once_with(
            record={'pid': 1234, 'created_at': 123.0}, name='前端预检', timeout=0,
        )
        process.wait.assert_called_with(timeout=5)


class TestGuiStartup(unittest.TestCase):
    def test_migration_failure_is_logged_and_does_not_start_webui(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'gui.txt'
            handler = logging.FileHandler(path, encoding='utf-8')
            try:
                with patch.object(logger, 'handlers', [handler]), patch.object(
                    logger, 'set_file_logger'
                ) as bind, patch(
                    'module.persistence.database.initialize',
                    side_effect=MigrationError('旧 daily 数据库的表结构不符合迁移约定'),
                ), patch('gui.run_webui_supervisor') as supervisor, patch('gui.func') as service:
                    self.assertEqual(main(), EXIT_STARTUP_FAILURE)
                    bind.assert_called_once_with('gui-launcher')
                    supervisor.assert_not_called()
                    service.assert_not_called()
            finally:
                handler.close()
            content = path.read_text(encoding='utf-8')
            self.assertIn('普通业务数据初始化失败', content)
            self.assertIn('旧 daily 数据库的表结构不符合迁移约定', content)
            self.assertIn('Traceback', content)

    def test_successful_migration_starts_selected_webui_mode(self):
        for reload in (False, True):
            with self.subTest(reload=reload), patch.object(logger, 'set_file_logger'), patch(
                'module.persistence.database.initialize'
            ), patch('gui.set_start_method'), patch(
                'gui.State', deploy_config=SimpleNamespace(EnableReload=reload)
            ), patch('gui.run_webui_supervisor', return_value=0) as supervisor, patch('gui.func') as service, patch.object(
                sys, 'argv', ['gui.py', '-q']
            ):
                self.assertEqual(main(), 0)
                if reload:
                    supervisor.assert_called_once_with(watch_sources=False)
                    service.assert_not_called()
                else:
                    supervisor.assert_not_called()
                    service.assert_called_once_with(None, None)


class TestSupervisorExitCode(unittest.TestCase):
    """启动失败必须以对应的具体非零退出码结束。"""

    def test_orphan_recovery_failure_returns_failure_code(self):
        with patch("gui._recover_orphaned_workers", return_value=False):
            self.assertEqual(run_webui_supervisor(), EXIT_WORKER_CLEANUP_FAILURE)

    def test_frontend_build_failure_returns_failure_code(self):
        # 固定前端模式并隔离预检边界，避免测试启动真实构建进程。
        with self.assertLogs('alas', level='INFO') as captured, patch("gui._recover_orphaned_workers", return_value=True), patch(
            "gui._prepare_dependency_sync_before_webui_start",
            return_value=(True, None, None, None),
        ), patch("gui.USE_REACT_FRONTEND", True), patch(
            "gui._ensure_frontend_if_needed", side_effect=subprocess.CalledProcessError(1, ['frontend-check'])
        ):
            self.assertEqual(run_webui_supervisor(), EXIT_FRONTEND_BUILD_FAILURE)
        messages = '\n'.join(captured.output)
        stages = ('检查并回收', '检查依赖同步状态', '依赖已就绪', '检查前端静态资源', 'React 前端构建失败')
        positions = [messages.index(stage) for stage in stages]
        self.assertEqual(positions, sorted(positions))

    def test_dependency_sync_not_ready_returns_failure_code(self):
        with patch("gui._recover_orphaned_workers", return_value=True), patch(
            "gui._prepare_dependency_sync_before_webui_start",
            return_value=(False, None, None, None),
        ):
            self.assertEqual(run_webui_supervisor(), EXIT_DEPENDENCY_SYNC_FAILURE)

    def test_keyboard_interrupt_returns_success_code(self):
        process = Mock()
        process.pid = 4242
        with patch("gui._recover_orphaned_workers", return_value=True), patch(
            "gui._prepare_dependency_sync_before_webui_start",
            return_value=(True, None, None, None),
        ), patch("gui._ensure_frontend_if_needed"), patch(
            "gui.Process", return_value=process
        ), patch("gui._wait_for_webui_ready", side_effect=KeyboardInterrupt), patch(
            "gui._stop_webui_process_tree", return_value=True
        ):
            self.assertEqual(run_webui_supervisor(), 0)

    def test_service_log_path_is_visible_before_console_logging_is_disabled(self):
        from module.logger import logger
        logfile = 'fixture/2026-10-09_webui.txt'
        with self.assertLogs('alas', level='INFO') as captured, \
                patch('module.logger.set_file_logger') as file_logger, \
                patch('module.logger.set_console_logger') as console_logger, \
                patch.object(logger, 'log_file', logfile), \
                patch.dict(sys.modules, {'uvicorn': None}):
            def disable_console(enabled):
                self.assertFalse(enabled)
                self.assertIn('后续详细日志：' + logfile, '\n'.join(captured.output))
            console_logger.side_effect = disable_console
            # 阻止后续服务导入与启动，只验证日志交接顺序。
            with self.assertRaises(ModuleNotFoundError):
                func(None)
        file_logger.assert_called_once_with('webui')
        console_logger.assert_called_once_with(False)


if __name__ == "__main__":
    unittest.main()
