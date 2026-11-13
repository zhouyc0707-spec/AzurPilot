"""维护公告变化后的排程唤醒及演习补次数周期计算。"""
import json
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from deploy.atomic import atomic_write
from module.config.server import to_server
from module.config.time_source import now as current_time
from module.config.transaction import config_transaction
from module.logger import logger

TASK_SWITCHES = {'Exercise': 'MaintenanceAdvance', 'Hard': 'RunAt18'}


def exercise_next_update(now, refresh_times, server_offset):
    """首次识别设备之前，按实例区服计算下一补次数节点。"""
    zone = timezone(server_offset)
    server_now = now.astimezone(zone)
    if isinstance(refresh_times, str):
        refresh_times = refresh_times.replace(' ', '').split(',')
    candidates = [datetime.combine(server_now.date() + timedelta(days=day), time.fromisoformat(value), zone)
                  for day in (0, 1) for value in refresh_times]
    update = min(value for value in candidates if value >= server_now)
    return update.astimezone(now.tzinfo) if now.tzinfo else update.astimezone().replace(tzinfo=None)


def exercise_start_time(next_update, maintenance_start, refresh_times, delay_hours, server_offset):
    """只在维护前一小时所在的补次数周期覆盖普通演习门禁。"""
    normal = next_update - timedelta(hours=delay_hours)
    if maintenance_start is None:
        return normal
    zone = timezone(server_offset)
    end = next_update.astimezone(zone)
    early = maintenance_start.astimezone(zone) - timedelta(hours=1)
    if isinstance(refresh_times, str):
        refresh_times = refresh_times.replace(' ', '').split(',')
    candidates = [datetime.combine(end.date() + timedelta(days=day), time.fromisoformat(value), zone)
                  for day in (-1, 0) for value in refresh_times]
    previous = max(value for value in candidates if value < end)
    if previous <= early < end:
        return early.astimezone(next_update.tzinfo) if next_update.tzinfo else early.astimezone().replace(tzinfo=None)
    return normal


def _profile_server(data):
    alas = data.get('Alas') if isinstance(data, dict) else None
    emulator = alas.get('Emulator') if isinstance(alas, dict) else None
    if not isinstance(emulator, dict):
        raise ValueError('实例缺少有效模拟器配置')
    package = emulator.get('PackageName', 'auto')
    return to_server(package if isinstance(package, str) else 'auto')


def capture_baselines(directory, server):
    """在请求公告前记录各实例的排程，供发布后的短事务比较。"""
    baselines = {}
    for path in sorted(Path(directory).glob('*.json')):
        if path.name == 'template.json':
            continue
        try:
            data = json.loads(path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError) as error:
            logger.warning('[维护排程] 跳过暂不可读的实例：%s', type(error).__name__)
            continue
        alas = data.get('Alas') if isinstance(data, dict) else None
        if not isinstance(alas, dict) or not isinstance(alas.get('Emulator'), dict):
            continue
        if _profile_server(data) != server:
            continue
        schedules = {}
        for task in TASK_SWITCHES:
            groups = data.get(task)
            if isinstance(groups, dict) and isinstance(groups.get('Scheduler'), dict):
                schedules[task] = groups['Scheduler'].get('NextRun')
        baselines[path.name] = schedules
    return baselines


def reconcile_plan(directory, server, baselines, run_at=None):
    """按请求前排程逐实例唤醒，重复重排和并发人工编辑均保留当前值。"""
    directory = Path(directory)
    completed = True
    now = current_time().replace(microsecond=0)
    wake_at = datetime.fromisoformat(run_at) if run_at is not None else now
    for name, expected in baselines.items():
        # 基线来自缓存文件，仍只允许当前配置目录中的直接JSON子文件。
        if Path(name).name != name or not name.endswith('.json'):
            logger.warning('[维护排程] 忽略无效实例文件名')
            continue
        path = directory / name
        try:
            with config_transaction(path):
                if not path.exists():
                    continue
                data = json.loads(path.read_text(encoding='utf-8-sig'))
                if _profile_server(data) != server:
                    continue
                changed = False
                for task, gate in TASK_SWITCHES.items():
                    groups = data.get(task, {})
                    if not isinstance(groups, dict):
                        continue
                    scheduler = groups.get('Scheduler', {})
                    settings = groups.get(task, {})
                    if not isinstance(scheduler, dict) or not isinstance(settings, dict):
                        continue
                    if scheduler.get('Enable') is not True or settings.get(gate, task == 'Exercise') is not True:
                        continue
                    value = scheduler.get('NextRun')
                    if task not in expected or value != expected[task]:
                        continue
                    try:
                        if datetime.fromisoformat(value).astimezone() <= now.astimezone():
                            continue
                    except (TypeError, ValueError):
                        pass
                    scheduler['NextRun'] = wake_at.isoformat(sep=' ')
                    changed = True
                if changed:
                    atomic_write(str(path), json.dumps(data, ensure_ascii=False, indent=2))
        except (OSError, ValueError, KeyError, TypeError) as error:
            completed = False
            logger.warning('[维护排程] 实例重排等待重试：%s', type(error).__name__)
    return completed
