"""白色主界面出击与公会弹窗后的局部提前重试，不连接游戏或用户配置。"""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

import module.base.timer as timer_module
from module.base.timer import Timer
from module.base.utils import load_image
from module.exception import GameStuckError
from module.handler.assets import GUILD_POPUP_CANCEL, GUILD_POPUP_CONFIRM
from module.ui.assets import BACK_ARROW, MAIN_GOTO_CAMPAIGN, MAIN_GOTO_FLEET
from module.ui.page import Page, page_campaign, page_campaign_menu, page_fleet, page_main, page_main_white
from module.ui.ui import UI
from module.ui_white.assets import MAIN_GOTO_CAMPAIGN_WHITE
from tests.test_island_goto_management import FakeClock, FakeDevice, build_frame


WHITE_MAIN = build_frame(MAIN_GOTO_CAMPAIGN_WHITE)
CLASSIC_MAIN = build_frame(MAIN_GOTO_FLEET, MAIN_GOTO_CAMPAIGN)
CAMPAIGN_MENU = build_frame(page_campaign_menu.check_button)
CAMPAIGN = build_frame(page_campaign.check_button, BACK_ARROW)
UNKNOWN = np.zeros((720, 1280, 3), dtype=np.uint8)


def with_popup(frame):
    """只叠加已有匿名按钮资源，保留可见背景来检测模态穿透。"""
    return np.maximum(frame, build_frame(GUILD_POPUP_CANCEL, GUILD_POPUP_CONFIRM))


def shifted_button(button, x_offset):
    frame = load_image(button.file).copy()
    x1, y1, x2, y2 = button.area
    region = frame[y1:y2, x1:x2].copy()
    frame[y1:y2, x1:x2] = 0
    frame[y1:y2, x1 + x_offset:x2 + x_offset] = region
    return frame


class PopupNavigationRetryTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.enterContext(patch.object(timer_module, 'time', lambda: self.clock.now))
        self.enterContext(patch.object(Page, 'all_pages', {
            page_main_white.name: page_main_white,
            page_main.name: page_main,
            page_campaign_menu.name: page_campaign_menu,
            page_campaign.name: page_campaign,
        }))
        self.addCleanup(Page.clear_connection)
        for button in (MAIN_GOTO_CAMPAIGN_WHITE, MAIN_GOTO_FLEET, MAIN_GOTO_CAMPAIGN,
                       page_campaign_menu.check_button, page_campaign.check_button,
                       BACK_ARROW, GUILD_POPUP_CONFIRM, GUILD_POPUP_CANCEL):
            self.addCleanup(button.clear_offset)

    def make_ui(self, timeline, screenshot_cost=0.2, click_cost=0.2):
        device = FakeDevice(self.clock, timeline, screenshot_cost=screenshot_cost,
                            click_cost=click_cost, max_screenshots=250)
        ui = UI.__new__(UI)
        ui.config = SimpleNamespace(SERVER='cn', BUTTON_OFFSET=(20, 20))
        ui.device = device
        ui.interval_timer = {}
        ui.additional_frames = []

        def additional(get_ship=True):
            ui.additional_frames.append(round(self.clock.now - device.t0, 2))
            return False

        ui.ui_additional = Mock(side_effect=additional)
        ui.ui_get_current_page = Mock(side_effect=GameStuckError('离线导航超时'))
        return ui, device

    @staticmethod
    def navigation_clicks(device, name=MAIN_GOTO_CAMPAIGN_WHITE.name):
        return [stamp for stamp, clicked in device.clicks if clicked == name]

    @staticmethod
    def popup_timeline(t, source=WHITE_MAIN, ready_after=0.8, end=2.0):
        if t >= end:
            return CAMPAIGN_MENU
        if 0.2 <= t < ready_after:
            return with_popup(source)
        return source

    def test_guild_cancel_recovers_only_after_fresh_stable_ready_frames(self):
        """日志中的首次导航、取消弹窗、五秒重试缩短为新画面确认后重试。"""
        ui, device = self.make_ui(self.popup_timeline)

        ui.ui_goto(page_campaign_menu, get_ship=False)

        clicks = self.navigation_clicks(device)
        self.assertEqual(len(clicks), 2)
        self.assertEqual(clicks[0], 0.0)
        self.assertLess(clicks[1], 2.0)
        self.assertGreaterEqual(clicks[1], 1.2)
        self.assertIn((0.4, GUILD_POPUP_CANCEL.name), device.clicks)
        self.assertGreaterEqual(device.screenshot_count, 5)
        self.assertFalse(ui.ui_get_current_page.called)

    def test_classic_main_navigation_retains_five_second_retry_interval(self):
        ui, device = self.make_ui(lambda t: CLASSIC_MAIN if t < 5.8 else CAMPAIGN_MENU)

        ui.ui_goto(page_campaign_menu, get_ship=False)

        clicks = self.navigation_clicks(device, MAIN_GOTO_CAMPAIGN.name)
        self.assertEqual(len(clicks), 2)
        self.assertGreaterEqual(clicks[1] - clicks[0], 5.0)
        self.assertNotIn(GUILD_POPUP_CANCEL.name, [name for _, name in device.clicks])

    def test_white_main_stable_entry_allows_one_retry_before_five_seconds(self):
        ui, device = self.make_ui(lambda t: WHITE_MAIN if t < 3.2 else CAMPAIGN_MENU)

        ui.ui_goto(page_campaign_menu, get_ship=False)

        clicks = self.navigation_clicks(device)
        self.assertEqual(len(clicks), 2)
        self.assertEqual(clicks[0], 0.0)
        self.assertGreaterEqual(clicks[1], 2.0)
        self.assertLess(clicks[1], 3.2)
        self.assertGreaterEqual(device.screenshot_count, 10)
        self.assertNotIn(GUILD_POPUP_CANCEL.name, [name for _, name in device.clicks])

    def test_white_main_consumed_retry_keeps_later_clicks_five_seconds_apart(self):
        ui, device = self.make_ui(lambda t: WHITE_MAIN if t < 8.2 else CAMPAIGN_MENU)

        ui.ui_goto(page_campaign_menu, get_ship=False)

        clicks = self.navigation_clicks(device)
        self.assertEqual(len(clicks), 3)
        self.assertTrue(2.0 <= clicks[1] - clicks[0] < 5.0)
        self.assertGreaterEqual(clicks[2] - clicks[1], 5.0)

    def test_normal_fast_transition_does_not_add_a_retry_or_wait(self):
        ui, device = self.make_ui(lambda t: WHITE_MAIN if t < 0.4 else CAMPAIGN_MENU)

        ui.ui_goto(page_campaign_menu, get_ship=False)

        self.assertEqual(self.navigation_clicks(device), [0.0])
        self.assertLess(self.clock.now - device.t0, 1.0)

    def test_other_white_main_destination_still_uses_five_second_retry(self):
        fleet = build_frame(page_fleet.check_button)
        ui, device = self.make_ui(lambda t: WHITE_MAIN if t < 5.8 else fleet)
        with patch.object(Page, 'all_pages', {
            page_main_white.name: page_main_white, page_fleet.name: page_fleet,
        }):
            ui.ui_goto(page_fleet, get_ship=False)

        clicks = self.navigation_clicks(device, page_main_white.links[page_fleet].name)
        self.assertEqual(len(clicks), 2)
        self.assertGreaterEqual(clicks[1] - clicks[0], 5.0)

    def test_white_main_darkened_entry_does_not_allow_early_retry(self):
        dark = WHITE_MAIN.copy()
        x1, y1, x2, y2 = MAIN_GOTO_CAMPAIGN_WHITE.area
        dark[y1:y2, x1:x2] //= 2
        self.assertTrue(MAIN_GOTO_CAMPAIGN_WHITE.match(dark, offset=(30, 30)))
        self.assertFalse(MAIN_GOTO_CAMPAIGN_WHITE.match_template_color(dark, offset=(30, 30)))
        ui, device = self.make_ui(lambda t: (
            CAMPAIGN_MENU if t >= 5.8 else WHITE_MAIN if t < 0.2 else dark))

        ui.ui_goto(page_campaign_menu, get_ship=False)

        clicks = self.navigation_clicks(device)
        self.assertEqual(len(clicks), 2)
        self.assertGreaterEqual(clicks[1] - clicks[0], 5.0)

    def test_white_main_moved_entry_restarts_two_second_confirmation(self):
        moved = shifted_button(MAIN_GOTO_CAMPAIGN_WHITE, 9)
        ui, device = self.make_ui(lambda t: (
            CAMPAIGN_MENU if t >= 4.2 else WHITE_MAIN if t < 1.2 else moved))

        ui.ui_goto(page_campaign_menu, get_ship=False)

        clicks = self.navigation_clicks(device)
        self.assertEqual(len(clicks), 2)
        self.assertGreaterEqual(clicks[1], 3.2)
        self.assertLess(clicks[1], 4.2)

    def test_white_main_unknown_frames_cannot_accumulate_ready_time(self):
        def timeline(t):
            if t >= 5.8:
                return CAMPAIGN_MENU
            return UNKNOWN if 0.2 <= t < 3.0 else WHITE_MAIN
        ui, device = self.make_ui(timeline)

        ui.ui_goto(page_campaign_menu, get_ship=False)

        clicks = self.navigation_clicks(device)
        self.assertEqual(len(clicks), 2)
        self.assertGreaterEqual(clicks[1], 5.0)

    def test_normal_early_retry_and_later_guild_popup_share_one_allowance(self):
        def timeline(t):
            if t >= 8.2:
                return CAMPAIGN_MENU
            return with_popup(WHITE_MAIN) if 2.8 <= t < 3.4 else WHITE_MAIN
        ui, device = self.make_ui(timeline)

        ui.ui_goto(page_campaign_menu, get_ship=False)

        clicks = self.navigation_clicks(device)
        self.assertEqual(len(clicks), 3)
        self.assertLess(clicks[1], 2.8)
        self.assertGreaterEqual(clicks[2] - clicks[1], 5.0)
        self.assertIn(GUILD_POPUP_CANCEL.name, [name for _, name in device.clicks])

    def test_additional_action_restarts_normal_entry_confirmation(self):
        ui, device = self.make_ui(lambda t: WHITE_MAIN if t < 4.6 else CAMPAIGN_MENU)
        handled = []
        def additional(get_ship=True):
            stamp = round(self.clock.now - device.t0, 2)
            if stamp == 1.6 and not handled:
                handled.append(stamp)
                return True
            return False
        ui.ui_additional = Mock(side_effect=additional)

        ui.ui_goto(page_campaign_menu, get_ship=False)

        self.assertEqual(handled, [1.6])
        clicks = self.navigation_clicks(device)
        self.assertEqual(len(clicks), 2)
        self.assertGreaterEqual(clicks[1], 3.6)
        self.assertLess(clicks[1], 4.6)

    def test_remaining_modal_never_clicks_background_while_cancel_is_cooling(self):
        ui, device = self.make_ui(lambda t: self.popup_timeline(t, ready_after=3.0, end=4.5))

        ui.ui_goto(page_campaign_menu, get_ship=False)

        clicks = self.navigation_clicks(device)
        self.assertEqual(len(clicks), 2)
        self.assertGreaterEqual(clicks[1], 3.4)
        self.assertTrue(all(stamp < 0.2 or stamp >= 3.4 for stamp in clicks))
        self.assertTrue(all(not 0.2 <= stamp < 3.0 for stamp in ui.additional_frames))

    def test_modal_before_first_navigation_does_not_enable_popup_short_retry(self):
        def timeline(t):
            if t < 1.0:
                return with_popup(WHITE_MAIN)
            return WHITE_MAIN if t < 6.8 else CAMPAIGN_MENU

        ui, device = self.make_ui(timeline)

        ui.ui_goto(page_campaign_menu, get_ship=False)

        clicks = self.navigation_clicks(device)
        self.assertEqual(len(clicks), 2)
        self.assertGreaterEqual(clicks[0], 1.0)
        # 弹窗前没有导航点击，不授予 0.4 秒补点；首次导航后仍可按新规则稳定两秒重试。
        self.assertGreaterEqual(clicks[1] - clicks[0], 2.0)
        self.assertLess(clicks[1] - clicks[0], 5.0)

    def test_repeated_popups_only_allow_one_early_retry_per_source(self):
        def timeline(t):
            if t >= 7.3:
                return CAMPAIGN_MENU
            if 0.2 <= t < 0.8 or 1.6 <= t < 2.2:
                return with_popup(WHITE_MAIN)
            return WHITE_MAIN

        ui, device = self.make_ui(timeline)

        ui.ui_goto(page_campaign_menu, get_ship=False)

        clicks = self.navigation_clicks(device)
        self.assertEqual(len(clicks), 3)
        self.assertLess(clicks[1] - clicks[0], 5.0)
        self.assertGreaterEqual(clicks[2] - clicks[1], 5.0)
        self.assertTrue(all(not 1.6 <= stamp < 2.2 for stamp in clicks))

    def test_unknown_source_cannot_use_pending_retry(self):
        def timeline(t):
            if t >= 6.0:
                return CAMPAIGN_MENU
            if 0.2 <= t < 0.8:
                return with_popup(WHITE_MAIN)
            if 0.8 <= t < 4.9:
                return UNKNOWN
            return WHITE_MAIN

        ui, device = self.make_ui(timeline)

        ui.ui_goto(page_campaign_menu, get_ship=False)

        clicks = self.navigation_clicks(device)
        self.assertEqual(len(clicks), 2)
        self.assertGreaterEqual(clicks[1] - clicks[0], 5.0)

    def test_visible_source_without_actual_navigation_button_keeps_cooldown(self):
        header_only = build_frame(MAIN_GOTO_FLEET)

        def timeline(t):
            if t >= 6.0:
                return CAMPAIGN_MENU
            if 0.2 <= t < 0.8:
                return with_popup(CLASSIC_MAIN)
            if 0.8 <= t < 5.0:
                return header_only
            return CLASSIC_MAIN

        ui, device = self.make_ui(timeline)

        ui.ui_goto(page_campaign_menu, get_ship=False)

        clicks = self.navigation_clicks(device, MAIN_GOTO_CAMPAIGN.name)
        self.assertEqual(len(clicks), 2)
        self.assertGreaterEqual(clicks[1] - clicks[0], 5.0)

    def test_navigation_button_darkened_does_not_enable_early_retry(self):
        dark = CLASSIC_MAIN.copy()
        x1, y1, x2, y2 = MAIN_GOTO_CAMPAIGN.area
        dark[y1:y2, x1:x2] //= 2
        self.assertTrue(MAIN_GOTO_CAMPAIGN.match(dark, offset=(30, 30)))
        self.assertFalse(MAIN_GOTO_CAMPAIGN.match_template_color(dark, offset=(30, 30)))

        def timeline(t):
            if t >= 6.0:
                return CAMPAIGN_MENU
            if 0.2 <= t < 0.8:
                return with_popup(CLASSIC_MAIN)
            if 0.8 <= t < 5.0:
                return dark
            return CLASSIC_MAIN

        ui, device = self.make_ui(timeline)

        ui.ui_goto(page_campaign_menu, get_ship=False)

        clicks = self.navigation_clicks(device, MAIN_GOTO_CAMPAIGN.name)
        self.assertEqual(len(clicks), 2)
        self.assertGreaterEqual(clicks[1] - clicks[0], 5.0)

    def test_moving_button_restarts_confirmation_instead_of_reusing_old_position(self):
        moved = np.maximum(build_frame(MAIN_GOTO_FLEET), shifted_button(MAIN_GOTO_CAMPAIGN, 9))

        def timeline(t):
            if t >= 3.0:
                return CAMPAIGN_MENU
            if 0.2 <= t < 0.8:
                return with_popup(CLASSIC_MAIN)
            if 1.0 <= t < 1.4:
                return moved
            return CLASSIC_MAIN

        ui, device = self.make_ui(timeline)

        ui.ui_goto(page_campaign_menu, get_ship=False)

        clicks = self.navigation_clicks(device, MAIN_GOTO_CAMPAIGN.name)
        self.assertEqual(len(clicks), 2)
        self.assertGreaterEqual(clicks[1], 1.8)
        self.assertLess(clicks[1], 3.0)

    def test_slow_screenshots_still_require_three_new_ready_observations(self):
        ui, device = self.make_ui(
            lambda t: self.popup_timeline(t, ready_after=1.8, end=5.2), screenshot_cost=0.8)

        ui.ui_goto(page_campaign_menu, get_ship=False)

        clicks = self.navigation_clicks(device)
        self.assertEqual(len(clicks), 2)
        self.assertGreaterEqual(clicks[1], 3.6)
        self.assertLess(clicks[1], 5.0)

    def test_other_page_switch_discards_previous_source_fast_retry(self):
        def timeline(t):
            if t >= 6.8:
                return CAMPAIGN_MENU
            if 0.2 <= t < 0.8:
                return with_popup(WHITE_MAIN)
            if 0.8 <= t < 1.2:
                return CAMPAIGN
            return WHITE_MAIN

        ui, device = self.make_ui(timeline)

        ui.ui_goto(page_campaign_menu, get_ship=False)

        clicks = self.navigation_clicks(device)
        self.assertEqual(len(clicks), 2)
        self.assertGreaterEqual(clicks[1] - clicks[0], 5.0)
        self.assertIn(BACK_ARROW.name, [name for _, name in device.clicks])

    def test_original_cooldown_expiry_does_not_add_another_ready_wait(self):
        def timeline(t):
            if t >= 5.4:
                return CAMPAIGN_MENU
            if 0.2 <= t < 0.8:
                return with_popup(WHITE_MAIN)
            if 0.8 <= t < 5.1:
                return UNKNOWN
            return WHITE_MAIN

        ui, device = self.make_ui(timeline)

        ui.ui_goto(page_campaign_menu, get_ship=False)

        clicks = self.navigation_clicks(device)
        self.assertEqual(len(clicks), 2)
        self.assertGreaterEqual(clicks[1], 5.0)
        self.assertLess(clicks[1], 5.4)

    def test_additional_popup_action_restarts_ready_confirmation(self):
        ui, device = self.make_ui(lambda t: self.popup_timeline(t, end=2.6))
        handled_frames = []

        def additional(get_ship=True):
            stamp = round(self.clock.now - device.t0, 2)
            if stamp == 1.0 and not handled_frames:
                handled_frames.append(stamp)
                return True
            return False

        ui.ui_additional = Mock(side_effect=additional)

        ui.ui_goto(page_campaign_menu, get_ship=False)

        self.assertEqual(handled_frames, [1.0])
        clicks = self.navigation_clicks(device)
        self.assertEqual(len(clicks), 2)
        self.assertGreaterEqual(clicks[1], 1.6)
        self.assertLess(clicks[1], 2.6)

    def test_fast_retry_does_not_clear_other_page_or_popup_timers(self):
        ui, device = self.make_ui(self.popup_timeline)
        first_click = device.click
        other_timer = Timer(5)
        unrelated_timer = Timer(17)

        def click(button, control_check=True):
            first_click(button, control_check=control_check)
            if button is GUILD_POPUP_CANCEL:
                ui.interval_timer[page_campaign.check_button.name] = other_timer.reset()
                ui.interval_timer['unrelated_task_button'] = unrelated_timer.reset()

        device.click = click

        ui.ui_goto(page_campaign_menu, get_ship=False)

        self.assertEqual(len(self.navigation_clicks(device)), 2)
        self.assertTrue(other_timer.started())
        self.assertFalse(other_timer.reached())
        self.assertTrue(unrelated_timer.started())
        self.assertFalse(unrelated_timer.reached())
        self.assertTrue(ui.interval_timer[GUILD_POPUP_CANCEL.name].started())

    def test_destination_already_reached_precedes_popup_and_fast_retry(self):
        ui, device = self.make_ui(lambda t: (
            with_popup(CAMPAIGN_MENU) if t >= 0.8 else self.popup_timeline(t)))

        ui.ui_goto(page_campaign_menu, get_ship=False)

        self.assertEqual(self.navigation_clicks(device), [0.0])
        self.assertEqual(len([name for _, name in device.clicks
                              if name == GUILD_POPUP_CANCEL.name]), 1)

    def test_never_ready_retains_unknown_page_timeout_and_recovery_argument(self):
        def timeline(t):
            if t < 0.2:
                return WHITE_MAIN
            if t < 0.8:
                return with_popup(WHITE_MAIN)
            return UNKNOWN

        ui, device = self.make_ui(timeline)

        with self.assertRaises(GameStuckError):
            ui.ui_goto(page_campaign_menu, get_ship=False, recover_unknown=False)

        self.assertEqual(self.navigation_clicks(device), [0.0])
        self.assertGreaterEqual(self.clock.now - device.t0, 30.0)
        ui.ui_get_current_page.assert_called_once_with(
            skip_first_screenshot=True, recover_unknown=False)


if __name__ == '__main__':
    unittest.main()
