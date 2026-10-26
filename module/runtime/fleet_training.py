"""普通舰队培养条件与持久记录；不操作设备，不估算等级或好感。"""

from copy import deepcopy
from datetime import datetime, timedelta

from module.config.time_source import now
from module.exception import FleetManagementError
from module.runtime.mind_calculator import find_ship
from module.runtime.fleet_rotation import ROTATION_PATH, active_fleets

LEVEL_PATH = 'FleetInfo.FleetLevelTraining'
AFFINITY_PATH = 'FleetInfo.FleetAffinityTraining'
TRAINING_PATH = 'FleetInfo.FleetTraining.State'


def goals(config):
    return {metric: config.cross_get(path + '.Target', default) for metric, path, default in
            (('level', LEVEL_PATH, 120), ('affinity', AFFINITY_PATH, 100))
            if config.cross_get(path + '.Enabled', False) is True}


def interval(config):
    return min((config.cross_get(path + '.CheckInterval', 30) for path in (LEVEL_PATH, AFFINITY_PATH)
                if config.cross_get(path + '.Enabled', False) is True), default=30)


def due(config, context):
    if not goals(config):
        return False
    signature = goals(config)
    checked = context.get('training_checked_at')
    return (context.get('training_goals') != signature
            or context.get('training_rules') != config.cross_get(ROTATION_PATH + '.Rules', '{}')
            or context.get('training_fleets') != active_fleets(config) or not isinstance(checked, datetime)
            or now() >= checked + timedelta(minutes=interval(config)))


def state(config):
    saved = deepcopy(config.cross_get(TRAINING_PATH, {}))
    if not isinstance(saved, dict) or any(not isinstance(saved.get(key, {}), dict) for key in ('records', 'pending')):
        raise FleetManagementError('培养记录格式无效，保留原记录，请检查配置')
    saved.setdefault('records', {})
    saved.setdefault('pending', {})
    return saved


def ships(result, fleet):
    return {item['slot']: item for category in ('main', 'vanguard')
            for item in result.get(category, {}).get(str(fleet), [])
            if type(item.get('slot')) is int and item.get('occupied', True)}


def identity(ship, result):
    if not ship or ship.get('review') or not ship.get('name'):
        return None
    info = find_ship(ship['name'])
    if info is None:
        return None
    if ship.get('raw_name'):
        raw = find_ship(ship['raw_name'])
        if raw is None or raw['name'] != info['name']:
            return None
    name = info['name']
    matches = sum(item.get('name') == ship['name'] for category in ('main', 'vanguard')
                  for fleet in result.get(category, {}).values() for item in fleet if item.get('occupied', True))
    return name if matches == 1 else None


def completed(record, targets):
    """必须同一舰船的所有已启用条件都有可靠读数；未知不视为完成。"""
    return bool(targets) and all(type(record.get(metric)) is int and record[metric] >= target
                                 for metric, target in targets.items())


def excluded(record, targets):
    """完成是持久排除；修改目标不能把已完成舰船自动选回来。"""
    done = record.get('completed_for', {})
    return bool(targets) and isinstance(done, dict) and bool(done)


def observe(saved, name, reading, targets):
    record = dict(saved['records'].get(name, {}), name=name, checked_at=now().isoformat(timespec='seconds'))
    # 同次检查重新写入全部已启用条件，不能把旧高好感与新等级拼成完成。
    record.update({metric: reading.get(metric) for metric in targets})
    if completed(record, targets) and not record.get('completed_for'):
        record['completed_for'] = dict(targets)
        record['completed_at'] = now().isoformat(timespec='seconds')
    saved['records'][name] = record
    return completed(record, targets)
