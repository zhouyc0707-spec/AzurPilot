"""验证短统计族增量写与旧整月路径等价，且不重建其他历史。"""

import copy
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from module.persistence.database import BusinessDatabase, create_schema
from module.persistence.values import write_value
from module.statistics import cl1_database as database


NOW = datetime(2026, 11, 8, 16)
MONTH = '2026-11'
PREVIOUS = '2026-10'
RUNNING = {'name': '钻石委托', 'duration': 2, 'create_time': '2026-10-31T22:00:00',
           'finish_time': '2026-11-01T00:00:00'}


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


class MonthlyLocalUpdatesTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory(ignore_cleanup_errors=True))
        self.store = BusinessDatabase(Path(directory))
        with closing(sqlite3.connect(self.store.path)) as conn, conn:
            create_schema(conn)
        self.store._ready = True
        self.db = database.Cl1Database(store=self.store)
        self.enterContext(patch.object(database, 'datetime', FixedDatetime))
        self.enterContext(patch.object(database, 'current_time', return_value=NOW))

    def seed(self, instance, month=MONTH, extra=None):
        data = self.db._empty_data(month)
        data.update(battle_count=10, akashi_ap=50, akashi_encounters=2,
                    coins_history_version=database.COINS_HISTORY_VERSION,
                    yellow_coin_snapshots=[{'ts': f'{month}-01T12:00:00', 'yellow_coin': 20, 'source': 'cl1'}],
                    ap_snapshots=[{'ts': f'{month}-02T00:00:00', 'ap': 20, 'source': 'cl1'},
                                  {'ts': f'{month}-01T00:00:00', 'ap': 30, 'source': 'cl1'}],
                    siren_research_devices={'cl1': 3, 'meow': {'5': 2}},
                    siren_research_device_entries=[{'ts': f'{month}-01T12:00:00', 'source': 'cl1',
                                                    'hazard_level': None}],
                    research_drop_entries=[{'ts': f'{month}-01T12:00:00', 'imgid': 'original',
                                            'series': 8, 'project': 'D-001', 'items': {'Coin': 20},
                                            'source_items': [], 'item_entries': []}],
                    cloud_commission_entries=[{'record_id': 'original', 'collected_at': f'{month}-01T12:00:00',
                                               'unknown': {'preserve': [1, None]}}])
        if extra:
            data.update(copy.deepcopy(extra))
        self.db.save_stats(instance, month, data)
        return data

    def no_month(self):
        return patch.multiple(self.db,
                              _get_stats_in_connection=Mock(side_effect=AssertionError('不应展开整月')),
                              _save_stats_in_connection=Mock(side_effect=AssertionError('不应重写整月')))

    def compare_call(self, method, *args, **kwargs):
        with self.no_month():
            actual = getattr(self.db, method)('fast', *args, **kwargs)
        with patch.object(self.db, '_native_month_for_update', return_value=None):
            expected = getattr(self.db, method)('legacy', *args, **kwargs)
        self.assertEqual(actual, expected)
        for month in (MONTH, PREVIOUS):
            self.assertEqual(self.db.get_stats('fast', month), self.db.get_stats('legacy', month))
        return actual

    def test_normal_updates_match_full_month_with_research_and_cloud_extensions(self):
        for instance in ('fast', 'legacy'):
            self.seed(instance)
            self.seed(instance, PREVIOUS, {'running_gem_commissions': [RUNNING]})
        with self.store.transaction(write=False) as conn:
            untouched = tuple(conn.execute("SELECT id FROM yellow_coin_snapshots WHERE instance='fast'").fetchone())
        self.compare_call('add_siren_research_device', 'cl1')
        self.compare_call('add_siren_research_device', 'meow', 5)
        self.compare_call('set_last_ap_notification', 140)
        self.compare_call('add_gem_commission', 4, 20)
        current = dict(RUNNING, create_time='2026-11-08T14:00:00', finish_time='2026-11-08T16:00:00')
        self.compare_call('add_running_gem_commission', current)
        self.compare_call('add_running_gem_commission', current)
        self.compare_call('settle_gem_commission', 30, create_time=current['create_time'])
        self.compare_call('settle_expired_gem_commissions', NOW)
        self.compare_call('settle_expired_gem_commissions', NOW)
        self.compare_call('add_cloud_commission_drop', {'record_id': 'new', 'collected_at': NOW.isoformat(),
                                                      'new_field': [None, '保留']})
        self.compare_call('add_cloud_commission_drop', {'record_id': 'new', 'collected_at': NOW.isoformat()})
        self.compare_call('add_research_drop', 'D-001', 8, {'Coin': 100}, imgid='new', source_items=[])
        self.compare_call('add_research_drop', 'D-001', 8, {'Coin': 100}, imgid='new', source_items=[])
        with self.store.transaction(write=False) as conn:
            self.assertEqual(untouched, tuple(conn.execute(
                "SELECT id FROM yellow_coin_snapshots WHERE instance='fast'").fetchone()))
            self.assertEqual([], conn.execute('PRAGMA foreign_key_check').fetchall())

    def test_running_order_duplicates_pop_and_override_remain_equivalent(self):
        for instance in ('fast', 'legacy'):
            self.seed(instance, extra={'running_gem_commissions': [dict(RUNNING, name='later',
                                                                      finish_time='2026-11-09T00:00:00')]})
            self.seed(instance, PREVIOUS, {'running_gem_commissions': [RUNNING]})
        self.compare_call('add_running_gem_commission', dict(RUNNING, name='early'))
        self.compare_call('get_running_gem_commissions')
        self.compare_call('pop_running_gem_commission', name='early')
        self.compare_call('pop_running_gem_commission', name=RUNNING['name'], create_time=RUNNING['create_time'])
        self.compare_call('save_running_gem_commissions', [RUNNING, dict(RUNNING, name='second')])
        self.compare_call('save_running_gem_commissions', [])

    def test_compatibility_target_and_unknown_root_fall_back_without_loss(self):
        for extras in ({'running_gem_commissions': [dict(RUNNING, unknown={'keep': None})]},
                       {'unknown_root': ['保留', None]},
                       {'siren_research_devices': {'cl1': '4', 'meow': {'05': '3'}}}):
            with self.subTest(extras=extras):
                for instance in ('fast', 'legacy'):
                    self.seed(instance, extra=extras)
                for method, args in (('add_siren_research_device', ('meow', 5)),
                                     ('set_last_ap_notification', (100,)),
                                     ('add_running_gem_commission', (dict(RUNNING, name='new'),))):
                    actual = getattr(self.db, method)('fast', *args)
                    with patch.object(self.db, '_native_month_for_update', return_value=None):
                        expected = getattr(self.db, method)('legacy', *args)
                    self.assertEqual(actual, expected)
                    self.assertEqual(self.db.get_stats('fast', MONTH), self.db.get_stats('legacy', MONTH))

    def test_local_new_compatibility_values_fall_back_before_any_partial_write(self):
        for instance in ('fast', 'legacy'):
            self.seed(instance)
        commission = dict(RUNNING, extension={'keep': [None, True]})
        self.db.add_running_gem_commission('fast', commission)
        with patch.object(self.db, '_native_month_for_update', return_value=None):
            self.db.add_running_gem_commission('legacy', commission)
        self.assertEqual(self.db.get_stats('fast', MONTH), self.db.get_stats('legacy', MONTH))

    def test_expired_cross_month_failure_rolls_back_source_and_target(self):
        self.seed('fast')
        self.seed('fast', PREVIOUS, {'running_gem_commissions': [RUNNING]})
        before = {month: self.db.get_stats('fast', month) for month in (MONTH, PREVIOUS)}
        with self.store.transaction() as conn:
            conn.execute("CREATE TRIGGER reject_gem BEFORE INSERT ON gem_commission_history "
                         "BEGIN SELECT RAISE(ABORT, '故障注入'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.settle_expired_gem_commissions('fast', NOW)
        self.assertEqual(before, {month: self.db.get_stats('fast', month) for month in (MONTH, PREVIOUS)})

    def test_history_caps_preserve_retained_rows_and_remove_only_expired_tree(self):
        entries = [{'ts': NOW.isoformat(), 'imgid': str(i), 'items': {'Coin': i},
                    'source_items': [], 'item_entries': []} for i in range(5000)]
        self.seed('fast', extra={'research_drop_entries': entries})
        with self.store.transaction(write=False) as conn:
            retained_id = conn.execute("SELECT id FROM research_drops WHERE instance='fast' AND ordinal=1").fetchone()[0]
            old_tree = conn.execute("SELECT compat_value_set_id FROM research_drops WHERE instance='fast' AND ordinal=0").fetchone()[0]
        with self.no_month():
            self.db.add_research_drop('fast', 'D-001', 8, {'Coin': 100}, imgid='new', source_items=[])
            self.db.add_siren_research_device('fast')
        with self.store.transaction(write=False) as conn:
            self.assertEqual(5000, conn.execute('SELECT COUNT(*) FROM research_drops').fetchone()[0])
            self.assertIsNotNone(conn.execute('SELECT 1 FROM research_drops WHERE id=?', (retained_id,)).fetchone())
            self.assertIsNone(conn.execute('SELECT 1 FROM typed_value_sets WHERE id=?', (old_tree,)).fetchone())
            self.assertIsNotNone(self.db._native_month_for_update(conn, 'fast', MONTH))

    def test_gem_history_cap_and_ordinal_overflow_reset_preserve_snapshot(self):
        entries = [{'ts': NOW.isoformat(), 'duration': 2, 'reward': i, 'success': True} for i in range(5000)]
        for instance in ('fast', 'legacy'):
            self.seed(instance, extra={'gem_commission_entries': entries})
        self.compare_call('add_gem_commission', 4, 20)
        with self.store.transaction() as conn:
            conn.execute("UPDATE gem_commission_history SET ordinal=9223372036854775806 "
                         "WHERE instance='fast' AND ordinal=(SELECT MAX(ordinal) FROM gem_commission_history WHERE instance='fast')")
        self.compare_call('add_gem_commission', 2, 10)

    def test_last_ap_uses_list_order_not_timestamp_and_falls_back_across_months(self):
        self.seed('fast', extra={'last_ap_notification': {'ts': NOW.isoformat(), 'ap': 100}})
        with self.no_month():
            self.assertEqual(30, self.db.get_last_ap_snapshot('fast')['ap'])
            self.assertEqual(100, self.db.get_last_ap_notification('fast')['ap'])
        self.seed('fast', extra={'ap_snapshots': [], 'last_ap_notification': {}})
        self.seed('fast', PREVIOUS, {'last_ap_notification': {'ap': 90, 'ts': '2026-10-31T23:00:00'}})
        with self.no_month():
            self.assertEqual(30, self.db.get_last_ap_snapshot('fast')['ap'])
            self.assertEqual(90, self.db.get_last_ap_notification('fast')['ap'])

    def test_local_family_in_place_edit_is_saved_instead_of_mistaken_for_append(self):
        self.seed('fast', extra={'gem_commission_entries': [
            {'ts': NOW.isoformat(), 'duration': 2, 'reward': 10, 'success': True}]})
        with self.no_month(), self.store.transaction() as conn:
            names = ('gem_commission_entries',)
            data, original = self.db._read_mutable_fields(conn, 'fast', MONTH, names)
            data['gem_commission_entries'][0]['reward'] = 99
            self.db._save_mutable_fields(conn, 'fast', MONTH, data, original, names)
        self.assertEqual(99, self.db.get_stats('fast', MONTH)['gem_commission_entries'][0]['reward'])

    def test_last_ap_read_preserves_error_for_broken_earlier_value_row(self):
        self.seed('fast')
        with self.store.transaction() as conn:
            conn.execute("UPDATE action_point_snapshots SET entry_kind='value' "
                         "WHERE instance='fast' AND ordinal=0")
        with self.assertRaises(ValueError):
            self.db.get_stats('fast', MONTH)
        with self.assertRaises(ValueError):
            self.db.get_last_ap_snapshot('fast')

    def test_cold_history_version_check_needs_no_month_decode(self):
        self.seed('fast')
        with self.no_month(), self.store.transaction() as conn:
            self.db.ensure_coins_history('fast', conn)
        self.assertIn('fast', self.db._coins_history_checked)

    def test_cold_version_override_is_not_mistaken_for_native_completed_marker(self):
        self.seed('fast')
        with self.store.transaction() as conn:
            value_id = write_value(conn, 'fast', '旧版本', month=MONTH)
            conn.execute('INSERT INTO cl1_compat_fields(instance,month,name,mode,value_set_id) VALUES(?,?,?,?,?)',
                         ('fast', MONTH, 'coins_history_version', 'value', value_id))
        with patch.object(self.db, '_get_stats_in_connection', wraps=self.db._get_stats_in_connection) as read:
            with patch('module.statistics.resource_stats.get_resource_timeline', return_value=[]) as timeline:
                with self.store.transaction() as conn:
                    self.db.ensure_coins_history('fast', conn)
            self.assertTrue(read.called)
            self.assertTrue(timeline.called)

    def test_coin_sequence_guard_handles_gaps_and_earlier_disorder(self):
        for family, table in (('yellow_coin_snapshots', 'yellow_coin_snapshots'),
                              ('coins_snapshots', 'coin_snapshots')):
            for disorder in (False, True):
                with self.subTest(family=family, disorder=disorder):
                    entries = [{'ts': f'2026-11-08T1{hour}:00:00', 'source': 'cl1',
                                **({'yellow_coin': hour} if family == 'yellow_coin_snapshots'
                                   else {'yellow_coins': hour, 'purple_coins': hour})}
                               for hour in ([2, 1, 3] if disorder else [1, 2, 3])]
                    for instance in ('fast', 'legacy'):
                        self.seed(instance, extra={family: entries})
                        self.db._coins_history_checked.add(instance)
                        self.db._coins_cleanup_checked.add(instance)
                    with self.store.transaction() as conn:
                        conn.execute(f"UPDATE {table} SET ordinal=ordinal+10 WHERE instance='fast' AND ordinal>=1")
                    method = 'add_yellow_coin_snapshot' if family == 'yellow_coin_snapshots' else 'add_coins_snapshot'
                    args = (9,) if family == 'yellow_coin_snapshots' else (9, 9)
                    with patch.object(self.db, '_get_stats_in_connection', wraps=self.db._get_stats_in_connection) as read:
                        getattr(self.db, method)('fast', *args)
                    self.assertEqual(disorder, read.called)
                    with patch.object(self.db, '_native_month_for_update', return_value=None):
                        getattr(self.db, method)('legacy', *args)
                    self.assertEqual(self.db.get_stats('fast', MONTH), self.db.get_stats('legacy', MONTH))

    def test_shared_compatibility_tree_is_not_deleted_with_trimmed_event(self):
        self.seed('fast')
        with self.store.transaction() as conn:
            value_id = conn.execute('SELECT compat_value_set_id FROM research_drops').fetchone()[0]
            conn.execute('INSERT INTO cl1_compat_fields(instance,month,name,mode,value_set_id) VALUES(?,?,?,?,?)',
                         ('fast', MONTH, 'coins_month_start_residue', 'value', value_id))
            self.db._delete_unreferenced_month_values(conn, [value_id])
            self.assertIsNotNone(conn.execute('SELECT 1 FROM typed_value_sets WHERE id=?', (value_id,)).fetchone())
            self.assertEqual([], conn.execute('PRAGMA foreign_key_check').fetchall())

    def test_totals_preserve_missing_zero_null_and_compatibility_values(self):
        self.seed('fast', extra={'battle_count': None, 'akashi_encounters': 0})
        with self.no_month():
            self.assertEqual({'battle_count': None, 'akashi_encounters': 0, 'akashi_ap': 50},
                             self.db.get_cl1_totals('fast', MONTH))
            self.assertEqual(dict.fromkeys(('battle_count', 'akashi_encounters', 'akashi_ap'), 0),
                             self.db.get_cl1_totals('missing', MONTH))
        self.seed('fast', extra={'battle_count': '历史字符串'})
        self.assertEqual('历史字符串', self.db.get_cl1_totals('fast', MONTH)['battle_count'])

    def test_corrupt_research_tree_aborts_local_write(self):
        self.seed('fast')
        with self.store.transaction() as conn:
            value_id = conn.execute('SELECT compat_value_set_id FROM research_drops').fetchone()[0]
            conn.execute('DELETE FROM typed_value_nodes WHERE value_set_id=?', (value_id,))
        with self.assertRaises(ValueError):
            self.db.add_siren_research_device('fast')
        with self.store.transaction(write=False) as conn:
            self.assertEqual(3, conn.execute("SELECT device_count FROM siren_device_counts WHERE source='cl1'").fetchone()[0])

    def test_request_cache_isolated_from_threads_and_refreshes_after_local_write(self):
        self.seed('fast')
        with self.db.request_read_cache():
            first = self.db.get_stats('fast', MONTH)
            def other_request():
                with self.db.request_read_cache():
                    self.db.increment_battle_count('fast')
                    return self.db.get_stats('fast', MONTH)
            with ThreadPoolExecutor(max_workers=1) as executor:
                other = executor.submit(other_request).result()
            self.assertEqual(11, other['battle_count'])
            self.assertIs(first, self.db.get_stats('fast', MONTH))
            self.assertEqual(10, first['battle_count'])
            with self.db.request_read_cache():
                self.assertEqual(11, self.db.get_stats('fast', MONTH)['battle_count'])
            self.assertIs(first, self.db.get_stats('fast', MONTH))
            self.db.increment_battle_count('fast')
            self.assertEqual(12, self.db.get_stats('fast', MONTH)['battle_count'])
            with self.db.request_read_cache():
                self.db.increment_battle_count('fast')
            self.assertEqual(13, self.db.get_stats('fast', MONTH)['battle_count'])
        self.assertEqual(13, self.db.get_stats('fast', MONTH)['battle_count'])


if __name__ == '__main__':
    unittest.main()
