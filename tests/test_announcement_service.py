"""公告服务与 API 分发回归测试。"""
import asyncio
import threading
import unittest
from unittest.mock import Mock, patch

from module.api.announcement_service import AnnouncementService
from module.api.protocol import AnnouncementParams
from module.api.router import Router


class AnnouncementServiceTest(unittest.TestCase):
    def setUp(self):
        self.service = AnnouncementService(ttl=2)

    def test_get_announcement_and_caching(self):
        mock_data = {
            'announcementId': 'test-id-1',
            'title': '测试公告',
            'content': '测试公告内容',
            'url': 'https://example.com',
        }
        with patch('module.base.api_client.ApiClient.get_announcement', return_value=mock_data) as mock_get:
            res1 = self.service.get(force=False)
            self.assertEqual(res1, mock_data)
            self.assertEqual(mock_get.call_count, 1)

            # TTL 内再次获取，直接命中缓存，不应再次调用远端
            res2 = self.service.get(force=False)
            self.assertEqual(res2, mock_data)
            self.assertEqual(mock_get.call_count, 1)

            # force=True 时应忽略缓存强制请求
            self.service.get(force=True)
            self.assertEqual(mock_get.call_count, 2)

    def test_304_preserves_existing_cache(self):
        initial_data = {
            'announcementId': 'id-100',
            'title': '旧公告',
            'content': '旧内容',
            'url': '',
        }
        with patch('module.base.api_client.ApiClient.get_announcement', return_value=initial_data):
            self.service.get(force=False)

        # 远端返回 None（例如 304 未修改）
        with patch('module.base.api_client.ApiClient.get_announcement', return_value=None):
            result = self.service.get(force=True)
            self.assertEqual(result, initial_data)

    def test_router_dispatch(self):
        router = Router(configs=None, runtime=None)
        mock_data = {
            'announcementId': 'router-test',
            'title': '路由测试',
            'content': '分发测试',
            'url': '',
        }
        with patch('module.base.api_client.ApiClient.get_announcement', return_value=mock_data):
            res = router.dispatch('announcement.get', {'force': True})
            self.assertEqual(res, mock_data)

    def test_empty_and_failed_fetches_are_cached_until_completion_ttl(self):
        for outcome in (None, RuntimeError('offline')):
            with self.subTest(outcome=outcome):
                service = AnnouncementService(ttl=2)
                with patch('module.base.api_client.ApiClient.get_announcement',
                           side_effect=[outcome, {'announcementId': 'recovered'}]) as fetch, \
                        patch('module.api.announcement_service.time.monotonic', return_value=10) as clock:
                    self.assertIsNone(service.get())
                    clock.return_value = 11
                    self.assertIsNone(service.get())
                    self.assertEqual(fetch.call_count, 1)
                    clock.return_value = 12
                    self.assertEqual(service.get(), {'announcementId': 'recovered'})
                    self.assertEqual(fetch.call_count, 2)

    def test_failed_refresh_keeps_old_announcement(self):
        with patch('module.base.api_client.ApiClient.get_announcement',
                   side_effect=[{'announcementId': 'old'}, RuntimeError('offline')]) as fetch:
            self.assertEqual(self.service.get(), {'announcementId': 'old'})
            self.assertEqual(self.service.get(force=True), {'announcementId': 'old'})
            self.assertEqual(self.service.get(), {'announcementId': 'old'})
            self.assertEqual(fetch.call_count, 2)


class AnnouncementAsyncTest(unittest.IsolatedAsyncioTestCase):
    async def test_inflight_is_shared_and_disconnect_does_not_cancel_other_clients(self):
        service = AnnouncementService()
        started, release = threading.Event(), threading.Event()

        def slow_fetch(**kwargs):
            started.set()
            if not release.wait(3):
                raise AssertionError('测试未释放查询')
            return {'announcementId': 'shared'}

        with patch('module.base.api_client.ApiClient.get_announcement', side_effect=slow_fetch) as fetch:
            first = asyncio.create_task(service.get_async())
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                second = asyncio.create_task(service.get_async(force=True))
                await asyncio.sleep(0)
                first.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first
                self.assertFalse(second.done())
                release.set()
                self.assertEqual(await asyncio.wait_for(second, 2), {'announcementId': 'shared'})
                self.assertEqual(fetch.call_count, 1)
            finally:
                release.set()


if __name__ == '__main__':
    unittest.main()
