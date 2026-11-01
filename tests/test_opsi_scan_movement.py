"""真实大世界默认配置、脱敏截图与无设备完整扫描回归。"""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from module.config.config_generated import GeneratedConfig
from module.config.config_manual import ManualConfig
from module.exception import MapDetectionError
from module.map_detection.os_grid import OSGrid
from module.map_detection.view import View
from module.os.config import OSConfig
from module.os.map import OSMap
from module.os.map_base import OSCampaignMap
from module.os.scan_movement import predict_scan_swipe


class ScanConfig(OSConfig, ManualConfig, GeneratedConfig):
    # Homography 的遮罩也按任务身份选择，仅 mode='os' 不足。
    Scheduler_Command = 'OpsiExplore'


FIXTURES = Path(__file__).parent / 'fixtures' / 'opsi_scan_movement'
STORAGE = ((10, 7), [(110.307, 103.657), (1012.311, 103.657),
                    (-32.959, 600.567), (1113.057, 600.567)])


def screenshot_view(name):
    view = View(ScanConfig(), mode='os', grid_class=OSGrid)
    view.detector_set_backend('homography')
    view.backend.load_homography(storage=STORAGE)
    with Image.open(FIXTURES / name) as image:
        view.load(np.array(image.convert('RGB')))
    return view


def texture(seed):
    """每个全局格子固定一块非重复纹理，不依赖被测匹配代码。"""
    return np.random.default_rng(seed).integers(0, 256, (16, 16), dtype=np.uint8)


class TextureGrid:
    def __init__(self, location, full):
        self.location = location
        self.config = SimpleNamespace(HOMO_TILE=full.shape[::-1])
        self.image_trans = np.repeat(full[:, :, None], 3, axis=2)
        # 合成纹理放在真实OS遮罩内，生产路径仍检查整个搜索区域是否合法。
        self.homo_data = np.array(((1, 0, -600), (0, 1, -300), (0, 0, 1)), dtype=float)

    def predict_fleet(self):
        return False

    def predict_current_fleet(self):
        return False


def texture_view(grids, center=(0, 0)):
    # 保留真实 View.predict_swipe/select；仅由已知全局图案代替截图检测。
    view = View.__new__(View)
    view.grids = {grid.location: grid for grid in grids}
    view.image = object()
    view.center_loca = center
    view.center_offset = np.array((0.5, 0.5))
    view.shape = (7, 4)
    view.left_edge = view.right_edge = view.upper_edge = view.lower_edge = False
    return view


class TestScanMovementEvidence(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.os.scan_movement.logger'))
        self.enterContext(patch('module.map_detection.view.logger'))

    def test_real_os_defaults_and_masked_screenshots_reproduce_actual_movement(self):
        previous, current = screenshot_view('before.png'), screenshot_view('after.png')
        self.assertFalse(previous.config.MAP_SWIPE_PREDICT)
        self.assertEqual(previous.config.MAP_SWIPE_DROP, 0.35)
        self.assertEqual(previous.center_loca, (1, 4))
        self.assertEqual(current.center_loca, (4, 4))
        self.assertEqual((len(previous.grids), len(current.grids)), (52, 81))
        np.testing.assert_allclose(previous.center_offset, (0.50890036, 0.50640643), atol=1e-7)
        np.testing.assert_allclose(current.center_offset, (0.35890047, 0.55640650), atol=1e-7)
        self.assertEqual((previous.left_edge, previous.upper_edge), (True, True))
        self.assertEqual((current.left_edge, current.right_edge, current.upper_edge, current.lower_edge),
                         (True, False, False, False))
        self.assertEqual(predict_scan_swipe(previous, current), (3, -1))

    def test_same_real_frame_is_zero_movement(self):
        self.assertEqual(predict_scan_swipe(screenshot_view('before.png'), screenshot_view('before.png')), (0, 0))

    def replay_focus(self, name):
        runner = OSMap.__new__(OSMap)
        runner.config = ScanConfig()
        runner.map = OSCampaignMap('J25现场')
        runner.map.shape = 'J25'
        runner.camera = (1, 22)
        runner.view = screenshot_view('before.png')
        runner.predict = Mock()
        runner.show_camera = Mock()
        with Image.open(FIXTURES / name) as image:
            frame = np.array(image.convert('RGB'))

        def swipe(vector):
            # 重用真实 View.load，验证生产路径浅拷贝仍持有上一帧图像和格子。
            runner.view.load(frame)
            runner._update_view_data()
            return True

        runner._map_swipe = Mock(side_effect=swipe)
        return runner

    def test_real_focus_entry_reaches_e22_with_default_os_configuration(self):
        runner = self.replay_focus('after.png')
        runner.focus_to_scan_target((4, 21))
        self.assertEqual(runner.camera, (4, 21))
        self.assertFalse(runner.config.MAP_SWIPE_PREDICT)
        runner._map_swipe.assert_called_once()
        np.testing.assert_allclose(runner._map_swipe.call_args.args[0], (2.99109964, -1.00640643), atol=1e-7)
        self.assertIsNone(runner._prev_view)

    def test_repeated_real_frame_cannot_satisfy_requested_target(self):
        runner = self.replay_focus('before.png')
        with self.assertRaisesRegex(MapDetectionError, '未推进'):
            runner.focus_to_scan_target((4, 21))
        self.assertEqual(runner.camera, (1, 22))
        runner._map_swipe.assert_called_once()

    def test_real_grid_evidence_remains_when_fleet_detection_is_unavailable(self):
        previous, current = screenshot_view('before.png'), screenshot_view('after.png')
        # 原图仍含舰队，不把停用识别声称为实拍舰队已出视野。
        with patch.object(OSGrid, 'predict_current_fleet', return_value=False):
            self.assertIsNone(previous.predict_swipe(current, with_current_fleet=True, with_sea_grids=False))
            self.assertEqual(predict_scan_swipe(previous, current), (3, -1))

    def test_multiple_unique_textures_without_any_fleet_confirm_movement(self):
        previous = texture_view([TextureGrid((x, 0), texture(x)) for x in range(3)])
        current = texture_view([TextureGrid((x - 3, 1), texture(x)) for x in range(3)])
        self.assertEqual(predict_scan_swipe(previous, current), (3, -1))

    def test_single_texture_cannot_confirm_movement(self):
        previous = texture_view([TextureGrid((0, 0), texture(1))])
        current = texture_view([TextureGrid((-2, 0), texture(1))])
        self.assertIsNone(predict_scan_swipe(previous, current))

    def test_constant_textures_cannot_confirm_movement(self):
        previous = texture_view([TextureGrid((x, 0), np.full((16, 16), x, np.uint8)) for x in range(3)])
        current = texture_view([TextureGrid((x - 2, 0), np.full((16, 16), x, np.uint8)) for x in range(3)])
        self.assertIsNone(predict_scan_swipe(previous, current))

    def test_repeated_textures_cannot_confirm_movement(self):
        previous = texture_view([TextureGrid((x, 0), texture(1)) for x in range(3)])
        current = texture_view([TextureGrid((x - 2, 0), texture(1)) for x in range(3)])
        self.assertIsNone(predict_scan_swipe(previous, current))

    def test_one_way_best_matches_are_not_accepted_as_mutual(self):
        a, b, c = [texture(seed) for seed in range(3)]
        previous = texture_view([TextureGrid((0, 0), a), TextureGrid((1, 0), a), TextureGrid((2, 0), b)])
        current = texture_view([TextureGrid((-2, 0), a), TextureGrid((-1, 0), b), TextureGrid((0, 0), c)])
        # 两个A都单向匹配同一个A，反向无法唯一确定；只剩B这一对，不能通过。
        self.assertIsNone(predict_scan_swipe(previous, current))

    def test_ui_or_screen_boundary_cannot_supply_matching_evidence(self):
        for source_origin in ((0, 0), (-8, 300), (1272, 300), (600, 712)):
            with self.subTest(source_origin=source_origin):
                previous = texture_view([TextureGrid((x, 0), texture(x)) for x in range(3)])
                current = texture_view([TextureGrid((x - 2, 0), texture(x)) for x in range(3)])
                x, y = source_origin
                for grid in previous:
                    grid.homo_data[:2, 2] = (-x, -y)
                self.assertIsNone(predict_scan_swipe(previous, current))

    def test_partial_ui_mask_inside_full_search_area_is_rejected(self):
        previous = texture_view([TextureGrid((x, 0), texture(x)) for x in range(3)])
        current = texture_view([TextureGrid((x - 2, 0), texture(x)) for x in range(3)])
        mask = np.full((720, 1280), 255, np.uint8)
        # 中央模板仍可见，但搜索区域左侧仅一个像素属于UI，也不得拿来匹配。
        mask[300:316, 600] = 0
        with patch('module.os.scan_movement.ASSETS', SimpleNamespace(ui_mask_os_in_map=mask)):
            self.assertIsNone(predict_scan_swipe(previous, current))

    def test_conflicting_unique_texture_movements_are_rejected(self):
        previous = texture_view([TextureGrid((x, 0), texture(x)) for x in range(3)])
        current = texture_view([TextureGrid((-2, 0), texture(0)), TextureGrid((-1, 0), texture(1)),
                                TextureGrid((0, 1), texture(2))])
        self.assertIsNone(predict_scan_swipe(previous, current))


class TestFullScanWithImageEvidence(unittest.TestCase):
    def setUp(self):
        self.map_logger = self.enterContext(patch('module.os.map.logger'))
        for name in ('module.os.camera.logger', 'module.map.camera.logger',
                     'module.os.scan_movement.logger', 'module.map_detection.view.logger'):
            self.enterContext(patch(name))

    def make_runner(self, shape, lose_evidence_at=None):
        runner = OSMap.__new__(OSMap)
        runner.config = ScanConfig()
        runner.config.pending_map_event = {'type': 'akashi', 'node': 'B3'}
        runner.map = OSCampaignMap('全局纹理模拟')
        runner.map.shape = shape
        runner.physical_camera = (4, 1)
        runner.camera = runner.physical_camera
        runner.swipes = []
        runner.checked = []
        runner._solved_map_event = {'is_logging_tower'}

        def render(blank=False):
            cx, cy = runner.physical_camera
            grids = []
            for y in range(cy - 1, cy + 4):
                for x in range(cx - 4, cx + 4):
                    full = np.zeros((16, 16), np.uint8) if blank else texture(1000 + y * 100 + x)
                    grids.append(TextureGrid((x - cx + 4, y - cy + 1), full))
            return texture_view(grids, center=(4, 1))

        runner.view = render()
        runner.render = render

        def physical_swipe(vector):
            if not np.any(np.abs(vector) > runner.config.MAP_SWIPE_DROP):
                return False
            vector = tuple(np.rint(vector).astype(int))
            runner.swipes.append(vector)
            # 模拟拖动被截短：实际最多走 (2,1)，不把请求量当作已到达。
            actual = np.minimum(np.abs(vector), (2, 1)) * np.sign(vector)
            runner.physical_camera = tuple(np.add(runner.physical_camera, actual))
            runner.view = render(blank=len(runner.swipes) == lose_evidence_at)
            # 保留通用相机请求量积分；严格扫描必须从独立图案重新核验它。
            runner._update_view_data()
            return True

        def inspect(**kwargs):
            self.assertEqual(runner.camera, runner.physical_camera)
            runner.checked.append(runner.physical_camera)
            return False

        runner._map_swipe = physical_swipe
        runner.map_data_init = Mock()
        runner.handle_info_bar = Mock()
        runner.update = Mock()
        runner.map_init = Mock()
        runner.predict = Mock()
        runner.show_camera = Mock()
        runner.map_rescan_current = inspect
        return runner

    def test_all_nine_and_ten_targets_require_actual_matching_views(self):
        for shape, count in (('T13', 9), ('J25', 10)):
            with self.subTest(shape=shape):
                runner = self.make_runner(shape)
                self.assertFalse(runner.map_rescan_once())
                self.assertFalse(runner.config.MAP_SWIPE_PREDICT)
                self.assertEqual(len(runner.map.camera_data), count)
                self.assertEqual(len(runner.checked), count + 1)
                self.assertEqual(set(runner.checked[1:]), set(runner.map.camera_data.location))
                self.assertTrue(runner.swipes)
                self.assertTrue(all(abs(x) <= 3 and abs(y) <= 2 for x, y in runner.swipes))
                self.assertEqual(runner.camera, runner.physical_camera)

    def test_missing_evidence_mid_scan_preserves_events_and_never_completes(self):
        runner = self.make_runner('T13', lose_evidence_at=5)
        with self.assertRaisesRegex(MapDetectionError, '缺少位移证据'):
            runner.map_rescan_once()
        self.assertGreater(len(runner.checked), 1)
        self.assertLess(len(runner.checked), len(runner.map.camera_data) + 1)
        self.assertNotEqual(runner.camera, runner.physical_camera)
        self.assertEqual(runner._solved_map_event, {'is_logging_tower'})
        self.assertEqual(runner.config.pending_map_event, {'type': 'akashi', 'node': 'B3'})
        self.assertFalse(any('扫描完成' in str(call) for call in self.map_logger.info.call_args_list))

    def event_move_runner(self):
        runner = self.make_runner('T13')
        inspect = runner.map_rescan_current

        def interrupted_event(**kwargs):
            result = inspect(**kwargs)
            if len(runner.checked) == 2:
                # 模拟事件没有完成，但舰队行走带动镜头，update_os 未改绝对坐标。
                runner.physical_camera = tuple(np.add(runner.physical_camera, (1, 1)))
                runner.view = runner.render()
            return result

        runner.map_rescan_current = interrupted_event
        runner.ensure_edge_insight = Mock(side_effect=lambda **kwargs: setattr(runner, 'camera', runner.physical_camera))
        return runner

    def test_failed_event_reanchors_changed_view_before_next_target(self):
        runner = self.event_move_runner()
        self.assertFalse(runner.map_rescan_once())
        runner.ensure_edge_insight.assert_called_once_with(skip_first_update=False)
        self.assertEqual(set(runner.checked[1:]), set(runner.map.camera_data.location))
        self.assertEqual(runner.camera, runner.physical_camera)

    def test_failed_reanchor_preserves_pending_event_and_incomplete_scan(self):
        runner = self.event_move_runner()
        runner.ensure_edge_insight.side_effect = MapDetectionError('无法恢复绝对坐标')
        with self.assertRaisesRegex(MapDetectionError, '无法恢复绝对坐标'):
            runner.map_rescan_once()
        self.assertEqual(len(runner.checked), 2)
        self.assertEqual(runner.config.pending_map_event, {'type': 'akashi', 'node': 'B3'})
        self.assertEqual(runner._solved_map_event, {'is_logging_tower'})
        self.assertFalse(any('扫描完成' in str(call) for call in self.map_logger.info.call_args_list))


if __name__ == '__main__':
    unittest.main()
