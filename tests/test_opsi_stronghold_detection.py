"""回放旋涡动画暗帧，并验证候选点击不会污染同屏其余海域的取色坐标。"""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from module.map.map_grids import SelectedGrids
from module.os.globe_camera import GlobeCamera


class StrongholdDetectionTests(unittest.TestCase):
    def make_camera(self, image):
        camera = GlobeCamera.__new__(GlobeCamera)
        camera.device = SimpleNamespace(image=image)
        zone = SimpleNamespace(location=np.array((41.5, 44.5)))
        camera.name_to_zone = Mock(return_value=zone)
        # 裁剪保留原像素；这里仅把地图坐标转换到裁剪后的局部屏幕坐标。
        camera.globe2screen = lambda points: np.round(np.array(points) * (1, 0.625) + (0, 4.5))
        return camera

    def test_live_vortex_light_and_dark_frames_are_all_detected(self):
        path = Path(__file__).parent / 'fixtures' / 'opsi_stronghold' / 'vortex_frames.png'
        with Image.open(path) as source:
            frames = np.array(source.convert('RGB'))
        self.assertEqual(frames.shape, (48, 64 * 12, 3))
        for index in range(12):
            with self.subTest(frame=index):
                image = frames[:, index * 64:(index + 1) * 64]
                self.assertTrue(self.make_camera(image)._globe_predict_stronghold(141))

    def test_blue_map_does_not_become_a_candidate(self):
        image = np.full((48, 64, 3), (35, 75, 125), dtype=np.uint8)
        self.assertFalse(self.make_camera(image)._globe_predict_stronghold(1))

    def test_isolated_red_pixel_does_not_become_a_candidate(self):
        image = np.full((48, 64, 3), (35, 75, 125), dtype=np.uint8)
        image[24, 32] = (255, 0, 80)
        self.assertFalse(self.make_camera(image)._globe_predict_stronghold(1))

    def test_red_outside_search_neighborhood_is_ignored(self):
        image = np.full((48, 64, 3), (35, 75, 125), dtype=np.uint8)
        image[:, 58:] = (255, 0, 80)
        self.assertFalse(self.make_camera(image)._globe_predict_stronghold(1))

    def test_dim_red_region_keeps_original_brightness_threshold(self):
        image = np.full((48, 64, 3), (100, 20, 50), dtype=np.uint8)
        self.assertFalse(self.make_camera(image)._globe_predict_stronghold(1))

    def test_incomplete_color_window_is_rejected(self):
        camera = self.make_camera(np.zeros((48, 64, 3), dtype=np.uint8))
        camera.image_crop = Mock(return_value=np.zeros((2, 2, 3), dtype=np.uint8))
        self.assertFalse(camera._globe_predict_stronghold(1))


class StrongholdCandidateScanTests(unittest.TestCase):
    def make_camera(self, predicted, types):
        camera = GlobeCamera.__new__(GlobeCamera)
        zones = SelectedGrids([
            SimpleNamespace(zone_id=index, location=(index * 100, 300))
            for index in (1, 2)
        ])
        events = []
        camera.globe_camera = (0, 0)
        camera.camera_to_zone = Mock(return_value=SimpleNamespace(location=(0, 0)))
        camera.globe_in_sight = Mock()
        camera.globe2screen = lambda points: np.array(points)

        def predict(zone):
            self.assertFalse(any(event[0] == 'focus' for event in events),
                             '本批初筛必须在点击改变视角前全部完成')
            events.append(('predict', zone.zone_id))
            return predicted[zone.zone_id - 1]

        camera._globe_predict_stronghold = Mock(side_effect=predict)
        camera.globe_focus_to = Mock(side_effect=lambda zone: events.append(('focus', zone.zone_id)))
        camera.get_zone_pinned_name = Mock(side_effect=types)
        camera.ensure_no_zone_pinned = Mock(side_effect=lambda: events.append(('unpin',)))
        camera.globe_update = Mock(side_effect=lambda: events.append(('update',)))
        return camera, zones, events

    @patch('module.os.globe_camera.logger')
    def test_false_candidate_cannot_hide_next_real_stronghold(self, _logger):
        camera, zones, events = self.make_camera([True, True], ['SAFE', 'STRONGHOLD'])
        result = camera._find_siren_stronghold(zones)
        self.assertIs(result, zones[1])
        self.assertEqual(events, [
            ('predict', 1), ('predict', 2), ('focus', 1),
            ('unpin',), ('update',), ('focus', 2),
        ])

    @patch('module.os.globe_camera.logger')
    def test_color_candidate_still_requires_stronghold_panel(self, _logger):
        camera, zones, _ = self.make_camera([True, False], ['SAFE'])
        self.assertIsNone(camera._find_siren_stronghold(zones))
        camera.globe_focus_to.assert_called_once_with(zones[0])
        camera.globe_update.assert_called_once_with()

    @patch('module.os.globe_camera.logger')
    def test_negative_batch_does_not_add_screenshots_or_clicks(self, _logger):
        camera, zones, _ = self.make_camera([False, False], [])
        self.assertIsNone(camera._find_siren_stronghold(zones))
        camera.globe_focus_to.assert_not_called()
        camera.globe_update.assert_not_called()


if __name__ == '__main__':
    unittest.main()
