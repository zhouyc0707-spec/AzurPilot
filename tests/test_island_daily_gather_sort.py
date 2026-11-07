"""每日采集升序确认使用新截图、有限重试，并将失败交给现有恢复。"""

from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

import module.base.timer as timer_module
from module.exception import GameStuckError
from module.island.island_daily_gather import ISLAND_SELECT_CHARACTER_CHECK, IslandDailyGather
from module.island_select_character.assets import (
    SELECT_CHARACTER_FILTER_CONFIRM,
    SELECT_CHARACTER_FILTER_STAMINA,
    SELECT_CHARACTER_SORT_ASC,
    SELECT_CHARACTER_SORT_CLICK,
    SELECT_CHARACTER_SORT_DESC,
)
from tests.test_island_goto_management import FakeClock, FakeDevice, build_frame


ASCENDING = build_frame(ISLAND_SELECT_CHARACTER_CHECK, SELECT_CHARACTER_SORT_ASC)
DESCENDING = build_frame(ISLAND_SELECT_CHARACTER_CHECK, SELECT_CHARACTER_SORT_DESC)
UNKNOWN_ARROW = build_frame(ISLAND_SELECT_CHARACTER_CHECK)


class DailyGatherSortTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.enterContext(patch.object(timer_module, 'time', lambda: self.clock.now))
        for button in (ISLAND_SELECT_CHARACTER_CHECK, SELECT_CHARACTER_FILTER_CONFIRM,
                       SELECT_CHARACTER_FILTER_STAMINA, SELECT_CHARACTER_SORT_ASC,
                       SELECT_CHARACTER_SORT_DESC):
            self.addCleanup(button.clear_offset)

    def make_runner(self, timeline):
        device = FakeDevice(self.clock, timeline, screenshot_cost=0.2, click_cost=0.05)
        runner = IslandDailyGather.__new__(IslandDailyGather)
        runner.device = device
        runner.interval_timer = {}
        runner.config = SimpleNamespace(BUTTON_OFFSET=(20, 20), SERVER='cn', task_delay=Mock())
        return runner, device

    def test_existing_ascending_is_checked_on_fresh_frame_without_click(self):
        runner, device = self.make_runner(lambda seconds: ASCENDING)
        device.image = DESCENDING
        runner._sort_by_life_level()
        self.assertEqual(device.screenshot_count, 1)
        self.assertEqual(device.clicks, [])

    def test_delayed_arrow_change_does_not_toggle_sort_back(self):
        runner, device = self.make_runner(lambda seconds: DESCENDING)
        device.timeline = lambda seconds: (
            ASCENDING if device.clicks and seconds >= device.clicks[0][0] + 0.7 else DESCENDING)
        runner._sort_by_life_level()
        self.assertEqual([name for _, name in device.clicks], [SELECT_CHARACTER_SORT_CLICK.name])
        self.assertGreaterEqual(self.clock.now - device.t0, device.clicks[0][0] + 0.9)

    def test_ignored_first_click_retries_then_confirms_new_ascending_frames(self):
        runner, device = self.make_runner(lambda seconds: DESCENDING)
        device.timeline = lambda seconds: (
            ASCENDING if len(device.clicks) >= 2 and seconds >= device.clicks[1][0] + 0.4
            else DESCENDING)
        runner._sort_by_life_level()
        self.assertEqual(len(device.clicks), 2)
        self.assertTrue(all(name == SELECT_CHARACTER_SORT_CLICK.name for _, name in device.clicks))
        self.assertGreaterEqual(device.clicks[1][0] - device.clicks[0][0], 2)
        self.assertGreaterEqual(self.clock.now - device.t0, device.clicks[1][0] + 0.6)

    def test_one_ascending_frame_after_click_does_not_finish_sorting(self):
        runner, device = self.make_runner(lambda seconds: DESCENDING)

        def timeline(seconds):
            if device.clicks and (0.6 <= seconds < 0.8 or seconds >= 1.2):
                return ASCENDING
            return DESCENDING

        device.timeline = timeline
        runner._sort_by_life_level()
        self.assertGreaterEqual(self.clock.now - device.t0, 1.4)
        self.assertEqual(len(device.clicks), 1)

    def test_unknown_arrow_never_clicks_or_logs_success(self):
        runner, device = self.make_runner(lambda seconds: UNKNOWN_ARROW)
        with patch('module.island.island_daily_gather.logger') as log:
            with self.assertRaisesRegex(GameStuckError, '升序.*未确认'):
                runner._sort_by_life_level()
            self.assertFalse(any('排序完成' in call.args[0] for call in log.info.call_args_list))
        self.assertEqual(device.clicks, [])
        self.assertLessEqual(self.clock.now - device.t0, 4.3)

    def test_permanently_ignored_clicks_are_bounded_and_never_log_success(self):
        runner, device = self.make_runner(lambda seconds: DESCENDING)
        # 允许旧实现推进固定等待，以直接复现第二次点击后误报成功的缺口。
        device.sleep = self.clock.advance
        with patch('module.island.island_daily_gather.logger') as log:
            with self.assertRaisesRegex(GameStuckError, '升序.*未确认'):
                runner._sort_by_life_level()
            self.assertFalse(any('排序完成' in call.args[0] for call in log.info.call_args_list))
        self.assertEqual(len(device.clicks), 2)
        self.assertGreaterEqual(device.clicks[1][0] - device.clicks[0][0], 2)
        self.assertLessEqual(self.clock.now - device.t0, 4.3)

    def test_popup_or_arrow_without_character_page_cannot_trigger_sort(self):
        frames = (
            np.maximum(DESCENDING, build_frame(SELECT_CHARACTER_FILTER_CONFIRM,
                                              SELECT_CHARACTER_FILTER_STAMINA)),
            build_frame(SELECT_CHARACTER_SORT_DESC),
        )
        for frame in frames:
            with self.subTest(popup=bool(np.array_equal(frame, frames[0]))):
                runner, device = self.make_runner(lambda seconds: frame)
                with self.assertRaises(GameStuckError):
                    runner._sort_by_life_level()
                self.assertEqual(device.clicks, [])

    def test_failed_sort_stops_character_dispatch_and_success_scheduling(self):
        runner, device = self.make_runner(lambda seconds: UNKNOWN_ARROW)
        for name in ('goto_management', 'ui_goto', 'post_manage_mode_collection',
                     '_claim_existing_rewards', '_click_depart', '_handle_collection_complete',
                     '_exit_management', 'select_specific_character',
                     '_select_character_with_stamina_check', 'confirm_selected_character_closed'):
            setattr(runner, name, Mock())
        runner._handle_target_selection = Mock(return_value=True)
        runner._daily_gather_worker_list = Mock(return_value=['Helena'])
        runner._open_gather_character_select = Mock(return_value=True)
        with patch('module.island.island_daily_gather.current_time',
                   return_value=datetime(2026, 11, 7, 18)), self.assertRaises(GameStuckError):
            runner.run()
        for name in ('select_specific_character', '_select_character_with_stamina_check',
                     'confirm_selected_character_closed', '_click_depart',
                     '_handle_collection_complete'):
            getattr(runner, name).assert_not_called()
        runner.config.task_delay.assert_not_called()
        self.assertEqual(device.clicks, [])


if __name__ == '__main__':
    unittest.main()
