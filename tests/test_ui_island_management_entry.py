"""通用 UI 导航进入岛屿管理页的截图状态回归测试。"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import module.base.timer as timer_module
import module.ui.ui as ui_module
from module.base.button import Button
from module.base.timer import Timer
from module.exception import GameStuckError
from module.ui.page import Page, page_island, page_island_management
from module.ui.ui import UI
from tests.test_island_goto_management import (
    FakeClock,
    FakeDevice,
    ISLAND_CHECK,
    ISLAND_GOTO_MANAGEMENT,
    ISLAND_PAGE,
    LOADING_PAGE,
    POSTMANAGE_PAGE,
    build_frame,
)


class IslandManagementNavigationTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.time_patch = patch.object(timer_module, 'time', lambda: self.clock.now)
        self.time_patch.start()
        # 保留真实页面图和匹配逻辑，仅隔离无关页面，确保失败来自目标入口。
        self.pages_patch = patch.object(Page, 'all_pages', {
            page_island.name: page_island,
            page_island_management.name: page_island_management,
        })
        self.pages_patch.start()

    def tearDown(self):
        Page.clear_connection()
        self.pages_patch.stop()
        self.time_patch.stop()
        ISLAND_CHECK.clear_offset()
        ISLAND_GOTO_MANAGEMENT.clear_offset()

    def make_ui(self, timeline, screenshot_cost=0.4):
        device = FakeDevice(self.clock, timeline, screenshot_cost=screenshot_cost)
        ui = UI.__new__(UI)
        ui.config = SimpleNamespace(SERVER='cn', BUTTON_OFFSET=(20, 20))
        ui.device = device
        ui.interval_timer = {}
        ui.handle_guild_popup_cancel = lambda: False
        ui.ui_additional = lambda get_ship=True: False
        ui.ui_get_current_page = lambda **kwargs: self.fail_navigation()
        return ui, device

    @staticmethod
    def fail_navigation():
        raise GameStuckError('离线导航未在限制内进入管理页')

    def test_ready_requires_page_icon_and_entry(self):
        """管理按钮或岛屿页图标单独出现都不足以开启确认。"""
        for frame in (LOADING_PAGE, build_frame(ISLAND_GOTO_MANAGEMENT)):
            with self.subTest(page_icon_visible=frame is LOADING_PAGE):
                ui, _ = self.make_ui(lambda t: frame)
                confirm = Timer(1, count=2)
                self.assertFalse(ui.ui_island_management_entry_ready(confirm))
                self.assertFalse(confirm.started())

    def test_ready_rejects_template_shape_with_wrong_color(self):
        """保留图案但按钮仍呈过渡暗色时不能进入管理页。"""
        frame = ISLAND_PAGE.copy()
        x1, y1, x2, y2 = ISLAND_GOTO_MANAGEMENT.area
        frame[y1:y2, x1:x2] //= 2
        self.assertTrue(ISLAND_GOTO_MANAGEMENT.match(frame, offset=(20, 20)))
        self.assertFalse(ISLAND_GOTO_MANAGEMENT.match_template_color(frame, offset=(20, 20)))
        ui, _ = self.make_ui(lambda t: frame)
        self.assertFalse(ui.ui_island_management_entry_ready(Timer(1, count=2)))

    def test_english_entry_uses_english_check_template(self):
        """英文服右上角管理图案使用本服资源，不能强制中文模板。"""
        english_entry = Button(
            area=ISLAND_CHECK.raw_area['en'],
            color=ISLAND_CHECK.raw_color['en'],
            button=ISLAND_CHECK.raw_button['en'],
            file=ISLAND_CHECK.raw_file['en'],
            name='ISLAND_CHECK',
        )
        english_page = build_frame(english_entry)
        self.assertTrue(english_entry.match_template_color(english_page, offset=(20, 20)))
        self.assertFalse(ISLAND_GOTO_MANAGEMENT.match_template_color(english_page, offset=(20, 20)))
        ui, device = self.make_ui(lambda t: english_page if t < 1.6 else POSTMANAGE_PAGE)
        ui.config.SERVER = 'en'

        with patch.object(ui_module, 'ISLAND_CHECK', english_entry), patch.object(
                page_island, 'check_button', english_entry):
            ui.ui_goto(page_island_management, get_ship=False)

        self.assertEqual(device.clicks, [(1.2, 'ISLAND_GOTO_MANAGEMENT')])

    def test_other_servers_use_existing_management_entry_template(self):
        for server in ('cn', 'jp', 'tw'):
            with self.subTest(server=server):
                ui, device = self.make_ui(lambda t: ISLAND_PAGE, screenshot_cost=1.5)
                ui.config.SERVER = server
                confirm = Timer(1, count=2)
                self.assertFalse(ui.ui_island_management_entry_ready(confirm))
                device.screenshot()
                self.assertFalse(ui.ui_island_management_entry_ready(confirm))
                device.screenshot()
                self.assertTrue(ui.ui_island_management_entry_ready(confirm))

    def test_ready_requires_new_observations_even_with_slow_screenshots(self):
        """慢截图不能让单张完整画面直接触发点击。"""
        ui, device = self.make_ui(lambda t: ISLAND_PAGE, screenshot_cost=1.5)
        confirm = Timer(1, count=2)
        self.assertFalse(ui.ui_island_management_entry_ready(confirm))
        device.screenshot()
        self.assertFalse(ui.ui_island_management_entry_ready(confirm))
        device.screenshot()
        self.assertTrue(ui.ui_island_management_entry_ready(confirm))

    def test_visible_then_missing_restarts_full_confirmation(self):
        """按钮消失后已积累的可见时间和次数均失效。"""
        def timeline(t):
            if 0.7 <= t < 1.3:
                return LOADING_PAGE
            return ISLAND_PAGE

        ui, device = self.make_ui(timeline)
        confirm = Timer(1, count=2)
        self.assertFalse(ui.ui_island_management_entry_ready(confirm))
        device.screenshot()
        self.assertFalse(ui.ui_island_management_entry_ready(confirm))
        device.screenshot()
        self.assertFalse(ui.ui_island_management_entry_ready(confirm))
        self.assertFalse(confirm.started())
        device.screenshot()
        self.assertFalse(ui.ui_island_management_entry_ready(confirm))
        device.screenshot()
        self.assertFalse(ui.ui_island_management_entry_ready(confirm))
        device.screenshot()
        self.assertFalse(ui.ui_island_management_entry_ready(confirm))

        device.screenshot()
        self.assertFalse(ui.ui_island_management_entry_ready(confirm))
        device.screenshot()
        self.assertTrue(ui.ui_island_management_entry_ready(confirm))

    def test_navigation_wait_does_not_consume_five_second_click_interval(self):
        """稳定等待期间不触发源页冷却，首次有效点击无需额外等 5 秒。"""
        ui, device = self.make_ui(lambda t: ISLAND_PAGE if t < 1.6 else POSTMANAGE_PAGE)
        ui.ui_goto(page_island_management, get_ship=False)

        self.assertEqual(device.clicks, [(1.2, 'ISLAND_GOTO_MANAGEMENT')])
        self.assertLess(device.clicks[0][0], 5.0)
        self.assertEqual(device.screenshot_count, 4)

    def test_navigation_does_not_click_while_entry_missing(self):
        ui, device = self.make_ui(lambda t: LOADING_PAGE if t < 2.0 else POSTMANAGE_PAGE)
        ui.ui_goto(page_island_management, get_ship=False)
        self.assertEqual(device.clicks, [])

    def test_navigation_rechecks_after_loading_flash(self):
        def timeline(t):
            if t < 0.5:
                return ISLAND_PAGE
            if t < 2.0:
                return LOADING_PAGE
            if t < 3.6:
                return ISLAND_PAGE
            return POSTMANAGE_PAGE

        ui, device = self.make_ui(timeline)
        ui.ui_goto(page_island_management, get_ship=False)
        self.assertEqual(len(device.clicks), 1)
        self.assertGreater(device.clicks[0][0], 3.0)

    def test_navigation_already_at_destination_never_clicks(self):
        ui, device = self.make_ui(lambda t: POSTMANAGE_PAGE)
        ui.ui_goto(page_island_management, get_ship=False)
        self.assertEqual(device.clicks, [])
        self.assertEqual(device.screenshot_count, 0)

    def test_navigation_retries_remain_rate_limited(self):
        ui, device = self.make_ui(lambda t: ISLAND_PAGE if t < 7.0 else POSTMANAGE_PAGE)
        ui.ui_goto(page_island_management, get_ship=False)
        self.assertEqual(len(device.clicks), 2)
        self.assertGreaterEqual(device.clicks[1][0] - device.clicks[0][0], 5.0)

    def test_navigation_missing_entry_retains_timeout_recovery(self):
        ui, device = self.make_ui(lambda t: LOADING_PAGE)
        with self.assertRaises(GameStuckError):
            ui.ui_goto(page_island_management, get_ship=False, recover_unknown=False)
        self.assertEqual(device.clicks, [])
        self.assertGreaterEqual(self.clock.now - device.t0, 30.0)

    def test_navigation_popup_resets_visible_confirmation(self):
        """通用导航的额外弹窗处理和大舰队弹窗均打断可操作确认。"""
        for handler_name in ('ui_additional', 'handle_guild_popup_cancel'):
            with self.subTest(handler=handler_name):
                ui, device = self.make_ui(lambda t: ISLAND_PAGE if t < 3.0 else POSTMANAGE_PAGE)
                handled = False

                def popup(*args, **kwargs):
                    nonlocal handled
                    if not handled and self.clock.now - device.t0 >= 0.79:
                        handled = True
                        return True
                    return False

                setattr(ui, handler_name, popup)
                ui.ui_goto(page_island_management, get_ship=False)
                self.assertTrue(handled)
                self.assertEqual(len(device.clicks), 1)
                self.assertGreaterEqual(device.clicks[0][0], 2.4)


if __name__ == '__main__':
    unittest.main()
