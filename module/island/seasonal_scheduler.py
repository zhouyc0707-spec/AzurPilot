"""季节制造目标的临时调度资格，不修改用户保存的制造业开关。"""

from types import SimpleNamespace

from module.config.deep import deep_get
from module.island.seasonal_targets import seasonal_targets
from module.logger import logger


MANUFACTURE_CATEGORIES = ('wood_processing', 'industrial_production', 'electronic_processing', 'handmade')


def seasonal_manufacture_enabled(data, instance=None):
    """当季正数制造目标可补产；一键暂停和损坏目标均不能触发隐式派遣。"""
    if not isinstance(data.get('IslandManufacture', {}).get('Scheduler'), dict):
        return False
    if instance:
        from module.api.island_suspend import read_state

        if read_state(instance)['suspended']:
            return False
    config = SimpleNamespace(cross_get=lambda path, default=None: deep_get(data, path, default))
    try:
        # 先完整读取，不能因前一条产线有目标而跳过后续非法导入目标。
        targets = [seasonal_targets(config, category) for category in MANUFACTURE_CATEGORIES]
    except (ValueError, TypeError) as error:
        logger.warning(f'[岛屿-季节制造] 目标配置无效，暂不加入补产调度：{error}')
        return False
    return any(targets)


def effective_task_enabled(data, task, instance=None):
    """普通任务仍按保存开关；仅制造业允许当季有限目标的临时资格。"""
    return bool(deep_get(data, f'{task}.Scheduler.Enable', False)) or (
        task == 'IslandManufacture' and seasonal_manufacture_enabled(data, instance))
