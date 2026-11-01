"""扫描专用定位与上游相机回退共存，不连接设备。"""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from module.exception import MapDetectionError
from module.os.map import OSMap
from module.os.map_base import OSCampaignMap


class ScanCameraTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.map.camera.logger'))
        self.enterContext(patch('module.os.camera.logger'))
        self.enterContext(patch('module.os.map.logger'))

    def runner(self, observed=None):
        runner = OSMap.__new__(OSMap)
        runner.config = SimpleNamespace(MAP_SWIPE_PREDICT=True, MAP_SWIPE_PREDICT_WITH_CURRENT_FLEET=True,
                                        MAP_SWIPE_PREDICT_WITH_SEA_GRIDS=False, MAP_SWIPE_DROP=0.25)
        runner.map = OSCampaignMap('离线定位')
        runner.map.shape = 'T13'
        runner.camera = (8, 5)
        runner.view = SimpleNamespace(center_offset=np.array((0.5, 0.5)), predict_swipe=Mock(return_value=observed),
                                      left_edge=False, right_edge=False, upper_edge=False, lower_edge=False,
                                      center_loca=(4, 1), shape=(7, 4))
        runner.predict = Mock()
        runner.show_camera = Mock()
        runner.requests = []

        def swipe(vector):
            if not np.any(np.abs(vector) > runner.config.MAP_SWIPE_DROP):
                return False
            runner.requests.append(tuple(vector))
            runner._update_view_data()
            return True
        runner._map_swipe = Mock(side_effect=swipe)
        return runner

    def test_normal_camera_keeps_upstream_requested_vector_fallback(self):
        runner = self.runner()
        runner.focus_to((12, 5))
        self.assertEqual(runner.camera, (12, 5))
        self.assertEqual(runner.requests, [(4, 0)])

    def test_scan_rejects_missing_prediction_instead_of_claiming_target(self):
        runner = self.runner()
        with self.assertRaisesRegex(MapDetectionError, '缺少位移证据'):
            runner.focus_to_scan_target((12, 5))
        self.assertEqual(runner.camera, (8, 5))
        self.assertEqual(runner.requests, [(4, 0)])
        self.assertIsNone(runner._prev_view)
        self.assertIsNone(runner._prev_swipe)
        self.assertTrue(all(call.kwargs == {'with_current_fleet': True, 'with_sea_grids': False}
                            for call in runner.view.predict_swipe.call_args_list))

    def test_confirmed_zero_and_opposite_movement_stop_after_one_swipe(self):
        for observed in ((0, 0), (-1, 0)):
            with self.subTest(observed=observed):
                runner = self.runner(observed)
                with self.assertRaisesRegex(MapDetectionError, '未推进'):
                    runner.focus_to_scan_target((12, 5))
                self.assertEqual(len(runner.requests), 1)

    def test_partial_confirmed_movement_keeps_advancing_until_target(self):
        runner = self.runner((2, 0))
        runner.focus_to_scan_target((12, 5))
        self.assertEqual(runner.camera, (12, 5))
        self.assertEqual(runner.requests, [(4, 0), (2, 0)])

    def test_visible_edge_anchors_requested_axis_without_feature_match(self):
        runner = self.runner()
        runner.view.left_edge = True
        runner.focus_to_scan_target((4, 5))
        self.assertEqual(runner.camera, (4, 5))
        self.assertEqual(runner.requests, [(-4, 0)])

    def test_one_visible_edge_does_not_confirm_other_moving_axis(self):
        runner = self.runner()
        runner.view.left_edge = True
        with self.assertRaisesRegex(MapDetectionError, '缺少位移证据'):
            runner.focus_to_scan_target((4, 8))
        self.assertEqual(runner.camera, (8, 5))

    def test_disabled_prediction_is_not_forced_on_for_scanning(self):
        runner = self.runner((4, 0))
        runner.config.MAP_SWIPE_PREDICT = False
        with self.assertRaisesRegex(MapDetectionError, '缺少位移证据'):
            runner.focus_to_scan_target((12, 5))
        runner.view.predict_swipe.assert_not_called()

    def test_already_reached_centered_target_needs_no_device_swipe(self):
        runner = self.runner()
        runner.focus_to_scan_target((8, 5))
        self.assertEqual(runner.requests, [])
        runner.view.predict_swipe.assert_not_called()

    def test_grid_center_without_progress_is_not_an_unbounded_loop(self):
        runner = self.runner((0, 0))
        runner.view.center_offset = np.array((0.9, 0.5))
        with self.assertRaisesRegex(MapDetectionError, '未推进'):
            runner.focus_to_scan_target((8, 5))
        self.assertEqual(len(runner.requests), 1)

    def test_grid_center_confirmation_keeps_verified_target(self):
        runner = self.runner((0, 0))
        runner.view.center_offset = np.array((0.9, 0.5))
        original_swipe = runner._map_swipe.side_effect

        def center(vector):
            runner.view = copy.copy(runner.view)
            runner.view.center_offset = np.array((0.5, 0.5))
            return original_swipe(vector)
        runner._map_swipe.side_effect = center
        runner.focus_to_scan_target((8, 5))
        self.assertEqual(runner.camera, (8, 5))
        self.assertEqual(len(runner.requests), 1)

    def test_grid_center_phase_alone_cannot_replace_displacement_evidence(self):
        runner = self.runner()
        runner.view.center_offset = np.array((0.9, 0.5))
        original_swipe = runner._map_swipe.side_effect

        def center(vector):
            runner.view = copy.copy(runner.view)
            runner.view.center_offset = np.array((0.5, 0.5))
            return original_swipe(vector)
        runner._map_swipe.side_effect = center
        with self.assertRaisesRegex(MapDetectionError, '缺少位移证据'):
            runner.focus_to_scan_target((8, 5))
        self.assertEqual(runner.camera, (8, 5))
        self.assertEqual(len(runner.requests), 1)

    def test_full_scan_failure_keeps_seen_events_and_pending_recovery(self):
        runner = self.runner()
        pending = {'type': 'akashi', 'node': 'B3'}
        runner.config.pending_map_event = pending.copy()
        runner._solved_map_event = {'is_logging_tower'}
        runner.map_data_init = Mock()
        runner.handle_info_bar = Mock()
        runner.update = Mock()
        runner.map_init = Mock()
        runner.map_rescan_current = Mock(return_value=False)
        runner.focus_to_grid_center = Mock()
        with self.assertRaisesRegex(MapDetectionError, '缺少位移证据'):
            runner.map_rescan_once()
        runner.map_rescan_current.assert_called_once_with(drop=None)
        runner.focus_to_grid_center.assert_not_called()
        self.assertEqual(runner._solved_map_event, {'is_logging_tower'})
        self.assertEqual(runner.config.pending_map_event, pending)


if __name__ == '__main__':
    unittest.main()
