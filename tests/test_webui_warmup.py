"""WebUI worker 后台预热的护栏测试。

预热的价值在于「第一次进总览页不再冷启动」，但它的每个约束都比省下的时间更重要：
不能写盘（数据库预热必须是只读）、不能因为失败影响启动、不能每会话重复跑。
"""

import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from module.webui import warmup


class TestWarmupGuards(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(warmup, "_started", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_start_is_idempotent_per_process(self):
        """同一进程内重复调用只启动一次线程。"""
        started = []

        class FakeThread:
            def __init__(self, target=None, daemon=None, name=None):
                started.append(name)

            def start(self):
                return

        with patch.object(warmup.threading, "Thread", FakeThread):
            self.assertTrue(warmup.start_warmup())
            self.assertFalse(warmup.start_warmup())
            self.assertFalse(warmup.start_warmup())

        self.assertEqual(["webui-warmup"], started)

    def test_step_failure_does_not_abort_remaining_steps(self):
        """某一步失败只记录，不能中断后续步骤，更不能抛出去。"""
        called = []

        def boom():
            called.append("boom")
            raise RuntimeError("模拟预热失败")

        def ok():
            called.append("ok")

        with (
            patch.object(warmup, "_warm_device_id", boom),
            patch.object(warmup, "_warm_modules", ok),
            patch.object(warmup, "_warm_databases", ok),
            patch.object(warmup, "_warm_config", ok),
            patch.object(warmup.logger, "warning") as warning,
        ):
            warmup._run()  # 不应抛出

        self.assertEqual(["boom", "ok", "ok", "ok"], called)
        self.assertEqual(1, warning.call_count)

    def test_database_warmup_opens_read_only_and_never_writes(self):
        """数据库预热必须以只读模式打开：绝不能建表或写入。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        config_dir = Path(tmp.name) / "config"
        config_dir.mkdir()
        db_path = config_dir / "cl1_data.db"
        # 注意：sqlite3 连接必须显式 close，`with sqlite3.connect(...)` 只管事务提交，
        # Windows 下不关连接会让临时目录删不掉（WinError 32）。
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE cl1_data (instance TEXT, month TEXT)")
            conn.commit()
        finally:
            conn.close()
        before = db_path.read_bytes()
        before_mtime = db_path.stat().st_mtime_ns

        recorded = []
        real_connect = sqlite3.connect

        def spy_connect(database, *args, **kwargs):
            recorded.append((str(database), kwargs.get("uri"), kwargs.get("mode")))
            return real_connect(database, *args, **kwargs)

        # _warm_databases 自己按项目根推导路径，这里把它指向临时目录
        with (
            patch.object(warmup, "__file__", str(config_dir.parent / "module" / "webui" / "warmup.py")),
            patch.object(sqlite3, "connect", side_effect=spy_connect),
        ):
            (config_dir.parent / "module" / "webui").mkdir(parents=True, exist_ok=True)
            warmup._warm_databases()

        self.assertTrue(recorded, "应当打开过数据库")
        for database, uri, mode in recorded:
            self.assertTrue(uri, f"必须用 uri 形式打开: {database}")
            self.assertIn("mode=ro", database, "必须以只读模式打开")
            self.assertIsNone(mode, "只读应由 URI 指定，而不是可写的 mode 参数")
        self.assertEqual(before, db_path.read_bytes(), "预热不得改动数据库内容")
        self.assertEqual(before_mtime, db_path.stat().st_mtime_ns, "预热不得触碰 mtime")

    def test_database_warmup_skips_missing_files(self):
        """库文件不存在时安静跳过，不建库。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        (Path(tmp.name) / "module" / "webui").mkdir(parents=True)

        with patch.object(
            warmup, "__file__", str(Path(tmp.name) / "module" / "webui" / "warmup.py")
        ):
            warmup._warm_databases()  # 不应抛出，也不应创建文件

        self.assertEqual([], list((Path(tmp.name) / "config").glob("*.db"))
                         if (Path(tmp.name) / "config").exists() else [])

    def test_warmup_modules_are_importable(self):
        """预热导入的模块名必须真实存在，否则预热是静默无效的。"""
        import importlib

        for name in (
            "module.statistics.azurstats",
            "module.statistics.cl1_database",
            "module.statistics.commission_income_stats",
            "module.statistics.opsi_month",
            "module.statistics.ship_exp_stats",
            "module.config.config",
            "module.log_res.log_res",
        ):
            with self.subTest(module=name):
                if name in sys.modules:
                    continue
                self.assertIsNotNone(importlib.util.find_spec(name))

    def test_run_reports_completion_without_session(self):
        """没有实例/配置时也必须安静结束，不能挂住线程。"""
        done = threading.Event()

        def fake_run():
            warmup._run()
            done.set()

        with (
            patch.object(warmup, "_warm_device_id", lambda: None),
            patch.object(warmup, "_warm_modules", lambda: None),
            patch.object(warmup, "_warm_databases", lambda: None),
            patch.object(warmup, "_warm_config", lambda: None),
        ):
            thread = threading.Thread(target=fake_run, daemon=True)
            thread.start()
            self.assertTrue(done.wait(timeout=2))


if __name__ == "__main__":
    unittest.main()
