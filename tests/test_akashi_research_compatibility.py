"""验证科研扩展不会阻塞明石增量记账，也不会丢失或绕过复杂历史。"""

import copy
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from module.persistence.database import BusinessDatabase, create_schema
from module.persistence.snapshots import MONTH_FIELDS
from module.persistence.values import write_value
from module.statistics import cl1_database as database


NOW = datetime(2026, 11, 8, 14, 18)
MONTH = '2026-11'
WRITES = (
    ('add_akashi_ap_entry', (100, 50, 2, 'akashi')),
    ('add_yellow_coin_snapshot', (9000, 'cl1')),
    ('add_coins_snapshot', (9000, 100, 'cl1')),
)
RESEARCH_TABLES = (
    'research_drops', 'research_drop_items', 'cl1_compat_fields',
    'typed_value_sets', 'typed_value_nodes',
)


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


class AkashiResearchCompatibilityTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory(ignore_cleanup_errors=True))
        self.store = BusinessDatabase(Path(directory))
        with closing(sqlite3.connect(self.store.path)) as conn, conn:
            create_schema(conn)
        self.store._ready = True
        self.db = database.Cl1Database(store=self.store)
        self.enterContext(patch.object(database, 'datetime', FixedDatetime))
        self.enterContext(patch.object(database, 'current_time', return_value=NOW))

    def seed(self, instance='test', mutate=None):
        data = self.db._empty_data(MONTH)
        data.update(
            akashi_ap=20,
            akashi_ap_entries=[{'ts': '2026-11-08T13:00:00', 'amount': 20,
                               'base': 20, 'count': 1, 'source': 'akashi'}],
            yellow_coin_snapshots=[{'ts': '2026-11-08T13:00:00', 'yellow_coin': 10000, 'source': 'cl1'}],
            coins_snapshots=[{'ts': '2026-11-08T13:00:00', 'yellow_coins': 10000,
                              'purple_coins': 100, 'source': 'cl1'}],
            coins_history_version=database.COINS_HISTORY_VERSION,
            coins_month_start_residue=[{'ts': '2026-11-01T00:00:00', 'purple_coins': 10}],
            akashi_shop_champion={'monthly': {'ap': 250, 'screenshot': 'fixture.png'}},
            research_drop_entries=[{
                'ts': '2026-11-08T12:00:00', 'imgid': 'research-fixture', 'series': 8,
                'project': 'fixture-project', 'items': {'Coin': 20},
                'source_items': [{'name': 'Coin', 'amount': 20, 'extra': [True, None, '原记录']}],
                'item_entries': [{'name': 'Coin', 'amount': 20}],
            }],
        )
        if mutate is not None:
            mutate(data)
        self.db.save_stats(instance, MONTH, data)
        self.db._coins_history_checked.add(instance)
        self.addCleanup(self.db._coins_history_checked.discard, instance)
        return data

    def rows(self, tables=RESEARCH_TABLES):
        with self.store.transaction(write=False) as conn:
            return {table: sorted((tuple(row) for row in conn.execute(f'SELECT * FROM {table}')), key=repr)
                    for table in tables}

    def call(self, instance, write):
        name, args = write
        return getattr(self.db, name)(instance, *args)

    def no_month_rewrite(self):
        return patch.multiple(
            self.db,
            _get_stats_in_connection=unittest.mock.Mock(side_effect=AssertionError('不应读取整月')),
            _save_stats_in_connection=unittest.mock.Mock(side_effect=AssertionError('不应重写整月')),
        )

    def test_each_write_matches_legacy_and_preserves_original_research_rows(self):
        for write in WRITES:
            with self.subTest(write=write[0]):
                self.seed('fast')
                self.seed('legacy')
                original_rows = self.rows()
                with self.no_month_rewrite():
                    actual = self.call('fast', write)
                self.assertEqual(original_rows, self.rows())
                with patch.object(self.db, '_native_month_for_update', return_value=None):
                    expected = self.call('legacy', write)
                self.assertEqual(actual, expected)
                self.assertEqual(self.db.get_stats('fast', MONTH), self.db.get_stats('legacy', MONTH))

    def test_each_optional_research_list_can_be_empty_or_absent(self):
        for extras in ({'source_items': []}, {'item_entries': []}, {'source_items': [], 'item_entries': []}):
            with self.subTest(extras=extras):
                def mutate(data):
                    entry = data['research_drop_entries'][0]
                    entry.pop('source_items')
                    entry.pop('item_entries')
                    entry.update(extras)
                self.seed(mutate=mutate)
                before = self.rows()
                with self.no_month_rewrite():
                    for write in WRITES:
                        self.call('test', write)
                self.assertEqual(before, self.rows())

    def test_complex_research_extensions_still_use_lossless_fallback(self):
        variants = (
            {'unknown_field': {'keep': [1, None]}},
            {'ts': 123},
            {'source_items': {'keep': '旧格式'}},
            {'item_entries': None},
        )
        for extra in variants:
            for write in WRITES:
                with self.subTest(extra=extra, write=write[0]):
                    def mutate(data):
                        data['research_drop_entries'][0].update(copy.deepcopy(extra))
                    self.seed('fast', mutate)
                    self.seed('legacy', mutate)
                    with patch.object(self.db, '_get_stats_in_connection',
                                      wraps=self.db._get_stats_in_connection) as read:
                        actual = self.call('fast', write)
                    self.assertTrue(read.called)
                    with patch.object(self.db, '_native_month_for_update', return_value=None):
                        expected = self.call('legacy', write)
                    self.assertEqual(actual, expected)
                    self.assertEqual(self.db.get_stats('fast', MONTH), self.db.get_stats('legacy', MONTH))

    def test_hidden_nonrecord_and_unreferenced_values_reject_native_guard(self):
        for case in ('hidden_research', 'nonrecord', 'unreferenced'):
            with self.subTest(case=case):
                self.seed(mutate=(lambda data: data.update(research_drop_entries=[['旧记录']]))
                          if case == 'nonrecord' else None)
                with self.store.transaction() as conn:
                    if case == 'hidden_research':
                        mask = 1 << MONTH_FIELDS.index('research_drop_entries')
                        conn.execute('UPDATE cl1_months SET field_mask=field_mask & ? '
                                     'WHERE instance=? AND month=?', (~mask, 'test', MONTH))
                    elif case == 'unreferenced':
                        write_value(conn, instance='test', month=MONTH, value={'source_items': []})
                    self.assertIsNone(self.db._native_month_for_update(conn, 'test', MONTH))

    def test_damaged_research_tree_raises_and_rolls_back_each_purchase_write(self):
        self.seed()
        with self.store.transaction() as conn:
            value_id = conn.execute('SELECT compat_value_set_id FROM research_drops').fetchone()[0]
            conn.execute('DELETE FROM typed_value_nodes WHERE value_set_id=? AND parent_no IS NULL', (value_id,))
        tables = (*RESEARCH_TABLES, 'cl1_months', 'akashi_ap_purchases', 'yellow_coin_snapshots', 'coin_snapshots')
        before = self.rows(tables)
        for write in WRITES:
            with self.subTest(write=write[0]):
                with self.assertRaises(ValueError):
                    self.call('test', write)
                self.assertEqual(before, self.rows(tables))

    def test_concurrent_purchase_and_coin_writes_keep_every_record(self):
        original = self.seed()
        before = self.rows()
        other = database.Cl1Database(store=self.store)

        def record(index):
            writer = self.db if index % 2 else other
            writer.add_akashi_ap_entry('test', 20, 20, 1, 'akashi')
            writer.add_yellow_coin_snapshot('test', 20000 + index, 'concurrent')
            writer.add_coins_snapshot('test', 20000 + index, 200 + index, 'concurrent')

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(record, range(12)))
        stats = self.db.get_stats('test', MONTH)
        self.assertEqual(stats['akashi_ap'], 260)
        self.assertEqual(len(stats['akashi_ap_entries']), 13)
        for family, column in (('yellow_coin_snapshots', 'yellow_coin'), ('coins_snapshots', 'yellow_coins')):
            self.assertEqual(len(stats[family]), 13)
            self.assertEqual({point[column] for point in stats[family]}, {10000, *range(20000, 20012)})
        self.assertEqual(stats['research_drop_entries'], original['research_drop_entries'])
        self.assertEqual(before, self.rows())
        with self.store.transaction(write=False) as conn:
            self.assertEqual([], conn.execute('PRAGMA foreign_key_check').fetchall())
            self.assertEqual('ok', conn.execute('PRAGMA integrity_check').fetchone()[0])


if __name__ == '__main__':
    unittest.main()
