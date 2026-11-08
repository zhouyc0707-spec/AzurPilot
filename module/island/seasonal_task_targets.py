"""季节任务目标的编号转换、严格校验与保留较大值的写回。"""

import json
import re
from functools import lru_cache

from module.island.data import DIC_ISLAND_ITEM as LOCAL_ITEMS
from module.island.item_ids import ITEM_ID_TO_LOCAL
from module.island_planner.data import (
    DIC_ISLAND_ITEM, DIC_ISLAND_RECIPE, DIC_ISLAND_SEASON, DIC_ISLAND_SLOT, DIC_ISLAND_TASK,
)

MAX_TARGET = 9007199254740991
TASK = 'IslandSeasonalProduction'
PLACE_CATEGORIES = {
    101: 'farm', 102: 'ranch', 401: 'mine', 402: 'forest', 501: 'orchard', 502: 'nursery',
    601: 'restaurant', 602: 'teahouse', 603: 'juu_eatery', 604: 'grill', 901: 'juu_coffee',
    703: 'wood_processing', 704: 'industrial_production', 705: 'electronic_processing', 706: 'handmade',
}


@lru_cache(maxsize=1)
def imported_item_catalog():
    """以完整中文名校对两套编号，再按官方配方定位生产区域。

    冬季物品的本地旧编号与规划器编号不同，不能直接以整数相等关联。
    名称仅用于两份静态数据的精确连接；游戏 OCR 不参与编号转换。
    """
    from module.island.seasonal_targets import SEASONAL_PRODUCTION_ITEMS

    seasonal_names = {name for groups in SEASONAL_PRODUCTION_ITEMS.values()
                      for names in groups.values() for name in names}
    candidates = {item for season in DIC_ISLAND_SEASON.values() for task in season['task_list']
                  for item in (DIC_ISLAND_TASK[task].get('target') or {}) if item in DIC_ISLAND_ITEM}
    local_names = {}
    for item_id, name in ITEM_ID_TO_LOCAL.items():
        if item_id in LOCAL_ITEMS:
            local_names.setdefault(LOCAL_ITEMS[item_id]['name']['cn'], []).append(name)
    categories = {}
    for slot in DIC_ISLAND_SLOT.values():
        category = PLACE_CATEGORIES.get(slot['place'])
        if category:
            for recipe_id in slot['formula'] + slot['activity_formula']:
                for item_id in DIC_ISLAND_RECIPE[recipe_id]['commission_product']:
                    categories.setdefault(item_id, set()).add(category)
    result = {}
    for item_id, item in DIC_ISLAND_ITEM.items():
        names = local_names.get(item['name']['cn'], [])
        if len(names) != 1:
            continue
        name = names[0]
        if item_id not in candidates and name not in seasonal_names:
            continue
        locations = categories.get(item_id, set())
        if item_id in (2521, 2522):
            locations = {'fish_meat'}
        if name in seasonal_names:
            locations = {category for groups in SEASONAL_PRODUCTION_ITEMS.values()
                         for category, names in groups.items() if name in names}
        if len(locations) == 1:
            result[item_id] = (name, next(iter(locations)))
    return result


def target_label(item_id):
    return f'{DIC_ISLAND_ITEM[item_id]["name"]["cn"]} ({item_id})'


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'季节任务物品重复：{key}')
        result[key] = value
    return result


def parse_imported_targets(text, season=None):
    """读取页面保存的 JSON；非法物品、重复编号和非整数数量均明确报错。"""
    if not isinstance(text, str):
        raise ValueError('季节任务目标必须是 JSON 文本')
    try:
        data = json.loads(text or '{}', object_pairs_hook=_unique_pairs)
    except (ValueError, RecursionError) as exc:
        raise ValueError(f'季节任务目标格式错误：{exc}') from exc
    if not isinstance(data, dict):
        raise ValueError('季节任务目标必须是物品到数量的映射')
    result = {}
    for key, value in data.items():
        match = re.fullmatch(r'.+ \(([0-9]+)\)', key)
        item_id = int(match[1]) if match else None
        if item_id not in imported_item_catalog() or key != target_label(item_id):
            raise ValueError(f'未知或名称与编号不符的季节任务物品：{key}')
        if type(value) is not int or not 0 <= value <= MAX_TARGET:
            raise ValueError(f'{key} 的目标必须为 0 至 {MAX_TARGET} 的整数')
        if item_id in result:
            raise ValueError(f'季节任务物品编号重复：{item_id}')
        if season is not None:
            _check_item_season(item_id, season)
        result[item_id] = value
    return result


def _check_item_season(item_id, season):
    from module.island.seasonal_targets import SEASONAL_PRODUCTION_ITEMS

    name = imported_item_catalog()[item_id][0]
    for product_season, groups in SEASONAL_PRODUCTION_ITEMS.items():
        if any(name in names for names in groups.values()) and season != product_season:
            raise ValueError(f'{target_label(item_id)} 不属于当前设置的季节')


def format_imported_targets(targets):
    return json.dumps({target_label(item): count for item, count in targets.items()}, ensure_ascii=False)


def imported_targets_for_category(config, season, category):
    """为生产端提供同季节的追加目标；损坏的配置不能变成零库存需求。"""
    path = f'{TASK}.IslandSeason{season.capitalize()}.ImportedTargets'
    targets = parse_imported_targets(config.cross_get(path, default='{}'), season=season)
    catalog = imported_item_catalog()
    return {catalog[item][0]: count for item, count in targets.items()
            if count > 0 and catalog[item][1] == category}


def build_target_updates(config, item_targets, expected_season):
    """仅更新本季目标；同物品采用 max，不累加已导入的任务数量。"""
    from module.island.seasonal_targets import SEASONAL_PRODUCTION_ITEMS

    if expected_season not in SEASONAL_PRODUCTION_ITEMS or config.cross_get(
            'IslandPlan.IslandPlan.Season', default='spring') != expected_season:
        raise ValueError('季节配置与本次扫描不一致，未写入目标，请核对全局季节设置')
    group = f'{TASK}.IslandSeason{expected_season.capitalize()}'
    imported_path = f'{group}.ImportedTargets'
    original = config.cross_get(imported_path, default='{}')
    imported = parse_imported_targets(original, season=expected_season)
    seasonal_names = {name for names in SEASONAL_PRODUCTION_ITEMS[expected_season].values() for name in names}
    changes = {}
    catalog = imported_item_catalog()
    for item_id, count in item_targets.items():
        if type(item_id) is not int or item_id not in catalog:
            raise ValueError(f'季节任务物品 {item_id} 尚无已确认的生产映射，未写入本次目标')
        if type(count) is not int or not 0 < count <= MAX_TARGET:
            raise ValueError(f'季节任务物品 {item_id} 的目标数量无效：{count}')
        _check_item_season(item_id, expected_season)
        name, _ = catalog[item_id]
        if name in seasonal_names:
            path = f'{group}.{name}'
            old = config.cross_get(path, default=0)
            if type(old) is not int or not 0 <= old <= MAX_TARGET:
                raise ValueError(f'{path} 的现有数量无效，未写入目标')
            target = max(old, count, imported.pop(item_id, 0))
            if target != old:
                changes[path] = target
        else:
            imported[item_id] = max(imported.get(item_id, 0), count)
    if imported != parse_imported_targets(original):
        changes[imported_path] = format_imported_targets(imported)
    return changes


def merge_imported_targets(config, item_targets, expected_season):
    """在同一配置事务内重读并合并，保留扫描期间用户刚调高的目标。"""
    from module.config.transaction import config_transaction
    from module.config.utils import filepath_config

    with config_transaction(filepath_config(config.config_name)):
        config.load(apply_override=False)
        changes = build_target_updates(config, item_targets, expected_season)
        if changes:
            config.cross_set_many(changes)
        return changes
