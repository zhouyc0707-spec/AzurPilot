"""被动截图通道：游戏线程只投递图像，后台编码，父进程分发最新帧。"""
import base64
import io
import queue
import threading
import time
from datetime import datetime
from typing import Callable


class PreviewHub:
    """运行预览截图广播中心。

    每实例只保留最新一帧，订阅者通过通知取最新值，不累积视频队列。

    Attributes:
        frames: 各实例最新帧数据缓存映射。
        listeners: 订阅监听器回调集合。
        lock: 互斥锁。
    """

    def __init__(self):
        """初始化预览截图广播中心。"""
        self.frames = {}
        self.listeners = set()
        self.lock = threading.Lock()
        self.viewers = {}
        self.workers = {}

    def set_viewer(self, owner, instance: str | None):
        """按连接登记实际观看的实例；普通帧监听器不代表有人观看。"""
        with self.lock:
            previous = self.viewers.pop(owner, None)
            if instance is not None:
                self.viewers[owner] = instance
            for name in {previous, instance} - {None}:
                self._update_worker(name)

    def bind_worker(self, instance: str, demand):
        """绑定本轮 worker 的共享开关，继承现有观看需求并关闭旧开关。"""
        with self.lock:
            previous = self.workers.get(instance)
            if previous is not None and previous is not demand:
                previous.clear()
            self.workers[instance] = demand
            self._update_worker(instance)

    def _update_worker(self, instance: str):
        """在持锁状态下更新共享开关；每帧无需查询父进程或执行 RPC。"""
        demand = self.workers.get(instance)
        if demand is not None:
            if instance in self.viewers.values():
                demand.set()
            else:
                demand.clear()

    def publish(self, instance: str, frame: dict):
        """发布并更新指定实例的最新一帧截图。

        Args:
            instance: 实例名称。
            frame: 包含时间戳与 Base64 图像数据的帧字典。
        """
        with self.lock:
            self.frames[instance] = frame
            listeners = tuple(self.listeners)
        for listener in listeners:
            listener(instance)

    def get(self, instance: str) -> dict:
        """获取指定实例当前缓存的最新一帧截图。

        Args:
            instance: 实例名称。

        Returns:
            dict: 帧字典；若无缓存则返回空图占位字典。
        """
        with self.lock:
            return self.frames.get(instance, {'instance': instance, 'image': None, 'capturedAt': None})

    def discard(self, instance: str):
        """清除指定实例的预览帧缓存。

        Args:
            instance: 实例名称。
        """
        with self.lock:
            self.frames.pop(instance, None)
            demand = self.workers.pop(instance, None)
            if demand is not None:
                demand.clear()
            self.viewers = {owner: name for owner, name in self.viewers.items() if name != instance}

    def subscribe(self, listener: Callable[[str], None]):
        """注册新帧到达监听器。

        Args:
            listener: 接收实例名的回调函数。
        """
        with self.lock:
            self.listeners.add(listener)

    def unsubscribe(self, listener: Callable[[str], None]):
        """注销新帧到达监听器。

        Args:
            listener: 待注销的回调函数。
        """
        with self.lock:
            self.listeners.discard(listener)


hub = PreviewHub()
_images = None
_demand = None
_next_frame_at = 0.0
FRAME_INTERVAL = 0.5  # 预览最高每秒两帧，不改变游戏识别的截图频率。


def initialize(instance: str, output: queue.Queue, task_sink: queue.Queue, run_id: str = None, demand=None):
    """在运行子进程中安装输出通道，独立脚本无需初始化。

    只有共享开关表示有人观看时，才把正常游戏截图转换为 Base64 预览。

    Args:
        instance: 实例名称。
        output: 跨进程帧传输输出队列。
        task_sink: 任务事件状态输出队列。
        run_id: 可选的运行会话 ID。
        demand: 父进程维护的本实例观看需求共享开关；未提供时不生成预览。
    """
    from module.runtime.worker_events import initialize as initialize_events

    global _images, _demand, _next_frame_at
    initialize_events(task_sink, run_id)
    _images = queue.Queue(maxsize=1)
    _demand = demand
    _next_frame_at = 0.0

    def encode():
        from PIL import Image
        while True:
            image, timestamp = _images.get()
            if _demand is None or not _demand.is_set():
                continue
            try:
                buffer = io.BytesIO()
                # 各后端在统一截图入口返回 RGB 图像。
                Image.fromarray(image).save(buffer, format='JPEG', quality=85)
                frame = {'instance': instance, 'capturedAt': timestamp, 'runId': run_id,
                         'image': 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode()}
                # 编码期间关闭预览时，丢弃尾帧，不再向父进程传输。
                if not _demand.is_set():
                    continue
                try:
                    output.put_nowait(frame)
                except queue.Full:
                    try:
                        output.get_nowait()
                    except queue.Empty:
                        pass
                    output.put_nowait(frame)
            except (OSError, EOFError, BrokenPipeError):
                return
            except (MemoryError, ValueError, queue.Full):
                # 预览丢帧不能中断实际任务或造成无界积压。
                continue

    threading.Thread(target=encode, daemon=True, name='preview-encoder').start()


def publish(image):
    """有观看需求且达到预览间隔时，才复制截图交给编码线程。

    避免后续游戏逻辑在图像上绘制调试标注产生画面污染。

    Args:
        image: NumPy RGB 图像数组。
    """
    global _next_frame_at
    if _images is None or _demand is None or not _demand.is_set():
        _next_frame_at = 0.0
        return
    now = time.monotonic()
    if now < _next_frame_at:
        return
    try:
        frame = (image.copy(), datetime.now().isoformat())
    except MemoryError:
        # 预览副本分配失败只丢当前预览帧；游戏仍使用已经取得的原截图。
        return
    if not _demand.is_set():
        return
    _next_frame_at = now + FRAME_INTERVAL
    try:
        _images.put_nowait(frame)
    except queue.Full:
        try:
            _images.get_nowait()
        except queue.Empty:
            pass
        try:
            _images.put_nowait(frame)
        except queue.Full:
            pass


def set_task(command: str):
    """通过可靠的进程队列传递当前执行的任务边界。

    避免上层从非结构化的日志文字反向推测运行状态。

    Args:
        command: 当前执行的任务命令名称。
    """
    from module.runtime.worker_events import set_task as publish_task

    publish_task(command)
