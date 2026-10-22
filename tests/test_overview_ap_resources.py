"""总览只读取需要的行动力最新值，保留原始历史与兼容数据语义。"""
import copy
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from module.api.runtime_service import monthly_statistics_resources
from module.persistence.database import BusinessDatabase, VERSION, create_schema
from module.persistence.snapshots import FAMILIES, MONTH_FIELDS, load_row
from module.persistence.values import write_value
from module.statistics.cl1_database import Cl1Database


INSTANCE = 'overview_probe'
MONTH = '2026-10'
NOW = datetime(2026, 10, 22, 12)


class OverviewApResourcesTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        self.path = directory / 'azurpilot.db'
        with closing(sqlite3.connect(self.path)) as conn, conn:
            create_schema(conn)
            conn.execute('INSERT INTO storage_migrations VALUES(?,?,?)',
                         (VERSION, NOW.isoformat(), 'overview-fixture'))
        (directory / 'azurpilot.migrated').write_text(f'{VERSION}\noverview-fixture\n', encoding='utf-8')
        self.db = Cl1Database(store=BusinessDatabase(directory))

    def save(self, timeline, instance=INSTANCE, month=MONTH):
        self.db.save_stats(instance, month, {'ap_snapshots': copy.deepcopy(timeline)})

    def expected(self, instance=INSTANCE, month=MONTH):
        """冻结原总览按时间、同时间最后一条及非空字段的选择规则。"""
        timeline = self.db.get_stats(instance, month).get('ap_snapshots', [])
        if not timeline:
            return {}
        try:
            timeline = sorted(timeline, key=lambda row: row.get('ts', ''))
        except Exception:
            pass
        result = {'distance': {}, 'asset': {}}
        for field in result:
            for row in timeline:
                if row.get(field) is None:
                    continue
                if not result[field] or (row.get('ts') or '') >= (result[field].get('ts') or ''):
                    result[field] = row
        return result

    def test_native_latest_is_independent_for_each_field_and_retains_zero(self):
        self.save([
            {'ts': '2026-10-22T10:00:00', 'distance': 12, 'asset': 30},
            {'ts': '2026-10-22T11:00:00', 'distance': 0},
            {'ts': '2026-10-22T09:00:00', 'distance': 99, 'asset': 99},
            {'ts': '2026-10-22T10:30:00', 'asset': 0},
            {'ts': '2026-10-22T12:00:00', 'ap': 100, 'distance': None, 'asset': None},
        ])
        actual = self.db.get_latest_ap_resources(INSTANCE, MONTH)
        self.assertEqual(actual, self.expected())
        self.assertEqual(actual['distance']['distance'], 0)
        self.assertEqual(actual['asset']['asset'], 0)

    def test_equal_timestamps_keep_last_original_record(self):
        self.save([
            {'ts': '2026-10-22T10:00:00', 'distance': 1, 'asset': 2},
            {'ts': '2026-10-22T11:00:00', 'ap': 0},
            {'ts': '2026-10-22T10:00:00', 'distance': 3, 'asset': 4},
        ])
        actual = self.db.get_latest_ap_resources(INSTANCE, MONTH)
        self.assertEqual(actual, self.expected())
        self.assertEqual(actual['distance']['distance'], 3)
        self.assertEqual(actual['asset']['asset'], 4)

    def test_missing_null_and_empty_timestamps_match_original(self):
        self.save([{'distance': 1}, {'ts': None, 'asset': 2},
                   {'ts': '', 'distance': 3}, {'ts': None, 'asset': 4}])
        self.assertEqual(self.db.get_latest_ap_resources(INSTANCE, MONTH), self.expected())

    def test_empty_and_unrecorded_resources_remain_distinct(self):
        self.assertEqual(self.db.get_latest_ap_resources(INSTANCE, MONTH), {})
        for value in ([], None, {}):
            self.save(value)
            self.assertEqual(self.db.get_latest_ap_resources(INSTANCE, MONTH), {})
        self.save([{'ts': '2026-10-22', 'ap': 10}])
        self.assertEqual(self.db.get_latest_ap_resources(INSTANCE, MONTH), {'distance': {}, 'asset': {}})

    def test_scope_is_exact_and_committed_updates_are_visible(self):
        self.save([{'ts': '2026-10-22', 'distance': 3, 'asset': 4}])
        self.save([{'ts': '2099-12-31', 'distance': 900}], instance='other')
        self.save([{'ts': '2099-12-31', 'asset': 900}], month='2026-09')
        self.assertEqual(self.db.get_latest_ap_resources(INSTANCE, MONTH), self.expected())
        other = Cl1Database(store=BusinessDatabase(self.path.parent))
        other.save_stats(INSTANCE, MONTH, {'ap_snapshots': [{'ts': '2026-10-22', 'distance': 7}]})
        self.assertEqual(self.db.get_latest_ap_resources(INSTANCE, MONTH)['distance']['distance'], 7)

    def test_native_path_decodes_only_selected_rows_and_never_whole_month(self):
        timeline = [{'ts': f'2026-10-{day:02d}', 'distance': day, 'asset': day * 2}
                    for day in range(1, 29)]
        self.save(timeline)
        expected = self.expected()
        before = self.path.read_bytes()
        with patch('module.persistence.snapshots.read_month', side_effect=AssertionError('读取了整月')), \
                patch('module.persistence.snapshots.load_row', wraps=load_row) as decode:
            self.assertEqual(self.db.get_latest_ap_resources(INSTANCE, MONTH), expected)
            self.assertEqual(decode.call_count, 2)
        self.assertEqual(self.path.read_bytes(), before)

    def test_readonly_connection_does_not_touch_other_statistic_families(self):
        self.db.save_stats(INSTANCE, MONTH, {
            'ap_snapshots': [{'ts': '2026-10-22', 'distance': 12, 'asset': 34}],
            'coins_snapshots': [{'ts': '2026-10-22', 'yellow_coins': 999}],
            'commission_income_entries': [{'items': {'Cube': 2}, 'screenshots': ['keep.png']}],
            'meow_round_times': [10, 20, 30],
        })
        before = self.db.get_stats(INSTANCE, MONTH)
        transaction = self.db.store.transaction
        allowed = {'cl1_months', 'cl1_compat_fields', 'action_point_snapshots'}
        @contextmanager
        def restricted(*, write=True, **kwargs):
            self.assertFalse(write)
            with transaction(write=write, **kwargs) as conn:
                conn.set_authorizer(lambda action, table, *_:
                    sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and table not in allowed
                    else sqlite3.SQLITE_OK)
                yield conn
        with patch.object(self.db.store, 'transaction', restricted):
            actual = self.db.get_latest_ap_resources(INSTANCE, MONTH)
        self.assertEqual(actual, self.expected())
        self.assertEqual(self.db.get_stats(INSTANCE, MONTH), before)

    def test_compatible_values_and_extensions_keep_original_types(self):
        self.save([
            {'ts': '2026-10-20', 'distance': 3, 'asset': 4},
            {'ts': '2026-10-21', 'distance': 2 ** 80, 'asset': 'unknown', 'extra': {'keep': [None, True]}},
            {'ts': '2026-10-22', 'asset': True},
        ])
        expected = self.expected()
        before = self.path.read_bytes()
        with patch('module.persistence.snapshots.read_month', side_effect=AssertionError('读取了整月')):
            actual = self.db.get_latest_ap_resources(INSTANCE, MONTH)
        self.assertEqual(actual, expected)
        self.assertIs(actual['asset']['asset'], True)
        self.assertEqual(self.path.read_bytes(), before)

    def test_compatible_whole_family_overrides_native_projection(self):
        self.save([{'ts': '2026-10-22', 'distance': 1}])
        with self.db.store.transaction() as conn:
            value_id = write_value(conn, INSTANCE, [{'ts': '2026-10-22', 'distance': 7}], month=MONTH)
            conn.execute('INSERT INTO cl1_compat_fields VALUES(?,?,?,?,?)',
                         (INSTANCE, MONTH, 'ap_snapshots', 'value', value_id))
        self.assertEqual(self.db.get_latest_ap_resources(INSTANCE, MONTH), self.expected())
        self.assertEqual(self.db.get_latest_ap_resources(INSTANCE, MONTH)['distance']['distance'], 7)

    def test_field_presence_masks_are_respected(self):
        self.save([{'ts': '2026-10-22', 'distance': 12, 'asset': 34}])
        fields = FAMILIES['ap_snapshots'][1]
        distance_mask = 1 << next(index for index, field in enumerate(fields) if field[0] == 'distance')
        with self.db.store.transaction() as conn:
            conn.execute('UPDATE action_point_snapshots SET field_mask=field_mask & ?', (~distance_mask,))
        self.assertEqual(self.db.get_latest_ap_resources(INSTANCE, MONTH), self.expected())
        with self.db.store.transaction() as conn:
            conn.execute('UPDATE cl1_months SET field_mask=field_mask & ?',
                         (~(1 << MONTH_FIELDS.index('ap_snapshots')),))
        self.assertEqual(self.db.get_latest_ap_resources(INSTANCE, MONTH), {})

    def test_corrupt_compatibility_tree_is_not_silently_discarded(self):
        self.save([{'ts': '2026-10-22', 'distance': 2 ** 80}])
        with self.db.store.transaction() as conn:
            conn.execute('DELETE FROM typed_value_nodes WHERE parent_no IS NULL')
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.db.get_latest_ap_resources(INSTANCE, MONTH)
        self.assertEqual(self.path.read_bytes(), before)

    def test_runtime_resource_cards_keep_values_and_record_format(self):
        self.save([{'ts': '2026-10-22T11:12:13.999', 'distance': 0},
                   {'ts': '2026-10-21T11:12:13', 'asset': 45.5}])
        with patch('module.statistics.cl1_database.db', self.db), \
                patch('module.config.time_source.now', return_value=NOW):
            resources = monthly_statistics_resources(INSTANCE)
        self.assertEqual([(item['name'], item['value'], item['record']) for item in resources], [
            ('Distance', 0, '2026-10-22 11:12:13'), ('ActionAsset', 45.5, '2026-10-21 11:12:13'),
        ])


if __name__ == '__main__':
    unittest.main()
