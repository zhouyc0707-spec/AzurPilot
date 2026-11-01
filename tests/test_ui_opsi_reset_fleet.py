"""大世界月初舰队选择窗口的匿名实图与点击冷却回归。"""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from module.base import utils
from module.base.utils import load_image
from module.exception import RequestHumanTakeover
from module.map.assets import FLEET_PREPARATION, MAP_PREPARATION_CANCEL
from module.os_handler.assets import RESET_FLEET_PREPARATION, RESET_FLEET_PREPARATION_2
from module.ui.ui import UI


FIXTURE = Path(__file__).parent / 'fixtures' / 'opsi_reset_fleet_current.png'


class OpsiResetFleetTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.enterContext(patch('module.base.timer.time', lambda: self.now))
        self.addCleanup(utils.set_template_match_non_native_720p,
                        utils.TEMPLATE_MATCH_NON_NATIVE_720P,
                        utils.TEMPLATE_MATCH_NON_NATIVE_720P_RESOLUTION)
        utils.set_template_match_non_native_720p(False)
        for button in (FLEET_PREPARATION, RESET_FLEET_PREPARATION, RESET_FLEET_PREPARATION_2):
            self.addCleanup(button.clear_offset)

    def make_ui(self, image=None, server='cn'):
        ui = UI.__new__(UI)
        ui.config = SimpleNamespace(SERVER=server, BUTTON_OFFSET=(20, 20), data={})
        ui.device = SimpleNamespace(
            image=load_image(str(FIXTURE)) if image is None else image,
            click=Mock(), sleep=Mock(), stuck_record_add=Mock(),
        )
        ui.interval_timer = {}
        ui.ui_current = SimpleNamespace(name='page_unknown')
        for name in ('handle_popup_confirm', 'handle_urgent_commission', 'handle_guild_popup_cancel',
                     'ui_page_main_popups', 'handle_story_skip', 'handle_game_tips',
                     'handle_bilibili_login_record', 'handle_idle_page'):
            setattr(ui, name, Mock(return_value=False))
        return ui

    def test_actual_window_uses_opsi_entry_instead_of_campaign_cancel(self):
        """当前字形不匹配旧重置提示，却会匹配通用舰队准备按钮。"""
        for non_native in (False, True):
            with self.subTest(non_native=non_native):
                utils.set_template_match_non_native_720p(non_native, (2560, 1440))
                ui = self.make_ui()
                self.assertFalse(RESET_FLEET_PREPARATION.match(ui.device.image, offset=(30, 30)))
                self.assertTrue(FLEET_PREPARATION.match(ui.device.image, offset=(20, 50)))
                self.assertTrue(ui.ui_additional())
                ui.device.click.assert_called_once_with(RESET_FLEET_PREPARATION_2)
                self.assertEqual(ui._opsi_reset_fleet_preparation_click, 1)

    def test_visible_window_during_cooldown_never_falls_through(self):
        ui = self.make_ui()
        self.assertTrue(ui.ui_additional())
        for tick in (0.2, 1.0, 2.9):
            self.now = 1000 + tick
            self.assertFalse(ui.ui_additional())
        ui.device.click.assert_called_once_with(RESET_FLEET_PREPARATION_2)
        ui.handle_popup_confirm.assert_not_called()
        self.now = 1003.01
        self.assertTrue(ui.ui_additional())
        self.assertEqual(ui.device.click.call_count, 2)

    def test_old_template_remains_supported_and_shares_cooldown(self):
        ui = self.make_ui(load_image(RESET_FLEET_PREPARATION.file))
        self.assertTrue(ui.ui_additional())
        ui.device.click.assert_called_once_with(RESET_FLEET_PREPARATION)
        ui.device.image = load_image(str(FIXTURE))
        self.now += 1
        self.assertFalse(ui.ui_additional())
        self.assertEqual(ui.device.click.call_count, 1)

    def test_five_unsuccessful_entries_still_request_human_takeover(self):
        ui = self.make_ui()
        for _ in range(5):
            self.assertTrue(ui.ui_additional())
            self.now += 3.1
        with self.assertRaises(RequestHumanTakeover):
            ui.ui_additional()
        self.assertEqual(ui.device.click.call_count, 5)

    def test_fifth_entry_that_succeeds_does_not_raise_on_later_page(self):
        ui = self.make_ui(np.zeros((720, 1280, 3), dtype=np.uint8))
        ui._opsi_reset_fleet_preparation_click = 5
        self.assertFalse(ui.ui_page_os_popups())
        ui.device.click.assert_not_called()

    def test_ordinary_fleet_preparation_keeps_campaign_cancel(self):
        ui = self.make_ui(load_image(FLEET_PREPARATION.file))
        self.assertIsNone(ui.ui_get_os_reset_fleet_preparation())
        self.assertTrue(ui.ui_additional())
        ui.device.click.assert_called_once_with(MAP_PREPARATION_CANCEL)

    def test_unknown_image_is_not_an_opsi_reset(self):
        ui = self.make_ui(np.zeros((720, 1280, 3), dtype=np.uint8))
        self.assertFalse(ui.ui_page_os_popups())
        ui.device.click.assert_not_called()

    def test_current_cn_variant_does_not_change_other_servers(self):
        for server in ('en', 'jp', 'tw'):
            with self.subTest(server=server):
                self.assertIsNone(self.make_ui(server=server).ui_get_os_reset_fleet_preparation())


if __name__ == '__main__':
    unittest.main()
