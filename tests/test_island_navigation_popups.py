"""岛屿信息与领取奖励遮挡导航时的截图和状态回归。"""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from module.base import utils
from module.exception import GameStuckError
from module.island.island import (
    Island, ISLAND_INFO_EXIT, ISLAND_GET, ISLAND_MANAGEMENT_CHECK,
    ISLAND_POST_SAFE_AREA, ISLAND_CLICK_SAFE_AREA, POST_MANAGE_PRODUCTION, POST_MANAGE_BUSINESS,
    ISLAND_GATHER_COLLECT, ISLAND_GATHER_COLLECT_CHECK,
)
from module.ui.page import Page, page_island, page_island_management, page_island_phone
from module.ui.assets import ISLAND_GOTO_ISLAND_PHONE
from module.ui.ui import UI
from tests.test_island_goto_management import (
    FakeClock, FakeDevice, ISLAND_PAGE, POSTMANAGE_PAGE, ISLAND_GOTO_MANAGEMENT,
)
from tests.test_island_reward_state import FrameFlow


FIXTURES = Path(__file__).parent / 'fixtures' / 'island_navigation_popups'
SUPPLY_FIXTURES = Path(__file__).parent / 'fixtures' / 'island_air_drop_reward'


class IslandNavigationPopupTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        clock_patch = patch('module.base.timer.time', lambda: self.clock.now)
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        self.addCleanup(utils.set_template_match_non_native_720p,
                        utils.TEMPLATE_MATCH_NON_NATIVE_720P,
                        utils.TEMPLATE_MATCH_NON_NATIVE_720P_RESOLUTION)
        utils.set_template_match_non_native_720p(True, resolution=(2560, 1440))
        for button in (ISLAND_INFO_EXIT, ISLAND_GET, ISLAND_MANAGEMENT_CHECK,
                       ISLAND_GOTO_ISLAND_PHONE, page_island_phone.check_button):
            self.addCleanup(button.clear_offset)

    def island(self, timeline):
        task = object.__new__(Island)
        task.config = SimpleNamespace(SERVER='cn', BUTTON_OFFSET=(20, 20))
        task.device = FakeDevice(self.clock, timeline)
        task.interval_timer = {}
        task.ui_get_current_page = Mock(return_value=SimpleNamespace(name='page_main'))
        task.ui_goto = Mock()
        return task

    def test_existing_templates_recognize_actual_scaled_error_frames(self):
        info = utils.load_image(str(FIXTURES / 'season_info.png'))
        reward = utils.load_image(str(FIXTURES / 'post_reward.png'))
        self.assertTrue(ISLAND_INFO_EXIT.match(info, offset=(30, 30)))
        self.assertTrue(ISLAND_GOTO_MANAGEMENT.match(info, offset=(20, 20)))
        self.assertFalse(ISLAND_GOTO_MANAGEMENT.match_template_color(info, offset=(20, 20)))
        self.assertFalse(ISLAND_GET.match(info, offset=30))
        self.assertTrue(ISLAND_GET.match(reward, offset=30))
        self.assertFalse(ISLAND_INFO_EXIT.match(reward, offset=(30, 30)))
        self.assertFalse(ISLAND_INFO_EXIT.match(ISLAND_PAGE, offset=(30, 30)))

    def test_island_inheritance_handles_info_before_common_ui(self):
        info = utils.load_image(str(FIXTURES / 'season_info.png'))
        task = self.island(lambda t: info)
        with patch.object(UI, 'ui_additional', return_value=False) as common:
            self.assertTrue(task.ui_additional(get_ship=False))
            self.assertFalse(task.ui_additional(get_ship=False))
            common.assert_not_called()
        self.assertEqual(task.device.clicks, [(0.0, 'ISLAND_INFO_EXIT')])

    def test_non_island_popups_keep_common_handler(self):
        task = self.island(lambda t: ISLAND_PAGE)
        with patch.object(UI, 'ui_additional', return_value=True) as common:
            self.assertTrue(task.ui_additional(get_ship=False))
            common.assert_called_once_with(get_ship=False)

    def test_daily_supply_reward_cooldown_never_falls_through_to_common_handler(self):
        reward = utils.load_image(str(SUPPLY_FIXTURES / 'reward_navigation.png'))
        task = self.island(lambda t: reward)
        with patch.object(UI, 'ui_additional', return_value=False) as common:
            self.assertTrue(task.ui_additional(get_ship=False))
            self.assertFalse(task.ui_additional(get_ship=False))
            self.clock.advance(1.8)
            self.assertFalse(task.ui_additional(get_ship=False))
            self.clock.advance(0.3)
            task.device.screenshot()
            self.assertTrue(task.ui_additional(get_ship=False))
            common.assert_not_called()
        self.assertEqual([name for _, name in task.device.clicks], [ISLAND_CLICK_SAFE_AREA.name] * 2)
        stamps = [stamp for stamp, _ in task.device.clicks]
        self.assertGreaterEqual(stamps[1] - stamps[0], 2)

    def test_shared_navigation_closes_daily_supply_before_clicking_phone_on_new_clean_frames(self):
        reward = utils.load_image(str(SUPPLY_FIXTURES / 'reward_navigation.png'))
        # 干净源页及目标页由既有按钮资源组成，不冒充本次现场后续截图。
        clean = np.maximum(ISLAND_PAGE, utils.load_image(ISLAND_GOTO_ISLAND_PHONE.file))
        phone = utils.load_image(page_island_phone.check_button.file)
        self.assertTrue(page_island.check_button.match(reward, offset=(30, 30)))
        self.assertTrue(ISLAND_GOTO_ISLAND_PHONE.match(reward, offset=(30, 30)))
        self.assertFalse(ISLAND_GOTO_ISLAND_PHONE.match_template_color(reward, offset=(30, 30)))
        task = self.island(lambda t: reward if t < 3.2 else clean if t < 5.6 else phone)
        task.ui_goto = UI.ui_goto.__get__(task, Island)
        task.handle_guild_popup_cancel = Mock(return_value=False)
        clicks = []
        original_click = task.device.click

        def click(button, **kwargs):
            clicks.append((button.name, task.device.screenshot_count, task.device.image))
            original_click(button, **kwargs)

        def common_handler(**kwargs):
            self.assertIsNot(task.device.image, reward, '补给关闭冷却中不能落入通用弹窗处理')
            return False

        task.device.click = click
        with patch.object(Page, 'all_pages', {
                page_island.name: page_island,
                page_island_phone.name: page_island_phone,
        }), patch.object(UI, 'ui_additional', side_effect=common_handler):
            try:
                task.ui_goto(page_island_phone, get_ship=False)
            finally:
                Page.clear_connection()
        names = [name for _, name in task.device.clicks]
        self.assertEqual(names, [ISLAND_CLICK_SAFE_AREA.name] * 2 + [ISLAND_GOTO_ISLAND_PHONE.name])
        self.assertGreaterEqual(task.device.clicks[-1][0], 4.2)
        self.assertGreater(clicks[-1][1], clicks[-2][1])
        self.assertIs(clicks[-1][2], clean)
        self.assertIs(task.device.image, phone)
        task.ui_get_current_page.assert_not_called()

    def test_management_entry_recovers_after_info_and_confirms_new_frames(self):
        info = utils.load_image(str(FIXTURES / 'season_info.png'))
        # 强制背景入口也可见，确认模态检测先于背景的稳定计时和点击。
        popup = np.maximum(info, ISLAND_PAGE)
        task = self.island(lambda t: popup if t < 2.0 else ISLAND_PAGE if t < 4.0 else POSTMANAGE_PAGE)
        with patch.object(UI, 'ui_additional', return_value=False):
            task.goto_management()
        self.assertEqual([name for _, name in task.device.clicks],
                         ['ISLAND_INFO_EXIT', 'ISLAND_GOTO_MANAGEMENT'])
        self.assertGreaterEqual(task.device.clicks[1][0], 3.0)

    def test_persistent_info_blocks_background_and_preserves_timeout(self):
        popup = np.maximum(utils.load_image(str(FIXTURES / 'season_info.png')), POSTMANAGE_PAGE)
        task = self.island(lambda t: popup)
        with self.assertRaisesRegex(GameStuckError, '进入岛屿管理页面超时'):
            task.goto_management()
        self.assertEqual({name for _, name in task.device.clicks}, {'ISLAND_INFO_EXIT'})
        stamps = [stamp for stamp, _ in task.device.clicks]
        self.assertTrue(all(b - a >= 3 for a, b in zip(stamps, stamps[1:])))

    def test_management_target_under_reward_is_not_complete(self):
        reward = np.maximum(utils.load_image(str(FIXTURES / 'post_reward.png')), POSTMANAGE_PAGE)
        task = self.island(lambda t: reward if t < 3.0 else POSTMANAGE_PAGE)
        with patch.object(UI, 'ui_additional', return_value=False):
            task.goto_management()
        self.assertEqual({name for _, name in task.device.clicks}, {'ISLAND_POST_SAFE_AREA'})
        self.assertGreaterEqual(self.clock.now - task.device.t0, 3.0)

    def test_known_island_page_uses_override_inside_shared_navigation(self):
        info = utils.load_image(str(FIXTURES / 'season_info.png'))
        task = self.island(lambda t: info if t < 7.0 else ISLAND_PAGE if t < 9.0 else POSTMANAGE_PAGE)
        task.ui_get_current_page.return_value = page_island
        task.ui_goto = UI.ui_goto.__get__(task, Island)
        task.handle_guild_popup_cancel = Mock(return_value=False)
        with patch.object(Page, 'all_pages', {
                page_island.name: page_island,
                page_island_management.name: page_island_management,
        }), patch.object(UI, 'ui_additional', return_value=False):
            try:
                task.goto_management()
            finally:
                Page.clear_connection()
        names = [name for _, name in task.device.clicks]
        self.assertEqual(names, ['ISLAND_INFO_EXIT'] * 3 + ['ISLAND_GOTO_MANAGEMENT'])
        self.assertGreaterEqual(task.device.clicks[-1][0], 8.0)
        stamps = [stamp for stamp, name in task.device.clicks if name == 'ISLAND_INFO_EXIT']
        self.assertTrue(all(b - a >= 3 for a, b in zip(stamps, stamps[1:])))


class IslandPostModeRewardTests(unittest.TestCase):
    def test_reward_blocks_already_visible_target_until_new_frame(self):
        flow = FrameFlow([
            *[[ISLAND_GET, POST_MANAGE_PRODUCTION]] * 8,
            [POST_MANAGE_PRODUCTION],
        ])
        self.assertTrue(flow.run('post_manage_mode', POST_MANAGE_PRODUCTION))
        self.assertEqual(flow.names(), ['ISLAND_POST_SAFE_AREA'])
        self.assertEqual(flow.reads, 9)

    def test_reward_blocks_clicking_other_tab_during_cooldown(self):
        flow = FrameFlow([
            *[[ISLAND_GET, POST_MANAGE_BUSINESS]] * 6,
            [POST_MANAGE_BUSINESS], [POST_MANAGE_PRODUCTION],
        ])
        self.assertTrue(flow.run('post_manage_mode', POST_MANAGE_PRODUCTION))
        self.assertEqual(flow.names(), ['ISLAND_POST_SAFE_AREA', 'POST_MANAGE_BUSINESS'])
        self.assertEqual(flow.clicks[1][1], 7)

    def test_collection_tab_uses_same_reward_priority(self):
        flow = FrameFlow([
            *[[ISLAND_GET, ISLAND_GATHER_COLLECT, ISLAND_GATHER_COLLECT_CHECK]] * 6,
            [ISLAND_GATHER_COLLECT_CHECK],
        ])
        self.assertTrue(flow.run('post_manage_mode_collection'))
        self.assertEqual(flow.names(), ['ISLAND_POST_SAFE_AREA'])
        self.assertEqual(flow.reads, 7)

    def test_persistent_reward_still_raises_and_rate_limits(self):
        flow = FrameFlow([[ISLAND_GET, POST_MANAGE_PRODUCTION]] * 80)
        with self.assertRaisesRegex(GameStuckError, '切换岗位管理页签超时'):
            flow.run('post_manage_mode', POST_MANAGE_PRODUCTION)
        self.assertEqual(set(flow.names()), {'ISLAND_POST_SAFE_AREA'})
        stamps = [stamp for _, _, stamp in flow.clicks]
        self.assertTrue(all(b - a >= 2 for a, b in zip(stamps, stamps[1:])))


if __name__ == '__main__':
    unittest.main()
