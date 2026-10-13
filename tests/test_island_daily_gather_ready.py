"""每日采集角色槽位就绪的离线截图与状态回归。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

import module.base.timer as timer_module
from module.island.island_daily_gather import (
    GATHER_STAMINA_THRESHOLD, ISLAND_GATHER_COLLECT_CHECK, ISLAND_GATHER_PLUS_A,
    ISLAND_GATHER_PLUS_B, ISLAND_GATHER_PLUS_C, ISLAND_SELECT_CHARACTER_CHECK,
    IslandDailyGather,
)
from tests.test_island_goto_management import FakeClock, FakeDevice, build_frame


PLUS_BUTTONS = (ISLAND_GATHER_PLUS_A, ISLAND_GATHER_PLUS_B, ISLAND_GATHER_PLUS_C)
COLLECTION_PAGE = build_frame(ISLAND_GATHER_COLLECT_CHECK)
CHARACTER_PAGE = build_frame(ISLAND_SELECT_CHARACTER_CHECK)
UNKNOWN_PAGE = np.zeros((720, 1280, 3), dtype=np.uint8)


class IslandDailyGatherReadyTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.enterContext(patch.object(timer_module, 'time', lambda: self.clock.now))
        for button in (*PLUS_BUTTONS, ISLAND_GATHER_COLLECT_CHECK, ISLAND_SELECT_CHARACTER_CHECK):
            self.addCleanup(button.clear_offset)

    def make_runner(self, timeline, screenshot_cost=0.2):
        device = FakeDevice(self.clock, timeline, screenshot_cost=screenshot_cost, max_screenshots=120)
        runner = IslandDailyGather.__new__(IslandDailyGather)
        runner.device = device
        runner.interval_timer = {}
        runner.config = SimpleNamespace(BUTTON_OFFSET=(20, 20), SERVER='cn')
        return runner, device

    def test_all_three_slots_wait_for_their_ready_button(self):
        for index, button in enumerate(PLUS_BUTTONS):
            with self.subTest(slot=index):
                ready_page = build_frame(ISLAND_GATHER_COLLECT_CHECK, button)
                runner, device = self.make_runner(
                    lambda seconds: COLLECTION_PAGE if seconds < 1 else ready_page,
                )
                device.timeline = lambda seconds: (
                    CHARACTER_PAGE if device.clicks else COLLECTION_PAGE if seconds < 1 else ready_page
                )
                self.assertTrue(runner._open_gather_character_select(button, index))
                self.assertEqual(len(device.clicks), 1)
                self.assertEqual(device.clicks[0][1], button.name)
                self.assertGreaterEqual(device.clicks[0][0], 1.4)

    def test_existing_character_page_never_clicks_plus(self):
        runner, device = self.make_runner(lambda seconds: CHARACTER_PAGE)
        self.assertTrue(runner._open_gather_character_select(ISLAND_GATHER_PLUS_B, 1))
        self.assertEqual(device.clicks, [])

    def test_page_marker_without_target_plus_does_not_click(self):
        runner, device = self.make_runner(lambda seconds: COLLECTION_PAGE)
        self.assertFalse(runner._open_gather_character_select(ISLAND_GATHER_PLUS_B, 1))
        self.assertEqual(device.clicks, [])

    def test_plus_without_collection_page_does_not_click(self):
        runner, device = self.make_runner(lambda seconds: build_frame(ISLAND_GATHER_PLUS_C))
        self.assertFalse(runner._open_gather_character_select(ISLAND_GATHER_PLUS_C, 2))
        self.assertEqual(device.clicks, [])

    def test_dimmed_plus_behind_popup_does_not_click(self):
        frame = build_frame(ISLAND_GATHER_COLLECT_CHECK, ISLAND_GATHER_PLUS_A)
        x1, y1, x2, y2 = ISLAND_GATHER_PLUS_A.area
        frame[y1:y2, x1:x2] = (frame[y1:y2, x1:x2].astype(float) * .6).astype(np.uint8)
        runner, device = self.make_runner(lambda seconds: frame)
        self.assertFalse(runner._open_gather_character_select(ISLAND_GATHER_PLUS_A, 0))
        self.assertEqual(device.clicks, [])

    def test_disappearing_plus_resets_ready_confirmation(self):
        ready_page = build_frame(ISLAND_GATHER_COLLECT_CHECK, ISLAND_GATHER_PLUS_A)

        def timeline(seconds):
            if device.clicks:
                return CHARACTER_PAGE
            if seconds < .5 or seconds > 2:
                return ready_page
            return UNKNOWN_PAGE

        runner, device = self.make_runner(lambda seconds: ready_page)
        device.timeline = timeline
        self.assertTrue(runner._open_gather_character_select(ISLAND_GATHER_PLUS_A, 0))
        self.assertEqual(len(device.clicks), 1)
        self.assertGreaterEqual(device.clicks[0][0], 2.4)

    def test_failed_open_keeps_three_click_limit_and_cooldown(self):
        frame = build_frame(ISLAND_GATHER_COLLECT_CHECK, ISLAND_GATHER_PLUS_C)
        runner, device = self.make_runner(lambda seconds: frame)
        self.assertFalse(runner._open_gather_character_select(ISLAND_GATHER_PLUS_C, 2))
        self.assertEqual(len(device.clicks), 3)
        times = [seconds for seconds, _ in device.clicks]
        self.assertTrue(all(later - earlier >= 3 for earlier, later in zip(times, times[1:])))

    def test_slow_screenshot_still_requires_a_second_ready_frame(self):
        frame = build_frame(ISLAND_GATHER_COLLECT_CHECK, ISLAND_GATHER_PLUS_B)
        runner, device = self.make_runner(lambda seconds: frame, screenshot_cost=1.2)
        device.timeline = lambda seconds: CHARACTER_PAGE if device.clicks else frame
        self.assertTrue(runner._open_gather_character_select(ISLAND_GATHER_PLUS_B, 1))
        self.assertEqual(device.clicks, [(2.4, ISLAND_GATHER_PLUS_B.name)])
        self.assertEqual(device.screenshot_count, 3)

    def test_custom_worker_and_stamina_fallback_are_preserved(self):
        runner, device = self.make_runner(lambda seconds: CHARACTER_PAGE)
        runner._open_gather_character_select = Mock(return_value=True)
        runner._sort_by_life_level = Mock()
        runner.select_specific_character = Mock(return_value=False)
        runner._select_character_with_stamina_check = Mock(return_value=True)
        runner.confirm_selected_character_closed = Mock(return_value=True)
        device.sleep = Mock()
        self.assertTrue(runner._click_plus_and_select_character(1, ['Akashi', 'Helena']))
        runner.select_specific_character.assert_called_once_with('Helena', min_stamina=GATHER_STAMINA_THRESHOLD)
        runner._select_character_with_stamina_check.assert_called_once_with()
        runner._sort_by_life_level.assert_called_once_with()
        runner.confirm_selected_character_closed.assert_called_once_with('每日采集第2个槽位')


if __name__ == '__main__':
    unittest.main()
