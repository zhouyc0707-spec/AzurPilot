"""启动器（alas-launcher）在本机使用的控制、通知与免密端点。

上游把启动器协议实现在旧界面（本地保留的 PyWebIO 版 ``module/webui/api.py``）
里，新前端（React + ``module/api``）没有这些端点 —— 启动器拉起新前端时，
``/api/launcher/stream`` 与 ``/api/notify_stream`` 会落到前端静态资源的 SPA 兜底
（返回 ``index.html`` 且 200），启动器的控制流与通知流因此断掉。本模块把同一套
协议搬到新前端的应用工厂：

- ``POST /api/notify`` / ``GET /api/notify_stream``：应用内通知进队列、启动器订阅；
- ``GET /api/launcher/status`` / ``POST /api/launcher/startup``：连接与开机自启；
- ``GET /api/launcher/stream`` / ``POST /api/launcher/report``：本地命令通道；
- ``POST /api/launcher/trusted-login`` / ``GET /launcher-login``：信任密钥换免密令牌，
  再用种子页把密码写进前端 localStorage（React 前端存 ``azurpilot.access-password``；
  回环连接本就不需要密码，这一步只是让启动器的既有流程不至于拿到 404）。

命令通道与信任逻辑两端共用 :mod:`module.runtime.launcher` 与
:mod:`module.runtime.launcher_trust`，因此旧界面与新前端不会出现两份状态。
"""
import asyncio
import json
import time

from starlette.responses import HTMLResponse, JSONResponse, StreamingResponse
from starlette.routing import Route

from module.logger import logger
from module.runtime.launcher import is_local_request, launcher_control
from module.runtime.launcher_trust import (
    TOKEN_TTL_SECONDS,
    check_secret,
    enabled as launcher_trust_enabled,
    issue_token,
    validate_token,
    webui_key,
)

# React 前端保存登录密码的 localStorage 键（见 frontend/src/api/client.ts）
REACT_PASSWORD_KEY = "azurpilot.access-password"
# 旧界面用的键，一起写入以便同源页面在两种界面下都能免密进入
LEGACY_PASSWORD_KEY = "password"

#: 应用内通知队列：`module/notify` 通过 POST /api/notify 投递，启动器订阅 SSE 消费。
#: 队列满或无人订阅时只是丢掉一条通知，不能阻塞调用方。
_notification_queue: asyncio.Queue = asyncio.Queue(maxsize=100)

# 免密令牌只对回环开放，但仍对「试密钥」做简单限频，避免本机其它进程暴力探测。
_LAUNCHER_SECRET_FAILURES: list[float] = []
_LAUNCHER_SECRET_WINDOW = 300
_LAUNCHER_SECRET_MAX_FAILURES = 15


def _launcher_secret_try_allowed() -> bool:
    now = time.time()
    while _LAUNCHER_SECRET_FAILURES and now - _LAUNCHER_SECRET_FAILURES[0] > _LAUNCHER_SECRET_WINDOW:
        _LAUNCHER_SECRET_FAILURES.pop(0)
    return len(_LAUNCHER_SECRET_FAILURES) < _LAUNCHER_SECRET_MAX_FAILURES


def _launcher_secret_failure() -> None:
    _LAUNCHER_SECRET_FAILURES.append(time.time())


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


async def api_launcher_status(request):
    """GET /api/launcher/status — 启动器连接与开机自启动状态。"""
    return JSONResponse(launcher_control.status(request_local=is_local_request(request)))


async def api_launcher_startup(request):
    """POST /api/launcher/startup — 请求启动器设置 Windows 开机自启动。"""
    if not is_local_request(request):
        return JSONResponse({'success': False, 'error': '开机自启动只能从本机 WebUI 设置'}, status_code=403)

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({'success': False, 'error': '请求体不是有效 JSON'}, status_code=400)

    if 'enabled' not in data:
        return JSONResponse({'success': False, 'error': '缺少 enabled 字段'}, status_code=400)
    if not isinstance(data.get('enabled'), bool):
        return JSONResponse({'success': False, 'error': 'enabled 必须是布尔值'}, status_code=400)

    result = await launcher_control.set_autostart(data['enabled'])
    return JSONResponse(result, status_code=200 if result.get('success') else 500)


async def api_launcher_stream(request):
    """GET /api/launcher/stream — 启动器订阅的本地命令流（SSE）。"""
    if not is_local_request(request):
        return JSONResponse({'success': False, 'error': '启动器命令流只允许本机连接'}, status_code=403)

    async def event_generator():
        await launcher_control.mark_connected()
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    command = await asyncio.wait_for(launcher_control.next_command(), timeout=30)
                    launcher_control.keep_alive()
                    yield launcher_control.event(command)
                except asyncio.TimeoutError:
                    launcher_control.keep_alive()
                    yield launcher_control.keepalive_event()
        finally:
            launcher_control.mark_disconnected()

    return StreamingResponse(
        event_generator(),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


async def api_launcher_report(request):
    """POST /api/launcher/report — 启动器回报命令执行结果。"""
    if not is_local_request(request):
        return JSONResponse({'success': False, 'error': '启动器回报只允许本机连接'}, status_code=403)

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({'success': False, 'error': '请求体不是有效 JSON'}, status_code=400)

    return JSONResponse(await launcher_control.report(data))


async def api_launcher_trusted_login(request):
    """POST /api/launcher/trusted-login — 启动器以信任密钥换取一次性免密令牌。"""
    if not is_local_request(request):
        return JSONResponse({'success': False, 'error': '免密通道只允许本机连接'}, status_code=403)
    if not launcher_trust_enabled():
        # 未由启动器（带信任密钥）拉起，或没配置 WebUI 密码，免密通道整体关闭。
        return JSONResponse({'success': False, 'error': '启动器免密通道未启用'}, status_code=403)
    if not _launcher_secret_try_allowed():
        return JSONResponse({'success': False, 'error': '尝试次数过多，请稍后再试'}, status_code=429)

    if not check_secret(request.headers.get('x-webui-launcher-secret')):
        _launcher_secret_failure()
        return JSONResponse({'success': False, 'error': '信任密钥无效'}, status_code=403)

    token = issue_token()
    if token is None:
        return JSONResponse({'success': False, 'error': '免密令牌签发失败'}, status_code=403)
    return JSONResponse({'success': True, 'token': token, 'ttl': TOKEN_TTL_SECONDS})


async def launcher_login(request):
    """GET /launcher-login?token= — 校验令牌后预置本机窗口的登录状态。

    返回与前端同源的种子页：把当前有效密码写进 localStorage 再跳回首页。
    新前端的回环连接本就免密（见 tests/test_api.py 的本机免密用例），这一步主要是
    让启动器既有的「取令牌 → 打开种子页」流程不至于落空。
    """
    if not is_local_request(request):
        return _launcher_login_denied()
    if not validate_token(request.query_params.get('token')):
        return _launcher_login_denied()

    key = webui_key()
    if not key:
        # 没有密码时本就不需要登录，直接回首页。
        return HTMLResponse('<!doctype html><script>location.replace("/");</script>', status_code=200)

    # json.dumps 生成合法 JS 字符串字面量，安全内嵌任意密码。
    script = (
        'localStorage.setItem(' + json.dumps(REACT_PASSWORD_KEY) + ', ' + json.dumps(str(key)) + ');'
        'localStorage.setItem(' + json.dumps(LEGACY_PASSWORD_KEY) + ', ' + json.dumps(str(key)) + ');'
        'location.replace("/");'
    )
    html = (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<title>AzurPilot 免密登录</title>'
        f'<script>{script}</script>'
        '<body>正在免密登录，请稍候…</body></html>'
    )
    return HTMLResponse(html, status_code=200)


def _launcher_login_denied():
    return HTMLResponse(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<title>拒绝访问</title><body>该链接无效或已过期，请从启动器重新打开。</body></html>',
        status_code=403,
    )


#: 交给应用工厂挂在静态资源之前的路由（顺序很重要：SPA 兜底会接住所有未匹配路径）。
routes = [
    Route('/api/notify', api_notify, methods=['POST']),
    Route('/api/notify_stream', api_notify_stream),
    Route('/api/launcher/status', api_launcher_status),
    Route('/api/launcher/startup', api_launcher_startup, methods=['POST']),
    Route('/api/launcher/stream', api_launcher_stream),
    Route('/api/launcher/report', api_launcher_report, methods=['POST']),
    Route('/api/launcher/trusted-login', api_launcher_trusted_login, methods=['POST']),
    Route('/launcher-login', launcher_login, methods=['GET']),
]
