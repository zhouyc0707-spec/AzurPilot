"""META 统一任务的旧配置迁移，保留原配置供核对与回退。"""

from copy import deepcopy
from datetime import datetime

from module.config.deep import deep_get, deep_set


def migrate_meta_assist(old, new):
    """只迁移尚未配置的新支援组；无法无损合并时不发布新配置。"""
    legacy = old.get('OpsiAshAssist')
    archived = old.get('_legacyMetaAssist')
    if isinstance(archived, dict):
        new['_legacyMetaAssist'] = deepcopy(archived)
    if not isinstance(legacy, dict):
        return new
    new.setdefault('_legacyMetaAssist', deepcopy(legacy))
    new.pop('OpsiAshAssist', None)
    current = deep_get(old, 'OpsiAshBeacon.OpsiAshAssist', {})
    if not isinstance(current, dict):
        current = {}
    has_enable = isinstance(current.get('Enable'), bool)
    has_tier = type(current.get('Tier')) is int
    if has_enable and has_tier:
        return new

    assist_enabled = deep_get(legacy, 'Scheduler.Enable')
    beacon_enabled = deep_get(old, 'OpsiAshBeacon.Scheduler.Enable')
    if not has_enable and assist_enabled is True and beacon_enabled is False:
        raise ValueError('META 支援原为单独启用，合并后会同时运行信标；请先确认 META 作战启用范围，原配置未改写')
    assist_equipment = legacy.get('Equipment', {})
    beacon_equipment = deep_get(old, 'OpsiAshBeacon.Equipment', {})
    if (not has_enable and assist_enabled is True and assist_equipment != beacon_equipment
            and (assist_equipment.get('Enable') or beacon_equipment.get('Enable'))):
        raise ValueError('META 作战与支援的装备方案不同，统一任务无法同时沿用；请先统一方案，原配置未改写')

    if not has_enable and isinstance(assist_enabled, bool):
        deep_set(new, 'OpsiAshBeacon.OpsiAshAssist.Enable', assist_enabled)
    tier = deep_get(legacy, 'OpsiAshAssist.Tier')
    if not has_tier and type(tier) is int:
        deep_set(new, 'OpsiAshBeacon.OpsiAshAssist.Tier', tier)
    # 任务合并后保留两项已启用任务中较早的既定检查时间，不提前到当前时刻。
    if not has_enable and assist_enabled is True and beacon_enabled is True:
        times = [deep_get(old, f'{task}.Scheduler.NextRun') for task in ('OpsiAshBeacon', 'OpsiAshAssist')]
        if all(isinstance(value, (str, datetime)) and value for value in times):
            try:
                earliest = min(value if isinstance(value, datetime) else datetime.fromisoformat(value)
                               for value in times)
            except (ValueError, TypeError):
                pass
            else:
                previous = deep_get(new, 'OpsiAshBeacon.Scheduler.NextRun')
                deep_set(new, 'OpsiAshBeacon.Scheduler.NextRun',
                         earliest if isinstance(previous, datetime) else earliest.isoformat(sep=' '))
    return new
