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

from module.persistence.database import BusinessDatabase, use_database
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
        store = BusinessDatabase(config_dir)
        db_path = store.path
        # 注意：sqlite3 连接必须显式 close，`with sqlite3.connect(...)` 只管事务提交，
        # Windows 下不关连接会让临时目录删不掉（WinError 32）。
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE warmup_fixture (instance TEXT, month TEXT)")
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

        with (
            use_database(store),
            patch.object(BusinessDatabase, "ensure_ready", side_effect=AssertionError("预热不能初始化总库")),
            patch.object(sqlite3, "connect", side_effect=spy_connect),
        ):
            warmup._warm_databases()

        self.assertTrue(recorded, "应当打开过数据库")
        for database, uri, mode in recorded:
            self.assertTrue(uri, f"必须用 uri 形式打开: {database}")
            self.assertIn("mode=ro", database, "必须以只读模式打开")
            self.assertIsNone(mode, "只读应由 URI 指定，而不是可写的 mode 参数")
        self.assertEqual(before, db_path.read_bytes(), "预热不得改动数据库内容")
        self.assertEqual(before_mtime, db_path.stat().st_mtime_ns, "预热不得触碰 mtime")
        self.assertFalse(store.marker.exists(), "直接只读预热不得补迁移标记")

    def test_database_warmup_skips_missing_files(self):
        """库文件不存在时安静跳过，不建库。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = BusinessDatabase(Path(tmp.name) / "config")

        with (
            use_database(store),
            patch.object(sqlite3, "connect") as connect,
            patch.object(BusinessDatabase, "ensure_ready", side_effect=AssertionError("预热不能初始化总库")),
        ):
            warmup._warm_databases()  # 不应抛出，也不应创建文件
            connect.assert_not_called()

        self.assertFalse(store.directory.exists(), "预热不创建缺失的配置目录")

    def test_legacy_sources_do_not_trigger_migration_or_creation(self):
        """尚未迁移时保留旧库，预热不能成为第二个迁移入口。"""
        with tempfile.TemporaryDirectory() as temporary:
            store = BusinessDatabase(Path(temporary) / "config")
            store.directory.mkdir()
            legacy = store.directory / "cl1_data.db"
            legacy.write_bytes(b"legacy fixture")
            before = legacy.read_bytes()

            with (
                use_database(store),
                patch.object(sqlite3, "connect") as connect,
                patch.object(BusinessDatabase, "ensure_ready", side_effect=AssertionError("预热不能迁移旧库")),
            ):
                warmup._warm_databases()
                connect.assert_not_called()

            self.assertEqual(before, legacy.read_bytes())
            self.assertEqual([legacy], list(store.directory.iterdir()))

    def test_missing_migrated_database_is_not_recreated(self):
        """已完成迁移却丢失总库时同样只跳过，交给正式入口报错和恢复。"""
        with tempfile.TemporaryDirectory() as temporary:
            store = BusinessDatabase(Path(temporary) / "config")
            store.directory.mkdir()
            store.marker.write_text("1\nfixture-digest\n", encoding="utf-8")
            before = store.marker.read_bytes()

            with use_database(store), patch.object(sqlite3, "connect") as connect:
                warmup._warm_databases()
                connect.assert_not_called()

            self.assertFalse(store.path.exists())
            self.assertEqual(before, store.marker.read_bytes())

    def test_readonly_warmup_reads_current_wal_schema(self):
        """不使用 immutable 旧快照：未 checkpoint 的新页仍属于当前总库。"""
        with tempfile.TemporaryDirectory() as temporary:
            store = BusinessDatabase(temporary)
            writer = sqlite3.connect(store.path)
            try:
                writer.execute("PRAGMA journal_mode=WAL")
                writer.execute("PRAGMA wal_autocheckpoint=0")
                writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                before = store.path.read_bytes()
                writer.execute("CREATE TABLE wal_only_fixture (value TEXT)")
                writer.commit()
                self.assertEqual(before, store.path.read_bytes())
                rows = []
                real_connect = sqlite3.connect

                class ReadCursor:
                    def __init__(self, cursor):
                        self.cursor = cursor

                    def fetchone(self):
                        row = self.cursor.fetchone()
                        rows.append(row)
                        return row

                class ReadConnection:
                    def __init__(self, connection):
                        self.connection = connection

                    def execute(self, query):
                        return ReadCursor(self.connection.execute(query))

                    def close(self):
                        self.connection.close()

                def connect_readonly(database, **kwargs):
                    self.assertIn("mode=ro", database)
                    self.assertNotIn("immutable", database)
                    return ReadConnection(real_connect(database, **kwargs))

                with use_database(store), patch.object(sqlite3, "connect", side_effect=connect_readonly):
                    warmup._warm_databases()

                self.assertEqual([("wal_only_fixture",)], rows)
                self.assertEqual(before, store.path.read_bytes())
                self.assertFalse(store.marker.exists())
            finally:
                writer.close()

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
