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
        ui.config = SimpleNamespace(BUTTON_OFFSET=(20, 20), SERVER='cn', data={})
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

    @staticmethod
    def prompt_fixture(button, offset=(0, 0)):
        """已有页码资源加关闭区背景的构造图，不代替真实整页提示截图。"""
        image = utils.load_image(button.file).copy()
        x1, y1, x2, y2 = GAME_TIPS._button
        image[y1:y2, x1:x2] = (40, 40, 40)
        if offset != (0, 0):
            shifted = np.zeros_like(image)
            dx, dy = offset
            shifted[dy:, dx:] = image[:image.shape[0] - dy, :image.shape[1] - dx]
            return shifted
        return image

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

    def test_tips_variants_still_use_existing_confirm_handler(self):
        for button in (GAME_TIPS3, GAME_TIPS4):
            with self.subTest(button=button.name):
                for tip in (GAME_TIPS, GAME_TIPS3, GAME_TIPS4):
                    tip.clear_offset()
                ui = self.make_ui(self.prompt_fixture(button))

                self.assertTrue(ui.ui_additional(get_ship=False))

                ui.device.click.assert_called_once_with(GAME_TIPS)

    def test_prompt_shape_without_confirm_background_never_clicks(self):
        image = utils.load_image(GAME_TIPS.file).copy()
        x1, y1, x2, y2 = GAME_TIPS.button
        image[y1:y2, x1:x2] = (255, 255, 255)
        ui = self.make_ui(image)
        # 此例验证标准提示的确认背景；其他布局在独立正例中覆盖。
        ui.match_template_color = lambda button, **kwargs: (
            ModuleBase.match_template_color(ui, button, **kwargs) if button is GAME_TIPS else False)

        self.assertFalse(ui.ui_additional(get_ship=False))

        ui.device.click.assert_not_called()

    def test_visible_prompt_detection_ignores_close_cooldown_and_has_no_side_effects(self):
        image = utils.load_image(GAME_TIPS.file).copy()
        x1, y1, x2, y2 = GAME_TIPS.button
        image[y1:y2, x1:x2] = (40, 40, 40)
        ui = self.make_ui(image)
        ui.match_template_color = Mock(wraps=ui.match_template_color)

        self.assertIs(ui.game_tips_appear(), GAME_TIPS)
        self.assertEqual(ui.interval_timer, {})
        ui.device.click.assert_not_called()
        self.assertTrue(all(call.kwargs.get('interval', 0) == 0
                            for call in ui.match_template_color.call_args_list))

        self.assertTrue(ui.handle_game_tips())
        self.assertIsNotNone(ui.game_tips_appear())
        self.assertFalse(ui.handle_game_tips())
        self.assertIsNotNone(ui.game_tips_appear())
        self.assertEqual(ui.device.click.call_count, 1)

    def test_real_main_false_positive_is_not_reported_as_visible_prompt(self):
        fixture = Path(__file__).parent / 'fixtures' / 'ui_game_tips' / 'main_false_positive.png'
        ui = self.make_ui(utils.load_image(str(fixture)))
        self.assertIsNone(ui.game_tips_appear())
        self.assertEqual(ui.interval_timer, {})
        ui.device.click.assert_not_called()

    def test_each_prompt_variant_remains_visible_while_close_is_cooling(self):
        for button in (GAME_TIPS3, GAME_TIPS4):
            with self.subTest(button=button.name):
                for tip in (GAME_TIPS, GAME_TIPS3, GAME_TIPS4):
                    tip.clear_offset()
                ui = self.make_ui(self.prompt_fixture(button))
                self.assertIsNotNone(ui.game_tips_appear())
                self.assertTrue(ui.handle_game_tips())
                self.assertIsNotNone(ui.game_tips_appear())
                self.assertFalse(ui.handle_game_tips())
                self.assertEqual(ui.device.click.call_count, 1)

    def test_real_dark_main_background_does_not_count_as_prompt(self):
        fixture = Path(__file__).parent / 'fixtures' / 'ui_game_tips' / 'main_dark_background.png'
        image = utils.load_image(str(fixture))
        self.assertTrue(GAME_TIPS3.match(image, offset=(20, 20)))
        ui = self.make_ui(image)
        self.assertIsNone(ui.game_tips_appear())
        self.assertFalse(ui.handle_game_tips())
        self.assertEqual(GAME_TIPS.button, GAME_TIPS._button)
        ui.device.click.assert_not_called()

    def test_probability_information_popup_is_not_paginated_game_tips(self):
        fixture = Path(__file__).parent / 'fixtures' / 'ui_game_tips' / 'build_probability_popup.png'
        ui = self.make_ui(utils.load_image(str(fixture)))
        self.assertIsNone(ui.game_tips_appear())
        self.assertFalse(ui.handle_game_tips())
        ui.device.click.assert_not_called()

    def test_variant_checks_actual_close_area_instead_of_number_background(self):
        for button in (GAME_TIPS3, GAME_TIPS4):
            with self.subTest(button=button.name):
                ui = self.make_ui(utils.load_image(button.file))
                self.assertTrue(ui.match_template_color(button, offset=(20, 20)))
                self.assertTrue(ui.image_color_count(button.button, color=(40, 40, 40), threshold=15, count=50))
                self.assertIsNone(ui.game_tips_appear())
                ui.device.click.assert_not_called()

    def test_confirm_uses_matched_variant_offset_not_failed_standard_match(self):
        for button in (GAME_TIPS3, GAME_TIPS4):
            with self.subTest(button=button.name):
                ui = self.make_ui(self.prompt_fixture(button, offset=(10, 8)))
                # 强制走变体分支，但标准模板仍实际匹配并写入候选偏移。
                ui.match_template_color = lambda candidate, **kwargs: (
                    ModuleBase.match_template_color(ui, candidate, **kwargs) and candidate is button)
                expected = utils.area_offset(GAME_TIPS._button, (10, 8))
                clicked = []
                ui.device.click.side_effect = lambda target: clicked.append(tuple(target.button))
                self.assertIs(ui.game_tips_appear(), button)
                self.assertTrue(ui.handle_game_tips(button=button))
                self.assertEqual(clicked, [tuple(expected)])


if __name__ == '__main__':
    unittest.main()
