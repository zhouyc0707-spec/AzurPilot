"""移除旧自动规划配置，仅迁移独立规划工具的用户参数。"""

from copy import deepcopy
from datetime import datetime
import re

from yaml import YAMLError, safe_dump, safe_load

from module.config.deep import deep_get


LEGACY_WINTER_ITEMS = {
    1023: 4048, 1024: 4050, 2023: 4047, 2024: 4049,
    # 冬季花束的唯一原料配方确认旧冬季花卉对应官方水仙花。
    4043: 4045, 4044: 4046, 4045: 4043, 4046: 4044,
    4047: 4055, 4048: 4056, 4049: 4051, 4050: 4052, 4051: 4053, 4052: 4054,
}
LEGACY_WINTER_NAMES = {'Winter Flower': 'Daffodils', '冬季花卉': '水仙花'}


def _migrate_legacy_targets(value):
    """只转换旧工具目标中已确认变号的物品键，非法输入留给工具原校验。"""
    if not isinstance(value, str) or not value.strip():
        return value
    try:
        targets = safe_load(value)
    except YAMLError:
        return value
    if not isinstance(targets, dict):
        return value
    converted = {}
    changed = False
    for key, count in targets.items():
        item_id = key if type(key) is int else None
        if isinstance(key, str):
            text = key.strip()
            if text.isdigit():
                item_id = int(text)
            else:
                match = re.fullmatch(r'.*\((\d+)\)', text)
                if match:
                    item_id = int(match[1])
        migrated = LEGACY_WINTER_ITEMS.get(
            item_id, LEGACY_WINTER_NAMES.get(key.strip(), key) if isinstance(key, str) else key)
        converted[migrated] = count
        changed = changed or migrated != key
    return safe_dump(converted, allow_unicode=True, sort_keys=False) if changed else value


def migrate_island_planner(old, new):
    """保留独立工具设置，并撤销旧规划器临时拥有的工坊启用状态。"""
    legacy_tool = deep_get(old, 'IslandProductionPlanner.IslandPlannerTool', {})
    current_tool = deep_get(old, 'IslandProductionPlanner.IslandProductionPlanner', {})
    target = deep_get(new, 'IslandProductionPlanner.IslandProductionPlanner', {})
    if isinstance(legacy_tool, dict) and isinstance(target, dict):
        current_tool = current_tool if isinstance(current_tool, dict) else {}
        for field, value in legacy_tool.items():
            if field in target and field not in current_tool:
                target[field] = (_migrate_legacy_targets(value)
                                 if field in ('ExtraTargets', 'SeasonTaskTargets', 'CargoTargets')
                                 else deepcopy(value))

    legacy_auto = deep_get(old, 'IslandPlan.IslandProductionPlanner', {})
    scheduler = deep_get(new, 'IslandManufacture.Scheduler')
    if (isinstance(legacy_auto, dict) and legacy_auto.get('AutoManufactureActive') is True
            and isinstance(scheduler, dict)):
        # 该标记只由原本关闭的工坊被规划器临时开启时写入，不影响手工启用的工坊。
        scheduler['Enable'] = False
        original = legacy_auto.get('AutoManufactureNextRun')
        if isinstance(original, (str, datetime)) and original:
            try:
                restored = original if isinstance(original, datetime) else datetime.fromisoformat(original)
            except ValueError:
                pass
            else:
                scheduler['NextRun'] = (restored if isinstance(scheduler.get('NextRun'), datetime)
                                        else restored.isoformat(sep=' '))

    for task, group in (('IslandPlan', 'IslandProductionPlanner'),
                        ('IslandProductionPlanner', 'IslandPlannerTool')):
        groups = new.get(task)
        if isinstance(groups, dict):
            groups.pop(group, None)
    for index in range(1, 6):
        shop = deep_get(new, f'IslandBusiness.IslandBusinessShop{index}')
        if isinstance(shop, dict):
            shop.pop('PlannedMenu', None)
    return new
