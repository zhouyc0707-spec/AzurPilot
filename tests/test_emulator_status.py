"""模拟器状态 API 只读取临时配置和已有检测结果，不连接真实设备。"""

import json
import tempfile
import unittest
from unittest.mock import Mock, patch

from starlette.testclient import TestClient

from module.api.app import create_app
from module.api.config_service import ConfigService
from module.api.protocol import ApiError, ConfigChange, SubscribeParams
from module.api.router import Router
from module.api.runtime_service import RuntimeService
from module.runtime.process_manager import ProcessManager
from tests.test_api import fixture


class TestEmulatorStatus(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.configs = ConfigService(fixture(temp.name))
        path = self.configs.path('testpilot')
        data = json.loads(path.read_text(encoding='utf-8'))
        data['Alas']['Emulator']['Serial'] = '127.0.0.1:16480'
        data['Alas']['EmulatorManagement'].update(
            ScheduledEmulatorRestart=True, ForceScheduledRestart=False, RestartIntervalHours=12,
        )
        path.write_text(json.dumps(data), encoding='utf-8')
        self.manager = Mock(spec=ProcessManager)
        self.manager.state = 1
        self.manager.started_func = 'alas'
        self.manager.emulator_uptime_snapshot.return_value = {
            'serial': '127.0.0.1:16480', 'uptimeSeconds': 3 * 3600,
            'checkedAt': 1000000.0, 'lastAttemptAt': 1000000.0, 'available': True,
        }
        self.enterContext(patch.object(ProcessManager, '_processes', {'testpilot': self.manager}))
        self.enterContext(patch('module.api.runtime_service.time.time', return_value=1000030.0))
        self.runtime = RuntimeService(self.configs)

    def change(self, path, value):
        return self.configs.patch('testpilot', None, [ConfigChange(path=path, value=value)])

    def test_uses_last_sample_to_calculate_restart_threshold_without_device_access(self):
        path = self.configs.path('testpilot')
        before = path.read_bytes()
        with (
            patch('module.device.connection.Connection.get_emulator_uptime') as read,
            patch.object(ProcessManager, 'get_manager') as create,
        ):
            result = self.runtime.emulator_status('testpilot')
        self.assertEqual(result['nextRestartAt'], 1000000 + 9 * 3600)
        self.assertEqual(result['uptimeSeconds'], 10800)
        self.assertEqual(result['checkedAt'], 1000000)
        self.assertEqual(result['serverTime'], 1000030)
        self.assertTrue(result['schedulerRunning'])
        self.assertEqual(before, path.read_bytes())
        read.assert_not_called()
        create.assert_not_called()

    def test_changed_interval_immediately_recalculates_estimate(self):
        self.change('Alas.EmulatorManagement.RestartIntervalHours', 8)
        result = self.runtime.emulator_status('testpilot')
        self.assertEqual(result['nextRestartAt'], 1000000 + 5 * 3600)
        self.assertEqual(result['intervalHours'], 8)

    def test_disabled_restart_keeps_last_detection_without_estimate(self):
        self.change('Alas.EmulatorManagement.ScheduledEmulatorRestart', False)
        result = self.runtime.emulator_status('testpilot')
        self.assertIsNone(result['nextRestartAt'])
        self.assertFalse(result['scheduled'])
        self.assertEqual(result['uptimeSeconds'], 10800)

    def test_failed_detection_marks_history_and_hides_estimate(self):
        self.manager.emulator_uptime_snapshot.return_value.update(available=False, lastAttemptAt=1000030)
        result = self.runtime.emulator_status('testpilot')
        self.assertFalse(result['available'])
        self.assertEqual(result['checkedAt'], 1000000)
        self.assertEqual(result['lastAttemptAt'], 1000030)
        self.assertIsNone(result['nextRestartAt'])

    def test_new_boot_sample_recalculates_from_actual_uptime(self):
        self.manager.emulator_uptime_snapshot.return_value.update(uptimeSeconds=60, checkedAt=1000030)
        self.assertEqual(self.runtime.emulator_status('testpilot')['nextRestartAt'], 1000030 + 12 * 3600 - 60)

    def test_changed_serial_cannot_reuse_previous_device_sample(self):
        self.change('Alas.Emulator.Serial', '127.0.0.1:5555')
        result = self.runtime.emulator_status('testpilot')
        self.assertIsNone(result['uptimeSeconds'])
        self.assertIsNone(result['checkedAt'])
        self.assertIsNone(result['nextRestartAt'])

    def test_stopped_or_standalone_task_is_not_running_scheduler(self):
        self.manager.state = 2
        self.assertFalse(self.runtime.emulator_status('testpilot')['schedulerRunning'])
        self.manager.state = 1
        self.manager.started_func = 'commission'
        self.assertFalse(self.runtime.emulator_status('testpilot')['schedulerRunning'])

    def test_without_manager_does_not_invent_a_sample(self):
        with patch.object(ProcessManager, '_processes', {}):
            result = self.runtime.emulator_status('testpilot')
        self.assertIsNone(result['uptimeSeconds'])
        self.assertIsNone(result['lastAttemptAt'])
        self.assertFalse(result['schedulerRunning'])

    def test_readonly_route_and_subscriptions_validate_instance(self):
        router = Router(self.configs, self.runtime)
        method = router.methods['emulator.status']
        self.assertFalse(method.mutates)
        self.assertEqual(method.handler(method.params(instance='testpilot'))['instance'], 'testpilot')
        with self.assertRaises(ApiError):
            method.handler(method.params(instance='../testpilot'))
        params = SubscribeParams(instance='testpilot', topics=['instances', 'overview', 'logs', 'preview', 'emulator'])
        self.assertIn('emulator', params.topics)

    def test_websocket_sends_updated_detection_for_subscribed_instance(self):
        app = create_app(root=self.configs.root, manage_runtime=False, mount_mcp=False)
        client = TestClient(app, client=('127.0.0.1', 55555))
        with client.websocket_connect('/api/v1/ws', headers={
            'host': '127.0.0.1:22267', 'origin': 'http://127.0.0.1:22267',
        }) as ws:
            self.assertFalse(ws.receive_json()['data']['authRequired'])
            ws.send_json({'v': 1, 'type': 'request', 'id': 'status', 'method': 'emulator.status',
                          'params': {'instance': 'testpilot'}})
            response = ws.receive_json()
            self.assertTrue(response['ok'])
            self.assertEqual(10800, response['result']['uptimeSeconds'])
            ws.send_json({'v': 1, 'type': 'request', 'id': 'subscribe', 'method': 'events.subscribe',
                          'params': {'instance': 'testpilot', 'topics': ['emulator']}})
            self.assertTrue(ws.receive_json()['ok'])
            first = ws.receive_json()
            self.assertEqual('emulator', first['topic'])
            self.assertEqual('testpilot', first['data']['instance'])
            self.manager.emulator_uptime_snapshot.return_value.update(uptimeSeconds=60, checkedAt=1000030)
            second = ws.receive_json()
            self.assertEqual(60, second['data']['uptimeSeconds'])
            self.assertEqual(1000030 + 12 * 3600 - 60, second['data']['nextRestartAt'])
            self.assertGreater(second['seq'], first['seq'])


if __name__ == '__main__':
    unittest.main()
