"""大世界全图扫描与边缘定位回归；不连接设备、不读写真实配置。"""

import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.base.utils import node2location
from module.exception import MapDetectionError
from module.os.camera import OSCamera
from module.os.map import OSMap
from module.os.map_base import OSCampaignMap
from module.os.map_data import DIC_OS_MAP
from module.os.tasks.meowfficer_farming import OpsiMeowfficerFarming


class TestOSMapScanCoverage(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.os.camera.logger'))
        self.enterContext(patch('module.os.map.logger'))
        self.enterContext(patch('module.os.tasks.meowfficer_farming.logger'))

    def make_edge_runner(self, camera=(16, 1), edges=(False, False, True, False)):
        runner = OSCamera.__new__(OSCamera)
        runner.config = SimpleNamespace(MAP_ENSURE_EDGE_INSIGHT_CORNER='upper-left')
        runner.map = SimpleNamespace(shape=node2location('T13'))
        runner.camera = camera
        runner.view = SimpleNamespace(
            left_edge=edges[0], right_edge=edges[1], lower_edge=edges[2], upper_edge=edges[3],
            backend=SimpleNamespace(homo_loca=(53, 60)),
            shape=(7, 4), center_loca=(4, 1),
        )
        runner.update = Mock()
        runner.map_swipe = Mock()
        return runner

    @staticmethod
    def physical_swipe(runner, vector):
        """模拟整格移动：格内相位不变，只有实际进入边缘视野才出现边线。"""
        x, y = runner.camera
        dx, dy = vector
        runner.camera = (min(16, max(4, x + dx)), min(9, max(1, y + dy)))
        x, y = runner.camera
        runner.view.left_edge = x <= 4
        runner.view.right_edge = x >= 16
        runner.view.lower_edge = y <= 1
        runner.view.upper_edge = y >= 9

    def make_scan_runner(self):
        runner = OSMap.__new__(OSMap)
        runner.map = OSCampaignMap('离线侵蚀五')
        runner.map.shape = 'T13'
        runner.camera = (10, 6)
        runner.map_data_init = Mock()
        runner.handle_info_bar = Mock()
        runner.update = Mock()
        runner.map_init = Mock()
        runner.focus_to = Mock(side_effect=lambda target, **kwargs: setattr(runner, 'camera', target.location))
        runner.focus_to_grid_center = Mock()
        runner.map_rescan_current = Mock(return_value=False)
        return runner

    def test_unchanged_grid_phase_does_not_end_before_real_horizontal_edge(self):
        """先有纵边，连续移动多格相位仍相同，必须等到真实横边出现。"""
        runner = self.make_edge_runner()
        runner.map_swipe.side_effect = lambda vector: self.physical_swipe(runner, vector)
        record = runner.ensure_edge_insight()
        self.assertEqual(record, [(-4, 0), (-4, 0), (-4, 0)])
        self.assertEqual(runner.camera, (4, 1))
        self.assertEqual(runner.view.backend.homo_loca, (53, 60))
        self.assertTrue(runner.view.left_edge and runner.view.lower_edge)
        runner.update.assert_not_called()

    def test_missing_horizontal_edge_fails_after_bounded_swipes(self):
        """滑动一直无横边时禁止伪装定位成功。"""
        runner = self.make_edge_runner()
        with self.assertRaises(MapDetectionError):
            runner.ensure_edge_insight()
        self.assertGreater(runner.map_swipe.call_count, 1)
        self.assertLessEqual(runner.map_swipe.call_count, 12)
        self.assertTrue(all(call.args[0] == (-4, 0) for call in runner.map_swipe.call_args_list))

    def test_missing_both_axes_fails_after_bounded_swipes(self):
        runner = self.make_edge_runner(edges=(False, False, False, False))
        with self.assertRaises(MapDetectionError):
            runner.ensure_edge_insight()
        self.assertLessEqual(runner.map_swipe.call_count, 12)
        self.assertTrue(all(call.args[0] == (-4, -3) for call in runner.map_swipe.call_args_list))

    def test_already_visible_both_edges_needs_no_swipe(self):
        for horizontal in (0, 1):
            for vertical in (2, 3):
                with self.subTest(horizontal=horizontal, vertical=vertical):
                    edges = [False] * 4
                    edges[horizontal] = edges[vertical] = True
                    runner = self.make_edge_runner(edges=edges)
                    self.assertEqual(runner.ensure_edge_insight(), [])
                    runner.map_swipe.assert_not_called()

    def test_visible_edges_correct_stale_absolute_camera(self):
        for horizontal, x in ((0, 4), (1, 16)):
            for vertical, y in ((2, 1), (3, 9)):
                with self.subTest(horizontal=horizontal, vertical=vertical):
                    edges = [False] * 4
                    edges[horizontal] = edges[vertical] = True
                    runner = self.make_edge_runner(camera=(99, 99), edges=edges)
                    runner.ensure_edge_insight()
                    self.assertEqual(runner.camera, (x, y))
                    runner.map_swipe.assert_not_called()

    def test_initial_update_is_optional_and_errors_propagate(self):
        runner = self.make_edge_runner(edges=(True, False, False, True))
        runner.ensure_edge_insight(skip_first_update=False)
        runner.update.assert_called_once_with()
        runner.update.side_effect = MapDetectionError('离线识别失败')
        with self.assertRaises(MapDetectionError):
            runner.ensure_edge_insight(skip_first_update=False)
        runner.map_swipe.assert_not_called()

    def test_preset_and_reverse_restore_original_view(self):
        runner = self.make_edge_runner(camera=(13, 8), edges=(False, False, False, False))
        original = runner.camera
        runner.map_swipe.side_effect = lambda vector: self.physical_swipe(runner, vector)
        record = runner.ensure_edge_insight(preset=(-1, -1), reverse=True)
        self.assertEqual(record[0], (-1, -1))
        self.assertEqual(runner.camera, original)
        forward = [call.args[0] for call in runner.map_swipe.call_args_list[:len(record)]]
        backward = [call.args[0] for call in runner.map_swipe.call_args_list[len(record):]]
        self.assertEqual(forward, record)
        self.assertEqual(backward, [(-x, -y) for x, y in reversed(record)])

    def test_zero_swipe_limit_is_rejected(self):
        for limits in ((0, 3), (4, 0)):
            with self.subTest(limits=limits):
                runner = self.make_edge_runner()
                with self.assertRaises(MapDetectionError):
                    runner.ensure_edge_insight(swipe_limit=limits)
                runner.map_swipe.assert_not_called()

    def test_t13_has_nine_camera_targets(self):
        runner = self.make_scan_runner()
        self.assertEqual(len(runner.map.camera_data), 9)
        self.assertEqual(
            set(runner.map.camera_data.location),
            {(x, y) for x in (4, 12, 16) for y in (1, 6, 9)},
        )

    def test_all_zone_shapes_have_complete_theoretical_camera_coverage(self):
        """以真实海域尺寸和大世界视野验证每个地图格都落在至少一个扫描节点内。"""
        for shape in sorted({zone['shape'] for zone in DIC_OS_MAP.values()}):
            with self.subTest(shape=shape):
                map_ = OSCampaignMap('离线覆盖')
                map_.shape = shape
                left, top, right, bottom = map_.camera_sight
                covered = set()
                for cx, cy in map_.camera_data.location:
                    for x in range(max(0, cx + left), min(map_.shape[0], cx + right) + 1):
                        for y in range(max(0, cy + top), min(map_.shape[1], cy + bottom) + 1):
                            covered.add((x, y))
                self.assertEqual(covered, set(map_.grids))

    def test_full_scan_reaches_all_nine_targets_before_no_event_result(self):
        runner = self.make_scan_runner()
        checked = []
        runner.map_rescan_current.side_effect = lambda **kwargs: checked.append(runner.camera) or False
        self.assertFalse(runner.map_rescan_once(drop='离线记录'))
        self.assertEqual(runner.focus_to.call_count, 9)
        self.assertEqual(runner.focus_to_grid_center.call_count, 9)
        self.assertEqual(len(checked), 10)
        self.assertEqual(set(checked[1:]), set(runner.map.camera_data.location))
        for call in runner.focus_to.call_args_list:
            self.assertEqual(call.kwargs, {'swipe_limit': (6, 5)})
        for call in runner.map_rescan_current.call_args_list:
            self.assertEqual(call.kwargs, {'drop': '离线记录'})

    def test_failed_focus_never_checks_wrong_view_or_returns_no_event(self):
        runner = self.make_scan_runner()
        runner.focus_to.side_effect = None
        with self.assertRaises(MapDetectionError):
            runner.map_rescan_once()
        runner.map_rescan_current.assert_called_once_with(drop=None)
        runner.focus_to.assert_called_once()

    def test_grid_center_drift_is_also_rejected(self):
        runner = self.make_scan_runner()
        runner.focus_to_grid_center.side_effect = lambda *args: setattr(runner, 'camera', (0, 0))
        with self.assertRaises(MapDetectionError):
            runner.map_rescan_once()
        runner.map_rescan_current.assert_called_once_with(drop=None)

    def test_initial_update_failure_propagates_before_event_scan(self):
        runner = self.make_scan_runner()
        runner.update.side_effect = MapDetectionError('初次地图识别失败')
        with self.assertRaises(MapDetectionError):
            runner.map_rescan_once()
        runner.map_rescan_current.assert_not_called()
        runner.map_init.assert_not_called()
        runner.focus_to.assert_not_called()

    def test_edge_failure_prevents_full_scan_and_following_action(self):
        """当前无事件但全图坐标无法定位时，异常阻止执行后续跳图动作。"""
        runner = self.make_scan_runner()
        edge_runner = self.make_edge_runner()
        runner.map_init.side_effect = lambda **kwargs: edge_runner.ensure_edge_insight()
        following_action = Mock()
        with self.assertRaises(MapDetectionError):
            runner.map_rescan_once()
            following_action()
        runner.map_rescan_current.assert_called_once_with(drop=None)
        runner.focus_to.assert_not_called()
        following_action.assert_not_called()

    def test_current_mode_does_not_visit_full_map(self):
        runner = self.make_scan_runner()
        self.assertFalse(runner.map_rescan_once(rescan_mode='current'))
        runner.map_rescan_current.assert_called_once_with(drop=None)
        runner.map_init.assert_not_called()
        runner.focus_to.assert_not_called()

    def test_current_event_can_end_scan_successfully(self):
        runner = self.make_scan_runner()
        runner.map_rescan_current.return_value = True
        self.assertTrue(runner.map_rescan_once())
        runner.map_init.assert_not_called()
        runner.focus_to.assert_not_called()

    def test_event_found_at_later_target_can_end_scan_successfully(self):
        runner = self.make_scan_runner()
        runner.map_rescan_current.side_effect = (False, False, False, True)
        self.assertTrue(runner.map_rescan_once())
        self.assertEqual(runner.focus_to.call_count, 3)
        self.assertEqual(runner.map_rescan_current.call_count, 4)

    def test_meow_scan_failure_stops_real_stay_flow_and_next_round(self):
        """真实指定海域调用链定位失败后，不能清尾、切任务或再开下一轮补给。"""
        runner = OpsiMeowfficerFarming.__new__(OpsiMeowfficerFarming)
        zone = SimpleNamespace(zone_id=12)
        runner.zone = zone
        runner.is_zone_name_hidden = True
        runner.config = SimpleNamespace(
            OpsiFleet_Fleet=1,
            OpsiFleet_Submarine=False,
            OpsiMeowfficerFarming_StayInZone=True,
            OpsiMeowfficerFarming_ExecuteFixedPatrolScan=False,
            check_task_switch=Mock(),
        )
        runner._meow_target_zone_list = [zone]
        runner._meow_traditional_zone = None
        runner._prepare_meowfficer_farming = Mock(return_value=0)
        runner._close_scheduling_action_point = Mock()
        runner._meow_ap_check = Mock(return_value=True)
        runner.get_current_zone = Mock()
        runner.globe_goto = Mock()
        runner.action_point_reusable = Mock(return_value=False)
        runner.action_point_set = Mock()
        runner.fleet_set = Mock()
        runner.os_order_execute = Mock()
        runner.meow_search_metrics_start = Mock()
        runner.meow_search_metrics_end = Mock()
        runner.run_strategic_search = Mock(return_value=True)
        runner._meow_debug_clip = Mock(return_value=nullcontext())
        runner._clear_question_primary = Mock(return_value=False)
        runner.map_rescan = Mock(side_effect=MapDetectionError('横边未定位'))
        runner._clear_question_other_fleets = Mock()
        runner._meow_fixed_patrol_scan = Mock()
        runner.handle_after_auto_search = Mock()
        runner._meow_record_akashi_if_solved = Mock()
        with patch('module.base.debug_clip.cleanup_clips_if_due'):
            with self.assertRaises(MapDetectionError):
                runner.run_meowfficer_farming()
        runner.map_rescan.assert_called_once_with()
        runner.meow_search_metrics_end.assert_called_once_with()
        runner.handle_after_auto_search.assert_not_called()
        runner._clear_question_other_fleets.assert_not_called()
        runner._meow_fixed_patrol_scan.assert_not_called()
        runner._meow_record_akashi_if_solved.assert_not_called()
        runner.config.check_task_switch.assert_not_called()
        runner._meow_ap_check.assert_called_once_with(0, False)
        runner.action_point_set.assert_called_once_with(cost=120, keep_current_ap=True, check_rest_ap=True)


if __name__ == '__main__':
    unittest.main()
