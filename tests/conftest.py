"""隔离测试结束时确认后台写入完成，并释放业务总库的复用连接。"""
import unittest
import tempfile
import os
import shutil
from pathlib import Path

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

# SQLite 临时文件走 TMP/TEMP，不探测可写性；受限环境里该目录不可写会让
# 大语句报 SQLITE_CANTOPEN。为测试进程统一指向可写目录，裸连接同样受益。
_sqlite_temp = tempfile.TemporaryDirectory(prefix='pytest-sqlite-temp-')
_sqlite_temp_root = Path(_sqlite_temp.name).resolve()
os.environ['TMP'] = os.environ['TEMP'] = _sqlite_temp.name
tempfile.tempdir = None


def pytest_sessionfinish():
    """仅回收本进程创建的临时根，长路径测试同样经过项目路径适配。"""
    from module.persistence.migration import io_path

    _cleanup_storage()
    if Path(_sqlite_temp.name).resolve() != _sqlite_temp_root:
        raise RuntimeError('测试临时根已被替换，保留目录以供检查')
    if _sqlite_temp_root.exists():
        cleanup_root = str(io_path(_sqlite_temp_root))
        # 根本身较短时 io_path 不加前缀，rmtree 仍需让长子路径继承扩展形式。
        if os.name == 'nt' and not cleanup_root.startswith('\\\\?\\'):
            cleanup_root = ('\\\\?\\UNC\\' + cleanup_root[2:] if cleanup_root.startswith('\\\\')
                            else '\\\\?\\' + cleanup_root)
        shutil.rmtree(cleanup_root)
    _sqlite_temp.cleanup()


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
