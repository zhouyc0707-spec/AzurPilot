"""用虚拟视图回放定位结果，复用相机坐标更新与设备操作检测。"""

import collections
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from module.device.device import Device
from module.exception import GameStuckError, MapDetectionError
from module.map.camera import Camera
from module.map_detection.view import View


def view():
    return SimpleNamespace(
        center_offset=np.array([0.5, 0.5]), center_loca=(5, 4), shape=(10, 8),
        left_edge=False, right_edge=False, upper_edge=False, lower_edge=False,
        predict_swipe=Mock(return_value=None),
    )


class ReplayCamera(Camera):
    def __init__(self, movement=None):
        self.camera = (0, 0)
        self.view = view()
        self.map = SimpleNamespace(shape=(30, 30))
        self.config = SimpleNamespace(
            MAP_SWIPE_DROP=0.25, MAP_SWIPE_PREDICT=True,
            MAP_SWIPE_PREDICT_WITH_CURRENT_FLEET=True, MAP_SWIPE_PREDICT_WITH_SEA_GRIDS=False,
            MAP_ENSURE_EDGE_INSIGHT_CORNER='bottom_left',
        )
        self.device = Device.__new__(Device)
        self.device.click_record = collections.deque(maxlen=50)
        self.device.stuck_record_clear = Mock()
        self.predict = Mock()
        self.update = Mock()
        self.swipes = []
        self.movement = movement or (lambda requested: requested)

    def _map_swipe(self, vector):
        if np.all(np.abs(vector) <= self.config.MAP_SWIPE_DROP):
            return False
        requested = self._prev_swipe
        self.device.handle_control_check('MAP_SWIPE_' + '_'.join(map(str, requested)))
        self.swipes.append(requested)
        observed = self.movement(requested)
        self._prev_view.predict_swipe.return_value = observed
        self.view = view()
        self._update_view_data()
        return True


class CameraProgressTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.map.camera.logger'))

    def test_normal_patrol_can_cross_regions_more_than_fifty_times(self):
        camera = ReplayCamera()
        for _ in range(30):
            camera.focus_to((16, 12))
            camera.focus_to((0, 0))
        self.assertGreater(len(camera.swipes), 50)
        self.assertEqual(camera.camera, (0, 0))
        self.assertEqual(list(camera.device.click_record), [])

    def test_no_actual_movement_recovers_once_then_exits(self):
        camera = ReplayCamera(lambda requested: (0, 0))
        camera.ensure_edge_insight = Mock()
        with self.assertRaisesRegex(GameStuckError, '连续三次无已确认的定位进展'):
            camera.focus_to((8, 6))
        camera.ensure_edge_insight.assert_called_once_with(skip_first_update=False)
        self.assertEqual(len(camera.swipes), 6)
        self.assertFalse(camera._camera_confirm_swipe)

    def test_unconfirmed_prediction_cannot_claim_to_have_reached_target(self):
        camera = ReplayCamera(lambda requested: None)
        camera.ensure_edge_insight = Mock()
        with self.assertRaises(GameStuckError):
            camera.focus_to((4, 3))
        self.assertEqual(camera.camera, (0, 0))
        self.assertFalse(camera._last_swipe_confirmed)
        self.assertEqual(len(camera.device.click_record), 6)

    def test_oscillation_is_detected(self):
        camera = ReplayCamera(lambda requested: (4, 0) if camera.camera == (0, 0) else (-4, 0))
        camera.ensure_edge_insight = Mock()
        with self.assertRaisesRegex(GameStuckError, '相机坐标反复震荡'):
            camera.focus_to((10, 0))
        self.assertEqual(len(camera.swipes), 8)

    def test_relocalization_can_restore_progress(self):
        camera = ReplayCamera(lambda requested: (0, 0))
        def recover(**kwargs):
            camera.movement = lambda requested: requested
        camera.ensure_edge_insight = Mock(side_effect=recover)
        camera.focus_to((8, 6))
        self.assertEqual(camera.camera, (8, 6))
        camera.ensure_edge_insight.assert_called_once()

    def test_recognition_error_attempts_relocalization_before_failing(self):
        camera = ReplayCamera()
        camera.map_swipe = Mock(side_effect=MapDetectionError('bad grid'))
        camera.ensure_edge_insight = Mock(side_effect=MapDetectionError('no edge'))
        with self.assertRaisesRegex(GameStuckError, '重新定位失败'):
            camera.focus_to((8, 6))
        camera.ensure_edge_insight.assert_called_once()
        self.assertIsNone(camera._prev_swipe)
        self.assertFalse(camera._camera_confirm_swipe)

    def test_even_continuous_small_progress_has_a_maximum_attempt_count(self):
        camera = ReplayCamera(lambda requested: (1, 0))
        camera.ensure_edge_insight = Mock()
        with self.assertRaisesRegex(GameStuckError, '超过最大定位尝试次数'):
            camera.focus_to((1000, 0))
        self.assertEqual(len(camera.swipes), 40)

    def test_real_edge_recovery_is_bounded_when_map_cannot_move(self):
        camera = ReplayCamera(lambda requested: (0, 0))
        with self.assertRaisesRegex(GameStuckError, '边缘重新定位失败'):
            camera.focus_to((8, 6))
        self.assertEqual(len(camera.swipes), 15)
        camera.update.assert_called_once()

    def test_whole_grid_motion_does_not_require_a_change_in_modulo_offset(self):
        camera = ReplayCamera()
        def swipe(vector):
            camera.view.backend = SimpleNamespace(homo_loca=(50, 50))
            camera.swipes.append(vector)
            if len(camera.swipes) == 2:
                camera.view.left_edge = camera.view.lower_edge = True
            return True
        camera.map_swipe = swipe
        camera.ensure_edge_insight()
        self.assertEqual(len(camera.swipes), 2)

    def test_grid_matching_is_used_when_current_fleet_is_out_of_view(self):
        camera = ReplayCamera()
        camera._camera_confirm_swipe = True
        camera._prev_view = view()
        camera._prev_view.predict_swipe.side_effect = [None, (4, 3)]
        previous = camera._prev_view
        camera._prev_swipe = (6, 5)
        camera._update_view_data()
        self.assertEqual(camera.camera, (4, 3))
        previous.predict_swipe.assert_called_with(camera.view, with_current_fleet=False, with_sea_grids=True)

    def test_visible_edges_can_anchor_a_move_without_grid_matching(self):
        camera = ReplayCamera()
        camera._camera_confirm_swipe = True
        camera._prev_view = view()
        camera._prev_swipe = (4, 3)
        camera.view.left_edge = camera.view.lower_edge = True
        camera._update_view_data()
        self.assertTrue(camera._last_swipe_confirmed)
        self.assertEqual(camera.camera, (5, 4))

    def test_grid_matcher_reports_zero_for_unchanged_map_features(self):
        # 使用真实匹配投票逻辑；各网格只匹配自己的稳定地标。
        def feature_view():
            result = View.__new__(View)
            result.center_loca = (5, 4)
            result.grids = {}
            for index in range(3):
                result.grids[(index, 0)] = SimpleNamespace(
                    is_similar_to=lambda other, index=index: other.feature == index, feature=index,
                )
            return result
        observed = feature_view().predict_swipe(feature_view(), with_current_fleet=False)
        self.assertEqual(observed, (0, 0))


if __name__ == '__main__':
    unittest.main()
