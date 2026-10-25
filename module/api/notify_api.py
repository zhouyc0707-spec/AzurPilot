"""新旧启动器通知入口：有界接收、标准字段广播、旧附加字段只进入旧 SSE。"""
import asyncio
import json

from pydantic import ValidationError
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from module.api.launcher_protocol import NotificationParams
from module.api.launcher_api import _service
from module.runtime.launcher import is_local_request
from module.runtime.notify_hub import hub

STREAM_IDLE_TIMEOUT = 30
_NOTIFICATION_QUEUE_MAXSIZE = 100
_notification_queue = asyncio.Queue(_NOTIFICATION_QUEUE_MAXSIZE)


def _queue(request):
    service = _service(request)
    return service.legacy_control().notifications if service else _notification_queue


async def _receive(request, *, legacy):
    failure_key = 'success' if legacy else 'ok'
    if not is_local_request(request):
        return JSONResponse({failure_key: False, 'error': '通知接收只允许本机调用'}, status_code=403)
    if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
        return JSONResponse({failure_key: False, 'error': 'Content-Type 必须是 application/json'}, status_code=400)
    try:
        if hasattr(request, 'stream'):
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 65536:
                    return JSONResponse({failure_key: False, 'error': '通知超过大小限制'}, status_code=413)
            data = json.loads(body)
        else:
            data = await request.json()
        if not isinstance(data, dict):
            raise ValueError('通知必须是对象')
        standard = {key: data.get(key, '') for key in ('instance', 'title', 'content')} if legacy else data
        notification = NotificationParams.model_validate(standard).model_dump()
    except (ValidationError, ValueError, TypeError):
        return JSONResponse({failure_key: False, 'error': '通知格式无效'}, status_code=400)
    service = _service(request)
    if service:
        service.publish(notification, legacy=data if legacy else None)
    else:
        queue = _queue(request)
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(data if legacy else notification)
    hub.publish(notification)
    return JSONResponse({'success': True} if legacy else {'ok': True})


async def notify(request):
    """保留旧通知字段和 Python 导入入口，不放宽 V2 严格模型。"""
    return await _receive(request, legacy=True)


async def notify_v2(request):
    return await _receive(request, legacy=False)


async def notify_stream(request):
    if not is_local_request(request):
        return JSONResponse({'success': False, 'error': '通知流只允许本机连接'}, status_code=403)
    queue = _queue(request)

    async def events():
        while not await request.is_disconnected():
            try:
                payload = await asyncio.wait_for(queue.get(), STREAM_IDLE_TIMEOUT)
                yield f'data: {json.dumps(payload, ensure_ascii=False)}\n\n'
            except asyncio.TimeoutError:
                yield ': keepalive\n\n'
    return StreamingResponse(events(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


def routes():
    return [Route('/api/v2/internal/notifications', notify_v2, methods=['POST']),
            Route('/api/notify', notify, methods=['POST']),
            Route('/api/notify_stream', notify_stream)]
