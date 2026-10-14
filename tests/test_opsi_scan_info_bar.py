"""当前视野免等待信息栏、完整扫图等待与识别失败隔离；不连接设备。"""

import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from module.exception import MapDetectionError
from module.map.camera import Camera
from module.map.map_grids import SelectedGrids
from module.os.map import OSMap
from module.os.map_base import OSCampaignMap


class ReadyTimer:
    """让异常重试立即到期，测试无需等待真实时间。"""

    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        return self

    def reached(self):
        return True

    def reset(self):
        return self


class TestCameraInfoBarPolicy(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.map.camera.logger'))
        self.enterContext(patch('module.map.camera.Timer', ReadyTimer))

    @staticmethod
    def make_camera():
        runner = Camera.__new__(Camera)
        runner.config = SimpleNamespace(task=SimpleNamespace(command='OpsiHazard1Leveling'))
        runner.device = SimpleNamespace(
            image=object(), screenshot=Mock(), app_is_running=Mock(return_value=True),
            _screenshot_interval=Mock(), click=Mock(),
        )
        runner.view = SimpleNamespace(load=Mock(), update=Mock(), center_offset=(0.5, 0.5))
        runner._view_init = Mock()
        runner._update_view_data = Mock()
        runner.is_in_map = Mock(return_value=True)
        runner.info_bar_count = Mock(return_value=1)
        runner.handle_info_bar = Mock()
        runner.appear = Mock(return_value=False)
        runner.handle_story_skip = Mock(return_value=False)
        runner.is_in_stage = Mock(return_value=False)
        runner.handle_popup_confirm = Mock(return_value=False)
        runner._auto_search_menu_offset = (50, 50)
        return runner

    def test_visible_info_bar_does_not_delay_successful_quick_recognition(self):
        """信息栏可见但网格已能识别时，直接使用这张新截图。"""
        runner = self.make_camera()

        self.assertIsNone(runner.update(wait_info_bar=False))

        runner.device.screenshot.assert_called_once_with()
        runner.view.load.assert_called_once_with(runner.device.image)
        runner.handle_info_bar.assert_not_called()
        runner._update_view_data.assert_called_once_with()

    def test_default_update_still_waits_then_retries_with_fresh_screenshot(self):
        runner = self.make_camera()
        runner.view.load.side_effect = (MapDetectionError('信息栏挡住网格'), None)

        self.assertIsNone(runner.update())

        runner.handle_info_bar.assert_called_once_with()
        self.assertEqual(runner.device.screenshot.call_count, 2)
        self.assertEqual(runner.view.load.call_count, 2)
        runner._update_view_data.assert_called_once_with()

    def test_quick_failure_returns_without_waiting_or_predicting_stale_view(self):
        runner = self.make_camera()
        runner.view.load.side_effect = MapDetectionError('信息栏挡住网格')

        self.assertIs(runner.update(wait_info_bar=False), False)

        runner.device.screenshot.assert_called_once_with()
        runner.view.load.assert_called_once_with(runner.device.image)
        runner.handle_info_bar.assert_not_called()
        runner._update_view_data.assert_not_called()
        runner.device.click.assert_not_called()

    def test_quick_policy_keeps_image_only_update_behavior(self):
        runner = self.make_camera()

        self.assertTrue(runner.update(camera=False, wait_info_bar=False))

        runner.view.update.assert_called_once_with(image=runner.device.image)
        runner.view.load.assert_not_called()
        runner.handle_info_bar.assert_not_called()
        runner._update_view_data.assert_not_called()

    def test_unrelated_detection_error_is_not_converted_to_info_bar_failure(self):
        runner = self.make_camera()
        failure = MapDetectionError('无法识别地图边缘')
        runner.info_bar_count.return_value = 0
        runner.view.load.side_effect = failure

        with self.assertRaises(MapDetectionError) as captured:
            runner.update(wait_info_bar=False)

        self.assertIs(captured.exception, failure)
        runner.handle_info_bar.assert_not_called()
        runner._update_view_data.assert_not_called()

    def test_quick_policy_keeps_reward_popup_recovery_and_fresh_retry(self):
        """免等待仅影响信息栏，不跳过实际奖励弹窗的恢复。"""
        runner = self.make_camera()
        runner.info_bar_count.return_value = 0
        runner.view.load.side_effect = (MapDetectionError('奖励页遮住地图'), None)
        runner.appear.return_value = True

        self.assertIsNone(runner.update(wait_info_bar=False))

        runner.device.click.assert_called_once()
        self.assertEqual(runner.device.screenshot.call_count, 2)
        runner.handle_info_bar.assert_not_called()
        runner._update_view_data.assert_called_once_with()


class RadarQuestionView:
    """提供真实雷达坐标转换所需的局部舰队和目标格子。"""

    def __init__(self, events):
        self.events = events
        self.fleet = SimpleNamespace(location=(5, 4))
        self.target = SimpleNamespace(
            location=(4, 4), is_scanning_device=False, is_logging_tower=False,
        )
        self.load = Mock(side_effect=lambda image: events.append(('load', image)))
        self.predict = Mock(side_effect=lambda: events.append(('predict',)))
        self.show = Mock(side_effect=lambda: events.append(('show',)))
        self.select = Mock(side_effect=self.select_fleet)

    def select_fleet(self, **kwargs):
        self.events.append(('select', kwargs))
        return SelectedGrids([self.fleet])

    def __getitem__(self, location):
        location = tuple(location)
        self.events.append(('convert', location))
        if location != self.target.location:
            raise KeyError(location)
        return self.target


class TestRadarQuestionInfoBarPolicy(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.os.map.logger'))
        self.enterContext(patch('module.os.camera.logger'))

    @staticmethod
    def make_question_runner(question=(-1, 0), walk_result=('akashi',)):
        """保留 clear_question、update_os 和坐标转换的真实实现。"""
        runner = OSMap.__new__(OSMap)
        events = []
        frame = object()
        runner.device = SimpleNamespace(
            image=frame,
            screenshot=Mock(side_effect=AssertionError('近距离问号必须复用当前截图')),
            click=Mock(side_effect=lambda grid: events.append(('click', grid))),
        )
        runner.config = SimpleNamespace(temporary=Mock(side_effect=lambda **kwargs: nullcontext()))
        runner.zone = SimpleNamespace(is_port=False)
        runner.view = RadarQuestionView(events)
        runner.radar = SimpleNamespace(
            predict_question=Mock(side_effect=lambda image, **kwargs: events.append(('radar', image)) or question),
        )
        runner.handle_info_bar = Mock(side_effect=AssertionError('近距离问号不应等待提示条'))
        runner._os_camera_recover_to_fleet = Mock(side_effect=AssertionError('舰队位置有效，不应重新对焦'))
        runner.wait_until_walk_stable = Mock(
            side_effect=lambda **kwargs: events.append(('walk', kwargs)) or walk_result,
        )
        runner._solved_map_event = set()
        runner._question_unreachable = False
        return runner, events, frame

    def test_question_reuses_same_frame_and_clicks_before_original_walk_confirmation(self):
        runner, events, frame = self.make_question_runner()

        self.assertTrue(runner.clear_question(drop='离线问号掉落'))

        runner.radar.predict_question.assert_called_once_with(frame, in_port=False)
        runner.view.load.assert_called_once_with(frame)
        runner.view.predict.assert_called_once_with()
        runner.view.select.assert_has_calls([call(is_current_fleet=True), call(is_current_fleet=True)])
        phases = [entry[0] for entry in events]
        self.assertEqual(phases[:4], ['radar', 'load', 'predict', 'show'])
        self.assertLess(phases.index('convert'), phases.index('click'))
        self.assertLess(phases.index('click'), phases.index('walk'))
        runner.device.click.assert_called_once_with(runner.view.target)
        runner.wait_until_walk_stable.assert_called_once()
        walk_kwargs = runner.wait_until_walk_stable.call_args.kwargs
        self.assertEqual(walk_kwargs['drop'], '离线问号掉落')
        self.assertIs(walk_kwargs['walk_out_of_step'], False)
        self.assertEqual(walk_kwargs['confirm_timer'].limit, 3)
        self.assertEqual(walk_kwargs['confirm_timer'].count, 4)
        runner.config.temporary.assert_called_once_with(
            STORY_ALLOW_SKIP=False, OS_SIREN_DEVICE_USAGE='use_until_destroyed',
        )
        self.assertEqual(runner._solved_map_event, {'is_akashi'})
        self.assertFalse(runner._question_unreachable)
        runner.handle_info_bar.assert_not_called()
        runner.device.screenshot.assert_not_called()

    def test_no_question_never_initializes_view_or_clicks(self):
        runner, events, frame = self.make_question_runner(question=None)

        self.assertFalse(runner.clear_question())

        self.assertEqual(events, [('radar', frame)])
        runner.view.load.assert_not_called()
        runner.view.predict.assert_not_called()
        runner.device.click.assert_not_called()
        runner.wait_until_walk_stable.assert_not_called()
        runner.handle_info_bar.assert_not_called()
        runner.device.screenshot.assert_not_called()
        self.assertEqual(runner._solved_map_event, set())
        self.assertFalse(runner._question_unreachable)

    def test_three_failed_walks_keep_unreachable_flag_and_bounded_attempts(self):
        runner, events, frame = self.make_question_runner(walk_result=())

        self.assertFalse(runner.clear_question(drop='离线失败掉落'))

        self.assertTrue(runner._question_unreachable)
        self.assertEqual(runner._solved_map_event, set())
        self.assertEqual(runner.radar.predict_question.call_count, 3)
        self.assertEqual(runner.view.load.call_count, 3)
        self.assertEqual(runner.device.click.call_count, 3)
        self.assertEqual(runner.wait_until_walk_stable.call_count, 3)
        for invocation in runner.view.load.call_args_list:
            self.assertIs(invocation.args[0], frame)
        self.assertEqual([entry[1] for entry in events if entry[0] == 'convert'], [(4, 4)] * 3)
        runner.handle_info_bar.assert_not_called()
        runner.device.screenshot.assert_not_called()

    def test_generic_event_keeps_original_success_mark_after_fast_click(self):
        runner, _, _ = self.make_question_runner(walk_result=('event',))

        self.assertTrue(runner.clear_question())

        self.assertEqual(runner._solved_map_event, {'is_logging_tower'})
        self.assertFalse(runner._question_unreachable)
        runner.device.click.assert_called_once_with(runner.view.target)
        runner.wait_until_walk_stable.assert_called_once()
        runner.handle_info_bar.assert_not_called()


class TestOSMapScanInfoBarPolicy(unittest.TestCase):
    def setUp(self):
        self.logger = self.enterContext(patch('module.os.map.logger'))

    def make_scan_runner(self, initial_update=None, current_event=False):
        runner = OSMap.__new__(OSMap)
        runner.map = OSCampaignMap('离线信息栏检查')
        runner.map.shape = 'H8'
        runner.camera = (0, 0)
        events = []

        runner.map_data_init = Mock(side_effect=lambda **kwargs: events.append('map_data'))
        runner.update = Mock(side_effect=lambda **kwargs: events.append(('update', kwargs)) or initial_update)
        runner.handle_info_bar = Mock(side_effect=lambda: events.append('wait_info_bar'))
        runner.map_init = Mock(side_effect=lambda **kwargs: events.append('map_init'))
        runner.focus_to_grid_center = Mock()

        def focus(target, **kwargs):
            runner.camera = target.location
            events.append(('focus', target.location))

        runner.focus_to = Mock(side_effect=focus)
        runner.map_rescan_current = Mock(
            side_effect=lambda **kwargs: events.append(('scan_current', runner.camera)) or current_event,
        )
        self.logger.hr.side_effect = lambda title, **kwargs: events.append('full_scan') if title == '完全重新扫描地图' else None
        return runner, events

    def test_current_event_is_processed_without_info_bar_wait(self):
        runner, events = self.make_scan_runner(current_event=True)

        self.assertTrue(runner.map_rescan_once(drop='离线掉落'))

        runner.update.assert_called_once_with(wait_info_bar=False)
        runner.map_rescan_current.assert_called_once_with(drop='离线掉落')
        self.assertEqual(events[:3], ['map_data', ('update', {'wait_info_bar': False}), ('scan_current', (0, 0))])
        runner.handle_info_bar.assert_not_called()
        runner.map_init.assert_not_called()
        runner.focus_to.assert_not_called()

    def test_full_scan_waits_only_after_current_view_attempt_before_repositioning(self):
        runner, events = self.make_scan_runner()

        self.assertFalse(runner.map_rescan_once())

        self.assertEqual(events[:6], [
            'map_data', ('update', {'wait_info_bar': False}), ('scan_current', (0, 0)),
            'full_scan', 'wait_info_bar', 'map_init',
        ])
        runner.handle_info_bar.assert_called_once_with()
        self.assertEqual(runner.focus_to.call_count, len(runner.map.camera_data))
        self.assertEqual(runner.map_rescan_current.call_count, len(runner.map.camera_data) + 1)

    def test_quick_failure_enters_full_scan_without_processing_stale_view(self):
        runner, events = self.make_scan_runner(initial_update=False)

        self.assertFalse(runner.map_rescan_once(drop='离线掉落'))

        self.assertEqual(events[:5], [
            'map_data', ('update', {'wait_info_bar': False}), 'full_scan', 'wait_info_bar', 'map_init',
        ])
        runner.handle_info_bar.assert_called_once_with()
        visited = [entry.args[0].location for entry in runner.focus_to.call_args_list]
        self.assertEqual(set(visited), set(runner.map.camera_data.location))
        self.assertEqual(runner.map_rescan_current.call_count, len(visited))
        for entry in runner.map_rescan_current.call_args_list:
            self.assertEqual(entry, call(drop='离线掉落'))
        first_focus = next(index for index, entry in enumerate(events) if isinstance(entry, tuple) and entry[0] == 'focus')
        first_scan = next(index for index, entry in enumerate(events) if isinstance(entry, tuple) and entry[0] == 'scan_current')
        self.assertLess(first_focus, first_scan)

    def test_current_only_failed_recognition_raises_instead_of_reporting_no_event(self):
        runner, _ = self.make_scan_runner(initial_update=False)

        with self.assertRaises(MapDetectionError):
            runner.map_rescan_once(rescan_mode='current')

        runner.map_rescan_current.assert_not_called()
        runner.handle_info_bar.assert_not_called()
        runner.map_init.assert_not_called()
        runner.focus_to.assert_not_called()

    def test_current_only_success_does_not_wait_or_enter_full_scan(self):
        runner, _ = self.make_scan_runner()

        self.assertFalse(runner.map_rescan_once(rescan_mode='current'))

        runner.map_rescan_current.assert_called_once_with(drop=None)
        runner.handle_info_bar.assert_not_called()
        runner.map_init.assert_not_called()
        runner.focus_to.assert_not_called()

    def test_real_map_error_propagates_before_full_scan(self):
        runner, _ = self.make_scan_runner()
        failure = MapDetectionError('实际地图坐标无法定位')
        runner.update.side_effect = failure

        with self.assertRaises(MapDetectionError) as captured:
            runner.map_rescan_once()

        self.assertIs(captured.exception, failure)
        runner.map_rescan_current.assert_not_called()
        runner.handle_info_bar.assert_not_called()
        runner.map_init.assert_not_called()
        runner.focus_to.assert_not_called()

    def test_full_scan_wait_failure_does_not_scan_unconfirmed_map(self):
        runner, _ = self.make_scan_runner(initial_update=False)
        runner.handle_info_bar.side_effect = MapDetectionError('信息栏消失后仍无法确认地图')

        with self.assertRaises(MapDetectionError):
            runner.map_rescan_once()

        runner.map_init.assert_not_called()
        runner.map_rescan_current.assert_not_called()
        runner.focus_to.assert_not_called()


if __name__ == '__main__':
    unittest.main()
