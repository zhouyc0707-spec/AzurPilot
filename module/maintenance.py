"""按区服自然日共享维护计划，供界面和独立任务读取。"""
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from deploy.api_endpoints import MAINTENANCE_API_URL
from deploy.atomic import atomic_write
from module.base import http
from module.base.device_id import get_device_id
from module.config.server import to_server
from module.config.time_source import now as current_time
from module.config.transaction import config_transaction
from module.config.utils import SERVER_TO_TIMEZONE
from module.logger import logger


DEFAULT_DIRECTORY = Path(__file__).resolve().parents[1] / 'config'


def server_for_config(data, fallback='cn'):
    """明确包名沿用既有映射，自动包名可使用运行中识别出的区服。"""
    alas = data.get('Alas', {})
    emulator = alas.get('Emulator', {}) if isinstance(alas, dict) else {}
    package = emulator.get('PackageName', 'auto') if isinstance(emulator, dict) else 'auto'
    if package == 'auto':
        return to_server(fallback)
    return to_server(package if isinstance(package, str) else 'auto')


def _directory(directory):
    return Path(directory or DEFAULT_DIRECTORY).resolve()


def _path(server, directory):
    if server not in SERVER_TO_TIMEZONE:
        raise ValueError(f'不支持的维护计划区服：{server}')
    return directory / 'maintenance' / f'{server}.json'


def _clock(server):
    return current_time(timezone.utc).astimezone(timezone(SERVER_TO_TIMEZONE[server]))


def _read(path, server):
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict) or data.get('server') != server:
        raise ValueError('维护计划缓存的区服不匹配')
    return data


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(str(path), json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def _start(value, server, now):
    if not value:
        return None
    at = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if at.tzinfo is None:
        raise ValueError('维护时间缺少时区')
    at = at.astimezone(timezone(SERVER_TO_TIMEZONE[server]))
    return at if at.date() >= now.date() else None


def _snapshot(data, server, now):
    at = _start(data.get('startsAt'), server, now)
    state = data.get('state', 'pending')
    if data.get('attemptDay') != now.date().isoformat():
        state = 'pending'
    return {'state': state, 'startsAt': at.isoformat() if at else None,
            'checkedAt': data.get('checkedAt'), 'error': data.get('error')}


def _query(server, now):
    response = http.get(MAINTENANCE_API_URL, params={'server': server},
                        headers={'X-AzurPilot-ID': get_device_id()}, timeout=10)
    response.raise_for_status()
    payload = response.json()
    if (payload.get('schema_version') != 2 or payload.get('server') != server
            or 'maintenance' not in payload):
        raise ValueError('维护接口返回了无效或其他区服的数据')
    plan = payload['maintenance']
    if plan is None:
        return None
    at = _start(plan['starts_at'], server, now)
    return at.isoformat() if at else None


def _resume_reconciliation(path, server, directory):
    """在缓存锁外重排，成功后只确认同一条持久记录。"""
    from module.maintenance_schedule import reconcile_plan

    while True:
        data = _read(path, server)
        pending = data.get('reconciliation')
        if not pending:
            return True
        if not reconcile_plan(directory, server, pending['baselines'], run_at=pending.get('wakeAt')):
            return False
        with config_transaction(path):
            latest = _read(path, server)
            if latest.get('reconciliation', {}).get('id') == pending['id']:
                latest['appliedStart'] = pending['snapshot']['startsAt']
                latest['hasApplied'] = True
                latest['lastReconciliation'] = {'baselines': pending['baselines'], 'wakeAt': pending.get('wakeAt')}
                del latest['reconciliation']
                _write(path, latest)
        # 并发发布了新记录时继续应用新记录，避免带着迟到重排进入单次任务基线。


def _refresh(server, directory, path):
    from module.maintenance_schedule import capture_baselines

    # 实例事务锁与缓存锁不嵌套，任务持有自己的配置锁时也可读取维护计划。
    _resume_reconciliation(path, server, directory)
    existing = _read(path, server)
    now = _clock(server)
    today = now.date().isoformat()
    needs_request = existing.get('attemptDay') != today
    needs_recovery = existing.get('state') == 'pending' and not needs_request
    if not needs_request and not needs_recovery:
        _resume_reconciliation(path, server, directory)
        return _read(path, server)
    baselines = capture_baselines(directory, server)
    with config_transaction(path):
        data = _read(path, server)
        now = _clock(server)
        today = now.date().isoformat()
        if data.get('attemptDay') != today or data.get('state') == 'pending':
            if data.get('attemptDay') != today:
                # 请求前登记尝试。进程在请求中退出，也不会让新进程当天再次请求。
                data.update(server=server, attemptDay=today, state='pending',
                            checkedAt=now.isoformat(), error=None, baselines=baselines)
                _write(path, data)
                try:
                    data['startsAt'] = _query(server, now)
                    data['state'] = 'announced' if data['startsAt'] else 'not_published'
                    data['error'] = None
                except Exception as error:
                    data['state'] = 'error'
                    data['error'] = f'{type(error).__name__}: {error}'
                    logger.warning('[维护计划] %s 查询失败，当天沿用有效缓存：%s', server, type(error).__name__)
            else:
                # 已获得锁而记录仍在查询中，说明上个进程未完成发布。
                data['state'] = 'error'
                data['error'] = '上次维护查询未完成，等待下一自然日重新查询'
            snapshot = _snapshot(data, server, now)
            data['startsAt'] = snapshot['startsAt']
            pending = data.get('reconciliation')
            if pending or not data.get('hasApplied') or data.get('appliedStart') != snapshot['startsAt']:
                pending_baselines = data.get('baselines', baselines)
                wake_at = now.astimezone().replace(tzinfo=None, microsecond=0).isoformat(sep=' ')
                if pending:
                    # 同公告延续旧CAS基线，已完成本轮的实例不会被重复唤醒。
                    # 公告变化才采用新基线，未读到的旧失败实例仍可继续恢复。
                    if pending['snapshot']['startsAt'] == snapshot['startsAt']:
                        pending_baselines = {**pending_baselines, **pending['baselines']}
                        wake_at = pending.get('wakeAt') or wake_at
                    else:
                        pending_baselines = {**pending['baselines'], **pending_baselines}
                data['reconciliation'] = {'id': uuid4().hex, 'snapshot': snapshot,
                                          'baselines': pending_baselines, 'wakeAt': wake_at}
            data.pop('baselines', None)
            _write(path, data)
    _resume_reconciliation(path, server, directory)
    return _read(path, server)


def get_maintenance_snapshot(server, directory=None, refresh=False):
    """读取计划快照，refresh开启时至多进行当天首次HTTP尝试。

    不刷新时仅读缓存，不创建目录、不联网、不执行任务重排。
    查询失败仍返回同区服当日或未来的有效缓存时间。
    """
    directory = _directory(directory)
    path = _path(server, directory)
    try:
        data = _refresh(server, directory, path) if refresh else _read(path, server)
        return _snapshot(data, server, _clock(server))
    except Exception as error:
        logger.warning('[维护计划] %s 缓存或排程更新失败：%s', server, type(error).__name__)
        try:
            snapshot = _snapshot(_read(path, server), server, _clock(server))
        except Exception:
            snapshot = {'state': 'error', 'startsAt': None, 'checkedAt': None, 'error': None}
        return dict(snapshot, state='error', error=f'{type(error).__name__}: {error}')


def get_maintenance_start(server, directory=None, refresh=True):
    """获取带区服时区的维护开始时间，未公告或无有效缓存时返回None。"""
    snapshot = get_maintenance_snapshot(server, directory, refresh)
    return datetime.fromisoformat(snapshot['startsAt']) if snapshot['startsAt'] else None


def settle_maintenance_instance(server, instance, directory=None):
    """只完成当前实例的待重排，在单次任务建立基线前调用。

    可在当前实例事务内调用；不联网、不持缓存锁，也不确认其他实例的重排。
    """
    from module.maintenance_schedule import reconcile_plan

    directory = _directory(directory)
    path = _path(server, directory)
    try:
        pending = _read(path, server).get('reconciliation')
        name = f'{instance}.json'
        if not pending or name not in pending['baselines']:
            return True
        return reconcile_plan(directory, server, {name: pending['baselines'][name]}, run_at=pending.get('wakeAt'))
    except (OSError, ValueError, KeyError, TypeError) as error:
        logger.warning('[维护计划] 当前实例重排未完成：%s', type(error).__name__)
        return False


def was_maintenance_rescheduled(server, instance, task, before, after, directory=None):
    """用固定唤醒值和原始基线证明排程变更，供单次任务保留人工改期。"""
    try:
        data = _read(_path(server, _directory(directory)), server)
        before = datetime.fromisoformat(str(before)).astimezone()
        after = datetime.fromisoformat(str(after)).astimezone()
        if before == after:
            return False
        for receipt in (data.get('reconciliation'), data.get('lastReconciliation')):
            if not receipt or not receipt.get('wakeAt'):
                continue
            expected = receipt['baselines'].get(f'{instance}.json', {}).get(task)
            if expected is None:
                continue
            original = datetime.fromisoformat(str(expected)).astimezone()
            wake_at = datetime.fromisoformat(receipt['wakeAt']).astimezone()
            if original == before and wake_at == after:
                return True
        return False
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def _configured_servers(directory):
    servers = set()
    for path in directory.glob('*.json'):
        if path.name == 'template.json':
            continue
        try:
            data = json.loads(path.read_text(encoding='utf-8-sig'))
            if (isinstance(data, dict) and isinstance(data.get('Alas'), dict)
                    and isinstance(data['Alas'].get('Emulator'), dict)):
                servers.add(server_for_config(data))
        except (OSError, ValueError, TypeError) as error:
            logger.warning('[维护计划] 跳过无法读取的实例 %s：%s', path.stem, type(error).__name__)
    return servers


class MaintenanceMonitor:
    """与浏览器会话独立的每日检查线程，停止信号可打断等待。"""

    def __init__(self, provider, directory, interval):
        self.directory = _directory(directory)
        self.provider = provider or (lambda: _configured_servers(self.directory))
        self.interval = interval
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name='maintenance-plan')

    def _run(self):
        while not self.stopped.is_set():
            try:
                for server in set(self.provider()):
                    if self.stopped.is_set():
                        break
                    get_maintenance_snapshot(server, self.directory, refresh=True)
            except Exception as error:
                logger.warning('[维护计划] 每日检查失败：%s', type(error).__name__)
            self.stopped.wait(self.interval)

    def stop(self):
        self.stopped.set()
        if self.thread is not threading.current_thread():
            self.thread.join(timeout=12)
        return not self.thread.is_alive()


def start_maintenance_monitor(provider=None, directory=None, interval=60):
    """启动即查，之后周期核对跨日状态；默认从当前项目实例配置发现区服。"""
    monitor = MaintenanceMonitor(provider, directory, interval)
    monitor.thread.start()
    return monitor
