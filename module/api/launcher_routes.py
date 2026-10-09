"""组装启动器本地通道与本地保留的通知通道。

启动器控制、状态、报告与可信登录复用 :mod:`module.api.launcher_api` 的同一套
处理器和限流状态；本模块保留 ``POST /api/notify`` 与 ``GET /api/notify_stream``，
供通知模块投递、外部启动器订阅。应用工厂只注册这里的路由集合，并将其放在 SPA
静态资源兜底之前，避免启动器拿到 ``index.html`` 而非 JSON 或 SSE。
"""
import asyncio
import json

from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from module.api import launcher_api
from module.logger import logger

# 保留既有 Python 导入入口；实际处理逻辑与上游启动器接口共用。
api_launcher_status = launcher_api.status
api_launcher_startup = launcher_api.startup
api_launcher_stream = launcher_api.stream
api_launcher_report = launcher_api.report
api_launcher_trusted_login = launcher_api.trusted_login
launcher_login = launcher_api.login_seed
REACT_PASSWORD_KEY = launcher_api.REACT_PASSWORD_KEY
LEGACY_PASSWORD_KEY = launcher_api.LEGACY_PASSWORD_KEY

# 旧模块的内部入口也指向共用状态，兼容本地调用而不形成第二套限流。
_LAUNCHER_SECRET_FAILURES = launcher_api._secret_failures
_LAUNCHER_SECRET_WINDOW = launcher_api.SECRET_FAILURE_WINDOW
_LAUNCHER_SECRET_MAX_FAILURES = launcher_api.SECRET_FAILURE_LIMIT


def _launcher_secret_try_allowed():
    return launcher_api._secret_try_allowed()


def _launcher_secret_failure():
    launcher_api._secret_failures.append(launcher_api.time.time())


def _launcher_login_denied():
    return launcher_api._login_denied()

#: 通知队列满时丢弃当前通知，不阻塞调用方。
_notification_queue: asyncio.Queue = asyncio.Queue(maxsize=100)


async def api_notify(request):
    """POST /api/notify — 接收通知并转交给订阅了 SSE 的启动器。"""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({'success': False, 'error': '请求体不是有效 JSON'}, status_code=400)
    try:
        _notification_queue.put_nowait(data)
    except asyncio.QueueFull:
        logger.warning('[Launcher] 通知队列已满，丢弃一条通知')
    return JSONResponse({'success': True})


async def api_notify_stream(request):
    """GET /api/notify_stream — 启动器订阅的通知流（SSE）。"""
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            try:
                payload = await asyncio.wait_for(_notification_queue.get(), timeout=30)
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"

    return StreamingResponse(
        event_generator(),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


#: 应用工厂仅注册一次：通知扩展与上游启动器端点均在静态兜底之前。
routes = [
    Route('/api/notify', api_notify, methods=['POST']),
    Route('/api/notify_stream', api_notify_stream),
    *launcher_api.routes(),
]
