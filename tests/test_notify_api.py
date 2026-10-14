"""notify_api（任务推送通知本地路由）的端点契约单元测试。"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

from module.api import launcher_routes, notify_api
from module.api.app import create_app
from module.api.protocol import SubscribeParams
from module.api.socket import Session
from module.runtime.notify_hub import hub

ROOT = Path(__file__).resolve().parents[1]


class FakeRequest:
    """最小可用的 Starlette 请求替身：只需要处理器真正读取的那几个属性。"""

    def __init__(self, *, body=None, host='127.0.0.1', content_type='application/json'):
        self.client = SimpleNamespace(host=host)
        self.headers = {'host': '127.0.0.1:25548', 'content-type': content_type}
        self._body = body

    async def json(self):
        """返回预置请求体；None 表示非法 JSON。"""
        if self._body is None:
            raise ValueError('not json')
        return self._body

    async def is_disconnected(self):
        """通知流处理器会轮询它，这里恒定未断开。"""
        return False


class TestNotifyRoutes(unittest.TestCase):
    def test_route_paths_match_legacy_contract(self):
        """端点路径是外部启动器写死的对接面（旧版 WebUI 传下来），改名即断连。"""
        paths = {route.path for route in notify_api.routes()}
        self.assertEqual(paths, {'/api/notify', '/api/notify_stream'})


class TestNotifyEndpoint(unittest.TestCase):
    def setUp(self):
        queue = asyncio.Queue(maxsize=notify_api._NOTIFICATION_QUEUE_MAXSIZE)
        override = patch.object(notify_api, '_notification_queue', queue)
        override.start()
        self.addCleanup(override.stop)

    def test_local_post_queues_payload(self):
        """启动器保留原报文与附加字段，浏览器仅广播三字段。"""
        payload = {'instance': 'alas', 'title': '标题', 'content': '内容', 'extra': '附加'}
        with patch.object(hub, 'publish') as publish:
            response = asyncio.run(notify_api.notify(FakeRequest(body=payload)))
        publish.assert_called_once_with({'instance': 'alas', 'title': '标题', 'content': '内容'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(bytes(response.body)), {'success': True})
        notification = notify_api._notification_queue.get_nowait()
        self.assertEqual(notification, payload)

    def test_legacy_imports_share_the_current_notification_queue(self):
        """旧入口与上游入口共用同一队列，测试替换状态后也不会分叉。"""
        self.assertIs(launcher_routes.api_notify, notify_api.notify)
        self.assertIs(launcher_routes.api_notify_stream, notify_api.notify_stream)
        self.assertIs(launcher_routes._notification_queue, notify_api._notification_queue)
        payload = {'title': '旧字段', 'body': '仍由启动器读取'}
        response = asyncio.run(launcher_routes.api_notify(FakeRequest(body=payload)))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(launcher_routes._notification_queue.get_nowait(), payload)

    def test_cross_site_content_type_rejected(self):
        """跨站表单只能发 text/plain 类请求体：非 application/json 一律 400，不入队。"""
        response = asyncio.run(notify_api.notify(FakeRequest(
            body={'instance': 'alas', 'title': '伪造', 'content': '钓鱼'},
            content_type='text/plain')))
        self.assertEqual(response.status_code, 400)
        self.assertTrue(notify_api._notification_queue.empty())

    def test_queue_overflow_drops_oldest(self):
        """队列满时挤掉最旧一条再入队，无人消费也不会无限堆积。"""
        queue = asyncio.Queue(maxsize=2)
        notify_api._notification_queue = queue
        for title in ('旧1', '旧2'):
            asyncio.run(notify_api.notify(FakeRequest(body={'instance': 'alas', 'title': title, 'content': ''})))
        asyncio.run(notify_api.notify(FakeRequest(body={'instance': 'alas', 'title': '新', 'content': ''})))
        self.assertEqual(queue.get_nowait()['title'], '旧2')
        self.assertEqual(queue.get_nowait()['title'], '新')
        self.assertTrue(queue.empty())

    def test_remote_post_rejected(self):
        """非本机请求 403，且不入队。"""
        response = asyncio.run(notify_api.notify(FakeRequest(body={'instance': 'alas'}, host='example.com')))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(notify_api._notification_queue.empty())

    def test_remote_host_header_is_rejected_even_from_local_client(self):
        """连接地址与 Host 头必须都为本机，防止回环访问伪装。"""
        request = FakeRequest(body={'title': '测试'})
        request.headers['host'] = 'app.example.com'
        response = asyncio.run(notify_api.notify(request))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(notify_api._notification_queue.empty())

    def test_invalid_body_rejected(self):
        """非法 JSON 与非对象请求体一律 400，不入队。"""
        invalid = asyncio.run(notify_api.notify(FakeRequest(body=None)))
        not_object = asyncio.run(notify_api.notify(FakeRequest(body=['alas'])))
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(not_object.status_code, 400)
        self.assertTrue(notify_api._notification_queue.empty())


class TestNotifyStreamEndpoint(unittest.TestCase):
    def test_remote_stream_rejected(self):
        """通知流只允许本机订阅：远端连接直接 403。"""
        response = asyncio.run(notify_api.notify_stream(FakeRequest(host='example.com')))
        self.assertEqual(response.status_code, 403)

    def test_stream_preserves_original_payload_and_finishes_after_disconnect(self):
        """直接读取一个 SSE 帧，避免常驻流测试阻塞或启动真实服务。"""
        async def read_frame():
            queue = asyncio.Queue(maxsize=100)
            payload = {'title': '启动器', 'body': '旧正文', 'extra': {'test': True}}
            queue.put_nowait(payload)
            request = FakeRequest()
            request.is_disconnected = AsyncMock(side_effect=[False, True])
            with patch.object(notify_api, '_notification_queue', queue):
                response = await notify_api.notify_stream(request)
                frame = await anext(response.body_iterator)
                with self.assertRaises(StopAsyncIteration):
                    await anext(response.body_iterator)
                return frame

        frame = asyncio.run(read_frame())
        self.assertTrue(frame.startswith('data: '))
        self.assertEqual(json.loads(frame[6:].strip()),
                         {'title': '启动器', 'body': '旧正文', 'extra': {'test': True}})


class TestWebSocketNotification(unittest.TestCase):
    WS_HEADERS = {'host': '127.0.0.1:25548'}
    """TestClient 的 WS 握手 Host 写死 testserver，会误判为远程客户端，这里显式覆盖。"""
    def setUp(self):
        """按 test_api 的隔离模式构造应用：临时配置目录、不启真实进程。"""
        queue = asyncio.Queue(maxsize=notify_api._NOTIFICATION_QUEUE_MAXSIZE)
        override = patch.object(notify_api, '_notification_queue', queue)
        override.start()
        self.addCleanup(override.stop)
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        (root / 'config').mkdir()
        (root / 'module/config/argument').mkdir(parents=True)
        (root / 'module/config/i18n').mkdir(parents=True)
        for relative in ['module/config/argument/args.json', 'module/config/argument/menu.json',
                         'module/config/i18n/zh-CN.json', 'module/config/i18n/en-US.json', 'config/template.json']:
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_bytes((ROOT / relative).read_bytes())
        self.app = create_app(root=root, password='test', manage_runtime=False, mount_mcp=False)
        # Host 头决定本机判定，默认 testserver 会被当成远程客户端。
        self.client = TestClient(self.app, base_url='http://127.0.0.1:25548', client=('127.0.0.1', 50000))

    def tearDown(self):
        self.temp.cleanup()

    def test_post_broadcasts_to_websocket(self):
        """本机 POST 通知后，已订阅 notifications 的 WS 会话实时收到事件。"""
        with self.client.websocket_connect('/api/v1/ws', headers=self.WS_HEADERS) as ws:
            session = ws.receive_json()
            self.assertEqual(session['topic'], 'session')
            ws.send_json({'v': 1, 'type': 'request', 'id': '1', 'method': 'events.subscribe',
                          'params': {'instance': None, 'topics': ['notifications', 'instances']}})
            reply = ws.receive_json()
            self.assertTrue(reply['ok'], reply)
            response = self.client.post('/api/notify', json={'instance': 'alas', 'title': '标题', 'content': '正文'})
            self.assertEqual(response.status_code, 200)
            event = ws.receive_json()
            self.assertEqual(event['type'], 'event')
            self.assertEqual(event['topic'], 'notifications')
            self.assertEqual(event['data'], {'instance': 'alas', 'title': '标题', 'content': '正文'})

    def test_unsubscribed_session_not_disturbed(self):
        """未订阅 notifications 的会话不受广播影响，POST 仍成功。"""
        with self.client.websocket_connect('/api/v1/ws', headers=self.WS_HEADERS) as ws:
            ws.receive_json()
            response = self.client.post('/api/notify', json={'instance': 'alas', 'title': '标题', 'content': '正文'})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(notify_api._notification_queue.empty())

    def test_notification_subscription_still_requires_authentication(self):
        """全局通知主题只免实例参数，不免远程访问认证。"""
        remote = TestClient(self.app, base_url='https://app.example.com', client=('203.0.113.7', 50000))
        with remote.websocket_connect('/api/v1/ws') as ws:
            self.assertTrue(ws.receive_json()['data']['authRequired'])
            ws.send_json({'v': 1, 'type': 'request', 'id': '1', 'method': 'events.subscribe',
                          'params': {'instance': None, 'topics': ['notifications']}})
            reply = ws.receive_json()
            self.assertFalse(reply['ok'])
            self.assertEqual(reply['error']['code'], 'UNAUTHORIZED')

    def test_emulator_subscription_requires_an_instance(self):
        """新增全局通知不会放宽定制模拟器主题的实例约束。"""
        with self.client.websocket_connect('/api/v1/ws', headers=self.WS_HEADERS) as ws:
            ws.receive_json()
            ws.send_json({'v': 1, 'type': 'request', 'id': '1', 'method': 'events.subscribe',
                          'params': {'instance': None, 'topics': ['notifications', 'emulator']}})
            reply = ws.receive_json()
            self.assertFalse(reply['ok'])
            self.assertEqual(reply['error']['code'], 'INVALID_PARAMS')

    def test_websocket_disconnect_cleans_notification_listener(self):
        """会话生命周期会实际取消通知协程并回收广播监听器。"""
        previous = set(hub.listeners)
        with self.client.websocket_connect('/api/v1/ws', headers=self.WS_HEADERS) as ws:
            ws.receive_json()
            ws.send_json({'v': 1, 'type': 'request', 'id': '1', 'method': 'events.subscribe',
                          'params': {'instance': None, 'topics': ['notifications']}})
            self.assertTrue(ws.receive_json()['ok'])
            self.assertEqual(len(hub.listeners), len(previous) + 1)
        self.assertEqual(set(hub.listeners), previous)


class TestNotificationProducer(unittest.IsolatedAsyncioTestCase):
    async def start_producer(self, *, authorized=True, topics=None):
        session = Session(SimpleNamespace(password='test'), AsyncMock(), local=authorized)
        session.subscription = SubscribeParams(topics=topics or [])
        previous = set(hub.listeners)
        task = asyncio.create_task(session.notification_producer())
        await asyncio.sleep(0)
        self.assertEqual(len(hub.listeners), len(previous) + 1)

        async def cleanup():
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(set(hub.listeners), previous)

        self.addAsyncCleanup(cleanup)
        return session

    async def test_unsubscribed_and_unauthorized_sessions_do_not_receive_events(self):
        """授权与订阅在实际生产者中共同控制广播，其他会话不受干扰。"""
        unauthorized = await self.start_producer(authorized=False, topics=['notifications'])
        unsubscribed = await self.start_producer(authorized=True, topics=['instances'])
        subscribed = await self.start_producer(authorized=True, topics=['notifications'])
        notification = {'instance': 'another', 'title': '任务', 'content': '结束'}
        hub.publish(notification)
        event = await asyncio.wait_for(subscribed.queue.get(), timeout=1)
        self.assertEqual(event['topic'], 'notifications')
        self.assertEqual(event['data'], notification)
        self.assertTrue(unauthorized.queue.empty())
        self.assertTrue(unsubscribed.queue.empty())

    async def test_disconnect_cleanup_removes_the_hub_listener(self):
        """生产者取消时必须退订，否则后续推送仍占用断开会话。"""
        await self.start_producer(authorized=True, topics=['notifications'])

    def test_protocol_accepts_all_seven_topics(self):
        """新通知主题与本地模拟器主题同时保留。"""
        topics = ['instances', 'overview', 'logs', 'preview', 'emulator', 'stock', 'notifications']
        self.assertEqual(SubscribeParams(instance='testpilot', topics=topics).topics, topics)

    async def test_snapshot_producer_does_not_reuse_action_for_notifications(self):
        """采样通知主题会错误复用前一个实例快照，必须完全跳过。"""
        session = Session(SimpleNamespace(
            password='', router=SimpleNamespace(runtime=SimpleNamespace(instances=lambda: [])),
            workers=asyncio.Semaphore(1)), ws=None, local=True)
        session.subscription = SubscribeParams(topics=['instances', 'notifications'])
        session.event = AsyncMock()
        with patch('module.api.socket.asyncio.sleep', new=AsyncMock(side_effect=[None, asyncio.CancelledError])):
            with self.assertRaises(asyncio.CancelledError):
                await session.producer()
        session.event.assert_awaited_once_with('instances', [])


if __name__ == '__main__':
    unittest.main()
