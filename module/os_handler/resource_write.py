"""冻结大世界资源观察后串行落库，仅事务合并仪表盘字段。"""

from contextvars import copy_context
from copy import deepcopy
from concurrent.futures import Future
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from module.base.async_executor import async_executor
from module.config.deep import deep_get, deep_set
from module.config.transaction import config_transaction
from module.config.utils import filepath_config, read_file, write_file
from module.logger import logger


def _record_time(value):
    """兼容运行器内的日期对象及 JSON 中的 ISO 时间；损坏旧时间不阻止新观察。"""
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        return stamp.timestamp()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def resource_observation_is_current(config, name, observed_at):
    """逐资源拒绝迟到截图，包含主线程尚未保存的新观察。"""
    stamp = _record_time(observed_at)
    if stamp is None:
        return False
    key = f'Dashboard.{name}.Record'
    records = (deep_get(getattr(config, 'data', {}), key), getattr(config, 'modified', {}).get(key))
    return all(previous is None or previous <= stamp for previous in map(_record_time, records))


def _persist_resources(snapshot, values, observed_at):
    from module.log_res.log_res import LogRes

    try:
        recorder = LogRes(snapshot)
        for name, value in values.items():
            recorder.record(name, value, defer_history=True, observed_at=observed_at)
        path = Path(filepath_config(snapshot.config_name))
        if not path.is_file():
            return
        with config_transaction(path):
            current = read_file(str(path))
            disk_config = SimpleNamespace(data=current, modified={})
            for name, value in values.items():
                prefix = f'Dashboard.{name}'
                if not resource_observation_is_current(disk_config, name, observed_at):
                    continue
                fields = value if isinstance(value, dict) else {'Value': value}
                for field, number in fields.items():
                    deep_set(current, prefix + '.' + field, number)
                deep_set(current, prefix + '.Record', observed_at)
            write_file(str(path), current)
    except Exception:
        logger.exception('[大世界-资源后台] 资源观察或仪表盘保存失败')
        raise


def record_resources_background(config, values, *, observed_at=None):
    """后台使用独立快照；运行中配置、任务绑定及待提交设置只由主线程操作。"""
    observed_at = observed_at or datetime.now()
    values = {name: deepcopy(value) for name, value in values.items()
              if resource_observation_is_current(config, name, observed_at)}
    if not values:
        finished = Future()
        finished.set_result(None)
        return finished
    dashboard = deepcopy(deep_get(config.data, 'Dashboard', default={}))
    # 内存值立即可供本轮预算使用，后台不调用 live config.save()/update()。
    for name, value in values.items():
        fields = value if isinstance(value, dict) else {'Value': value}
        for field, number in fields.items():
            deep_set(config.data, f'Dashboard.{name}.{field}', number)
        deep_set(config.data, f'Dashboard.{name}.Record', observed_at)
    snapshot = SimpleNamespace(
        config_name=getattr(config, 'config_name', 'default'),
        task=deepcopy(getattr(config, 'task', None)),
        _bind_task_override=getattr(config, '_bind_task_override', None),
        data={'Dashboard': dashboard}, modified={},
    )
    context = copy_context()
    return async_executor.submit(context.run, _persist_resources, snapshot, values, observed_at)
