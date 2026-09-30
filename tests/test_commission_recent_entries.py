"""「最近委托记录」只读当前自然月的回归测试。

旧界面会读「当前月 + 32 天前所在月 + 64 天前所在月」三个归档桶补足条数，
导致新月份开始后仍显示上月记录（用户 2026-09 实测看到 8 月记录）。
现在默认只读当前月；需要旧行为时显式传 cross_month=True。
"""
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import module.statistics.commission_income_stats as stats


def entry(ts, amount=60):
    return {'ts': ts, 'items': {'Gem': amount}, 'commission_count': 1}


class FrozenDatetime(datetime):
    """冻结「现在」，但保留 fromisoformat 等真实行为（_parse_ts 要用）。"""

    frozen = datetime(2026, 9, 29, 12, 0, 0)

    @classmethod
    def now(cls, tz=None):
        return cls.frozen


class RecentCommissionEntriesTest(unittest.TestCase):
    def setUp(self):
        self.now = FrozenDatetime.frozen
        self.last_month = self.now - timedelta(days=32)          # 2026-08-28 → 8 月桶
        self.buckets = {
            (self.now.year, self.now.month): [entry('2026-09-28 10:00:00')],
            (self.last_month.year, self.last_month.month): [entry('2026-08-15 10:00:00')],
        }

    def fake_db(self, instance, year, month):
        return list(self.buckets.get((year, month), []))

    def query(self, **kwargs):
        with patch.object(stats, 'cl1_db') as db, \
                patch.object(stats, 'datetime', FrozenDatetime):
            db.get_commission_income.side_effect = self.fake_db
            return stats.get_recent_commission_entries('alas', **kwargs)

    def test_default_reads_current_month_only(self):
        """默认只读当前月：8 月的记录不再出现。"""
        entries = self.query(limit=50)

        self.assertEqual([e['ts'] for e in entries], ['2026-09-28 10:00:00'])

    def test_cross_month_keeps_legacy_behavior(self):
        """cross_month=True 时保留旧界面的三桶回溯。"""
        entries = self.query(limit=50, cross_month=True)

        self.assertEqual([e['ts'] for e in entries],
                         ['2026-09-28 10:00:00', '2026-08-15 10:00:00'])

    def test_empty_current_month_returns_nothing(self):
        """本月没有记录时返回空，而不是回退到上月。"""
        self.buckets[(self.now.year, self.now.month)] = []

        self.assertEqual(self.query(limit=50), [])

    def test_limit_is_respected(self):
        """条数上限仍然生效（时间倒序取前 N）。"""
        self.buckets[(self.now.year, self.now.month)] = [
            entry('2026-09-28 10:00:00'), entry('2026-09-27 10:00:00'), entry('2026-09-26 10:00:00'),
        ]

        entries = self.query(limit=2)

        self.assertEqual([e['ts'] for e in entries],
                         ['2026-09-28 10:00:00', '2026-09-27 10:00:00'])


if __name__ == '__main__':
    unittest.main()
