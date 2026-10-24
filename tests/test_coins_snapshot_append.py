"""临时总库验证凭证原生追加、兼容回退和历史完整性。"""
import copy
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from module.persistence.database import BusinessDatabase, VERSION, create_schema
from module.statistics import cl1_database as database


NOW = datetime(2026, 10, 13, 1, 0)
MONTH = '2026-10'
INSTANCE = 'coin_append_probe'


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


class TestCoinSnapshotAppend(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'config' / 'azurpilot.db'
        self.path.parent.mkdir()
        with closing(sqlite3.connect(self.path)) as conn, conn:
            create_schema(conn)
            conn.execute('INSERT INTO storage_migrations VALUES(?,?,?)',
                         (VERSION, NOW.isoformat(), 'isolated-append-fixture'))
        self.path.with_suffix('.migrated').write_text(f'{VERSION}\nisolated-append-fixture\n', encoding='utf-8')
        store = BusinessDatabase(self.path.parent)
        self.db = database.Cl1Database(store=store)
        self.other = database.Cl1Database(store=store)
        clock = patch.object(database, 'datetime', FixedDatetime)
        clock.start()
        self.addCleanup(clock.stop)
        self.seed = {
            'battle_count': 321,
            'coins_history_version': database.COINS_HISTORY_VERSION,
            'coins_snapshots': [self.coin(1000, 10)],
            'yellow_coin_snapshots': [self.yellow(1000)],
            'meow_round_times': [{'duration': 15.5, 'hazard_level': 5}],
            'commission_income_entries': [{
                'ts': '2026-10-12T15:00:00', 'commission_count': 1,
                'items': {'Cube': 2}, 'screenshots': ['isolated-fixture.png'],
            }],
        }
        self.db.save_stats(INSTANCE, MONTH, copy.deepcopy(self.seed))
        self.db._coins_history_checked.add(INSTANCE)
        self.other._coins_history_checked.add(INSTANCE)

    @staticmethod
    def coin(yellow, purple=10, stamp='2026-10-12T12:00:00', source='original'):
        result = {'ts': stamp, 'yellow_coins': yellow, 'source': source}
        if purple is not None:
            result['purple_coins'] = purple
        return result

    @staticmethod
    def yellow(value, stamp='2026-10-12T12:00:00', source='original'):
        return {'ts': stamp, 'yellow_coin': value, 'source': source}

    def rows(self, table):
        with self.db.store.transaction(write=False) as conn:
            return [tuple(row) for row in conn.execute(
                f'SELECT * FROM {table} WHERE instance=? AND month=? ORDER BY id',
                (INSTANCE, MONTH),
            )]

    def no_month_rewrite(self):
        """正常路径不得读取全月，也不得删除重建其他统计族。"""
        return patch.multiple(
            self.db,
            _get_stats_in_connection=unittest.mock.Mock(side_effect=AssertionError('读取了全月')),
            _save_stats_in_connection=unittest.mock.Mock(side_effect=AssertionError('改写了全月')),
        )

    def test_coin_append_preserves_original_ids_other_metrics_and_source(self):
        originals = self.rows('coin_snapshots')
        commissions = self.rows('commission_income')
        with self.no_month_rewrite():
            self.db.add_coins_snapshot(INSTANCE, 900, 11, source='akashi_probe')
        self.assertEqual(originals, self.rows('coin_snapshots')[:1])
        self.assertEqual(commissions, self.rows('commission_income'))
        stats = self.db.get_stats(INSTANCE, MONTH)
        self.assertEqual(stats['coins_snapshots'], [
            self.seed['coins_snapshots'][0], self.coin(900, 11, NOW.isoformat(), 'akashi_probe'),
        ])
        self.assertEqual(stats['meow_round_times'], self.seed['meow_round_times'])
        self.assertEqual(stats['battle_count'], 321)

    def test_background_purple_carry_keeps_zero_and_native_append(self):
        self.seed['coins_snapshots'] = [self.coin(1000, 12), self.coin(950, 0)]
        self.db.save_stats(INSTANCE, MONTH, self.seed)
        originals = self.rows('coin_snapshots')
        with self.no_month_rewrite():
            self.db.add_coins_snapshot(INSTANCE, 900, carry_purple=True, source='hazard1')
        self.assertEqual(originals, self.rows('coin_snapshots')[:2])
        point = self.db.get_stats(INSTANCE, MONTH)['coins_snapshots'][-1]
        self.assertEqual(point['purple_coins'], 0)
        self.assertEqual(point['source'], 'hazard1')

    def test_purple_carry_uses_previous_month_when_current_has_no_known_purple(self):
        self.db.save_stats(INSTANCE, '2026-09', {'coins_snapshots': [
            self.coin(2000, 13, '2026-09-30T23:59:00'),
        ]})
        self.seed['coins_snapshots'] = [self.coin(1000, None)]
        self.db.save_stats(INSTANCE, MONTH, self.seed)
        self.db.add_coins_snapshot(INSTANCE, 900, carry_purple=True)
        self.assertEqual(self.db.get_stats(INSTANCE, MONTH)['coins_snapshots'][-1]['purple_coins'], 13)

    def test_observation_timestamp_selects_original_month_and_keeps_all_points(self):
        stamp = datetime(2026, 9, 30, 23, 59, 59)
        self.db.add_coins_snapshot(INSTANCE, 500, 7, observed_at=stamp)
        self.db.add_yellow_coin_snapshot(INSTANCE, 501, observed_at=stamp)
        previous = self.db.get_stats(INSTANCE, '2026-09')
        self.assertEqual(previous['coins_snapshots'][-1]['ts'], stamp.isoformat())
        self.assertEqual(previous['yellow_coin_snapshots'][-1]['ts'], stamp.isoformat())
        self.assertEqual(self.db.get_stats(INSTANCE, MONTH)['coins_snapshots'], self.seed['coins_snapshots'])

    def test_purple_carry_preserves_compatibility_fields_and_rejects_corruption(self):
        self.seed['private_extension'] = {'nested': [1, True, None]}
        self.db.save_stats(INSTANCE, MONTH, self.seed)
        self.db.add_coins_snapshot(INSTANCE, 900, carry_purple=True)
        self.assertEqual(self.db.get_stats(INSTANCE, MONTH)['private_extension'], self.seed['private_extension'])
        with self.db.store.transaction() as conn:
            conn.execute('DELETE FROM typed_value_nodes WHERE parent_no IS NULL')
        before = self.rows('coin_snapshots')
        with self.assertRaises(ValueError):
            self.db.add_coins_snapshot(INSTANCE, 800, carry_purple=True)
        self.assertEqual(before, self.rows('coin_snapshots'))

    def test_yellow_append_preserves_original_ids_and_source(self):
        originals = self.rows('yellow_coin_snapshots')
        with self.no_month_rewrite():
            self.db.add_yellow_coin_snapshot(INSTANCE, 950, source='dashboard')
        self.assertEqual(originals, self.rows('yellow_coin_snapshots')[:1])
        self.assertEqual(self.db.get_stats(INSTANCE, MONTH)['yellow_coin_snapshots'], [
            self.seed['yellow_coin_snapshots'][0], self.yellow(950, NOW.isoformat(), 'dashboard'),
        ])

    def test_unchanged_values_do_not_append_or_rewrite(self):
        coins, yellow = self.rows('coin_snapshots'), self.rows('yellow_coin_snapshots')
        with self.no_month_rewrite():
            self.db.add_coins_snapshot(INSTANCE, 1000, 10, source='different')
            self.db.add_yellow_coin_snapshot(INSTANCE, 1000, source='different')
        self.assertEqual(coins, self.rows('coin_snapshots'))
        self.assertEqual(yellow, self.rows('yellow_coin_snapshots'))

    def test_missing_purple_is_distinct_from_explicit_zero(self):
        self.seed['coins_snapshots'] = [self.coin(1000, None)]
        self.db.save_stats(INSTANCE, MONTH, self.seed)
        with self.no_month_rewrite():
            self.db.add_coins_snapshot(INSTANCE, 1000, None)
            self.db.add_coins_snapshot(INSTANCE, 1000, 0)
            self.db.add_coins_snapshot(INSTANCE, 1000, 0)
            self.db.add_coins_snapshot(INSTANCE, 1000, None)
            self.db.add_coins_snapshot(INSTANCE, 1000, None)
        points = self.db.get_stats(INSTANCE, MONTH)['coins_snapshots']
        self.assertEqual(len(points), 3)
        self.assertNotIn('purple_coins', points[0])
        self.assertEqual(points[1]['purple_coins'], 0)
        self.assertNotIn('purple_coins', points[2])

    def test_first_yellow_family_sets_month_presence_without_other_defaults(self):
        self.db.save_stats(INSTANCE, MONTH, {'battle_count': 321})
        with self.no_month_rewrite():
            self.db.add_yellow_coin_snapshot(INSTANCE, 1000)
        self.assertEqual(self.db.get_stats(INSTANCE, MONTH), {
            'battle_count': 321,
            'yellow_coin_snapshots': [self.yellow(1000, NOW.isoformat(), 'dashboard')],
        })

    def test_fast_append_invalidates_read_cache(self):
        self.db.enable_read_cache()
        self.db.get_stats(INSTANCE, MONTH)
        self.assertTrue(self.db._read_cache)
        self.db.add_coins_snapshot(INSTANCE, 900, 11)
        self.assertEqual(self.db._read_cache, {})
        self.assertEqual(self.db.get_stats(INSTANCE, MONTH)['coins_snapshots'][-1]['yellow_coins'], 900)

    def test_old_same_hour_points_are_all_kept(self):
        originals = [self.coin(1000 + n, 10, f'2026-10-01T12:{n:02d}:00') for n in range(50)]
        self.seed['coins_snapshots'] = originals
        self.db.save_stats(INSTANCE, MONTH, self.seed)
        with self.no_month_rewrite():
            self.db.add_coins_snapshot(INSTANCE, 850, 11)
        points = self.db.get_stats(INSTANCE, MONTH)['coins_snapshots']
        self.assertEqual(points[:-1], originals)
        self.assertEqual(len(points), 51)

    def test_custom_compatibility_data_uses_original_path_and_preserves_it(self):
        self.seed['private_extension'] = {'nested': [1, True, None]}
        self.db.save_stats(INSTANCE, MONTH, self.seed)
        with patch.object(self.db, '_get_stats_in_connection', wraps=self.db._get_stats_in_connection) as read:
            self.db.add_coins_snapshot(INSTANCE, 900, 11)
            self.assertTrue(read.called)
        self.assertEqual(self.db.get_stats(INSTANCE, MONTH)['private_extension'], self.seed['private_extension'])

    def test_valid_month_start_residue_keeps_fast_path_and_original_tree(self):
        residue = [self.coin(1200, 12, '2026-10-01T00:00:00', 'residue')]
        self.seed['coins_month_start_residue'] = residue
        self.db.save_stats(INSTANCE, MONTH, self.seed)
        with self.db.store.transaction(write=False) as conn:
            original = [tuple(row) for row in conn.execute('SELECT * FROM typed_value_nodes ORDER BY value_set_id,node_no')]
        with self.no_month_rewrite():
            self.db.add_coins_snapshot(INSTANCE, 900, 11)
            self.db.add_yellow_coin_snapshot(INSTANCE, 900)
        with self.db.store.transaction(write=False) as conn:
            current = [tuple(row) for row in conn.execute('SELECT * FROM typed_value_nodes ORDER BY value_set_id,node_no')]
        self.assertEqual(original, current)
        self.assertEqual(self.db.get_stats(INSTANCE, MONTH)['coins_month_start_residue'], residue)

    @staticmethod
    def champion():
        return {'cn': {'server': 'cn', 'month': MONTH, 'totalAp': 220,
                       'boxes': {'20': 1, '50': 0, '100': 2},
                       'time': '2026-10-12T12:00:00+08:00', 'screenshot': 'isolated-record.png'}}

    def compatibility_rows(self):
        with self.db.store.transaction(write=False) as conn:
            return tuple(tuple(tuple(row) for row in conn.execute(query)) for query in (
                'SELECT * FROM typed_value_sets ORDER BY id',
                'SELECT * FROM typed_value_nodes ORDER BY value_set_id,node_no',
                'SELECT * FROM cl1_compat_fields ORDER BY instance,month,name',
            ))

    def test_champion_alone_or_with_residue_keeps_native_append_and_original_trees(self):
        for with_residue in (False, True):
            with self.subTest(with_residue=with_residue):
                seed = copy.deepcopy(self.seed)
                seed['akashi_shop_champion'] = self.champion()
                if with_residue:
                    seed['coins_month_start_residue'] = [self.coin(1200, 12)]
                self.db.save_stats(INSTANCE, MONTH, seed)
                original_trees = self.compatibility_rows()
                original_coins = self.rows('coin_snapshots')
                original_commissions = self.rows('commission_income')

                with self.no_month_rewrite():
                    self.db.add_coins_snapshot(INSTANCE, 900, 11)
                    self.db.add_yellow_coin_snapshot(INSTANCE, 900)

                self.assertEqual(original_trees, self.compatibility_rows())
                self.assertEqual(original_coins, self.rows('coin_snapshots')[:1])
                self.assertEqual(original_commissions, self.rows('commission_income'))
                current = self.db.get_stats(INSTANCE, MONTH)
                self.assertEqual(current['akashi_shop_champion'], seed['akashi_shop_champion'])
                self.assertEqual(current.get('coins_month_start_residue'), seed.get('coins_month_start_residue'))
                self.assertEqual([point['yellow_coins'] for point in current['coins_snapshots']], [1000, 900])

    def test_each_independent_tree_is_validated_before_native_append(self):
        for damaged in ('akashi_shop_champion', 'coins_month_start_residue'):
            with self.subTest(damaged=damaged):
                seed = copy.deepcopy(self.seed)
                seed['akashi_shop_champion'] = self.champion()
                seed['coins_month_start_residue'] = [self.coin(1200, 12)]
                self.db.save_stats(INSTANCE, MONTH, seed)
                with self.db.store.transaction() as conn:
                    value_id = conn.execute('SELECT value_set_id FROM cl1_compat_fields WHERE name=?',
                                            (damaged,)).fetchone()[0]
                    conn.execute('DELETE FROM typed_value_nodes WHERE value_set_id=? AND parent_no IS NULL',
                                 (value_id,))
                before = self.rows('coin_snapshots'), self.rows('yellow_coin_snapshots')
                for method, args in ((self.db.add_coins_snapshot, (900, 11)),
                                     (self.db.add_yellow_coin_snapshot, (900,))):
                    with self.no_month_rewrite(), self.assertRaises(ValueError):
                        method(INSTANCE, *args)
                    self.assertEqual(before, (self.rows('coin_snapshots'), self.rows('yellow_coin_snapshots')))

    def test_champion_does_not_allow_other_compatibility_fields_into_fast_path(self):
        self.seed['akashi_shop_champion'] = self.champion()
        self.seed['private_extension'] = {'nested': [1, True, None]}
        self.db.save_stats(INSTANCE, MONTH, self.seed)
        with patch.object(self.db, '_get_stats_in_connection', wraps=self.db._get_stats_in_connection) as read:
            self.db.add_coins_snapshot(INSTANCE, 900, 11)
            self.assertTrue(read.called)
        current = self.db.get_stats(INSTANCE, MONTH)
        self.assertEqual(current['akashi_shop_champion'], self.seed['akashi_shop_champion'])
        self.assertEqual(current['private_extension'], self.seed['private_extension'])

    def test_champion_extra_mode_or_unreferenced_tree_rejects_native_guard(self):
        from module.persistence.values import write_value

        for case in ('extra_mode', 'unreferenced_tree'):
            with self.subTest(case=case):
                seed = copy.deepcopy(self.seed)
                seed['akashi_shop_champion'] = self.champion()
                self.db.save_stats(INSTANCE, MONTH, seed)
                before = self.rows('coin_snapshots')
                with self.db.store.transaction() as conn:
                    if case == 'extra_mode':
                        conn.execute("UPDATE cl1_compat_fields SET mode='extra' WHERE name='akashi_shop_champion'")
                    else:
                        write_value(conn, instance=INSTANCE, month=MONTH, value={'unreferenced': 1})
                    self.assertIsNone(self.db._native_month_for_update(conn, INSTANCE, MONTH))
                    self.assertFalse(self.db._try_append_coin_snapshot(
                        conn, INSTANCE, MONTH, 'coins_snapshots', self.coin(900, 11, NOW.isoformat()),
                    ))
                self.assertEqual(before, self.rows('coin_snapshots'))

    def test_corrupt_month_start_residue_raises_without_modifying_records(self):
        self.seed['coins_month_start_residue'] = [self.coin(1200, 12)]
        self.db.save_stats(INSTANCE, MONTH, self.seed)
        with self.db.store.transaction() as conn:
            conn.execute('DELETE FROM typed_value_nodes WHERE parent_no IS NULL')
        before = self.rows('coin_snapshots'), self.rows('yellow_coin_snapshots')
        for method, args in ((self.db.add_coins_snapshot, (900, 11)),
                             (self.db.add_yellow_coin_snapshot, (900,))):
            with self.subTest(method=method.__name__), self.assertRaises(ValueError):
                method(INSTANCE, *args)
            self.assertEqual(before, (self.rows('coin_snapshots'), self.rows('yellow_coin_snapshots')))

    def test_another_compat_field_sharing_residue_tree_uses_original_path(self):
        residue = [self.coin(1200, 12, '2026-10-01T00:00:00', 'residue')]
        self.seed['coins_month_start_residue'] = residue
        self.db.save_stats(INSTANCE, MONTH, self.seed)
        with self.db.store.transaction() as conn:
            value_id = conn.execute(
                "SELECT value_set_id FROM cl1_compat_fields WHERE name='coins_month_start_residue'",
            ).fetchone()[0]
            conn.execute('INSERT INTO cl1_compat_fields VALUES(?,?,?,?,?)',
                         (INSTANCE, MONTH, 'shared_private_extension', 'value', value_id))
            self.assertFalse(self.db._try_append_coin_snapshot(
                conn, INSTANCE, MONTH, 'coins_snapshots', self.coin(900, 11, NOW.isoformat()),
            ))
        with patch.object(self.db, '_get_stats_in_connection', wraps=self.db._get_stats_in_connection) as read:
            self.db.add_coins_snapshot(INSTANCE, 900, 11)
            self.assertTrue(read.called)
        stats = self.db.get_stats(INSTANCE, MONTH)
        self.assertEqual(stats['coins_month_start_residue'], residue)
        self.assertEqual(stats['shared_private_extension'], residue)
        self.assertEqual([point['yellow_coins'] for point in stats['coins_snapshots']], [1000, 900])

    def test_corrupt_compatibility_tree_raises_without_modifying_records(self):
        self.seed['private_extension'] = {'nested': [1, 2]}
        self.db.save_stats(INSTANCE, MONTH, self.seed)
        with self.db.store.transaction() as conn:
            conn.execute('DELETE FROM typed_value_nodes WHERE parent_no IS NULL')
        before = self.rows('coin_snapshots'), self.rows('yellow_coin_snapshots')
        for method, args in ((self.db.add_coins_snapshot, (900, 11)),
                             (self.db.add_yellow_coin_snapshot, (900,))):
            with self.subTest(method=method.__name__), self.assertRaises(ValueError):
                method(INSTANCE, *args)
            self.assertEqual(before, (self.rows('coin_snapshots'), self.rows('yellow_coin_snapshots')))

    def test_clock_rollback_uses_sorting_fallback_without_losing_points(self):
        self.seed['coins_snapshots'] = [self.coin(1000, 10, '2026-10-14T12:00:00')]
        self.db.save_stats(INSTANCE, MONTH, self.seed)
        with patch.object(self.db, '_get_stats_in_connection', wraps=self.db._get_stats_in_connection) as read:
            self.db.add_coins_snapshot(INSTANCE, 900, 11)
            self.assertTrue(read.called)
        points = self.db.get_stats(INSTANCE, MONTH)['coins_snapshots']
        self.assertEqual([point['yellow_coins'] for point in points], [900, 1000])

    def test_preexisting_out_of_order_points_use_sorting_fallback(self):
        self.seed['coins_snapshots'] = [self.coin(1000, 10, '2026-10-12T15:00:00'),
                                      self.coin(1100, 10, '2026-10-12T12:00:00')]
        self.db.save_stats(INSTANCE, MONTH, self.seed)
        self.db.add_coins_snapshot(INSTANCE, 900, 11)
        self.assertEqual([point['yellow_coins'] for point in self.db.get_stats(INSTANCE, MONTH)['coins_snapshots']],
                         [1100, 1000, 900])

    def test_noncanonical_point_uses_fallback_and_keeps_extension(self):
        self.seed['coins_snapshots'][0]['custom_marker'] = {'a': 1}
        self.db.save_stats(INSTANCE, MONTH, self.seed)
        self.db.add_coins_snapshot(INSTANCE, 900, 11)
        self.assertEqual(self.db.get_stats(INSTANCE, MONTH)['coins_snapshots'][0], self.seed['coins_snapshots'][0])

    def test_nullable_native_coin_uses_original_coercion_fallback(self):
        for table, method, args in (
            ('coin_snapshots', self.db.add_coins_snapshot, (900, 11)),
            ('yellow_coin_snapshots', self.db.add_yellow_coin_snapshot, (900,)),
        ):
            with self.subTest(table=table):
                self.db.save_stats(INSTANCE, MONTH, self.seed)
                with self.db.store.transaction() as conn:
                    conn.execute(f'UPDATE {table} SET yellow_coin=NULL')
                with patch.object(self.db, '_get_stats_in_connection', wraps=self.db._get_stats_in_connection) as read:
                    method(INSTANCE, *args)
                    self.assertTrue(read.called)
                stats = self.db.get_stats(INSTANCE, MONTH)
                family = 'coins_snapshots' if table == 'coin_snapshots' else 'yellow_coin_snapshots'
                key = 'yellow_coins' if table == 'coin_snapshots' else 'yellow_coin'
                self.assertIsNone(stats[family][0][key])
                self.assertEqual(stats[family][-1][key], 900)

    def test_strict_native_balance_rejects_fractional_value_without_mutation(self):
        before = self.rows('coin_snapshots')
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.store.transaction() as conn:
                conn.execute('UPDATE coin_snapshots SET yellow_coin=100.5')
        self.assertEqual(before, self.rows('coin_snapshots'))

    def test_month_start_purple_residue_stays_outside_display_curve(self):
        self.db.save_stats(INSTANCE, '2026-09', {'coins_snapshots': [
            self.coin(1000, 10, '2026-09-30T23:59:00'),
        ]})
        self.db.save_stats(INSTANCE, MONTH, {'coins_history_version': database.COINS_HISTORY_VERSION})
        self.db.add_coins_snapshot(INSTANCE, 990, 10)
        self.db.add_coins_snapshot(INSTANCE, 990, 10)
        self.db.add_coins_snapshot(INSTANCE, 980, 1)
        stats = self.db.get_stats(INSTANCE, MONTH)
        self.assertEqual([point['yellow_coins'] for point in stats['coins_month_start_residue']], [990])
        self.assertEqual([point['purple_coins'] for point in stats['coins_snapshots']], [1])

    def test_new_month_preserves_original_default_fields(self):
        self.db.add_yellow_coin_snapshot('new_probe', 1000)
        stats = self.db.get_stats('new_probe', MONTH)
        self.assertEqual(stats['battle_count'], 0)
        self.assertEqual(stats['yellow_coin_snapshots'], [self.yellow(1000, NOW.isoformat(), 'dashboard')])
        self.assertEqual(stats['coins_snapshots'], [])

    def test_history_backfill_is_still_invoked_before_coin_write(self):
        events = []
        original = self.db._try_append_coin_snapshot
        with patch.object(self.db, 'ensure_coins_history', side_effect=lambda *args: events.append('history')), \
                patch.object(self.db, '_try_append_coin_snapshot',
                             side_effect=lambda *args: (events.append('append'), original(*args))[1]):
            self.db.add_coins_snapshot(INSTANCE, 900, 11)
        self.assertEqual(events, ['history', 'append'])

    def test_concurrent_writers_keep_every_distinct_observation(self):
        def record(index):
            writer = self.db if index % 2 else self.other
            writer.add_coins_snapshot(INSTANCE, 2000 + index, 20 + index, source='concurrent')
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(record, range(20)))
        points = self.db.get_stats(INSTANCE, MONTH)['coins_snapshots']
        self.assertEqual(len(points), 21)
        self.assertEqual({point['yellow_coins'] for point in points}, {1000, *range(2000, 2020)})
        with self.db.store.transaction(write=False) as conn:
            self.assertEqual([], conn.execute('PRAGMA foreign_key_check').fetchall())
            self.assertEqual('ok', conn.execute('PRAGMA integrity_check').fetchone()[0])


if __name__ == '__main__':
    unittest.main()
