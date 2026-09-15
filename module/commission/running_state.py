"""运行中委托的状态文件（ALAS worker 写、WebUI 读）。

委托的运行状态只存在于 ALAS worker 进程的内存里（`Commission.finish_time` 由
`create_time + duration` 得出），WebUI 是另一个进程，拿不到这份数据。这里用
一个小的 JSON 状态文件把两边接起来：

- worker 侧：`module/commission/commission.py` 在每轮扫描并启动委托之后调用
  :func:`write_running_commissions`；
- WebUI 侧：`module/webui/app_stat_commission.py` 读 :func:`read_running_state`
  渲染「正在进行」区块。

完成时间统一存 **epoch 秒**而不是格式化字符串：显示时由前端按本地时区渲染，
避免把 worker 的时区写死进文件。
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

# 状态文件目录与文件名模板
STATE_DIR = "./config"
STATE_FILE_TEMPLATE = "commission_running_{instance}.json"


def state_file(instance: str) -> str:
    """状态文件路径。

    Args:
        instance: 配置实例名。

    Returns:
        str: 状态文件的相对路径。
    """
    return os.path.join(STATE_DIR, STATE_FILE_TEMPLATE.format(instance=instance))


def load_previous_finish(instance: str) -> Dict[str, float]:
    """读取上一次写入的各委托完成时间，用于让显示保持稳定。

    「预计完成时刻」按扫描时刻加剩余时间算出来，每次扫描都会差几十秒。
    如果每次都直接覆盖，页面上这个时刻会一直小幅跳动，看着像是算错了。
    因此对仍在运行的委托沿用上一轮的完成时间。

    Args:
        instance: 配置实例名。

    Returns:
        dict: ``{委托名: 完成时间戳}``，读不到时返回空字典。
    """
    try:
        with open(state_file(instance), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    result = {}
    for entry in data.get("running") or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        finish = entry.get("finish")
        if isinstance(name, str) and isinstance(finish, (int, float)):
            result[name] = float(finish)
    return result


def write_running_commissions(instance: str, entries: List[Dict[str, Any]]) -> None:
    """写入当前运行中的委托列表。

    Args:
        instance: 配置实例名。
        entries: ``[{'name': str, 'finish': float}, ...]``，``finish`` 为
            epoch 秒。
    """
    payload = {
        "updated_at": datetime.now().timestamp(),
        "running": entries,
    }
    path = state_file(instance)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # 先写临时文件再替换：WebUI 可能正好在这时读，避免读到写了一半的内容
        temp_path = f"{path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(temp_path, path)
    except OSError:
        # 状态文件写不进去不该影响委托任务本身
        pass


@dataclass
class RunningState:
    """WebUI 侧读到的运行中委托状态。

    Attributes:
        available: 状态文件是否存在且可解析。False 表示「尚未获取到委托状态」
            （worker 还没跑过委托任务），与「扫描过但确实没有运行中委托」
            （available=True 且 ``commissions`` 为空）是两回事。
        updated_at: 状态写入时间（epoch 秒），0 表示未知。
        commissions: ``[{'name': str, 'finish': float}, ...]``，已按完成时间升序。
    """

    available: bool = False
    updated_at: float = 0.0
    commissions: Optional[List[Dict[str, Any]]] = None


def read_running_state(instance: str) -> RunningState:
    """读取运行中委托状态，并过滤掉预计已完成（或刚结束）的条目。

    Args:
        instance: 配置实例名。

    Returns:
        RunningState: 见该类说明。
    """
    try:
        with open(state_file(instance), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return RunningState(available=False, updated_at=0.0, commissions=[])

    now = datetime.now().timestamp()
    entries = []
    for entry in data.get("running") or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        finish = entry.get("finish")
        if not isinstance(name, str) or not isinstance(finish, (int, float)):
            continue
        # 过期的条目直接丢掉：worker 可能已经停止运行（游戏离线、任务暂停），
        # 文件不会更新，但时间在走，完成时刻一过就该从列表里消失
        if float(finish) <= now:
            continue
        entries.append({"name": name, "finish": float(finish)})

    entries.sort(key=lambda item: item["finish"])
    updated_at = data.get("updated_at")
    return RunningState(
        available=True,
        updated_at=float(updated_at) if isinstance(updated_at, (int, float)) else 0.0,
        commissions=entries,
    )
