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
        second = opsi_secure.Vault(self.root, provider=self.provider, background_migration=False, deep_check=False)
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
        restarted = opsi_secure.Vault(self.root, provider=self.provider, background_migration=False, deep_check=False)
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
        second = opsi_secure.Vault(self.root, provider=self.provider, background_migration=False, deep_check=False)
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

    def test_shallow_chain_mismatch_preserves_all_remaining_data_and_credentials(self):
        self.assertTrue(self.vault.ensure_ready())
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("UPDATE __opsi_integrity SET chain='invalid'")
        before = self.path.read_bytes()
        state = copy.deepcopy(self.provider.states)
        descriptor = self.vault.keyring_path.read_bytes()
        self.vault.verify_on_page_open()
        self.assertTrue(self.vault.status()['blocked'])
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(state, self.provider.states)
        self.assertEqual(descriptor, self.vault.keyring_path.read_bytes())
        self.assertFalse(self.vault.wipe_path.exists())

    def test_deep_mismatch_preserves_ciphertext_and_credentials(self):
        self.assertTrue(self.vault.ensure_ready())
        self.vault.deep_check_once()
        context = opsi_secure.row_context('cl1', {'instance': 'inst', 'month': '2026-09'})
        blob = self.vault.seal('cl1', {'battle_count': 999}, context)
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute('UPDATE cl1_data SET secure_json=?', (blob,))
        before = self.path.read_bytes()
        state = copy.deepcopy(self.provider.states)
        self.vault.deep_check_once()
        self.assertTrue(self.vault.status()['blocked'])
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(state, self.provider.states)
        self.assertFalse(self.vault.wipe_path.exists())

    def test_bad_record_freezes_same_batch_before_background_rescue(self):
        self.assertTrue(self.vault.ensure_ready())
        context = opsi_secure.row_context('cl1', {'instance': 'inst', 'month': '2026-09'})
        with closing(sqlite3.connect(self.path)) as conn:
            good = conn.execute('SELECT secure_json FROM cl1_data').fetchone()[0]
        before = self.path.read_bytes()
        state = copy.deepcopy(self.provider.states)
        with self.vault.reading():
            self.assertIsNone(self.vault.open_or_none('cl1', good[:-1] + '!', context))
            self.assertTrue((self.vault.directory / 'blocked.json').exists())
            # 后台线程尚在等待当前协调锁，同批其他读取、写入也必须已被阻止。
            self.assertIsNone(self.vault.open_or_none('cl1', good, context))
            with self.assertRaises(opsi_secure.VaultLocked):
                self.vault.seal('cl1', {'battle_count': 1}, context)
        self.vault._wipe_thread.join(timeout=10)
        self.assertFalse(self.vault._wipe_thread.is_alive())
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(state, self.provider.states)
        restarted = opsi_secure.Vault(self.root, provider=self.provider, deep_check=False)
        self.assertFalse(restarted.ensure_ready())

    def test_page_check_with_competing_file_lock_does_not_freeze(self):
        self.assertTrue(self.vault.ensure_ready())
        before = self.path.read_bytes()
        with patch.object(self.vault.coordinator, 'lock', side_effect=portalocker.exceptions.LockException):
            self.vault.verify_on_page_open()
        self.assertEqual(before, self.path.read_bytes())
        self.assertFalse(self.vault.status()['blocked'])

    def test_bad_record_during_write_rolls_back_even_if_read_error_is_downgraded(self):
        self.assertTrue(self.vault.ensure_ready())
        before = self.path.read_bytes()
        state = copy.deepcopy(self.provider.states)
        context = opsi_secure.row_context('cl1', {'instance': 'inst', 'month': '2026-09'})
        with self.assertRaises(opsi_secure.VaultLocked):
            with closing(sqlite3.connect(self.path)) as conn, self.vault.transaction(conn, self.path):
                blob = conn.execute('SELECT secure_json FROM cl1_data').fetchone()[0]
                self.assertIsNone(self.vault.open_or_none('cl1', blob[:-1] + '!', context))
                conn.execute("UPDATE cl1_data SET data_json='{}'")
        self.vault._wipe_thread.join(timeout=10)
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(state, self.provider.states)
        self.assertTrue(self.vault.status()['blocked'])

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
        header = ','.join(AzurStats.meowofficer_farming_labels)
        # 历史导出文件本就使用 UTF-8；夹具必须遵守相同格式，避免被系统编码写成 GBK。
        np.savetxt(csv_path, expected, delimiter=',', header=header, comments='', encoding='utf-8')
        self.assertEqual(csv_path.read_bytes().splitlines()[0], header.encode('utf-8'))
        self.assertTrue(self.vault.ensure_ready())
        self.assertTrue(csv_path.read_text(encoding='utf-8').startswith(opsi_secure.BLOB_PREFIX))
        with patch.object(AzurStats, 'LOCAL_MEOW_CSV', str(csv_path)):
            np.testing.assert_array_equal(AzurStats.load_meowofficer_farming(), expected)


if __name__ == '__main__':
    unittest.main()
