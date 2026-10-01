"""岛屿计划的「一键暂停 / 恢复」服务（本地定制）。

岛屿组下有 17 个任务（`IslandPlan` … `IslandPearlSell`），想临时全部停下时逐个点开关太麻烦。
这里提供一次调用关闭**当前所有已启用**的岛屿任务，并记下是哪几个被这次操作关掉的；
下次再点一下即可恢复。

- 恢复只处理**仍处于关闭状态**的那些：中途被手动打开过的任务保持不动，尊重手动意图；
- 被关闭的任务只改 `Scheduler.Enable`，调度时间（`NextRun`）等其余配置一律不动，
  恢复后按原计划继续；
- 记录写在实例配置同目录的 `island_suspend_<实例>.json`（`config/*.json` 已被 gitignore），
  文件丢失时降级为「没有暂停记录」，不会误开用户自己关掉的任务。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from module.api.protocol import ConfigChange
from module.config.utils import filepath_config
from module.logger import logger

# 岛屿组下的任务都以 Island 开头（见 module/config/argument/task.yaml 的 Island 组），
# 按前缀识别可随上游新增岛屿任务自动覆盖，不必维护固定名单。
ISLAND_PREFIX = 'Island'
STATE_FILE = 'island_suspend_{instance}.json'


def _state_path(instance: str) -> Path:
    """暂停记录文件路径：与实例配置同目录，按实例分开。"""
    return Path(filepath_config(instance)).parent / STATE_FILE.format(instance=instance)


def read_state(instance: str) -> dict:
    """读取暂停记录。

    Args:
        instance (str): 实例名。

    Returns:
        dict: {'suspended': [任务名…], 'at': 记录时间}；无记录或文件损坏时为空记录。
    """
    path = _state_path(instance)
    if not path.is_file():
        return {'suspended': [], 'at': ''}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        logger.warning(f'[岛屿计划] 暂停记录读取失败，按无记录处理: {path}')
        return {'suspended': [], 'at': ''}
    suspended = [item for item in data.get('suspended', []) if isinstance(item, str)]
    return {'suspended': suspended, 'at': str(data.get('at', ''))}


def write_state(instance: str, suspended: list, at: str) -> None:
    """写入暂停记录；列表为空时删除文件（表示没有暂停中的任务）。"""
    path = _state_path(instance)
    try:
        if not suspended:
            path.unlink(missing_ok=True)
            return
        path.write_text(
            json.dumps({'suspended': suspended, 'at': at}, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
    except OSError as error:
        logger.warning(f'[岛屿计划] 暂停记录写入失败: {error}')


class IslandSuspendService:
    """供 WebUI 使用的岛屿计划批量开关。"""

    def __init__(self, configs) -> None:
        """
        Args:
            configs: 配置服务（ConfigService），用于读取与批量修改实例配置。
        """
        self.configs = configs

    @staticmethod
    def _island_tasks(data: dict) -> list:
        """配置里可调度的岛屿任务名（升序）。

        只收有 `Scheduler` 组的任务：`IslandPlan` 是「全局配置」模块（当前只有季节一项），
        本身没有 Scheduler，不参与启用/关闭。
        """
        return sorted(
            task for task, groups in data.items()
            if task.startswith(ISLAND_PREFIX) and isinstance(groups, dict) and 'Scheduler' in groups
        )

    @staticmethod
    def _enabled(data: dict, task: str) -> bool:
        """任务的 Scheduler.Enable 是否为真。"""
        return bool(data.get(task, {}).get('Scheduler', {}).get('Enable'))

    def _snapshot(self, instance: str) -> dict:
        """一次读取配置与记录，供状态查询与两种操作共用。"""
        data, _ = self.configs.read(instance)
        tasks = self._island_tasks(data)
        state = read_state(instance)
        return {
            'data': data,
            'tasks': tasks,
            'enabled': [task for task in tasks if self._enabled(data, task)],
            # 记录里已被删除的任务（例如上游改名）不再参与恢复，避免误开同名新任务
            'suspended': [task for task in state['suspended'] if task in tasks],
            'at': state['at'],
        }

    def state(self, instance: str) -> dict:
        """界面状态：当前启用数量、被本功能关掉的任务与其数量。

        Args:
            instance (str): 实例名。

        Returns:
            dict: {'total', 'enabledCount', 'suspended', 'suspendedCount', 'at'}。
        """
        snapshot = self._snapshot(instance)
        return {
            'total': len(snapshot['tasks']),
            'enabledCount': len(snapshot['enabled']),
            'suspended': snapshot['suspended'],
            'suspendedCount': len(snapshot['suspended']),
            'at': snapshot['at'],
        }

    def toggle(self, instance: str) -> dict:
        """有暂停记录就恢复，否则关闭全部已启用的岛屿任务。

        Args:
            instance (str): 实例名。

        Returns:
            dict: 操作后的最新状态（同 :meth:`state`）。
        """
        snapshot = self._snapshot(instance)
        if snapshot['suspended']:
            return self._restore(instance, snapshot)
        return self._suspend(instance, snapshot)

    def _suspend(self, instance: str, snapshot: dict) -> dict:
        """关闭当前所有已启用的岛屿任务，并记录被关掉的任务。"""
        targets = list(snapshot['enabled'])
        if targets:
            self.configs.patch(instance, None, [
                ConfigChange(path=f'{task}.Scheduler.Enable', value=False) for task in targets
            ])
            logger.info(f'[岛屿计划] 一键关闭 {len(targets)} 个已启用任务: {", ".join(targets)}')
        else:
            logger.info('[岛屿计划] 当前没有处于启用状态的岛屿任务，无需关闭')
        write_state(instance, targets, datetime.now().isoformat(sep=' ', timespec='seconds'))
        return self.state(instance)

    def _restore(self, instance: str, snapshot: dict) -> dict:
        """恢复仍处于关闭状态的记录任务；手动打开过的保持不动。"""
        targets = [task for task in snapshot['suspended'] if not self._enabled(snapshot['data'], task)]
        skipped = [task for task in snapshot['suspended'] if task not in targets]
        if targets:
            self.configs.patch(instance, None, [
                ConfigChange(path=f'{task}.Scheduler.Enable', value=True) for task in targets
            ])
            logger.info(f'[岛屿计划] 一键恢复 {len(targets)} 个任务: {", ".join(targets)}')
        if skipped:
            logger.info(f'[岛屿计划] 以下任务已被手动启用，保持不动: {", ".join(skipped)}')
        write_state(instance, [], '')
        return self.state(instance)
