"""设备ID 首次获取不应阻塞在硬件指纹采集上。

设备ID 是 opsi_items 等表的归属键（查询都带 device_id 条件），换掉就读不到历史
数据，所以它必须保持稳定；但采集指纹要跑 4 次 wmic 子进程（实测 0.5~1.1 s），
若放在首个 WebUI 页面渲染路径上，首屏会凭空多等近一秒。
"""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from module.base import device_id as did


class TestDeviceIdFastPath(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.file = Path(self._tmp.name) / "device_id.json"
        self.file.write_text(
            json.dumps({"device_id": "cached00000000000000000000000abcde"}),
            encoding="utf-8",
        )
        # 隔离进程级缓存，避免测试之间互相影响
        patcher = patch.object(did, "_device_id", None)
        patcher.start()
        self.addCleanup(patcher.stop)
        old_patcher = patch.object(did, "_old_device_id", None)
        old_patcher.start()
        self.addCleanup(old_patcher.stop)
        timer_patcher = patch.object(did, "_refresh_timer", None)
        timer_patcher.start()
        self.addCleanup(timer_patcher.stop)
        file_patcher = patch.object(did, "_device_id_file", lambda: self.file)
        file_patcher.start()
        self.addCleanup(file_patcher.stop)

    def test_stored_id_is_returned_without_waiting_for_fingerprint(self):
        """指纹采集再慢，首次取设备ID 也应立即返回已登记的值。"""

        def slow_fingerprint():
            time.sleep(0.4)
            return "cached00000000000000000000000abcde"

        started = time.perf_counter()
        with (
            patch.object(did, "generate_device_id", side_effect=slow_fingerprint),
            patch.object(did, "_start_refresh_timer"),
        ):
            value = did.get_device_id()
            elapsed = time.perf_counter() - started
            self.assertEqual("cached00000000000000000000000abcde", value)
            self.assertLess(elapsed, 0.2, "首次取设备ID 不应等待硬件指纹采集")
            # 后续调用同样走缓存
            self.assertEqual(value, did.get_device_id())

    def test_hardware_change_still_detected_in_background(self):
        """后台核对发现指纹变化时，应把缓存值记为旧 ID 供数据库迁移。"""
        done = threading.Event()

        def fake_refresh(*args, **kwargs):
            done.set()

        with (
            patch.object(did, "generate_device_id", return_value="newid0000000000000000000000000abc"),
            patch.object(did, "_start_refresh_timer", side_effect=fake_refresh),
        ):
            self.assertEqual(
                "cached00000000000000000000000abcde", did.get_device_id()
            )
            self.assertTrue(done.wait(timeout=2), "后台核对线程未运行")

        # 迁移用的旧 ID 必须保留原登记值
        self.assertEqual(
            "cached00000000000000000000000abcde", did.get_old_device_id()
        )
        self.assertEqual(
            "newid0000000000000000000000000abc", did.get_device_id()
        )
        stored = json.loads(self.file.read_text(encoding="utf-8"))
        self.assertEqual("newid0000000000000000000000000abc", stored["device_id"])

    def test_missing_file_falls_back_to_sync_generation(self):
        """全新安装没有缓存文件时，仍要同步生成，不能返回空设备ID。"""
        self.file.unlink()
        with (
            patch.object(
                did, "generate_device_id", return_value="fresh00000000000000000000000abcde"
            ),
            patch.object(did, "_start_refresh_timer"),
        ):
            self.assertEqual(
                "fresh00000000000000000000000abcde", did.get_device_id()
            )
        stored = json.loads(self.file.read_text(encoding="utf-8"))
        self.assertEqual(
            "fresh00000000000000000000000abcde", stored["device_id"]
        )

    def test_corrupt_file_falls_back_to_sync_generation(self):
        """缓存文件损坏时按没有缓存处理。"""
        self.file.write_text("{ not json", encoding="utf-8")
        with (
            patch.object(
                did, "generate_device_id", return_value="fresh00000000000000000000000abcde"
            ),
            patch.object(did, "_start_refresh_timer"),
        ):
            self.assertEqual(
                "fresh00000000000000000000000abcde", did.get_device_id()
            )


if __name__ == "__main__":
    unittest.main()
