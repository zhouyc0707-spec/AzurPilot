"""普通排程任务的单次执行入口，复用实例互斥与工作进程身份校验。"""

from module.api.protocol import ApiError
from module.runtime.process_manager import ProcessManager

TASK_PREFIX = 'task:'


def scheduled_tasks(template: dict) -> set[str]:
    """仅接受模板声明的普通排程任务，不能把请求当作任意函数名执行。"""
    return {
        name for name, groups in template.items()
        if isinstance(groups, dict)
        and isinstance(groups.get('Scheduler'), dict)
        and groups['Scheduler'].get('Command') == name
    }


def is_run_once_allowed(data: dict, template: dict, task: str) -> bool:
    """已启用的排程任务可以提前单次执行，保留其原有参数和排程。"""
    if task not in scheduled_tasks(template):
        return False
    groups = data.get(task)
    if not isinstance(groups, dict) or not isinstance(groups.get('Scheduler'), dict):
        return False
    scheduler = groups['Scheduler']
    return bool(scheduler.get('Enable')) and scheduler.get('Command') == task


def single_task_name(manager) -> str | None:
    """从启动模式获取目标任务，包含 worker 尚未发出首个任务事件的阶段。"""
    func = getattr(manager, 'started_func', None)
    if isinstance(func, str) and func.startswith(TASK_PREFIX):
        return func[len(TASK_PREFIX):]
    return None


def is_single_task(manager) -> bool:
    """单次任务不会被记忆或服务重启恢复为调度器，包括刚自然结束的 worker。"""
    return single_task_name(manager) is not None


def single_task_state(manager, *, alive: bool | None = None) -> dict | None:
    """只返回当前存活单次任务的名称与轮次，停止请求必须携带同一轮次。"""
    name = single_task_name(manager)
    if name and (manager.alive if alive is None else alive):
        return {'name': name, 'runId': manager.run_id}
    return None


def run_once(instance: str, task: str, *, configs=None) -> ProcessManager:
    """启动指定排程任务一遍；同一实例已有任何 worker 时拒绝启动。"""
    if configs is None:
        from module.api.config_service import ConfigService
        configs = ConfigService()
    configs.path(instance)
    with ProcessManager._get_lifecycle_lock(instance):
        manager = ProcessManager.get_manager(instance)
        if manager.alive:
            raise ApiError('INSTANCE_RUNNING', '实例已在运行，请先停止当前任务')
        data, _ = configs.read(instance)
        if not is_run_once_allowed(data, configs.template, task):
            raise ApiError('INVALID_PARAMS', '只能单次执行已启用的普通排程任务')
        from module.runtime.updater import updater
        manager.start(f'{TASK_PREFIX}{task}', ev=updater.event)
        if not manager.alive:
            raise ApiError('START_FAILED', '任务未启动，请检查服务是否正在重启')
        return manager


def stop_once(instance: str, task: str, run_id: str, *, configs=None) -> ProcessManager:
    """立即停止本轮单次任务，不触发调度器停止后的游戏或模拟器收尾动作。"""
    if configs is None:
        from module.api.config_service import ConfigService
        configs = ConfigService()
    configs.path(instance)
    with ProcessManager._get_lifecycle_lock(instance):
        manager = ProcessManager.get_manager(instance)
        if manager.run_id != run_id or single_task_name(manager) != task:
            raise ApiError('TASK_RUN_CHANGED', '任务运行轮次已变化，请刷新后重试')
        if not manager.stop():
            raise ApiError('STOP_FAILED', '尚未确认全部工作进程停止，请检查日志后重试')
        return manager


def execute_once(instance: str, task: str, stop_event=None):
    """Worker 内绑定目标任务，执行一遍后退出，不启动调度或故障重试循环。"""
    import inflection

    from alas import AzurLaneAutoScript
    from module.api.config_service import ConfigService
    from module.config.config import AzurLaneConfig

    config = AzurLaneConfig(config_name=instance, task=task)
    if not is_run_once_allowed(config.data, ConfigService().template, task):
        raise ApiError('INVALID_PARAMS', '目标任务已关闭或不属于普通排程任务')
    config.begin_single_run()
    script = AzurLaneAutoScript(config_name=instance)
    script.config = config
    result = False
    try:
        result = script.run(inflection.underscore(task))
        return result
    finally:
        succeeded = result is True and not (stop_event is not None and stop_event.is_set())
        config.finish_single_run(succeeded)
