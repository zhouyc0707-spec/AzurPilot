"""作战委托复用共享维护计划，按区服日期安排查询与维护前委托。"""
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from module.handover.handover import OperationHandover


class FakeHandover:
    handover_maintain_query = OperationHandover.handover_maintain_query
    handover_maintain_timezone = OperationHandover.handover_maintain_timezone
    handover_maintain_state = OperationHandover.handover_maintain_state
    handover_idle_time = OperationHandover.handover_idle_time

    def __init__(self, server='cn'):
        self.config = SimpleNamespace(SERVER=server, OperationHandover_MaintainOverride=True,
                                      OperationHandover_ConsumeAllBook=False)


class TestHandoverMaintenance(unittest.TestCase):
    def setUp(self):
        self.snapshot = patch('module.handover.handover.get_maintenance_snapshot').start()
        self.addCleanup(patch.stopall)
        self.snapshot.return_value = {'state': 'not_published', 'startsAt': None,
                                      'checkedAt': '2026-11-13T08:00:00+08:00', 'error': None}

    def test_query_uses_shared_daily_refresh(self):
        self.snapshot.return_value.update(state='announced', startsAt='2026-11-13T09:00:00+08:00')
        payload, default, error = FakeHandover().handover_maintain_query()
        self.snapshot.assert_called_once_with('cn', refresh=True)
        self.assertEqual(payload['maintenance_date'], '2026-11-13')
        self.assertEqual(payload['start_time'], '09:00')
        self.assertEqual(payload['timezone'], 'UTC+8')
        self.assertEqual(default, timedelta(hours=8))
        self.assertEqual(error, '')

    def test_cold_start_explicit_jp_package_overrides_default_cn_for_query_and_midnight(self):
        fake = FakeHandover('cn')
        fake.config.data = {'Alas': {'Emulator': {'PackageName': 'com.YoStarJP.AzurLane'}}}
        fake.handover_maintain_query()
        self.snapshot.assert_called_once_with('jp', refresh=True)
        zone = timezone(timedelta(hours=9))
        now = datetime(2026, 11, 13, 19, tzinfo=zone).astimezone().replace(tzinfo=None)
        expected = datetime(2026, 11, 14, 0, tzinfo=zone).astimezone().replace(tzinfo=None)
        with patch('module.handover.handover.current_time', return_value=now):
            self.assertEqual(fake.handover_idle_time(None, False), expected)

    def test_failed_query_uses_valid_shared_cache(self):
        self.snapshot.return_value.update(state='error', startsAt='2026-11-13T09:00:00+08:00', error='offline')
        payload, _, error = FakeHandover().handover_maintain_query()
        self.assertEqual(payload['start_time'], '09:00')
        self.assertEqual(error, '')

    def test_failed_query_without_cache_preserves_failure_state(self):
        self.snapshot.return_value.update(state='error', error='offline')
        payload, _, error = FakeHandover().handover_maintain_query()
        self.assertIsNone(payload)
        self.assertIn('未找到有效缓存', error)

    def test_success_without_announcement_is_ordinary_day(self):
        self.assertEqual(FakeHandover().handover_maintain_state(), (None, '未发布维护安排', True))

    def test_maintenance_today_follows_game_server_date(self):
        server_zone = timezone(timedelta(hours=-7))
        start = datetime(2026, 11, 13, 14, tzinfo=server_zone)
        self.snapshot.return_value.update(state='announced', startsAt=start.isoformat())
        now = datetime(2026, 11, 13, 11, tzinfo=server_zone).astimezone().replace(tzinfo=None)
        with patch('module.handover.handover.current_time', return_value=now):
            actual, reason, queried = FakeHandover('en').handover_maintain_state()
        self.assertEqual(actual, start.astimezone().replace(tzinfo=None))
        self.assertIn('维护前', reason)
        self.assertTrue(queried)

    def test_today_already_started_maintenance_remains_distinguishable(self):
        zone = timezone(timedelta(hours=9))
        start = datetime(2026, 11, 13, 8, tzinfo=zone)
        self.snapshot.return_value.update(state='announced', startsAt=start.isoformat())
        now = (start + timedelta(hours=2)).astimezone().replace(tzinfo=None)
        with patch('module.handover.handover.current_time', return_value=now):
            actual, reason, queried = FakeHandover('jp').handover_maintain_state()
        self.assertEqual(actual, start.astimezone().replace(tzinfo=None))
        self.assertIn('已经过去', reason)
        self.assertTrue(queried)

    def test_future_maintenance_keeps_ordinary_flow_today(self):
        start = datetime(2026, 11, 14, 8, tzinfo=timezone(timedelta(hours=8)))
        self.snapshot.return_value.update(state='announced', startsAt=start.isoformat())
        now = (start - timedelta(days=1)).astimezone().replace(tzinfo=None)
        with patch('module.handover.handover.current_time', return_value=now):
            actual, reason, queried = FakeHandover().handover_maintain_state()
        self.assertIsNone(actual)
        self.assertIn('不是今天', reason)
        self.assertTrue(queried)

    def test_failed_query_idles_until_next_server_midnight(self):
        zone = timezone(timedelta(hours=-7))
        now = datetime(2026, 11, 13, 19, tzinfo=zone)
        expected = datetime(2026, 11, 14, 0, tzinfo=zone).astimezone().replace(tzinfo=None)
        with patch('module.handover.handover.current_time', return_value=now.astimezone().replace(tzinfo=None)):
            self.assertEqual(FakeHandover('en').handover_idle_time(None, False), expected)

    def test_disabled_switch_does_not_query(self):
        fake = FakeHandover()
        fake.config.OperationHandover_MaintainOverride = False
        self.assertEqual(fake.handover_maintain_state(), (None, '开关未开启', False))
        self.snapshot.assert_not_called()


if __name__ == '__main__':
    unittest.main()
