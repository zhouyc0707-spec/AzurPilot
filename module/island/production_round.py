"""手工岛屿生产的单轮准备；任务仍由原调度器分别执行。"""

from copy import copy, deepcopy
from datetime import datetime, timedelta
from importlib import import_module

from module.config.time_source import now as current_time
from module.config.config import TaskEnd, _round_up_half_hour, _round_up_hour, name_to_function
from module.config.utils import ensure_time
from module.exception import GamePageUnknownError, GameStuckError, GameTooManyClickError
from module.island.round_inventory import IslandRoundInventory
from module.logger import logger


PRODUCTION_TASKS = {
    'IslandFarm': ('island_farm', 'IslandFarm'),
    'IslandRancher': ('island_rancher', 'IslandRancher'),
    'IslandMineForest': ('island_mine_forest', 'IslandMineForest'),
    'IslandRestaurant': ('island_restaurant', 'IslandRestaurant'),
    'IslandTeahouse': ('island_teahouse', 'IslandTeahouse'),
    'IslandGrill': ('island_grill', 'IslandGrill'),
    'IslandJuuEatery': ('island_juu_eatery', 'IslandJuuEatery'),
    'IslandJuuCoffee': ('island_juu_coffee', 'IslandJuuCoffee'),
    'IslandManufacture': ('island_manufacture', 'IslandManufacture'),
}


def invalidates_round(task):
    """委托、科研等不消耗岛屽物资；岛屿其他玩法和重启使本轮账失效。"""
    return task == 'Restart' or (task.startswith('Island') and task not in PRODUCTION_TASKS)


def preparation_retry_deadline(owner, task):
    """共享准备的冷却跨游戏重启保留，不阻挡委托、科研或恢复入口。"""
    deadline = getattr(owner, '_island_prepare_retry_at', None)
    if deadline is None:
        return None
    if deadline <= current_time():
        owner._island_prepare_retry_at = None
        return None
    if task not in PRODUCTION_TASKS:
        return None
    previous = owner.config.cross_get(f'{task}.Scheduler.NextRun')
    if isinstance(previous, str):
        previous = datetime.fromisoformat(previous)
    return max(deadline, previous) if previous is not None else deadline


def delay_failed_preparation(owner, task):
    """同一准备流程失败时延后整组，避免换一个原任务入口重复触发故障。"""
    config = owner.config
    deadline = (current_time() + timedelta(minutes=ensure_time(config.Scheduler_FailureInterval))).replace(microsecond=0)
    alignment = config.cross_get('IslandPlan.IslandPlan.TaskAlignment', default=config.IslandPlan_TaskAlignment)
    if alignment == 'half_hour':
        deadline = _round_up_half_hour(deadline)
    elif alignment == 'hour':
        deadline = _round_up_hour(deadline)
    owner._island_prepare_retry_at = deadline
    tasks = []
    with config.multi_set():
        for name in PRODUCTION_TASKS:
            scheduler = config.data.get(name, {}).get('Scheduler', {})
            if name != task and not scheduler.get('Enable', False):
                continue
            tasks.append(name)
            previous = scheduler.get('NextRun')
            if isinstance(previous, str):
                previous = datetime.fromisoformat(previous)
            if previous is None or previous < deadline:
                # 沿用原整点/半点对齐；已经排得更晚的任务保持不变。
                config.task_delay(target=deadline, task=name)
    logger.warning(f'[岛屿单轮] 共享准备失败，生产任务统一冷却至 '
                   f'{owner._island_prepare_retry_at}，保留更晚排期：{tasks}')


def select_unfinished_task(config, task, session):
    """跨整点仍先完成旧轮；委托科研等原优先任务不受影响。"""
    if (session is None or not session.valid(config) or task not in PRODUCTION_TASKS
            or task in session.pending):
        return task
    for candidate in getattr(config, 'pending_task', ()):
        if candidate.command in session.pending:
            return candidate.command
    return task


def round_config_signature(config):
    """任务定时器和运行记录不影响本轮，手工目标/开关变化立即使其失效。"""
    result = {}
    for name, values in config.data.items():
        if not name.startswith('Island'):
            continue
        values = deepcopy(values)
        values.pop('Storage', None)
        values.get('Scheduler', {}).pop('NextRun', None)
        result[name] = values
    return result


def collection_config(config, task):
    """只读副本绑定各任务；不调用会读写用户文件的 init_task。"""
    result = copy(config)
    for name in ('data', 'bound', 'overridden', 'modified'):
        object.__setattr__(result, name, deepcopy(getattr(config, name, {})))
    object.__setattr__(result, 'auto_update', False)
    object.__setattr__(result, 'modified', {})
    object.__setattr__(result, '_scheduler_overrides', {})
    result.bind(task)
    object.__setattr__(result, 'task', name_to_function(task))
    return result


def task_due(config, task, now):
    value = config.data.get(task, {}).get('Scheduler', {}).get('NextRun')
    if value is None:
        return False
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value <= now


def has_idle_posts(runner):
    """只认巡检后的空闲状态，不把未识别和工作中当成可派遣。"""
    if any(info.get('state', info.get('status')) == 'idle'
           and info.get('round_enabled', info.get('eligible', True))
           for info in getattr(runner, 'posts', {}).values()):
        return True
    for post, state in getattr(runner, '_round_ranch_states', {}).items():
        if state == 'idle' and runner.is_ranch_post_enabled(post):
            return True
    fishery = getattr(runner, '_round_fishery', None)
    return fishery is not None and has_idle_posts(fishery)


class IslandProductionRound:
    """保留尚未完成的本轮任务，允许委托科研插队后继续，不持久化到磁盘。"""

    def __init__(self, config, device, task):
        self.signature = round_config_signature(config)
        self.started_at = current_time()
        self.runners = {}
        self.pending = set()
        self.inventory = IslandRoundInventory()
        self.ready = False
        self._prepare(config, device, task)

    @staticmethod
    def _check_stop(config):
        event = getattr(config, 'stop_event', None)
        if event is not None and event.is_set():
            raise TaskEnd('岛屿统一收获期间收到停止请求')

    def _prepare(self, config, device, task):
        from module.island.stock_probe import read_item_stocks, ROUND_WAREHOUSE_SWIPES

        logger.hr('岛屿单轮：统一收获', level=1)
        for name, (module, class_name) in PRODUCTION_TASKS.items():
            self._check_stop(config)
            cls = getattr(import_module(f'module.island.{module}'), class_name)
            runner = cls(config=collection_config(config, name), device=device)
            runner.prepare_round()
            self.runners[name] = runner
            enabled = config.data.get(name, {}).get('Scheduler', {}).get('Enable', False)
            if name == task or (enabled and (task_due(config, name, self.started_at) or has_idle_posts(runner))):
                self.pending.add(name)

        self._check_stop(config)
        items = set()
        for name in self.pending:
            items.update(self.runners[name].round_stock_items())
        logger.hr('岛屿单轮：集中盘点', level=1)
        observed = read_item_stocks(self.runners[task], items,
                                   max_swipes=ROUND_WAREHOUSE_SWIPES, reset_to_top=True)
        for item, quantity in observed.items():
            self.inventory.observe(item, quantity)
        for runner in self.runners.values():
            runner.island_round = self.inventory
        if 'IslandRancher' in self.pending:
            from module.island.round_recipe import probe_round_missing_stocks
            probe_round_missing_stocks(
                [self.runners[name] for name in PRODUCTION_TASKS if name in self.pending],
                self.inventory, {2000, 2001, 2008, 2600, 2602, 3000, 3001, 3002, 3003, 3004})

        # 已收获成为空岗的启用任务提前进入原优先级队列，不替它执行或写成功。
        with config.multi_set():
            for name in self.pending:
                if name != task and not task_due(config, name, self.started_at):
                    config.cross_set(f'{name}.Scheduler.NextRun', self.started_at)
        self.ready = True
        logger.attr('岛屿本轮任务', sorted(self.pending))
        logger.attr('岛屿本轮明确库存', len(observed))

    def valid(self, config):
        return self.ready and bool(self.pending) and self.signature == round_config_signature(config)

    def matches(self, config, task):
        return self.valid(config) and task in self.pending

    def run_task(self, config, task):
        runner = self.runners[task]
        runner.config = config
        runner.run()
        self.pending.remove(task)


def run_production_task(owner, task, cls):
    """接到单个原调度任务后准备或复用本轮；异常不会遗留库存供恢复使用。"""
    from module.island.production_planner import planner_enabled

    if planner_enabled(owner.config):
        owner._island_production_round = None
        return cls(config=owner.config, device=owner.device).run()
    session = getattr(owner, '_island_production_round', None)
    try:
        if session is None or not session.matches(owner.config, task):
            owner._island_production_round = None
            try:
                session = IslandProductionRound(owner.config, owner.device, task)
            except (GameStuckError, GameTooManyClickError, GamePageUnknownError):
                delay_failed_preparation(owner, task)
                raise
            owner._island_production_round = session
        else:
            logger.info(f'[岛屿单轮] 继续 {task}，复用本轮库存，剩余任务 {len(session.pending)} 个')
        session.run_task(owner.config, task)
    except BaseException:
        owner._island_production_round = None
        raise
    if not session.pending:
        owner._island_production_round = None
        logger.info('[岛屿单轮] 本轮生产任务已结束，丢弃临时库存')
