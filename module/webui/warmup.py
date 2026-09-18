"""WebUI worker 启动后的后台预热。

**为什么需要**：`alas` 实例 worker 是新起的进程，第一次进总览页时才惰性导入
统计相关模块（`cv2` / `numpy` / `module.statistics.*`）、首次打开
`azurstats_local.db`(38 MB) 与 `cl1_data.db`(17 MB)、并首次读配置。这三件事叠加
在第一次渲染路径上，而 worker 之后就常驻了 —— 表现就是「刚重启后第一次进总览页
慢，之后再进就正常」。

这里在 worker 启动后立刻起一条后台线程把它们先跑一遍，与首屏渲染并行，把冷启动
开销藏到用户还没点到总览页的空档里。

设计约束（都不可省）：

- **只预热、不改写**：数据库一律以 `mode=ro` 只读打开，绝不建表、不迁移、不写盘。
- **失败静默**：任何异常都只记一条 warning，预热失败只是没有优化，绝不能影响启动。
- **每进程一次**：用一个锁 + 标记位保证只跑一次；预热本身实测约 300 ms
  （其中首次 `load_config` 占大头），但绝不能每会话重复跑。
"""

import threading

from module.logger import logger

_lock = threading.Lock()
_started = False


def _warm_modules() -> None:
    """导入首次进总览页才会用到的重模块。

    这些导入在其所属渲染函数里是函数内的惰性导入，所以只有真正渲染过才会加载；
    这里提前触发，让 `sys.modules` 与 .pyc 缓存准备好。
    """
    # cv2 / numpy 由 module.statistics.* 间接引入，是最大的一块
    import module.statistics.azurstats  # noqa: F401
    import module.statistics.cl1_database  # noqa: F401
    import module.statistics.commission_income_stats  # noqa: F401
    import module.statistics.opsi_month  # noqa: F401
    import module.statistics.ship_exp_stats  # noqa: F401
    import module.config.config  # noqa: F401
    import module.log_res.log_res  # noqa: F401


def _warm_databases() -> None:
    """只读打开两个统计库，触发建连接与冷页读取。

    刻意不实例化 `Cl1Database`：它的 `__init__` 会 `_init_db()` 建表并在必要时
    迁移，属于写操作。这里只做一次最小只读查询 —— 预热的目标是文件句柄与页缓存。
    """
    import sqlite3
    from contextlib import closing
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[2]
    for name in ("cl1_data.db", "azurstats_local.db"):
        path = project_root / "config" / name
        if not path.exists():
            continue
        uri = f"file:{path.as_posix()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as conn:
            conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()


def _warm_config() -> None:
    """读一次实例配置并构建一次 LogRes，预热配置解析与仪表盘数据源。

    刻意走 `load_config()`（`ui_alas` 用的就是这个入口）而不是直接构造
    `AzurLaneConfig`，保证预热的就是首次切实例时真正要付的那一次开销 ——
    实测首次 `load_config('alas')` 约 250 ms，第二次只要 10 ms 量级。
    """
    from module.config.utils import alas_instance
    from module.log_res.log_res import LogRes
    from module.submodule.submodule import load_config

    instances = alas_instance()
    if not instances:
        return
    config = load_config(instances[0])
    config.read_file(instances[0])
    LogRes(config)


def _warm_device_id() -> None:
    """触发设备ID 初始化。

    已有缓存文件时它是读一次 JSON 就走（指纹核对在它自己的后台线程里），但全新
    安装时这一步仍要跑 4 次 wmic —— 放在这里就不会卡在首屏渲染上。
    """
    from module.base.device_id import get_device_id

    get_device_id()


def _run() -> None:
    steps = (
        ("设备ID", _warm_device_id),
        ("重模块导入", _warm_modules),
        ("统计数据库", _warm_databases),
        ("实例配置", _warm_config),
    )
    for name, step in steps:
        try:
            step()
        except Exception as exc:  # noqa: BLE001 - 预热失败不影响启动
            logger.warning(f"[WebUI-预热] {name} 预热失败（已跳过）: {exc}")
    logger.debug("[WebUI-预热] 后台预热完成")


def start_warmup() -> bool:
    """启动一次后台预热，返回本次调用是否真正启动了线程。

    幂等：同一进程内重复调用只有第一次生效。
    """
    global _started
    with _lock:
        if _started:
            return False
        _started = True

    threading.Thread(target=_run, daemon=True, name="webui-warmup").start()
    return True
