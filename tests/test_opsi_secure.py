"""统计版本迁移、状态校验、事务恢复与故障隔离测试；全部数据位于临时目录。"""

import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
import base64
import hashlib
import hmac
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, closing

@contextmanager
def db(path):
    with closing(sqlite3.connect(path)) as conn, conn:
        yield conn

from module.statistics.opsi_keys import ContainerFileProvider, KeyProvider, ProviderUnavailable
from module.statistics.opsi_state import canonical

from module.statistics import cl1_database, opsi_secure

NOW = 1_800_000_000.0


def make_cl1_db(path):
    """构造旧版明文 cl1 库（含大世界战斗数据与委托、科研等非大世界字段）。"""
    conn = sqlite3.connect(path)
    conn.execute(
        'CREATE TABLE cl1_data (instance TEXT, month TEXT, data_json TEXT, '
        'encrypted_blob BLOB, PRIMARY KEY (instance, month))'
    )
    data = {
        'battle_count': 120,
        'akashi_encounters': 3,
        'akashi_ap': 60,
        'akashi_ap_entries': [{'ts': '2026-09-01T10:00:00', 'amount': 20, 'base': 10, 'count': 2, 'source': 'cl1'}],
        'ap_snapshots': [{'ts': '2026-09-01T10:00:00', 'ap': 131, 'asset': 7500.5, 'source': 'cl1'}],
        'last_ap_notification': {'ts': '2026-09-01T10:00:00', 'ap': 131},
        'yellow_coin_snapshots': [{'ts': '2026-09-01T10:00:00', 'yellow_coin': 500, 'source': 'cl1'}],
        'coins_snapshots': [{'ts': '2026-09-01T10:00:00', 'yellow_coins': 500, 'purple_coins': 20, 'source': 'cl1'}],
        'coins_history_version': 2,
        'coins_cleanup_version': 1,
        'meow_battle_raw_count': 10,
        'meow_battle_count': 5.0,
        'meow_round_times': [{'duration': 60.5, 'hazard_level': 3}],
        'meow_battle_times': [20.5],
        'meow_hazard_stats': {'3': {'battle_raw_count': 4, 'effective_rounds': 2.0, 'round_times': [60.0], 'battle_times': [20.0]}},
        'siren_research_devices': {'cl1': 2, 'meow': {'3': 1}},
        'siren_research_device_entries': [{'ts': '2026-09-01T10:00:00', 'source': 'cl1', 'hazard_level': None}],
        'commission_income_entries': [{'ts': '2026-09-01T09:00:00', 'items': {'Gem': 5}, 'commission_count': 1, 'screenshots': []}],
        'research_drop_entries': [{'ts': '2026-09-01T09:00:00', 'project': 'D-737-MI', 'series': 9, 'items': {'Blueprint': 1}, 'imgid': 'x'}],
        'gem_commission_entries': [],
        'running_gem_commissions': [],
    }
    conn.execute(
        'INSERT INTO cl1_data VALUES (?, ?, ?, NULL)',
        ('inst', '2026-09', json.dumps(data, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()
    return data


class MemoryProvider(KeyProvider):
    name = 'isolated-test'

    def __init__(self):
        self.states = {}
        self.offline = False

    def load(self, slot):
        if self.offline:
            raise ProviderUnavailable('offline')
        return json.loads(json.dumps(self.states[slot])) if slot in self.states else None

    def save(self, slot, state):
        if self.offline:
            raise ProviderUnavailable('offline')
        self.states[slot] = json.loads(json.dumps(state))

    def delete(self, slot):
        self.states.pop(slot, None)


def legacy_ring(root, key, wrap=lambda x: x):
    directory = root / 'config' / 'opsi_secure'
    directory.mkdir(parents=True, exist_ok=True)
    ring = {'version': 1, 'wrapped_local': base64.b64encode(wrap(key)).decode(),
            'manifest': {'files': {'obsolete-code-path': 'obsolete-code-hash'}}, 'created': 'old', 'updated': 'old'}
    ring['mac'] = hmac.new(opsi_secure._subkey(key, 'opsi-stats/v1/keyring-mac'), canonical(ring), hashlib.sha256).hexdigest()
    (directory / 'keyring.json').write_bytes(canonical(ring))
    return ring


def legacy_blob(key, kind, data):
    cipher = opsi_secure.AES.new(opsi_secure._subkey(key, 'opsi-stats/v1/' + kind), opsi_secure.AES.MODE_GCM,
                                nonce=os.urandom(12))
    cipher.update(('opsi-stats/v1/' + kind).encode())
    raw, tag = cipher.encrypt_and_digest(canonical(data))
    return opsi_secure.LEGACY_PREFIX + base64.b64encode(cipher.nonce + raw + tag).decode()


class OpsiSecureTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.directory.name)
        (self.root / 'config').mkdir()
        (self.root / 'log' / 'cl1' / 'inst').mkdir(parents=True)
        self.provider = MemoryProvider()
        self.key = os.urandom(32)
        self.dpapi_patch = patch.object(opsi_secure.Vault, '_dpapi', side_effect=lambda x, decrypt=False: x)
        self.dpapi_patch.start()
        self.full = make_cl1_db(self.root / 'config' / 'cl1_data.db')
        make_loot_db(self.root / 'config' / 'azurstats_local.db')
        self.ship = self.root / 'log' / 'cl1' / 'inst' / 'ship_exp_data.json'
        self.ship.write_text(json.dumps({'battle_times': [1, 2, 3]}), encoding='utf-8')
        self.csv = self.root / 'log' / 'azurstat_meowofficer_farming.csv'
        self.csv.write_text('a,b\n1,2\n', encoding='utf-8')
        self.vault = self.new_vault()
        self.previous = opsi_secure._VAULT
        opsi_secure.set_vault(self.vault)

    def tearDown(self):
        opsi_secure.set_vault(self.previous)
        self.dpapi_patch.stop()
        self.directory.cleanup()

    def new_vault(self, deep_check=False):
        return opsi_secure.Vault(self.root, provider=self.provider, background_migration=False,
                                 deep_check=deep_check)

    def read_cl1(self, vault=None):
        vault = vault or self.vault
        with db(vault.cl1_db) as conn:
            public, blob = conn.execute('SELECT data_json,secure_json FROM cl1_data').fetchone()
        secure = vault.open_('cl1', blob, opsi_secure.row_context('cl1', {'instance': 'inst', 'month': '2026-09'}))
        return {**json.loads(public), **secure}

    def to_v1(self):
        ring = legacy_ring(self.root, self.key)
        with db(self.vault.cl1_db) as conn:
            conn.execute('ALTER TABLE cl1_data ADD COLUMN secure_json TEXT')
            public, data = opsi_secure.partition_cl1(self.full)
            conn.execute('UPDATE cl1_data SET data_json=?,secure_json=?',
                         (json.dumps(public), legacy_blob(self.key, 'cl1', data)))
        with db(self.vault.azurstats_db) as conn:
            for table, column, kind, fields in [('opsi_items', 'secure_payload', 'loot', opsi_secure.LOOT_SECURE_FIELDS),
                                               ('resource_snapshots', 'opsi_payload', 'res', opsi_secure.RES_SECURE_FIELDS)]:
                conn.execute('ALTER TABLE ' + table + ' ADD COLUMN ' + column + ' TEXT')
                conn.row_factory = sqlite3.Row
                row = dict(conn.execute('SELECT * FROM ' + table).fetchone())
                data = {k: row[k] for k in fields}
                conn.execute('UPDATE ' + table + ' SET ' + column + '=?,' + ','.join(k + '=NULL' for k in fields),
                             (legacy_blob(self.key, kind, data),))
        self.ship.write_text(json.dumps({opsi_secure.LEGACY_WRAPPER_KEY: True,
                                       'payload': legacy_blob(self.key, 'ships', {'battle_times': [1, 2, 3]})}))
        self.csv.write_text(legacy_blob(self.key, 'loot', {'header': ['a', 'b'], 'rows': [['1', '2']]}))
        return ring

    def test_v1_all_stores_equal_after_v2_and_restart(self):
        self.to_v1()
        self.assertTrue(self.vault.ensure_ready())
        fresh = self.new_vault()
        self.assertTrue(fresh.ensure_ready())
        self.assertEqual(self.read_cl1(fresh), self.full)
        with db(fresh.azurstats_db) as conn:
            conn.row_factory = sqlite3.Row
            row = dict(conn.execute('SELECT * FROM opsi_items').fetchone())
        data = fresh.open_('loot', row['secure_payload'], opsi_secure.row_context('loot', row))
        self.assertEqual(data['item'], 'PlateGeneralT4')
        self.assertEqual(data['amount'], 3)
        self.assertEqual(fresh.open_('loot', self.csv.read_text(), fresh.file_context('loot', self.csv))['rows'], [['1', '2']])
        wrapper = json.loads(self.ship.read_text())
        self.assertEqual(fresh.open_('ships', wrapper['payload'], fresh.file_context('ships', self.ship)), {'battle_times': [1, 2, 3]})

    def test_failure_before_commit_preserves_v1_bytes(self):
        self.to_v1()
        paths = [self.vault.keyring_path, self.vault.cl1_db, self.vault.azurstats_db, self.ship, self.csv]
        before = {p: p.read_bytes() for p in paths}
        with patch.object(self.vault, '_check_roundtrip', side_effect=OSError('injected')):
            self.assertFalse(self.vault.ensure_ready())
        self.assertEqual(before, {p: p.read_bytes() for p in paths})
        self.assertFalse(self.vault.wipe_path.exists())
        with db(self.vault.cl1_db) as conn:
            blob = conn.execute('SELECT secure_json FROM cl1_data').fetchone()[0]
        self.assertEqual(self.new_vault().open_('cl1', blob), opsi_secure.partition_cl1(self.full)[1])
        self.assertTrue(self.new_vault().ensure_ready())

    def test_failure_after_first_database_commit_restores_v1(self):
        self.to_v1()
        original = self.vault.coordinator.snapshot()
        real = opsi_secure.durable_write
        failed = False

        def fail_once(path, data):
            nonlocal failed
            if Path(path) == self.ship and not failed:
                failed = True
                raise OSError('occupied')
            return real(path, data)

        with patch.object(opsi_secure, 'durable_write', side_effect=fail_once):
            self.assertFalse(self.vault.ensure_ready())
        self.assertEqual(self.vault.coordinator.snapshot(), original)
        self.assertEqual(json.loads(self.vault.keyring_path.read_bytes())['version'], 1)
        self.assertFalse(self.vault.wipe_path.exists())
        self.assertTrue(self.new_vault().ensure_ready())
        self.assertEqual(self.read_cl1(self.new_vault()), self.full)

    def test_upgrade_code_change_is_safe(self):
        self.to_v1()
        self.assertTrue(self.vault.ensure_ready())
        self.assertTrue(opsi_secure.Vault(self.root, protected_files=[self.root / 'nonexistent.py'],
                                        provider=self.provider, deep_check=False).ensure_ready())
        self.assertEqual(self.read_cl1(), self.full)
        self.assertFalse(self.vault.wipe_path.exists())

    def test_container_file_provider_adopts_plaintext_and_persists(self):
        """容器本地文件凭据（免配置兜底）：首次运行接管明文数据，重启新实例后仍可读。"""
        state = self.root / 'config' / 'opsi_secure' / 'state.json'
        vault = opsi_secure.Vault(self.root, provider=ContainerFileProvider(state),
                                  background_migration=False, deep_check=False)
        opsi_secure.set_vault(vault)
        self.assertTrue(vault.ensure_ready())
        self.assertEqual(self.read_cl1(vault), self.full)
        self.assertTrue(state.exists())
        again = opsi_secure.Vault(self.root, provider=ContainerFileProvider(state),
                                  background_migration=False, deep_check=False)
        self.assertTrue(again.ensure_ready())
        self.assertEqual(self.read_cl1(again), self.full)

    def test_fallback_keeps_unreachable_environment_locked_without_wipe(self):
        """已有环境（如曾配 Broker）下兜底凭据取不到状态：只锁定保留，绝不清空。"""
        self.assertTrue(self.vault.ensure_ready())
        fallback = opsi_secure.Vault(self.root,
                                     provider=ContainerFileProvider(self.root / 'config' / 'opsi_secure' / 'state.json'),
                                     background_migration=False, deep_check=False)
        self.assertFalse(fallback.ensure_ready())
        self.assertFalse(fallback.wipe_path.exists())
        self.assertIsNotNone(self.provider.load(self.vault.slot))

    def assert_no_global_detection(self):
        """结构变化等豁免路径不触发全局冻结：新实例可用、无 wipe 记录、凭据未撤销。"""
        fresh = self.new_vault()
        self.assertTrue(fresh.ensure_ready())
        self.assertFalse(self.vault.wipe_path.exists())
        self.assertIsNotNone(self.provider.load(self.vault.slot))
        return fresh

    def assert_blocked_state(self):
        """检测仍生效，按定制策略持久冻结，保留原件与凭据并生成救援副本。"""
        self.assertTrue(self.vault.status()['blocked'])
        self.assertFalse(self.vault.wipe_path.exists())
        self.assertTrue(list(self.vault.directory.glob('rescue-*')))
        self.assertIsNotNone(self.provider.load(self.vault.slot))
        with db(self.vault.cl1_db) as conn:
            row = conn.execute('SELECT secure_json FROM cl1_data').fetchone()
        self.assertTrue(row[0].startswith(opsi_secure.BLOB_PREFIX))
        self.assertFalse(self.new_vault().ensure_ready())
        with self.assertRaises(opsi_secure.VaultLocked):
            self.update_count(9999)

    def test_unprotected_columns_stay_editable(self):
        """公开列（非大世界资源列、CL1 公开字段）库外编辑不触发浅检查冻结。"""
        self.assertTrue(self.vault.ensure_ready())
        with db(self.vault.azurstats_db) as conn:
            conn.execute('UPDATE resource_snapshots SET oil=999')
        with db(self.vault.cl1_db) as conn:
            public = json.loads(conn.execute('SELECT data_json FROM cl1_data').fetchone()[0])
            public['research_drop_entries'] = []
            conn.execute('UPDATE cl1_data SET data_json=?', (json.dumps(public, ensure_ascii=False),))
        self.vault.verify_on_page_open()
        self.assertFalse(self.vault.wipe_path.exists())
        self.assertFalse((self.vault.directory / 'integrity.json').exists())
        with db(self.vault.azurstats_db) as conn:
            self.assertEqual(conn.execute('SELECT oil FROM resource_snapshots').fetchone()[0], 999)
        self.assertEqual(self.read_cl1()['battle_count'], self.full['battle_count'])

    def test_out_of_band_row_deletion_triggers_freeze(self):
        """库外删行：下一次写入即按行数锚点冻结。"""
        self.assertTrue(self.vault.ensure_ready())
        with db(self.vault.azurstats_db) as conn:
            conn.execute('DELETE FROM opsi_items')
        with self.assertRaises(opsi_secure.VaultLocked):
            with db(self.vault.azurstats_db) as conn:
                with self.vault.transaction(conn, self.vault.azurstats_db):
                    conn.execute("INSERT INTO resource_snapshots(instance, ts) VALUES('inst','2026-09-02T00:00:00')")
        self.assert_blocked_state()

    def test_out_of_band_row_insertion_triggers_freeze(self):
        """库外插行：页面核对点即按行数锚点冻结。"""
        self.assertTrue(self.vault.ensure_ready())
        with db(self.vault.azurstats_db) as conn:
            conn.execute("INSERT INTO opsi_items(imgid,instance) VALUES('forged','inst')")
        self.vault.verify_on_page_open()
        self.assert_blocked_state()

    def test_record_replacement_triggers_freeze_on_read(self):
        """行身份被库外改写（AAD 不再匹配）：读取该条即按篡改信号冻结。"""
        self.assertTrue(self.vault.ensure_ready())
        with db(self.vault.azurstats_db) as conn:
            conn.execute("UPDATE opsi_items SET instance='other'")
        fresh = self.new_vault()
        self.assertTrue(fresh.ensure_ready())
        self.assertFalse(self.vault.wipe_path.exists())
        with db(self.vault.azurstats_db) as conn:
            row = dict(zip([r[1] for r in conn.execute('PRAGMA table_info(opsi_items)')], conn.execute('SELECT * FROM opsi_items').fetchone()))
        # 行身份参与认证：换实例后该条读不出，读取路径判定为篡改并冻结。
        self.assertIsNone(fresh.open_or_none('loot', row['secure_payload'], opsi_secure.row_context('loot', row)))
        self.assert_blocked_state()

    def test_record_exchange_triggers_freeze_on_read(self):
        """跨行挪动密文（AAD 绑定行身份）：读取任一条即按篡改信号冻结。"""
        with db(self.vault.azurstats_db) as conn:
            conn.execute("INSERT INTO opsi_items(imgid,instance,item,amount,created_at) VALUES('second','inst','other',9,1789000000)")
        self.assertTrue(self.vault.ensure_ready())
        with db(self.vault.azurstats_db) as conn:
            records = conn.execute('SELECT id,secure_payload FROM opsi_items ORDER BY id').fetchall()
            conn.execute('UPDATE opsi_items SET secure_payload=? WHERE id=?', (records[1][1], records[0][0]))
            conn.execute('UPDATE opsi_items SET secure_payload=? WHERE id=?', (records[0][1], records[1][0]))
        fresh = self.new_vault()
        self.assertTrue(fresh.ensure_ready())
        self.assertFalse(self.vault.wipe_path.exists())
        with db(self.vault.azurstats_db) as conn:
            conn.row_factory = sqlite3.Row
            row = dict(conn.execute('SELECT * FROM opsi_items ORDER BY id').fetchone())
        self.assertIsNone(fresh.open_or_none('loot', row['secure_payload'], opsi_secure.row_context('loot', row)))
        self.assert_blocked_state()

    def test_copied_file_new_path_verify_silent_read_freezes(self):
        """受保护文件被复制到新路径：核对点静默采纳新文件；读取复制品即冻结。"""
        self.assertTrue(self.vault.ensure_ready())
        extra = self.root / 'log' / 'azurstat_meowofficer_farming.instance-forged.csv'
        extra.write_bytes(self.csv.read_bytes())
        fresh = self.new_vault()
        self.assertTrue(fresh.ensure_ready())
        self.assertFalse(self.vault.wipe_path.exists())
        # 文件的相对路径参与认证：复制的密文在新路径下读不出，按篡改信号冻结。
        self.assertIsNone(fresh.open_or_none('loot', extra.read_text(), fresh.file_context('loot', extra)))
        self.assert_blocked_state()

    def test_transaction_writes_leave_no_pending_state(self):
        """写入不再走 pending/journal 协议：提交即持久，状态里不残留中间态。"""
        self.assertTrue(self.vault.ensure_ready())
        self.update_count(888)
        self.assertNotIn('pending', self.vault._state)
        self.assertFalse(self.vault.journal_path.exists())
        fresh = self.new_vault()
        self.assertTrue(fresh.ensure_ready())
        self.assertEqual(self.read_cl1(fresh)['battle_count'], 888)

    def test_daily_cleanup_commits_without_touching_other_tables(self):
        path = self.root / 'config' / 'daily_summary.db'
        with db(path) as conn:
            conn.execute('CREATE TABLE daily_summary_cl1_events(id INTEGER PRIMARY KEY, instance TEXT, ts TEXT, duration_seconds REAL, estimated_exp INTEGER)')
            conn.execute("INSERT INTO daily_summary_cl1_events VALUES(1,'inst','2026-09-01',10,20)")
            conn.execute('CREATE TABLE daily_summary_periods(instance TEXT,period_key TEXT,report_text TEXT, PRIMARY KEY(instance,period_key))')
            conn.execute("INSERT INTO daily_summary_periods VALUES('inst','2026-09-01','sentinel report')")
            conn.execute('CREATE TABLE other_business(value TEXT)')
            conn.execute("INSERT INTO other_business VALUES('keep')")
        self.assertTrue(self.vault.ensure_ready())
        with db(path) as conn:
            with self.vault.transaction(conn, path):
                conn.execute('DELETE FROM daily_summary_cl1_events')
                conn.execute('DELETE FROM daily_summary_periods')
        self.assertTrue(self.new_vault().ensure_ready())
        with db(path) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM daily_summary_cl1_events').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT count(*) FROM daily_summary_periods').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT value FROM other_business').fetchone()[0], 'keep')
        self.assertEqual(self.read_cl1(), self.full)

    def test_old_backup_is_preserved_as_payload_and_regular_expiry_is_authenticated(self):
        path = self.root / 'AzurPilot_Data_Backup' / '2026-09-01' / 'cl1_data.db'
        path.parent.mkdir(parents=True)
        raw = self.vault.cl1_db.read_bytes()
        path.write_bytes(raw)
        self.assertTrue(self.vault.ensure_ready())
        self.assertNotIn(b'akashi_ap_entries', path.read_bytes())
        wrapper = json.loads(path.read_bytes())
        data = self.vault.open_('archives', wrapper['payload'], self.vault.file_context('archives', path))
        self.assertEqual(base64.b64decode(data['bytes']), raw)
        self.vault.remove_files([path])
        self.assertFalse(path.exists())
        self.assertTrue(self.new_vault().ensure_ready())
        path.write_bytes(raw)
        self.assertTrue(self.new_vault().ensure_ready())
        self.assertFalse(self.vault.wipe_path.exists())
        self.assertEqual(path.read_bytes(), raw)

    def test_unknown_future_version_is_not_tampering(self):
        self.assertTrue(self.vault.ensure_ready())
        before = self.vault.cl1_db.read_bytes()
        state = self.provider.load(self.vault.slot)
        self.provider.save(self.vault.slot, dict(state, schema=3))
        self.vault.keyring_path.write_text('{"version":3}')
        self.assertFalse(self.new_vault().ensure_ready())
        self.assertEqual(self.vault.cl1_db.read_bytes(), before)
        self.assertFalse(self.vault.wipe_path.exists())

    def test_archived_json_and_csv_are_retained_without_normal_content(self):
        archive = self.ship.parent / 'cl1_monthly.json.bak'
        archive.write_text(json.dumps({'2026-09': 987}))
        self.assertTrue(self.vault.ensure_ready())
        wrapper = json.loads(archive.read_bytes())
        self.assertTrue(wrapper[opsi_secure.WRAPPER_KEY])
        self.assertNotIn('987', archive.read_text())
        self.assertEqual(self.vault.open_('archives', wrapper['payload'], self.vault.file_context('archives', archive)),
                         {'2026-09': 987})

    def test_bit_flip_triggers_background_freeze_on_page_read(self):
        """库外逐字节改密文：页面读取路径（事务内）触发后台冻结，本次读取降级为空。"""
        self.assertTrue(self.vault.ensure_ready())
        with db(self.vault.azurstats_db) as conn:
            conn.execute("UPDATE opsi_items SET secure_payload=substr(secure_payload,1,length(secure_payload)-1)||'!' ")
        fresh = self.new_vault()
        self.assertTrue(fresh.ensure_ready())
        with db(self.vault.azurstats_db) as conn:
            columns = [r[1] for r in conn.execute('PRAGMA table_info(opsi_items)')]
            row = dict(zip(columns, conn.execute('SELECT * FROM opsi_items').fetchone()))
        with fresh.reading():
            self.assertIsNone(fresh.open_or_none('loot', row['secure_payload'], opsi_secure.row_context('loot', row)))
        fresh._wipe_thread.join(timeout=10)
        self.assert_blocked_state()

    def test_forged_triggers_do_not_wipe_or_block(self):
        self.assertTrue(self.vault.ensure_ready())
        with db(self.vault.azurstats_db) as conn:
            conn.execute("CREATE TRIGGER forged_delete BEFORE DELETE ON opsi_items BEGIN SELECT RAISE(ABORT,'blocked'); END")
        fresh = self.assert_no_global_detection()
        self.assertTrue(fresh.ensure_ready())

    def test_deleted_database_triggers_freeze(self):
        """受保护数据库被整个删除：启动核对点即冻结。"""
        self.assertTrue(self.vault.ensure_ready())
        self.vault.azurstats_db.unlink()
        fresh = self.new_vault()
        self.assertFalse(fresh.ensure_ready())
        self.assert_blocked_state()

    def test_deleted_protected_file_triggers_freeze(self):
        """受保护统计文件被删除：页面核对点即冻结。"""
        self.assertTrue(self.vault.ensure_ready())
        self.ship.unlink()
        self.vault.verify_on_page_open()
        self.assert_blocked_state()

    def test_missing_expectations_are_vouched_silently(self):
        """升级场景：旧状态里没有基线字段时静默补记全部路径，不误报。"""
        self.assertTrue(self.vault.ensure_ready())
        state = self.provider.load(self.vault.slot)
        state.pop('expect', None)
        self.provider.save(self.vault.slot, state)
        fresh = self.new_vault()
        self.assertTrue(fresh.ensure_ready())
        self.assertTrue(fresh._state.get('expect'))
        self.assertFalse((self.vault.directory / 'integrity.json').exists())

    def test_regular_operations_leave_no_integrity_record(self):
        """正常读写不产生误报：基线与实际一致时没有任何 integrity 记录。"""
        self.assertTrue(self.vault.ensure_ready())
        self.update_count(121)
        self.update_count(122)
        self.assertTrue(self.new_vault().ensure_ready())
        self.assertFalse((self.vault.directory / 'integrity.json').exists())
        self.assertFalse(self.vault.wipe_path.exists())

    def test_snapshot_rollback_triggers_freeze_at_next_write(self):
        """整库回滚到旧字节：下一次写入前按链值冻结（证据 + 救援 + 保留凭据）。"""
        self.assertTrue(self.vault.ensure_ready())
        old = self.vault.cl1_db.read_bytes()
        self.update_count(999)
        self.vault.cl1_db.write_bytes(old)
        with self.assertRaises(opsi_secure.VaultLocked):
            self.update_count(888)
        self.assert_blocked_state()

    def test_snapshot_rollback_detected_at_startup(self):
        """被替换为旧备份：新进程启动核对即冻结，不再加载被回滚的数据。"""
        self.assertTrue(self.vault.ensure_ready())
        old = self.vault.cl1_db.read_bytes()
        self.update_count(555)
        self.vault.cl1_db.write_bytes(old)
        fresh = self.new_vault()
        self.assertFalse(fresh.ensure_ready())
        self.assert_blocked_state()

    def test_file_rollback_triggers_freeze(self):
        """受保护文件回滚到旧字节：页面核对点冻结。"""
        self.assertTrue(self.vault.ensure_ready())
        before = self.ship.read_bytes()
        self.vault.write_file('ships', self.ship, {'changed': True}, wrapper=True)
        self.ship.write_bytes(before)
        self.vault.verify_on_page_open()
        self.assert_blocked_state()

    def test_external_table_rebuild_is_repaired_and_rebaselined(self):
        """外部工具把 cl1_data 改成三列主键：重启构造自愈重建，vault 按结构变化
        重记基线（不冻结），随后写入与行数锚点恢复正常。"""
        self.assertTrue(self.vault.ensure_ready())
        self.update_count(41)
        with db(self.vault.cl1_db) as conn:
            conn.execute('CREATE TABLE "cl1_data_new" ("instance" TEXT, "month" TEXT, "encrypted_blob" BLOB,'
                         ' "data_json" TEXT, "secure_json" TEXT, PRIMARY KEY("instance","month","secure_json"))')
            conn.execute("INSERT INTO cl1_data_new (instance, month, encrypted_blob, data_json, secure_json)"
                         " SELECT instance, month, encrypted_blob, data_json, secure_json FROM cl1_data")
            conn.execute("DROP TABLE cl1_data")
            conn.execute("ALTER TABLE cl1_data_new RENAME TO cl1_data")
        with patch.object(cl1_database.Cl1Database, "_get_legacy_decryption_keys", return_value=[]):
            cl1_database.Cl1Database(self.vault.cl1_db)
        with db(self.vault.cl1_db) as conn:
            self.assertEqual(cl1_database.Cl1Database._primary_key(conn.cursor()), ["instance", "month"])
        self.update_count(42)
        self.assertEqual(self.read_cl1()["battle_count"], 42)
        self.assertFalse(self.vault.wipe_path.exists())
        self.assert_no_global_detection()
        with db(self.vault.cl1_db) as conn:
            anchors = conn.execute("SELECT count(*) FROM sqlite_master"
                                   " WHERE name LIKE '__opsi_count_cl1_data%'").fetchone()[0]
        self.assertEqual(anchors, 2)

    def test_expectation_persists_across_restart(self):
        """文件类路径同样记基线；重启后基线从安全服务恢复，常规状态下无误报。"""
        self.assertTrue(self.vault.ensure_ready())
        self.vault.write_file('ships', self.ship, {'a': 1}, wrapper=True)
        key = str(self.ship.resolve().relative_to(self.root))
        self.assertIn(key, self.vault._state['expect'])
        fresh = self.new_vault()
        self.assertTrue(fresh.ensure_ready())
        self.assertEqual(fresh._state['expect'][key], self.vault._state['expect'][key])
        self.assertFalse((self.vault.directory / 'integrity.json').exists())
        self.assertFalse(self.vault.wipe_path.exists())

    def test_revouch_re_baselines_explicitly(self):
        """显式重记入口：库外改动被 revouch 接受后，核对点不再判定篡改。"""
        self.assertTrue(self.vault.ensure_ready())
        before = self.ship.read_bytes()
        self.vault.write_file('ships', self.ship, {'b': 2}, wrapper=True)
        self.ship.write_bytes(before)
        self.vault.revouch(self.ship)
        self.vault.verify_on_page_open()
        self.assertFalse(self.vault.wipe_path.exists())
        self.assertTrue(self.new_vault().ensure_ready())

    def test_descriptor_tamper(self):
        self.assertTrue(self.vault.ensure_ready())
        original = self.vault.cl1_db.read_bytes()
        credentials = self.provider.load(self.vault.slot)
        self.vault.keyring_path.write_text('{}')
        self.assertFalse(self.new_vault().ensure_ready())
        self.assertTrue((self.vault.directory / 'blocked.json').exists())
        self.assertFalse(self.vault.wipe_path.exists())
        self.assertEqual(self.vault.cl1_db.read_bytes(), original)
        self.assertEqual(self.provider.load(self.vault.slot), credentials)
        self.assertEqual(self.vault.keyring_path.read_text(), '{}')
        self.assertFalse(self.new_vault().ensure_ready())

    def test_schema_upgrade_rebaselines_without_wipe(self):
        """应用升级改表结构（ALTER 加列）：甲胄乙记日志并重记基线，不冻结。"""
        self.assertTrue(self.vault.ensure_ready())
        with db(self.vault.azurstats_db) as conn:
            conn.execute('ALTER TABLE opsi_items ADD COLUMN extra TEXT')
        self.vault.verify_on_page_open()
        self.assertFalse(self.vault.wipe_path.exists())
        self.assertFalse((self.vault.directory / 'integrity.json').exists())
        self.assertTrue(self.new_vault().ensure_ready())
        self.update_count(777)
        self.assertEqual(self.read_cl1(self.new_vault())['battle_count'], 777)

    def update_count(self, count, vault=None):
        vault = vault or self.vault
        context = opsi_secure.row_context('cl1', {'instance': 'inst', 'month': '2026-09'})
        with db(vault.cl1_db) as conn:
            with vault.transaction(conn, vault.cl1_db):
                blob = vault.seal('cl1', {'battle_count': count}, context)
                conn.execute('UPDATE cl1_data SET secure_json=?', (blob,))

    def test_row_replay_is_caught_by_deep_check(self):
        """库内记录回放（旧密文本省合法、行数不变）：浅检查看不见，深检查冻结。"""
        self.assertTrue(self.vault.ensure_ready())
        with db(self.vault.cl1_db) as conn:
            old = conn.execute('SELECT secure_json FROM cl1_data').fetchone()[0]
        self.update_count(123)
        self.vault.deep_check_once()
        with db(self.vault.cl1_db) as conn:
            conn.execute('UPDATE cl1_data SET secure_json=?', (old,))
        self.vault.deep_check_once()
        self.assert_blocked_state()

    def test_first_run_initializes_chain_silently(self):
        """首次启用（升级到本版本）：静默建链补基线，不冻结、无证据文件。"""
        self.assertTrue(self.vault.ensure_ready())
        with db(self.vault.cl1_db) as conn:
            self.assertEqual(conn.execute('SELECT seq FROM __opsi_integrity').fetchone()[0], 1)
        self.assertFalse(self.vault.wipe_path.exists())
        self.assertFalse((self.vault.directory / 'integrity.json').exists())
        self.update_count(131)
        with db(self.vault.cl1_db) as conn:
            self.assertEqual(conn.execute('SELECT seq FROM __opsi_integrity').fetchone()[0], 2)

    def test_binding_dataset_instance_identity_period_installation(self):
        self.assertTrue(self.vault.ensure_ready())
        context = self.vault.context('cl1', 'inst', 'row', '2026-09')
        blob = self.vault.seal('cl1', {'battle_count': 7}, context)
        # 直接校验标签，不让每个参数试验冻结后影响其余试验。
        for field, value in [('dataset', 'loot'), ('instance', 'other'), ('identity', 'other'),
                             ('period', '2026-10'), ('installation_id', 'other'), ('algorithm', 'AES-GCM')]:
            aad = dict(context, schema=2, algorithm=opsi_secure.ALGORITHM,
                       installation_id=self.vault._state['installation_id'])
            aad[field] = value
            with self.assertRaises(opsi_secure.IntegrityFailure):
                self.vault._decode('opsi-stats/v2/cl1', blob[len(opsi_secure.BLOB_PREFIX):], aad)

    def test_cross_device_account_and_copied_installation_preserve_original(self):
        self.assertTrue(self.vault.ensure_ready())
        other = opsi_secure.Vault(self.root, provider=MemoryProvider(), deep_check=False)
        self.assertFalse(other.ensure_ready())
        self.assertFalse(self.vault.wipe_path.exists())
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            clone = Path(folder) / 'clone'
            shutil.copytree(self.root, clone)
            copied = opsi_secure.Vault(clone, provider=self.provider, deep_check=False)
            self.assertFalse(copied.ensure_ready())
            self.assertFalse(copied.wipe_path.exists())
        self.assertTrue(self.vault.ensure_ready())

    def test_provider_offline_does_not_wipe(self):
        self.assertTrue(self.vault.ensure_ready())
        original = self.vault.cl1_db.read_bytes()
        self.provider.offline = True
        self.assertFalse(self.vault.ensure_ready())
        self.assertEqual(self.vault.cl1_db.read_bytes(), original)
        self.assertFalse(self.vault.wipe_path.exists())
        self.provider.offline = False
        self.assertTrue(self.vault.ensure_ready())

    def test_sqlite_busy_does_not_wipe(self):
        self.assertTrue(self.vault.ensure_ready())
        with db(self.vault.azurstats_db) as conn:
            conn.execute('BEGIN EXCLUSIVE')
            # 解封与写入都不再触碰数据库文件，占用中的库不影响可用性判断，也不触发冻结。
            self.assertTrue(self.new_vault().ensure_ready())
            self.assertFalse(self.vault.wipe_path.exists())

    def test_exception_exit_rolls_back_without_pending(self):
        self.assertTrue(self.vault.ensure_ready())
        self.update_count(777)
        fresh = self.new_vault()
        self.assertTrue(fresh.ensure_ready())
        self.assertEqual(self.read_cl1(fresh)['battle_count'], 777)
        self.assertNotIn('pending', self.provider.load(self.vault.slot))
        old = self.read_cl1(fresh)
        with self.assertRaises(RuntimeError), db(fresh.cl1_db) as conn:
            with fresh.transaction(conn, fresh.cl1_db):
                conn.execute('DELETE FROM cl1_data')
                raise RuntimeError('terminated')
        self.assertEqual(self.read_cl1(self.new_vault()), old)

    def test_two_process_style_vaults_and_threads(self):
        self.assertTrue(self.vault.ensure_ready())
        def update(_):
            vault = self.new_vault()
            with db(vault.cl1_db) as conn:
                with vault.transaction(conn, vault.cl1_db):
                    raw = conn.execute('SELECT secure_json FROM cl1_data').fetchone()[0]
                    context = opsi_secure.row_context('cl1', {'instance': 'inst', 'month': '2026-09'})
                    data = vault.open_('cl1', raw, context)
                    data['battle_count'] += 1
                    conn.execute('UPDATE cl1_data SET secure_json=?', (vault.seal('cl1', data, context),))
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(update, range(12)))
        self.assertEqual(self.read_cl1(self.new_vault())['battle_count'], 132)

    def test_crash_window_between_commit_and_save_rebaselines(self):
        """提交成功但期望值未写入安全服务的崩溃窗口：重记基线，不冻结。"""
        self.assertTrue(self.vault.ensure_ready())
        with patch.object(self.vault, '_save_expectations', return_value=False):
            self.update_count(500)
        self.assertTrue((self.vault.directory / 'pending.json').exists())
        fresh = self.new_vault()
        self.assertTrue(fresh.ensure_ready())
        self.assertFalse(self.vault.wipe_path.exists())
        self.assertFalse((self.vault.directory / 'integrity.json').exists())
        self.assertFalse((self.vault.directory / 'pending.json').exists())
        self.assertEqual(self.read_cl1(fresh)['battle_count'], 500)
        # 同一 Vault 也能自愈：下一笔写入前按崩溃窗口重记基线后照常进行。
        self.update_count(501)
        self.assertEqual(self.read_cl1(self.new_vault())['battle_count'], 501)

    def test_reencrypted_row_bypassing_chain_is_caught_by_deep_check(self):
        """用本环境密钥重加密单条但绕过链更新：浅检查看不见，深检查冻结。"""
        self.assertTrue(self.vault.ensure_ready())
        self.vault.deep_check_once()
        context = opsi_secure.row_context('cl1', {'instance': 'inst', 'month': '2026-09'})
        blob = self.vault.seal('cl1', {'battle_count': 4242}, context)
        with db(self.vault.cl1_db) as conn:
            conn.execute('UPDATE cl1_data SET secure_json=?', (blob,))
        self.vault.deep_check_once()
        self.assert_blocked_state()

    def test_deep_check_runs_concurrently_with_writes_without_wipe(self):
        """深检查与写入并发：序号乐观并发不误报，写入全部成功。"""
        self.assertTrue(self.vault.ensure_ready())
        self.vault.deep_check_once()
        errors = []
        stop = threading.Event()

        def writer(offset):
            try:
                for step in range(15):
                    self.update_count(2000 + offset * 100 + step)
            except Exception as exc:
                errors.append(exc)

        def deep():
            try:
                while not stop.is_set():
                    self.vault.deep_check_once()
            except Exception as exc:
                errors.append(exc)

        writers = [threading.Thread(target=writer, args=(index,)) for index in range(2)]
        watcher = threading.Thread(target=deep)
        for worker in [*writers, watcher]:
            worker.start()
        for worker in writers:
            worker.join()
        stop.set()
        watcher.join(timeout=10)
        self.assertEqual(errors, [])
        self.assertFalse(self.vault.wipe_path.exists())
        self.assertFalse((self.vault.directory / 'integrity.json').exists())

    def test_write_path_never_digests(self):
        """性能回归护栏：写入路径只读链行/行数/结构，不做任何全量摘要。"""
        self.assertTrue(self.vault.ensure_ready())
        with patch.object(opsi_secure.Vault, '_path_digest',
                          wraps=opsi_secure.Vault._path_digest) as digest:
            self.update_count(222)
        self.assertEqual(digest.call_count, 0)
        with db(self.vault.cl1_db) as conn:
            self.assertEqual(conn.execute('SELECT seq FROM __opsi_integrity').fetchone()[0], 2)

    def test_provider_state_stays_within_platform_budget(self):
        """Windows 凭据有 2560 字节上限：期望值编码必须保持精简。"""
        self.assertTrue(self.vault.ensure_ready())
        self.update_count(131)
        self.vault.write_file('ships', self.ship, {'a': 1}, wrapper=True)
        state = self.provider.load(self.vault.slot)
        self.assertLess(len(canonical(state)), 2200)





def make_loot_db(path):
    """构造旧版明文掉落明细与资源快照库（同一个 azurstats_local.db）。"""
    conn = sqlite3.connect(path)
    conn.execute('''
        CREATE TABLE opsi_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            imgid TEXT NOT NULL, server TEXT, zone TEXT, zone_type TEXT,
            zone_id INTEGER, hazard_level INTEGER, item TEXT, amount INTEGER,
            tag TEXT, device_id TEXT, instance TEXT, genre TEXT,
            combat_count INTEGER, created_at INTEGER
        )
    ''')
    conn.execute(
        'INSERT INTO opsi_items (imgid, server, zone, zone_type, zone_id, hazard_level, '
        'item, amount, tag, device_id, instance, genre, combat_count, created_at) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        ('abc123', 'cn', 'NA海域', 'abyssal', 5, 6, 'PlateGeneralT4', 3, 'gold',
         'device-1', 'inst', 'opsi_abyssal', 2, 1789000000),
    )
    conn.execute('''
        CREATE TABLE resource_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, instance TEXT NOT NULL, ts TEXT NOT NULL,
            oil INTEGER, coin INTEGER, gem INTEGER, pt INTEGER, cube INTEGER, core INTEGER,
            medal INTEGER, merit INTEGER, guild_coin INTEGER,
            action_point INTEGER, yellow_coin INTEGER, purple_coin INTEGER
        )
    ''')
    conn.execute(
        'INSERT INTO resource_snapshots (instance, ts, oil, coin, gem, pt, cube, core, medal, '
        'merit, guild_coin, action_point, yellow_coin, purple_coin) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        ('inst', '2026-09-01T10:00:00', 14000, 180000, 2400, 40000, 380, 1200, 600, 18000, 7500, 131, 500, 20),
    )
    conn.commit()
    conn.close()




if __name__ == '__main__':
    unittest.main()
