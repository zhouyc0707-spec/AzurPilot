"""验证自律提示关闭后进入奖励菜单时，等待加载流程能正常结束。

真实 1440p 夹具只保留两个操作按钮；设备与配置均在内存中模拟，
不连接模拟器、不初始化用户配置，并限制截图次数防止回归时挂起。
"""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from module.base.button import Button
from module.base.resource import Resource
from module.base import utils
from module.combat.auto_search_combat import AutoSearchCombat
from module.device.screenshot import Screenshot
from module.exception import CampaignEnd
from module.handler import auto_search
from module.retire.assets import IN_RETIREMENT_CHECK


FIXTURE_DIR = Path(__file__).parent / 'fixtures' / 'auto_search_menu_recovery'


def reward_menu_1440p():
    """把真实按钮局部放回原坐标，其他像素全部清空以匿名化。"""
    image = np.zeros((1440, 2560, 3), dtype=np.uint8)
    for name, origin in (
        ('exit_1440p.png', (746, 1196)),
        ('continue_1440p.png', (1546, 1196)),
    ):
        with Image.open(FIXTURE_DIR / name) as source:
            button = np.array(source.convert('RGB'))
        x, y = origin
        height, width = button.shape[:2]
        image[y:y + height, x:x + width] = button
    return image


def cn_button(source):
    """创建独立国服按钮，避免修改其他测试共享的资源偏移。"""
    return Button(
        area=source.raw_area['cn'], color=source.raw_color['cn'],
        button=source.raw_button['cn'], file=source.raw_file['cn'],
        name=source.name,
    )


class FrameDevice:
    """只提供有限张预先构造的截图，没有任何真实设备能力。"""

    def __init__(self, frames, screenshot_limit=4):
        self.frames = frames
        self.image = frames[0]
        self.frame_index = 0
        self.screenshot_limit = screenshot_limit
        self.screenshot = Mock(side_effect=self._advance)
        self.click = Mock()
        self.stuck_record_clear = Mock()
        self.stuck_record_add = Mock()
        self.click_record_clear = Mock()

    def _advance(self):
        if self.screenshot.call_count > self.screenshot_limit:
            raise AssertionError('等待循环耗尽模拟截图，奖励菜单未触发结束')
        self.frame_index = min(self.frame_index + 1, len(self.frames) - 1)
        self.image = self.frames[self.frame_index]
        return self.image


class AutoSearchMenuRecoveryTests(unittest.TestCase):
    def setUp(self):
        previous_mode = utils.TEMPLATE_MATCH_NON_NATIVE_720P
        previous_resolution = utils.TEMPLATE_MATCH_NON_NATIVE_720P_RESOLUTION
        self.addCleanup(
            utils.set_template_match_non_native_720p,
            previous_mode, previous_resolution,
        )
        utils.set_template_match_non_native_720p(True, resolution=(2560, 1440))
        resource_patch = patch.dict(Resource.instances)
        resource_patch.start()
        self.addCleanup(resource_patch.stop)
        self.continue_button = cn_button(auto_search.AUTO_SEARCH_MENU_CONTINUE)
        self.exit_button = cn_button(auto_search.AUTO_SEARCH_MENU_EXIT)
        menu_patch = patch.object(
            auto_search, 'AUTO_SEARCH_MENU_CONTINUE', self.continue_button,
        )
        menu_patch.start()
        self.addCleanup(menu_patch.stop)
        self.menu_image = Screenshot.resize_screenshot_to_720p(reward_menu_1440p())
        self.map_image = np.zeros((720, 1280, 3), dtype=np.uint8)

    def make_campaign(self, frames):
        """跳过真实配置和设备初始化，只保留本次状态循环。"""
        campaign = AutoSearchCombat.__new__(AutoSearchCombat)
        campaign.config = SimpleNamespace(Retirement_RetireMode='one_click_retire')
        campaign.device = FrameDevice(frames)
        campaign.interval_reset = Mock()
        campaign.is_auto_search_running = Mock(return_value=False)
        campaign.handle_auto_search_map_option = Mock(return_value=False)
        campaign.appear = Mock(return_value=False)
        campaign.appear_then_click = Mock(return_value=False)
        campaign.handle_combat_low_emotion = Mock(return_value=False)
        campaign.handle_retirement = Mock(return_value=False)
        campaign.is_combat_loading = Mock(return_value=False)
        campaign.is_in_stage = Mock(return_value=False)
        campaign._auto_search_in_stage_timer = Mock()
        campaign._auto_search_in_stage_timer.reached.return_value = False
        return campaign

    def test_real_1440p_buttons_remain_recognizable(self):
        """经过实际缩放管线后，两个真实按钮仍通过原有严格阈值。"""
        self.assertEqual(reward_menu_1440p().shape, (1440, 2560, 3))
        campaign = self.make_campaign([self.menu_image])
        for non_native in (False, True):
            with self.subTest(non_native=non_native):
                utils.set_template_match_non_native_720p(non_native)
                self.assertTrue(campaign.is_in_auto_search_menu())
                self.assertTrue(self.exit_button.match(
                    self.menu_image, offset=campaign._auto_search_menu_offset,
                ))
                self.assertEqual(self.continue_button.button, (773, 597, 919, 645))
                self.assertEqual(self.exit_button.button, (373, 597, 520, 646))

    def test_existing_menu_ends_before_operations_or_loading(self):
        """已显示汇总菜单时，优先结束，不能误当作战斗加载完成。"""
        campaign = self.make_campaign([self.menu_image])
        # 即使加载检测也会返回真，奖励菜单也必须优先交给 CampaignEnd。
        campaign.is_combat_loading.return_value = True
        with self.assertRaises(CampaignEnd):
            campaign.map_offensive_auto_search()
        campaign.handle_auto_search_map_option.assert_not_called()
        campaign.appear.assert_not_called()
        campaign.appear_then_click.assert_not_called()
        campaign.handle_combat_low_emotion.assert_not_called()
        campaign.handle_retirement.assert_not_called()
        campaign.is_combat_loading.assert_not_called()
        campaign.device.click.assert_not_called()
        campaign.device.screenshot.assert_not_called()

    def test_game_tips_then_reward_menu_ends_waiting(self):
        """提示处理返回真后进入等待循环，新出现的奖励菜单仍能结束。"""
        campaign = self.make_campaign([self.map_image, self.menu_image])
        # 使用真实退役入口，复现普通 GAME_TIPS 也返回 True 的调用关系。
        del campaign.handle_retirement
        campaign.retirement_appear = Mock(return_value=False)
        campaign._unable_to_enhance = False
        handled_tips = False

        def handle_tips():
            nonlocal handled_tips
            if handled_tips:
                return False
            handled_tips = True
            campaign.device.click('GAME_TIPS')
            return True

        campaign.handle_game_tips = Mock(side_effect=handle_tips)
        with self.assertRaises(CampaignEnd):
            campaign.auto_search_moving()
        campaign.device.click.assert_called_once_with('GAME_TIPS')
        campaign.device.screenshot.assert_called_once_with()
        campaign.is_combat_loading.assert_called_once_with()
        self.assertTrue(campaign.is_in_auto_search_menu())

    def test_map_recovery_still_waits_until_combat_loading(self):
        """正常地图先恢复自律开关，再等加载，不改变既有启动行为。"""
        campaign = self.make_campaign([self.map_image] * 3)
        campaign.handle_auto_search_map_option.side_effect = [True, False, False]
        campaign.is_combat_loading.side_effect = [False, True]
        campaign.map_offensive_auto_search()
        self.assertEqual(campaign.handle_auto_search_map_option.call_count, 3)
        self.assertEqual(campaign.is_combat_loading.call_count, 2)
        self.assertEqual(campaign.interval_reset.call_count, 2)
        self.assertEqual(campaign.device.screenshot.call_count, 2)
        campaign.device.click.assert_not_called()

    def test_low_emotion_and_retirement_continue_on_fresh_frame(self):
        """没有奖励菜单时，低情绪及真实退役入口仍处理后取新帧等加载。"""
        for handler in ('low_emotion', 'retirement'):
            with self.subTest(handler=handler):
                campaign = self.make_campaign([self.map_image] * 2)
                campaign.is_combat_loading.return_value = True
                if handler == 'low_emotion':
                    campaign.handle_combat_low_emotion.side_effect = [True, False]
                else:
                    # 保留真实入口的 True 返回分支，具体退役操作使用内存模拟。
                    del campaign.handle_retirement
                    campaign.retirement_appear = Mock(return_value=False)
                    campaign.handle_game_tips = Mock(return_value=False)
                    campaign._unable_to_enhance = False
                    campaign._retire_handler = Mock()
                    campaign.map_cat_attack_timer = Mock()
                    campaign.appear.side_effect = lambda button, **kwargs: (
                        button is IN_RETIREMENT_CHECK
                        and campaign.device.frame_index == 0
                    )

                campaign.map_offensive_auto_search()
                campaign.device.screenshot.assert_called_once_with()
                campaign.is_combat_loading.assert_called_once_with()
                self.assertEqual(campaign.device.frame_index, 1)
                self.assertEqual(campaign.handle_combat_low_emotion.call_count, 2)
                campaign.device.click.assert_not_called()
                if handler == 'retirement':
                    campaign._retire_handler.assert_called_once_with()
                    campaign.map_cat_attack_timer.reset.assert_called_once_with()
                else:
                    campaign.handle_retirement.assert_called_once_with()

    def test_direct_stage_return_reuses_missing_menu_confirmation(self):
        """游戏直接返回关卡页时，复用原有菜单缺失稳定确认。"""
        campaign = self.make_campaign([self.map_image] * 2)
        campaign.is_in_stage.return_value = True
        campaign._auto_search_in_stage_timer.reached.side_effect = [False, True]
        with self.assertRaises(CampaignEnd):
            campaign.map_offensive_auto_search()
        self.assertEqual(campaign.is_in_stage.call_count, 2)
        self.assertEqual(campaign._auto_search_in_stage_timer.reached.call_count, 2)
        campaign._auto_search_in_stage_timer.reset.assert_not_called()
        campaign.is_combat_loading.assert_called_once_with()
        campaign.device.screenshot.assert_called_once_with()
        campaign.device.click.assert_not_called()

    def test_transient_stage_resets_confirmation_and_allows_loading(self):
        """短暂返回关卡页尚未确认结束时，回地图会重置计时并继续。"""
        campaign = self.make_campaign([self.map_image] * 2)
        campaign.is_in_stage.side_effect = [True, False]
        campaign.is_combat_loading.side_effect = [False, True]
        campaign.map_offensive_auto_search()
        campaign._auto_search_in_stage_timer.reached.assert_called_once_with()
        campaign._auto_search_in_stage_timer.reset.assert_called_once_with()
        self.assertEqual(campaign.is_combat_loading.call_count, 2)
        campaign.device.click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
