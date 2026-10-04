"""交易游戏文件的本机认证存储，复用账号保险库的密钥保护。"""
import base64
import hashlib
import hmac
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager, closing
from pathlib import Path

from Crypto.Cipher import AES

from deploy.atomic import atomic_write
from module.api.protocol import ApiError
from module.config.transaction import config_transaction
from module.runtime.account_local import LocalProtector
from module.runtime.account_vault import SecretKey

INSTANCE_FIELD = '_stockInstance'


def damaged(message='交易游戏文件校验失败，请恢复完整备份；已停止使用原文件'):
    return ApiError('STOCK_STORAGE_DAMAGED', message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


class GameDataProtector:
    """项目外保存认证密钥、实例登记与检查点，项目文件回滚不能重置检查点。"""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.directory = self.root / 'cache' / 'stock-exchange'
        self.protector = LocalProtector(self.root, 'stock-exchange-files-v1')
        self.state_path = self.protector.key_directory() / (self.protector.context + '.game')
        self.marker = self.directory / 'protected-v1'

    @staticmethod
    def _safe(path):
        for parent in (path, *path.parents):
            if parent.is_symlink() or parent.is_junction():
                raise damaged('交易游戏文件路径包含链接，已停止使用')

    def initialized(self):
        return self.marker.exists() or self.state_path.exists()

    @contextmanager
    def transaction(self):
        """登记与检查点跨进程串行更新；每次重新解封，缓存不能绕过密钥丢失。"""
        self._safe(self.state_path)
        self._safe(self.marker)
        with config_transaction(self.state_path):
            key = None
            try:
                if self.state_path.exists():
                    envelope = json.loads(self.state_path.read_bytes())
                    wrapped = base64.b64decode(envelope['wrapped'], validate=True)
                    key = SecretKey(self.protector.unwrap(wrapped))
                    data = self._decrypt(key, envelope, 'registry')
                    if data.get('version') != 1 or any(not isinstance(data.get(k), dict) for k in ('instances', 'files', 'anchors')):
                        raise ValueError()
                else:
                    if self.marker.exists():
                        raise damaged('本机游戏保护登记丢失，请恢复完整备份，不能自动重建身份')
                    key = SecretKey(os.urandom(32))
                    wrapped = self.protector.wrap(key.value)
                    data = {'version': 1, 'instances': {}, 'files': {}, 'anchors': {}}
                before = canonical(data)
                yield data, key
                if not self.state_path.exists() or canonical(data) != before:
                    envelope = self._encrypt(key, data, 'registry')
                    envelope['wrapped'] = base64.b64encode(wrapped).decode()
                    atomic_write(str(self.state_path), canonical(envelope))
                    self.state_path.chmod(0o600)
                self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
                if not self.marker.exists():
                    atomic_write(str(self.marker), 'AzurPilot game protection v1\n')
                    self.marker.chmod(0o600)
            except ApiError as error:
                if error.code.startswith('LOCAL_'):
                    raise damaged('本机游戏密钥不可用或主机/用户不匹配，请恢复原保护环境和完整备份') from None
                raise
            except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
                raise damaged() from None
            finally:
                if key is not None:
                    key.clear()

    def _encrypt(self, key, data, purpose):
        cipher = AES.new(key.value, AES.MODE_GCM, nonce=os.urandom(12))
        cipher.update(f'AzurPilot/game/v1/{self.protector.context}/{purpose}'.encode())
        payload, tag = cipher.encrypt_and_digest(canonical(data))
        return {'version': 1, 'nonce': base64.b64encode(cipher.nonce).decode(),
                'tag': base64.b64encode(tag).decode(), 'payload': base64.b64encode(payload).decode()}

    def _decrypt(self, key, envelope, purpose):
        if envelope['version'] != 1:
            raise ValueError()
        cipher = AES.new(key.value, AES.MODE_GCM, nonce=base64.b64decode(envelope['nonce'], validate=True))
        cipher.update(f'AzurPilot/game/v1/{self.protector.context}/{purpose}'.encode())
        return json.loads(cipher.decrypt_and_verify(base64.b64decode(envelope['payload'], validate=True),
                                                   base64.b64decode(envelope['tag'], validate=True)))

    def _configs(self):
        from module.api.config_service import accepts_name
        result = {}
        for path in (self.root / 'config').glob('*.json'):
            if not accepts_name(path.stem) or path.is_symlink() or path.is_junction():
                continue
            try:
                content = json.loads(path.read_bytes())
                if isinstance(content, dict) and isinstance(content.get('Alas'), dict):
                    result[path.stem] = content.get(INSTANCE_FIELD)
            except (OSError, ValueError):
                # 损坏的原配置保留登记，不能被视为删除后复用。
                result[path.stem] = None
        return result

    @staticmethod
    def _reconcile(data, configs):
        for identity, record in data['instances'].items():
            if not record['active']:
                continue
            if record.get('baseline'):
                # 旧统计按实例名存储；永久保留迁移来源，之后的重命名不能丢失旧月记录。
                record.setdefault('legacyStatsName', record['name'])
            matches = [name for name, value in configs.items() if value == identity]
            if record['name'] in matches:
                continue
            if len(matches) == 1 and record['name'] not in configs:
                record['name'] = matches[0]
            elif record['name'] not in configs and not matches:
                record['active'] = False

    def reconcile(self):
        if not self.initialized():
            return {}
        with self.transaction() as (data, _):
            self._reconcile(data, self._configs())
            return {identity: record['name'] for identity, record in data['instances'].items() if record['active']}

    def resolve(self, instance, fresh=False):
        from module.api.config_service import validate_name
        instance = validate_name(instance)
        path = self.root / 'config' / (instance + '.json')
        self._safe(path)
        with config_transaction(path):
            if not path.is_file():
                self.reconcile()
                raise ApiError('NOT_FOUND', '实例不存在')
            try:
                content = json.loads(path.read_bytes())
                if not isinstance(content, dict) or not isinstance(content.get('Alas'), dict):
                    raise ValueError()
            except (OSError, ValueError):
                raise ApiError('CONFIG_INVALID', '实例配置损坏，已停止游戏数据同步') from None
            with self.transaction() as (data, _):
                configs = self._configs()
                self._reconcile(data, configs)
                identity = content.get(INSTANCE_FIELD)
                record = data['instances'].get(identity) if isinstance(identity, str) else None
                if identity and (not record or not record['active']):
                    raise damaged('实例身份未登记或已删除，不能使用复制或回滚的玩家数据')
                if record and record['name'] == instance:
                    return identity
                if record and record['name'] not in configs and sum(value == identity for value in configs.values()) > 1:
                    raise damaged('存在多份相同实例身份，无法确定重命名来源，请保留原件')
                # 复制仍存在的实例只能取得新身份；仅原文件消失时识别为重命名。
                for old in data['instances'].values():
                    if old['active'] and old['name'] == instance:
                        old['active'] = False
                legacy_path = self.directory / 'identities' / (hashlib.sha256(instance.encode()).hexdigest() + '.json')
                legacy = INSTANCE_FIELD not in content and not fresh and legacy_path.exists()
                if legacy:
                    self._safe(legacy_path)
                    try:
                        previous = json.loads(legacy_path.read_bytes())
                        identity = str(uuid.UUID(previous['instanceId']))
                    except (OSError, ValueError, TypeError, KeyError):
                        raise damaged('旧实例身份损坏，请恢复身份备份') from None
                    if identity in data['instances']:
                        raise damaged('旧实例身份已登记，不能再次迁移')
                else:
                    identity = str(uuid.uuid4())
                predecessors = [key for key, row in data['instances'].items() if row['name'] == instance]
                baseline = not fresh and INSTANCE_FIELD not in content and not predecessors
                data['instances'][identity] = {'name': instance, 'active': True, 'schedulerName': instance, 'legacy': legacy,
                                               'predecessor': predecessors[-1] if predecessors else None,
                                               'baseline': baseline, 'legacyStatsName': instance if baseline else None}
                content[INSTANCE_FIELD] = identity
                atomic_write(str(path), json.dumps(content, ensure_ascii=False, indent=2))
                return identity

    def retire(self, instance):
        if self.initialized():
            with self.transaction() as (data, _):
                for record in data['instances'].values():
                    if record['name'] == instance:
                        record['active'] = False

    def record(self, identity):
        with self.transaction() as (data, _):
            row = data['instances'].get(identity)
            if not row or not row['active']:
                raise damaged('实例已经删除，已停止使用其玩家数据')
            return dict(row)

    def key(self, identity, purpose):
        with self.transaction() as (data, key):
            if not data['instances'].get(identity, {}).get('active'):
                raise damaged('实例已经删除，已停止使用其玩家数据')
            return SecretKey(hmac.digest(key.value, f'{identity}/{purpose}'.encode(), 'sha256'))

    def file_path(self, name):
        path = self.directory / name
        if not path.resolve().is_relative_to(self.directory.resolve()):
            raise damaged()
        self._safe(path)
        return path

    def read_file(self, name):
        with config_transaction(self.file_path(name)):
            return self._read_file(name)

    def _read_file(self, name):
        path = self.file_path(name)
        with self.transaction() as (data, key):
            expected = data['files'].get(name)
            if not path.exists():
                if expected:
                    raise damaged('受保护的交易文件丢失，请恢复完整备份')
                return None
            if not expected:
                raise damaged('交易文件未登记，不能自动接受替换文件')
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if digest not in (expected.get('head'), expected.get('pending')):
                raise damaged()
            result = self._decrypt(key, json.loads(raw), name)
            data['files'][name] = {'head': digest}
            return result

    def has_file(self, name):
        with self.transaction() as (data, _):
            return name in data['files']

    def write_file(self, name, value, legacy=False):
        with config_transaction(self.file_path(name)):
            self._write_file(name, value, legacy)

    def _write_file(self, name, value, legacy=False):
        path = self.file_path(name)
        if not legacy:
            self.read_file(name)
        # 先持久化候选检查点，再替换文件；崩溃只允许完整的旧版或候选新版。
        with self.transaction() as (data, key):
            raw = canonical(self._encrypt(key, value, name))
            digest = hashlib.sha256(raw).hexdigest()
            expected = data['files'].setdefault(name, {})
            expected['pending'] = digest
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        atomic_write(str(path), raw)
        path.chmod(0o600)
        with self.transaction() as (data, _):
            data['files'][name] = {'head': digest}

    def anchor(self, name, actual, prepare=False):
        with self.transaction() as (data, _):
            expected = data['anchors'].get(name)
            if prepare:
                data['anchors'].setdefault(name, {})['pending'] = actual
            elif expected is None:
                return False
            elif actual not in (expected.get('head'), expected.get('pending')):
                raise damaged('行动力历史哈希链或持久检查点不匹配，已停止同步')
            else:
                data['anchors'][name] = {'head': actual}
            return True

    def has_anchor(self, name):
        with self.transaction() as (data, _):
            return name in data['anchors']

    def relocate_scheduler(self, instance, identity):
        """手动重命名配置后迁移已提交的资源记录，冲突时保留两边原件。"""
        record = self.record(identity)
        old = record['schedulerName']
        directory = self.root / 'config' / 'scheduler'
        source, target = directory / (old + '.sqlite3'), directory / (instance + '.sqlite3')
        if old == instance:
            predecessor = record.get('predecessor')
            if predecessor and target.exists():
                with config_transaction(target):
                    with closing(sqlite3.connect(target.as_uri() + '?mode=ro', uri=True)) as db:
                        chained = db.execute("SELECT 1 FROM sqlite_master WHERE name='action_point_chain_owner'").fetchone()
                        owner = db.execute('SELECT instance_id FROM action_point_chain_owner WHERE id=1').fetchone() if chained else None
                        if owner and owner[0] == predecessor:
                            backup = self.root / 'config' / 'backup' / ('stock-' + predecessor) / 'scheduler' / target.name
                            backup.parent.mkdir(parents=True, exist_ok=True)
                            self._safe(backup)
                            with closing(sqlite3.connect(backup)) as outgoing:
                                db.backup(outgoing)
                    if owner and owner[0] == predecessor:
                        for suffix in ('', '-wal', '-shm'):
                            target.with_name(target.name + suffix).unlink(missing_ok=True)
                with self.transaction() as (data, _):
                    data['instances'][identity]['predecessor'] = None
            return
        self._safe(source)
        self._safe(target)
        with config_transaction(source), config_transaction(target):
            if source.exists():
                if target.exists():
                    raise damaged('实例重命名后资源数据库冲突，请保留原文件并恢复对应备份')
                temporary = target.with_name(target.name + '.' + uuid.uuid4().hex + '.relocating')
                self._safe(temporary)
                try:
                    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as incoming, closing(sqlite3.connect(temporary)) as outgoing:
                        incoming.backup(outgoing)
                    os.replace(temporary, target)
                    for suffix in ('', '-wal', '-shm'):
                        source.with_name(source.name + suffix).unlink(missing_ok=True)
                finally:
                    temporary.unlink(missing_ok=True)
        with self.transaction() as (data, _):
            data['instances'][identity]['schedulerName'] = instance
