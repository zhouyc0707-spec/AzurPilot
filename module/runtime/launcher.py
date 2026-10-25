"""每个应用独立的启动器会话、命令状态与通知缓冲。"""
import asyncio
import json
import sys
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field

from module.api.protocol import ApiError
from module.base.runtime_params import COMMAND_TIMEOUT, CONNECTION_EXPIRE
from module.runtime.launcher_trust import LauncherTrust, LegacyLauncherTrust
from module.runtime.password_utils import REMOTE_ACCESS_HEADER, is_local_client


def is_local_request(request):
    """直连回环与来源校验；经隧道或代理转发的请求不授予本机权限。"""
    return is_local_client(
        request.client.host if request.client else '', request.headers.get('host'),
        request.headers.get('origin'), request.headers.get(REMOTE_ACCESS_HEADER),
    ) and not any(request.headers.get(key) for key in ('forwarded', 'x-forwarded-for', 'x-forwarded-host'))


@dataclass
class LauncherConnection:
    socket: object
    loop: object
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(128))
    ready: bool = False
    last_seen: float = field(default_factory=time.monotonic)


class LauncherService:
    """跨线程业务调用受锁保护，发送操作回到 WS 所属事件循环。"""

    def __init__(self, secret=None):
        self.trust = LauncherTrust(secret)
        self.legacy_trust = LegacyLauncherTrust(secret)
        self.legacy_failures = []
        self._legacy_control = None
        self.lock = threading.RLock()
        self.connection = None
        self.commands = OrderedDict()
        self.notifications = OrderedDict()
        self.capabilities = []
        self.platform = sys.platform
        self.version = ''
        self.autostart = {'supported': False, 'enabled': None}

    def legacy_control(self):
        """旧 SSE 与 V2 使用同一连接、命令及状态，防止前端出现两套状态。"""
        with self.lock:
            if self._legacy_control is None:
                self._legacy_control = LauncherControl(self)
            return self._legacy_control

    async def attach(self, socket):
        connection = LauncherConnection(socket, asyncio.get_running_loop())
        with self.lock:
            previous = self.connection
            self._fail_pending('LAUNCHER_DISCONNECTED', '启动器连接已更换，请查询实际状态')
            self.connection = connection
        if previous:
            await previous.socket.close(code=1000)
        return connection

    def detach(self, connection):
        with self.lock:
            if self.connection is connection:
                self.connection = None
                self._fail_pending('LAUNCHER_DISCONNECTED', '启动器已断开，请查询实际状态')

    def hello(self, connection, params):
        with self.lock:
            if self.connection is not connection:
                raise ApiError('LAUNCHER_DISCONNECTED', '启动器会话已失效')
            if connection.ready:
                raise ApiError('INVALID_REQUEST', '启动器已完成握手')
            self.platform, self.version = params.platform, params.launcherVersion
            self.capabilities = params.capabilities
            self.autostart = params.autostart.model_dump()
            connection.ready = True
            return {'protocolVersion': 2, 'heartbeatSeconds': 15}

    def replay(self, connection):
        """握手响应先入队，客户端确认协议后才能收到缓冲通知。"""
        with self.lock:
            for message in self.notifications.values():
                self._enqueue(connection, message)

    def status(self):
        with self.lock:
            self._expire()
            connected = self.connection is not None and self.connection.ready
            return {'connected': connected, 'platform': self.platform, 'version': self.version,
                    'capabilities': list(self.capabilities) if connected else [],
                    'autostart': dict(self.autostart),
                    'commands': [dict(value) for value in self.commands.values()]}

    def set_autostart(self, enabled):
        with self.lock:
            self._expire()
            connection = self.connection
            if connection is None or not connection.ready:
                raise ApiError('LAUNCHER_DISCONNECTED', '启动器未连接')
            if 'autostart' not in self.capabilities or not self.autostart['supported']:
                raise ApiError('UNSUPPORTED', '当前启动器不支持开机自启')
            if sum(value['status'] == 'pending' for value in self.commands.values()) >= 32:
                raise ApiError('BUSY', '启动器命令队列已满')
            command_id = uuid.uuid4().hex
            self.commands[command_id] = {'id': command_id, 'status': 'pending',
                                         'deadline': time.time() + COMMAND_TIMEOUT}
            while len(self.commands) > 128:
                completed = next(key for key, value in self.commands.items() if value['status'] != 'pending')
                self.commands.pop(completed)
            message = {'type': 'request', 'id': command_id, 'method': 'autostart.set',
                       'params': {'enabled': enabled}, 'deadline': self.commands[command_id]['deadline']}
            connection.loop.call_soon_threadsafe(self._enqueue, connection, message)
            return {'commandId': command_id, 'status': 'pending'}

    def command(self, command_id):
        with self.lock:
            self._expire()
            if command_id not in self.commands:
                raise ApiError('NOT_FOUND', '启动器命令不存在')
            return dict(self.commands[command_id])

    def should_send(self, message):
        with self.lock:
            self._expire()
            return message.get('type') != 'request' or self.commands.get(message['id'], {}).get('status') == 'pending'

    def complete(self, connection, reply):
        with self.lock:
            if self.connection is not connection:
                return
            self._expire()
            command = self.commands.get(reply.id)
            if not command or command['status'] != 'pending':
                return
            if reply.ok:
                from module.api.launcher_protocol import AutostartState
                result = AutostartState.model_validate(reply.result).model_dump()
                self.autostart = result
                command.update(status='succeeded', result=result)
            else:
                command.update(status='failed', error=reply.error.model_dump())

    def report_state(self, params):
        with self.lock:
            self.autostart = params.autostart.model_dump()
        return {'accepted': True}

    def publish(self, notification, legacy=None):
        message = {'type': 'event', 'id': uuid.uuid4().hex, 'topic': 'notification', 'data': notification}
        with self.lock:
            self.notifications[message['id']] = message
            while len(self.notifications) > 100:
                self.notifications.popitem(last=False)
            queue = self.legacy_control().notifications
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(notification if legacy is None else legacy)
            connection = self.connection
            if connection and connection.ready and not isinstance(connection.socket, _LegacySocket):
                connection.loop.call_soon_threadsafe(self._enqueue, connection, message)

    def acknowledge(self, ids):
        with self.lock:
            for event_id in ids:
                self.notifications.pop(event_id, None)
        return {'accepted': True}

    def _enqueue(self, connection, message):
        if self.connection is not connection:
            return
        try:
            connection.queue.put_nowait(message)
        except asyncio.QueueFull:
            self.detach(connection)
            connection.loop.create_task(connection.socket.close(code=1013))

    def _expire(self):
        for command in self.commands.values():
            if command['status'] == 'pending' and command['deadline'] <= time.time():
                command.update(status='expired', error={'code': 'TIMEOUT', 'message': '启动器响应超时，请查询实际状态'})
        connection = self.connection
        if connection and time.monotonic() - connection.last_seen > CONNECTION_EXPIRE:
            self.detach(connection)
            connection.loop.call_soon_threadsafe(lambda: connection.loop.create_task(connection.socket.close(code=1001)))

    def _fail_pending(self, code, message):
        for command in self.commands.values():
            if command['status'] == 'pending':
                command.update(status='failed', error={'code': code, 'message': message})

    async def close(self):
        with self.lock:
            connection = self.connection
            if connection:
                self.detach(connection)
            self.notifications.clear()
            self.trust.clear()
            self.legacy_trust.clear()
        if connection:
            await connection.socket.close(code=1001)


class _LegacySocket:
    """让旧 SSE 会话参与统一连接替换与关闭，无伪造 WS 帧。"""

    def __init__(self):
        self.closed = False
        self.queue = None

    async def close(self, code=1000):
        self.closed = True
        if self.queue is not None:
            if self.queue.full():
                self.queue.get_nowait()
            self.queue.put_nowait(None)


class LauncherControl:
    """翻译旧启动器报文；队列、命令截止时间与状态由 LauncherService 管理。"""

    def __init__(self, service=None):
        self.service = service if service is not None else LauncherService()
        self.connection = None
        self.notifications = asyncio.Queue(100)
        self.query_id = ''

    def status(self, request_local=True):
        state = self.service.status()
        return {'success': True, 'platform': state['platform'], 'windows': state['platform'] == 'win32',
                'request_local': request_local, 'launcher_connected': state['connected'],
                'autostart_supported': state['autostart']['supported'],
                'autostart_enabled': state['autostart']['enabled'], 'last_error': ''}

    async def mark_connected(self):
        from module.api.launcher_protocol import HelloParams
        self.connection = await self.service.attach(_LegacySocket())
        self.connection.socket.queue = self.connection.queue
        self.service.hello(self.connection, HelloParams(
            protocolVersion=2, launcherVersion='legacy', platform=sys.platform,
            capabilities=['autostart', 'notifications', 'login'],
            autostart={'supported': sys.platform == 'win32', 'enabled': None}))
        self.query_id = uuid.uuid4().hex
        self.connection.queue.put_nowait({'type': 'startup.query', 'id': self.query_id, 'payload': {}})
        return self.connection

    def mark_disconnected(self, connection=None):
        self.service.detach(connection or self.connection)

    def keep_alive(self, connection=None):
        current = connection or self.connection
        if self.service.connection is current and current is not None:
            current.last_seen = time.monotonic()

    async def next_command(self, connection=None):
        current = connection or self.connection
        while self.service.connection is current and not current.socket.closed:
            message = await current.queue.get()
            if message is None:
                raise asyncio.CancelledError
            if message['type'] == 'startup.query':
                return message
            if message['type'] == 'request' and self.service.should_send(message):
                return {'id': message['id'], 'type': 'startup.set',
                        'payload': {**message['params'], 'mode': 'tray'}}
            # 旧通知流与控制流分开；未发送过期命令，不把通知误当控制命令。
        raise asyncio.CancelledError

    async def set_autostart(self, enabled):
        try:
            command_id = self.service.set_autostart(enabled)['commandId']
            while True:
                command = self.service.command(command_id)
                if command['status'] != 'pending':
                    if command['status'] == 'succeeded':
                        return {'success': True, 'data': self.status()}
                    return {'success': False, 'error': command.get('error', {}).get('message', '启动器设置失败')}
                await asyncio.sleep(0.05)
        except ApiError as error:
            return {'success': False, 'error': error.message}

    async def report(self, data):
        from module.api.launcher_protocol import LauncherResponse, StateParams
        current = self.connection
        if current is None or self.service.connection is not current:
            return {'success': False, 'error': '旧启动器会话已断开'}
        payload = data.get('data') or {}
        if not isinstance(payload, dict) or not isinstance(data.get('success'), bool):
            return {'success': False, 'error': '启动器状态格式无效'}
        if data['success'] and not isinstance(payload.get('enabled'), bool):
            return {'success': False, 'error': 'enabled 必须是布尔值'}
        self.keep_alive(current)
        if data.get('id') == self.query_id:
            if data['success']:
                self.service.report_state(StateParams(autostart={
                    'supported': sys.platform == 'win32', 'enabled': payload['enabled']}))
        elif data.get('id') in self.service.commands:
            reply = {'type': 'response', 'id': data['id'], 'ok': data['success']}
            if data['success']:
                reply['result'] = {'supported': sys.platform == 'win32', 'enabled': payload['enabled']}
            else:
                reply['error'] = {'code': 'LAUNCHER_ERROR', 'message': str(data.get('error') or '启动器设置失败')[:256]}
            self.service.complete(current, LauncherResponse.model_validate(reply))
        return {'success': True}

    @staticmethod
    def event(command):
        return f'data: {json.dumps(command, ensure_ascii=False)}\n\n'

    @staticmethod
    def keepalive_event():
        return ': keepalive\n\n'


launcher_control = LauncherControl()
