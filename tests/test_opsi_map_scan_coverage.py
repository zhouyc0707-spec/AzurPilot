"""上游大世界扫描流程及无关定制回归；不连接设备、不读写真实配置。"""

import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from module.exception import MapDetectionError
from module.os.map import OSMap
from module.os.map_base import OSCampaignMap
from module.os.map_data import DIC_OS_MAP
from module.os.tasks.meowfficer_farming import OpsiMeowfficerFarming


class TestOSMapScanCoverage(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.os.camera.logger'))
        self.enterContext(patch('module.os.map.logger'))
        self.enterContext(patch('module.os.tasks.meowfficer_farming.logger'))

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

    def test_current_scan_keeps_tip_wait_before_view_detection(self):
        runner = self.make_scan_runner()
        order = []
        runner.handle_info_bar.side_effect = lambda: order.append('tip')
        runner.update.side_effect = lambda: order.append('view')
        runner.map_rescan_current.return_value = True
        self.assertTrue(runner.map_rescan_once())
        self.assertEqual(order, ['tip', 'view'])
        runner.map_init.assert_not_called()

    def test_nearby_question_keeps_tip_wait_before_coordinate_detection(self):
        runner = OSMap.__new__(OSMap)
        runner.device = Mock()
        runner.zone = SimpleNamespace(is_port=False)
        runner.radar = SimpleNamespace(predict_question=Mock(return_value=(-1, 0)))
        runner.view = Mock()
        runner.view.select.return_value.count = 1
        order = []
        runner.handle_info_bar = Mock(side_effect=lambda: order.append('tip'))
        runner.update_os = Mock(side_effect=lambda: order.append('view'))
        runner.convert_radar_to_local = Mock(return_value=SimpleNamespace())
        runner._should_skip_siren_research = Mock(return_value=False)
        runner._solved_map_event = set()
        runner.config = SimpleNamespace(
            task=SimpleNamespace(command='OpsiDaily'),
            temporary=lambda **kwargs: nullcontext(),
        )
        runner._close_fleet_bar_if_open = Mock(return_value=False)
        runner.wait_until_walk_stable = Mock(return_value='akashi')
        self.assertTrue(OSMap.clear_question(runner))
        self.assertEqual(order, ['tip', 'view'])
        runner.device.click.assert_called_once()
        self.assertEqual(runner._solved_map_event, {'is_akashi'})

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

    def test_requested_full_mode_visits_upstream_targets_and_centers_each_view(self):
        runner = self.make_scan_runner()
        checked = []
        runner.map_rescan_current.side_effect = lambda **kwargs: checked.append(runner.camera) or False
        self.assertFalse(runner.map_rescan_once(drop='离线记录'))
        self.assertEqual(runner.focus_to.call_count, 9)
        self.assertEqual(runner.focus_to_grid_center.call_args_list, [call(0.3)] * 9)
        self.assertEqual(len(checked), 10)
        self.assertEqual(set(checked[1:]), set(runner.map.camera_data.location))
        for entry in runner.focus_to.call_args_list:
            self.assertEqual(entry.kwargs, {'swipe_limit': (6, 5)})
        for entry in runner.map_rescan_current.call_args_list:
            self.assertEqual(entry.kwargs, {'drop': '离线记录'})

    def test_normal_initial_detection_error_warns_and_returns_false(self):
        runner = self.make_scan_runner()
        runner.update.side_effect = MapDetectionError('初次地图识别失败')
        with patch('module.os.map.logger') as logger:
            self.assertFalse(runner.map_rescan_once())
        logger.warning.assert_called_once()
        runner.map_rescan_current.assert_not_called()
        runner.map_init.assert_not_called()
        runner.focus_to.assert_not_called()

    def test_cleanup_initial_detection_error_propagates(self):
        runner = self.make_scan_runner()
        runner._opsi_meowfficer_cleanup = True
        runner.update.side_effect = MapDetectionError('初次地图识别失败')
        with self.assertRaises(MapDetectionError):
            runner.map_rescan_once()
        runner.map_rescan_current.assert_not_called()
        runner.map_init.assert_not_called()
        runner.focus_to.assert_not_called()

    def test_map_initialization_error_prevents_following_action(self):
        """上游没有捕获的地图初始化异常继续交由任务层处理。"""
        runner = self.make_scan_runner()
        runner.map_init.side_effect = MapDetectionError('地图初始化失败')
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
        runner.map_rescan = Mock(side_effect=MapDetectionError('地图初始化失败'))
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
        runner.action_point_set.assert_called_once_with(cost=120, keep_current_ap=True, check_rest_ap=True, routine=True)


if __name__ == '__main__':
    unittest.main()
