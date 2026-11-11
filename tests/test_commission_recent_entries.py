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

    def test_none_limit_returns_all_current_month_records(self):
        """本月超过 50 条时也全部返回，不跨月补足。"""
        self.buckets[(self.now.year, self.now.month)] = [
            entry(f'2026-09-28T10:{minute:02d}:00') for minute in range(60)
        ]

        entries = self.query(limit=None)

        self.assertEqual(len(entries), 60)
        self.assertEqual(entries[0]['ts'], '2026-09-28T10:59:00')
        self.assertEqual(entries[-1]['ts'], '2026-09-28T10:00:00')

    def test_default_limit_remains_ten(self):
        self.buckets[(self.now.year, self.now.month)] = [
            entry(f'2026-09-28T10:{minute:02d}:00') for minute in range(12)
        ]

        self.assertEqual(len(self.query()), 10)


class MonthlyGemCommissionSuccessTest(unittest.TestCase):
    def query(self, entries, income=None, **kwargs):
        with patch.object(stats, 'cl1_db') as db, \
                patch.object(stats, 'datetime', FrozenDatetime):
            db.get_gem_commissions.return_value = entries
            db.get_commission_income.return_value = income or []
            result = stats.get_monthly_gem_commission_success('alas', **kwargs)
            return result, db.get_gem_commissions.call_args

    def test_success_rate_uses_completed_gem_commissions(self):
        entries = [
            {'ts': '2026-09-01T12:00:00', 'duration': 2, 'reward': 10, 'success': True},
            {'ts': '2026-09-02T12:00:00', 'duration': 4, 'reward': 0, 'success': False},
            {'ts': '2026-09-03T12:00:00', 'duration': 8, 'reward': 0, 'success': False},
        ]

        result, call = self.query(entries)

        self.assertEqual(result, {'successes': 1, 'completed': 3, 'rate': 33.33})
        self.assertEqual(call.args, ('alas', 2026, 9))

    def test_unknown_and_other_month_records_do_not_inflate_denominator(self):
        entries = [
            {'ts': '2026-09-01T12:00:00', 'duration': 2, 'reward': 10},
            {'ts': '2026-08-31T12:00:00', 'duration': 4, 'reward': 0},
            {'ts': 'bad-time', 'duration': 8, 'reward': 0},
            {'ts': '2026-09-02T12:00:00', 'duration': 1, 'reward': 0},
            {'ts': '2026-09-03T12:00:00', 'duration': 2},
            {'ts': '2026-09-04T12:00:00', 'duration': 2, 'reward': -1},
        ]

        result, _ = self.query(entries)

        self.assertEqual(result, {'successes': 1, 'completed': 1, 'rate': 100.0})

    def test_no_completed_records_has_unknown_rate(self):
        result, call = self.query([], year=2025, month=12)

        self.assertEqual(result, {'successes': 0, 'completed': 0, 'rate': None})
        self.assertEqual(call.args, ('alas', 2025, 12))

    def test_income_fills_missing_success_history_without_double_counting(self):
        """实际漏记回归，21 次成功收获中有一笔 40 钻缺少结算历史。"""
        income = [
            entry(f'2026-11-{day:02d}T12:00:00', 22 if day <= 18 else 24) for day in range(1, 21)
        ]
        income.append(entry('2026-11-09T19:49:00.299342', 40))
        history = [
            {'ts': item['ts'], 'duration': 4, 'reward': item['items']['Gem']} for item in income[:20]
        ]
        history.extend(
            {'ts': '2026-11-01T10:00:00', 'duration': 2, 'reward': 0} for _ in range(40)
        )

        result, _ = self.query(history, income=income, year=2026, month=11)

        self.assertEqual(sum(item['reward'] for item in history), 444)
        self.assertEqual(sum(item['items']['Gem'] for item in income), 484)
        self.assertEqual(result, {'successes': 21, 'completed': 61, 'rate': 34.43})

    def test_success_union_preserves_duplicate_multiplicity_and_unmatched_history(self):
        income = [
            entry('2026-09-01T12:00:00', 10),
            entry('2026-09-01T12:00:00', 10),
            {'ts': '2026-09-02T12:00:00', 'items': {'Gems': '20'}},
        ]
        history = [
            {'ts': '2026-09-01 12:00:00', 'duration': 2, 'reward': 10},
            {'ts': '2026-09-02T12:00:00', 'duration': 4, 'reward': 20},
            {'ts': '2026-09-02T12:00:00', 'duration': 4, 'reward': 20},
            {'ts': '2026-09-03T12:00:00', 'reward': 50},
        ]

        result, _ = self.query(history, income=income)

        self.assertEqual(result, {'successes': 5, 'completed': 5, 'rate': 100.0})

    def test_invalid_and_ordinary_income_do_not_become_completed_gem_commissions(self):
        income = [
            entry('2026-08-31T12:00:00', 10),
            entry('bad-time', 10),
            entry('2026-09-01T12:00:00', -1),
            entry('2026-09-02T12:00:00', 'unknown'),
            {'ts': '2026-09-03T12:00:00', 'items': {'Cube': 2}, 'commission_count': 100},
            {'ts': '2026-09-04T12:00:00', 'items': None},
        ]

        result, _ = self.query([], income=income)

        self.assertEqual(result, {'successes': 0, 'completed': 0, 'rate': None})


if __name__ == '__main__':
    unittest.main()
