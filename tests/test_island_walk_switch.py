"""布莱梅路线切换餐厅的截图状态回归，完全使用离线图像和虚拟时钟。"""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

import module.base.timer as timer_module
from module.exception import GameStuckError
from module.base.utils import load_image
from module.island.island import ISLAND_CHECK, ISLAND_JUU_CAFE_ENTER, Island
from module.island_daily_interact.assets import ROUTE_TWO_OPTION_COMPLETE
from tests.test_island_goto_management import FakeClock, FakeDevice, build_frame


ISLAND_PAGE = build_frame(ISLAND_CHECK)
OPTION_PAGE = build_frame(ISLAND_CHECK, ISLAND_JUU_CAFE_ENTER)
LOADING_PAGE = np.zeros((720, 1280, 3), dtype=np.uint8)


class IslandWalkSwitchTests(unittest.TestCase):
    FIXTURES = Path(__file__).parent / 'fixtures' / 'island_cafe_entry'

    def setUp(self):
        self.clock = FakeClock()
        timer_patch = patch.object(timer_module, 'time', lambda: self.clock.now)
        timer_patch.start()
        self.addCleanup(timer_patch.stop)
        self.addCleanup(ISLAND_CHECK.clear_offset)
        self.addCleanup(ISLAND_JUU_CAFE_ENTER.clear_offset)
        self.addCleanup(ROUTE_TWO_OPTION_COMPLETE.clear_offset)

    def make_island(self, timeline, screenshot_cost=.4):
        device = FakeDevice(self.clock, timeline, screenshot_cost=screenshot_cost)

        def control_delay(seconds):
            # appear_then_click 自带 0.1 秒触控收尾；页面就绪不能额外用休眠猜测。
            self.assertEqual(seconds, .1)
            self.clock.advance(seconds)

        device.sleep = control_delay
        island = Island.__new__(Island)
        island.config = SimpleNamespace(SERVER='cn', BUTTON_OFFSET=(20, 20))
        island.device = device
        island.interval_timer = {}
        return island, device

    def test_visible_island_marker_does_not_skip_initial_option(self):
        """切换前两标识同时可见，入店点击后仍等到新场景稳定。"""
        def timeline(seconds):
            if seconds < 1.2:
                return OPTION_PAGE
            if seconds < 1.6:
                return LOADING_PAGE
            return ISLAND_PAGE

        island, device = self.make_island(timeline)
        island.island_walk_switch_restaurant()

        self.assertEqual(device.clicks, [(.5, 'ISLAND_JUU_CAFE_ENTER')])
        self.assertGreaterEqual(self.clock.now - device.t0, 2.8)
        self.assertGreaterEqual(device.screenshot_count, 6)

    def test_delayed_option_is_not_completed_by_old_island_page(self):
        """原场景标识先出现，入店入口迟到时仍必须真实点击。"""
        def timeline(seconds):
            if seconds < 2:
                return ISLAND_PAGE
            if seconds < 3:
                return OPTION_PAGE
            if seconds < 4:
                return LOADING_PAGE
            return ISLAND_PAGE

        island, device = self.make_island(timeline)
        island.island_walk_switch_restaurant()

        self.assertEqual(len(device.clicks), 1)
        self.assertEqual(device.clicks[0][1], 'ISLAND_JUU_CAFE_ENTER')
        self.assertGreaterEqual(device.clicks[0][0], 2)
        self.assertLessEqual(device.clicks[0][0], 2.5)
        self.assertGreater(self.clock.now - device.t0, 5)

    def test_island_marker_without_option_never_confirms_switch(self):
        island, device = self.make_island(lambda seconds: ISLAND_PAGE)

        with self.assertRaisesRegex(GameStuckError, '切换啾咖啡餐厅超时'):
            island.island_walk_switch_restaurant()

        self.assertEqual(device.clicks, [])
        self.assertLess(self.clock.now - device.t0, 13)

    def test_persistent_option_retries_with_cooldown_and_times_out(self):
        """入店未生效时主页面标识仍可见，不能误返回成功或连续点击。"""
        island, device = self.make_island(lambda seconds: OPTION_PAGE)

        with self.assertRaises(GameStuckError):
            island.island_walk_switch_restaurant()

        times = [seconds for seconds, _ in device.clicks]
        self.assertGreaterEqual(len(times), 3)
        self.assertTrue(all(later - earlier >= 3 for earlier, later in zip(times, times[1:])))
        self.assertLess(self.clock.now - device.t0, 13)

    def test_loading_resets_confirmation_after_brief_island_frame(self):
        def timeline(seconds):
            if seconds < .8:
                return OPTION_PAGE
            if seconds < 1.5:
                return ISLAND_PAGE
            if seconds < 3:
                return LOADING_PAGE
            return ISLAND_PAGE

        island, device = self.make_island(timeline)
        island.island_walk_switch_restaurant()

        self.assertEqual(len(device.clicks), 1)
        self.assertGreater(self.clock.now - device.t0, 4)

    def test_slow_screenshots_still_require_multiple_ready_frames(self):
        island, device = self.make_island(
            lambda seconds: OPTION_PAGE if seconds < 2 else ISLAND_PAGE,
            screenshot_cost=1.5,
        )
        island.island_walk_switch_restaurant()

        self.assertEqual(device.clicks, [(1.6, 'ISLAND_JUU_CAFE_ENTER')])
        self.assertEqual(device.screenshot_count, 4)

    def test_failed_switch_does_not_execute_following_route_step(self):
        island, _ = self.make_island(lambda seconds: OPTION_PAGE)
        island.island_walk_steps = lambda route: (('up', 2600), ('switch', 0), ('left', 600))
        island.island_move = Mock()

        with self.assertRaises(GameStuckError):
            island.island_walk_route('DailyBulaimei')

        island.island_move.assert_called_once_with('up', 2600)

    def test_real_entry_frames_reject_old_talk_and_accept_full_cafe_label(self):
        """真实五帧仅保留两个入口，旧交谈资源不能识别，新文字与门图标能识别。"""
        paths = sorted(self.FIXTURES.glob('enter_and_base_*.png'))
        self.assertEqual(len(paths), 5)
        for path in paths:
            with self.subTest(frame=path.name):
                frame = load_image(str(path))
                self.assertFalse(ROUTE_TWO_OPTION_COMPLETE.appear_on(frame))
                self.assertFalse(ROUTE_TWO_OPTION_COMPLETE.match_template_color(frame))
                self.assertTrue(ISLAND_JUU_CAFE_ENTER.match_template_color(frame, similarity=0.9))
                self.assertGreaterEqual(ISLAND_JUU_CAFE_ENTER.button[1], 335)
                self.assertLess(ISLAND_JUU_CAFE_ENTER.button[3], 385)

    def test_base_label_at_cafe_position_is_not_entry(self):
        """相同门图标、按钮原色甚至上方坐标都不足以把“前往基地”认作入店。"""
        frame = load_image(str(self.FIXTURES / 'base_only_at_entry.png'))
        self.assertFalse(ISLAND_JUU_CAFE_ENTER.match_template_color(frame, similarity=0.9))
        island, device = self.make_island(lambda seconds: np.maximum(frame, ISLAND_PAGE))
        with self.assertRaises(GameStuckError):
            island.island_walk_switch_restaurant()
        self.assertEqual(device.clicks, [])

    def test_real_entry_transition_starts_after_one_click(self):
        frame = np.maximum(
            load_image(str(self.FIXTURES / 'enter_and_base_05.png')), ISLAND_PAGE)
        island, device = self.make_island(
            lambda seconds: frame if seconds < 1.2 else ISLAND_PAGE)
        island.island_walk_switch_restaurant()
        self.assertEqual(device.clicks, [(.5, 'ISLAND_JUU_CAFE_ENTER')])
        self.assertGreaterEqual(device.screenshot_count, 5)

    def test_dimmer_entry_cannot_pass_original_color_confirmation(self):
        """相同图案处于暗幕时不能触发点击，仅恢复原色后才发起入店。"""
        frame = load_image(str(self.FIXTURES / 'enter_and_base_05.png'))
        dimmed = (frame.astype(np.float32) * 0.45).astype(np.uint8)
        self.assertTrue(ISLAND_JUU_CAFE_ENTER.match_luma(dimmed, similarity=0.9))
        self.assertFalse(ISLAND_JUU_CAFE_ENTER.match_template_color(dimmed, similarity=0.9))
        island, device = self.make_island(lambda seconds: np.maximum(dimmed, ISLAND_PAGE))
        with self.assertRaises(GameStuckError):
            island.island_walk_switch_restaurant()
        self.assertEqual(device.clicks, [])


if __name__ == '__main__':
    unittest.main()
