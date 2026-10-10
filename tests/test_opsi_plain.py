"""普通统计存储、无损解密迁移、发布回滚与跨进程读改写的隔离测试。"""
import base64
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np

from module.base import backup as backup_module
from module.statistics import opsi_secure, resource_stats
from module.statistics.azurstats import AzurStats
from module.statistics.cl1_database import Cl1Database
from module.statistics.daily_summary_store import DailySummaryStore
from module.statistics.opsi_plain import PlainStatisticsStore
from module.statistics.opsi_state import canonical, durable_write
from module.statistics.ship_exp_stats import ShipExpStats
from tests.test_opsi_secure import MemoryProvider, legacy_blob, legacy_ring, make_cl1_db, make_loot_db


class PlainStatisticsCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / 'config').mkdir()
        from module.persistence.database import _directory
        token = _directory.set(self.root / 'config')
        self.addCleanup(_directory.reset, token)
        (self.root / 'log' / 'cl1' / 'inst').mkdir(parents=True)
        self.cl1 = self.root / 'config' / 'cl1_data.db'
        self.local = self.root / 'config' / 'azurstats_local.db'
        self.ship = self.root / 'log' / 'cl1' / 'inst' / 'ship_exp_data.json'
        self.csv = self.root / 'log' / 'azurstat_meowofficer_farming.csv'
        self.provider = MemoryProvider()
        self.provider.offline = True
        self.store = PlainStatisticsStore(self.root, provider=self.provider)
        previous = opsi_secure._VAULT
        opsi_secure.set_vault(self.store)
        self.addCleanup(opsi_secure.set_vault, previous)
        for item in (patch.object(AzurStats, 'LOCAL_DB', str(self.local)),
                     patch.object(AzurStats, 'LOCAL_MEOW_CSV', str(self.csv)),
                     patch.object(resource_stats, '_LOCAL_DB', str(self.local)),
                     patch.object(resource_stats, '_table_ensured', False),
                     patch.object(backup_module, 'CONFIG_DIR', self.root / 'config'),
                     patch.object(backup_module, 'BACKUP_ROOT', self.root / 'AzurPilot_Data_Backup')):
            item.start()
            self.addCleanup(item.stop)

    def sql(self, path, statement, params=()):
        with closing(sqlite3.connect(path)) as conn:
            return conn.execute(statement, params).fetchall()

    def configure_encrypted(self):
        self.provider.offline = False
        self.full = make_cl1_db(self.cl1)
        # 原始黄紫币点、月初残留与未知扩展字段都应原样保留。
        self.full['coins_month_start_residue'] = {'yellow': 7, 'purple': 3}
        self.full['coins_snapshots'] *= 12
        self.full['custom_extension'] = {'keep': [1, 2, 3]}
        with closing(sqlite3.connect(self.cl1)) as conn, conn:
            conn.execute('UPDATE cl1_data SET data_json=?', (canonical(self.full).decode(),))
        make_loot_db(self.local)
        self.ship.write_bytes(canonical({'battle_times': [1, 2, 3], 'custom': {'ship': 7}}))
        header = ','.join(AzurStats.meowofficer_farming_labels)
        self.csv_raw = (header + '\n' + '\n'.join(f'{i},1800000000,1,20,2,0,0' for i in range(1, 7)) + '\n').encode('utf-8')
        self.csv.write_bytes(self.csv_raw)
        now = datetime.now().replace(microsecond=0).isoformat(' ')
        daily = self.root / 'config' / 'daily_summary.db'
        # 独立旧工具用例直接种入旧格式，不能经过已迁移的运行适配器写原件。
        with closing(sqlite3.connect(daily)) as conn, conn:
            conn.execute('CREATE TABLE daily_summary_cl1_events(id INTEGER PRIMARY KEY,instance TEXT,ts TEXT,'
                         'duration_seconds REAL,estimated_exp INTEGER,secure_payload TEXT)')
            conn.execute('INSERT INTO daily_summary_cl1_events VALUES(1,?,?,?,?,NULL)', ('inst', now, 23.5, 1871))
            conn.execute("CREATE TABLE daily_summary_periods(id INTEGER PRIMARY KEY,instance TEXT,period_key TEXT,report_text TEXT,"
                         "server TEXT DEFAULT 'cn',window_start TEXT DEFAULT '2026-10-05 00:00:00',"
                         "window_end TEXT DEFAULT '2026-10-06 00:00:00',status TEXT DEFAULT 'sent',"
                         "llm_attempts INTEGER DEFAULT 0,send_attempts INTEGER DEFAULT 0,error_kind TEXT,"
                         "created_at TEXT DEFAULT '2026-10-05 00:00:00',updated_at TEXT DEFAULT '2026-10-05 00:00:00')")
            conn.execute('INSERT INTO daily_summary_periods(instance,period_key,report_text) VALUES(?,?,?)',
                         ('inst', '2026-10-05', '完整日报：23.5 秒，1871 经验'))
        # 首次加密前的原始备份应恢复成完全相同的字节。
        archive = self.root / 'AzurPilot_Data_Backup' / '2026-10-01' / 'cl1_data.db'
        archive.parent.mkdir(parents=True)
        shutil.copy2(self.cl1, archive)
        self.archive_raw = archive.read_bytes()
        self.archive = archive
        vault = opsi_secure.Vault(self.root, provider=self.provider, background_migration=False, deep_check=False)
        opsi_secure.set_vault(vault)
        self.assertTrue(vault.ensure_ready())
        self.encrypted = vault
        opsi_secure.set_vault(self.store)

    def test_default_factory_uses_plain_store(self):
        with patch.object(opsi_secure, '_VAULT', None), patch(
                'module.statistics.opsi_plain.PlainStatisticsStore', return_value=self.store) as factory:
            self.assertIs(opsi_secure.get_vault(), self.store)
            factory.assert_called_once_with()

    def test_new_store_does_not_access_credentials(self):
        with patch('module.statistics.opsi_secure.get_provider', side_effect=AssertionError('不能访问凭据')):
            store = PlainStatisticsStore(self.root)
            self.assertTrue(store.writer_ready())
            self.assertFalse(store.encrypted)
            self.assertFalse(store.keyring_path.exists())

    def test_native_cl1_writes_complete_snapshot(self):
        database = Cl1Database(self.cl1)
        database.increment_battle_count('inst', 3)
        database.add_ap_snapshot('inst', 131, source='cl1')
        database.add_commission_income('inst', {'Gem': 5})
        data = database.get_stats('inst', datetime.now().strftime('%Y-%m'))
        self.assertEqual(data['battle_count'], 3)
        self.assertEqual(data['ap_snapshots'][0]['ap'], 131)
        self.assertEqual(len(data['commission_income_entries']), 1)
        self.assertEqual(self.sql(database.db_path, 'SELECT battle_count FROM cl1_months'), [(3,)])
        self.assertFalse(self.cl1.exists())

    def test_loot_writes_business_columns(self):
        row = {'imgid': 'img', 'device_id': 'dev', 'instance': 'inst', 'genre': 'opsi_abyssal',
               'created_at': 123, 'server': 'cn', 'zone': '海域', 'zone_type': 'abyssal',
               'zone_id': 5, 'hazard_level': 6, 'item': 'PlateGeneralT4', 'amount': 3,
               'tag': 'gold', 'combat_count': 2}
        self.assertEqual(AzurStats._insert_local_opsi_items([row]), 1)
        self.assertEqual(self.sql(AzurStats._database().path, 'SELECT item,amount,hazard_level FROM opsi_items'),
                         [('PlateGeneralT4', 3, 6)])
        data = AzurStats._load_local_opsi_items(genre='opsi_abyssal', instance='inst')
        self.assertEqual(data[0]['amount'], 3)

    def test_resource_writes_plain_values(self):
        self.assertTrue(resource_stats.record_resource_snapshot('inst', {'Oil': 500, 'ActionPoint': 131,
                                                                       'YellowCoin': 777, 'PurpleCoin': 45}))
        self.assertEqual(self.sql(self.root / 'config' / 'azurpilot.db', 'SELECT oil,action_point,yellow_coin,purple_coin FROM resource_snapshots'),
                         [(500, 131, 777, 45)])
        self.assertEqual(resource_stats.get_resource_timeline('inst')[0]['action_point'], 131)

    def test_native_ship_snapshot_roundtrip(self):
        stats = ShipExpStats(self.ship, 'inst')
        stats.data = {'battle_times': [23.5], 'custom': {'keep': True}}
        stats._save()
        self.assertFalse(self.ship.exists())
        self.assertEqual(ShipExpStats(self.ship, 'inst').data, stats.data)

    def test_native_farming_cache_remains_readable_without_csv(self):
        data = np.zeros((6, len(AzurStats.meowofficer_farming_labels)))
        data[:, 0] = np.arange(1, 7)
        data[0, 3] = 17
        AzurStats._write_meowofficer_farming(data)
        self.assertFalse(self.csv.exists())
        np.testing.assert_array_equal(AzurStats.load_meowofficer_farming(), data)

    def test_daily_events_and_report_are_plain(self):
        store = DailySummaryStore(self.root / 'config' / 'daily_summary.db')
        now = datetime.now().replace(microsecond=0)
        store.record_cl1_battle_event('inst', now, 23.5, 1871)
        self.assertEqual(self.sql(store.db_path, 'SELECT duration_seconds,estimated_exp FROM daily_summary_cl1_events'),
                         [(23.5, 1871)])
        summary = store.get_cl1_interval_summary('inst', now, now + timedelta(hours=1))
        self.assertEqual(summary['estimated_exp'], 1871)
        self.assertEqual(summary['battles'], 1)
        self.assertTrue(store.claim_period('inst', '2026-10-05', 'cn', now, now + timedelta(days=1)))
        store.update_period('inst', '2026-10-05', 'sent', report_text='普通日报正文')
        self.assertEqual(store.get_period('inst', '2026-10-05')['report_text'], '普通日报正文')
        self.assertFalse(store.claim_period('inst', '2026-10-05', 'cn', now, now + timedelta(days=1)))

    def test_plain_transaction_rolls_back_on_error(self):
        database = Cl1Database(self.cl1)
        database.increment_battle_count('inst', 3)
        with self.assertRaises(RuntimeError), database._stats_transaction() as conn:
            conn.execute('DELETE FROM cl1_months')
            raise RuntimeError('故障')
        self.assertEqual(database.get_stats('inst', datetime.now().strftime('%Y-%m'))['battle_count'], 3)

    def test_plain_seal_rejects_accidental_encryption(self):
        with self.assertRaises(opsi_secure.VaultError):
            self.store.seal('cl1', {'battle_count': 1})

    def test_atomic_write_handles_long_statistics_filenames(self):
        folder = self.root / ('directory-' + 'd' * 55)
        length = 245 - len(str(folder)) - 1 - len('.json')
        target = folder / ('s' * length + '.json')
        durable_write(target, b'{"preserved":true}')
        self.assertEqual(target.read_bytes(), b'{"preserved":true}')
        durable_write(target, b'{"preserved":false}')
        self.assertEqual(target.read_bytes(), b'{"preserved":false}')
        self.assertEqual(list(folder.glob('*.stage')), [])

    def test_v2_migration_preserves_every_dataset(self):
        self.configure_encrypted()
        ring = self.encrypted.keyring_path.read_bytes()
        installation = self.encrypted._state['installation_id']
        provider_state = self.provider.load(self.encrypted.slot)
        self.assertTrue(self.store.ensure_ready())
        data, blob, legacy = self.sql(self.cl1, 'SELECT data_json,secure_json,encrypted_blob FROM cl1_data')[0]
        self.assertEqual(json.loads(data), self.full)
        self.assertIsNone(blob)
        self.assertIsNone(legacy)
        self.assertEqual(self.sql(self.local, 'SELECT item,amount,hazard_level,secure_payload FROM opsi_items'),
                         [('PlateGeneralT4', 3, 6, None)])
        self.assertEqual(self.sql(self.local, 'SELECT action_point,yellow_coin,purple_coin,opsi_payload FROM resource_snapshots'),
                         [(131, 500, 20, None)])
        self.assertEqual(json.loads(self.ship.read_bytes()), {'battle_times': [1, 2, 3], 'custom': {'ship': 7}})
        self.assertEqual(self.csv.read_bytes(), self.csv_raw)
        self.assertEqual(self.archive.read_bytes(), self.archive_raw)
        daily = self.root / 'config' / 'daily_summary.db'
        self.assertEqual(self.sql(daily, 'SELECT duration_seconds,estimated_exp,secure_payload FROM daily_summary_cl1_events'),
                         [(23.5, 1871, None)])
        self.assertEqual(self.sql(daily, 'SELECT report_text FROM daily_summary_periods'), [('完整日报：23.5 秒，1871 经验',)])
        for path in (self.cl1, self.local, daily):
            self.assertEqual(self.sql(path, "SELECT name FROM sqlite_master WHERE name LIKE '__opsi_%'"), [])
        self.assertEqual(self.encrypted.keyring_path.read_bytes(), ring)
        self.assertEqual(self.provider.load(self.encrypted.slot)['installation_id'], installation)
        self.assertEqual(self.provider.load(self.encrypted.slot), provider_state)
        self.assertEqual(self.store._sources(), [])

    def test_changed_integrity_chain_keeps_all_originals(self):
        self.configure_encrypted()
        with closing(sqlite3.connect(self.cl1)) as conn, conn:
            conn.execute('UPDATE __opsi_integrity SET seq=seq+1')
        before = self.sql(self.cl1, 'SELECT data_json,secure_json FROM cl1_data')
        state = self.provider.load(self.encrypted.slot)
        self.assertFalse(self.store.ensure_ready())
        self.assertEqual(self.sql(self.cl1, 'SELECT data_json,secure_json FROM cl1_data'), before)
        self.assertEqual(self.provider.load(self.encrypted.slot), state)
        self.assertFalse((self.store.directory / 'blocked.json').exists())

    def test_unknown_columns_tables_and_business_triggers_are_preserved(self):
        self.configure_encrypted()
        with closing(sqlite3.connect(self.cl1)) as conn, conn:
            conn.execute('ALTER TABLE cl1_data ADD COLUMN "custom-blob" BLOB')
            conn.execute('UPDATE cl1_data SET "custom-blob"=?', (b'custom-private-bytes',))
            conn.execute('CREATE TABLE custom_audit (updates INTEGER)')
            conn.execute('INSERT INTO custom_audit VALUES (7)')
            conn.execute('CREATE TRIGGER custom_business_update AFTER UPDATE ON cl1_data '
                         'BEGIN UPDATE custom_audit SET updates=updates+1; END')
        self.encrypted.revouch(self.cl1)
        self.assertTrue(self.store.ensure_ready())
        self.assertEqual(self.sql(self.cl1, 'SELECT "custom-blob" FROM cl1_data'), [(b'custom-private-bytes',)])
        self.assertEqual(self.sql(self.cl1, 'SELECT updates FROM custom_audit'), [(7,)])
        with closing(sqlite3.connect(self.cl1)) as conn, conn:
            conn.execute('UPDATE cl1_data SET data_json=data_json')
        self.assertEqual(self.sql(self.cl1, 'SELECT updates FROM custom_audit'), [(8,)])

    def test_unknown_encrypted_fields_are_not_silently_discarded(self):
        self.configure_encrypted()
        with closing(sqlite3.connect(self.local)) as conn, self.encrypted.transaction(conn, self.local):
            conn.row_factory = sqlite3.Row
            row = dict(conn.execute('SELECT * FROM opsi_items').fetchone())
            payload = self.encrypted.open_('loot', row['secure_payload'], opsi_secure.row_context('loot', row))
            payload['custom_extra'] = [1, 2, 3]
            blob = self.encrypted.seal('loot', payload, opsi_secure.row_context('loot', row))
            conn.execute('UPDATE opsi_items SET secure_payload=? WHERE id=?', (blob, row['id']))
        before = self.sql(self.local, 'SELECT secure_payload FROM opsi_items')
        self.assertFalse(self.store.ensure_ready())
        self.assertEqual(self.sql(self.local, 'SELECT secure_payload FROM opsi_items'), before)

    def test_new_backup_is_ordinary_sqlite(self):
        Cl1Database(self.cl1).increment_battle_count('inst', 3)
        folder = self.root / 'AzurPilot_Data_Backup' / '2026-10-05'
        folder.mkdir(parents=True)
        files = backup_module.backup_database(folder)
        self.assertIn('azurpilot.db', [entry['name'] for entry in files])
        target = folder / 'azurpilot.db'
        self.assertEqual(target.read_bytes()[:16], b'SQLite format 3\x00')
        self.assertEqual(self.sql(target, 'SELECT battle_count FROM cl1_months'), [(3,)])
        backup_module.backup_config(folder)
        self.assertFalse((folder / 'opsi_secure').exists())

    def test_encrypted_backup_with_encrypted_rows_is_fully_restored(self):
        self.configure_encrypted()
        archive = self.root / 'AzurPilot_Data_Backup' / '2026-10-02' / 'cl1_data.db'
        archive.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.cl1)) as conn:
            raw = self.encrypted._standalone_image(conn.serialize())
        self.encrypted.write_file('archives', archive, {'bytes': base64.b64encode(raw).decode()}, wrapper=True)
        self.assertTrue(self.store.ensure_ready())
        data, blob = self.sql(archive, 'SELECT data_json,secure_json FROM cl1_data')[0]
        self.assertEqual(json.loads(data), self.full)
        self.assertIsNone(blob)

    def test_archived_legacy_aes_bytes_are_preserved_exactly(self):
        self.configure_encrypted()
        with closing(sqlite3.connect(':memory:')) as conn:
            conn.deserialize(self.archive_raw)
            conn.execute('INSERT INTO cl1_data(instance,month,data_json,encrypted_blob) VALUES(?,?,NULL,?)',
                         ('inst', '2026-02', b'unknown-historical-AES'))
            conn.commit()
            raw = self.encrypted._standalone_image(conn.serialize())
        self.encrypted.write_file('archives', self.archive, {'bytes': base64.b64encode(raw).decode()}, wrapper=True)
        self.assertTrue(self.store.ensure_ready())
        self.assertEqual(self.archive.read_bytes(), raw)
        self.assertEqual(self.sql(self.archive, "SELECT encrypted_blob FROM cl1_data WHERE month='2026-02'"),
                         [(b'unknown-historical-AES',)])

    def test_mixed_v2_and_legacy_aes_archive_preserves_opaque_old_rows(self):
        self.configure_encrypted()
        with closing(sqlite3.connect(self.cl1)) as conn:
            raw = self.encrypted._standalone_image(conn.serialize())
        with closing(sqlite3.connect(':memory:')) as conn:
            conn.deserialize(raw)
            conn.execute('INSERT INTO cl1_data(instance,month,data_json,encrypted_blob) VALUES(?,?,NULL,?)',
                         ('inst', '2026-02', b'unknown-historical-AES'))
            conn.commit()
            raw = conn.serialize()
        self.encrypted.write_file('archives', self.archive, {'bytes': base64.b64encode(raw).decode()}, wrapper=True)
        self.assertTrue(self.store.ensure_ready())
        self.assertEqual(self.sql(self.archive, "SELECT encrypted_blob FROM cl1_data WHERE month='2026-02'"),
                         [(b'unknown-historical-AES',)])
        data, blob = self.sql(self.archive, "SELECT data_json,secure_json FROM cl1_data WHERE month='2026-09'")[0]
        self.assertEqual(json.loads(data), self.full)
        self.assertIsNone(blob)

    def test_offline_migration_keeps_all_ciphertext(self):
        self.configure_encrypted()
        before = self.sql(self.cl1, 'SELECT data_json,secure_json FROM cl1_data')
        ship = self.ship.read_bytes()
        self.provider.offline = True
        self.assertFalse(self.store.writer_ready())
        self.assertEqual(self.sql(self.cl1, 'SELECT data_json,secure_json FROM cl1_data'), before)
        self.assertEqual(self.ship.read_bytes(), ship)
        Cl1Database(self.cl1).increment_battle_count('inst', 99)
        self.assertEqual(self.sql(self.cl1, 'SELECT data_json,secure_json FROM cl1_data'), before)
        self.provider.offline = False
        self.assertTrue(self.store.writer_ready())
        self.assertEqual(json.loads(self.sql(self.cl1, 'SELECT data_json FROM cl1_data')[0][0]), self.full)

    def test_corrupt_payload_never_publishes_partial_plaintext(self):
        self.configure_encrypted()
        with closing(sqlite3.connect(self.cl1)) as conn, conn:
            blob = conn.execute('SELECT secure_json FROM cl1_data').fetchone()[0]
            conn.execute('UPDATE cl1_data SET secure_json=?', (blob[:-8] + 'AAAAAAAA',))
        before = self.sql(self.cl1, 'SELECT data_json,secure_json FROM cl1_data')
        ship = self.ship.read_bytes()
        self.assertFalse(self.store.ensure_ready())
        self.assertEqual(self.sql(self.cl1, 'SELECT data_json,secure_json FROM cl1_data'), before)
        self.assertEqual(self.ship.read_bytes(), ship)

    def test_publish_failure_restores_all_original_files(self):
        self.configure_encrypted()
        before = self.sql(self.cl1, 'SELECT data_json,secure_json FROM cl1_data')
        ship = self.ship.read_bytes()
        publish = self.store._publish
        attempts = []

        def fail_second(backup, entry, version):
            if version == 'plain':
                attempts.append(entry)
                if len(attempts) == 2:
                    raise PermissionError('模拟文件占用')
            return publish(backup, entry, version)

        with patch.object(self.store, '_publish', side_effect=fail_second):
            self.assertFalse(self.store.ensure_ready())
        self.assertEqual(self.sql(self.cl1, 'SELECT data_json,secure_json FROM cl1_data'), before)
        self.assertEqual(self.ship.read_bytes(), ship)
        self.assertFalse(self.store.transition_path.exists())
        self.assertTrue(self.store.ensure_ready())

    def test_interrupted_publish_is_rolled_back_before_reading(self):
        self.configure_encrypted()
        before = self.sql(self.cl1, 'SELECT data_json,secure_json FROM cl1_data')
        ship = self.ship.read_bytes()
        self.assertTrue(self.store.ensure_ready())
        mode = json.loads(self.store.mode_path.read_bytes())
        backup = self.store.directory / mode['backup']
        manifest = json.loads((backup / 'manifest.json').read_bytes())
        durable_write(self.store.transition_path, canonical(manifest))
        self.provider.offline = True
        resumed = PlainStatisticsStore(self.root, provider=self.provider)
        self.assertFalse(resumed.ensure_ready())
        self.assertEqual(self.sql(self.cl1, 'SELECT data_json,secure_json FROM cl1_data'), before)
        self.assertEqual(self.ship.read_bytes(), ship)
        self.assertFalse(resumed.transition_path.exists())

    def test_converted_data_works_in_new_directory_without_credentials(self):
        self.configure_encrypted()
        self.assertTrue(self.store.ensure_ready())
        self.provider.offline = True
        other = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(other.cleanup)
        target = Path(other.name)
        shutil.copytree(self.root / 'config', target / 'config')
        shutil.copytree(self.root / 'log', target / 'log')
        store = PlainStatisticsStore(target, provider=self.provider)
        opsi_secure.set_vault(store)
        self.assertTrue(store.writer_ready())
        self.assertEqual(Cl1Database(target / 'config' / 'cl1_data.db').get_stats('inst', '2026-09'), self.full)
        self.assertEqual(ShipExpStats(target / 'log' / 'cl1' / 'inst' / 'ship_exp_data.json', 'inst').data['custom']['ship'], 7)

    def test_v1_migration_retains_history(self):
        full = make_cl1_db(self.cl1)
        key = b'x' * 32
        legacy_ring(self.root, key)
        public, secure = opsi_secure.partition_cl1(full)
        with closing(sqlite3.connect(self.cl1)) as conn, conn:
            conn.execute('ALTER TABLE cl1_data ADD COLUMN secure_json TEXT')
            conn.execute('UPDATE cl1_data SET data_json=?,secure_json=?', (canonical(public).decode(), legacy_blob(key, 'cl1', secure)))
        self.provider.offline = False
        with patch.object(opsi_secure.Vault, '_dpapi', side_effect=lambda raw, decrypt=False: raw):
            self.assertTrue(self.store.ensure_ready())
        self.assertEqual(json.loads(self.sql(self.cl1, 'SELECT data_json FROM cl1_data')[0][0]), full)

    def test_unreadable_legacy_aes_is_not_deleted(self):
        make_cl1_db(self.cl1)
        with closing(sqlite3.connect(self.cl1)) as conn, conn:
            conn.execute('UPDATE cl1_data SET data_json=NULL,encrypted_blob=?', (b'unknown-old-AES',))
        with patch('module.base.device_id.get_device_id', return_value=None), patch(
                'module.base.device_id.get_old_device_id', return_value=None):
            self.assertFalse(self.store.ensure_ready())
        self.assertEqual(self.sql(self.cl1, 'SELECT encrypted_blob FROM cl1_data'), [(b'unknown-old-AES',)])

    def test_rollback_manifest_cannot_write_outside_statistics(self):
        with self.assertRaises(opsi_secure.VaultLocked):
            self.store._rollback({'backup': '../../escape', 'files': []})

    def test_cross_process_increments_preserve_every_update(self):
        Cl1Database(self.cl1).increment_battle_count('inst', 1)
        code = '''
import sys
from pathlib import Path
from module.statistics import opsi_secure
from module.statistics.opsi_plain import PlainStatisticsStore
from module.statistics.cl1_database import Cl1Database
root = Path(sys.argv[1])
opsi_secure.set_vault(PlainStatisticsStore(root))
Cl1Database._auto_migrate = lambda self: None
db = Cl1Database(root / 'config' / 'cl1_data.db')
for _ in range(20):
    db.increment_battle_count('inst')
'''
        processes = [subprocess.Popen([sys.executable, '-c', code, str(self.root)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                     for _ in range(2)]
        for process in processes:
            _, errors = process.communicate(timeout=60)
            self.assertEqual(process.returncode, 0, errors.decode('utf-8', errors='replace'))
        self.assertEqual(Cl1Database(self.cl1).get_stats('inst', datetime.now().strftime('%Y-%m'))['battle_count'], 41)


if __name__ == '__main__':
    unittest.main()


class UpstreamPlainCompatibility(unittest.TestCase):
    """上游普通 JSON 载荷与本地普通业务列/安全迁移的融合回归。"""

    setUp = PlainStatisticsCase.setUp
    sql = PlainStatisticsCase.sql
    configure_encrypted = PlainStatisticsCase.configure_encrypted

    def test_plain_payload_and_business_columns_merge_without_credentials(self):
        full = make_cl1_db(self.cl1)
        full['custom_extension'] = {'keep': [1, 2, 3]}
        with closing(sqlite3.connect(self.cl1)) as conn, conn:
            conn.execute('ALTER TABLE cl1_data ADD COLUMN secure_json TEXT')
            conn.execute('UPDATE cl1_data SET data_json=?,secure_json=?',
                         (canonical(full).decode(), canonical({'battle_count': 137, 'coins_month_start_residue': {'yellow': 7}}).decode()))
        with patch.object(self.provider, 'load', side_effect=AssertionError('普通 JSON 不应访问凭据')):
            self.assertTrue(self.store.ensure_ready())
            restored = Cl1Database(self.cl1).get_stats('inst', '2026-09')
        self.assertEqual(restored['battle_count'], 137)
        self.assertEqual(restored['custom_extension'], {'keep': [1, 2, 3]})
        self.assertEqual(restored['coins_month_start_residue'], {'yellow': 7})
        self.assertEqual(self.sql(self.cl1, 'SELECT secure_json FROM cl1_data'), [(None,)])

    def test_plain_resource_payload_restores_all_original_points(self):
        make_loot_db(self.local)
        now = datetime.now().isoformat()
        with closing(sqlite3.connect(self.local)) as conn, conn:
            conn.execute('ALTER TABLE resource_snapshots ADD COLUMN opsi_payload TEXT')
            conn.execute('DELETE FROM resource_snapshots')
            for i in range(11):
                conn.execute('INSERT INTO resource_snapshots(instance,ts,oil,action_point,yellow_coin,purple_coin,opsi_payload) '
                             'VALUES (?,?,?,?,?,?,?)', ('inst', now, i, None, None, None,
                             canonical({'action_point': 31+i, 'yellow_coin': 97+i, 'purple_coin': 11+i}).decode()))
        self.assertTrue(self.store.ensure_ready())
        rows = self.sql(self.local, 'SELECT oil,action_point,yellow_coin,purple_coin,opsi_payload FROM resource_snapshots ORDER BY id')
        self.assertEqual(len(rows), 11)
        self.assertEqual(rows[-1], (10, 41, 107, 21, None))

    def test_cross_directory_credential_recovery_is_read_only(self):
        self.configure_encrypted()
        old_states = json.loads(json.dumps(self.provider.states))
        moved = self.root / 'relocated'
        moved.mkdir()
        shutil.copytree(self.root / 'config', moved / 'config')
        shutil.copytree(self.root / 'log', moved / 'log')
        shutil.copytree(self.root / 'AzurPilot_Data_Backup', moved / 'AzurPilot_Data_Backup')
        saved = next(iter(old_states.values()))
        with patch.object(self.provider, 'load_any', return_value=saved) as lookup:
            relocated = PlainStatisticsStore(moved, provider=self.provider)
            self.assertTrue(relocated.ensure_ready())
        self.assertTrue(lookup.called)
        self.assertEqual(self.provider.states, old_states)
        self.assertTrue((moved / 'config' / 'opsi_secure' / 'keyring.json').exists())
        data = json.loads(self.sql(moved / 'config' / 'cl1_data.db', 'SELECT data_json FROM cl1_data')[0][0])
        self.assertEqual(data, self.full)

    def test_unreadable_plain_payload_never_overwrites_original(self):
        make_cl1_db(self.cl1)
        with closing(sqlite3.connect(self.cl1)) as conn, conn:
            conn.execute('ALTER TABLE cl1_data ADD COLUMN secure_json TEXT')
            conn.execute('UPDATE cl1_data SET secure_json=?', ('{damaged',))
        before = self.cl1.read_bytes()
        self.assertFalse(self.store.ensure_ready())
        from module.persistence.migration import MigrationError
        with self.assertRaises(MigrationError):
            Cl1Database(self.cl1).increment_battle_count('inst', 1)
        self.assertEqual(self.cl1.read_bytes(), before)

    def test_unified_decoders_accept_plaintext_and_legacy_wrapper(self):
        self.assertEqual(opsi_secure.decode_record('cl1', '{"battle_count":9}'), {'battle_count': 9})
        self.assertEqual(opsi_secure.decode_file_payload('ships', self.ship,
                         {opsi_secure.WRAPPER_KEY: True, 'payload': {'custom': 7}}), {'custom': 7})
        self.assertIsNone(opsi_secure.decode_record('cl1', '{damaged'))
        self.assertEqual(opsi_secure.decode_text('普通日报'), '普通日报')

    def test_initialize_is_bounded_and_concurrent_calls_share_worker(self):
        start = threading.Event()
        release = threading.Event()
        calls = []

        def wait_for_migration():
            calls.append(True)
            start.set()
            release.wait(3)
            return True

        with patch.object(self.store, 'ensure_ready', side_effect=wait_for_migration), \
                patch.object(opsi_secure, '_INIT_THREAD', None), \
                patch.object(opsi_secure, '_INIT_DONE', None), \
                patch.object(opsi_secure, '_INIT_ROOT', None):
            before = time.monotonic()
            self.assertFalse(opsi_secure.initialize(timeout=0.02))
            self.assertLess(time.monotonic() - before, 1.0)
            self.assertTrue(start.wait(1))
            threads = [threading.Thread(target=lambda: opsi_secure.initialize(timeout=0.02)) for _ in range(7)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(1)
            self.assertEqual(len(calls), 1)
            release.set()
            self.assertTrue(opsi_secure.initialize(timeout=1))

    def test_unified_plain_write_keeps_csv_and_long_path_semantics(self):
        csv = 'a,b\n1,2\n'
        opsi_secure.write_file('loot', self.csv, csv)
        self.assertEqual(self.csv.read_text(encoding='utf-8'), csv)
        self.assertTrue((self.root / 'config' / 'opsi_secure' / 'plaintext.json').exists())
        self.assertEqual(self.provider.states, {})


    def test_late_legacy_payload_is_preserved_without_reimport(self):
        make_cl1_db(self.cl1)
        database = Cl1Database(self.cl1)
        database.increment_battle_count('inst', 3)
        month = datetime.now().strftime('%Y-%m')
        for value in ('OPSIV2.XCHACHA20-POLY1305.corrupt', '{damaged'):
            with self.subTest(value=value):
                with closing(sqlite3.connect(self.cl1)) as conn, conn:
                    if not conn.execute("SELECT 1 FROM pragma_table_info('cl1_data') WHERE name='secure_json'").fetchone():
                        conn.execute('ALTER TABLE cl1_data ADD COLUMN secure_json TEXT')
                    conn.execute('UPDATE cl1_data SET secure_json=?', (value,))
                original = self.sql(self.cl1, 'SELECT data_json,secure_json,encrypted_blob FROM cl1_data')
                displayed = database.get_stats('inst', month)
                database.save_stats('inst', month, displayed)
                self.assertEqual(self.sql(self.cl1, 'SELECT data_json,secure_json,encrypted_blob FROM cl1_data'), original)
                database.increment_battle_count('inst', 1)
                self.assertEqual(self.sql(self.cl1, 'SELECT data_json,secure_json,encrypted_blob FROM cl1_data'), original)

    def test_late_legacy_aes_remains_untouched_by_native_save(self):
        make_cl1_db(self.cl1)
        database = Cl1Database(self.cl1)
        database.increment_battle_count('inst', 3)
        month = datetime.now().strftime('%Y-%m')
        with closing(sqlite3.connect(self.cl1)) as conn, conn:
            conn.execute('UPDATE cl1_data SET encrypted_blob=?,data_json=NULL', (b'opaque AES source',))
        original = self.sql(self.cl1, 'SELECT data_json,encrypted_blob FROM cl1_data')
        database.save_stats('inst', month, {'battle_count': 0})
        self.assertEqual(self.sql(self.cl1, 'SELECT data_json,encrypted_blob FROM cl1_data'), original)

    def test_late_cipher_file_is_not_overwritten(self):
        self.assertTrue(self.store.ensure_ready())
        original = canonical({opsi_secure.WRAPPER_KEY: True, 'payload': 'OPSIV2.XCHACHA20-POLY1305.corrupt'})
        self.ship.write_bytes(original)
        with self.assertRaises(opsi_secure.StoreUnavailable):
            opsi_secure.write_file('ships', self.ship, {'replacement': 0})
        self.assertEqual(self.ship.read_bytes(), original)

    def test_late_legacy_cipher_report_is_not_overwritten(self):
        summary = DailySummaryStore(self.root / 'config' / 'daily_summary.db')
        now = datetime.now().replace(microsecond=0)
        period = now.strftime('%Y-%m-%d')
        summary.claim_period('inst', period, 'cn', now, now + timedelta(days=1))
        original = 'OPSIV2.XCHACHA20-POLY1305.corrupt'
        legacy = self.root / 'config' / 'daily_summary.db'
        with closing(sqlite3.connect(legacy)) as conn, conn:
            conn.execute('CREATE TABLE daily_summary_periods(report_text TEXT)')
            conn.execute('INSERT INTO daily_summary_periods VALUES(?)', (original,))
        summary.update_period('inst', period, 'sent', report_text='替换正文')
        self.assertEqual(self.sql(legacy, 'SELECT report_text FROM daily_summary_periods'), [(original,)])
        self.assertEqual(summary.get_period('inst', period)['report_text'], '替换正文')
