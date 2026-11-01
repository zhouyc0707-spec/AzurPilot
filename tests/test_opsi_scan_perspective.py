"""连续实拍透视匹配与正式相机更新链回归；不连接设备。"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image

from module.map_detection.os_grid import OSGrid
from module.os.map import OSMap
from module.os.map_base import OSCampaignMap
from module.os.scan_movement import predict_scan_swipe
from tests.test_opsi_scan_movement import FIXTURES, ScanConfig, screenshot_view


class ScreenshotDevice:
    """设备替身只记录滑动并依次提供既有实拍，不能生成新画面。"""
    def __init__(self, frames):
        self.image = frames[0]
        self.frames = iter(frames[1:])
        self.swipes = []
        self.screenshot_count = 0
        self._screenshot_interval = SimpleNamespace(clear=lambda: None)

    def swipe_vector(self, vector, **kwargs):
        self.swipes.append(tuple(vector))

    def screenshot(self):
        # 截图不足即失败，不能假造尚未有现场证据的 G17 最后一步。
        self.image = next(self.frames)
        self.screenshot_count += 1


class TestScanPerspectiveEvidence(unittest.TestCase):
    def setUp(self):
        for name in ('module.os.scan_movement.logger', 'module.map_detection.view.logger',
                     'module.os.camera.logger', 'module.map.camera.logger'):
            self.enterContext(patch(name))

    def test_every_consecutive_real_pair_matches_without_fleet_detection(self):
        views = [screenshot_view(f'recurrence_{index}.png') for index in range(5)]
        expected = ((3, -1), (2, 0), (0, -2), (0, -2))
        with patch.object(OSGrid, 'predict_current_fleet', return_value=False):
            for index, movement in enumerate(expected):
                with self.subTest(pair=index):
                    previous, current = views[index:index + 2]
                    self.assertFalse(previous.config.MAP_SWIPE_PREDICT)
                    self.assertEqual(previous.config.MAP_SWIPE_DROP, 0.35)
                    self.assertIsNone(previous.predict_swipe(
                        current, with_current_fleet=True, with_sea_grids=False))
                    self.assertEqual(predict_scan_swipe(previous, current), movement)

    def test_each_real_frame_without_fleet_is_zero_movement(self):
        with patch.object(OSGrid, 'predict_current_fleet', return_value=False):
            for index in range(5):
                with self.subTest(frame=index):
                    previous = screenshot_view(f'recurrence_{index}.png')
                    current = screenshot_view(f'recurrence_{index}.png')
                    self.assertEqual(predict_scan_swipe(previous, current), (0, 0))

    def test_production_camera_chain_reaches_only_targets_supported_by_real_frames(self):
        frames = []
        for index in range(5):
            with Image.open(FIXTURES / f'recurrence_{index}.png') as image:
                frames.append(np.array(image.convert('RGB')))
        runner = OSMap.__new__(OSMap)
        runner.config = ScanConfig()
        runner.config.DEVICE_CONTROL_METHOD = 'adb'
        runner.map = OSCampaignMap('连续现场')
        runner.map.shape = 'J25'
        runner.device = ScreenshotDevice(frames)
        runner.camera = (0, 0)
        # 脱敏遮罩清掉了页面按钮；只固定已知“在地图”的页面前提。
        # map_swipe、update、_update_view、View.load、浅拷贝及实际位移核验均为生产代码。
        runner.is_in_map = lambda: True
        runner._view_init()
        self.assertTrue(runner._update_view())
        runner.ensure_edge_insight()
        self.assertEqual(runner.camera, (1, 22))  # 原图双边锚定的 B23。
        original_view = runner.view
        previous_grids = runner.view.grids
        for index, (target, expected) in enumerate((
                ('E22', (4, 21)), ('G22', (6, 21)), ('G20', (6, 19)), ('G18', (6, 17)))):
            with self.subTest(target=target):
                runner.focus_to_scan_target(target)
                self.assertEqual(runner.camera, expected)
                self.assertEqual(runner.device.screenshot_count, index + 1)
                # 同一个View加载新帧时替换grids，旧快照必须保留前帧用于确认位移。
                self.assertIs(runner.view, original_view)
                self.assertIsNot(runner.view.grids, previous_grids)
                previous_grids = runner.view.grids
                self.assertIsNone(runner._prev_view)
                self.assertIsNone(runner._prev_swipe)
        self.assertEqual(len(runner.device.swipes), 4)
        self.assertFalse(runner.config.MAP_SWIPE_PREDICT)
        self.assertEqual(runner.camera, (6, 17))
        # G17 的后续画面未保存，回归仅证明到达 G18，不声称完整全图已完成。


if __name__ == '__main__':
    unittest.main()
