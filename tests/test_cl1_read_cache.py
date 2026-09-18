"""CL1 只读缓存的语义测试。

`Cl1Database.read_cache()` 是给只读渲染路径用的：一次渲染会经由多条路径重复读取
同一个月份的 blob（每个都是一次 2~4 MB JSON 反序列化）。缓存的失效语义很容易被
后续改动破坏，而这些破坏不会在功能上表现出来（只会读到过期数据），因此单独测。
"""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from module.statistics.cl1_database import Cl1Database


class TestCl1ReadCache(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        db_path = Path(self.temporary_directory.name) / "cl1_data.db"
        self.db = Cl1Database(db_path=db_path)
        # 写一份初始数据（此时缓存未开启）
        self.db.save_stats("probe", "2026-09", {"battle_count": 1})

    def test_cache_is_off_by_default(self):
        """默认关闭：其它调用方（含写路径）行为完全不变。"""
        self.assertFalse(self.db._read_cache_enabled)

    def test_second_read_returns_the_cached_object(self):
        with self.db.read_cache():
            first = self.db.get_stats("probe", "2026-09")
            second = self.db.get_stats("probe", "2026-09")
            self.assertIs(first, second)

    def test_cache_is_disabled_and_cleared_on_exit(self):
        with self.db.read_cache():
            self.db.get_stats("probe", "2026-09")
            self.assertTrue(self.db._read_cache_enabled)
            self.assertEqual(1, len(self.db._read_cache))
        self.assertFalse(self.db._read_cache_enabled)
        self.assertEqual(0, len(self.db._read_cache))

    def test_cache_is_cleared_even_when_body_raises(self):
        """异常路径也必须关掉：忘了关会让写路径读到旧数据。"""
        with self.assertRaises(RuntimeError):
            with self.db.read_cache():
                self.db.get_stats("probe", "2026-09")
                raise RuntimeError("boom")
        self.assertFalse(self.db._read_cache_enabled)
        self.assertEqual(0, len(self.db._read_cache))

    def test_write_invalidates_cache(self):
        """save_stats 是唯一写入口，写完必须清缓存。"""
        with self.db.read_cache():
            self.db.get_stats("probe", "2026-09")
            self.assertEqual(1, len(self.db._read_cache))
            self.db.save_stats("probe", "2026-09", {"battle_count": 2})
            self.assertEqual(0, len(self.db._read_cache))
            self.assertEqual(2, self.db.get_stats("probe", "2026-09")["battle_count"])

    def test_external_write_is_detected_by_db_signature(self):
        """worker 是另一个进程，进程内失效看不到它 —— 靠库文件签名识别。"""
        with self.db.read_cache():
            self.assertEqual(1, self.db.get_stats("probe", "2026-09")["battle_count"])
            # 绕过本实例直接改库，模拟外部进程写入（同时改动大小与 mtime）
            with closing(sqlite3.connect(self.db.db_path)) as conn:
                conn.execute(
                    "UPDATE cl1_data SET data_json = ? WHERE instance = ? AND month = ?",
                    ('{"battle_count":99,"ap_snapshots":[]}', "probe", "2026-09"),
                )
                conn.commit()
            self.assertEqual(99, self.db.get_stats("probe", "2026-09")["battle_count"])

    def test_cache_expires_after_ttl(self):
        """超过 TTL 必须重新查库，避免长期显示旧数据。"""
        with self.db.read_cache():
            self.db.get_stats("probe", "2026-09")
            key = ("probe", "2026-09")
            signature, _timestamp, data = self.db._read_cache[key]
            # 把缓存时刻改成很久以前
            from datetime import datetime, timedelta

            self.db._read_cache[key] = (
                signature,
                datetime.now() - timedelta(seconds=self.db.READ_CACHE_TTL + 1),
                data,
            )
            with closing(sqlite3.connect(self.db.db_path)) as conn:
                conn.execute(
                    "UPDATE cl1_data SET data_json = ? WHERE instance = ? AND month = ?",
                    ('{"battle_count":77,"ap_snapshots":[]}', "probe", "2026-09"),
                )
                conn.commit()
            self.assertEqual(77, self.db.get_stats("probe", "2026-09")["battle_count"])


if __name__ == "__main__":
    unittest.main()
