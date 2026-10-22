"""慢公告不阻塞首屏和操作队列，且仍受认证、去重与会话回收约束。"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from module.api.socket import Gateway, Session


class AnnouncementSocketTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.input = asyncio.Queue()
        self.release = asyncio.Event()
        self.started = asyncio.Event()

        async def fetch(force=False):
            self.started.set()
            await self.release.wait()
            return {'announcementId': 'test', 'title': '测试', 'content': '', 'url': ''}

        self.announcements = SimpleNamespace(get_async=AsyncMock(side_effect=fetch))
        self.router = SimpleNamespace(announcements=self.announcements,
                                      dispatch=Mock(return_value={'pong': True}))
        self.ws = SimpleNamespace(receive_text=self.receive, close=AsyncMock())
        self.session = Session(Gateway(self.router, ''), self.ws)
        self.reader = asyncio.create_task(self.session.reader())

    async def asyncTearDown(self):
        self.release.set()
        self.reader.cancel()
        tasks = [self.reader]
        if self.session.announcement_task is not None:
            tasks.append(self.session.announcement_task)
        await asyncio.gather(*tasks, return_exceptions=True)

    async def receive(self):
        import json
        return json.dumps(await self.input.get())

    async def send(self, identity, method, params=None):
        await self.input.put({'v': 1, 'type': 'request', 'id': identity,
                              'method': method, 'params': params or {}})

    async def reply(self):
        return await asyncio.wait_for(self.session.queue.get(), 1)

    async def test_slow_announcement_allows_later_requests_in_original_order(self):
        await self.send('news', 'announcement.get')
        await asyncio.wait_for(self.started.wait(), 1)
        await self.send('first', 'system.ping')
        await self.send('second', 'system.ping')
        self.assertEqual((await self.reply())['id'], 'first')
        self.assertEqual((await self.reply())['id'], 'second')
        self.assertEqual(self.router.dispatch.call_count, 2)
        self.release.set()
        result = await self.reply()
        self.assertEqual(result['id'], 'news')
        self.assertEqual(result['result']['announcementId'], 'test')

    async def test_announcement_requires_authentication(self):
        self.session.authorized = False
        await self.send('news', 'announcement.get')
        self.assertEqual((await self.reply())['error']['code'], 'UNAUTHORIZED')
        self.announcements.get_async.assert_not_called()

    async def test_inflight_is_bounded_and_request_id_reserved(self):
        await self.send('news', 'announcement.get')
        await asyncio.wait_for(self.started.wait(), 1)
        await self.send('news', 'announcement.get')
        self.assertEqual((await self.reply())['error']['code'], 'DUPLICATE_REQUEST')
        await self.send('another', 'announcement.get')
        self.assertEqual((await self.reply())['error']['code'], 'RATE_LIMITED')
        self.announcements.get_async.assert_awaited_once()

    async def test_announcement_validates_parameters(self):
        await self.send('news', 'announcement.get', {'force': 'true'})
        self.assertEqual((await self.reply())['error']['code'], 'INVALID_PARAMS')
        self.announcements.get_async.assert_not_called()

    async def test_revoked_pairing_does_not_receive_background_result(self):
        await self.send('news', 'announcement.get')
        await asyncio.wait_for(self.started.wait(), 1)
        self.session.paired_token = 'revoked'
        self.session.gateway.paired_authorized = Mock(return_value=False)
        self.release.set()
        self.assertEqual((await self.reply())['error']['code'], 'UNAUTHORIZED')

    async def test_background_failure_is_reported_and_reader_keeps_working(self):
        self.announcements.get_async.side_effect = RuntimeError('test failure')
        await self.send('news', 'announcement.get')
        self.assertEqual((await self.reply())['error']['code'], 'INTERNAL_ERROR')
        await self.send('ping', 'system.ping')
        self.assertTrue((await self.reply())['ok'])

    async def test_revocation_detected_by_reader_also_blocks_pending_announcement(self):
        await self.send('news', 'announcement.get')
        await asyncio.wait_for(self.started.wait(), 1)
        self.session.paired_token = 'revoked'
        self.session.gateway.paired_authorized = Mock(return_value=False)
        await self.send('ping', 'system.ping')
        self.assertEqual((await self.reply())['error']['code'], 'UNAUTHORIZED')
        self.assertFalse(self.session.authorized)
        self.assertEqual(self.session.paired_token, '')
        self.release.set()
        self.assertEqual((await self.reply())['error']['code'], 'UNAUTHORIZED')
