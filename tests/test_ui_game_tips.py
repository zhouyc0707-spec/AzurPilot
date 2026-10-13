"""通用导航游戏提示的真实局部画面回归，不读取用户配置或连接设备。"""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np

from module.base import utils
from module.base.base import ModuleBase
from module.handler.assets import GAME_TIPS, GAME_TIPS3, GAME_TIPS4
from module.ui.ui import UI


class UiGameTipsTest(unittest.TestCase):
    def setUp(self):
        previous_mode = utils.TEMPLATE_MATCH_NON_NATIVE_720P
        previous_resolution = utils.TEMPLATE_MATCH_NON_NATIVE_720P_RESOLUTION
        self.addCleanup(utils.set_template_match_non_native_720p,
                        previous_mode, previous_resolution)
        utils.set_template_match_non_native_720p(True, resolution=(2560, 1440))
        for button in (GAME_TIPS, GAME_TIPS3, GAME_TIPS4):
            self.addCleanup(button.clear_offset)

    @staticmethod
    def make_ui(image):
        ui = UI.__new__(UI)
        ui.config = SimpleNamespace(BUTTON_OFFSET=(20, 20))
        ui.interval_timer = {}
        ui.device = SimpleNamespace(image=image, click=Mock(), stuck_record_add=Mock())
        ui.ui_current = SimpleNamespace(name='page_main')
        for name in ('ui_page_os_popups', 'handle_popup_confirm',
                     'handle_urgent_commission', 'handle_guild_popup_cancel',
                     'ui_page_main_popups', 'handle_story_skip', 'handle_idle_page'):
            setattr(ui, name, Mock(return_value=False))
        # 仅隔离无关弹窗；提示仍走真实模板、偏移、颜色和点击冷却。
        ui.appear = lambda button, **kwargs: (
            ModuleBase.appear(ui, button, **kwargs)
            if button in (GAME_TIPS, GAME_TIPS3, GAME_TIPS4) else False)
        ui.appear_then_click = Mock(return_value=False)
        return ui

    def test_main_page_false_positive_does_not_click_home(self):
        """真实误判帧只保留模板搜索区和确认背景区，没有账号信息。"""
        fixture = Path(__file__).parent / 'fixtures' / 'ui_game_tips' / 'main_false_positive.png'
        image = utils.load_image(str(fixture))
        self.assertTrue(GAME_TIPS.match(image, offset=(30, 30)))
        ui = self.make_ui(image)

        self.assertFalse(ui.ui_additional(get_ship=False))

        ui.device.click.assert_not_called()

    def test_true_tips_close_confirm_button_and_preserve_click_interval(self):
        image = utils.load_image(GAME_TIPS.file).copy()
        x1, y1, x2, y2 = GAME_TIPS.button
        image[y1:y2, x1:x2] = (40, 40, 40)
        ui = self.make_ui(image)

        self.assertTrue(ui.ui_additional(get_ship=False))
        ui.device.click.assert_called_once_with(GAME_TIPS)
        self.assertFalse(ui.ui_additional(get_ship=False))
        self.assertEqual(ui.device.click.call_count, 1)

    def test_true_tips_variants_still_use_existing_confirm_handler(self):
        for button in (GAME_TIPS3, GAME_TIPS4):
            with self.subTest(button=button.name):
                for tip in (GAME_TIPS, GAME_TIPS3, GAME_TIPS4):
                    tip.clear_offset()
                ui = self.make_ui(utils.load_image(button.file))

                self.assertTrue(ui.ui_additional(get_ship=False))

                ui.device.click.assert_called_once_with(GAME_TIPS)

    def test_prompt_shape_without_confirm_background_never_clicks(self):
        image = utils.load_image(GAME_TIPS.file).copy()
        x1, y1, x2, y2 = GAME_TIPS.button
        image[y1:y2, x1:x2] = (255, 255, 255)
        ui = self.make_ui(image)
        # 此例验证标准提示的确认背景；其他布局在独立正例中覆盖。
        ui.appear = lambda button, **kwargs: (
            ModuleBase.appear(ui, button, **kwargs) if button is GAME_TIPS else False)

        self.assertFalse(ui.ui_additional(get_ship=False))

        ui.device.click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
