"""大世界统计的版本化存储运行服务。"""
from __future__ import annotations
import base64
import csv
import hashlib
import hmac
import io
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import closing, contextmanager
from functools import wraps
from pathlib import Path
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from Crypto.Cipher import AES, ChaCha20_Poly1305
from module.statistics.opsi_keys import ProviderUnavailable, get_provider, installation_slot
from module.statistics.opsi_state import StoreCoordinator, canonical, durable_write
from module.logger import logger

KINDS = ('cl1', 'ships', 'loot', 'res', 'daily', 'reports', 'archives')

CL1_SECURE_FIELDS = frozenset({
    'battle_count',
    'akashi_encounters',
    'akashi_ap',
    'akashi_ap_entries',
    'ap_snapshots',
    'last_ap_notification',
    'yellow_coin_snapshots',
    'coins_snapshots',
    'coins_history_version',
    'coins_cleanup_version',
    # 本地保留的月初残留也包含凭证原始值，必须与显示序列一起加密。
    'coins_month_start_residue',
    'meow_battle_raw_count',
    'meow_battle_count',
    'meow_round_times',
    'meow_battle_times',
    'meow_hazard_stats',
    'siren_research_devices',
    'siren_research_device_entries',
})

LOOT_SECURE_FIELDS = ('server', 'zone', 'zone_type', 'zone_id', 'item', 'amount', 'tag', 'hazard_level', 'combat_count')

RES_SECURE_FIELDS = ('action_point', 'yellow_coin', 'purple_coin')

LEGACY_PREFIX = 'OPSIV1.'
ALGORITHM = 'XCHACHA20-POLY1305'
BLOB_PREFIX = 'OPSIV2.' + ALGORITHM + '.'
LEGACY_WRAPPER_KEY = '__opsi_secure_v1__'
WRAPPER_KEY = '__opsi_secure_v2__'
MISSING_MARKER = '__opsi_secure_missing__'

MIGRATION_CHUNK = 5000

# 完整性链（2026-10-05“发现即清空”定稿）：库内链行与安全服务期望值两层互证。
# 浅检查 O(1)（链值/行数锚点/结构指纹），全量摘要只在后台深检查执行。
CHAIN_TABLE = '__opsi_integrity'
CHAIN_LEN = 32              # 链值截断长度（128 位 MAC，无敏感内容可明文存库）
SIG_LEN = 16                # 摘要截断长度（64 位，控制凭据状态体积不超平台上限）
VERIFY_TIMEOUT = 2          # 浅检查读库超时；数据库繁忙时跳过本轮，绝不据此清空
DEEP_CHECK_MIN_INTERVAL = 120.0
DEEP_CHECK_PERIODIC = 1800.0
DEEP_CHECK_WRITE_CADENCE = 40
PROTECTED_TABLES = ('cl1_data', 'opsi_items', 'resource_snapshots',
                    'daily_summary_cl1_events', 'daily_summary_periods')
COUNT_TABLE = '__opsi_integrity_counts'
CHAIN_DDL = ('CREATE TABLE IF NOT EXISTS ' + CHAIN_TABLE + ' ('
             'id INTEGER PRIMARY KEY CHECK (id = 1), seq INTEGER NOT NULL, '
             'chain TEXT NOT NULL, counts TEXT NOT NULL, fingerprint TEXT NOT NULL)')
COUNTS_DDL = ('CREATE TABLE IF NOT EXISTS ' + COUNT_TABLE + ' ('
              'name TEXT PRIMARY KEY, rows INTEGER NOT NULL)')


def _counter_triggers(table):
    """行数锚点的增量触发器：任何写入（含库外工具）都自动移动计数器。

    口径与核对一致：日报 periods 只计有正文的行，其余表全量计数。
    UPDATE 不需要触发器（行数不变），只有 NULL↔正文 的日报状态迁移例外。
    """
    name = '__opsi_count_' + table
    adds = drops = '' if table != 'daily_summary_periods' else None
    update = None
    if table == 'daily_summary_periods':
        adds = 'WHEN NEW.report_text IS NOT NULL '
        drops = 'WHEN OLD.report_text IS NOT NULL '
        update = (f'CREATE TRIGGER IF NOT EXISTS {name}_upd AFTER UPDATE OF report_text ON {table} '
                  f'WHEN (OLD.report_text IS NULL) <> (NEW.report_text IS NULL) BEGIN '
                  f'UPDATE {COUNT_TABLE} SET rows = rows + CASE WHEN NEW.report_text IS NULL THEN -1 ELSE 1 END '
                  f"WHERE name = '{table}'; END")
    return tuple(sql for sql in (
        f'CREATE TRIGGER IF NOT EXISTS {name}_ins AFTER INSERT ON {table} {adds}BEGIN '
        f"UPDATE {COUNT_TABLE} SET rows = rows + 1 WHERE name = '{table}'; END",
        f'CREATE TRIGGER IF NOT EXISTS {name}_del AFTER DELETE ON {table} {drops}BEGIN '
        f"UPDATE {COUNT_TABLE} SET rows = rows - 1 WHERE name = '{table}'; END",
        update,
    ) if sql)


class VaultError(RuntimeError):
    pass


class VaultLocked(VaultError):
    pass


class IntegrityFailure(VaultError):
    pass


class RecordTampered(IntegrityFailure):
    """记录级认证失败：本环境内的密文被库外改动或替换（读取路径的篡改信号）。"""


def partition_cl1(data):
    return ({k: v for k, v in data.items() if k not in CL1_SECURE_FIELDS},
            {k: v for k, v in data.items() if k in CL1_SECURE_FIELDS})


def _b64e(data):
    return base64.b64encode(data).decode('ascii')


def _b64d(text):
    return base64.b64decode(text, validate=True)


def _subkey(key, info):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info.encode()).derive(key)


def _encrypt(key, raw, aad):
    cipher = ChaCha20_Poly1305.new(key=key, nonce=os.urandom(24))
    cipher.update(canonical(aad))
    data, tag = cipher.encrypt_and_digest(raw)
    return _b64e(cipher.nonce + data + tag)


def _decrypt(key, value, aad):
    try:
        raw = _b64d(value)
        if len(raw) < 40:
            raise ValueError()
        cipher = ChaCha20_Poly1305.new(key=key, nonce=raw[:24])
        cipher.update(canonical(aad))
        return cipher.decrypt_and_verify(raw[24:-16], raw[-16:])
    except (ValueError, TypeError) as exc:
        raise RecordTampered('记录校验失败') from exc


def row_context(kind, row):
    if kind == 'cl1':
        return {'dataset': kind, 'instance': row['instance'], 'identity': row['month'], 'period': row['month']}
    stamp = row.get('ts') if kind in ('res', 'daily') else row.get('created_at')
    if isinstance(stamp, (int, float)):
        from datetime import datetime, timezone
        stamp = datetime.fromtimestamp(stamp, timezone.utc).isoformat()
    return {'dataset': kind, 'instance': row.get('instance'), 'identity': str(row['id']),
            'period': str(stamp or '')[:7], 'stamp': stamp,
            'imgid': row.get('imgid'), 'device': row.get('device_id'), 'genre': row.get('genre')}


class Vault:
    def __init__(self, root=None, protected_files=None, clock=time.time, background_migration=True,
                 provider=None, deep_check=True):
        self.root = Path(root).resolve() if root else Path(__file__).resolve().parents[2]
        self.directory = self.root / 'config' / 'opsi_secure'
        self.keyring_path = self.directory / 'keyring.json'
        self.wipe_path = self.directory / 'wipe.json'
        self.journal_path = self.directory / 'transition.bin'
        self.cl1_db = self.root / 'config' / 'cl1_data.db'
        self.azurstats_db = self.root / 'config' / 'azurstats_local.db'
        self.coordinator = StoreCoordinator(self.root)
        self.slot = installation_slot(self.root)
        self.provider = provider
        self._state = None
        self._dek = None
        self._legacy = None
        self._active = False
        self._read_warned = set()
        self._dropped = {}
        self._background_migration = background_migration
        self._migration_kicked = False
        self._in_transaction = False
        # 已完成解封的协调锁持有令牌：同一持有期内不重复解封。
        self._fresh = None
        # 进程内是否已做过启动基线核对。
        self._startup_checked = False
        # 浅检查去重：同一协调锁持有期内不重复做全路径核对。
        self._verified_hold = None
        # 冻结去重：先阻止异常读写，救援副本在后台保存。
        self._wipe_scheduled = False
        self._wipe_thread = None
        # 后台深检查（全量摘要）；测试与工具可传 False 关闭线程。
        self._deep = _DeepCheck(self) if deep_check else None
        self.coordinator.external_lock = lambda: self._provider().lock(self.slot)

    def _provider(self):
        if self.provider is None:
            self.provider = get_provider()
        return self.provider

    def _keyring(self):
        try:
            value = json.loads(self.keyring_path.read_bytes())
            if not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] < 1:
                raise ValueError()
            return value
        except FileNotFoundError:
            return None
        except (ValueError, TypeError) as exc:
            raise IntegrityFailure('统计描述文件无效') from exc

    def keyring_present(self):
        return self.keyring_path.exists()

    def is_configured(self):
        # 无可靠服务时仍禁止写入普通格式。
        return True

    def legacy_plaintext_readable(self):
        return not self._is_blocked() and not self._active and (self._legacy is not None or bool(
            self._state and self._state.get('phase') == 'preparing'))

    def _is_blocked(self):
        return self._wipe_scheduled or (self.directory / 'blocked.json').exists()

    def _dpapi(self, data, decrypt=False):
        from module.runtime.account_local import dpapi
        return dpapi(data, decrypt=decrypt)

    def _legacy_key(self, ring):
        if hasattr(self._provider(), 'prepare_legacy'):
            self._provider().prepare_legacy(self.slot, ring)
            return b'@host'
        try:
            key = self._dpapi(_b64d(ring['wrapped_local']), decrypt=True)
        except Exception as exc:
            raise VaultLocked('旧统计环境暂不可用') from exc
        payload = {k: v for k, v in ring.items() if k != 'mac'}
        mac_key = _subkey(key, 'opsi-stats/v1/keyring-mac')
        mac = hmac.new(mac_key, canonical(payload), hashlib.sha256).hexdigest()
        if len(key) != 32 or not hmac.compare_digest(mac, str(ring.get('mac', ''))):
            raise VaultLocked('旧统计描述文件校验失败，保留原数据')
        return key

    def _load(self):
        self._active = False
        self._legacy = None
        self._dek = None
        if self._is_blocked():
            raise VaultLocked('统计校验异常，原件和凭据已保留，暂停大世界统计读写')
        try:
            self._state = self._provider().load(self.slot)
        except ProviderUnavailable:
            ring = self._keyring()
            if ring and ring['version'] == 1:
                self._legacy = self._legacy_key(ring)
                return False
            raise
        self._active = bool(self._state and self._state.get('phase') == 'ready')
        if self._state and self._state.get('schema', 2) > 2:
            self._active = False
            raise VaultLocked('当前软件不支持此统计版本')
        if self._state and self._state.get('phase') != 'wiping' and self._state.get('algorithm') != ALGORITHM:
            self._active = False
            raise VaultLocked('当前软件不支持此统计算法')
        ring = self._keyring()
        if self._state:
            if self._state.get('phase') == 'wiping':
                self._finish_wipe()
                raise VaultLocked('旧统计重置状态已冻结，原件和凭据已保留')
            self._dek = self._provider().key(self._state)
            if self._state.get('phase') == 'migration':
                self._recover_migration()
                ring = self._keyring()
            if self._state.get('phase') == 'preparing':
                if ring and ring['version'] == 1:
                    self._legacy = self._legacy_key(ring)
                return False
            self._active = self._state.get('phase') == 'ready'
            if ring != self._descriptor():
                raise IntegrityFailure('统计描述文件与本机状态不匹配')
            self._active = True
            self._resolve_pending()
            return True
        if ring and ring['version'] >= 2:
            raise VaultLocked('当前设备或账户没有统计环境')
        if ring:
            self._legacy = self._legacy_key(ring)
        return False

    def _descriptor(self):
        return {'version': 2, 'algorithm': ALGORITHM, 'installation_id': self._state['installation_id'],
                'provider': self._provider().name}

    def ensure_ready(self):
        return self._refresh() is not False

    def _refresh(self):
        """解封安全服务并确认可用；同一协调锁持有期内只解封一次。

        进程内首次解封核对全部受保护路径的链值、行数、结构及文件摘要，
        并调度后台深检查。写入前只核对所写路径；统计页面另有浅检查入口。
        校验异常时保留原件和凭据，冻结读写并另存救援副本。
        """
        try:
            with self.coordinator.lock():
                if self._is_blocked():
                    self._active = False
                    self._dek = self._legacy = None
                    return False
                if self._active and self._fresh == self.coordinator.hold_token:
                    return True
                if self._load():
                    self._startup_check()
                    self._fresh = self.coordinator.hold_token
                    return True
                self.ensure_migrated()
                if self._active:
                    self._startup_check()
                    self._fresh = self.coordinator.hold_token
                return self._active
        except IntegrityFailure:
            if self._active:
                self._wipe('状态校验失败')
            return False
        except (ProviderUnavailable, VaultLocked, OSError, sqlite3.Error, portalocker_error()):
            return False

    writer_ready = ensure_ready

    def ensure_integrity(self):
        self.ensure_ready()

    def status(self):
        integrity = self.directory / 'integrity.json'
        return {'configured': self._active or self.keyring_present(), 'keyringPresent': self.keyring_present(),
                'lastWipe': json.loads(self.wipe_path.read_bytes()) if self.wipe_path.exists() else None,
                'blocked': self._is_blocked(),
                'lastMismatch': json.loads(integrity.read_bytes()) if integrity.exists() else None,
                'dropped': dict(self._dropped)}

    def record_dropped(self, kind):
        self._dropped[kind] = self._dropped.get(kind, 0) + 1

    def _path_digest(self, path):
        """受保护路径的内容摘要：数据库取逻辑映像，普通文件取原始字节。

        数据库摘要用 serialize() 的映像计算：与 WAL/checkpoint 状态无关，
        同一份内容在不同进程、不同时间得到同一摘要。
        """
        path = Path(path).resolve()
        if not path.exists():
            return None
        if path in set(self.coordinator.paths()):
            try:
                with closing(sqlite3.connect(path, timeout=5)) as conn:
                    return hashlib.sha256(conn.serialize()).hexdigest()
            except sqlite3.Error:
                return None
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return None

    def _expected(self):
        return self._state.setdefault('expect', {})

    def _expect_key(self, path):
        return str(Path(path).resolve().relative_to(self.root))

    def _record_evidence(self, key, detail, expected=None, actual=None):
        """把不一致证据落盘（integrity.json），失败不阻断冻结流程。"""
        try:
            durable_write(self.directory / 'integrity.json',
                          canonical({'at': time.strftime('%Y-%m-%d %H:%M:%S'), 'path': key,
                                     'detail': detail, 'expected': expected, 'actual': actual}))
        except OSError:
            pass

    def _tamper(self, key, detail, expected=None, actual=None):
        """检测到库外修改或替换：写证据、冻结读写、保存副本并中止当前操作。"""
        logger.warning(f'[统计-加密] 检测到统计文件被库外修改或替换: {key}（{detail}）')
        self._record_evidence(key, detail, expected, actual)
        try:
            self._wipe('检测到统计文件被库外修改或替换')
        except Exception:
            logger.exception('[统计-加密] 冻结或救援未完整执行，原件和凭据仍保留')
        raise VaultLocked('统计校验异常，原件和凭据已保留，暂停大世界统计读写')

    def _schedule_wipe(self, reason):
        """读取路径发现异常：立即持久化冻结，后台保存救援副本。"""
        if self._wipe_scheduled:
            return
        if not self._freeze(reason):
            return
        thread = threading.Thread(target=self._background_wipe, args=(reason,),
                                  name='statistics-tamper-rescue', daemon=True)
        self._wipe_thread = thread
        thread.start()

    def _background_wipe(self, reason):
        try:
            with self.coordinator.lock():
                self._rescue_wipe_copy()
        except Exception:
            logger.exception('[统计-加密] 后台救援副本未完成，冻结标记与原件仍保留')

    # ---- 写入意图标记（甲胄甲：区分崩溃窗口与篡改） ----

    def _pending_path(self):
        return self.directory / 'pending.json'

    def _pending_keys(self):
        try:
            data = json.loads(self._pending_path().read_bytes())
            return set(str(key) for key in data.get('paths', []))
        except FileNotFoundError:
            return set()
        except (ValueError, TypeError, AttributeError):
            logger.warning('[统计-加密] 写入标记文件不可读，按无标记处理')
            return set()

    def _write_pending(self, keys):
        durable_write(self._pending_path(),
                      canonical({'paths': sorted(keys), 'at': time.strftime('%Y-%m-%d %H:%M:%S')}))

    def _mark_pending(self, key):
        keys = self._pending_keys()
        if key in keys:
            return
        keys.add(key)
        try:
            self._write_pending(keys)
        except OSError as exc:
            raise VaultLocked('统计写入意图未能落盘，本次写入已中止') from exc

    def _clear_pending(self, key):
        keys = self._pending_keys()
        if key not in keys:
            return
        keys.discard(key)
        try:
            if keys:
                self._write_pending(keys)
            else:
                self._pending_path().unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(f'[统计-加密] 清理写入标记失败: {exc}')

    def _save_expectations(self):
        """把期望值写入安全服务；返回是否成功（失败保留写入标记待下次核对）。"""
        try:
            self._provider().save(self.slot, self._state)
            return True
        except (ProviderUnavailable, OSError) as exc:
            logger.warning(f'[统计-加密] 期望值更新未能写入安全服务: {exc}')
            return False

    # ---- 完整性链（浅检查的库内锚点） ----

    def _chain_key(self):
        return self._provider().chain_key(self.slot, self._state)

    def _chain_mac(self, seq, counts, fingerprint):
        payload = {'seq': int(seq), 'counts': counts, 'fingerprint': fingerprint,
                   'installation_id': self._state['installation_id']}
        return hmac.new(self._chain_key(), canonical(payload), hashlib.sha256).hexdigest()[:CHAIN_LEN]

    @staticmethod
    def _protected_counts(conn):
        """受保护表的实况行数（全表扫描，仅建链/重记时使用）；口径见触发器。"""
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {}
        for table in PROTECTED_TABLES:
            if table not in tables:
                continue
            if table == 'daily_summary_periods':
                counts[table] = conn.execute(
                    'SELECT COUNT(*) FROM daily_summary_periods WHERE report_text IS NOT NULL').fetchone()[0]
            else:
                counts[table] = conn.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
        return counts

    def _ensure_counters(self, conn):
        """建行数锚点的计数器表与触发器（幂等；升级/迁移重建时复用）。"""
        conn.execute(COUNTS_DDL)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in PROTECTED_TABLES:
            if table not in tables:
                continue
            for sql in _counter_triggers(table):
                conn.execute(sql)

    def _reset_counters(self, conn):
        """按库内实况重算计数器并返回行数（仅建链/重记路径调用，O(表)）。"""
        counts = self._protected_counts(conn)
        conn.execute('DELETE FROM ' + COUNT_TABLE)
        conn.executemany('INSERT INTO ' + COUNT_TABLE + '(name, rows) VALUES (?, ?)',
                         list(counts.items()))
        return counts

    def _live_counts(self, conn):
        """读取计数器（O(1)）；库外增删行会让它与链记录失配。"""
        return {name: int(rows) for name, rows in conn.execute('SELECT name, rows FROM ' + COUNT_TABLE)}

    @staticmethod
    def _schema_fingerprint(conn):
        """库结构指纹（含日志模式）：升级改表/切换 WAL 走甲胄乙豁免重记。

        自管的 __opsi_* 对象不参与指纹：它们的存在由链值/计数器自身守护。
        """
        mode = conn.execute('PRAGMA journal_mode').fetchone()
        rows = conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master "
                            "WHERE name NOT GLOB '__opsi_*' AND name NOT LIKE 'sqlite_%' "
                            "ORDER BY type,name").fetchall()
        return hashlib.sha256(canonical([mode[0] if mode else '', [list(row) for row in rows]])).hexdigest()[:SIG_LEN]

    def _has_chain_table(self, conn):
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                            (CHAIN_TABLE,)).fetchone() is not None

    def _read_chain(self, conn):
        """读取链行；数据库繁忙等环境错误一律向上抛（调用方按跳过/中止处理）。"""
        row = conn.execute('SELECT seq,chain,counts,fingerprint FROM ' + CHAIN_TABLE).fetchone()
        if row is None:
            return None
        try:
            counts = json.loads(row[2])
        except (TypeError, ValueError):
            return None
        return {'seq': int(row[0]), 'chain': row[1], 'counts': counts, 'fingerprint': row[3]}

    def _write_chain(self, conn, seq, counts, fingerprint):
        chain = self._chain_mac(seq, counts, fingerprint)
        conn.execute('INSERT OR REPLACE INTO ' + CHAIN_TABLE
                     + ' (id,seq,chain,counts,fingerprint) VALUES (1,?,?,?,?)',
                     (int(seq), chain, canonical(counts).decode('utf-8'), fingerprint))
        return chain

    def _init_database_chain(self, conn, key):
        """首次见到该库（升级/新建）：建链、建计数器并按现状记基线，不触发清空。"""
        self._mark_pending(key)
        with conn:
            conn.execute(CHAIN_DDL)
            self._ensure_counters(conn)
            counts = self._reset_counters(conn)
            fingerprint = self._schema_fingerprint(conn)
            chain = self._write_chain(conn, 1, counts, fingerprint)
        self._expected()[key] = {'s': 1, 'c': chain}
        logger.info(f'[统计-加密] 已建立完整性链: {key}')
        return True

    def _rebuild_database_chain(self, conn, key):
        """按现状重记链值与基线（甲胄乙/甲、显式重记）；不清空。"""
        self._mark_pending(key)
        try:
            row = self._read_chain(conn)
        except sqlite3.Error:
            row = None
        seq = row['seq'] + 1 if row else 1
        with conn:
            conn.execute(CHAIN_DDL)
            self._ensure_counters(conn)
            counts = self._reset_counters(conn)
            fingerprint = self._schema_fingerprint(conn)
            chain = self._write_chain(conn, seq, counts, fingerprint)
        self._expected()[key] = {'s': seq, 'c': chain}
        return True

    def _bump_database_chain(self, conn, key):
        """数据写入事务内同步更新链行（与数据原子提交）。"""
        row = self._read_chain(conn)
        if row is None:
            raise sqlite3.OperationalError('统计完整性链缺失')
        counts = self._live_counts(conn)
        fingerprint = self._schema_fingerprint(conn)
        seq = row['seq'] + 1
        chain = self._write_chain(conn, seq, counts, fingerprint)
        entry = self._expected().get(key)
        entry = dict(entry) if isinstance(entry, dict) else {}
        entry.update({'s': seq, 'c': chain})
        self._expected()[key] = entry

    def _verify_database(self, path, key):
        """浅检查单个受保护数据库；返回基线是否被更新（篡改将清空并中止）。"""
        expect = self._expected()
        entry = expect.get(key)
        if not (isinstance(entry, dict) and type(entry.get('s')) is int and entry.get('c')):
            entry = None
        if not path.exists():
            if entry is None:
                return False
            if key in self._pending_keys():
                expect.pop(key, None)
                return True
            self._tamper(key, '受保护数据库缺失')
        with closing(sqlite3.connect(path, timeout=VERIFY_TIMEOUT)) as conn:
            if not self._has_chain_table(conn):
                if entry is not None:
                    self._tamper(key, '完整性链缺失')
                return self._init_database_chain(conn, key)
            row = self._read_chain(conn)
            if row is None:
                if entry is not None:
                    self._tamper(key, '完整性链损坏')
                return self._rebuild_database_chain(conn, key)
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                (COUNT_TABLE,)).fetchone():
                if entry is not None:
                    self._tamper(key, '行数锚点缺失')
                return self._rebuild_database_chain(conn, key)
            counts = self._live_counts(conn)
            fingerprint = self._schema_fingerprint(conn)
            if entry is None:
                # 升级后的首次见到：链行自洽则采纳，否则按现状重记（无历史可对照）。
                if row['chain'] == self._chain_mac(row['seq'], row['counts'], row['fingerprint']) \
                        and row['counts'] == counts and row['fingerprint'] == fingerprint:
                    expect[key] = {'s': row['seq'], 'c': row['chain']}
                else:
                    return self._rebuild_database_chain(conn, key)
                return True
            if row['seq'] != entry['s'] or row['chain'] != entry['c']:
                if key in self._pending_keys():
                    logger.warning(f'[统计-加密] 发现未完成的写入窗口，按崩溃恢复重记基线: {key}')
                    merged = {k: v for k, v in entry.items() if k not in ('s', 'c')}
                    merged.update({'s': row['seq'], 'c': row['chain']})
                    expect[key] = merged
                    return True
                self._tamper(key, f'链值与期望不一致（库内 seq={row["seq"]}，期望 {entry["s"]}）',
                             expected={'seq': entry['s'], 'chain': entry['c']},
                             actual={'seq': row['seq'], 'chain': row['chain']})
            if row['chain'] != self._chain_mac(row['seq'], row['counts'], row['fingerprint']):
                self._tamper(key, '完整性链校验失败（链行被库外改写）')
            if row['counts'] != counts:
                self._tamper(key, '受保护表行数与链记录不一致（库外增删行）',
                             expected=row['counts'], actual=counts)
            if row['fingerprint'] != fingerprint:
                logger.warning(f'[统计-加密] 检测到库结构变化，按升级重记基线: {key}')
                return self._rebuild_database_chain(conn, key)
            if key in self._pending_keys():
                self._clear_pending(key)
            return False

    def _verify_protected_file(self, path, key):
        """浅检查单个受保护文件（字节摘要）；返回基线是否被更新。"""
        expect = self._expected()
        entry = expect.get(key)
        if not (isinstance(entry, dict) and entry.get('d')):
            entry = None
        if not path.exists():
            if entry is None:
                return False
            if key in self._pending_keys():
                expect.pop(key, None)
                return True
            self._tamper(key, '受保护统计文件缺失')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:SIG_LEN]
        if entry is None:
            expect[key] = {'d': digest}
            return True
        if entry['d'] != digest:
            if key in self._pending_keys():
                logger.warning(f'[统计-加密] 发现未完成的写入窗口，按崩溃恢复重记文件基线: {key}')
                expect[key] = {'d': digest}
                return True
            self._tamper(key, '文件内容与基线不一致',
                         expected={'digest': entry['d']}, actual={'digest': digest})
        if key in self._pending_keys():
            self._clear_pending(key)
        return False

    def _expect_deletions(self, seen):
        """甲胄丙：基线中存在、当前路径集合里没有、且无豁免 → 清空。"""
        changed = False
        pending = self._pending_keys()
        for key in sorted(set(self._expected()) - seen):
            path = self.root / key
            if self.coordinator.is_archive(path) or key in pending:
                self._expected().pop(key, None)
                changed = True
                continue
            self._tamper(key, '受保护统计文件缺失')
        return changed

    def _verify_all_shallow(self):
        """核对全部受保护路径（浅检查）；返回基线是否有更新。

        读库遭遇繁忙等环境错误时只跳过该路径本轮，绝不据此清空。
        """
        if not self._active or not self._state:
            return False
        changed = False
        touched = []
        seen = set()
        paths = set(self.coordinator.paths())
        for path in [*paths, *self.coordinator.files()]:
            path = Path(path).resolve()
            if self.coordinator.is_archive(path):
                continue
            key = self._expect_key(path)
            seen.add(key)
            try:
                if path in paths:
                    updated = self._verify_database(path, key)
                else:
                    updated = self._verify_protected_file(path, key)
            except (sqlite3.Error, OSError) as exc:
                logger.debug(f'[统计-加密] 浅检查暂不可用，本轮跳过: {key}（{type(exc).__name__}）')
                continue
            if updated:
                changed = True
                touched.append(key)
            if not self._active:
                return changed
        changed = self._expect_deletions(seen) or changed
        if changed and self._save_expectations():
            for key in touched:
                self._clear_pending(key)
        return changed

    def _startup_check(self):
        """每进程一次：启动（开机）核对全部受保护路径 + 调度后台深检查。"""
        if self._startup_checked:
            return
        self._startup_checked = True
        self._verified_hold = self.coordinator.hold_token
        self._verify_all_shallow()
        if self._deep:
            self._deep.kick(force=True)

    def verify_on_page_open(self):
        """打开/刷新统计页的核对点：毫秒级浅检查 + 后台深检查（节流）。"""
        try:
            with self.coordinator.lock():
                if not self.ensure_ready():
                    return
                if self._verified_hold != self.coordinator.hold_token:
                    self._verified_hold = self.coordinator.hold_token
                    self._verify_all_shallow()
        except (VaultError, ProviderUnavailable, OSError, sqlite3.Error, portalocker_error()) as exc:
            logger.debug(f'[统计-加密] 页面核对未完成: {type(exc).__name__}')
        if self._deep:
            self._deep.kick()

    def revouch(self, path):
        """按现状重记单条受保护路径的基线（豁免路径与测试使用；不触发清空）。"""
        path = Path(path).resolve()
        with self.coordinator.lock():
            if not self.ensure_ready():
                raise VaultLocked('统计运行环境暂不可用')
            if self.coordinator.is_archive(path):
                return
            key = self._expect_key(path)
            if path in set(self.coordinator.paths()):
                if not path.exists():
                    self._expected().pop(key, None)
                else:
                    with closing(sqlite3.connect(path, timeout=VERIFY_TIMEOUT)) as conn:
                        self._rebuild_database_chain(conn, key)
            else:
                if not path.exists():
                    self._expected().pop(key, None)
                else:
                    entry = self._expected().get(key)
                    entry = dict(entry) if isinstance(entry, dict) else {}
                    entry['d'] = hashlib.sha256(path.read_bytes()).hexdigest()[:SIG_LEN]
                    self._expected()[key] = entry
            if self._save_expectations():
                self._clear_pending(key)

    def deep_check_once(self):
        """同步执行一轮深检查（测试/诊断入口；生产路径一律走后台线程）。"""
        checker = self._deep or _DeepCheck(self)
        checker.run_once()

    def _resolve_pending(self):
        pending = self._state.get('pending')
        if not pending:
            return
        actual = self.coordinator.snapshot()
        if actual == self._state['root'] and actual != pending['root']:
            try:
                token = self.journal_path.read_bytes()
            except FileNotFoundError as exc:
                raise IntegrityFailure('统计提交记录缺失') from exc
            if hashlib.sha256(token).hexdigest() != pending['journal']:
                raise IntegrityFailure('统计提交记录不一致')
            images = json.loads(self._decode('opsi-stats/v2/commit', token.decode(),
                                           {'slot': self.slot, 'generation': pending['generation']}))
            self._restore_images(images, data_only=True)
            actual = self.coordinator.snapshot()
        if actual != pending['root']:
            raise IntegrityFailure('统计提交状态不一致')
        self._state.update(root=pending['root'], generation=pending['generation'])
        self._state.pop('pending', None)
        self._fresh = None
        self._provider().save(self.slot, self._state)
        self.journal_path.unlink(missing_ok=True)

    def _begin_commit(self, new_root, images):
        pending = {'root': new_root, 'generation': self._state['generation'] + 1}
        token = self._encode('opsi-stats/v2/commit', canonical(images),
                             {'slot': self.slot, 'generation': pending['generation']}).encode()
        durable_write(self.journal_path, token)
        pending['journal'] = hashlib.sha256(token).hexdigest()
        self._state['pending'] = pending
        self._provider().save(self.slot, self._state)

    def _end_commit(self):
        pending = self._state.pop('pending')
        self._state.update(root=pending['root'], generation=pending['generation'])
        self._fresh = None
        self._provider().save(self.slot, self._state)
        self.journal_path.unlink(missing_ok=True)

    def context(self, kind, instance, identity, period):
        return {'dataset': kind, 'instance': instance, 'identity': str(identity), 'period': str(period)}

    def report_context(self, instance, period):
        month = re.search(r'\d{4}-\d{2}', period)
        return self.context('reports', instance, period, month[0] if month else period)

    def file_context(self, kind, path, instance=None):
        path = Path(path).resolve()
        relative = str(path.relative_to(self.root)).replace('\\', '/')
        # 缓存与舰船文件覆盖多个月，逻辑周期属于整个历史集合。
        return self.context(kind, instance if instance is not None else relative,
                            relative, 'all-history')

    def check_database(self, path):
        if Path(path).resolve() not in self.coordinator.paths():
            raise VaultLocked('统计数据库不属于当前运行环境')

    def check_file(self, path):
        path = Path(path).resolve()
        if not path.is_relative_to(self.root):
            raise VaultLocked('统计文件不属于当前运行环境')
        valid = self.coordinator.is_archive(path) or (path.parent.parent == self.root / 'log' / 'cl1' and
            path.name in ('ship_exp_data.json', 'ship_exp_data.json.bak', 'cl1_monthly.json', 'cl1_monthly.json.bak')) or (
            path.parent == self.root / 'log' and path.name.startswith('azurstat_meowofficer_farming') and
            (path.name.endswith('.csv') or path.name.endswith('.csv.bak')))
        if not valid:
            raise VaultLocked('统计文件未登记')

    def _seal(self, kind, obj, context):
        if kind not in KINDS or not context or context.get('dataset') != kind:
            raise VaultError('记录身份不完整')
        aad = dict(context, schema=2, algorithm=ALGORITHM, installation_id=self._state['installation_id'])
        return BLOB_PREFIX + self._encode('opsi-stats/v2/' + kind, canonical(obj), aad)

    def _encode(self, info, raw, aad):
        return self._provider().encode(self.slot, self._state, info, raw, aad)

    def _decode(self, info, token, aad):
        return self._provider().decode(self.slot, self._state, info, token, aad)

    def seal(self, kind, obj, context=None):
        with self.coordinator.lock():
            if self._is_blocked():
                raise VaultLocked('统计校验异常，暂停写入大世界载荷')
            if not self._in_transaction and not self.ensure_ready():
                raise VaultLocked('统计运行环境暂不可用')
            return self._seal(kind, obj, context)

    def _open_legacy(self, kind, blob):
        if hasattr(self._provider(), 'legacy_open'):
            return self._provider().legacy_open(self.slot, kind, blob)
        try:
            raw = _b64d(blob[len(LEGACY_PREFIX):])
            cipher = AES.new(_subkey(self._legacy, 'opsi-stats/v1/' + kind), AES.MODE_GCM, nonce=raw[:12])
            cipher.update(('opsi-stats/v1/' + kind).encode())
            return json.loads(cipher.decrypt_and_verify(raw[12:-16], raw[-16:]))
        except (ValueError, TypeError) as exc:
            raise VaultLocked('旧记录校验失败，保留原数据') from exc

    def open_(self, kind, blob, context=None):
        with self.coordinator.lock():
            try:
                if self._is_blocked():
                    raise VaultLocked('统计校验异常，暂停读取大世界载荷')
                if not self._in_transaction:
                    try:
                        self._load()
                    except IntegrityFailure:
                        raise
                if isinstance(blob, str) and blob.startswith(LEGACY_PREFIX) and not self._active:
                    if self._legacy is None:
                        raise VaultLocked('旧统计环境暂不可用')
                    return self._open_legacy(kind, blob)
                if not self._active:
                    raise VaultLocked('统计运行环境暂不可用')
                if not context or context.get('dataset') != kind or not isinstance(blob, str) or not blob.startswith(BLOB_PREFIX):
                    raise IntegrityFailure('记录身份不一致')
                aad = dict(context, schema=2, algorithm=ALGORITHM, installation_id=self._state['installation_id'])
                return json.loads(self._decode('opsi-stats/v2/' + kind, blob[len(BLOB_PREFIX):], aad))
            except IntegrityFailure as exc:
                if self._active:
                    if not self._in_transaction:
                        self._wipe('记录校验失败')
                    elif isinstance(exc, RecordTampered):
                        # 降级返回空值前先冻结，防止同一批继续读写其他大世界记录。
                        self._schedule_wipe('记录校验失败')
                raise
            except ProviderUnavailable as exc:
                raise VaultLocked('统计运行环境暂不可用') from exc

    def open_or_none(self, kind, blob, context=None):
        try:
            return self.open_(kind, blob, context)
        except (VaultError, ProviderUnavailable, OSError, sqlite3.Error, portalocker_error()):
            return None

    @contextmanager
    def transaction(self, conn, path):
        """受保护数据库的写事务：写前浅检查（链值/行数/结构）→ 写入意图标记 →
        数据与链行同事务提交 → 期望值写入安全服务 → 清除标记。"""
        self.check_database(path)
        with self.coordinator.lock():
            if self._refresh() is False:
                raise VaultLocked('统计运行环境暂不可用')
            resolved = Path(path).resolve()
            key = self._expect_key(resolved)
            rebuilt = self._verify_database(resolved, key)
            if rebuilt and not self._save_expectations():
                logger.warning('[统计-加密] 重记基线未能写入安全服务，写入标记保留待下次核对')
            self._mark_pending(key)
            conn.execute('BEGIN IMMEDIATE')
            outer = self._in_transaction
            self._in_transaction = True
            try:
                yield conn
                if self._is_blocked():
                    raise VaultLocked('统计校验异常，当前事务停止提交并保留原数据')
                if conn.execute('PRAGMA foreign_keys').fetchone()[0] and conn.execute('PRAGMA foreign_key_check').fetchone():
                    raise sqlite3.IntegrityError('统计事务的延迟约束未满足')
                self._bump_database_chain(conn, key)
                conn.commit()
            except BaseException:
                conn.rollback()
                self._in_transaction = outer
                if not rebuilt:
                    self._clear_pending(key)
                raise
            self._in_transaction = outer
            if self._save_expectations():
                self._clear_pending(key)
            else:
                logger.warning('[统计-加密] 期望值未能写入安全服务，保留写入标记待下次核对')
            if self._deep:
                self._deep.note_write()

    @contextmanager
    def reading(self):
        with self.coordinator.lock():
            outer = self._in_transaction
            try:
                if self._is_blocked():
                    raise VaultLocked('统计校验异常，暂停读取大世界载荷')
                if not outer:
                    # 每批读取重新核对描述与凭据，不能沿用上一批的已解封状态。
                    if not self.ensure_ready() and not self.legacy_plaintext_readable():
                        raise VaultLocked('统计运行环境暂不可用')
                    if self._active or self._legacy is not None:
                        self._in_transaction = True
                yield
            except IntegrityFailure:
                if self._active and not outer:
                    self._wipe('状态校验失败')
                raise
            finally:
                self._in_transaction = outer

    def write_file(self, kind, path, data, context=None, wrapper=False):
        self.check_file(path)
        with self.coordinator.lock():
            if not self.ensure_ready():
                raise VaultLocked('统计运行环境暂不可用')
            resolved = Path(path).resolve()
            blob = self._seal(kind, data, context or self.file_context(kind, path))
            raw = canonical({WRAPPER_KEY: True, 'payload': blob}) if wrapper else blob.encode()
            if self.coordinator.is_archive(resolved):
                # 归档/备份载荷不参与基线核对（甲胄丙豁免），直接原子落盘。
                durable_write(resolved, raw)
                return
            key = self._expect_key(resolved)
            changed = self._verify_protected_file(resolved, key)
            digest = hashlib.sha256(raw).hexdigest()[:SIG_LEN]
            entry = self._expected().get(key)
            entry = dict(entry) if isinstance(entry, dict) else {}
            if entry.get('d') != digest:
                changed = True
            entry['d'] = digest
            self._expected()[key] = entry
            self._mark_pending(key)
            durable_write(resolved, raw)
            if changed:
                if self._save_expectations():
                    self._clear_pending(key)
                else:
                    logger.warning('[统计-加密] 期望值未能写入安全服务，保留写入标记待下次核对')
            else:
                self._clear_pending(key)

    def remove_files(self, paths):
        with self.coordinator.lock():
            if not self.ensure_ready():
                raise VaultLocked('统计运行环境暂不可用')
            paths = {Path(path).resolve(): None for path in paths}
            if not set(paths).issubset(set(self.coordinator.files())):
                raise VaultError('统计清理路径无效')
            keys = [self._expect_key(path) for path in paths
                    if not self.coordinator.is_archive(path)]
            for key in keys:
                self._mark_pending(key)
            for path in paths:
                path.unlink(missing_ok=True)
            changed = False
            for key in keys:
                if self._expected().pop(key, None) is not None:
                    changed = True
            if changed:
                if self._save_expectations():
                    for key in keys:
                        self._clear_pending(key)
                # 保存失败：保留标记，缺失 + 标记 → 下次核对按崩溃窗口丢弃记录
            else:
                for key in keys:
                    self._clear_pending(key)

    def _kick_migration(self):
        if not self._background_migration or self._migration_kicked:
            return
        self._migration_kicked = True
        threading.Thread(target=self.ensure_ready, name='statistics-transition', daemon=True).start()

    def ensure_migrated(self):
        with self.coordinator.lock():
            if self._load():
                return {'skipped': False}
            return self._migrate()

    def _migrate(self):
        ring = self._keyring()
        before_ring = self.keyring_path.read_bytes() if ring else None
        provider = self._provider()
        if not self._state:
            self._state = {'phase': 'preparing', 'schema': 2, 'algorithm': ALGORITHM, 'installation_id': uuid.uuid4().hex,
                           'key': provider.new_key(), 'root': '', 'generation': 0}
            provider.save(self.slot, self._state)
        self._dek = provider.key(self._state)
        if ring and ring['version'] == 1:
            self._legacy = self._legacy_key(ring)
        connections = {}
        readers = {}
        original, after, overrides = {}, {}, {}
        try:
            for path in self.coordinator.paths():
                if not path.exists():
                    continue
                reader = sqlite3.connect(path, timeout=5)
                readers[path] = reader
                reader.execute('BEGIN IMMEDIATE')
                image = self._standalone_image(reader.serialize())
                original[str(path.relative_to(self.root))] = _b64e(image)
                conn = sqlite3.connect(':memory:')
                connections[path] = conn
                conn.deserialize(image)
                conn.execute('PRAGMA secure_delete=ON')
                self._convert_database(conn)
                conn.commit()
                after[str(path.relative_to(self.root))] = _b64e(conn.serialize())
                overrides[path] = conn
            for path in self.coordinator.files():
                raw = path.read_bytes()
                original[str(path.relative_to(self.root))] = _b64e(raw)
                kind = 'archives' if self.coordinator.is_archive(path) or 'cl1_monthly' in path.name else ('ships' if '.json' in path.name else 'loot')
                context = self.file_context(kind, path)
                if self.coordinator.is_archive(path):
                    data = {'bytes': _b64e(raw)}
                    blob = self._seal(kind, data, context)
                    self._check_roundtrip(kind, blob, data, context)
                    updated = canonical({WRAPPER_KEY: True, 'payload': blob})
                elif '.json' in path.name:
                    data = json.loads(raw)
                    if data.get(LEGACY_WRAPPER_KEY):
                        data = self._open_legacy(kind, data['payload'])
                    blob = self._seal(kind, data, context)
                    self._check_roundtrip(kind, blob, data, context)
                    updated = canonical({WRAPPER_KEY: True, 'payload': blob})
                else:
                    text = raw.decode('utf-8')
                    if text.startswith(LEGACY_PREFIX):
                        data = self._open_legacy('loot', text)
                    else:
                        rows = list(csv.reader(io.StringIO(text)))
                        data = {'header': rows[0], 'rows': rows[1:]}
                    blob = self._seal('loot', data, context)
                    self._check_roundtrip('loot', blob, data, context)
                    updated = blob.encode()
                after[str(path.relative_to(self.root))] = _b64e(updated)
                overrides[path] = updated
            new_root = self.coordinator.snapshot(overrides)
            journal = {'before': original, 'after': after, 'keyring': _b64e(before_ring) if before_ring else None,
                       'descriptor': self._descriptor(), 'root': new_root}
            token = self._encode('opsi-stats/v2/transition', canonical(journal), self.slot).encode()
            durable_write(self.journal_path, token)
            self._state.update(phase='migration', journal=hashlib.sha256(token).hexdigest())
            provider.save(self.slot, self._state)
            for reader in readers.values():
                reader.rollback()
                reader.close()
            readers.clear()
            self._restore_images(after)
            durable_write(self.keyring_path, canonical(self._descriptor()))
            if self.coordinator.snapshot() != new_root:
                raise VaultLocked('迁移回读未通过')
            ready = dict(self._state, phase='ready', root=new_root, generation=1)
            ready.pop('journal', None)
            provider.save(self.slot, ready)
            self._state = ready
            self._active = True
            self._legacy = None
            self.journal_path.unlink(missing_ok=True)
            return {'skipped': False, 'cl1': len(original), 'files': len(after)}
        except BaseException:
            for reader in readers.values():
                reader.rollback()
                reader.close()
            readers.clear()
            for conn in connections.values():
                conn.rollback()
                conn.close()
            connections.clear()
            # 未发布 V2 前的任何失败都不触发清空；可重入恢复原状态。
            if self._state.get('phase') == 'migration':
                self._recover_migration(rollback=True)
            raise
        finally:
            for reader in readers.values():
                reader.close()
            for conn in connections.values():
                conn.close()

    @staticmethod
    def _standalone_image(raw):
        # serialize 已合并当前快照；独立副本不依赖原目录的 WAL。
        if raw[:16] != b'SQLite format 3\x00':
            raise VaultLocked('统计快照不可用')
        return raw[:18] + b'\x01\x01' + raw[20:]

    def _check_roundtrip(self, kind, blob, data, context):
        aad = dict(context, schema=2, algorithm=ALGORITHM, installation_id=self._state['installation_id'])
        decoded = json.loads(self._decode('opsi-stats/v2/' + kind, blob[len(BLOB_PREFIX):], aad))
        if decoded != data:
            raise VaultLocked('迁移回读未通过')

    def _convert_database(self, conn):
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, column, kind, fields in [('cl1_data', 'secure_json', 'cl1', CL1_SECURE_FIELDS),
                                            ('opsi_items', 'secure_payload', 'loot', LOOT_SECURE_FIELDS),
                                            ('resource_snapshots', 'opsi_payload', 'res', RES_SECURE_FIELDS),
                                            ('daily_summary_cl1_events', 'secure_payload', 'daily',
                                             ('duration_seconds', 'estimated_exp'))]:
            if table not in tables:
                continue
            columns = [r[1] for r in conn.execute('PRAGMA table_info(' + table + ')')]
            if column not in columns:
                conn.execute('ALTER TABLE ' + table + ' ADD COLUMN ' + column + ' TEXT')
                columns.append(column)
            for values in conn.execute('SELECT * FROM ' + table).fetchall():
                row = dict(zip(columns, values))
                if kind == 'cl1':
                    full = json.loads(row['data_json']) if row.get('data_json') else None
                    if full is None and row.get('encrypted_blob'):
                        from module.statistics.cl1_legacy import derive_legacy_key, decrypt_legacy_payload
                        from module.base.device_id import get_device_id, get_old_device_id
                        for device in (get_device_id(), get_old_device_id()):
                            if device:
                                try:
                                    full = decrypt_legacy_payload(row['encrypted_blob'], derive_legacy_key(device))
                                except (ValueError, TypeError, UnicodeError):
                                    continue
                                if full:
                                    break
                    if not isinstance(full, dict):
                        raise VaultLocked('旧快照无法读取')
                    public, data = partition_cl1(full)
                else:
                    data = {f: row.get(f) for f in fields}
                if row.get(column):
                    if not row[column].startswith(LEGACY_PREFIX):
                        raise VaultLocked('迁移源格式不支持')
                    data.update(self._open_legacy(kind, row[column]))
                context = row_context(kind, row)
                blob = self._seal(kind, data, context)
                self._check_roundtrip(kind, blob, data, context)
                if kind == 'cl1':
                    conn.execute('UPDATE cl1_data SET data_json=?, secure_json=?, encrypted_blob=NULL WHERE instance=? AND month=?',
                                 (canonical(public).decode(), blob, row['instance'], row['month']))
                else:
                    clear = ','.join(f + ('=0' if kind == 'daily' else '=NULL') for f in fields if f in columns)
                    conn.execute('UPDATE ' + table + ' SET ' + column + '=?,' + clear + ' WHERE id=?', (blob, row['id']))
        if 'daily_summary_periods' in tables:
            for instance, period, text in conn.execute('SELECT instance,period_key,report_text FROM daily_summary_periods WHERE report_text IS NOT NULL').fetchall():
                context = self.report_context(instance, period)
                data = {'text': text}
                blob = self._seal('reports', data, context)
                self._check_roundtrip('reports', blob, data, context)
                conn.execute('UPDATE daily_summary_periods SET report_text=? WHERE instance=? AND period_key=?', (blob, instance, period))

    def _restore_images(self, images, data_only=False):
        for relative, encoded in images.items():
            path = (self.root / relative).resolve()
            allowed_file = (path.parent.parent == self.root / 'log' / 'cl1' and path.name in
                            ('ship_exp_data.json', 'ship_exp_data.json.bak', 'cl1_monthly.json', 'cl1_monthly.json.bak')) or \
                (path.parent == self.root / 'log' and path.name.startswith('azurstat_meowofficer_farming') and
                 (path.name.endswith('.csv') or path.name.endswith('.csv.bak')))
            if not path.is_relative_to(self.root) or (path not in self.coordinator.paths() and not allowed_file and
                                                    not self.coordinator.is_archive(path)):
                raise VaultLocked('迁移记录路径无效')
            if encoded is None:
                path.unlink(missing_ok=True)
                continue
            raw = _b64d(encoded)
            if path in self.coordinator.paths():
                if data_only:
                    with closing(sqlite3.connect(':memory:')) as memory, closing(sqlite3.connect(path, timeout=5)) as target:
                        memory.deserialize(self._standalone_image(raw))
                        self._restore_rows(memory, target)
                else:
                    # 原数据库不写迁移中间值；只发布已经回读通过的完整映像。
                    durable_write(path, self._standalone_image(raw))
                    for suffix in ('-wal', '-shm', '-journal'):
                        Path(str(path) + suffix).unlink(missing_ok=True)
            else:
                durable_write(path, raw)

    @staticmethod
    def _restore_rows(source, target):
        tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        protected_tables = ('cl1_data', 'opsi_items', 'resource_snapshots', 'daily_summary_cl1_events', 'daily_summary_periods')
        triggers = [row for row in source.execute("SELECT name,tbl_name,sql FROM sqlite_master WHERE type='trigger'")
                    if row[1] in protected_tables]
        with target:
            target.execute('BEGIN IMMEDIATE')
            for name, table in target.execute("SELECT name,tbl_name FROM sqlite_master WHERE type='trigger'").fetchall():
                if table in protected_tables:
                    target.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
            for table in protected_tables:
                if table not in tables:
                    continue
                columns = [r[1] for r in source.execute('PRAGMA table_info(' + table + ')')]
                protected = {'cl1_data': ['secure_json', 'encrypted_blob'],
                             'resource_snapshots': ['opsi_payload', 'action_point', 'yellow_coin', 'purple_coin'],
                             'daily_summary_periods': ['report_text']} .get(table)
                if table in ('opsi_items', 'daily_summary_cl1_events'):
                    target.execute('DELETE FROM ' + table)
                else:
                    identity = ['instance', 'month'] if table == 'cl1_data' else \
                        (['instance', 'period_key'] if table == 'daily_summary_periods' else ['id'])
                    present = {tuple(row) for row in source.execute('SELECT ' + ','.join(identity) + ' FROM ' + table)}
                    for key in target.execute('SELECT ' + ','.join(identity) + ' FROM ' + table).fetchall():
                        if tuple(key) not in present:
                            target.execute('DELETE FROM ' + table + ' WHERE ' + ' AND '.join(k + '=?' for k in identity), key)
                for values in source.execute('SELECT * FROM ' + table):
                    row = dict(zip(columns, values))
                    if protected:
                        identity = ['instance', 'month'] if table == 'cl1_data' else \
                            (['instance', 'period_key'] if table == 'daily_summary_periods' else ['id'])
                        condition = ' AND '.join(k + '=?' for k in identity)
                        key = [row[k] for k in identity]
                        if target.execute('SELECT 1 FROM ' + table + ' WHERE ' + condition, key).fetchone():
                            target.execute('UPDATE ' + table + ' SET ' + ','.join(k + '=?' for k in protected)
                                           + ' WHERE ' + condition, [row[k] for k in protected] + key)
                            continue
                    target.execute('INSERT INTO ' + table + '(' + ','.join(columns) + ') VALUES ('
                                   + ','.join('?' for _ in columns) + ')', values)
            for _, _, sql in triggers:
                target.execute(sql)

    def _recover_migration(self, rollback=True):
        try:
            token = self.journal_path.read_bytes()
            if hashlib.sha256(token).hexdigest() != self._state['journal']:
                raise VaultLocked('迁移记录未通过校验，保留当前数据')
            journal = json.loads(self._decode('opsi-stats/v2/transition', token.decode(), self.slot))
            self._restore_images(journal['before'])
            if journal['keyring']:
                durable_write(self.keyring_path, _b64d(journal['keyring']))
            else:
                self.keyring_path.unlink(missing_ok=True)
            self._state.update(phase='preparing', root='', generation=0)
            self._state.pop('journal', None)
            self._provider().save(self.slot, self._state)
            self._active = False
            self.journal_path.unlink(missing_ok=True)
        except IntegrityFailure as exc:
            raise VaultLocked('迁移记录未通过校验，保留当前数据') from exc

    def _rescue_wipe_copy(self):
        """冻结前把受保护文件与凭据状态复制到救援目录，原件和凭据仍保留。"""
        try:
            folder = self.directory / time.strftime('rescue-%Y%m%d-%H%M%S')
            written = 0
            for path in [*self.coordinator.paths(), *self.coordinator.files()]:
                if not path.exists():
                    continue
                target = folder / f'{written:03d}-{path.name}'
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(path.read_bytes())
                written += 1
            if self._state:
                folder.mkdir(parents=True, exist_ok=True)
                (folder / 'provider_state.json').write_bytes(canonical(self._state))
            logger.warning(f'[统计-加密] 冻结前已救援备份 {written} 个文件到 {folder}')
        except OSError as exc:
            logger.warning(f'[统计-加密] 冻结前救援备份失败: {exc}')

    def _freeze(self, reason):
        """在协调锁内先停止解密和写入，再持久化跨进程冻结标记。"""
        self._wipe_scheduled = True
        self._fresh = None
        self._dek = self._legacy = None
        self._active = False
        blocked = self.directory / 'blocked.json'
        if blocked.exists():
            return False
        durable_write(blocked, canonical({
            'reason': reason, 'at': time.strftime('%Y-%m-%d %H:%M:%S'),
        }))
        logger.error('[统计-加密] 校验异常，已保留原件和凭据并暂停大世界统计读写')
        return True

    def _quarantine(self, reason):
        """校验失败时冻结读写，保留统计原件、描述文件与安全服务凭据。"""
        with self.coordinator.lock():
            if self._freeze(reason):
                self._rescue_wipe_copy()

    def _wipe(self, reason):
        # 按用户确认的保留策略，原清空入口统一冻结，不撤销凭据或删除历史。
        self._quarantine(reason)

    def _finish_wipe(self):
        # 延续用户已确认的冻结策略，旧清空状态也不能继续删除尚存历史或凭据。
        self._quarantine('旧统计环境处于重置阶段，保留剩余原件和凭据')

    def wipe(self, reason):
        self._wipe(reason)


class _DeepCheck:
    """后台深检查：全量映像摘要（数据库）/字节摘要（文件），绝不进入同步路径。

    防误报用“序号乐观并发”：先读链序号 → 算摘要 → 再读链序号，期间序号变化
    说明有正常写入插队，本轮放弃、下次再来；任何异常同样只放弃本轮，绝不据此
    清空。删除/初始化/结构变化的判定归浅检查。
    """

    def __init__(self, vault):
        self.vault = vault
        self._event = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._last = 0.0
        self._writes = 0

    def kick(self, force=False):
        self._ensure_thread()
        if force:
            with self._lock:
                self._last = 0.0
        self._event.set()

    def note_write(self):
        with self._lock:
            self._writes += 1
            due = self._writes >= DEEP_CHECK_WRITE_CADENCE
            if due:
                self._writes = 0
        if due:
            self.kick()

    def _ensure_thread(self):
        with self._lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(target=self._loop, name='statistics-deep-check', daemon=True)
            self._thread.start()

    def _loop(self):
        while True:
            triggered = self._event.wait(timeout=DEEP_CHECK_PERIODIC)
            self._event.clear()
            with self._lock:
                run = (not triggered) or (time.monotonic() - self._last >= DEEP_CHECK_MIN_INTERVAL)
            if not run:
                continue
            try:
                self.run_once()
            except Exception:
                logger.debug('[统计-加密] 深检查本轮放弃', exc_info=True)
            with self._lock:
                self._last = time.monotonic()

    def run_once(self):
        """执行一轮深检查（后台线程里跑；测试与诊断可直接调用）。"""
        vault = self.vault
        if not vault._active or not vault._state:
            return
        paths = set(vault.coordinator.paths())
        for path in [*paths, *vault.coordinator.files()]:
            path = Path(path).resolve()
            if vault.coordinator.is_archive(path):
                continue
            try:
                if path in paths:
                    self._check_database(path)
                else:
                    self._check_file(path)
            except (sqlite3.Error, OSError, VaultLocked) as exc:
                logger.debug(f'[统计-加密] 深检查暂不可用: {path.name}（{type(exc).__name__}）')
            if not vault._active:
                return

    @staticmethod
    def _read_seq(path):
        try:
            with closing(sqlite3.connect(path, timeout=VERIFY_TIMEOUT)) as conn:
                row = conn.execute('SELECT seq FROM ' + CHAIN_TABLE).fetchone()
        except sqlite3.Error:
            return None
        return int(row[0]) if row else None

    def _check_database(self, path):
        vault = self.vault
        key = vault._expect_key(path)
        before = self._read_seq(path)
        if before is None:
            return  # 链缺失/未初始化：由浅检查负责
        digest = vault._path_digest(path)
        if digest is None:
            return
        if self._read_seq(path) != before:
            return  # 有正常写入插队，本轮放弃
        with vault.coordinator.lock():
            if not vault._active:
                return
            try:
                vault._load()
            except (VaultError, ProviderUnavailable, OSError, sqlite3.Error):
                return
            if not vault._active:
                return
            entry = vault._expected().get(key)
            if not isinstance(entry, dict):
                return
            try:
                with closing(sqlite3.connect(path, timeout=VERIFY_TIMEOUT)) as conn:
                    row = vault._read_chain(conn)
                    fingerprint = vault._schema_fingerprint(conn)
            except sqlite3.Error:
                return
            if row is None or row['seq'] != before:
                return  # 链缺失或有写入插队：本轮放弃
            if row['fingerprint'] != fingerprint:
                return  # 结构/日志模式变化在途：交浅检查按升级重记
            digest = digest[:SIG_LEN]
            if entry.get('d') and entry.get('q') == before:
                if entry['d'] != digest:
                    vault._tamper(key, '全量映像与基线不一致（字节级改动）',
                                  expected={'digest': entry['d']}, actual={'digest': digest})
                return
            # 首次深检查或序号已前进（期间写入已被写路径逐笔核对）：以现状记/重记摘要。
            entry['d'] = digest
            entry['q'] = before
            vault._save_expectations()

    def _check_file(self, path):
        vault = self.vault
        key = vault._expect_key(path)
        with vault.coordinator.lock():
            if not vault._active:
                return
            try:
                vault._load()
            except (VaultError, ProviderUnavailable, OSError, sqlite3.Error):
                return
            if not vault._active:
                return
            entry = vault._expected().get(key)
            if not (isinstance(entry, dict) and entry.get('d')):
                return
            if not path.exists():
                return  # 删除判定归浅检查
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:SIG_LEN]
            if entry['d'] != digest:
                vault._tamper(key, '文件字节与基线不一致',
                              expected={'digest': entry['d']}, actual={'digest': digest})


def portalocker_error():
    import portalocker
    return portalocker.exceptions.LockException


_VAULT = None


def get_vault():
    global _VAULT
    if _VAULT is None:
        _VAULT = Vault()
    return _VAULT


def set_vault(vault):
    global _VAULT
    _VAULT = vault


def is_configured():
    return get_vault().is_configured()


def writer_ready():
    return get_vault().writer_ready()


def seal(kind, obj, context=None):
    return get_vault().seal(kind, obj, context)


def open_(kind, blob, context=None):
    return get_vault().open_(kind, blob, context)


def open_or_none(kind, blob, context=None):
    return get_vault().open_or_none(kind, blob, context)


def ensure_migrated():
    return get_vault().ensure_migrated()


def status():
    return get_vault().status()


def verify_on_page_open():
    """统计页面打开/刷新的核对点（由 WebUI 报表入口调用）。"""
    get_vault().verify_on_page_open()


def record_dropped(kind):
    get_vault().record_dropped(kind)


def checked_read(function):
    @wraps(function)
    def read(*args, **kwargs):
        vault = get_vault()
        if args and hasattr(args[0], 'db_path'):
            vault.check_database(args[0].db_path)
        elif args and hasattr(args[0], '_path'):
            vault.check_file(args[0]._path)
        elif 'AzurStats' in function.__globals__:
            stats = function.__globals__['AzurStats']
            if function.__name__ == 'load_meowofficer_farming':
                vault.check_file(stats._meowofficer_farming_path(kwargs.get('instance', args[0] if args else None)))
            else:
                vault.check_database(stats.LOCAL_DB)
        elif '_LOCAL_DB' in function.__globals__:
            vault.check_database(function.__globals__['_LOCAL_DB'])
        try:
            with vault.reading():
                return function(*args, **kwargs)
        except (ProviderUnavailable, VaultLocked, OSError, sqlite3.Error, portalocker_error()):
            # 迁移占用文件锁时沿用只读降级，不把暂时忙碌误判为统计损坏。
            return function(*args, **kwargs)
    return read
