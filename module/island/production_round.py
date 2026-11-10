"""手工岛屿生产的单轮准备；任务仍由原调度器分别执行。"""

from copy import copy, deepcopy
from datetime import datetime, timedelta
from importlib import import_module

from module.config.time_source import now as current_time
from module.config.config import TaskEnd, _round_up_half_hour, _round_up_hour, name_to_function
from module.config.utils import ensure_time
from module.exception import GamePageUnknownError, GameStuckError, GameTooManyClickError, IslandFishExchangeError
from module.island.round_inventory import IslandRoundInventory
from module.island.seasonal_progress import invalidate_progress, publish_round_progress
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

# 渔业属于牧场；鱼肉兑换另依赖经营菜单预留和季节订单保留量。
ROUND_CONFIG_TASKS = frozenset(PRODUCTION_TASKS) | {
    'IslandPlan', 'IslandSeasonalProduction', 'IslandBusiness', 'IslandDailyOrder',
}


def invalidates_round(task):
    """委托、科研等不消耗岛屽物资；岛屿其他玩法和重启使本轮账失效。"""
    return task == 'Restart' or (task.startswith('Island') and task not in PRODUCTION_TASKS)


def preparation_retry_deadline(owner, task):
    """岛屿生产故障冷却跨游戏重启保留，不阻挡委托、科研或恢复入口。"""
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


def delay_failed_preparation(owner, task, *, reason='共享准备失败'):
    """准备或专项兑换失败时延后整组，避免换入口重复触发同一故障。"""
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
    logger.warning(f'[岛屿单轮] {reason}，生产任务统一冷却至 '
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
    """只比较生产输入；定时器、运行记录和其他岛屿任务设置不影响本轮。"""
    result = {}
    for name, values in config.data.items():
        if name not in ROUND_CONFIG_TASKS:
            continue
        values = deepcopy(values)
        values.pop('Storage', None)
        values.get('Scheduler', {}).pop('NextRun', None)
        result[name] = values
    return result


def _changed_config_paths(previous, current, prefix=()):
    """差异仅定位到配置参数，不展开参数内容或输出前后值。"""
    if isinstance(previous, dict) and isinstance(current, dict) and len(prefix) < 3:
        paths = []
        for key in sorted(previous.keys() | current.keys()):
            path = (*prefix, key)
            if key not in previous or key not in current:
                paths.append('.'.join(path))
            else:
                paths.extend(_changed_config_paths(previous[key], current[key], path))
        return paths
    return ['.'.join(prefix)] if previous != current else []


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


def collect_cn_posts(runners, check_stop):
    """国服按已核实的八行顺序收获，生产任务资格与调度顺序保持独立。"""
    from module.island.assets import POST_MANAGE_PRODUCTION
    from module.island.post_list import CN_POST_ROWS

    order = {f'ISLAND_{category}': index for index, (_, category) in enumerate(
        entry for row in CN_POST_ROWS for entry in row)}
    posts = []
    for runner in runners.values():
        check_stop()
        for post_id in runner.prepare_round_start():
            category, number = post_id.rsplit('_POST', 1)
            posts.append((order[category], int(number), runner, post_id))
    first = next(iter(runners.values()))
    first.goto_postmanage()
    first.post_manage_mode(POST_MANAGE_PRODUCTION)
    first.post_close()
    for _, _, runner, post_id in sorted(posts, key=lambda post: post[:2]):
        check_stop()
        runner.prepare_round_post(post_id)
    for runner in runners.values():
        check_stop()
        runner.prepare_round_finish()


class IslandProductionRound:
    """保留尚未完成的本轮任务，允许委托科研插队后继续，不持久化到磁盘。"""

    def __init__(self, config, device, task):
        invalidate_progress(getattr(config, 'config_name', None), reason='开始新一轮收获盘点')
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
        from module.island.round_warehouse import read_round_warehouse_stocks

        logger.hr('岛屿单轮：统一收获', level=1)
        collect_by_row = getattr(config, 'SERVER', None) == 'cn'
        for name, (module, class_name) in PRODUCTION_TASKS.items():
            self._check_stop(config)
            cls = getattr(import_module(f'module.island.{module}'), class_name)
            runner = cls(config=collection_config(config, name), device=device)
            self.runners[name] = runner
            if not collect_by_row:
                runner.prepare_round()
        if collect_by_row:
            collect_cn_posts(self.runners, lambda: self._check_stop(config))
        for name, runner in self.runners.items():
            enabled = config.data.get(name, {}).get('Scheduler', {}).get('Enable', False)
            if name == task or (enabled and (task_due(config, name, self.started_at) or has_idle_posts(runner))):
                self.pending.add(name)

        self._check_stop(config)
        items = set()
        for name in self.pending:
            items.update(self.runners[name].round_stock_items())
        logger.hr('岛屿单轮：按产地集中盘点', level=1)
        observed = read_round_warehouse_stocks(
            self.runners[task], tuple(self.runners.values()), items)
        for item, quantity in observed.items():
            self.inventory.observe(item, quantity)
        for runner in self.runners.values():
            runner.island_round = self.inventory

        # 已收获成为空岗的启用任务提前进入原优先级队列，不替它执行或写成功。
        with config.multi_set():
            for name in self.pending:
                if name != task and not task_due(config, name, self.started_at):
                    config.cross_set(f'{name}.Scheduler.NextRun', self.started_at)
        self.ready = True
        publish_round_progress(config, self)
        logger.attr('岛屿本轮任务', sorted(self.pending))
        logger.attr('岛屿本轮明确库存', len(observed))

    def valid(self, config):
        return self.invalid_reason(config) is None

    def invalid_reason(self, config, task=None):
        """返回重建原因；配置变化只显示至多八个参数路径，不泄露取值。"""
        if not self.ready:
            return '准备尚未完成'
        if not self.pending:
            return '本轮任务已全部完成'
        current = round_config_signature(config)
        if self.signature != current:
            changed = _changed_config_paths(self.signature, current)
            extra = f'（另 {len(changed) - 8} 项省略）' if len(changed) > 8 else ''
            return f'生产相关配置改变：{", ".join(changed[:8])}{extra}'
        if task is not None and task not in self.pending:
            return '请求任务不在本轮待执行集合中'
        return None

    def matches(self, config, task):
        return self.invalid_reason(config, task) is None

    def run_task(self, config, task):
        runner = self.runners[task]
        runner.config = config
        runner.run()
        self.pending.remove(task)
        publish_round_progress(config, self)


def run_production_task(owner, task, cls):
    """接到单个原调度任务后准备或复用本轮；异常不会遗留库存供恢复使用。"""

    session = getattr(owner, '_island_production_round', None)
    try:
        reason = session.invalid_reason(owner.config, task) if session is not None else None
        if session is None or reason is not None:
            if session is not None:
                logger.info(f'[岛屿单轮] 重建 {task}：{reason}，未完成任务 {len(session.pending)} 个')
            owner._island_production_round = None
            try:
                session = IslandProductionRound(owner.config, owner.device, task)
            except (GameStuckError, GameTooManyClickError, GamePageUnknownError):
                delay_failed_preparation(owner, task)
                raise
            owner._island_production_round = session
        else:
            logger.info(f'[岛屿单轮] 继续 {task}，复用本轮库存，剩余任务 {len(session.pending)} 个')
        try:
            session.run_task(owner.config, task)
        except IslandFishExchangeError:
            # 兑换结果可能不确定，先丢弃共享账；冷却后必须重新收获和盘点。
            owner._island_production_round = None
            delay_failed_preparation(owner, task, reason='鱼肉兑换未确认，已丢弃本轮库存')
            raise
    except BaseException as exc:
        invalidate_progress(getattr(owner.config, 'config_name', None), reason='生产轮执行中断')
        if getattr(owner, '_island_production_round', None) is not None:
            logger.info(f'[岛屿单轮] 丢弃本轮：{task} 执行中断（{type(exc).__name__}）')
        owner._island_production_round = None
        raise
    if not session.pending:
        owner._island_production_round = None
        logger.info('[岛屿单轮] 本轮生产任务已结束，丢弃临时库存')
