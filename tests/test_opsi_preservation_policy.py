"""校验异常冻结、旧版汇总和本地完整历史的加密兼容验证。"""
import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import numpy as np
import portalocker

from module.statistics import opsi_secure
from module.statistics.azurstats import AzurStats
from module.statistics.cl1_database import Cl1Database
from tests.opsi_test_support import install_vault
from tests.test_opsi_secure import make_cl1_db


class OpsiPreservationPolicyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.vault = install_vault(self, self.root)
        self.provider = self.vault.provider
        self.path = self.root / 'config' / 'cl1_data.db'
        self.original_data = make_cl1_db(self.path)

    def test_descriptor_mismatch_preserves_files_credentials_and_blocks_loaded_reader(self):
        self.assertTrue(self.vault.ensure_ready())
        second = opsi_secure.Vault(self.root, provider=self.provider, background_migration=False)
        self.assertTrue(second.ensure_ready())
        before = self.path.read_bytes()
        state = copy.deepcopy(self.provider.states)
        with closing(sqlite3.connect(self.path)) as conn:
            blob = conn.execute('SELECT secure_json FROM cl1_data').fetchone()[0]
        context = opsi_secure.row_context('cl1', {'instance': 'inst', 'month': '2026-09'})
        self.vault.keyring_path.write_bytes(b'{}')
        # 已加载的对象也须在下一批读取时发现异常。
        with self.assertRaises(opsi_secure.VaultLocked):
            with second.reading():
                second.open_('cl1', blob, context)
        self.assertTrue(second.status()['blocked'])
        with self.assertRaises(opsi_secure.VaultLocked):
            self.vault.open_('cl1', blob, context)
        restarted = opsi_secure.Vault(self.root, provider=self.provider, background_migration=False)
        self.assertFalse(restarted.ensure_ready())
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(state, self.provider.states)
        self.assertEqual(b'{}', self.vault.keyring_path.read_bytes())
        self.assertFalse(self.vault.wipe_path.exists())

    def test_loaded_reader_cannot_decrypt_during_provider_outage(self):
        self.assertTrue(self.vault.ensure_ready())
        before = self.path.read_bytes()
        self.provider.offline = True
        with self.assertRaises(opsi_secure.VaultLocked):
            with self.vault.reading():
                self.fail('凭据不可用时不能进入读取正文')
        self.assertFalse(self.vault.status()['blocked'])
        self.assertEqual(before, self.path.read_bytes())
        self.provider.offline = False
        self.assertTrue(self.vault.ensure_ready())

    def test_competing_writer_lock_does_not_fail_or_freeze_readers(self):
        self.assertTrue(self.vault.ensure_ready())
        second = opsi_secure.Vault(self.root, provider=self.provider, background_migration=False)
        self.assertTrue(second.ensure_ready())
        with closing(sqlite3.connect(self.path)) as conn:
            blob = conn.execute('SELECT secure_json FROM cl1_data').fetchone()[0]
        context = opsi_secure.row_context('cl1', {'instance': 'inst', 'month': '2026-09'})

        class Reader:
            db_path = self.path

            @opsi_secure.checked_read
            def read(self):
                return opsi_secure.get_vault().open_or_none('cl1', blob, context)

        opsi_secure.set_vault(second)
        reader = Reader()
        expected = reader.read()
        before = self.path.read_bytes()
        descriptor = self.vault.keyring_path.read_bytes()
        state = copy.deepcopy(self.provider.states)
        original_lock = portalocker.Lock

        def immediate_lock(*args, **kwargs):
            kwargs['timeout'] = 0
            return original_lock(*args, **kwargs)

        # 使用真实争用的文件锁，缩短测试超时；不把暂时忙碌误判为数据损坏。
        with self.vault.coordinator.lock(), patch.object(portalocker, 'Lock', side_effect=immediate_lock):
            self.assertIsNone(reader.read())
        self.assertEqual(expected, reader.read())
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(descriptor, self.vault.keyring_path.read_bytes())
        self.assertEqual(state, self.provider.states)
        self.assertFalse(second.status()['blocked'])

    def test_old_wiping_phase_freezes_remaining_originals(self):
        self.assertTrue(self.vault.ensure_ready())
        self.provider.states[self.vault.slot]['phase'] = 'wiping'
        before = self.path.read_bytes()
        state = copy.deepcopy(self.provider.states)
        descriptor = self.vault.keyring_path.read_bytes()
        self.assertFalse(self.vault.ensure_ready())
        self.assertTrue(self.vault.status()['blocked'])
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(state, self.provider.states)
        self.assertEqual(descriptor, self.vault.keyring_path.read_bytes())

    def test_month_residue_and_all_original_points_migrate_without_loss(self):
        points = [{'ts': f'2026-09-01T00:{minute:02d}:00', 'yellow_coins': 500 + minute,
                   'purple_coins': 20} for minute in (1, 2, 3, 4, 5, 6)]
        full = dict(self.original_data, coins_snapshots=points[2:], coins_month_start_residue=points[:2])
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute('UPDATE cl1_data SET data_json = ?', (json.dumps(full),))
        self.assertTrue(self.vault.ensure_ready())
        with closing(sqlite3.connect(self.path)) as conn:
            public, sealed = conn.execute('SELECT data_json, secure_json FROM cl1_data').fetchone()
        self.assertNotIn('coins_month_start_residue', json.loads(public))
        self.assertNotIn('coins_snapshots', json.loads(public))
        self.assertTrue(sealed.startswith(opsi_secure.BLOB_PREFIX))
        stored = Cl1Database(self.path).get_stats('inst', '2026-09')
        self.assertEqual(stored['coins_month_start_residue'] + stored['coins_snapshots'], points)
        self.assertEqual(stored['commission_income_entries'], full['commission_income_entries'])

    def test_legacy_global_csv_remains_readable_after_migration(self):
        csv_path = self.root / 'log' / 'azurstat_meowofficer_farming.csv'
        csv_path.parent.mkdir()
        expected = np.arange(6 * len(AzurStats.meowofficer_farming_labels), dtype=float).reshape(
            6, len(AzurStats.meowofficer_farming_labels))
        np.savetxt(csv_path, expected, delimiter=',', header=','.join(AzurStats.meowofficer_farming_labels),
                   comments='')
        self.assertTrue(self.vault.ensure_ready())
        self.assertTrue(csv_path.read_text().startswith(opsi_secure.BLOB_PREFIX))
        with patch.object(AzurStats, 'LOCAL_MEOW_CSV', str(csv_path)):
            np.testing.assert_array_equal(AzurStats.load_meowofficer_farming(), expected)


if __name__ == '__main__':
    unittest.main()
