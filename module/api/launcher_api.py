"""启动器独立 WS 入口：回环鉴权、严格方法分发与有界收发。"""
import asyncio
import json
import time
from collections import deque

from pydantic import ValidationError
from starlette.responses import Response, HTMLResponse, JSONResponse, StreamingResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocketDisconnect

from module.api.launcher_protocol import INCOMING_METHODS, LauncherRequest, LauncherResponse
from module.api.protocol import ApiError
from module.runtime.launcher import is_local_request, launcher_control
from module.runtime import launcher_trust
from module.base.runtime_params import TOKEN_TTL_SECONDS
from module.runtime.password_utils import is_demo_mode


class LauncherGateway:
    def __init__(self, service):
        self.service = service
        self.failures = deque(maxlen=15)

    async def endpoint(self, ws):
        if not is_local_request(ws):
            await ws.close(code=1008)
            return
        now = time.monotonic()
        while self.failures and now - self.failures[0] > 300:
            self.failures.popleft()
        authorization = ws.headers.get('authorization', '')
        if (len(self.failures) >= 15 or not authorization.startswith('Bearer ')
                or not self.service.trust.check_secret(authorization[7:])):
            if len(self.failures) < 15:
                self.failures.append(now)
            await ws.close(code=1008)
            return
        await ws.accept()
        connection = await self.service.attach(ws)
        writer = asyncio.create_task(self.writer(ws, connection))
        received = deque(maxlen=128)
        request_times = deque()
        try:
            while self.service.connection is connection:
                frame = await asyncio.wait_for(ws.receive(), timeout=45 if connection.ready else 5)
                if self.service.connection is not connection:
                    break
                if frame['type'] == 'websocket.disconnect':
                    break
                raw = frame.get('text')
                if raw is None:
                    await ws.close(code=1003)
                    break
                if len(raw.encode('utf-8')) > 65536:
                    await ws.close(code=1009)
                    break
                connection.last_seen = time.monotonic()
                request_times.append(connection.last_seen)
                while request_times and connection.last_seen - request_times[0] >= 1:
                    request_times.popleft()
                if len(request_times) > 30:
                    await ws.close(code=1008)
                    break
                request_id = ''
                replay = False
                try:
                    data = json.loads(raw)
                    if isinstance(data, dict) and isinstance(data.get('id'), str):
                        request_id = data['id'][:100]
                    if isinstance(data, dict) and data.get('type') == 'response':
                        if not connection.ready:
                            raise ApiError('UNAUTHORIZED', '请先完成握手')
                        self.service.complete(connection, LauncherResponse.model_validate(data))
                        continue
                    request = LauncherRequest.model_validate(data)
                    if request.id in received:
                        raise ApiError('DUPLICATE_REQUEST', '请求 ID 已使用')
                    received.append(request.id)
                    model = INCOMING_METHODS.get(request.method)
                    if model is None:
                        raise ApiError('METHOD_NOT_FOUND', '未知启动器方法')
                    params = model.model_validate(request.params)
                    if request.method == 'session.hello':
                        result = self.service.hello(connection, params)
                        replay = True
                    elif not connection.ready:
                        raise ApiError('UNAUTHORIZED', '请先完成握手')
                    elif request.method == 'state.report':
                        result = self.service.report_state(params)
                    elif request.method == 'notifications.ack':
                        result = self.service.acknowledge(params.ids)
                    elif request.method == 'login.issue':
                        result = self.service.trust.issue()
                    else:
                        result = {'pong': True}
                    message = {'type': 'response', 'id': request.id, 'ok': True, 'result': result}
                except (ValidationError, ValueError, TypeError):
                    message = self.failure(request_id, 'INVALID_PARAMS', '启动器报文或参数无效')
                except ApiError as error:
                    message = self.failure(request_id, error.code, error.message)
                self.service._enqueue(connection, message)
                if replay:
                    self.service.replay(connection)
        except (WebSocketDisconnect, asyncio.TimeoutError):
            pass
        finally:
            self.service.detach(connection)
            writer.cancel()
            await asyncio.gather(writer, return_exceptions=True)
            if ws.application_state.name != 'DISCONNECTED' and ws.client_state.name != 'DISCONNECTED':
                await ws.close(code=1001)

    async def writer(self, ws, connection):
        try:
            while True:
                message = await connection.queue.get()
                if self.service.should_send(message):
                    await asyncio.wait_for(ws.send_json(message), timeout=5)
        except (WebSocketDisconnect, asyncio.TimeoutError, OSError):
            self.service.detach(connection)
            if ws.application_state.name != 'DISCONNECTED' and ws.client_state.name != 'DISCONNECTED':
                await ws.close(code=1013)

    @staticmethod
    def failure(request_id, code, message):
        return {'type': 'response', 'id': request_id, 'ok': False, 'error': {'code': code, 'message': message}}


def _service(request):
    try:
        return request.app.state.gateway.router.launcher
    except AttributeError:
        return None


def _control(request):
    service = _service(request)
    return service.legacy_control() if service else launcher_control


def _trust(request):
    service = _service(request)
    return service.legacy_trust if service else launcher_trust._legacy_trust


def _failures(request=None):
    service = _service(request) if request is not None else None
    return service.legacy_failures if service else _secret_failures


#: 信任密钥的失败窗口与上限：窗口内超限即拒绝，避免密钥被反复试探。
SECRET_FAILURE_WINDOW = 300
SECRET_FAILURE_LIMIT = 15
_secret_failures: list[float] = []

#: 命令流空闲多久发一次保活帧（秒）。
STREAM_IDLE_TIMEOUT = 30

#: 两种前端的登录凭据键；同源切换界面时沿用启动器已经完成的登录。
REACT_PASSWORD_KEY = "azurpilot.access-password"
LEGACY_PASSWORD_KEY = "password"


def configure_trust(webui_key: str | None) -> None:
    """登记启动器信任密钥与当前 WebUI 密码；未带密钥启动时免密通道整体关闭。

    Args:
        webui_key: 当前有效的 WebUI 访问密码。
    """
    launcher_trust.configure(launcher_trust.environment_secret(), webui_key)


def _forbidden(message: str, status_code: int = 403) -> JSONResponse:
    """本机限定端点的拒绝响应。"""
    return JSONResponse({"success": False, "error": message}, status_code=status_code)


def _secret_try_allowed(request=None) -> bool:
    """失败窗口内是否仍允许尝试。"""
    _secret_failures = _failures(request)
    now = time.time()
    while _secret_failures and now - _secret_failures[0] > SECRET_FAILURE_WINDOW:
        _secret_failures.pop(0)
    return len(_secret_failures) < SECRET_FAILURE_LIMIT


async def status(request):
    """GET /api/launcher/status — 启动器连接与开机自启状态。"""
    if not is_local_request(request):
        return _forbidden("启动器状态只允许本机读取")
    return JSONResponse(_control(request).status(request_local=True))


async def startup(request):
    """POST /api/launcher/startup — 请求启动器设置 Windows 开机自启。"""
    if not is_local_request(request):
        return _forbidden("开机自启动只能从本机 WebUI 设置")
    if is_demo_mode():
        return _forbidden("演示模式不允许修改开机自启动")
    try:
        data = await request.json()
    except Exception:
        return _forbidden("请求体不是有效 JSON", 400)
    if not isinstance(data, dict) or not isinstance(data.get("enabled"), bool):
        return _forbidden("enabled 必须是布尔值", 400)

    result = await _control(request).set_autostart(data["enabled"])
    return JSONResponse(result, status_code=200 if result.get("success") else 500)


async def stream(request):
    """GET /api/launcher/stream — 启动器订阅的本地命令流（SSE）。"""
    if not is_local_request(request):
        return _forbidden("启动器命令流只允许本机连接")

    async def events():
        control = _control(request)
        connection = await control.mark_connected()
        try:
            while control.service.connection is connection and not connection.socket.closed:
                if await request.is_disconnected():
                    break
                try:
                    command = await asyncio.wait_for(control.next_command(connection), timeout=STREAM_IDLE_TIMEOUT)
                    control.keep_alive(connection)
                    yield control.event(command)
                except asyncio.TimeoutError:
                    control.keep_alive(connection)
                    yield control.keepalive_event()
        finally:
            control.mark_disconnected(connection)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def report(request):
    """POST /api/launcher/report — 启动器回报命令执行结果。"""
    if not is_local_request(request):
        return _forbidden("启动器回报只允许本机连接")
    try:
        data = await request.json()
    except Exception:
        return _forbidden("请求体不是有效 JSON", 400)
    if not isinstance(data, dict):
        return _forbidden("请求体必须是 JSON 对象", 400)
    return JSONResponse(await _control(request).report(data))


async def trusted_login(request):
    """POST /api/launcher/trusted-login — 启动器以信任密钥换取一次性免密令牌。"""
    if not is_local_request(request):
        return _forbidden("免密通道只允许本机连接")
    if not _trust(request).enabled():
        return _forbidden("启动器免密通道未启用")
    if not _secret_try_allowed(request):
        return _forbidden("尝试次数过多，请稍后再试", 429)

    if not _trust(request).check_secret(request.headers.get("x-webui-launcher-secret")):
        _failures(request).append(time.time())
        return _forbidden("信任密钥无效")

    token = _trust(request).issue_token()
    if token is None:
        return _forbidden("免密令牌签发失败")
    return JSONResponse({"success": True, "token": token, "ttl": TOKEN_TTL_SECONDS})


async def login_seed(request):
    """GET /launcher-login?token= — 用一次性令牌把登录凭据写进本机窗口并跳回首页。"""
    if not is_local_request(request) or not _trust(request).consume(request.query_params.get("token")):
        return _login_denied()

    key = _trust(request).webui_key
    if not key:
        return HTMLResponse('<!doctype html><script>location.replace("/");</script>')

    # 与控制台同源，写入它读取的凭据键即免密进入；密码内嵌为 JS 字面量并转义 "<"，
    # 免得密码里出现 </script> 提前闭合脚本块。
    literal = json.dumps(str(key)).replace("<", "\\u003c")
    html = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<title>AzurPilot 免密登录</title><script>'
            f"localStorage.setItem('{REACT_PASSWORD_KEY}', {literal});"
            f"localStorage.setItem('{LEGACY_PASSWORD_KEY}', {literal});"
            'location.replace("/");</script>'
            '<body>正在免密登录，请稍候…</body></html>')
    return HTMLResponse(html)


def _login_denied() -> HTMLResponse:
    """令牌缺失、失效或非本机访问时的拒绝页。"""
    return HTMLResponse('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                        '<title>拒绝访问</title><body>该链接无效或已过期，请从启动器重新打开。</body></html>',
                        status_code=403)



def routes(service=None):
    """V2 与已安装的旧启动器共存，全部端点先于静态兜底注册。"""
    result = [Route('/api/launcher/status', status),
              Route('/api/launcher/startup', startup, methods=['POST']),
              Route('/api/launcher/stream', stream),
              Route('/api/launcher/report', report, methods=['POST']),
              Route('/api/launcher/trusted-login', trusted_login, methods=['POST']),
              Route('/launcher-login', login_seed)]
    if service is not None:
        result.insert(0, WebSocketRoute('/api/v2/launcher/ws', LauncherGateway(service).endpoint))
    result.append(Route('/api/launcher/{path:path}', removed, methods=['GET', 'POST']))
    return result


async def removed(request):
    return Response(status_code=404)
