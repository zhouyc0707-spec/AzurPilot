"""全局公告服务模块。

负责从远端获取公告，支持缓存与并发保护。
"""

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional

from module.base.api_client import ApiClient
from module.logger import logger

# 公告外网请求独占一个工作线程，不占用首屏和任务操作共用的线程池。
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='announcement')


class AnnouncementService:
    """提供公告缓存与获取能力的服务类。

    Attributes:
        _ttl (int): 缓存过期时间（秒）。
        _lock (threading.Lock): 线程安全锁。
        _cached (Optional[Dict[str, Any]]): 缓存的公告数据。
        _last_fetch (float | None): 上次请求完成的单调时钟值，空结果和失败同样缓存。
    """

    def __init__(self, ttl: int = 90):
        """初始化公告服务。

        Args:
            ttl (int, optional): 缓存生存时间（秒）。默认为 90。
        """
        self._ttl = ttl
        self._lock = threading.Lock()
        self._cached: Optional[Dict[str, Any]] = None
        self._last_fetch: Optional[float] = None
        self._pending_lock = threading.Lock()
        self._pending = None

    async def get_async(self, force: bool = False) -> Optional[Dict[str, Any]]:
        """共享在途公告查询，等待结果时不阻塞会话的其他请求。"""
        with self._pending_lock:
            if self._pending is None or self._pending.done():
                self._pending = _executor.submit(self.get, force)
            pending = self._pending
        # 一个浏览器离开或断线，不应取消其他浏览器共用的查询。
        return await asyncio.shield(asyncio.wrap_future(pending))

    def get(self, force: bool = False) -> Optional[Dict[str, Any]]:
        """获取当前最新公告。

        Args:
            force (bool, optional): 为 True 时跳过缓存 TTL 强制重新拉取。默认为 False。

        Returns:
            Optional[Dict[str, Any]]: 公告数据字典，包含 announcementId, title, content, url 等；无公告时返回 None。
        """
        with self._lock:
            if not force and self._last_fetch is not None and time.monotonic() - self._last_fetch < self._ttl:
                return self._cached

            cached_id = self._cached.get('announcementId') if self._cached else None
            try:
                # ApiClient.get_announcement 支持传入 current_id 进行增量检查
                data = ApiClient.get_announcement(timeout=5, current_id=cached_id)
                if data is not None:
                    self._cached = data or None
                # 若返回 None 且之前有缓存，则保留已有缓存（如 304 Not Modified）
            except Exception as e:
                logger.warning(f'[API] 拉取公告失败: {e}')
            finally:
                self._last_fetch = time.monotonic()

            return self._cached


announcement_service = AnnouncementService()
