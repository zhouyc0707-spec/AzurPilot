"""生活区导航按真实页面进展限时；模拟时钟和识别结果，不连接账号。"""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import module.base.timer as timer_module
from module.exception import GameStuckError
from module.handler.assets import GAME_TIPS, GUILD_POPUP_CANCEL, GUILD_POPUP_CONFIRM
from module.ui.page import (
    Page, page_build, page_dormmenu, page_fleet, page_main, page_main_white,
    page_meowfficer, page_mission,
)
from module.ui.ui import UI


class DormNavigationProgressTest(unittest.TestCase):
    def setUp(self):
        self.clock = SimpleNamespace(now=1000.0)
        self.enterContext(patch.object(timer_module, 'time', lambda: self.clock.now))
        pages = (page_main, page_main_white, page_build, page_mission, page_dormmenu,
                 page_meowfficer, page_fleet)
        self.enterContext(patch.object(Page, 'all_pages', {page.name: page for page in pages}))
        self.enterContext(patch('module.ui.ui.IslandEntryDiagnostic', return_value=Mock()))
        self.enterContext(patch('module.ui.ui.logger'))
        self.addCleanup(Page.clear_connection)

    def make_ui(self, timeline):
        ui = UI.__new__(UI)
        ui.config = SimpleNamespace(SERVER='cn', BUTTON_OFFSET=(20, 20))
        ui.interval_timer = {}
        ui.state = timeline(0)
        ui.clicks = []
        ui.device = SimpleNamespace(image=None)

        def screenshot():
            self.clock.now += 0.25
            if self.clock.now > 1110:
                raise AssertionError('导航没有按时结束')
            ui.state = timeline(self.clock.now - 1000)

        ui.device.screenshot = screenshot
        ui.device.click = lambda button: ui.clicks.append((self.clock.now - 1000, button.name))
        ui.ui_page_appear = lambda page, **kwargs: ui.state[0] is page
        ui.is_in_main = lambda **kwargs: ui.state[0] in (page_main, page_main_white)
        ui.match_template_color = Mock(return_value=True)
        ui.ui_additional = Mock(return_value=False)
        ui.ui_get_current_page = Mock(side_effect=AssertionError('不应通过重新定位绕过无进展保护'))

        def appear(button, interval=0, **kwargs):
            visible = (button is ui.state[0].check_button
                       or (ui.state[2] and button in (GUILD_POPUP_CANCEL, GUILD_POPUP_CONFIRM)))
            if not visible:
                return False
            if interval:
                timer = ui.get_interval_timer(button, interval=interval)
                if not timer.reached():
                    return False
                timer.reset()
            return True

        def modal_click(button, visible):
            if visible and ui.get_interval_timer(button, interval=2).reached():
                ui.device.click(button)
                ui.interval_reset(button, interval=2)
                return True
            return False

        ui.appear = appear
        ui.game_tips_appear = lambda: GAME_TIPS if ui.state[1] else None
        ui.handle_game_tips = lambda button=None: modal_click(GAME_TIPS, ui.state[1])
        ui.handle_guild_popup_cancel = lambda: modal_click(GUILD_POPUP_CANCEL, ui.state[2])
        return ui

    def assert_connections_cleared(self):
        self.assertTrue(all(page.parent is None for page in Page.iter_pages()))

    def test_build_mission_and_double_tips_loop_cannot_extend_deadline(self):
        def timeline(t):
            cycle = int(t // 6)
            if t % 6 < 2:
                return (page_build if cycle % 2 == 0 else page_mission, False, False)
            return page_main, True, False
        ui = self.make_ui(timeline)
        with self.assertRaisesRegex(GameStuckError, '30秒没有页面进展'):
            ui.ui_goto(page_dormmenu, get_ship=False)
        self.assertLessEqual(self.clock.now - 1000, 30.5)
        names = [name for _, name in ui.clicks]
        self.assertIn(['GOTO_MAIN', 'GAME_TIPS', 'GAME_TIPS'],
                      [names[i:i + 3] for i in range(len(names) - 2)])
        self.assertFalse(any('DORMMENU' in name for name in names))
        ui.ui_get_current_page.assert_not_called()
        self.assert_connections_cleared()

    def test_real_progress_allows_total_navigation_longer_than_thirty_seconds(self):
        def timeline(t):
            page = page_build if t < 20 else page_main if t < 40 else page_dormmenu if t < 60 else page_meowfficer
            return page, False, False
        ui = self.make_ui(timeline)
        ui.ui_goto(page_meowfficer, get_ship=False)
        self.assertEqual(self.clock.now - 1000, 60)
        self.assert_connections_cleared()
        ui.ui_get_current_page.assert_not_called()

    def test_equal_distance_main_themes_cannot_keep_resetting_progress(self):
        ui = self.make_ui(lambda t: (page_main if int(t) % 2 else page_main_white, False, False))
        with self.assertRaises(GameStuckError):
            ui.ui_goto(page_dormmenu, get_ship=False)
        self.assertLessEqual(self.clock.now - 1000, 30.5)
        self.assert_connections_cleared()

    def test_target_confirmed_on_timeout_frame_still_succeeds(self):
        ui = self.make_ui(lambda t: (page_main if t < 30.25 else page_dormmenu, False, False))
        ui.ui_goto(page_dormmenu, get_ship=False)
        self.assertEqual(self.clock.now - 1000, 30.25)
        self.assert_connections_cleared()

    def test_closer_source_on_timeout_frame_is_progress_even_when_click_is_cooling(self):
        def timeline(t):
            if t < 30.25:
                return page_build, False, False
            if t < 32:
                return page_main, False, False
            return page_dormmenu, False, False
        ui = self.make_ui(timeline)
        original_appear = ui.appear

        def appear(button, interval=0, **kwargs):
            if button is page_main.check_button and interval:
                return False
            return original_appear(button, interval=interval, **kwargs)

        ui.appear = appear
        ui.ui_goto(page_dormmenu, get_ship=False)
        self.assertEqual(self.clock.now - 1000, 32)
        self.assert_connections_cleared()

    def test_modal_cannot_make_closer_background_count_as_timeout_progress(self):
        for modal in ('tips', 'guild'):
            with self.subTest(modal=modal):
                self.clock.now = 1000
                ui = self.make_ui(lambda t: (
                    (page_build, False, False) if t < 30.25 else
                    (page_main, modal == 'tips', modal == 'guild')))
                with self.assertRaises(GameStuckError):
                    ui.ui_goto(page_dormmenu, get_ship=False)
                self.assertEqual(self.clock.now - 1000, 30.25)
                self.assert_connections_cleared()

    def test_persistent_tips_covering_target_never_click_through_or_succeed(self):
        ui = self.make_ui(lambda t: (page_dormmenu, True, False))
        with self.assertRaises(GameStuckError):
            ui.ui_goto(page_dormmenu, get_ship=False)
        self.assertLessEqual(self.clock.now - 1000, 30.5)
        self.assertTrue(ui.clicks)
        self.assertTrue(all(name == 'GAME_TIPS' for _, name in ui.clicks))
        self.assertTrue(all(b[0] - a[0] >= 2 for a, b in zip(ui.clicks, ui.clicks[1:])))
        ui.ui_additional.assert_not_called()
        self.assert_connections_cleared()

    def test_persistent_guild_cancel_cannot_extend_deadline(self):
        ui = self.make_ui(lambda t: (page_main, False, True))
        with self.assertRaises(GameStuckError):
            ui.ui_goto(page_dormmenu, get_ship=False)
        self.assertLessEqual(self.clock.now - 1000, 30.5)
        self.assertTrue(all(name == GUILD_POPUP_CANCEL.name for _, name in ui.clicks))
        self.assert_connections_cleared()

    def test_unrelated_route_keeps_original_navigation_behavior(self):
        ui = self.make_ui(lambda t: (page_main if t < 40 else page_fleet, False, False))
        ui.ui_goto(page_fleet, get_ship=False)
        self.assertEqual(self.clock.now - 1000, 40)
        ui.ui_get_current_page.assert_not_called()
        self.assert_connections_cleared()


if __name__ == '__main__':
    unittest.main()
