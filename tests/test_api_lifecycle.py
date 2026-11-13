"""真实共享进程与 ASGI 生命周期的集成回归，隔离所有游戏及网络任务。"""
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch, mock_open

from starlette.testclient import TestClient

from module.api.app import create_app
from module.api import lifecycle
from module.runtime.setting import State
from module.runtime.task_handler import TaskHandler
from tests.test_api import fixture


class ApiLifecycleTests(unittest.TestCase):
    def test_destroyed_vault_does_not_abort_webui_lifespan(self):
        self.run_lifecycle(fail_account=True)

    def run_lifecycle(self, fail_start=False, fail_discord=False, fail_discord_start=False, fail_account=False):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = fixture(directory)
            executed = threading.Event()

            def schedule():
                yield
                while True:
                    executed.set()
                    yield

            settings = SimpleNamespace(Run='', StartOcrServer=False, EnableRemoteAccess=False,
                                       DiscordRichPresence=fail_discord_start)
            tasks = TaskHandler()
            stack.enter_context(patch.object(State, '_deploy_config_', settings, create=True))
            stack.enter_context(patch.multiple(State, _init=False, _clearup=False, manager=None,
                                               process_registry=None, _restart_requested=False))
            stack.enter_context(patch('module.runtime.worker_registry.WORKER_REGISTRY_FILE', root / 'workers.json'))
            stack.enter_context(patch('module.runtime.worker_registry.LEGACY_WORKER_REGISTRY_FILE', root / 'old.json'))
            stack.enter_context(patch('module.runtime.updater.updater', SimpleNamespace(delay=0, schedule_update=schedule)))
            stack.enter_context(patch.object(lifecycle, 'task_handler', tasks))
            stack.enter_context(patch.object(lifecycle, 'maintenance_monitor', None))
            maintenance_monitor = Mock()
            start_maintenance = stack.enter_context(patch(
                'module.maintenance.start_maintenance_monitor', return_value=maintenance_monitor
            ))
            # 禁止读取用户的自动运行列表或接触设备进程；Manager 与调度线程真实运行。
            if fail_account:
                from module.api.protocol import ApiError
                broken = Mock(spec=lifecycle.ProcessManager, config_name='broken')
                healthy = Mock(spec=lifecycle.ProcessManager, config_name='healthy')
                broken.start.side_effect = ApiError('VAULT_DESTROYED', '合成旧版标记')
                stack.enter_context(patch.object(lifecycle.ProcessManager, 'get_manager',
                                               side_effect=lambda name: {'broken': broken, 'healthy': healthy}[name]))
                stack.enter_context(patch('module.runtime.process_manager.list_mod_instance'))
                stack.enter_context(patch('module.runtime.process_manager.get_config_mod', return_value='alas'))
                stack.enter_context(patch('module.runtime.process_manager.open',
                                         mock_open(read_data='broken\nhealthy\n'), create=True))
                stack.enter_context(patch('module.runtime.process_manager.os.remove'))
            else:
                stack.enter_context(patch.object(lifecycle.ProcessManager, 'restart_processes'))
            stack.enter_context(patch.object(lifecycle.ProcessManager, 'running_instances', return_value=[]))
            if fail_discord:
                stack.enter_context(patch('module.runtime.discord_presence.async_close_discord_rpc',
                                          new=AsyncMock(side_effect=RuntimeError('模拟 Discord 清理失败'))))
            if fail_discord_start:
                stack.enter_context(patch('module.runtime.discord_presence.init_discord_rpc',
                                          side_effect=RuntimeError('模拟 Discord 启动失败')))
            if fail_start:
                stack.enter_context(patch.object(tasks, 'start', side_effect=RuntimeError('模拟启动失败')))
            app = create_app(root=root, password='', mount_mcp=False)
            if fail_start:
                with self.assertRaisesRegex(RuntimeError, '模拟启动失败'), TestClient(app):
                    pass
            else:
                with TestClient(app) as client:
                    self.assertTrue(executed.wait(timeout=2))
                    self.assertIsNotNone(State.manager)
                    self.assertEqual(200, client.get('/healthz').status_code)
                    with client.websocket_connect('/api/v1/ws') as socket:
                        self.assertEqual('session', socket.receive_json()['topic'])
                        socket.send_json({'v': 1, 'type': 'request', 'id': 'smoke',
                                          'method': 'system.ping', 'params': {}})
                        self.assertEqual({'pong': True}, socket.receive_json()['result'])
            self.assertIsNone(State.manager)
            self.assertTrue(State._clearup)
            self.assertFalse(tasks._thread and tasks._thread.is_alive())
            if not fail_start:
                start_maintenance.assert_called_once_with(directory=root / 'config')
                maintenance_monitor.stop.assert_called_once_with()

    def test_real_manager_and_background_scheduler_start_and_stop(self):
        self.run_lifecycle()

    def test_partial_startup_failure_releases_manager(self):
        self.run_lifecycle(fail_start=True)

    def test_discord_cleanup_failure_releases_real_manager(self):
        self.run_lifecycle(fail_discord=True)

    def test_discord_startup_failure_keeps_webui_available(self):
        self.run_lifecycle(fail_discord_start=True)


class MaintenanceEntryTests(unittest.TestCase):
    def test_overview_reads_shared_cache_without_querying_network(self):
        from module.api.runtime_service import RuntimeService

        with tempfile.TemporaryDirectory() as directory:
            config_directory = Path(directory)
            configs = SimpleNamespace(
                directory=config_directory,
                read=lambda _: ({'Alas': {'Emulator': {'PackageName': 'com.YoStarJP.AzurLane'}}}, '1'),
            )
            runtime = RuntimeService(configs)
            snapshot = {'state': 'announced', 'startsAt': '2026-11-19T10:00:00+09:00',
                        'checkedAt': '2026-11-13T01:00:00+09:00', 'error': None}
            with patch('module.maintenance.get_maintenance_snapshot', return_value=snapshot) as cached, \
                    patch('module.api.runtime_service.monthly_statistics_resources', return_value=[]), \
                    patch('module.api.runtime_service.ProcessManager._processes', {}):
                self.assertEqual(runtime.overview('test')['maintenance'], snapshot)
            cached.assert_called_once_with('jp', directory=config_directory)

    def test_scheduler_releases_monitor_on_return_and_error(self):
        from alas import AzurLaneAutoScript

        for failure in (False, True):
            with self.subTest(failure=failure):
                script = object.__new__(AzurLaneAutoScript)
                script.config_name = 'test'
                script.__dict__['config'] = SimpleNamespace(
                    SERVER='cn', data={'Alas': {'Emulator': {'PackageName': 'com.YoStarJP.AzurLane'}}}
                )
                script._loop = Mock(return_value='done', side_effect=ValueError('模拟退出') if failure else None)
                monitor = Mock()
                with patch('module.maintenance.start_maintenance_monitor', return_value=monitor) as start, \
                        patch('alas.logger.set_file_logger'):
                    if failure:
                        with self.assertRaisesRegex(ValueError, '模拟退出'):
                            script.loop()
                    else:
                        self.assertEqual(script.loop(), 'done')
                self.assertEqual(tuple(start.call_args.args[0]()), ('jp',))
                monitor.stop.assert_called_once_with()

    def test_scheduler_monitor_reads_reloaded_configuration(self):
        from alas import AzurLaneAutoScript

        script = object.__new__(AzurLaneAutoScript)
        script.config_name = 'test'
        script.__dict__['config'] = SimpleNamespace(
            SERVER='cn', data={'Alas': {'Emulator': {'PackageName': 'com.YoStarJP.AzurLane'}}}
        )
        monitor = Mock()

        def reload_configuration():
            script.__dict__['config'] = SimpleNamespace(
                SERVER='cn', data={'Alas': {'Emulator': {'PackageName': 'com.YoStarEN.AzurLane'}}}
            )

        script._loop = reload_configuration
        with patch('module.maintenance.start_maintenance_monitor', return_value=monitor) as start, \
                patch('alas.logger.set_file_logger'):
            script.loop()
        self.assertEqual(tuple(start.call_args.args[0]()), ('en',))
        monitor.stop.assert_called_once_with()
