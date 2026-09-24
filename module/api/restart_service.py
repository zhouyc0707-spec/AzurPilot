"""手动重启 WebUI 服务（新前端「重启服务」按钮的后端实现）。

与「更新」触发的重启走同一条链路：

1. :func:`module.api.lifecycle.clearup` 停止任务线程、OCR、远程访问与运行中的实例，
   并把「退出那一刻仍在运行的实例」记进启动记忆（只记启用了「启动时记忆运行」的）；
2. 置位 ``State.restart_event``，父监督进程（``gui.py``）据此终止并重新拉起 WebUI
   子进程，新进程启动时按启动记忆恢复实例。

与自动更新事务通过 ``State.restart_lock`` 互斥：更新进行中时拒绝手动重启，
避免把更新事务打断成半成品。旧界面的同类按钮在
``module/webui/app_developer_tools.py``（开发工具页的「重启Alas」）。
"""
from module.api.protocol import ApiError
from module.logger import logger
from module.runtime.setting import State


def request_restart() -> dict:
    """请求父进程重启 WebUI。

    Returns:
        dict: ``{'restarting': True}``；进程随后会被父监督进程重新拉起。

    Raises:
        ApiError: 当前没有父监督进程、更新事务正在进行，或无法通知父进程时。
    """
    if State.restart_event is None:
        raise ApiError('UNAVAILABLE', '当前 WebUI 没有父监督进程（不是通过 gui.py 启动的），无法自动重启，请手动重启。')

    # 更新事务持锁期间不打断它：与 module/runtime/updater.py 的加锁方式一致。
    if not State.restart_lock.acquire(blocking=False):
        raise ApiError('CONFLICT', '自动更新正在进行，已取消本次重启。')

    try:
        if State._restart_requested:
            return {'restarting': True}

        from module.api.lifecycle import clearup

        State._restart_requested = True
        try:
            if not clearup():
                # 清理没做完也让父进程终止整棵进程树，与旧界面一致。
                logger.warning('WebUI 清理未完成，将由父进程终止完整进程树')
        except Exception as exc:
            logger.exception_context(
                title='WebUI 手动重启清理失败',
                exc=exc,
                impact='父进程仍会终止旧 WebUI 进程树。',
                action='检查 WebUI 清理日志，确认是否有残留资源。',
                level=50,
            )

        try:
            State.restart_event.set()
        except Exception as exc:
            State._restart_requested = False
            raise ApiError('INTERNAL', '无法通知父进程执行重启，请手动重启。') from exc

        logger.info('[GUI] 收到手动重启请求，正在通知父进程重启 WebUI')
        return {'restarting': True}
    finally:
        State.restart_lock.release()
