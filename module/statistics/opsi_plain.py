"""普通统计存储，以及从旧加密存储到普通格式的无损迁移。

保留协调锁、SQLite 事务和原子文件替换。旧密文只在迁移期间读取，
正常读写不访问 OS 凭据、Broker 或加密校验链。
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import shutil
import sqlite3
import uuid
from contextlib import closing, contextmanager, nullcontext
from datetime import datetime
from pathlib import Path

from module.logger import logger
from module.statistics.opsi_secure import (
    BLOB_PREFIX, LEGACY_PREFIX, LEGACY_WRAPPER_KEY, WRAPPER_KEY,
    CHAIN_TABLE, COUNT_TABLE, LOOT_SECURE_FIELDS, RES_SECURE_FIELDS,
    SIG_LEN, Vault, VaultError, VaultLocked, row_context,
)
from module.statistics.opsi_state import canonical, durable_write


DATASETS = (
    ('cl1_data', 'secure_json', 'cl1', ()),
    ('opsi_items', 'secure_payload', 'loot', LOOT_SECURE_FIELDS),
    ('resource_snapshots', 'opsi_payload', 'res', RES_SECURE_FIELDS),
    ('daily_summary_cl1_events', 'secure_payload', 'daily', ('duration_seconds', 'estimated_exp')),
)


def _tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(conn, table):
    return [row[1] for row in conn.execute('PRAGMA table_info(' + table + ')')]


def _encrypted_text(value):
    return isinstance(value, str) and value.startswith((BLOB_PREFIX, LEGACY_PREFIX))


def _database_needs_migration(conn, include_legacy_aes=True):
    tables = _tables(conn)
    if CHAIN_TABLE in tables or COUNT_TABLE in tables:
        return True
    for table, column, _, _ in DATASETS:
        if table not in tables:
            continue
        columns = _columns(conn, table)
        for field in (column, 'encrypted_blob') if table == 'cl1_data' and include_legacy_aes else (column,):
            if field in columns and conn.execute(
                    f'SELECT 1 FROM {table} WHERE {field} IS NOT NULL AND length({field}) > 0 LIMIT 1').fetchone():
                return True
    return bool('daily_summary_periods' in tables and conn.execute(
        "SELECT 1 FROM daily_summary_periods WHERE report_text LIKE 'OPSIV2.XCHACHA20-POLY1305.%' "
        "OR report_text LIKE 'OPSIV1.%' LIMIT 1").fetchone())


def _row_bytes(row):
    # SQLite 的未知业务 BLOB 也原样保留；仅摘要表示需要转为 JSON 可序列化类型。
    return canonical({key: {'blob': base64.b64encode(value).decode()} if isinstance(value, bytes) else value
                      for key, value in row.items()})


class _MigrationReader(Vault):
    """只读解密器：验证失败不修改源数据、凭据或另启后台恢复线程。"""

    def _resolve_pending(self):
        if self._state.get('pending') or self._pending_keys():
            raise VaultLocked('旧加密提交尚未恢复，请先用原版本完成恢复')

    def _recover_migration(self, rollback=False):
        raise VaultLocked('旧加密迁移尚未结束，请先用原版本完成恢复')

    def _finish_wipe(self):
        raise VaultLocked('旧加密状态已冻结，保留原件')

    def _schedule_wipe(self, reason):
        self._wipe_scheduled = True

    def verify_sources(self):
        if not self._active:
            return
        for relative, entry in self._expected().items():
            path = (self.root / relative).resolve()
            if self.coordinator.is_archive(path):
                continue
            if path in self.coordinator.paths():
                with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as conn:
                    if isinstance(entry, dict) and 's' in entry and 'c' in entry:
                        chain = self._read_chain(conn)
                        actual_counts = self._protected_counts(conn)
                        # 旧版建表事务可能尚未给新表登记计数器；只核对已有锚点，
                        # 新表的每条记录仍会逐条认证、迁移并回读，不重新写旧基线。
                        if (not chain or chain['seq'] != entry['s'] or chain['chain'] != entry['c'] or
                                chain['chain'] != self._chain_mac(chain['seq'], chain['counts'], chain['fingerprint']) or
                                chain['counts'] != self._live_counts(conn) or
                                any(actual_counts.get(name) != count for name, count in chain['counts'].items())):
                            raise VaultLocked('旧统计校验链不一致，保留原件')
            else:
                self.check_file(path)
                if isinstance(entry, dict) and entry.get('d') and PlainStatisticsStore._hash(path)[:SIG_LEN] != entry['d']:
                    raise VaultLocked('旧统计文件与基线不一致，保留原件')


class PlainStatisticsStore(Vault):
    """沿用统计调用接口，载荷保存到普通业务列与 JSON/CSV 文件。"""

    encrypted = False

    def __init__(self, root=None, provider=None):
        super().__init__(root, provider=provider, background_migration=False, deep_check=False)
        self.coordinator.external_lock = None
        self.mode_path = self.directory / 'plaintext.json'
        self.transition_path = self.directory / 'plaintext-transition.json'
        self._state = {'installation_id': 'plaintext'}
        self._checked = False
        self._migration_error = None

    def ensure_ready(self):
        with self.coordinator.lock():
            if self._checked:
                return True
            try:
                # 发布途中退出时先回滚，不让混合版本进入业务读写。
                if self.transition_path.exists():
                    self._rollback(json.loads(self.transition_path.read_bytes()))
                paths = self._sources()
                if paths:
                    self._migrate(paths)
                elif not self.mode_path.exists():
                    durable_write(self.mode_path, canonical({'version': 1, 'mode': 'plaintext'}))
                self._checked = True
                self._migration_error = None
                return True
            except Exception as exc:
                error = type(exc).__name__
                if self._migration_error != error:
                    logger.warning(f'[统计-迁移] 取消加密未完成，保留原件并暂停统计写入：{error}')
                self._migration_error = error
                return False

    writer_ready = ensure_ready

    def ensure_migrated(self):
        return self.ensure_ready()

    def is_configured(self):
        # 即使未设置密钥，也必须先确认旧密文已完整迁移。
        return True

    def legacy_plaintext_readable(self):
        return self.ensure_ready()

    def verify_on_page_open(self):
        self.ensure_ready()

    def status(self):
        return {'configured': True, 'encrypted': False, 'keyringPresent': self.keyring_present(),
                'blocked': not self.ensure_ready(), 'migrationError': self._migration_error,
                'dropped': dict(self._dropped)}

    @contextmanager
    def reading(self):
        with self.coordinator.lock():
            if not self.ensure_ready():
                raise VaultLocked('旧统计尚未完成迁移，保留原数据')
            yield

    @contextmanager
    def transaction(self, conn, path):
        self.check_database(path)
        with self.coordinator.lock():
            if not self.ensure_ready():
                raise VaultLocked('旧统计尚未完成迁移，拒绝覆盖')
            conn.execute('BEGIN IMMEDIATE')
            try:
                yield conn
                if conn.execute('PRAGMA foreign_keys').fetchone()[0] and conn.execute('PRAGMA foreign_key_check').fetchone():
                    raise sqlite3.IntegrityError('统计事务的延迟约束未满足')
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def seal(self, kind, obj, context=None):
        raise VaultError('普通统计存储不能生成密文')

    def open_(self, kind, blob, context=None):
        # 正常读写前已迁移全部载荷；遇到后放入的密文不能伪装成空统计。
        raise VaultLocked('发现未迁移的统计密文，请停止写入并重启服务完成迁移')

    @staticmethod
    def _file_bytes(kind, data):
        if kind == 'loot':
            if not isinstance(data, dict) or not isinstance(data.get('header'), list) or not isinstance(data.get('rows'), list):
                raise ValueError('统计 CSV 结构无效')
            stream = io.StringIO(newline='')
            writer = csv.writer(stream, lineterminator='\n')
            writer.writerow(data['header'])
            writer.writerows(data['rows'])
            return stream.getvalue().encode('utf-8')
        if kind == 'archives' and isinstance(data, dict) and set(data) == {'bytes'}:
            return base64.b64decode(data['bytes'], validate=True)
        return canonical(data)

    def write_file(self, kind, path, data, context=None, wrapper=False):
        self.check_file(path)
        with self.reading():
            durable_write(path, self._file_bytes(kind, data))

    def remove_files(self, paths):
        paths = [Path(path).resolve() for path in paths]
        for path in paths:
            self.check_file(path)
        with self.reading():
            for path in paths:
                path.unlink(missing_ok=True)

    def _sources(self):
        """只在每个进程首次使用时检查旧密文；不依赖旧凭据判断普通库可用性。"""
        result = []
        for path in self.coordinator.paths():
            if not path.exists():
                continue
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as conn:
                if _database_needs_migration(conn):
                    result.append(path)
        for path in self.coordinator.files():
            with path.open('rb') as stream:
                prefix = stream.read(4096)
            if (BLOB_PREFIX.encode() in prefix or LEGACY_PREFIX.encode() in prefix or
                    WRAPPER_KEY.encode() in prefix or LEGACY_WRAPPER_KEY.encode() in prefix):
                result.append(path)
        return sorted(set(result))

    def _migrate(self, paths):
        reader = _MigrationReader(self.root, provider=self.provider, background_migration=False, deep_check=False)
        # 共用同一可重入协调锁；仅迁移期间取得旧凭据服务的锁。
        reader.coordinator = self.coordinator
        # 早期 CL1 AES 不使用统计安全服务；普通库和这类旧库不额外依赖 OS 凭据。
        credential_lock = reader._provider().lock(reader.slot) if reader.keyring_present() else nullcontext()
        with credential_lock:
            if reader.keyring_present():
                reader._load()
                reader.verify_sources()
            reader._in_transaction = True
            backup = self.directory / ('plaintext-backup-' + datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8])
            manifest = {'version': 1, 'backup': backup.name, 'files': []}
            backup.mkdir()
            for path in paths:
                relative = path.relative_to(self.root).as_posix()
                original, plain = backup / 'original' / relative, backup / 'plain' / relative
                original.parent.mkdir(parents=True, exist_ok=True)
                plain.parent.mkdir(parents=True, exist_ok=True)
                if path in self.coordinator.paths():
                    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(original)) as target:
                        source.backup(target)
                else:
                    shutil.copy2(path, original)
                shutil.copy2(original, plain)
                if path in self.coordinator.paths():
                    counts = self._restore_database(plain, reader)
                else:
                    raw = original.read_bytes()
                    kind = 'archives' if self.coordinator.is_archive(path) or 'cl1_monthly' in path.name else (
                        'ships' if '.json' in path.name else 'loot')
                    if raw.startswith((BLOB_PREFIX.encode(), LEGACY_PREFIX.encode())):
                        blob = raw.decode('utf-8')
                    else:
                        wrapped = json.loads(raw)
                        if not isinstance(wrapped, dict) or not (wrapped.get(WRAPPER_KEY) or wrapped.get(LEGACY_WRAPPER_KEY)):
                            raise VaultLocked('统计文件封装无效')
                        blob = wrapped['payload']
                    data = reader.open_(kind, blob, reader.file_context(kind, path))
                    durable_write(plain, self._file_bytes(kind, data))
                    # 加密后的每日备份可能还内含密文列，也要还原业务字段。
                    with plain.open('rb') as stream:
                        sqlite_image = stream.read(16) == b'SQLite format 3\x00'
                    counts = {}
                    if sqlite_image:
                        with closing(sqlite3.connect(plain)) as archived:
                            migrate_archive = _database_needs_migration(archived, include_legacy_aes=False)
                        if migrate_archive:
                            counts = self._restore_database(plain, reader, keep_legacy_aes=True)
                manifest['files'].append({'path': relative, 'original': self._hash(original),
                                          'plain': self._hash(plain), 'tables': counts})
            # 所有副本已经解密并回读通过之后才触碰正在使用的数据。
            durable_write(backup / 'manifest.json', canonical(manifest))
            durable_write(self.transition_path, canonical(manifest))
            try:
                for entry in manifest['files']:
                    self._publish(backup, entry, 'plain')
                remaining = self._sources()
                if remaining:
                    raise VaultLocked('仍有未迁移的统计密文')
                durable_write(self.mode_path, canonical({'version': 1, 'mode': 'plaintext', 'backup': backup.name,
                                                        'completed': datetime.now().isoformat()}))
            except BaseException:
                self._rollback(manifest)
                raise
            self.transition_path.unlink(missing_ok=True)
            logger.info(f'[统计-迁移] 已取消统计加密，无损还原 {len(paths)} 个文件；原件保留于 {backup.name}')

    @staticmethod
    def _hash(path):
        hasher = hashlib.sha256()
        with Path(path).open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                hasher.update(block)
        return hasher.hexdigest()

    def _publish(self, backup, entry, version):
        path = (self.root / entry['path']).resolve()
        if not path.is_relative_to(self.root):
            raise VaultLocked('迁移路径越界')
        if path in self.coordinator.paths():
            self.check_database(path)
        else:
            self.check_file(path)
        source = (backup / version / entry['path']).resolve()
        if not source.is_relative_to(backup.resolve()) or self._hash(source) != entry[version]:
            raise VaultLocked('迁移副本缺失或内容发生变化')
        durable_write(path, source.read_bytes())
        if path in self.coordinator.paths():
            for suffix in ('-wal', '-shm', '-journal'):
                Path(str(path) + suffix).unlink(missing_ok=True)

    def _rollback(self, manifest):
        backup = (self.directory / manifest['backup']).resolve()
        if backup.parent != self.directory.resolve() or not backup.name.startswith('plaintext-backup-'):
            raise VaultLocked('迁移备份位置无效')
        for entry in manifest['files']:
            self._publish(backup, entry, 'original')
        self.transition_path.unlink(missing_ok=True)

    @staticmethod
    def _restore_database(path, reader, keep_legacy_aes=False):
        """保留业务表结构及所有记录，逐行解密、回读；移除旧加密校验元数据。"""
        counts = {}
        with closing(sqlite3.connect(path)) as conn, conn:
            if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise sqlite3.DatabaseError('原统计数据库损坏')
            tables = _tables(conn)
            triggers = conn.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'").fetchall()
            # 格式转换不能触发用户业务触发器；转换后恢复其原定义。
            for name, _ in triggers:
                conn.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
            for table in (CHAIN_TABLE, COUNT_TABLE):
                if table in tables:
                    conn.execute('DROP TABLE ' + table)
            for table, column, kind, fields in DATASETS:
                if table not in tables:
                    continue
                columns = _columns(conn, table)
                order = 'instance,month' if kind == 'cl1' else 'id'
                expected = hashlib.sha256()
                count = 0
                for values in conn.execute('SELECT * FROM ' + table + ' ORDER BY ' + order):
                    row = dict(zip(columns, values))
                    if kind == 'cl1':
                        full = json.loads(row['data_json']) if row.get('data_json') else None
                        # 历史归档中的早期 AES 原件不属于上游 V2；保留其不透明字节，
                        # 不能为解除外层加密而删掉旧行或用默认值重建。
                        if full is None and row.get('encrypted_blob') and not row.get(column) and keep_legacy_aes:
                            expected.update(_row_bytes(row) + b'\n')
                            count += 1
                            continue
                        if full is None and row.get('encrypted_blob'):
                            from module.statistics.cl1_legacy import derive_legacy_key, decrypt_legacy_payload
                            from module.base.device_id import get_device_id, get_old_device_id
                            for device in (get_device_id(), get_old_device_id()):
                                if device:
                                    try:
                                        full = decrypt_legacy_payload(row['encrypted_blob'], derive_legacy_key(device))
                                        break
                                    except (ValueError, TypeError, UnicodeError):
                                        continue
                        if not isinstance(full, dict):
                            raise VaultLocked('旧 CL1 快照无法读取，保留原件')
                        if row.get(column):
                            payload = reader.open_(kind, row[column], row_context(kind, row))
                            if not isinstance(payload, dict):
                                raise VaultLocked('旧 CL1 字段结构无效，保留原件')
                            full.update(payload)
                        row['data_json'] = canonical(full).decode('utf-8')
                        if 'encrypted_blob' in columns:
                            row['encrypted_blob'] = None
                    elif row.get(column):
                        payload = reader.open_(kind, row[column], row_context(kind, row))
                        if not isinstance(payload, dict) or any(field not in payload for field in fields):
                            raise VaultLocked('旧统计字段不完整，保留原件')
                        if set(payload) - set(fields):
                            raise VaultLocked('旧统计存在未登记的加密扩展字段，保留原件等待确认映射')
                        row.update({field: payload[field] for field in fields})
                    if column in columns:
                        row[column] = None
                    assignments = ','.join('"' + name.replace('"', '""') + '"=?' for name in columns)
                    identity = (row['instance'], row['month']) if kind == 'cl1' else (row['id'],)
                    where = 'instance=? AND month=?' if kind == 'cl1' else 'id=?'
                    conn.execute(f'UPDATE {table} SET {assignments} WHERE {where}', [row[name] for name in columns] + list(identity))
                    expected.update(_row_bytes(row) + b'\n')
                    count += 1
                actual = hashlib.sha256()
                for values in conn.execute('SELECT * FROM ' + table + ' ORDER BY ' + order):
                    actual.update(_row_bytes(dict(zip(columns, values))) + b'\n')
                if expected.digest() != actual.digest():
                    raise VaultLocked('统计迁移回读不一致')
                counts[table] = {'rows': count, 'digest': actual.hexdigest()}
            if 'daily_summary_periods' in tables:
                for instance, period, text in conn.execute('SELECT instance,period_key,report_text FROM daily_summary_periods').fetchall():
                    if _encrypted_text(text):
                        data = reader.open_('reports', text, reader.report_context(instance, period))
                        if not isinstance(data, dict) or not isinstance(data.get('text'), str):
                            raise VaultLocked('旧日报正文无法读取')
                        conn.execute('UPDATE daily_summary_periods SET report_text=? WHERE instance=? AND period_key=?',
                                     (data['text'], instance, period))
                        restored = conn.execute('SELECT report_text FROM daily_summary_periods WHERE instance=? AND period_key=?',
                                                (instance, period)).fetchone()[0]
                        if restored != data['text']:
                            raise VaultLocked('日报迁移回读不一致')
                counts['daily_summary_periods'] = {'rows': conn.execute('SELECT COUNT(*) FROM daily_summary_periods').fetchone()[0]}
            for name, sql in triggers:
                if not name.startswith('__opsi_count_'):
                    conn.execute(sql)
            if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise sqlite3.DatabaseError('统计迁移后的数据库损坏')
        return counts
