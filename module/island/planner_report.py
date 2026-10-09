"""仅用于展示生产方案与最近可信观测，不作为生产决策的库存缓存。"""

import copy
import json
import math
from functools import wraps
from numbers import Real

from module.config.time_source import now
from module.island.data import DIC_ISLAND_ITEM, DIC_ISLAND_PRODUCTION_PLACE, DIC_ISLAND_RECIPE
from module.island.item_ids import resolve_item_id
from module.island.planner_utils import load_item_mapping, normalize_item_keys
from module.logger import logger


PREFIX = 'IslandPlan.IslandProductionPlanner'


def _get(config, path, default=None):
    if isinstance(config, dict):
        value = config
        for part in path.split('.'):
            if not isinstance(value, dict) or part not in value:
                return default
            value = value[part]
        return value
    getter = getattr(config, 'cross_get', None)
    return getter(path, default=default) if callable(getter) else default


def _name(item):
    return DIC_ISLAND_ITEM.get(item, {}).get('name', {}).get('cn', str(item))


def _mapping(raw):
    return normalize_item_keys(load_item_mapping(raw or '{}'))


def _amount(value):
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _rows(targets, floors, buffers, reserves, demands, idle):
    items = set(targets) | set(idle)
    return [{
        'id': item, 'name': _name(item), 'target': targets.get(item, 0),
        'floor': floors.get(item, 0), 'buffer': buffers.get(item, 0),
        'reserve': reserves.get(item, 0), 'demand': demands.get(item, 0),
        'idle_per_day': idle.get(item, 0),
    } for item in sorted(items)]


def _menus(menus, capacities):
    return [{
        'id': place, 'name': DIC_ISLAND_PRODUCTION_PLACE[place]['name']['cn'],
        'capacity': capacities.get(place),
        'items': [{'id': item, 'name': _name(item), 'per_day': amount}
                  for item, amount in sorted(menu.items())],
    } for place, menu in sorted(menus.items())]


def build_planner_report(calculator, targets, menus, reserves, generated_at, manufacture_needs=None):
    """导出已验证方案的展示副本；新方案不沿用上一个方案的满足状态。"""
    targets = {int(item): value for item, value in targets.items()}
    floors = calculator.hard_floor_items
    buffers = dict(calculator.product_daily_buffer_items)
    reserves = dict(reserves)
    demands = {item: need.get('total_need_count', 0) for item, need in calculator.demand_items.items()}
    for item, amount in (manufacture_needs or {}).items():
        # 临时工坊只兑现订单依赖，实际目标已去掉日周转与无关制造安排。
        buffers[item] = reserves[item] = 0
        demands[item] = amount
    production = [{
        'recipe_id': recipe, 'name': DIC_ISLAND_RECIPE[recipe]['name']['cn'],
        'place': DIC_ISLAND_PRODUCTION_PLACE[calculator.GROUP_TO_PLACE[calculator.recipe_group[recipe]]]['name']['cn'],
        'batches_per_day': round(float(amount), 6),
    } for recipe, amount in sorted(calculator.production_plan.items())
        if amount > calculator.NET_ACCUMULATING_EPSILON]
    return {
        'version': 1, 'generated_at': generated_at.isoformat(sep=' ', timespec='seconds'),
        'legacy': False, 'daily_revenue': float(calculator.daily_coin_revenue),
        'daily_profit': float(calculator.daily_profit),
        'items': _rows(targets, floors, buffers, reserves, demands, calculator.idle_accumulating_items_per_day),
        'production': production, 'menus': _menus(menus, calculator.restaurant_capacity),
        'observations': {}, 'dispatches': {},
    }


def _legacy_report(config):
    """旧方案也可立即查看目标及菜单，不求解、不扫描、不写配置。"""
    from module.island.order_stock import RESTAURANT_TO_SHOP, get_restaurant_capacity

    class Reader:
        def cross_get(self, path, default=None):
            return _get(config, path, default)

    targets = _mapping(_get(config, f'{PREFIX}.PlannerTargets', '{}'))
    floors = _mapping(_get(config, f'{PREFIX}.HardFloorItems', '{}'))
    buffers = _mapping(_get(config, f'{PREFIX}.DailyBufferItems', '{}'))
    idle = _mapping(_get(config, f'{PREFIX}.IdleAccumulatingItems', '{}'))
    reserves = {}
    menus, capacities = {}, {}
    for place, shop in RESTAURANT_TO_SHOP.items():
        raw = _get(config, f'IslandBusiness.IslandBusinessShop{shop}.PlannedMenu', '')
        if not raw:
            continue
        menus[place] = _mapping(raw)
        try:
            capacities[place] = get_restaurant_capacity(Reader(), place)
        except Exception:
            capacities[place] = None
        if capacities[place] is not None:
            for item in menus[place]:
                reserves[item] = reserves.get(item, 0) + capacities[place]
    return {
        'version': 1, 'generated_at': '', 'legacy': True,
        'daily_revenue': None, 'daily_profit': None,
        # 旧计划未保存需求分项，不从差值猜订单或制造数量。
        'items': _rows(targets, floors, buffers, reserves, {}, idle),
        'production': [], 'menus': _menus(menus, capacities), 'observations': {}, 'dispatches': {},
    }


def get_planner_report(config):
    """读取展示报告，兼容旧配置；读取失败不影响配置页或游戏任务。"""
    if not _get(config, f'{PREFIX}.PlanFingerprint', ''):
        return None
    try:
        raw = _get(config, f'{PREFIX}.PlannerReport', '{}')
        report = json.loads(raw) if isinstance(raw, str) else copy.deepcopy(raw)
        if not isinstance(report, dict) or report.get('version') != 1:
            report = _legacy_report(config)
        report['enabled'] = bool(_get(config, f'{PREFIX}.Enabled', False))
        return report
    except Exception as error:
        logger.warning(f'[岛屿-规划详情] 无法读取展示报告: {error}')
        return None


def _optional_report(method):
    @wraps(method)
    def wrapped(config, *args, **kwargs):
        # 仅隔离辅助展示读写；游戏操作、OCR 与恢复异常仍在调用方处理。
        try:
            if not _get(config, f'{PREFIX}.Enabled', False):
                return False
            report = get_planner_report(config)
            if report is None:
                return False
            changed = method(report, *args, **kwargs)
            if changed:
                config.cross_set(f'{PREFIX}.PlannerReport', json.dumps(report, ensure_ascii=False, sort_keys=True))
            return changed
        except Exception as error:
            logger.warning(f'[岛屿-规划详情] 辅助观测未保存，任务继续: {error}')
            return False
    return wrapped


@_optional_report
def record_planner_stocks(report, stocks, source):
    """只保存正向确认的现货，未知缺项不补零，重复读取以最新观测替换。"""
    wanted = {row['id'] for row in report['items']}
    observations = report.setdefault('observations', {})
    at = now().isoformat(sep=' ', timespec='seconds')
    changed = False
    for key, stock in stocks.items():
        item = resolve_item_id(key)
        if item not in wanted or not _amount(stock):
            continue
        observations[str(item)] = {'stock': float(stock), 'at': at, 'source': source, 'stale': False}
        changed = True
    return changed


@_optional_report
def record_planner_dispatch(report, outputs, source):
    """保存最近一次确认下单的预计产出，既不累计也不当作已收获或当前在产。"""
    wanted = {row['id'] for row in report['items']}
    dispatches = report.setdefault('dispatches', {})
    at = now().isoformat(sep=' ', timespec='seconds')
    changed = False
    for key, amount in outputs.items():
        item = resolve_item_id(key)
        if item not in wanted or not _amount(amount) or amount == 0:
            continue
        dispatches[str(item)] = {'amount': float(amount), 'at': at, 'source': source}
        changed = True
    return changed


@_optional_report
def invalidate_planner_stocks(report, items, source):
    """消耗后保留操作前观测供回看，缺口等待下一次真实库存核验。"""
    changed = False
    for key in items:
        observation = report.get('observations', {}).get(str(resolve_item_id(key)))
        if observation is not None:
            observation['stale'] = True
            observation['reason'] = source
            changed = True
    return changed
