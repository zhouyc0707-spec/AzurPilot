"""启动器（alas-launcher）端点在 React 后端的行为。

协议与旧界面 ``module/webui/api.py`` 一致：启动器通过 ``/api/launcher/stream``
收命令、通过 ``/api/launcher/report`` 回报、通过 ``/api/notify_stream`` 收通知，
并用信任密钥换免密令牌。这些端点必须注册在 SPA 兜底之前 —— 否则会命中
``FrontendFiles`` 的兜底返回 index.html（HTTP 200 + text/html），启动器的控制流与
通知流会静默失效。

注意：两条 SSE 是常驻流，用 TestClient 读它们的响应体会一直等下去，因此这里改为
断言「路由已按顺序注册」+ 直接验证命令队列与通知队列的内容；端到端行为在真服务上
用 httpx 实测（见 定制化修改清单 第九章）。
"""

import asyncio
import tempfile
import unittest

from starlette.testclient import TestClient

from module.api import launcher_routes
from module.api.app import create_app
from module.runtime import launcher_trust
from module.runtime.launcher import launcher_control
from tests.test_api import fixture

LOCAL = ('127.0.0.1', 55555)
REMOTE = ('203.0.113.7', 55555)
#: is_local_request 同时要求连接来源与 Host 头都是回环地址
LOCAL_HEADERS = {'host': '127.0.0.1:22267'}
REMOTE_HEADERS = {'host': 'app.example.com'}


def local_client(app):
    return TestClient(app, client=LOCAL, headers=LOCAL_HEADERS)


def remote_client(app):
    return TestClient(app, client=REMOTE, headers=REMOTE_HEADERS)


class LauncherRouteRegistrationTests(unittest.TestCase):
    """端点必须排在静态资源之前，否则会被 SPA 兜底吞掉。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(root=fixture(self.temp.name), password='test-secret',
                              manage_runtime=False, mount_mcp=False)

    def paths(self):
        return [getattr(route, 'path', None) for route in self.app.routes]

    def test_launcher_routes_are_registered_before_static_mount(self):
        paths = self.paths()
        for path in ('/api/notify', '/api/notify_stream', '/api/launcher/status',
                     '/api/launcher/startup', '/api/launcher/stream',
                     '/api/launcher/report', '/api/launcher/trusted-login', '/launcher-login'):
            self.assertIn(path, paths, f'缺少启动器端点 {path}')
        # 静态资源兜底（有构建产物时是 Mount('/')，否则是 /{path:path} 路由）排在最后
        spa_index = min(index for index, path in enumerate(paths) if path in ('/', '/{path:path}'))
        for path in ('/api/launcher/stream', '/api/notify_stream'):
            self.assertLess(paths.index(path), spa_index, f'{path} 被 SPA 兜底挡住了')

    def test_methods_match_the_launcher_protocol(self):
        methods = {}
        for route in self.app.routes:
            if getattr(route, 'path', '').startswith(('/api/launcher', '/api/notify', '/launcher-login')):
                methods[route.path] = set(route.methods or [])
        # Starlette 会给 GET 路由自动补上 HEAD
        for path in ('/api/notify', '/api/launcher/startup', '/api/launcher/report',
                     '/api/launcher/trusted-login'):
            self.assertIn('POST', methods[path], path)
            self.assertNotIn('GET', methods[path], path)
        for path in ('/api/launcher/stream', '/api/notify_stream', '/launcher-login'):
            self.assertIn('GET', methods[path], path)
            self.assertNotIn('POST', methods[path], path)


class LauncherApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(root=fixture(self.temp.name), password='test-secret',
                              manage_runtime=False, mount_mcp=False)
        self.client = local_client(self.app)
        launcher_trust._reset()
        self.addCleanup(launcher_trust._reset)
        self._drain_notifications()
        self.addCleanup(self._drain_notifications)

    @staticmethod
    def _drain_notifications():
        while not launcher_routes._notification_queue.empty():
            launcher_routes._notification_queue.get_nowait()

    def test_status_returns_json_and_reports_request_locality(self):
        response = self.client.get('/api/launcher/status')
        self.assertEqual(200, response.status_code)
        self.assertEqual('application/json', response.headers['content-type'].split(';')[0])
        payload = response.json()
        self.assertTrue(payload['success'])
        self.assertTrue(payload['request_local'])
        self.assertIn('launcher_connected', payload)
        self.assertIn('autostart_supported', payload)

        remote = remote_client(self.app)
        self.assertFalse(remote.get('/api/launcher/status').json()['request_local'])

    def test_command_stream_and_report_reject_remote_clients(self):
        remote = remote_client(self.app)
        self.assertEqual(403, remote.get('/api/launcher/stream').status_code)
        self.assertEqual(403, remote.post('/api/launcher/report', json={'success': True}).status_code)
        self.assertEqual(403, remote.post('/api/launcher/startup', json={'enabled': True}).status_code)

    def test_connect_handshake_asks_for_autostart_state(self):
        """启动器连上命令流时，后端先下发 startup.query 同步开机自启状态。"""

        async def handshake():
            await launcher_control.mark_connected()
            return await asyncio.wait_for(launcher_control.next_command(), timeout=5)

        command = asyncio.run(handshake())
        self.assertEqual('startup.query', command['type'])
        self.assertTrue(command['id'])
        launcher_control.mark_disconnected()

    def test_notify_is_queued_for_the_launcher_stream(self):
        response = self.client.post('/api/notify', json={'title': '测试', 'body': 'hello'})
        self.assertTrue(response.json()['success'])
        queued = launcher_routes._notification_queue.get_nowait()
        self.assertEqual({'title': '测试', 'body': 'hello'}, queued)

    def test_notify_rejects_invalid_json(self):
        response = self.client.post('/api/notify', content=b'not json',
                                    headers={'content-type': 'application/json'})
        self.assertEqual(400, response.status_code)
        self.assertFalse(response.json()['success'])

    def test_report_updates_autostart_state(self):
        payload = {'id': 'cmd-1', 'success': True, 'data': {'enabled': True}}
        self.assertTrue(self.client.post('/api/launcher/report', json=payload).json()['success'])
        self.assertTrue(self.client.get('/api/launcher/status').json()['autostart_enabled'])

    def test_startup_requires_boolean_enabled(self):
        self.assertEqual(400, self.client.post('/api/launcher/startup', json={}).status_code)
        self.assertEqual(400, self.client.post('/api/launcher/startup', json={'enabled': 'yes'}).status_code)

    def test_trusted_login_requires_matching_secret_and_issues_token(self):
        # 未由启动器拉起（没有信任密钥）时免密通道整体关闭
        denied = self.client.post('/api/launcher/trusted-login',
                                  headers={'x-webui-launcher-secret': 'whatever'})
        self.assertEqual(403, denied.status_code)

        launcher_trust.configure('launcher-secret', 'test-secret')
        wrong = self.client.post('/api/launcher/trusted-login',
                                 headers={'x-webui-launcher-secret': 'wrong'})
        self.assertEqual(403, wrong.status_code)

        granted = self.client.post('/api/launcher/trusted-login',
                                   headers={'x-webui-launcher-secret': 'launcher-secret'})
        self.assertEqual(200, granted.status_code)
        token = granted.json()['token']

        # 种子页把密码写进前端 localStorage（React 前端用 azurpilot.access-password）
        seed = self.client.get('/launcher-login', params={'token': token})
        self.assertEqual(200, seed.status_code)
        self.assertIn('azurpilot.access-password', seed.text)
        self.assertIn('location.replace("/")', seed.text)

    def test_launcher_login_rejects_bad_token_and_remote_client(self):
        self.assertEqual(403, self.client.get('/launcher-login', params={'token': 'bad'}).status_code)
        launcher_trust.configure('launcher-secret', 'test-secret')
        token = self.client.post('/api/launcher/trusted-login',
                                 headers={'x-webui-launcher-secret': 'launcher-secret'}).json()['token']
        remote = remote_client(self.app)
        self.assertEqual(403, remote.get('/launcher-login', params={'token': token}).status_code)


if __name__ == '__main__':
    unittest.main()
