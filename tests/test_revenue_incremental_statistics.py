"""收益增量写与完整快照路径等价，覆盖保留上限、跨月结算和事务失败。"""

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from module.persistence.snapshots import MONTH_FIELDS
from module.statistics import cl1_database as database


NOW = datetime(2026, 10, 24, 12)
MONTH, PREVIOUS = '2026-10', '2026-09'


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


class RevenueIncrementalStatisticsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='revenue-incremental-', ignore_cleanup_errors=True)
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'config' / 'azurpilot.db'
        self.db = database.Cl1Database(self.path)
        self.db.store.ensure_ready()
        self.enterContext(patch.object(database, 'datetime', FixedDatetime))
        self.enterContext(patch.object(database, 'current_time', return_value=NOW))

    @staticmethod
    def research(index=0):
        return {'ts': f'2026-10-{index % 20 + 1:02d}T12:00:00', 'completed_at': NOW.isoformat(),
                'project': 'D-001', 'series': 8, 'imgid': f'image-{index}', 'items': {'PlateT3': index + 1}}

    @staticmethod
    def running(name='old', finish='2026-10-24T08:00:00', created='2026-09-30T23:00:00'):
        return {'name': name, 'duration': 8, 'create_time': created, 'finish_time': finish}

    def seed(self, instance='test', month=MONTH, **changes):
        data = {'battle_count': 15, 'ap_snapshots': [{'ts': NOW.isoformat(), 'ap': 100}],
                'coins_month_start_residue': [{'ts': '2026-10-01', 'purple_coins': 20}],
                'akashi_shop_champion': {'monthly': {'totalAp': 220, 'image': 'test.png'}},
                'research_drop_entries': [self.research()],
                'commission_income_entries': [{'ts': '2026-10-23', 'items': {'Cube': 2},
                                               'commission_count': 1, 'screenshots': ['old.png']}]}
        data.update(changes)
        self.db.save_stats(instance, month, data)
        return data

    def snapshot(self, tables=None):
        with closing(sqlite3.connect(self.path)) as conn:
            names = tables or [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            return {name: conn.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall() for name in names}

    def call(self, kind, instance='test', **kwargs):
        if kind == 'research':
            return self.db.add_research_drop(instance, 'D-002', 8, {'PlateT3': 2, 'Cube': 1},
                                             imgid=kwargs.pop('imgid', 'new'), **kwargs)
        return self.db.add_commission_income(instance, {'Gem': 60, 'Cube': 2}, screenshots=['new.png'], **kwargs)

    def assert_equivalent(self, kind, *, current=None, previous=None, **kwargs):
        for instance in ('fast', 'legacy'):
            self.seed(instance, **(current or {}))
            if previous is not None:
                self.seed(instance, PREVIOUS, **previous)
        with patch.object(self.db, '_get_stats_in_connection', side_effect=AssertionError('不应展开整月')), \
                patch.object(self.db, '_save_stats_in_connection', side_effect=AssertionError('不应重写整月')):
            actual = self.call(kind, 'fast', **kwargs)
        with patch.object(self.db, '_native_month_for_update', return_value=None):
            expected = self.call(kind, 'legacy', **kwargs)
        self.assertEqual(actual, expected)
        for month in (MONTH, PREVIOUS) if previous is not None else (MONTH,):
            self.assertEqual(self.db.get_stats('fast', month), self.db.get_stats('legacy', month))
        return actual

    def test_native_revenue_matches_complete_path_and_keeps_other_history_rows(self):
        for kind in ('research', 'commission'):
            with self.subTest(kind=kind):
                self.assert_equivalent(kind)
                self.seed()
                tables = ['action_point_snapshots', 'cl1_compat_fields', 'typed_value_sets', 'typed_value_nodes']
                before = self.snapshot(tables)
                self.call(kind)
                self.assertEqual(self.snapshot(tables), before)

    def test_research_duplicate_window_is_last_fifty_and_blank_ids_always_append(self):
        history = [self.research(index) for index in range(51)]
        for imgid, duplicate in (('image-1', True), ('image-0', False), ('', False)):
            with self.subTest(imgid=imgid):
                actual = self.assert_equivalent('research', current={'research_drop_entries': history}, imgid=imgid)
                self.assertEqual(actual is None, duplicate)
                self.assertEqual(len(self.db.get_stats('fast', MONTH)['research_drop_entries']), 51 + (not duplicate))

    def test_historical_import_uses_timestamp_month_and_remains_repairable(self):
        self.seed()
        self.seed(month=PREVIOUS)
        original_current = self.db.get_stats('test', MONTH)
        stamp = datetime(2026, 9, 20)
        self.call('research', imgid='historical', ts=stamp)
        self.assertEqual(self.db.get_stats('test', MONTH), original_current)
        repaired = self.db.update_research_drop_items('test', 'historical', {'CorrectPlate': 4})
        self.assertEqual(repaired['items'], {'CorrectPlate': 4})
        self.assertEqual(repaired['project'], 'D-002')
        self.assertEqual(repaired['series'], 8)
        self.assertEqual(repaired['ts'], stamp.isoformat())

    def test_missing_imgid_field_does_not_deduplicate_hidden_column(self):
        for instance in ('fast', 'legacy'):
            self.seed(instance)
            with closing(sqlite3.connect(self.path)) as conn, conn:
                conn.execute('UPDATE research_drops SET field_mask=field_mask & ~4 WHERE instance=?', (instance,))
        actual = self.call('research', 'fast', imgid='image-0')
        with patch.object(self.db, '_native_month_for_update', return_value=None):
            expected = self.call('research', 'legacy', imgid='image-0')
        self.assertEqual(actual, expected)
        self.assertIsNotNone(actual)
        self.assertEqual(self.db.get_stats('fast', MONTH), self.db.get_stats('legacy', MONTH))

    def test_native_caps_keep_last_five_thousand_and_delete_old_children(self):
        for kind, field in (('research', 'research_drop_entries'), ('commission', 'commission_income_entries')):
            with self.subTest(kind=kind):
                history = [self.research(index) if kind == 'research' else
                           {'ts': str(index), 'commission_count': 1, 'items': {'Cube': index},
                            'screenshots': [f'{index}.png']} for index in range(5002)]
                self.assert_equivalent(kind, current={field: history})
                entries = self.db.get_stats('fast', MONTH)[field]
                self.assertEqual(len(entries), 5000)
                self.assertEqual(entries[0], history[3])
                with closing(sqlite3.connect(self.path)) as conn:
                    self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
                    table, parent, key = (('research_drop_items', 'research_drops', 'drop_id') if kind == 'research'
                                          else ('commission_income_items', 'commission_income', 'income_id'))
                    self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM {table} c LEFT JOIN {parent} p '
                                                  f'ON p.id=c.{key} WHERE p.id IS NULL').fetchone()[0], 0)

    def test_new_month_and_missing_previous_month_use_original_defaults(self):
        for kind in ('research', 'commission'):
            actual = self.call(kind, f'new-{kind}')
            with patch.object(self.db, '_native_month_for_update', return_value=None):
                expected = self.call(kind, f'legacy-{kind}')
            self.assertEqual(actual, expected)
            self.assertEqual(self.db.get_stats(f'new-{kind}', MONTH), self.db.get_stats(f'legacy-{kind}', MONTH))
        self.seed('fast', running_gem_commissions=[self.running()])
        self.seed('legacy', running_gem_commissions=[self.running()])
        actual = self.call('commission', 'fast', gem_duration=8)
        with patch.object(self.db, '_native_month_for_update', return_value=None):
            expected = self.call('commission', 'legacy', gem_duration=8)
        self.assertEqual(actual, expected)
        self.assertEqual(self.db.get_stats('fast', MONTH), self.db.get_stats('legacy', MONTH))
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM cl1_months WHERE month=?', (PREVIOUS,)).fetchone()[0], 0)

    def test_cross_month_settlement_keeps_exact_match_priority_and_sorts_visited_lists(self):
        duplicate_previous = self.running('same', finish='2026-10-24T08:00:00')
        duplicate_current = self.running('same', finish='2026-10-24T14:00:00')
        later = self.running('later', finish='2026-10-24T11:00:00')
        earlier = self.running('earlier', finish='2026-10-24T10:00:00')
        actual = self.assert_equivalent(
            'commission', current={'running_gem_commissions': [later, duplicate_current, earlier]},
            previous={'running_gem_commissions': [duplicate_previous]}, gem_duration=8)
        self.assertEqual(actual, duplicate_current, '沿用上月选候选、当前月优先精确匹配的既有规则')
        self.assertEqual(self.db.get_stats('fast', PREVIOUS)['running_gem_commissions'], [duplicate_previous])
        self.assertEqual(self.db.get_stats('fast', MONTH)['running_gem_commissions'], [earlier, later])

    def test_previous_month_match_preserves_absent_current_running_field_and_caps_gem_history(self):
        history = [{'ts': str(i), 'duration': 8, 'reward': 1, 'success': True} for i in range(5000)]
        actual = self.assert_equivalent('commission', current={'gem_commission_entries': history},
                                       previous={'running_gem_commissions': [self.running()]}, gem_duration=8)
        self.assertEqual(actual, self.running())
        current = self.db.get_stats('fast', MONTH)
        self.assertNotIn('running_gem_commissions', current)
        self.assertEqual(len(current['gem_commission_entries']), 5000)
        self.assertEqual(current['gem_commission_entries'][0], history[1])
        self.assertEqual(self.db.get_stats('fast', PREVIOUS)['running_gem_commissions'], [])

    def test_no_due_match_does_not_sort_or_create_gem_history(self):
        running = [self.running('future', finish='2026-10-25T00:00:00'), self.running('bad', finish='invalid')]
        self.assertIsNone(self.assert_equivalent('commission', current={'running_gem_commissions': running},
                                                previous={'running_gem_commissions': []}, gem_duration=8))
        self.assertEqual(self.db.get_stats('fast', MONTH)['running_gem_commissions'], running)
        self.assertNotIn('gem_commission_entries', self.db.get_stats('fast', MONTH))

    def test_unknown_extensions_and_nested_compatibility_values_use_complete_path(self):
        variants = ({'custom': {'keep': [1, None]}},
                    {'research_drop_entries': [dict(self.research(), unknown={'keep': True})]},
                    {'research_drop_entries': [dict(self.research(), items={'Big': 2 ** 65})]},
                    {'commission_income_entries': [{'items': {'Nested': {'keep': 4}}}]})
        for index, data in enumerate(variants):
            for kind in ('research', 'commission'):
                with self.subTest(index=index, kind=kind):
                    self.seed('fast', **data)
                    self.seed('legacy', **data)
                    with patch.object(self.db, '_get_stats_in_connection', wraps=self.db._get_stats_in_connection) as read:
                        actual = self.call(kind, 'fast')
                    self.assertTrue(read.called)
                    with patch.object(self.db, '_native_month_for_update', return_value=None):
                        expected = self.call(kind, 'legacy')
                    self.assertEqual(actual, expected)
                    self.assertEqual(self.db.get_stats('fast', MONTH), self.db.get_stats('legacy', MONTH))

    def test_hidden_family_rows_fall_back_without_restoring_invisible_history(self):
        for kind, field in (('research', 'research_drop_entries'), ('commission', 'commission_income_entries')):
            for instance in ('fast', 'legacy'):
                self.seed(instance)
                with closing(sqlite3.connect(self.path)) as conn, conn:
                    conn.execute('UPDATE cl1_months SET field_mask=field_mask & ? WHERE instance=? AND month=?',
                                 (~(1 << MONTH_FIELDS.index(field)), instance, MONTH))
            with patch.object(self.db, '_get_stats_in_connection', wraps=self.db._get_stats_in_connection) as read:
                self.call(kind, 'fast')
            self.assertTrue(read.called)
            with patch.object(self.db, '_native_month_for_update', return_value=None):
                self.call(kind, 'legacy')
            self.assertEqual(self.db.get_stats('fast', MONTH), self.db.get_stats('legacy', MONTH))
            self.assertEqual(len(self.db.get_stats('fast', MONTH)[field]), 1)

    def test_native_failures_roll_back_income_children_and_cross_month_settlement(self):
        for table in ('commission_income_items', 'gem_commission_history', 'running_gem_commissions'):
            with self.subTest(table=table):
                self.seed(running_gem_commissions=[])
                self.seed(month=PREVIOUS, running_gem_commissions=[self.running()])
                event = 'DELETE' if table == 'running_gem_commissions' else 'INSERT'
                with closing(sqlite3.connect(self.path)) as conn, conn:
                    conn.execute(f"CREATE TRIGGER reject_revenue BEFORE {event} ON {table} "
                                 "BEGIN SELECT RAISE(ABORT, 'reject revenue'); END")
                before = self.snapshot()
                with self.assertRaises(sqlite3.IntegrityError):
                    self.call('commission', gem_duration=8)
                self.assertEqual(self.snapshot(), before)
                with closing(sqlite3.connect(self.path)) as conn, conn:
                    conn.execute('DROP TRIGGER reject_revenue')

    def test_commit_failure_rolls_back_and_releases_lock(self):
        self.seed()
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute('CREATE TABLE revenue_guard(id INTEGER PRIMARY KEY)')
            conn.execute('CREATE TABLE revenue_child(id REFERENCES revenue_guard(id) DEFERRABLE INITIALLY DEFERRED)')
            conn.execute('CREATE TRIGGER fail_commit AFTER INSERT ON research_drops '
                         'BEGIN INSERT INTO revenue_child VALUES (1); END')
        before = self.snapshot()
        with self.assertRaises(sqlite3.IntegrityError):
            self.call('research')
        self.assertEqual(self.snapshot(), before)
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute('DROP TRIGGER fail_commit')
        self.assertIsNotNone(self.call('research'))

    def test_success_clears_read_cache_and_concurrent_incomes_are_not_lost(self):
        self.seed()
        self.db.enable_read_cache()
        self.db.get_stats('test', MONTH)
        self.call('research')
        self.assertFalse(self.db._read_cache)
        other = database.Cl1Database(self.path)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda index: (self.db if index % 2 else other).add_commission_income(
                'test', {'Cube': index + 1}), range(12)))
        self.db.disable_read_cache()
        self.assertEqual(len(self.db.get_stats('test', MONTH)['commission_income_entries']), 13)


if __name__ == '__main__':
    unittest.main()
