"""报表读取的校验批次、无写入展示及异常隔离；只使用临时安装。"""
import sqlite3
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import patch

from module.api.statistics_service import _report, report, get_statistics_fingerprint
from module.statistics import opsi_secure
from module.statistics.cl1_database import Cl1Database
from module.statistics.ship_exp_stats import ShipExpStats
from tests.test_opsi_secure import make_cl1_db
from tests.test_opsi_secure_stores import VaultCase


class StatisticsReadingTests(VaultCase):
    def setUp(self):
        super().setUp()
        make_cl1_db(self.root / 'config/cl1_data.db')
        self.database = Cl1Database(self.root / 'config/cl1_data.db')
        self.vault = self.configure()
        self.ships = self.root / 'log/cl1/inst/ship_exp_data.json'
        self.vault.write_file('ships', self.ships, {}, wrapper=True)
        self.configs = SimpleNamespace(path=lambda _: self.root / 'config/inst.json')
        self.enterContext(patch('module.statistics.cl1_database.db', self.database))
        self.enterContext(patch('module.statistics.opsi_month.cl1_db', self.database))
        self.enterContext(patch('module.statistics.ship_exp_stats.ShipExpStats',
            side_effect=lambda **kwargs: ShipExpStats(path=self.ships, **kwargs)))

    def get_report(self, reader=report, category='opsi'):
        return reader(self.configs, 'inst', category, '2026-09', 7, 'month')

    def test_report_read_preserves_values_and_generation(self):
        for category in ('opsi', 'action', 'ships'):
            with self.subTest(category=category):
                expected = self.get_report(_report, category)
                generation = self.provider.load(self.vault.slot)['generation']
                before = (self.database.db_path.read_bytes(), self.ships.read_bytes())
                with patch.object(self.vault.coordinator, 'snapshot', wraps=self.vault.coordinator.snapshot) as snapshot, \
                        patch.object(self.provider, 'load', wraps=self.provider.load) as load:
                    self.assertEqual(self.get_report(category=category), expected)
                    self.assertEqual(snapshot.call_count, 0)
                self.assertEqual(self.provider.load(self.vault.slot)['generation'], generation)
                self.assertEqual((self.database.db_path.read_bytes(), self.ships.read_bytes()), before)

    def test_write_transaction_touches_only_written_path(self):
        """一次统计写入只解封一次、不做全量摘要，只为所写路径核对并更新链值。"""
        with patch.object(self.vault.coordinator, 'snapshot', wraps=self.vault.coordinator.snapshot) as snapshot, \
                patch.object(self.provider, 'load', wraps=self.provider.load) as load, \
                patch.object(self.provider, 'save', wraps=self.provider.save) as save, \
                patch.object(self.vault, '_path_digest', wraps=self.vault._path_digest) as digest:
            self.database.increment_battle_count('inst', 1)
        self.assertEqual(load.call_count, 1)
        self.assertEqual(snapshot.call_count, 0)
        self.assertEqual(save.call_count, 1)
        self.assertEqual(digest.call_count, 0)      # 写入路径不触碰任何全量摘要
        key = str(self.database.db_path.resolve().relative_to(self.root))
        entry = self.vault._state['expect'][key]
        self.assertEqual(entry['s'], 2)             # 建链（1）后每次写入推进链序号

    def test_meow_compatibility_is_in_memory_until_explicit_backfill(self):
        data = self.database.get_stats('inst', '2026-09')
        data.pop('meow_battle_raw_count')
        self.database.save_stats('inst', '2026-09', data)
        before = self.database.db_path.read_bytes()
        generation = self.provider.load(self.vault.slot)['generation']
        with patch.object(self.database, '_stats_transaction', side_effect=AssertionError('展示不能进入写事务')):
            result = self.database.get_meow_stats('inst', 2026, 9)
        self.assertGreater(result['battle_count'], 0)
        self.assertEqual(self.database.db_path.read_bytes(), before)
        self.assertEqual(self.provider.load(self.vault.slot)['generation'], generation)
        self.assertNotIn('meow_battle_raw_count', self.database.get_stats('inst', '2026-09'))
        self.assertTrue(self.database.backfill_meow_stats('inst', 2026, 9))
        self.assertEqual(self.database.get_stats('inst', '2026-09')['meow_battle_raw_count'], result['battle_count'])

    def test_tamper_triggers_freeze_on_page_read(self):
        """页面读取路径拿到被改的密文：本次读出降级，后台完成冻结。"""
        before = self.get_report()
        with closing(sqlite3.connect(self.database.db_path)) as conn, conn:
            conn.execute("UPDATE cl1_data SET secure_json = substr(secure_json, 1, length(secure_json) - 4) || 'AAAA'")
        data = self.get_report()
        self.vault._wipe_thread.join(timeout=10)
        self.assertTrue(self.vault.status()['blocked'])
        self.assertFalse(self.vault.wipe_path.exists())
        self.assertEqual(data['category'], before['category'])

    def test_row_deletion_triggers_freeze_on_page_checkpoint(self):
        """页面核对点发现受保护行被整体删除：当场冻结，页面降级显示。"""
        self.get_report()
        with closing(sqlite3.connect(self.database.db_path)) as conn, conn:
            conn.execute('DELETE FROM cl1_data')
        self.get_report()
        self.assertTrue(self.vault.status()['blocked'])
        self.assertFalse(self.vault.wipe_path.exists())

    def test_legacy_page_checkpoint_detects_chain_mismatch_without_deleting_history(self):
        from contextlib import ExitStack
        from module.api import legacy_stats_service

        with closing(sqlite3.connect(self.database.db_path)) as conn, conn:
            conn.execute("UPDATE __opsi_integrity SET chain='invalid'")
        before = self.database.db_path.read_bytes()
        credentials = self.provider.load(self.vault.slot)
        with ExitStack() as stack:
            for name in ('_ap_panel', '_opsi_panel', '_meow_loot_panel', '_ship_panel',
                         '_commission_periods', '_commission_recent', '_commission_running'):
                stack.enter_context(patch.object(legacy_stats_service, name, return_value={}))
            data = legacy_stats_service.report(self.configs, 'inst', '2026-09')
        self.assertEqual(data['month'], '2026-09')
        self.assertTrue(self.vault.status()['blocked'])
        self.assertEqual(before, self.database.db_path.read_bytes())
        self.assertEqual(credentials, self.provider.load(self.vault.slot))
        self.assertFalse(self.vault.wipe_path.exists())

    def test_backend_outage_does_not_write_or_wipe(self):
        expected = self.get_report()
        before = (self.database.db_path.read_bytes(), self.ships.read_bytes(), self.vault.keyring_path.read_bytes())
        self.provider.offline = True
        with patch.object(self.vault, '_wipe', side_effect=AssertionError('临时故障不能冻结')):
            self.get_report()
        self.assertEqual((self.database.db_path.read_bytes(), self.ships.read_bytes(), self.vault.keyring_path.read_bytes()), before)
        self.provider.offline = False
        self.assertEqual(self.get_report(), expected)

    def test_nested_transaction_preserves_outer_read_scope(self):
        with patch.object(self.vault.coordinator, 'snapshot', wraps=self.vault.coordinator.snapshot) as snapshot:
            with self.vault.reading():
                with closing(sqlite3.connect(self.database.db_path)) as conn:
                    with self.vault.transaction(conn, self.database.db_path):
                        pass
                self.assertTrue(self.vault._in_transaction)
                calls = snapshot.call_count
                self.database.get_stats('inst', '2026-09')
                self.assertEqual(snapshot.call_count, calls)
        self.assertFalse(self.vault._in_transaction)

    def test_fingerprint_tracks_wal_changes_without_overview_events(self):
        original = get_statistics_fingerprint('inst')
        from module.api import statistics_service
        stat = statistics_service.os.stat

        def changed(path):
            if path == './config/cl1_data.db-wal':
                return SimpleNamespace(st_mtime_ns=987654321, st_size=4096)
            return stat(path)

        with patch.object(statistics_service.os, 'stat', side_effect=changed):
            self.assertNotEqual(get_statistics_fingerprint('inst'), original)
