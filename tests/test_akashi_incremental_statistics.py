"""用独立临时总库验证明石增量写入、历史兼容与事务回滚。"""

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from module.statistics import cl1_database as database


NOW = datetime(2026, 10, 24, 9, 30)
MONTH = '2026-10'
MAX_SQLITE_INT = 2 ** 63 - 1
MUTATIONS = (
    ('increment_akashi_encounter', ()),
    ('add_akashi_ap_entry', (30, 10, 3, 'cl1')),
    ('increment_meow_akashi_encounter', (5,)),
    ('add_meow_akashi_ap', (5, 30)),
)


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


class AkashiIncrementalStatisticsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='akashi-incremental-', ignore_cleanup_errors=True)
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'config' / 'azurpilot.db'
        self.db = database.Cl1Database(self.path)
        self.db.store.ensure_ready()
        self.enterContext(patch.object(database, 'datetime', FixedDatetime))
        self.enterContext(patch.object(database, 'current_time', return_value=NOW))

    @staticmethod
    def bucket(encounters=2, ap=40):
        return {'battle_raw_count': 6, 'effective_rounds': 2.0, 'round_times': [60.0],
                'battle_times': [20.0, 21.0], 'akashi_encounters': encounters, 'akashi_ap': ap}

    def seed(self, instance='test', *, month=MONTH, mutate=None):
        data = self.db._empty_data(month)
        data.update(
            battle_count=10, akashi_encounters=4, akashi_ap=50,
            akashi_ap_entries=[
                {'ts': '2026-10-23T10:00:00', 'amount': 20, 'base': 10, 'count': 2, 'source': 'cl1'},
                {'ts': '2026-10-22T10:00:00', 'amount': 30, 'base': 30, 'count': 1, 'source': 'meow'},
            ],
            ap_snapshots=[{'ts': '2026-10-23T10:00:00', 'ap': 100, 'source': 'cl1', 'distance': 99}],
            coins_snapshots=[{'ts': '2026-10-23T10:00:00', 'yellow_coins': 500, 'purple_coins': 60}],
            meow_round_times=[{'duration': 60.0, 'hazard_level': 5}],
            meow_battle_times=[20.0, 21.0],
            meow_hazard_stats={'3': self.bucket(1, 20), '5': self.bucket()},
            coins_month_start_residue=[{'ts': '2026-10-01T00:00:00', 'purple_coins': 70}],
            akashi_shop_champion={'monthly': {'ap': 240, 'screenshot': 'screenshots/record.png'}},
        )
        if mutate is not None:
            mutate(data)
        self.db.save_stats(instance, month, data)
        return data

    def sql_snapshot(self, tables=None):
        with closing(sqlite3.connect(self.path)) as conn:
            names = tables or [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
            )]
            return {name: sorted(conn.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr) for name in names}

    def call(self, instance, method, args):
        return getattr(self.db, method)(instance, *args)

    def test_native_results_equal_original_read_modify_save_path(self):
        for method, args in MUTATIONS:
            with self.subTest(method=method):
                self.seed('fast')
                self.seed('legacy')
                with patch.object(self.db, '_get_stats_in_connection', side_effect=AssertionError('不应展开整月')), \
                        patch.object(self.db, '_save_stats_in_connection', side_effect=AssertionError('不应重写整月')):
                    actual = self.call('fast', method, args)
                with patch.object(self.db, '_native_month_for_update', return_value=None):
                    expected = self.call('legacy', method, args)
                self.assertEqual(actual, expected)
                self.assertEqual(self.db.get_stats('fast', MONTH), self.db.get_stats('legacy', MONTH))

    def test_native_writes_preserve_unrelated_history_ids_and_compatibility_trees(self):
        unrelated = ['action_point_snapshots', 'coin_snapshots', 'meow_duration_samples',
                     'cl1_compat_fields', 'typed_value_sets', 'typed_value_nodes']
        for method, args in MUTATIONS:
            with self.subTest(method=method):
                original = self.seed()
                before = self.sql_snapshot(unrelated)
                self.call('test', method, args)
                self.assertEqual(self.sql_snapshot(unrelated), before)
                data = self.db.get_stats('test', MONTH)
                self.assertEqual(data['coins_month_start_residue'], original['coins_month_start_residue'])
                self.assertEqual(data['akashi_shop_champion'], original['akashi_shop_champion'])

    def test_success_invalidates_existing_read_cache(self):
        for method, args in MUTATIONS:
            with self.subTest(method=method):
                self.seed()
                self.db.enable_read_cache()
                before = self.db.get_stats('test', MONTH)
                self.assertTrue(self.db._read_cache)
                self.call('test', method, args)
                self.assertFalse(self.db._read_cache)
                after = self.db.get_stats('test', MONTH)
                self.assertIsNot(after, before)
                self.assertNotEqual(after, before)
                self.db.disable_read_cache()

    def test_first_event_in_new_month_matches_legacy_defaults(self):
        for index, (method, args) in enumerate(MUTATIONS):
            with self.subTest(method=method):
                fast, legacy = f'new-fast-{index}', f'new-legacy-{index}'
                self.seed(fast, month='2026-09')
                previous = self.db.get_stats(fast, '2026-09')
                actual = self.call(fast, method, args)
                with patch.object(self.db, '_native_month_for_update', return_value=None):
                    expected = self.call(legacy, method, args)
                self.assertEqual(actual, expected)
                self.assertEqual(self.db.get_stats(fast, MONTH), self.db.get_stats(legacy, MONTH))
                self.assertEqual(self.db.get_stats(fast, '2026-09'), previous)

    def test_purchase_append_keeps_existing_ids_and_original_ordinal_order(self):
        original = self.seed()
        before = self.sql_snapshot(['akashi_ap_purchases'])['akashi_ap_purchases']
        self.db.add_akashi_ap_entry('test', 10, 10, 1, 'cl1')
        self.db.add_akashi_ap_entry('test', 40, 20, 2, 'meow')
        data = self.db.get_stats('test', MONTH)
        self.assertEqual(data['akashi_ap_entries'][:2], original['akashi_ap_entries'])
        self.assertEqual(data['akashi_ap_entries'][-2:], [
            {'ts': NOW.isoformat(), 'amount': 10, 'base': 10, 'count': 1, 'source': 'cl1'},
            {'ts': NOW.isoformat(), 'amount': 40, 'base': 20, 'count': 2, 'source': 'meow'},
        ])
        self.assertEqual(data['akashi_ap'], 100)
        after = self.sql_snapshot(['akashi_ap_purchases'])['akashi_ap_purchases']
        self.assertTrue(all(row in after for row in before), '旧购买行必须原地保留，不能删除重插')
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual([row[0] for row in conn.execute(
                'SELECT ordinal FROM akashi_ap_purchases WHERE instance=? AND month=? ORDER BY ordinal',
                ('test', MONTH),
            )], [0, 1, 2, 3])

    def test_meow_updates_and_new_bucket_remain_separate_from_cl1_and_other_hazards(self):
        original = self.seed()
        self.assertEqual(self.db.increment_meow_akashi_encounter('test', 5), 3)
        self.db.add_meow_akashi_ap('test', 5, 30)
        self.assertEqual(self.db.increment_meow_akashi_encounter('test', 6), 1)
        self.db.add_meow_akashi_ap('test', 6, 20)
        data = self.db.get_stats('test', MONTH)
        self.assertEqual(data['akashi_encounters'], original['akashi_encounters'])
        self.assertEqual(data['akashi_ap'], original['akashi_ap'])
        self.assertEqual(data['meow_hazard_stats']['3'], original['meow_hazard_stats']['3'])
        self.assertEqual(data['meow_hazard_stats']['5'], {**original['meow_hazard_stats']['5'],
                                                       'akashi_encounters': 3, 'akashi_ap': 70})
        self.assertEqual(data['meow_hazard_stats']['6'], {
            'battle_raw_count': 0, 'effective_rounds': 0.0, 'round_times': [], 'battle_times': [],
            'akashi_encounters': 1, 'akashi_ap': 20,
        })

    def test_concurrent_connections_keep_all_counts_amounts_and_purchase_rows(self):
        self.db.save_stats('test', MONTH, self.db._empty_data(MONTH))
        other = database.Cl1Database(self.path)

        def write(index):
            writer = self.db if index % 2 else other
            encounter = writer.increment_akashi_encounter('test')
            meow = writer.increment_meow_akashi_encounter('test', 5)
            writer.add_akashi_ap_entry('test', 20, 10, 2, 'cl1')
            writer.add_meow_akashi_ap('test', 5, 20)
            return encounter, meow

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(write, range(18)))
        data = self.db.get_stats('test', MONTH)
        self.assertEqual(sorted(value[0] for value in results), list(range(1, 19)))
        self.assertEqual(sorted(value[1] for value in results), list(range(1, 19)))
        self.assertEqual(data['akashi_encounters'], 18)
        self.assertEqual(data['akashi_ap'], 360)
        self.assertEqual(len(data['akashi_ap_entries']), 18)
        self.assertEqual(data['meow_hazard_stats']['5']['akashi_encounters'], 18)
        self.assertEqual(data['meow_hazard_stats']['5']['akashi_ap'], 360)

    def test_insert_or_update_failure_rolls_back_every_native_mutation(self):
        for index, (method, args) in enumerate(MUTATIONS):
            with self.subTest(method=method):
                self.seed()
                table = ('akashi_ap_purchases' if method == 'add_akashi_ap_entry'
                         else 'meow_hazard_counters' if 'meow' in method else 'cl1_months')
                with closing(sqlite3.connect(self.path)) as conn, conn:
                    for event in ('INSERT', 'UPDATE'):
                        conn.execute(f"CREATE TRIGGER reject_write_{index}_{event} BEFORE {event} ON {table} "
                                     "BEGIN SELECT RAISE(ABORT, 'write rejected'); END")
                before = self.sql_snapshot()
                with self.assertRaisesRegex(sqlite3.IntegrityError, 'write rejected'):
                    self.call('test', method, args)
                self.assertEqual(self.sql_snapshot(), before)
                with closing(sqlite3.connect(self.path)) as conn, conn:
                    for event in ('INSERT', 'UPDATE'):
                        conn.execute(f'DROP TRIGGER reject_write_{index}_{event}')

    def test_commit_failure_rolls_back_all_native_changes_and_releases_lock(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute('CREATE TABLE test_parent(id INTEGER PRIMARY KEY)')
            conn.execute('CREATE TABLE test_child(id REFERENCES test_parent(id) DEFERRABLE INITIALLY DEFERRED)')
        for index, (method, args) in enumerate(MUTATIONS):
            with self.subTest(method=method):
                self.seed()
                table = 'meow_hazard_counters' if 'meow' in method else 'cl1_months'
                with closing(sqlite3.connect(self.path)) as conn, conn:
                    for event in ('INSERT', 'UPDATE'):
                        conn.execute(f'CREATE TRIGGER reject_commit_{index}_{event} AFTER {event} ON {table} '
                                     'BEGIN INSERT INTO test_child VALUES (1); END')
                before = self.sql_snapshot()
                with self.assertRaises(sqlite3.IntegrityError):
                    self.call('test', method, args)
                self.assertEqual(self.sql_snapshot(), before)
                with closing(sqlite3.connect(self.path)) as conn, conn:
                    for event in ('INSERT', 'UPDATE'):
                        conn.execute(f'DROP TRIGGER reject_commit_{index}_{event}')
                # 提交失败必须放开写锁，下一次有效事件能够正常提交。
                self.assertEqual(self.db.increment_akashi_encounter('test'), 5)

    def test_complex_compatibility_history_uses_original_path_without_data_loss(self):
        variants = (
            lambda data: data.update(custom_history={'keep': [1, None, 'text']}),
            lambda data: data['ap_snapshots'][0].update(legacy_extra={'keep': True}),
            lambda data: data['akashi_ap_entries'][0].update(legacy_extra=['keep']),
            lambda data: data['meow_hazard_stats']['5'].update(akashi_encounters='4'),
        )
        for variant in variants:
            for method, args in MUTATIONS:
                with self.subTest(variant=variants.index(variant), method=method):
                    self.seed('fast', mutate=variant)
                    self.seed('legacy', mutate=variant)
                    with patch.object(self.db, '_get_stats_in_connection', wraps=self.db._get_stats_in_connection) as read, \
                            patch.object(self.db, '_save_stats_in_connection', wraps=self.db._save_stats_in_connection) as save:
                        actual = self.call('fast', method, args)
                        self.assertTrue(read.called)
                        self.assertTrue(save.called)
                    with patch.object(self.db, '_native_month_for_update', return_value=None):
                        expected = self.call('legacy', method, args)
                    self.assertEqual(actual, expected)
                    self.assertEqual(self.db.get_stats('fast', MONTH), self.db.get_stats('legacy', MONTH))

    def test_allowed_metadata_tree_is_validated_before_counter_update(self):
        self.seed()
        with closing(sqlite3.connect(self.path)) as conn, conn:
            value_id = conn.execute("SELECT value_set_id FROM cl1_compat_fields WHERE name='akashi_shop_champion'").fetchone()[0]
            conn.execute('DELETE FROM typed_value_nodes WHERE value_set_id=? AND parent_no IS NULL', (value_id,))
        before = self.sql_snapshot()
        for method, args in MUTATIONS:
            with self.subTest(method=method), self.assertRaises(ValueError):
                self.call('test', method, args)
            self.assertEqual(self.sql_snapshot(), before)

    def test_integer_overflow_matches_legacy_compatibility_representation(self):
        variants = (
            ('increment_akashi_encounter', (), lambda data: data.update(akashi_encounters=MAX_SQLITE_INT)),
            ('add_akashi_ap_entry', (1, 1, 1, 'cl1'), lambda data: data.update(akashi_ap=MAX_SQLITE_INT)),
            ('increment_meow_akashi_encounter', (5,),
             lambda data: data['meow_hazard_stats']['5'].update(akashi_encounters=MAX_SQLITE_INT)),
            ('add_meow_akashi_ap', (5, 1), lambda data: data['meow_hazard_stats']['5'].update(akashi_ap=MAX_SQLITE_INT)),
        )
        for method, args, variant in variants:
            with self.subTest(method=method):
                self.seed('fast', mutate=variant)
                self.seed('legacy', mutate=variant)
                actual = self.call('fast', method, args)
                with patch.object(self.db, '_native_month_for_update', return_value=None):
                    expected = self.call('legacy', method, args)
                self.assertEqual(actual, expected)
                self.assertEqual(self.db.get_stats('fast', MONTH), self.db.get_stats('legacy', MONTH))

    def test_invalid_meow_inputs_do_not_create_month_or_change_history(self):
        self.seed()
        before = self.sql_snapshot()
        for hazard in (None, 1, 7, '5'):
            self.assertIsNone(self.db.increment_meow_akashi_encounter('test', hazard))
            self.assertIsNone(self.db.add_meow_akashi_ap('test', hazard, 20))
        for amount in (None, 'invalid', 0, -1):
            self.assertIsNone(self.db.add_meow_akashi_ap('test', 5, amount))
        self.assertEqual(self.sql_snapshot(), before)


if __name__ == '__main__':
    unittest.main()
