"""档案密钥领取的按钮颜色、容量判断与连续截图回归，不连接游戏。"""

import unittest
from collections import deque
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import numpy as np
from PIL import Image

from module.base.button import Button
from module.base.resource import Resource
import module.base.utils as image_utils
from module.combat.assets import GET_ITEMS_1
from module.exception import GameStuckError
from module.freebies.assets import DATA_KEY_COLLECT, DATA_KEY_COLLECTED
from module.freebies.data_key import DataKey
from module.ui.assets import CAMPAIGN_MENU_GOTO_WAR_ARCHIVES, WAR_ARCHIVES_CHECK
from module.ui.page import page_archives, page_campaign_menu


class TestDataKeyButtonRecognition(unittest.TestCase):
    def setUp(self):
        # 每服按钮都独立构造，既不切换全局服务器，也不污染资源注册表。
        resource_patch = patch.object(Resource, 'instances', {})
        resource_patch.start()
        self.addCleanup(resource_patch.stop)

    def buttons(self, server):
        return [
            Button(
                area=source.raw_area[server],
                color=source.raw_color[server],
                button=source.raw_button[server],
                file=source.raw_file[server],
                name=f'{source.name}_{server}',
            )
            for source in (DATA_KEY_COLLECT, DATA_KEY_COLLECTED)
        ]

    def image(self, source, server):
        with Image.open(source.raw_file[server]) as image:
            return np.array(image.convert('RGB'))

    def assert_states(self, non_native, offset=(0, 0)):
        with patch.object(image_utils, 'TEMPLATE_MATCH_NON_NATIVE_720P', non_native):
            for server in ('cn', 'en', 'jp', 'tw'):
                for state, source in enumerate((DATA_KEY_COLLECT, DATA_KEY_COLLECTED)):
                    with self.subTest(server=server, state=source.name, offset=offset):
                        image = self.image(source, server)
                        if offset != (0, 0):
                            # 在完整识别区域中移动按钮，验证颜色核验使用模板实际偏移。
                            moved = np.zeros_like(image)
                            dx, dy = offset
                            moved[dy:, dx:] = image[:-dy, :-dx]
                            image = moved
                        buttons = self.buttons(server)
                        matches = [
                            bool(button.match_template_color(image, offset=(20, 20)))
                            for button in buttons
                        ]
                        self.assertEqual(matches, [state == 0, state == 1])

    def test_native_720p_distinguishes_claim_and_collected_for_each_server(self):
        self.assert_states(non_native=False)

    def test_non_native_threshold_distinguishes_claim_and_collected_for_each_server(self):
        self.assert_states(non_native=True)

    def test_offset_color_check_distinguishes_states_for_each_server(self):
        self.assert_states(non_native=True, offset=(8, 6))

    def test_cn_loose_template_alone_reproduces_both_false_positive_directions(self):
        with patch.object(image_utils, 'TEMPLATE_MATCH_NON_NATIVE_720P', True):
            for source in (DATA_KEY_COLLECT, DATA_KEY_COLLECTED):
                with self.subTest(state=source.name):
                    image = self.image(source, 'cn')
                    claim, collected = self.buttons('cn')
                    self.assertTrue(claim.match(image, offset=(20, 20)))
                    self.assertTrue(collected.match(image, offset=(20, 20)))
                    self.assertNotEqual(
                        claim.match_template_color(image, offset=(20, 20)),
                        collected.match_template_color(image, offset=(20, 20)),
                    )

    def test_no_button_does_not_pass_color_and_template_check(self):
        with patch.object(image_utils, 'TEMPLATE_MATCH_NON_NATIVE_720P', True):
            for server in ('cn', 'en', 'jp', 'tw'):
                with self.subTest(server=server):
                    image = np.zeros((720, 1280, 3), dtype=np.uint8)
                    for button in self.buttons(server):
                        self.assertFalse(button.match_template_color(image, offset=(20, 20)))


@dataclass
class KeyFrame:
    claim: bool = False
    collected: bool = False
    archives: bool = True
    claim_ready: bool = True
    reward: bool = False
    reward_ready: bool = True
    capacity_popup: bool = False
    campaign_menu: bool = False


class DataKeyFixture(unittest.TestCase):
    def setUp(self):
        self.reader = DataKey.__new__(DataKey)
        self.reader.config = SimpleNamespace(DataKey_ForceCollect=False)
        self.reader.device = Mock()
        self.reader.match_template_color = Mock(side_effect=self.match_color)
        self.reader.appear = Mock(side_effect=self.appear)
        self.reader.appear_then_click = Mock(side_effect=self.click_navigation)
        self.reader.handle_popup_confirm = Mock(
            side_effect=lambda name: self.reader.device.image.capacity_popup,
        )
        self.clicked_frames = []
        self.reader.device.click.side_effect = lambda button: self.clicked_frames.append(
            (self.reader.device.image, button),
        )
        self.ocr = Mock()
        self.ocr.ocr.return_value = (140, 160, 300)
        for target, replacement in (
            ('module.freebies.data_key.DATA_KEY', self.ocr),
            ('module.freebies.data_key.logger', Mock()),
        ):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def match_color(self, button, *, offset=(20, 20), interval=0, **kwargs):
        frame = self.reader.device.image
        if button is DATA_KEY_COLLECT:
            return frame.claim and (not interval or frame.claim_ready)
        if button is DATA_KEY_COLLECTED:
            return frame.collected
        raise AssertionError(f'未预期的颜色识别按钮: {button}')

    def appear(self, button, *, offset=0, interval=0, **kwargs):
        frame = self.reader.device.image
        if button is GET_ITEMS_1:
            return frame.reward and (not interval or frame.reward_ready)
        if button is WAR_ARCHIVES_CHECK:
            return frame.archives
        # 禁止重新引入会同时命中「领取／已领」的纯模板判断。
        raise AssertionError(f'不应使用纯模板判断密钥按钮: {button}')

    def click_navigation(self, button, **kwargs):
        self.assertIs(button, CAMPAIGN_MENU_GOTO_WAR_ARCHIVES)
        if self.reader.device.image.campaign_menu:
            self.reader.device.click(button)
            return True
        return False

    def frames(self, *frames):
        self.reader.device.image = frames[0]
        pending = deque(frames[1:])

        def screenshot():
            if not pending:
                raise GameStuckError('测试截图耗尽，密钥收集未结束')
            self.reader.device.image = pending.popleft()

        self.reader.device.screenshot.side_effect = screenshot


class TestDataKeyCollection(DataKeyFixture):
    def test_already_collected_skips_ocr_and_clicks(self):
        self.frames(KeyFrame(collected=True))
        self.assertFalse(self.reader.data_key_collect())
        self.ocr.ocr.assert_not_called()
        self.reader.device.click.assert_not_called()
        self.reader.device.screenshot.assert_not_called()
        self.reader.match_template_color.assert_called_once_with(DATA_KEY_COLLECTED, offset=(20, 20))

    def test_available_capacity_collects_and_waits_for_real_collected_state(self):
        available, final = KeyFrame(claim=True), KeyFrame(collected=True)
        self.frames(available, final)
        self.assertTrue(self.reader.data_key_collect())
        self.ocr.ocr.assert_called_once_with(available)
        self.assertEqual(self.clicked_frames, [(available, DATA_KEY_COLLECT)])
        self.reader.device.screenshot.assert_called_once()
        self.reader.match_template_color.assert_any_call(DATA_KEY_COLLECT, offset=(20, 20), interval=3)

    def test_full_capacity_without_force_skips_collection(self):
        self.ocr.ocr.return_value = (300, 0, 300)
        self.frames(KeyFrame(claim=True))
        self.assertFalse(self.reader.data_key_collect())
        self.reader.device.click.assert_not_called()
        self.reader.device.screenshot.assert_not_called()

    def test_full_capacity_with_force_collects_and_handles_capacity_popup(self):
        self.reader.config.DataKey_ForceCollect = True
        self.ocr.ocr.return_value = (300, 0, 300)
        available, popup, final = (
            KeyFrame(claim=True), KeyFrame(capacity_popup=True), KeyFrame(collected=True),
        )
        self.frames(available, popup, final)
        self.assertTrue(self.reader.data_key_collect())
        self.assertEqual(self.reader.device.screenshot.call_count, 2)
        self.assertEqual(self.clicked_frames, [(available, DATA_KEY_COLLECT)])
        self.reader.handle_popup_confirm.assert_any_call('DATA_KEY_LIMIT')

    def test_force_does_not_click_already_collected_button(self):
        self.reader.config.DataKey_ForceCollect = True
        self.frames(KeyFrame(collected=True))
        self.assertFalse(self.reader.data_key_collect())
        self.reader.device.click.assert_not_called()
        self.ocr.ocr.assert_not_called()

    def test_unchanged_claim_during_cooldown_does_not_end_early(self):
        initial = KeyFrame(claim=True)
        cooldown = KeyFrame(claim=True, claim_ready=False)
        final = KeyFrame(collected=True)
        self.frames(initial, cooldown, cooldown, final)
        self.reader._data_key_collect()
        self.assertEqual(self.reader.device.screenshot.call_count, 3)
        self.assertEqual(self.clicked_frames, [(initial, DATA_KEY_COLLECT)])
        self.assertIs(self.reader.device.image, final)

    def test_persistent_claim_without_collected_state_uses_stuck_recovery(self):
        initial = KeyFrame(claim=True)
        self.frames(initial, KeyFrame(claim=True, claim_ready=False))
        with self.assertRaises(GameStuckError):
            self.reader._data_key_collect()
        self.assertEqual(self.clicked_frames, [(initial, DATA_KEY_COLLECT)])

    def test_collected_frame_finishes_before_claim_click_can_run(self):
        self.frames(KeyFrame(collected=True))
        self.reader._data_key_collect()
        self.reader.device.click.assert_not_called()
        self.assertNotIn(call(DATA_KEY_COLLECT, offset=(20, 20), interval=3),
                         self.reader.match_template_color.call_args_list)

    def test_reward_is_dismissed_before_finishing_background_collected_state(self):
        reward = KeyFrame(collected=True, claim=True, reward=True)
        final = KeyFrame(collected=True)
        self.frames(reward, final)
        self.reader._data_key_collect()
        self.assertEqual(self.clicked_frames, [(reward, DATA_KEY_COLLECT)])
        self.reader.device.screenshot.assert_called_once()
        self.reader.appear.assert_any_call(GET_ITEMS_1, offset=20, interval=3)

    def test_visible_reward_during_cooldown_never_finishes_from_background(self):
        reward = KeyFrame(collected=True, claim=True, reward=True, reward_ready=False)
        ready_reward = KeyFrame(collected=True, reward=True)
        final = KeyFrame(collected=True)
        self.frames(reward, ready_reward, final)
        self.reader._data_key_collect()
        self.assertEqual(self.clicked_frames, [(ready_reward, DATA_KEY_COLLECT)])
        self.assertEqual(self.reader.device.screenshot.call_count, 2)
        self.assertEqual(self.reader.match_template_color.call_args_list,
                         [call(DATA_KEY_COLLECTED, offset=(20, 20))])

    def test_collected_button_requires_positive_archives_page(self):
        transition, final = KeyFrame(collected=True, archives=False), KeyFrame(collected=True)
        self.frames(transition, final)
        self.reader._data_key_collect()
        self.reader.device.screenshot.assert_called_once()
        self.reader.device.click.assert_not_called()
        self.assertIs(self.reader.device.image, final)

    def test_accidental_campaign_return_reenters_archives_before_completion(self):
        menu = KeyFrame(archives=False, campaign_menu=True)
        available, final = KeyFrame(claim=True), KeyFrame(collected=True)
        self.frames(menu, available, final)
        self.reader._data_key_collect()
        self.assertEqual(self.clicked_frames,
                         [(menu, CAMPAIGN_MENU_GOTO_WAR_ARCHIVES), (available, DATA_KEY_COLLECT)])
        self.reader.appear_then_click.assert_any_call(
            CAMPAIGN_MENU_GOTO_WAR_ARCHIVES, offset=(20, 20), interval=3,
        )

    def test_skip_first_false_acquires_new_screenshot_before_state_check(self):
        self.frames(KeyFrame(), KeyFrame(collected=True))
        self.reader._data_key_collect(skip_first_screenshot=False)
        self.reader.device.screenshot.assert_called_once()
        self.reader.device.click.assert_not_called()

    def test_run_keeps_navigation_and_clears_page_detection_intervals(self):
        self.reader.ui_ensure = Mock()
        self.reader.data_key_collect = Mock(return_value=False)
        self.reader.interval_clear = Mock()
        self.reader.run()
        self.reader.ui_ensure.assert_called_once_with(page_archives)
        self.reader.data_key_collect.assert_called_once_with()
        self.reader.interval_clear.assert_called_once_with(
            [page_archives.check_button, page_campaign_menu.check_button],
        )


if __name__ == '__main__':
    unittest.main()
