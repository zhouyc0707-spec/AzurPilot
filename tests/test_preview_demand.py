"""预览只在有人观看时复制和编码；测试使用内存图像，不访问模拟器或配置。"""

import asyncio
import json
import multiprocessing
import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import numpy as np
from starlette.websockets import WebSocketDisconnect

from module.api.protocol import SubscribeParams
from module.api.runtime_service import RuntimeService
from module.api.socket import Gateway, Session
from module.runtime import preview


class EncoderFinished(Exception):
    """同步执行编码线程时表示测试输入已消费完。"""


def run_preview_worker(demand, commands, replies, output):
    """真实 spawn 子进程共享需求信号，只提交人工生成的小图。"""
    from module.runtime.preview import initialize, publish

    class CountedImage:
        copies = 0

        def copy(self):
            self.copies += 1
            return np.full((8, 8, 3), (220, 30, 10), dtype=np.uint8)

    image = CountedImage()
    initialize('pilot', output, lambda _: None, 'spawn-run', demand)
    replies.put('ready')
    while True:
        command = commands.get(timeout=10)
        if command == 'stop':
            return
        publish(image)
        replies.put(image.copies)


class PreviewDemandHubTests(unittest.TestCase):
    def test_viewers_are_counted_per_instance_and_switching_releases_old_worker(self):
        hub = preview.PreviewHub()
        first, second = threading.Event(), threading.Event()
        hub.bind_worker('first', first)
        hub.bind_worker('second', second)
        owner_a, owner_b = object(), object()
        hub.set_viewer(owner_a, 'first')
        hub.set_viewer(owner_a, 'first')
        hub.set_viewer(owner_b, 'first')
        self.assertTrue(first.is_set())
        self.assertFalse(second.is_set())

        hub.set_viewer(owner_a, 'second')
        self.assertTrue(first.is_set())
        self.assertTrue(second.is_set())
        hub.set_viewer(owner_b, None)
        self.assertFalse(first.is_set())
        self.assertTrue(second.is_set())
        hub.set_viewer(owner_a, None)
        hub.set_viewer(owner_a, None)
        self.assertFalse(second.is_set())

    def test_viewer_before_worker_start_and_replacement_worker_keep_demand(self):
        hub = preview.PreviewHub()
        owner = object()
        hub.set_viewer(owner, 'pilot')
        old, replacement = threading.Event(), threading.Event()
        hub.bind_worker('pilot', old)
        self.assertTrue(old.is_set())
        hub.bind_worker('pilot', replacement)
        self.assertFalse(old.is_set())
        self.assertTrue(replacement.is_set())
        hub.set_viewer(owner, None)
        self.assertFalse(replacement.is_set())

    def test_cache_reads_and_listeners_do_not_activate_screenshots(self):
        hub = preview.PreviewHub()
        demand = threading.Event()
        hub.bind_worker('pilot', demand)
        listener = Mock()
        hub.subscribe(listener)
        runtime = RuntimeService(SimpleNamespace(path=Mock()))
        with patch.object(preview, 'hub', hub), patch('subprocess.run') as process:
            self.assertIsNone(hub.get('pilot')['image'])
            self.assertIsNone(runtime.capture('pilot')['image'])
            frame = {'instance': 'pilot', 'image': 'cached', 'capturedAt': 'old'}
            hub.publish('pilot', frame)
            self.assertEqual(runtime.capture('pilot'), frame)
            self.assertFalse(demand.is_set())
            process.assert_not_called()
        listener.assert_called_once_with('pilot')
        hub.unsubscribe(listener)
        self.assertFalse(demand.is_set())


class PreviewDemandWorkerTests(unittest.TestCase):
    def setUp(self):
        # initialize 会重设模块状态；每项测试结束后恢复，编码线程用同步夹具驱动。
        self.enterContext(patch.dict(preview.__dict__))
        self.enterContext(patch('module.runtime.worker_events.initialize'))
        self.thread = self.enterContext(patch.object(preview.threading, 'Thread'))
        self.output = queue.Queue(maxsize=4)

    def initialize(self, demand=None):
        if demand is None:
            preview.initialize('pilot', self.output, lambda _: None, 'test-run')
        else:
            preview.initialize('pilot', self.output, lambda _: None, 'test-run', demand)
        return self.thread.call_args.kwargs['target'] if self.thread.called else None

    def test_absent_or_inactive_demand_never_copies_image(self):
        for demand in (None, threading.Event()):
            with self.subTest(demand=demand):
                self.initialize(demand)
                image = Mock()
                for _ in range(5):
                    preview.publish(image)
                image.copy.assert_not_called()
                self.assertTrue(self.output.empty())
                self.assertTrue(preview._images is None or preview._images.empty())

    def test_active_viewer_copies_at_most_two_frames_each_second(self):
        demand = threading.Event()
        demand.set()
        self.initialize(demand)
        image = Mock(copy=Mock(return_value=np.zeros((8, 8, 3), dtype=np.uint8)))
        times = (100, 100.1, 100.49, 100.5, 100.51, 100.99, 101, 101.1)
        with patch.object(preview.time, 'monotonic', side_effect=times):
            for _ in times:
                preview.publish(image)
        self.assertEqual(image.copy.call_count, 3)
        self.assertEqual(preview._images.qsize(), 1)
        demand.clear()
        preview.publish(image)
        self.assertEqual(image.copy.call_count, 3)

    def test_demand_cleared_before_encoding_discards_pending_frame(self):
        demand = threading.Event()
        demand.set()
        pixels = np.zeros((8, 8, 3), dtype=np.uint8)
        images = SimpleNamespace(get=Mock(side_effect=[(pixels, 'pending'), EncoderFinished()]))
        with patch.object(preview.queue, 'Queue', return_value=images):
            encode = self.initialize(demand)
        demand.clear()
        with patch('PIL.Image.fromarray') as convert:
            with self.assertRaises(EncoderFinished):
                encode()
            convert.assert_not_called()
        self.assertTrue(self.output.empty())

    def test_demand_cleared_during_encoding_does_not_transmit_frame(self):
        demand = threading.Event()
        demand.set()
        pixels = np.zeros((8, 8, 3), dtype=np.uint8)
        images = SimpleNamespace(get=Mock(side_effect=[(pixels, 'pending'), EncoderFinished()]))
        with patch.object(preview.queue, 'Queue', return_value=images):
            encode = self.initialize(demand)

        def save(buffer, **kwargs):
            buffer.write(b'encoded')
            demand.clear()

        with patch('PIL.Image.fromarray', return_value=SimpleNamespace(save=save)):
            with self.assertRaises(EncoderFinished):
                encode()
        self.assertTrue(self.output.empty())


class PreviewDemandSessionTests(unittest.TestCase):
    def make_session(self, *, authorized=True):
        configs = SimpleNamespace(path=Mock())
        session = Session(Gateway(SimpleNamespace(configs=configs), ''), SimpleNamespace(
            accept=AsyncMock(), close=AsyncMock(), receive_text=AsyncMock(),
        ))
        session.authorized = authorized
        return session

    def test_authorization_topic_and_selected_instance_gate_demand(self):
        hub = preview.PreviewHub()
        pilot, other = threading.Event(), threading.Event()
        hub.bind_worker('pilot', pilot)
        hub.bind_worker('other', other)
        session = self.make_session(authorized=False)
        session.subscription = SubscribeParams(instance='pilot', topics=['preview'])
        with patch.object(preview, 'hub', hub):
            session._sync_preview_demand()
            self.assertFalse(pilot.is_set())
            session.authorized = True
            session._sync_preview_demand()
            self.assertTrue(pilot.is_set())
            session.subscription = SubscribeParams(instance='other', topics=['preview'])
            session._sync_preview_demand()
            self.assertFalse(pilot.is_set())
            self.assertTrue(other.is_set())
            session.subscription = SubscribeParams(instance='other', topics=['logs'])
            session._sync_preview_demand()
            self.assertFalse(other.is_set())

    def test_successful_subscription_registers_demand_and_revocation_releases_it(self):
        async def check():
            hub = preview.PreviewHub()
            demand = threading.Event()
            hub.bind_worker('pilot', demand)
            session = self.make_session()
            request = json.dumps({'v': 1, 'type': 'request', 'id': 'subscribe',
                                  'method': 'events.subscribe',
                                  'params': {'instance': 'pilot', 'topics': ['preview']}})
            with patch.object(preview, 'hub', hub):
                session.ws.receive_text.side_effect = [request, WebSocketDisconnect(1000)]
                with self.assertRaises(WebSocketDisconnect):
                    await session.reader()
                self.assertTrue(demand.is_set())
                session.paired_token = 'revoked-token'
                session.gateway.paired_authorized = Mock(return_value=False)
                session.ws.receive_text.side_effect = [request.replace('subscribe', 'revoked', 1),
                                                       WebSocketDisconnect(1000)]
                with self.assertRaises(WebSocketDisconnect):
                    await session.reader()
                self.assertFalse(session.authorized)
                self.assertFalse(demand.is_set())
        asyncio.run(check())

    def test_session_finally_releases_its_viewer(self):
        async def check():
            hub = preview.PreviewHub()
            demand = threading.Event()
            hub.bind_worker('pilot', demand)
            session = self.make_session()
            session.subscription = SubscribeParams(instance='pilot', topics=['preview'])

            async def blocked():
                await asyncio.Event().wait()

            for name in ('writer', 'producer', 'log_producer', 'preview_producer',
                         'stock_producer', 'notification_producer'):
                setattr(session, name, blocked)
            session.reader = AsyncMock(return_value=None)
            with patch.object(preview, 'hub', hub):
                session._sync_preview_demand()
                self.assertTrue(demand.is_set())
                await session.run()
                self.assertFalse(demand.is_set())
        asyncio.run(check())

    def test_writer_discards_queued_preview_after_authorization_is_revoked(self):
        async def check(mode):
            hub = preview.PreviewHub()
            demand = threading.Event()
            hub.bind_worker('pilot', demand)
            session = self.make_session()
            session.subscription = SubscribeParams(instance='pilot', topics=['preview'])
            sentinel = {'v': 1, 'type': 'response', 'id': 'sentinel', 'ok': True}

            async def sent(message):
                if message == sentinel:
                    raise EncoderFinished()

            session.ws.send_json = AsyncMock(side_effect=sent)
            with patch.object(preview, 'hub', hub):
                session._sync_preview_demand()
                self.assertTrue(demand.is_set())
                await session.event('preview', {'instance': 'pilot', 'image': 'queued'})
                await session.enqueue(sentinel)
                if mode == 'unauthorized':
                    session.authorized = False
                else:
                    session.paired_token = 'revoked-token'
                    session.gateway.paired_authorized = Mock(return_value=False)

                # 哨兵证明 writer 已越过排队的预览槽，不依赖等待时长猜测处理进度。
                with self.assertRaises(EncoderFinished):
                    await session.writer()
                session.ws.send_json.assert_awaited_once_with(sentinel)
                self.assertFalse(demand.is_set())

        for mode in ('unauthorized', 'paired_revoked'):
            with self.subTest(mode=mode):
                asyncio.run(check(mode))


class PreviewDemandSpawnTests(unittest.TestCase):
    def test_shared_event_controls_an_existing_spawn_worker(self):
        context = multiprocessing.get_context('spawn')
        demand = context.Event()
        commands, replies, output = context.Queue(), context.Queue(), context.Queue(maxsize=2)
        process = context.Process(target=run_preview_worker, args=(demand, commands, replies, output))
        hub = preview.PreviewHub()
        hub.bind_worker('pilot', demand)
        owner = object()
        process.start()
        try:
            self.assertEqual(replies.get(timeout=20), 'ready')
            commands.put('publish')
            self.assertEqual(replies.get(timeout=5), 0)
            with self.assertRaises(queue.Empty):
                output.get(timeout=.1)

            hub.set_viewer(owner, 'pilot')
            commands.put('publish')
            self.assertEqual(replies.get(timeout=5), 1)
            frame = output.get(timeout=5)
            self.assertEqual(frame['instance'], 'pilot')
            self.assertEqual(frame['runId'], 'spawn-run')
            self.assertTrue(frame['image'].startswith('data:image/jpeg;base64,'))

            hub.set_viewer(owner, None)
            commands.put('publish')
            self.assertEqual(replies.get(timeout=5), 1)
            with self.assertRaises(queue.Empty):
                output.get(timeout=.1)
        finally:
            commands.put('stop')
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            for channel in (commands, replies, output):
                channel.close()
                channel.join_thread()
        self.assertEqual(process.exitcode, 0)


if __name__ == '__main__':
    unittest.main()
