"""本机旧启动器与 V2 共用状态，不退化票据、来源及进程生命周期保护。"""
import asyncio
import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from starlette.testclient import TestClient

from module.api.app import create_app
from module.api.launcher_api import stream
from module.api.launcher_protocol import HelloParams
from module.runtime.launcher import LauncherService
from module.runtime.launcher_trust import environment_secret
from tests.test_api import fixture


class LauncherLegacyBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = LauncherService('fixture-secret')
        self.control = self.service.legacy_control()
        self.connection = await self.control.mark_connected()
        self.query = await self.control.next_command()
        self.addAsyncCleanup(self.service.close)

    async def test_old_query_report_updates_new_status_and_rpc_command(self):
        await self.control.report({'id': self.query['id'], 'success': True, 'data': {'enabled': False}})
        self.assertTrue(self.service.status()['connected'])
        self.assertFalse(self.service.status()['autostart']['enabled'])
        result = await asyncio.to_thread(self.service.set_autostart, True)
        command = await asyncio.wait_for(self.control.next_command(), 1)
        self.assertEqual(command, {'id': result['commandId'], 'type': 'startup.set',
                                   'payload': {'enabled': True, 'mode': 'tray'}})
        await self.control.report({'id': command['id'], 'success': True, 'data': {'enabled': True}})
        self.assertEqual(self.service.command(command['id'])['status'], 'succeeded')
        self.assertTrue(self.service.status()['autostart']['enabled'])

    async def test_failure_without_data_finishes_pending_command(self):
        result = self.service.set_autostart(True)
        await self.control.next_command()
        await self.control.report({'id': result['commandId'], 'success': False, 'error': '拒绝访问'})
        command = self.service.command(result['commandId'])
        self.assertEqual(command['status'], 'failed')
        self.assertEqual(command['error']['message'], '拒绝访问')

    async def test_v2_replacement_rejects_old_report_and_stale_cleanup(self):
        socket = Mock()
        socket.close = unittest.mock.AsyncMock()
        replacement = await self.service.attach(socket)
        self.service.hello(replacement, HelloParams(protocolVersion=2, launcherVersion='fixture',
            platform='win32', capabilities=['autostart'], autostart={'supported': True, 'enabled': False}))
        self.assertTrue(self.connection.socket.closed)
        self.control.mark_disconnected(self.connection)
        result = await self.control.report({'id': self.query['id'], 'success': True, 'data': {'enabled': True}})
        self.assertFalse(result['success'])
        self.assertIs(self.service.connection, replacement)
        self.assertFalse(self.service.status()['autostart']['enabled'])

    async def test_expired_command_is_not_sent_to_old_launcher(self):
        result = self.service.set_autostart(True)
        self.service.commands[result['commandId']]['deadline'] = time.time() - 1
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(self.control.next_command(), 0.02)
        self.assertEqual(self.service.command(result['commandId'])['status'], 'expired')

    async def test_notification_stream_keeps_extras_and_v2_uses_standard_fields(self):
        payload = {'instance': 'fixture', 'title': '任务', 'content': '完成'}
        self.service.publish(payload, legacy={**payload, 'extra': '旧报文'})
        self.assertEqual(self.control.notifications.get_nowait()['extra'], '旧报文')
        self.assertEqual(next(iter(self.service.notifications.values()))['data'], payload)

    async def test_notification_backlog_is_bounded_for_both_protocols(self):
        for index in range(140):
            self.service.publish({'instance': '', 'title': str(index), 'content': ''})
        self.assertEqual(len(self.service.notifications), 100)
        self.assertEqual(self.control.notifications.qsize(), 100)
        self.assertEqual(self.control.notifications.get_nowait()['title'], '40')
        self.assertTrue(self.service.status()['connected'])
        self.assertTrue(self.connection.queue.empty())

    async def test_new_legacy_stream_cleanup_does_not_detach_replacement(self):
        old = self.connection
        await self.control.mark_connected()
        self.control.mark_disconnected(old)
        self.assertIs(self.service.connection, self.control.connection)
        self.assertTrue(self.service.status()['connected'])

    async def test_sse_handler_captures_its_own_connection(self):
        request = SimpleNamespace(client=SimpleNamespace(host='127.0.0.1'), headers={'host': 'localhost'},
            app=SimpleNamespace(state=SimpleNamespace(gateway=SimpleNamespace(router=SimpleNamespace(launcher=self.service)))),
            is_disconnected=unittest.mock.AsyncMock(return_value=False))
        response = await stream(request)
        frame = await anext(response.body_iterator)
        command = json.loads(frame[6:])
        self.assertEqual(command['type'], 'startup.query')
        first = self.control.connection
        await self.control.mark_connected()
        await response.body_iterator.aclose()
        self.assertTrue(first.socket.closed)
        self.assertTrue(self.service.status()['connected'])


class LauncherLegacyIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        with patch.dict(os.environ, {'ALAS_WEBUI_TRUST_SECRET': 'old-secret'}, clear=True):
            self.app = create_app(root=fixture(self.temp), password='fixture-password',
                                  manage_runtime=False, mount_mcp=False)
        self.service = self.app.state.gateway.router.launcher
        self.client = TestClient(self.app, base_url='http://127.0.0.1', client=('127.0.0.1', 50000))

    def test_old_environment_configures_app_but_explicit_new_value_takes_priority(self):
        self.assertTrue(self.service.trust.check_secret('old-secret'))
        with patch.dict(os.environ, {'AP_WEBUI_TRUST_SECRET': '', 'ALAS_WEBUI_TRUST_SECRET': 'old'}, clear=True):
            self.assertEqual(environment_secret(), '')
        with patch.dict(os.environ, {'AP_WEBUI_TRUST_SECRET': 'new', 'ALAS_WEBUI_TRUST_SECRET': 'old'}, clear=True):
            self.assertEqual(environment_secret(), 'new')

    def test_old_ticket_is_single_use_and_not_a_v2_ticket(self):
        response = self.client.post('/api/launcher/trusted-login', headers={'x-webui-launcher-secret': 'old-secret'})
        token = response.json()['token']
        self.assertFalse(self.service.trust.consume(token))
        page = self.client.get('/launcher-login', params={'token': token})
        self.assertEqual(page.status_code, 200)
        self.assertIn("localStorage.setItem('password'", page.text)
        self.assertIn("localStorage.setItem('azurpilot.access-password'", page.text)
        self.assertEqual(self.client.get('/launcher-login', params={'token': token}).status_code, 403)

    def test_v2_ticket_cannot_be_consumed_as_old_password_seed(self):
        token = self.service.trust.issue()['ticket']
        self.assertEqual(self.client.get('/launcher-login', params={'token': token}).status_code, 403)
        self.assertTrue(self.service.trust.consume(token))

    def test_old_notifications_preserve_extra_fields_without_loosening_v2(self):
        payload = {'instance': '', 'title': '任务', 'content': '完成', 'body': '旧正文'}
        self.assertEqual(self.client.post('/api/notify', json=payload).status_code, 200)
        self.assertEqual(self.service.legacy_control().notifications.get_nowait(), payload)
        self.assertEqual(self.client.post('/api/v2/internal/notifications', json=payload).status_code, 400)
        self.assertEqual(self.client.post('/api/notify', json={'body': 'x' * 66000}).status_code, 413)

    def test_old_endpoints_reject_origin_and_proxy_bypass(self):
        for headers in ({'Origin': 'https://evil.example'}, {'x-forwarded-for': '127.0.0.1'},
                        {'forwarded': 'for=127.0.0.1'}, {'x-azurpilot-remote-access': '1'}):
            with self.subTest(headers=headers):
                self.assertEqual(self.client.get('/api/launcher/status', headers=headers).status_code, 403)
                self.assertEqual(self.client.post('/api/notify', json={'title': 'x'}, headers=headers).status_code, 403)

    def test_old_startup_endpoint_keeps_new_demo_write_protection(self):
        with patch.dict(os.environ, {'DEMO': '1'}):
            self.assertEqual(self.client.post('/api/launcher/startup', json={'enabled': True}).status_code, 403)

    def test_applications_do_not_share_legacy_notifications_or_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            other = create_app(root=fixture(directory), password='other', manage_runtime=False, mount_mcp=False)
            other_service = other.state.gateway.router.launcher
            self.client.post('/api/notify', json={'title': '私有'})
            token = self.service.legacy_trust.issue_token()
            self.assertTrue(other_service.legacy_control().notifications.empty())
            self.assertFalse(other_service.legacy_trust.consume(token))
            self.assertTrue(self.service.legacy_trust.consume(token))

class NotificationProducerCompatibilityTests(unittest.TestCase):
    def test_new_notification_uses_v2_and_reports_http_failure(self):
        from module.notify.notify import notify_webui
        with patch('module.notify.notify.requests.post', return_value=SimpleNamespace(ok=False, status_code=500)) as post:
            self.assertFalse(notify_webui('fixture', '标题', '正文'))
        self.assertEqual(post.call_count, 1)
        self.assertTrue(post.call_args.args[0].endswith('/api/v2/internal/notifications'))

    def test_legacy_extra_fields_keep_old_transport(self):
        from module.notify.notify import notify_webui
        with patch('module.notify.notify.requests.post', return_value=SimpleNamespace(ok=True, status_code=200)) as post:
            self.assertTrue(notify_webui('fixture', '标题', '正文', extra='旧字段'))
        self.assertTrue(post.call_args.args[0].endswith('/api/notify'))
        self.assertEqual(post.call_args.kwargs['json']['extra'], '旧字段')

    def test_pywebio_fallback_only_when_v2_endpoint_is_absent(self):
        from module.notify.notify import notify_webui
        with patch('module.notify.notify.requests.post', side_effect=[
                SimpleNamespace(ok=False, status_code=404), SimpleNamespace(ok=True, status_code=200)]) as post:
            self.assertTrue(notify_webui('fixture', '标题', '正文'))
        self.assertEqual(post.call_count, 2)
        self.assertTrue(post.call_args_list[0].args[0].endswith('/api/v2/internal/notifications'))
        self.assertTrue(post.call_args_list[1].args[0].endswith('/api/notify'))
        self.assertEqual(post.call_args_list[0].kwargs['json'], post.call_args_list[1].kwargs['json'])
