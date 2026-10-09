"""将 ALAS 纯生产规划连接到本地生产任务，保持手工配置原件。"""

import hashlib
import json
import math
from collections import defaultdict

from yaml import safe_dump

import module.config.server as server
from module.config.time_source import now
from module.config.utils import server_time_offset
from module.island.data import DIC_ISLAND_RECIPE, DIC_ISLAND_SLOT
from module.island.item_ids import ITEM_ID_TO_LOCAL, LOCAL_TO_ITEM_ID, resolve_item_id
from module.island.order_stock import (
    get_business_menu_items, get_menu_reserve_items, get_restaurant_settings,
    planner_enabled, save_planned_menus,
)
from module.island.planner_utils import (
    ceil_with_epsilon, get_current_activity_list, get_current_season_remaining_days,
    load_item_mapping, load_technology_status, normalize_item_keys,
    get_stuck_season_order_requirements, normalize_item_needs,
)
from module.island.production_plan_calculator import ProductionPlanCalculator
from module.logger import logger


CONFIG_PREFIX = 'IslandPlan.IslandProductionPlanner'
INPUT_DEFAULTS = {
    'TechnologyStatus': '', 'FieldsEfficiency': 0, 'OrchardEfficiency': 0,
    'NurseryEfficiency': 0, 'DailyProfitLowerLimit': 0,
    'DailyBufferSafetyMargin': 0, 'TaskTarget': '{}', 'HardFloorItems': '{}',
}
GROUP_CONFIG = {
    'field': ('IslandFarm', 'IslandFarm', 4),
    'orchard': ('IslandFarm', 'IslandOrchard', 4),
    'nursery': ('IslandFarm', 'IslandNursery', 2),
    'mine': ('IslandMineForest', 'IslandMine', 4),
    'wood': ('IslandMineForest', 'IslandForest', 4),
    'ranch_chicken': ('IslandRancher', None, 1),
    'ranch_pig': ('IslandRancher', None, 1),
    'ranch_cow': ('IslandRancher', None, 1),
    'ranch_sheep': ('IslandRancher', None, 1),
    'fishery': ('IslandRancher', 'IslandFishery', 3),
    'koi': ('IslandRestaurant', 'IslandRestaurant', 2),
    'bear': ('IslandTeahouse', 'IslandTeahouse', 2),
    'eatery': ('IslandJuuEatery', 'IslandJuuEatery', 2),
    'grill': ('IslandGrill', 'IslandGrill', 2),
    'cafe': ('IslandJuuCoffee', 'IslandJuuCoffee', 2),
    'manufacturing_lumber': ('IslandManufacture', 'WoodProcessing', 2),
    'manufacturing_machinery': ('IslandManufacture', 'Industrial', 2),
    'manufacturing_electronic': ('IslandManufacture', 'ElectronicProcessing', 2),
    'manufacturing_crafts': ('IslandManufacture', 'Handmade', 2),
}
FOOD_GROUPS = {'koi', 'bear', 'eatery', 'grill', 'cafe'}


class IslandPlanningError(ValueError):
    """规划输入、能力或需求无法安全生成计划；旧计划不得被半份结果覆盖。"""


def read_config(config, argument, default=None):
    cross_get = getattr(config, 'cross_get', None)
    if not callable(cross_get):
        return default
    return cross_get(f'{CONFIG_PREFIX}.{argument}', default=default)


def _cross_get(config, path, default=None):
    cross_get = getattr(config, 'cross_get', None)
    return cross_get(path, default=default) if callable(cross_get) else default


def get_configured_slots(config, temporary_manufacture=False, factory_enabled=None):
    """科技决定是否解锁，用户任务开关与岗位数进一步限定实际生产能力。"""
    slots = set()
    for group, (task, arguments, maximum) in GROUP_CONFIG.items():
        enabled = _cross_get(config, f'{task}.Scheduler.Enable', False)
        if task == 'IslandManufacture' and factory_enabled is not None:
            enabled = factory_enabled
        if not enabled and not (task == 'IslandManufacture' and temporary_manufacture):
            continue
        if arguments is None:
            count = maximum
        else:
            key = 'PostNumber' if group in FOOD_GROUPS else 'Positions'
            count = int(_cross_get(config, f'{task}.{arguments}.{key}', 0) or 0)
        if not 0 <= count <= maximum:
            raise IslandPlanningError(f'{task} 岗位数 {count} 超出允许范围 0..{maximum}')
        slots.update(ProductionPlanCalculator.GROUP_TO_SLOTS[group][:count])
    return slots


def _manufacture_recipes():
    result = {}
    for group, slots in ProductionPlanCalculator.GROUP_TO_SLOTS.items():
        if not group.startswith('manufacturing_'):
            continue
        for slot in slots:
            data = DIC_ISLAND_SLOT[slot]
            for recipe in (*data.get('formula', ()), *data.get('activity_formula', ())):
                for item in DIC_ISLAND_RECIPE[recipe]['commission_product']:
                    result[item] = recipe
    from module.island.recipe_groups import SEASONAL_RECIPE_GROUPS
    for recipe, group in SEASONAL_RECIPE_GROUPS.items():
        if group.startswith('manufacturing_'):
            for item in DIC_ISLAND_RECIPE[recipe]['commission_product']:
                result[item] = recipe
    return result


def manufacture_order_targets(stuck_order_id):
    """季节委托制造成品及完整制造依赖，数量使用官方单次实际产出率。"""
    recipes = _manufacture_recipes()
    requests = get_stuck_season_order_requirements(stuck_order_id)
    final = {item: count for item, count in requests.items() if item in recipes}
    needed = defaultdict(int)
    visiting = set()

    def include(item, count):
        if item not in recipes:
            return
        if item in visiting:
            raise IslandPlanningError(f'制造配方依赖成环：{item}')
        visiting.add(item)
        needed[item] += count
        recipe = DIC_ISLAND_RECIPE[recipes[item]]
        batches = math.ceil(count / recipe['commission_product'][item])
        for material, quantity in recipe['commission_cost'].items():
            include(material, batches * quantity)
        visiting.remove(item)
    for item, count in final.items():
        include(item, count)
    return dict(needed), final


def finish_auto_manufacture(config, stocks, working=False):
    """只有自身临时开启的工坊，且委托成品真实入库、所有岗位已收取时才关闭。"""
    if not read_config(config, 'AutoManufactureActive', False) or working:
        return False
    raw = read_config(config, 'OrderManufactureFinalTargets', '{}')
    finals = normalize_item_keys(json.loads(raw) if isinstance(raw, str) else raw)
    if any(item not in stocks or stocks[item] < target for item, target in finals.items()):
        return False
    with config.multi_set():
        config.cross_set('IslandManufacture.Scheduler.Enable', False)
        original = read_config(config, 'AutoManufactureNextRun', '')
        if original:
            config.cross_set('IslandManufacture.Scheduler.NextRun', original)
        config.cross_set(f'{CONFIG_PREFIX}.AutoManufactureActive', False)
        config.cross_set(f'{CONFIG_PREFIX}.OrderManufactureTargets', '{}')
        config.cross_set(f'{CONFIG_PREFIX}.OrderManufactureFinalTargets', '{}')
        config.cross_set(f'{CONFIG_PREFIX}.AutoManufactureNextRun', '')
        order_id = _cross_get(config, 'IslandDailyOrder.IslandDailyOrder.StuckSeasonOrderId', 0)
        config.cross_set(f'{CONFIG_PREFIX}.CompletedManufactureOrderId', order_id)
        config.cross_set(f'{CONFIG_PREFIX}.PlanFingerprint', _input_fingerprint(
            config, now() - server_time_offset(), order_id, factory_enabled=False))
        config.cross_set(f'{CONFIG_PREFIX}.PlannerStatus', '季节委托制造已入库，等待订单重新核验')
    if _cross_get(config, 'IslandDailyOrder.Scheduler.Enable', False):
        config.task_delay(minute=0, task='IslandDailyOrder')
    logger.info('[岛屿-生产规划] 季节委托制造成品已入库，关闭本次临时工坊')
    return True


def get_recipe_limits(config):
    """用户明确忽略的原料保持忽略，求解不能假定它会被生产。"""
    excluded = set()
    if _cross_get(config, 'IslandFarm.IslandOrchard.IgnoreAvocado', False):
        excluded.add(2021)
    if _cross_get(config, 'IslandFarm.IslandNursery.IgnorePineapple', False):
        excluded.add(4021)
    return {recipe for recipe, data in DIC_ISLAND_RECIPE.items()
            if not (set(data['commission_product']) & excluded)}


def get_planned_recipe_items(config, group, existing_items=()):
    """按真实活动与已扫描科技补齐本场所配方；旧季节手工列表不裁掉有效目标。"""
    calculator = ProductionPlanCalculator(
        technology_status=load_technology_status(read_config(config, 'TechnologyStatus', '')),
        activity_list=get_current_activity_list(), execution_limits={'recipes': get_recipe_limits(config)})
    originals = {item['name']: item for item in existing_items}
    items = []
    for recipe, unlocked in calculator.recipe_available.items():
        if not unlocked or calculator.recipe_group.get(recipe) != group:
            continue
        item_id = next(iter(DIC_ISLAND_RECIPE[recipe]['commission_product']))
        name = ITEM_ID_TO_LOCAL.get(item_id)
        if name is None:
            raise IslandPlanningError(f'有效配方 {recipe} 产物 {item_id} 没有本地名称映射')
        item = dict(originals.get(name, {'name': name}))
        item['recipe_id'] = recipe
        item['item_id'] = item_id
        items.append(item)
    return items


def _input_fingerprint(config, current_time, stuck_order_id, factory_enabled=None):
    payload = {key: read_config(config, key, default) for key, default in INPUT_DEFAULTS.items()}
    payload.update({
        'server': server.server, 'day': current_time.date().isoformat(),
        'stuck_order': stuck_order_id,
        'slots': sorted(get_configured_slots(config, factory_enabled=factory_enabled)),
        'restaurants': get_restaurant_settings(config),
        'season': _cross_get(config, 'IslandPlan.IslandPlan.Season', 'spring'),
        'activities': get_current_activity_list(current_time),
        'gather': _cross_get(config, 'IslandDailyGather.Scheduler.Enable', False),
    })
    return hashlib.sha256(json.dumps(payload, ensure_ascii=True, sort_keys=True).encode()).hexdigest()


def awaiting_manufacture_delivery(config, stuck_order_id):
    """同一季节单已完成制造后，等待订单实读；跨日也不能重复开启工坊。"""
    return bool(stuck_order_id and read_config(config, 'PlanFingerprint', '') and
                read_config(config, 'CompletedManufactureOrderId', 0) == stuck_order_id)


def island_services_suspended(config):
    name = getattr(config, 'config_name', None)
    if not isinstance(name, str) or not name:
        return False
    from module.api.island_suspend import read_state
    return bool(read_state(name).get('suspended'))


class IslandProductionPlanner:
    """按需扫描科技、求解完整配方，再原子写入规划与经营菜单。"""

    def __init__(self, config, device=None, scanner_factory=None):
        self.config = config
        self.device = device
        self.scanner_factory = scanner_factory

    def _technology_status(self):
        cached = read_config(self.config, 'TechnologyStatus', '')
        if cached and not read_config(self.config, 'RescanTechnology', False):
            return load_technology_status(cached)
        scanner_factory = self.scanner_factory
        if scanner_factory is None:
            from module.island.technology_scanner import IslandTechnologyScanner
            scanner_factory = IslandTechnologyScanner
        status = load_technology_status(scanner_factory(self.config, self.device).get_technology_status())
        if not status:
            raise IslandPlanningError('科技扫描没有返回有效结果，未生成生产计划')
        with self.config.multi_set():
            self.config.cross_set(f'{CONFIG_PREFIX}.TechnologyStatus', safe_dump(status, sort_keys=True))
            self.config.cross_set(f'{CONFIG_PREFIX}.RescanTechnology', False)
        return status

    def run(self, stuck_season_order_id=None, current_time=None, export=True):
        """科技、需求和菜单全部验证通过后才替换计划，不修改手工生产槽位。"""
        if not planner_enabled(self.config):
            return None
        current_time = current_time or now() - server_time_offset()
        if stuck_season_order_id is None:
            stuck_season_order_id = _cross_get(
                self.config, 'IslandDailyOrder.IslandDailyOrder.StuckSeasonOrderId', 0)
        if awaiting_manufacture_delivery(self.config, stuck_season_order_id):
            return None
        try:
            technology_status = self._technology_status()
            manufacture_needs, manufacture_final = manufacture_order_targets(stuck_season_order_id)
            already_auto = bool(read_config(self.config, 'AutoManufactureActive', False))
            manual_manufacture = bool(_cross_get(self.config, 'IslandManufacture.Scheduler.Enable', False)) and not already_auto
            explicit_needs = normalize_item_needs(
                load_item_mapping(read_config(self.config, 'TaskTarget', '{}')),
                default_period=get_current_season_remaining_days(current_time))
            explicit_factory = set(explicit_needs) & set(_manufacture_recipes())
            if explicit_factory and not manual_manufacture:
                names = ', '.join(ITEM_ID_TO_LOCAL.get(item, str(item)) for item in sorted(explicit_factory))
                raise IslandPlanningError(f'独立制造目标 {names} 需要用户主动开启制造任务；季节委托临时开关不承诺独立目标')
            temporary_manufacture = bool(manufacture_final) and not manual_manufacture
            if temporary_manufacture and island_services_suspended(self.config):
                raise IslandPlanningError('岛屿服务暂停中，不自动开启制造工坊')
            season = _cross_get(self.config, 'IslandPlan.IslandPlan.Season', 'spring')
            slots = get_configured_slots(self.config, temporary_manufacture)
            if already_auto and not manufacture_final:
                slots -= {slot for group, values in ProductionPlanCalculator.GROUP_TO_SLOTS.items()
                          if group.startswith('manufacturing_') for slot in values}
            recipe_limits = get_recipe_limits(self.config)
            if temporary_manufacture or already_auto:
                factory = _manufacture_recipes()
                required = {factory[item] for item in manufacture_needs}
                recipe_limits -= set(factory.values()) - required
            calculator = ProductionPlanCalculator(
                technology_status=technology_status,
                activity_list=get_current_activity_list(current_time),
                place_efficiency={
                    101: float(read_config(self.config, 'FieldsEfficiency', 0)),
                    501: float(read_config(self.config, 'OrchardEfficiency', 0)),
                    502: float(read_config(self.config, 'NurseryEfficiency', 0)),
                },
                restaurant_settings=get_restaurant_settings(self.config),
                daily_profit_lower_limit=float(read_config(self.config, 'DailyProfitLowerLimit', 0)),
                daily_buffer_safety_margin=float(read_config(self.config, 'DailyBufferSafetyMargin', 0)),
                execution_limits={
                    'slots': slots, 'strict_demands': True,
                    'recipes': recipe_limits,
                    'menus': {restaurant: {LOCAL_TO_ITEM_ID[name] for name in get_business_menu_items(restaurant, season)}
                              for restaurant in get_restaurant_settings(self.config)},
                    'passive_supply': bool(_cross_get(self.config, 'IslandDailyGather.Scheduler.Enable', False)),
                },
            )
            calculator.solve_production_plan(
                hard_floor_items=load_item_mapping(read_config(self.config, 'HardFloorItems', '{}')),
                task_target_items=load_item_mapping(read_config(self.config, 'TaskTarget', '{}')),
                task_target_period=get_current_season_remaining_days(current_time),
                stuck_season_order_id=stuck_season_order_id,
                current_time=current_time,
            )
            if not calculator.lp_success:
                diagnostics = '; '.join(calculator.failure_diagnostics or [calculator.lp_message])
                raise IslandPlanningError(f'生产规划无可行解：{diagnostics}')
            menus = {restaurant: {} for restaurant in get_restaurant_settings(self.config)}
            for (restaurant, item), amount in calculator.sell_plan.items():
                if amount > calculator.NET_ACCUMULATING_EPSILON:
                    menus[restaurant][item] = round(amount, 6)
            reserve = defaultdict(int)
            for restaurant, menu in menus.items():
                if len(menu) > 5:
                    raise IslandPlanningError(f'店铺 {restaurant} 规划超过本地 5 个菜单位置')
                for item in menu:
                    reserve[item] += calculator.restaurant_capacity[restaurant]
            targets = {}
            for item in set(calculator.product_daily_buffer_items) | set(calculator.hard_floor_items) | set(reserve) | set(calculator.demand_items):
                floor = calculator.hard_floor_items.get(item, 0)
                width = calculator.product_daily_buffer_items.get(item, 0)
                requirement = calculator.demand_items.get(item, {}).get('total_need_count', 0)
                target = ceil_with_epsilon(floor + reserve.get(item, 0) + width + requirement)
                if target > 0:
                    targets[str(item)] = target
            calculator.local_targets = targets
            calculator.local_menus = menus
            if temporary_manufacture:
                for item in _manufacture_recipes():
                    targets.pop(str(item), None)
                targets.update({str(item): count + calculator.hard_floor_items.get(item, 0)
                                for item, count in manufacture_needs.items()})
            final_targets = {str(item): count + calculator.hard_floor_items.get(item, 0)
                             for item, count in manufacture_final.items()}
            if export:
                from module.island.planner_report import build_planner_report
                try:
                    report = build_planner_report(calculator, targets, menus, reserve, now(),
                                                  manufacture_needs if temporary_manufacture else None)
                    report_json = json.dumps(report, ensure_ascii=False, sort_keys=True)
                except Exception as error:
                    # 展示副本失败不阻断已验证的生产方案；空副本会由读取端回显新目标。
                    logger.warning(f'[岛屿-规划详情] 无法生成详细副本，保留目标回显: {error}')
                    report_json = '{}'
                with self.config.multi_set():
                    save_planned_menus(self.config, menus)
                    self.config.cross_set(f'{CONFIG_PREFIX}.CompletedManufactureOrderId', 0)
                    if temporary_manufacture and not already_auto:
                        original = _cross_get(self.config, 'IslandManufacture.Scheduler.NextRun', '')
                        self.config.cross_set(f'{CONFIG_PREFIX}.AutoManufactureNextRun', str(original or ''))
                        self.config.cross_set(f'{CONFIG_PREFIX}.AutoManufactureActive', True)
                        self.config.cross_set('IslandManufacture.Scheduler.Enable', True)
                    if temporary_manufacture or already_auto:
                        self.config.cross_set(f'{CONFIG_PREFIX}.OrderManufactureTargets', json.dumps(
                            {str(item): count + calculator.hard_floor_items.get(item, 0)
                             for item, count in manufacture_needs.items()}, sort_keys=True))
                        self.config.cross_set(f'{CONFIG_PREFIX}.OrderManufactureFinalTargets', json.dumps(final_targets, sort_keys=True))
                    # 临时启用本身改变可执行岗位，指纹必须在启用后计算，避免下一任务重复求解。
                    fingerprint = _input_fingerprint(self.config, current_time, stuck_season_order_id,
                                                     factory_enabled=True if temporary_manufacture else None)
                    for argument, value in {
                        'DailyBufferItems': calculator.daily_buffer_items_to_yaml(),
                        'IdleAccumulatingItems': calculator.idle_accumulating_items_to_yaml(),
                        'PlannerTargets': json.dumps(targets, sort_keys=True),
                        'PlannerReport': report_json,
                        'PlanFingerprint': fingerprint,
                        'PlannerStatus': f'已生成：每日预计收入 {calculator.daily_coin_revenue:.0f}，净收益 {calculator.daily_profit:.0f}',
                    }.items():
                        self.config.cross_set(f'{CONFIG_PREFIX}.{argument}', value)
                self._wake_producers(calculator)
            logger.info(f'[岛屿-生产规划] 生成 {len(targets)} 项目标及 {sum(len(menu) for menu in menus.values())} 道经营菜单')
            return calculator
        except (ValueError, TypeError) as exc:
            error = exc if isinstance(exc, IslandPlanningError) else IslandPlanningError(str(exc))
            if export:
                self.config.cross_set(f'{CONFIG_PREFIX}.PlannerStatus', f'规划失败，保留旧计划：{error}')
            raise error from exc

    def _wake_producers(self, calculator):
        tasks = set()
        for recipe_id, amount in calculator.production_plan.items():
            if amount <= calculator.NET_ACCUMULATING_EPSILON:
                continue
            group = calculator.recipe_group[recipe_id]
            tasks.add(GROUP_CONFIG[group][0])
        if calculator.shop_plan:
            if any(shop < 110000 for shop in calculator.shop_plan):
                tasks.add('IslandRancher')
        for task in sorted(tasks):
            if _cross_get(self.config, f'{task}.Scheduler.Enable', False):
                self.config.task_delay(minute=0, task=task)


def refresh_production_plan(config, device=None):
    """生产入口只在首次、日期或规划输入改变时求解，库存始终由任务当轮重读。"""
    if not planner_enabled(config):
        return False
    current_time = now() - server_time_offset()
    stuck = _cross_get(config, 'IslandDailyOrder.IslandDailyOrder.StuckSeasonOrderId', 0)
    if awaiting_manufacture_delivery(config, stuck):
        return True
    fingerprint = _input_fingerprint(config, current_time, stuck)
    if read_config(config, 'PlanFingerprint', '') == fingerprint and not read_config(config, 'RescanTechnology', False):
        return True
    try:
        IslandProductionPlanner(config, device).run(stuck, current_time=current_time)
        return True
    except IslandPlanningError as exc:
        valid = bool(read_config(config, 'PlanFingerprint', ''))
        logger.warning(f'[岛屿-生产规划] 无法更新计划，{"保留上一有效计划" if valid else "本轮继续手工策略"}：{exc}')
        return valid


def load_planner_targets(config):
    """取得已生成目标，并补上经营换菜后的最新一架预留。"""
    if not planner_enabled(config) or not read_config(config, 'PlanFingerprint', ''):
        return {}
    raw = read_config(config, 'PlannerTargets', '{}')
    try:
        values = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(values, dict):
            raise ValueError('目标必须是字典')
        targets = {}
        for key, value in values.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f'{key} 目标数量无效')
            targets[resolve_item_id(key)] = ceil_with_epsilon(value)
        for item, reserve in get_menu_reserve_items(config).items():
            targets[item] = max(targets.get(item, 0), reserve)
        return targets
    except (ValueError, TypeError) as exc:
        raise IslandPlanningError(f'保存的生产规划目标无效：{exc}') from exc


def planner_target(config, name, manual_target=0):
    item_id = LOCAL_TO_ITEM_ID.get(name)
    if not planner_enabled(config) or not read_config(config, 'PlanFingerprint', ''):
        return manual_target
    return load_planner_targets(config).get(item_id, 0)


def planner_active(config):
    return planner_enabled(config) and bool(read_config(config, 'PlanFingerprint', ''))


def planner_idle_products(config, available_items, inventory=None):
    """按超额库存/每日积累速率排序余岗商品，已生成空计划不会恢复旧挂机品。"""
    if not planner_active(config):
        return None
    rates = normalize_item_keys(load_item_mapping(read_config(config, 'IdleAccumulatingItems', '{}')))
    targets = load_planner_targets(config)
    inventory = inventory or {}
    products = [name for name in available_items if rates.get(LOCAL_TO_ITEM_ID.get(name), 0) > 0]
    products.sort(key=lambda name: (
        max(inventory.get(name, 0) - targets.get(LOCAL_TO_ITEM_ID[name], 0), 0) / rates[LOCAL_TO_ITEM_ID[name]], name))
    return products


def load_production_protection(config):
    """取得原料保底：显式保留线与下一架经营预留，日缓冲仍可用于配方。"""
    if not planner_enabled(config):
        return {}
    floors = normalize_item_keys(load_item_mapping(read_config(config, 'HardFloorItems', '{}')))
    reserve = get_menu_reserve_items(config)
    requested = normalize_item_needs(load_item_mapping(read_config(config, 'TaskTarget', '{}')),
                                    default_period=get_current_season_remaining_days())
    direct = get_stuck_season_order_requirements(_cross_get(
        config, 'IslandDailyOrder.IslandDailyOrder.StuckSeasonOrderId', 0))
    claims = {item: need['total_need_count'] + direct.get(item, 0) for item, need in requested.items()}
    for item, count in direct.items():
        claims.setdefault(item, count)
    return {ITEM_ID_TO_LOCAL[item]: ceil_with_epsilon(
        max(float(floors.get(item, 0)), 0) + reserve.get(item, 0) + claims.get(item, 0))
        for item in set(floors) | set(reserve) | set(claims) if item in ITEM_ID_TO_LOCAL}


def merge_food_targets(config, products, available_items):
    """有效规划接管运行目标；手工槽位原件用于关闭规划后的恢复。"""
    if not planner_active(config):
        return list(products)
    targets = load_planner_targets(config)
    planned = [(name, targets[LOCAL_TO_ITEM_ID[name]]) for name in available_items
               if name in LOCAL_TO_ITEM_ID and targets.get(LOCAL_TO_ITEM_ID[name], 0) > 0]
    return planned
