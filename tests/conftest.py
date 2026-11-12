"""隔离测试结束时确认后台写入完成，并释放业务总库的复用连接。"""
import unittest

import pytest

from module.persistence.database import close_all
from module.persistence.write_queue import write_queue

_original_do_cleanups = unittest.TestCase.doCleanups


def _cleanup_storage():
    """先确认待写项已落库，再关闭句柄，清理失败交给测试框架报告。"""
    from module.base.async_executor import async_executor

    try:
        async_executor.flush()
        if not write_queue.flush(timeout=2):
            raise AssertionError(f'测试结束后仍有 {write_queue.pending()} 条待写数据')
    finally:
        close_all()


def _do_cleanups(self):
    """在临时目录自身的清理动作前登记存储清理。"""
    self.addCleanup(_cleanup_storage)
    return _original_do_cleanups(self)


unittest.TestCase.doCleanups = _do_cleanups


@pytest.fixture(autouse=True)
def cleanup_business_storage(request):
    """函数式测试同样在结束后释放业务句柄。"""
    yield
    if not isinstance(request.instance, unittest.TestCase):
        _cleanup_storage()
