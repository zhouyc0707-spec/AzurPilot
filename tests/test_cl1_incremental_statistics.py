"""独立临时总库对照旧读改写路径，验证战斗与行动力增量统计。"""

import copy
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from module.persistence.snapshots import HAZARD_FIELDS, MONTH_FIELDS
from module.statistics import cl1_database as database


NOW = datetime(2026, 10, 24, 14, 0)
MONTH = '2026-10'
MAX_INT = 2 ** 63 - 1
MUTATIONS = (
    ('increment_battle_count', (2,), {}),
    ('add_ap_snapshot', (90,), {'ap_total': 330, 'distance': 55}),
    ('increment_meow_battle_count', (5,), {}),
    ('add_meow_battle_time', (24.567, 5), {}),
    ('add_meow_round_time', (72.789, 5), {}),
)


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


class Cl1IncrementalStatisticsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='cl1-incremental-', ignore_cleanup_errors=True)
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'config' / 'azurpilot.db'
        self.db = database.Cl1Database(self.path)
        self.db.store.ensure_ready()
        self.db._coins_history_checked = set()
        self.db._coins_cleanup_checked = set()
        self.enterContext(patch.object(database, 'datetime', FixedDatetime))
        self.enterContext(patch.object(database, 'current_time', return_value=NOW))

    def seed(self, instance='test', *, mutate=None, month=MONTH):
        data = self.db._empty_data(month)
        bucket = {'battle_raw_count': 6, 'effective_rounds': 2.0, 'round_times': [60.0],
                  'battle_times': [20.0, 21.0], 'akashi_encounters': 2, 'akashi_ap': 40}
        data.update(
            battle_count=10, meow_battle_raw_count=10, meow_battle_count=3.5,
            coins_history_version=database.COINS_HISTORY_VERSION,
            coins_cleanup_version=database.COINS_CLEANUP_VERSION,
            ap_snapshots=[{'ts': '2026-10-23T10:00:00', 'ap_total': 300, 'ap': 100, 'source': 'cl1'}],
            yellow_coin_snapshots=[{'ts': '2026-10-23T10:00:00', 'yellow_coin': 500, 'source': 'cl1'}],
            coins_snapshots=[{'ts': '2026-10-23T10:00:00', 'yellow_coins': 500, 'purple_coins': 60}],
            meow_round_times=[{'duration': 60.0, 'hazard_level': 5}],
            meow_battle_times=[20.0, 21.0], meow_hazard_stats={'3': copy.deepcopy(bucket), '5': bucket},
            coins_month_start_residue=[{'ts': '2026-10-01T00:00:00', 'purple_coins': 70}],
            akashi_shop_champion={'monthly': {'ap': 240, 'screenshot': 'screenshots/record.png'}},
        )
        if mutate is not None:
            mutate(data)
        self.db.save_stats(instance, month, data)
        self.db._coins_history_checked.add(instance)
        self.db._coins_cleanup_checked.add(instance)
        return data

    def call(self, instance, mutation):
        name, args, kwargs = mutation
        return getattr(self.db, name)(instance, *args, **kwargs)

    def snapshot(self, tables=None):
        with closing(sqlite3.connect(self.path)) as conn:
            names = tables or [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            return {name: sorted(conn.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr) for name in names}

    def assert_stats_equal(self, left='fast', right='legacy', month=MONTH):
        # 特殊浮点也按原快照可序列化语义比较。
        self.assertEqual(json.dumps(self.db.get_stats(left, month), sort_keys=True, allow_nan=True),
                         json.dumps(self.db.get_stats(right, month), sort_keys=True, allow_nan=True))

    def compare(self, mutation, mutate=None, *, native=False):
        self.seed('fast', mutate=mutate)
        self.seed('legacy', mutate=mutate)
        if native:
            with patch.object(self.db, '_get_stats_in_connection', side_effect=AssertionError('展开了全月')), \
                    patch.object(self.db, '_save_stats_in_connection', side_effect=AssertionError('重写了全月')):
                actual = self.call('fast', mutation)
        else:
            actual = self.call('fast', mutation)
        with patch.object(self.db, '_native_month_for_update', return_value=None):
            expected = self.call('legacy', mutation)
        self.assertEqual(actual, expected)
        self.assert_stats_equal()

    def test_native_matches_unchanged_legacy_path(self):
        for mutation in MUTATIONS:
            with self.subTest(method=mutation[0]):
                self.compare(mutation, native=True)

    def test_unrelated_rows_and_compatibility_trees_keep_ids(self):
        tables = ['coin_snapshots', 'yellow_coin_snapshots', 'akashi_ap_purchases', 'commission_income',
                  'research_drops', 'cl1_compat_fields', 'typed_value_sets', 'typed_value_nodes']
        for mutation in MUTATIONS:
            with self.subTest(method=mutation[0]):
                self.seed()
                before = self.snapshot(tables)
                self.call('test', mutation)
                self.assertEqual(self.snapshot(tables), before)

    def test_existing_ap_and_sample_rows_remain_in_place(self):
        self.seed()
        tables = ['action_point_snapshots', 'meow_duration_samples']
        before = self.snapshot(tables)
        for mutation in MUTATIONS:
            self.call('test', mutation)
        after = self.snapshot(tables)
        for table in tables:
            self.assertTrue(all(row in after[table] for row in before[table]))

    def test_missing_fields_and_new_month_match_legacy_defaults(self):
        for mutation in MUTATIONS:
            with self.subTest(method=mutation[0]):
                self.compare(mutation, lambda data: data.clear(), native=True)
                self.db._coins_history_checked.update(('new-fast', 'new-legacy'))
                self.call('new-fast', mutation)
                with patch.object(self.db, '_native_month_for_update', return_value=None):
                    self.call('new-legacy', mutation)
                self.assert_stats_equal('new-fast', 'new-legacy')

    def test_ap_uses_last_ordinal_yellow_and_keeps_late_observation_month(self):
        def mutate(data):
            data['yellow_coin_snapshots'].append({'ts': '2026-10-01T01:00:00', 'yellow_coin': 0})
        self.compare(('add_ap_snapshot', (20,), {'ap_total': 100, 'observed_at': datetime(2026, 10, 1)}),
                     mutate, native=True)
        snapshot = self.db.get_stats('fast', MONTH)['ap_snapshots'][-1]
        self.assertEqual(snapshot['yellow_coin'], 0)
        self.assertEqual(snapshot['asset'], 5666.67)
        self.assertEqual(snapshot['ts'], '2026-10-01T00:00:00')
        self.db.add_ap_snapshot('fast', None, distance=100, observed_at=datetime(2026, 9, 30, 23, 59))
        self.assertEqual(self.db.get_stats('fast', '2026-09')['ap_snapshots'], [
            {'ts': '2026-09-30T23:59:00', 'source': 'cl1', 'distance': 100}])

    def test_distance_only_does_not_invent_total_or_assets_and_missing_total_is_noop(self):
        self.compare(('add_ap_snapshot', (999,), {'distance': '55'}), native=True)
        point = self.db.get_stats('fast', MONTH)['ap_snapshots'][-1]
        self.assertEqual(point, {'ts': NOW.isoformat(), 'source': 'cl1', 'distance': 55})
        before = self.snapshot()
        self.db.add_ap_snapshot('fast', 999)
        self.assertEqual(self.snapshot(), before)

    def test_meow_duration_keeps_latest_100_without_reinserting_survivors(self):
        def mutate(data):
            data['meow_round_times'] = [{'duration': float(n), 'hazard_level': 5} for n in range(103)]
            data['meow_battle_times'] = list(map(float, range(103)))
            for bucket in data['meow_hazard_stats'].values():
                bucket['round_times'] = list(map(float, range(103)))
                bucket['battle_times'] = list(map(float, range(103)))
        for mutation in MUTATIONS[3:]:
            with self.subTest(method=mutation[0]):
                self.compare(mutation, mutate, native=True)
                self.seed(mutate=mutate)
                before = self.snapshot(['meow_duration_samples'])['meow_duration_samples']
                self.call('test', mutation)
                after = self.snapshot(['meow_duration_samples'])['meow_duration_samples']
                self.assertGreater(len(set(before).intersection(after)), 400)
                field = 'round_times' if mutation[0] == 'add_meow_round_time' else 'battle_times'
                data = self.db.get_stats('test', MONTH)
                self.assertEqual(len(data['meow_' + field]), 100)
                self.assertEqual(len(data['meow_hazard_stats']['5'][field]), 100)
                self.assertEqual(len(data['meow_hazard_stats']['3'][field]), 103)

    def test_hazards_deltas_and_duration_inputs_match_old_path(self):
        for hazard in (None, 1, 2, 3, 4, 5, 6, '5', 5.0):
            for mutation in (
                ('increment_meow_battle_count', (hazard,), {}),
                ('increment_meow_battle_count', (hazard, '0.75'), {}),
                ('add_meow_round_time', (-0.12345, hazard), {}),
                ('add_meow_battle_time', (True, hazard), {}),
            ):
                with self.subTest(mutation=mutation):
                    self.compare(mutation)

    def test_legacy_normalization_unknown_fields_and_compatibility_values_fallback(self):
        variants = (
            lambda data: data.update(custom={'keep': [1, True, None]}),
            lambda data: data['ap_snapshots'][0].update(extra={'keep': 1}),
            lambda data: data.update(meow_round_times=[12.5, {'duration': 3}, 'invalid']),
            lambda data: data['meow_hazard_stats']['3'].update(battle_raw_count=-1, round_times=[{'duration': 8}]),
        )
        for mutation in MUTATIONS:
            for mutate in variants:
                with self.subTest(method=mutation[0], variant=variants.index(mutate)):
                    self.compare(mutation, mutate)

    def test_extreme_values_and_legacy_errors_match(self):
        cases = (
            (MUTATIONS[0], lambda data: data.update(battle_count=MAX_INT)),
            (('increment_battle_count', (True,), {}), None),
            (('increment_battle_count', (0.5,), {}), None),
            (MUTATIONS[2], lambda data: data.update(meow_battle_raw_count=MAX_INT)),
            (MUTATIONS[2], lambda data: data['meow_hazard_stats']['5'].update(battle_raw_count=MAX_INT)),
            (('increment_meow_battle_count', (5, float('inf')), {}), None),
            (('add_meow_battle_time', (float('inf'), 5), {}), None),
            (('add_ap_snapshot', (1,), {'ap_total': MAX_INT + 1}), None),
        )
        for mutation, mutate in cases:
            with self.subTest(mutation=mutation):
                self.compare(mutation, mutate)
        for mutation, field in ((MUTATIONS[0], 'battle_count'), (MUTATIONS[2], 'meow_battle_raw_count')):
            for instance in ('fast', 'legacy'):
                self.seed(instance, mutate=lambda data: data.update({field: None}))
            before = self.snapshot()
            with self.assertRaises(TypeError):
                self.call('fast', mutation)
            with patch.object(self.db, '_native_month_for_update', return_value=None), self.assertRaises(TypeError):
                self.call('legacy', mutation)
            self.assertEqual(self.snapshot(), before)

    def test_hidden_list_rows_do_not_become_visible(self):
        cases = (
            (MUTATIONS[1], 'ap_snapshots'),
            (MUTATIONS[1], 'yellow_coin_snapshots'),
            (MUTATIONS[3], 'meow_battle_times'),
            (MUTATIONS[4], 'meow_round_times'),
            (MUTATIONS[2], 'meow_hazard_stats'),
        )
        for mutation, field in cases:
            with self.subTest(field=field):
                self.seed('fast')
                self.seed('legacy')
                with self.db.store.transaction() as conn:
                    conn.execute('UPDATE cl1_months SET field_mask=field_mask & ? WHERE instance IN (?,?)',
                                 (~(1 << MONTH_FIELDS.index(field)), 'fast', 'legacy'))
                self.call('fast', mutation)
                with patch.object(self.db, '_native_month_for_update', return_value=None):
                    self.call('legacy', mutation)
                self.assert_stats_equal()

    def test_hidden_hazard_samples_are_not_resurrected(self):
        for instance in ('fast', 'legacy'):
            self.seed(instance)
        with self.db.store.transaction() as conn:
            mask = ~(1 << next(i for i, definition in enumerate(HAZARD_FIELDS) if definition[0] == 'round_times'))
            conn.execute('UPDATE meow_hazard_counters SET field_mask=field_mask & ? WHERE hazard_level=5', (mask,))
        self.call('fast', MUTATIONS[4])
        with patch.object(self.db, '_native_month_for_update', return_value=None):
            self.call('legacy', MUTATIONS[4])
        self.assert_stats_equal()

    def test_maximum_ordinals_fall_back_before_any_append(self):
        for mutation, table, condition in (
            (MUTATIONS[1], 'action_point_snapshots', ''),
            (MUTATIONS[3], 'meow_duration_samples', " AND kind='battle' AND bucket=0"),
            (MUTATIONS[4], 'meow_duration_samples', " AND kind='round' AND bucket=5"),
        ):
            with self.subTest(method=mutation[0], condition=condition):
                self.seed('fast')
                self.seed('legacy')
                with self.db.store.transaction() as conn:
                    conn.execute(f'UPDATE {table} SET ordinal=? WHERE ordinal=0' + condition, (MAX_INT,))
                self.call('fast', mutation)
                with patch.object(self.db, '_native_month_for_update', return_value=None):
                    self.call('legacy', mutation)
                self.assert_stats_equal()

    def test_corrupt_allowed_metadata_is_not_silently_bypassed(self):
        self.seed()
        with self.db.store.transaction() as conn:
            conn.execute('DELETE FROM typed_value_nodes WHERE parent_no IS NULL')
        before = self.snapshot()
        for mutation in MUTATIONS:
            with self.subTest(method=mutation[0]), self.assertRaises(ValueError):
                self.call('test', mutation)
            self.assertEqual(self.snapshot(), before)

    def test_parent_update_failure_rolls_back_prior_appends_and_bucket_writes(self):
        for mutation in MUTATIONS:
            with self.subTest(method=mutation[0]):
                self.seed()
                with self.db.store.transaction() as conn:
                    conn.execute("CREATE TRIGGER reject_cl1 BEFORE UPDATE ON cl1_months "
                                 "BEGIN SELECT RAISE(ABORT, 'reject-cl1'); END")
                before = self.snapshot()
                with self.assertRaisesRegex(sqlite3.IntegrityError, 'reject-cl1'):
                    self.call('test', mutation)
                self.assertEqual(self.snapshot(), before)
                with self.db.store.transaction() as conn:
                    conn.execute('DROP TRIGGER reject_cl1')

    def test_commit_failure_rolls_back_and_next_write_succeeds(self):
        with self.db.store.transaction() as conn:
            conn.execute('CREATE TABLE test_parent(id INTEGER PRIMARY KEY)')
            conn.execute('CREATE TABLE test_child(id REFERENCES test_parent(id) DEFERRABLE INITIALLY DEFERRED)')
        for mutation in MUTATIONS:
            with self.subTest(method=mutation[0]):
                self.seed()
                with self.db.store.transaction() as conn:
                    conn.execute('CREATE TRIGGER reject_commit AFTER UPDATE ON cl1_months '
                                 'BEGIN INSERT INTO test_child VALUES(1); END')
                before = self.snapshot()
                with self.assertRaises(sqlite3.IntegrityError):
                    self.call('test', mutation)
                self.assertEqual(self.snapshot(), before)
                with self.db.store.transaction() as conn:
                    conn.execute('DROP TRIGGER reject_commit')
                self.call('test', mutation)

    def test_concurrent_connections_keep_every_count_and_sample(self):
        self.seed()
        other = database.Cl1Database(self.path)
        other._coins_history_checked = {'test'}
        def write(index):
            writer = self.db if index % 2 else other
            writer.increment_battle_count('test')
            writer.increment_meow_battle_count('test', 5)
            writer.add_meow_battle_time('test', 20 + index, 5)
            writer.add_ap_snapshot('test', 90, ap_total=100 + index)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(write, range(12)))
        data = self.db.get_stats('test', MONTH)
        self.assertEqual(data['battle_count'], 22)
        self.assertEqual(data['meow_battle_raw_count'], 22)
        self.assertAlmostEqual(data['meow_battle_count'], 7.5)
        self.assertEqual(data['meow_hazard_stats']['5']['battle_raw_count'], 18)
        self.assertAlmostEqual(data['meow_hazard_stats']['5']['effective_rounds'], 6.0)
        self.assertEqual(len(data['meow_battle_times']), 14)
        self.assertEqual(len(data['ap_snapshots']), 13)

    def test_success_invalidates_read_cache(self):
        for mutation in MUTATIONS:
            with self.subTest(method=mutation[0]):
                self.seed()
                self.db.enable_read_cache()
                self.db.get_stats('test', MONTH)
                self.assertTrue(self.db._read_cache)
                self.call('test', mutation)
                self.assertFalse(self.db._read_cache)
                self.db.disable_read_cache()


if __name__ == '__main__':
    unittest.main()
