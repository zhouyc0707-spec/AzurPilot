"""地图导航的实拍误识别回放与有界状态循环回归。"""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from module.base.base import ModuleBase
from module.base.button import Button
from module.base import utils
from module.island import island as island_module
from module.island.island import Island
from module.ui.ui import UI
from tests.test_island_goto_management import FakeClock, FakeDevice


class MapEntryFlow:
    """复用真实识别和计时器，仅替换设备、通用导航与无关弹窗处理。"""

    loop = ModuleBase.loop
    ensure_button = ModuleBase.ensure_button
    appear = ModuleBase.appear
    appear_then_click = ModuleBase.appear_then_click
    match_template_color = ModuleBase.match_template_color
    ui_page_appear = UI.ui_page_appear
    is_post_detail_visible = Island.is_post_detail_visible
    _island_map_entry_status = Island._island_map_entry_status
    _island_map_popup_pending = Island._island_map_popup_pending
    _wait_island_map_entry = Island._wait_island_map_entry
    goto_island_map = Island.goto_island_map

    def __init__(self, device, expect_friend=False):
        self.device = device
        self.config = SimpleNamespace(SERVER='cn')
        self.interval_timer = {}
        self._island_expect_friend = expect_friend
        self.ui_goto = Mock()
        self.ui_additional = Mock(return_value=False)
        self.game_tips_appear = Mock(return_value=None)
        self.is_business_event_popup = Mock(return_value=False)
        self.handle_business_event_popup = Mock(return_value=False)
        self.handle_island_reward_popup = Mock(return_value=False)


class MapPage:
    def __init__(self, name, button):
        self.name = name
        self.check_button = button
        self.links = {}


class IslandMapEntryTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.enterContext(patch('module.base.timer.time', lambda: self.clock.now))
        self.enterContext(patch('module.island.island.logger'))
        self.enterContext(patch('module.island.island.is_daily_supply_reward', return_value=False))
        self.addCleanup(utils.set_template_match_non_native_720p,
                        utils.TEMPLATE_MATCH_NON_NATIVE_720P,
                        utils.TEMPLATE_MATCH_NON_NATIVE_720P_RESOLUTION)
        utils.set_template_match_non_native_720p(False)
        # 不改全局服务器；实拍和合成状态都明确采用独立 CN 资源。
        names = ('ISLAND_CHECK', 'ISLAND_GOTO_MAP', 'ISLAND_MAP_CHECK',
                 'ISLAND_ACCESS_MAP', 'AIR_DROP_RUN_AWAY', 'ISLAND_GET',
                 'ISLAND_INFO_EXIT', 'ISLAND_POSTMANAGE_CHECK',
                 'ISLAND_MANAGEMENT_CHECK', 'ISLAND_WAREHOUSE_CHECK',
                 'POST_MANAGE_PRODUCTION', 'ISLAND_POSTMANAGE_GOTO_MANAGEMENT',
                 'ISLAND_POST_CHECK', 'ISLAND_POST_VACANT_CHECK', 'ISLAND_POST_SELECT',
                 'ISLAND_WORKING', 'POST_ADD', 'POST_GET')
        self.buttons = {}
        for name in names:
            button = getattr(island_module, name)
            self.buttons[name] = Button(
                area=button.raw_area['cn'], color=button.raw_color['cn'],
                button=button.raw_button['cn'], file=button.raw_file['cn'], name=name)
        self.enterContext(patch.multiple(island_module, **self.buttons))
        # 仅替换本模块的 Page 引用，避免污染其他测试的全局导航图。
        self.pages = {
            name: MapPage(name, self.buttons[button])
            for name, button in (
                ('page_island', 'ISLAND_CHECK'),
                ('page_island_postmanage', 'ISLAND_POSTMANAGE_CHECK'),
                ('page_island_management', 'ISLAND_MANAGEMENT_CHECK'),
                ('page_island_warehouse', 'ISLAND_WAREHOUSE_CHECK'))
        }
        self.pages['page_island_postmanage'].links[self.pages['page_island_management']] = (
            self.buttons['ISLAND_POSTMANAGE_GOTO_MANAGEMENT'])
        self.enterContext(patch.multiple(island_module, **self.pages))
        self.empty = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.home = self.frame('ISLAND_CHECK', 'ISLAND_GOTO_MAP')
        self.map = self.frame('ISLAND_MAP_CHECK')
        self.friend = self.frame('AIR_DROP_RUN_AWAY', 'ISLAND_ACCESS_MAP')
        self.error_frame = utils.load_image(str(
            Path(__file__).parent / 'fixtures/island_map_entry/postmanage_false_map_entry.png'))

    def frame(self, *names):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        for name in names:
            frame = np.maximum(frame, utils.load_image(self.buttons[name].file))
        return frame

    def flow(self, timeline, expect_friend=False):
        device = FakeDevice(self.clock, timeline, max_screenshots=200)
        return MapEntryFlow(device, expect_friend=expect_friend)

    def names(self, flow):
        return [name for _, name in flow.device.clicks]

    def test_real_postmanage_frame_rejects_color_only_map_entry(self):
        for non_native in (False, True):
            with self.subTest(non_native=non_native):
                utils.set_template_match_non_native_720p(non_native)
                button = self.buttons['ISLAND_GOTO_MAP']
                self.assertTrue(button.appear_on(self.error_frame, threshold=30))
                self.assertFalse(button.match_template_color(self.error_frame, offset=(20, 20)))
                flow = self.flow(lambda _: self.error_frame)
                status = flow._wait_island_map_entry()
                self.assertFalse(status['home_map'])
                self.assertFalse(status['in_map'])
                self.assertTrue(status['postmanage'])
                self.assertEqual(flow.device.screenshot_count, 1)

    def test_known_postmanage_does_not_wait_for_a_nonexistent_map_button(self):
        flow = self.flow(lambda _: self.frame('ISLAND_POSTMANAGE_CHECK'))
        self.assertTrue(flow._wait_island_map_entry()['postmanage'])
        self.assertEqual(flow.device.screenshot_count, 1)

    def test_real_wrong_page_never_clicks_map_even_if_navigation_does_not_progress(self):
        flow = self.flow(lambda _: self.error_frame)
        self.assertFalse(flow.goto_island_map())
        flow.ui_goto.assert_not_called()
        self.assertEqual(set(self.names(flow)), {'ISLAND_POSTMANAGE_GOTO_MANAGEMENT'})
        self.assertLessEqual(len(flow.device.clicks), 7)
        stamps = [stamp for stamp, _ in flow.device.clicks]
        for before, after in zip(stamps, stamps[1:]):
            self.assertGreaterEqual(after - before, 3)
        self.assertLess(flow.device.screenshot_count, 65)

    def test_postmanage_uses_navigation_before_map_entry(self):
        current = {'image': self.error_frame, 'back_at': None}
        management = self.frame('ISLAND_MANAGEMENT_CHECK')

        def timeline(t):
            if current['back_at'] is not None and t >= current['back_at'] + 2:
                current['image'] = management
                current['back_at'] = None
            return current['image']

        flow = self.flow(timeline)

        def navigate(destination, get_ship):
            self.assertEqual(self.names(flow), ['ISLAND_POSTMANAGE_GOTO_MANAGEMENT'])
            self.assertIs(flow.device.image, management)
            self.assertIs(destination, self.pages['page_island'])
            self.assertFalse(get_ship)
            current['image'] = self.home

        flow.ui_goto.side_effect = navigate
        click = flow.device.click

        def click_and_open(button):
            click(button)
            if button.name == 'ISLAND_POSTMANAGE_GOTO_MANAGEMENT':
                current['image'] = self.empty
                current['back_at'] = flow.device.clock.now - flow.device.t0
            else:
                current['image'] = self.map

        flow.device.click = click_and_open
        self.assertTrue(flow.goto_island_map())
        flow.ui_goto.assert_called_once()
        self.assertEqual(self.names(flow), ['ISLAND_POSTMANAGE_GOTO_MANAGEMENT', 'ISLAND_GOTO_MAP'])
        self.assertGreaterEqual(flow.device.clicks[1][0] - flow.device.clicks[0][0], 3)
        self.assertIs(flow.device.image, self.map)

    def test_home_click_ends_only_on_new_map_frame(self):
        flow = self.flow(lambda t: self.home if t < 1.4 else self.map)
        self.assertTrue(flow.goto_island_map())
        flow.ui_goto.assert_not_called()
        self.assertEqual(self.names(flow), ['ISLAND_GOTO_MAP'])
        self.assertGreaterEqual(flow.device.screenshot_count, 3)

    def test_already_in_map_needs_no_entry_click(self):
        flow = self.flow(lambda _: self.map)
        self.assertTrue(flow.goto_island_map())
        self.assertEqual(flow.device.clicks, [])
        flow.ui_goto.assert_not_called()

    def test_post_add_color_outside_postmanage_does_not_block_navigation(self):
        for source, expected in ((self.map, []), (self.home, ['ISLAND_GOTO_MAP'])):
            with self.subTest(expected=expected):
                frame = source.copy()
                button = self.buttons['POST_ADD']
                x1, y1, x2, y2 = button.area
                frame[y1:y2, x1:x2] = button.color
                current = {'image': frame}
                flow = self.flow(lambda _: current['image'])
                click = flow.device.click

                def click_and_open(button):
                    click(button)
                    current['image'] = self.map

                flow.device.click = click_and_open
                # 旧详情辅助方法仅靠此处颜色即可误判，地图导航须先确认岗位页面。
                self.assertTrue(flow.is_post_detail_visible())
                self.assertTrue(flow.goto_island_map())
                self.assertEqual(self.names(flow), expected)
                flow.ui_goto.assert_not_called()

    def test_map_icon_without_home_page_is_not_clickable(self):
        flow = self.flow(lambda _: self.frame('ISLAND_GOTO_MAP'))
        self.assertFalse(flow.goto_island_map())
        self.assertEqual(flow.device.clicks, [])
        flow.ui_goto.assert_called_once()

    def test_slow_map_entry_keeps_three_second_click_interval(self):
        flow = self.flow(lambda t: self.home if t < 8 else self.map)
        self.assertTrue(flow.goto_island_map())
        stamps = [stamp for stamp, _ in flow.device.clicks]
        self.assertGreaterEqual(len(stamps), 2)
        self.assertLessEqual(len(stamps), 3)
        for before, after in zip(stamps, stamps[1:]):
            self.assertGreaterEqual(after - before, 3)

    def test_stuck_home_has_bounded_clicks_and_timeout(self):
        flow = self.flow(lambda _: self.home)
        self.assertFalse(flow.goto_island_map())
        self.assertGreaterEqual(len(flow.device.clicks), 2)
        self.assertLessEqual(len(flow.device.clicks), 7)
        self.assertLess(flow.device.screenshot_count, 65)

    def test_friend_map_preserves_friend_context(self):
        for frame in (self.friend, self.frame('ISLAND_ACCESS_MAP')):
            with self.subTest(access_only=frame is not self.friend):
                flow = self.flow(lambda t: frame if t < 1.4 else self.map, expect_friend=True)
                self.assertTrue(flow.goto_island_map())
                self.assertEqual(self.names(flow), ['ISLAND_ACCESS_MAP'])
                flow.ui_goto.assert_not_called()

    def test_unknown_friend_never_falls_back_to_home(self):
        flow = self.flow(lambda _: self.empty, expect_friend=True)
        self.assertFalse(flow.goto_island_map())
        self.assertEqual(flow.device.clicks, [])
        flow.ui_goto.assert_not_called()
        self.assertLess(flow.device.screenshot_count, 115)

    def test_friend_context_does_not_return_postmanage_to_own_island(self):
        flow = self.flow(lambda _: self.error_frame, expect_friend=True)
        self.assertFalse(flow.goto_island_map())
        self.assertEqual(flow.device.clicks, [])
        flow.ui_goto.assert_not_called()

    def test_production_tab_alone_cannot_authorize_postmanage_back(self):
        flow = self.flow(lambda _: self.frame('POST_MANAGE_PRODUCTION'))
        self.assertFalse(flow.goto_island_map())
        self.assertEqual(flow.device.clicks, [])

    def test_post_detail_over_real_postmanage_prevents_background_back(self):
        flow = self.flow(lambda _: self.error_frame)
        flow.is_post_detail_visible = Mock(return_value=True)
        self.assertFalse(flow.goto_island_map())
        self.assertEqual(flow.device.clicks, [])
        flow.ui_goto.assert_not_called()

    def test_popup_over_real_postmanage_prevents_background_back(self):
        flow = self.flow(lambda _: self.error_frame)
        flow.game_tips_appear.return_value = self.buttons['ISLAND_INFO_EXIT']
        self.assertFalse(flow.goto_island_map())
        self.assertEqual(flow.device.clicks, [])
        flow.ui_goto.assert_not_called()

    def test_visible_info_popup_blocks_navigation_even_during_click_cooldown(self):
        popup = self.frame('ISLAND_CHECK', 'ISLAND_GOTO_MAP', 'ISLAND_INFO_EXIT')
        flow = self.flow(lambda t: popup if t < 5 else self.home if t < 6 else self.map)
        # 处理器冷却返回 False，仍须正向阻止点击弹窗背后的入口。
        self.assertTrue(flow.goto_island_map())
        flow.ui_goto.assert_not_called()
        self.assertGreaterEqual(flow.ui_additional.call_count, 8)
        self.assertEqual(self.names(flow), ['ISLAND_GOTO_MAP'])
        self.assertGreaterEqual(flow.device.clicks[0][0], 5)

    def test_persistent_popup_cannot_navigate_or_accept_background_target(self):
        flow = self.flow(lambda _: self.frame('ISLAND_MAP_CHECK', 'ISLAND_INFO_EXIT'))
        self.assertFalse(flow.goto_island_map())
        self.assertEqual(flow.device.clicks, [])
        flow.ui_goto.assert_not_called()
        self.assertLess(flow.device.screenshot_count, 70)

    def test_business_reward_and_game_tips_prevent_background_navigation(self):
        for popup in ('business', 'reward', 'tips'):
            with self.subTest(popup=popup):
                frame = self.frame('ISLAND_MAP_CHECK', 'ISLAND_GET') if popup == 'reward' else self.map
                flow = self.flow(lambda _: frame)
                if popup == 'business':
                    flow.is_business_event_popup.return_value = True
                elif popup == 'tips':
                    flow.game_tips_appear.return_value = self.buttons['ISLAND_INFO_EXIT']
                self.assertFalse(flow.goto_island_map())
                self.assertEqual(flow.device.clicks, [])
                flow.ui_goto.assert_not_called()


if __name__ == '__main__':
    unittest.main()
