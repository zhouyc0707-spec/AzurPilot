"""季节目标的最近生产快照，仅供展示，不参与排产和库存判断。"""

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from deploy.atomic import atomic_write
from module.config.time_source import now as current_time
from module.island.data import DIC_ISLAND_RECIPE
from module.island.item_ids import LOCAL_TO_ITEM_ID
from module.logger import logger


ROOT = Path(__file__).resolve().parents[2]
POST_GROUPS = {
    'FARM': ('farm', 4), 'ORCHARD': ('orchard', 4), 'NURSERY': ('nursery', 2),
    'MINE': ('mine', 4), 'FOREST': ('forest', 4), 'FISHERY': ('fishery', 3),
    'RESTAURANT': ('restaurant', 2), 'TEAHOUSE': ('teahouse', 2),
    'JUU_EATERY': ('juu_eatery', 2), 'GRILL': ('grill', 2), 'JUU_COFFEE': ('juu_coffee', 2),
    'WOOD_PROCESSING': ('wood_processing', 2), 'INDUSTRIAL': ('industrial_production', 2),
    'ELECTRONIC_PROCESSING': ('electronic_processing', 2), 'HANDMADE': ('handmade', 2),
}


def _path(instance, root=None):
    if (not isinstance(instance, str) or not instance or instance in ('.', '..')
            or any(char in instance for char in '\\/:*?"\'<>|')
            or instance.rstrip(' .') != instance):
        raise ValueError('库存快照实例名无效')
    return Path(root or ROOT) / 'cache' / 'island-progress' / f'{instance}.json'


def _season_id(season, server, now):
    """按官方服务器活动周期隔离快照，不把同名下一季当成这一季。"""
    from module.island_planner.data import DIC_ISLAND_SEASON

    offsets = {'cn': 8, 'en': -7, 'jp': 9, 'tw': 8}
    names = {2: 'spring', 3: 'summer', 4: 'autumn', 5: 'winter'}
    if server not in offsets:
        return None
    local = now.astimezone(timezone(timedelta(hours=offsets[server]))).replace(tzinfo=None)
    matches = []
    for key, value in DIC_ISLAND_SEASON.items():
        start = (value.get('start_time') or {}).get(server)
        end = (value.get('end_time') or {}).get(server)
        if (names.get(key) == season and start and end
                and datetime.fromisoformat(start) <= local <= datetime.fromisoformat(end)):
            matches.append(key)
    return matches[0] if len(matches) == 1 else None


def _read(instance, root=None):
    try:
        data = json.loads(_path(instance, root).read_text(encoding='utf-8'))
        if (data.get('version') != 1 or data.get('instance') != instance
                or data.get('status') not in ('observed', 'invalidated')
                or not isinstance(data.get('items'), dict)):
            return None
        datetime.fromisoformat(data['updated_at'])
        datetime.fromisoformat(data['observed_at'])
        for key, item in data['items'].items():
            if not key.isascii() or not key.isdecimal() or not isinstance(item, dict):
                return None
            for field in ('stock', 'in_production'):
                value = item[field]
                if value is not None and (type(value) is not int or value < 0):
                    return None
        return data
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def load_progress(instance, season, server='cn', *, now=None, root=None):
    """读取同实例同开发季的历史记录；无快照或损坏时返回 None，不填零。"""
    now = now or current_time()
    data = _read(instance, root)
    cycle = _season_id(season, server, now)
    if (data is None or cycle is None or data.get('season_id') != cycle
            or data.get('season') != season or data.get('server') != server):
        return None
    data['age_seconds'] = max(0, int(now.timestamp() - datetime.fromisoformat(data['updated_at']).timestamp()))
    return data


def _write(instance, data, root=None):
    path = _path(instance, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(str(path), json.dumps(data, ensure_ascii=False, allow_nan=False))


def invalidate_progress(instance, *, root=None, reason='库存可能已变化'):
    """标记历史记录失效；保留实际观测时间和值，不伪装成刚完成盘点。"""
    try:
        data = _read(instance, root)
        if data is None:
            return False
        data['status'] = 'invalidated'
        data['invalidated_at'] = current_time().isoformat(timespec='seconds')
        data['reason'] = reason
        _write(instance, data, root)
        return True
    except (OSError, ValueError, TypeError):
        logger.warning('[岛屿-库存进度] 历史快照未能标记失效，不影响原任务')
        return False


def _recipe_outputs(name):
    item = LOCAL_TO_ITEM_ID.get(name)
    matches = [recipe['commission_product'] for recipe in DIC_ISLAND_RECIPE.values()
               if item in recipe['commission_product']]
    return matches[0] if len(matches) == 1 else None


def inspected_production(runners):
    """汇总明确的旧岗位；未巡检、未知产品或无可靠次数使该区域总在制未知。"""
    groups, totals, posts = {}, Counter(), {}
    all_runners = list(runners.values())
    for runner in tuple(all_runners):
        fishery = getattr(runner, '_round_fishery', None)
        if fishery is not None:
            all_runners.append(fishery)
    for runner in all_runners:
        posts.update(getattr(runner, 'posts', {}))
    for prefix, (category, count) in POST_GROUPS.items():
        complete = True
        for index in range(1, count + 1):
            info = posts.get(f'ISLAND_{prefix}_POST{index}', {})
            state = info.get('state', info.get('status'))
            if state in ('idle', 'locked') or info.get('dispatched_in_run'):
                continue
            if state != 'working':
                complete = False
                continue
            runs = info.get('runs')
            outputs = _recipe_outputs(info.get('crop'))
            if type(runs) is not int or runs <= 0 or not outputs:
                complete = False
                continue
            for item, quantity in outputs.items():
                totals[item] += runs * quantity
        groups[category] = complete
    ranch = runners.get('IslandRancher')
    states = getattr(ranch, '_round_ranch_states', {})
    # 现有牧场巡检只记录状态与时长，没有旧单剩余次数，不能猜在制数量。
    groups['ranch'] = len(states) == 4 and all(state in ('idle', 'locked') for state in states.values())
    # 鱼肉是即时兑换，不存在岗位队列；必须本轮确实巡检过渔场。
    groups['fish_meat'] = groups['fishery']
    return dict(totals), groups


def publish_round_progress(config, session, *, root=None):
    """仅投影已确认的共享账；任何展示缓存故障均不影响原生产流程。"""
    instance = getattr(config, 'config_name', None)
    if not isinstance(instance, str) or not instance:
        return False
    try:
        from module.island.seasonal_task_targets import imported_item_catalog

        now = current_time()
        season = config.cross_get('IslandPlan.IslandPlan.Season', default='spring')
        server = config.SERVER
        cycle = _season_id(season, server, now)
        if cycle is None:
            return False
        current_totals, current_groups = inspected_production(session.runners)
        if not hasattr(session, '_progress_existing'):
            session._progress_existing = current_totals, current_groups
            session._progress_observed_at = now.isoformat(timespec='seconds')
        old_totals, groups = session._progress_existing
        totals, groups = Counter(old_totals), dict(groups)
        catalog = {LOCAL_TO_ITEM_ID[name]: category for name, category in imported_item_catalog().values()}
        # 食品/制造支持运行中复核岗位；新派遣有独立标记，只由 ledger 计入一次。
        raw_categories = {'farm', 'orchard', 'nursery', 'ranch', 'mine', 'forest', 'fishery', 'fish_meat'}
        for item, category in catalog.items():
            if category not in raw_categories:
                totals[item] = current_totals.get(item, 0)
                groups[category] = current_groups.get(category, False)
        ledger = session.inventory
        items = {}
        for item in catalog.keys() | ledger.stocks.keys() | ledger.in_production.keys():
            category = catalog.get(item)
            in_production = (totals.get(item, 0) + ledger.in_production.get(item, 0)
                             if groups.get(category, False) else None)
            items[str(item)] = {'stock': ledger.get(item), 'in_production': in_production}
        data = dict(version=1, instance=instance, season=season, server=server, season_id=cycle,
                    observed_at=session._progress_observed_at, updated_at=now.isoformat(timespec='seconds'),
                    status='observed', items=items)
        previous = _read(instance, root)
        if (previous and previous['status'] == 'observed' and previous['items'] == items
                and previous['observed_at'] == data['observed_at']):
            data['updated_at'] = previous['updated_at']
        _write(instance, data, root)
        return True
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        logger.warning('[岛屿-库存进度] 快照保存失败，不影响原生产流程')
        return False
