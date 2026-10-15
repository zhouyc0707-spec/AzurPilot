"""邮件页面状态、点击节流和多分辨率识别回归；不连接游戏或加载真实配置。"""

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np
from PIL import Image

from module.base.button import Button
from module.base.resource import Resource
import module.base.utils as image_utils
from module.exception import GamePageUnknownError
from module.freebies import mail_white as mail
from module.freebies.assets import MAIL_BATCH_CLAIM, MAIL_MANAGE, MAIL_WHITE_EMPTY


@dataclass
class MailFrame:
    elapsed: float = 0
    page: bool = True
    home: bool = True
    manage: bool = False
    empty: bool = False
    batch: bool = False
    reward: bool = False
    main: bool = False


class MailEnterFixture(unittest.TestCase):
    def setUp(self):
        self.reader = mail.MailWhite.__new__(mail.MailWhite)
        self.reader.interval_timer = {}
        self.reader.config = SimpleNamespace()
        self.reader.device = Mock()
        self.reader.appear = Mock(side_effect=self.appear)
        self.reader._mail_manage_button = Mock(
            side_effect=lambda: MAIL_MANAGE if self.frame.manage else None,
        )
        self.reader._mail_is_empty = Mock(side_effect=lambda: self.frame.empty)
        self.reader.ui_main_appear_then_click = Mock(side_effect=self.enter_from_main)
        self.reader._handle_mail_reward = Mock(side_effect=self.handle_reward)
        self.clicks = []
        self.reader.device.click.side_effect = lambda button: self.clicks.append((self.frame.elapsed, button))
        self.clock = 100.0
        for target, replacement in (
            ('module.base.timer.time', lambda: self.clock),
            ('module.freebies.mail_white.logger', Mock()),
        ):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    @property
    def frame(self):
        return self.reader.device.image

    def appear(self, button, **kwargs):
        if button is MAIL_BATCH_CLAIM:
            return self.frame.batch
        if button is mail.page_mail.check_button:
            return self.frame.page
        if button is mail.GOTO_MAIN_WHITE:
            return self.frame.home
        if button is MAIL_WHITE_EMPTY:
            return self.frame.empty
        raise AssertionError(f'未预期的页面识别: {button}')

    def enter_from_main(self, page, **kwargs):
        self.assertIs(page, mail.page_mail)
        return self.frame.main

    def handle_reward(self):
        return self.frame.reward

    def frames(self, *frames):
        self.reader.device.image = frames[0]
        self.clock = 100 + frames[0].elapsed
        pending = deque(frames[1:])

        def screenshot():
            if not pending:
                raise AssertionError('测试截图耗尽：邮件流程未按预期结束')
            self.reader.device.image = pending.popleft()
            self.clock = 100 + self.frame.elapsed

        self.reader.device.screenshot.side_effect = screenshot

    def unknown_frames(self, **state):
        self.frames(*(MailFrame(elapsed=i * 2.1, **state) for i in range(12)))


class TestMailEnterState(MailEnterFixture):
    def test_management_appearing_after_old_timeout_is_clicked_and_not_empty(self):
        self.frames(MailFrame(), MailFrame(0.8), MailFrame(1.7, manage=True), MailFrame(1.9, batch=True))
        self.assertTrue(self.reader._mail_enter())
        self.assertEqual(self.clicks, [(1.7, MAIL_MANAGE)])
        self.reader.device.save_screenshot.assert_not_called()

    def test_home_icon_alone_is_unknown_and_never_empty(self):
        self.unknown_frames(page=False, home=True, empty=True)
        with self.assertRaises(GamePageUnknownError):
            self.reader._mail_enter()
        self.reader._mail_manage_button.assert_not_called()
        self.reader.device.click.assert_not_called()
        self.reader.device.save_screenshot.assert_called_once_with(genre='mail_unknown', interval=0)

    def test_mail_page_with_no_management_or_empty_proof_is_unknown(self):
        self.unknown_frames()
        with self.assertRaises(GamePageUnknownError):
            self.reader._mail_enter()
        self.reader.device.save_screenshot.assert_called_once_with(genre='mail_unknown', interval=0)

    def test_unknown_screenshot_failure_does_not_replace_page_error(self):
        self.unknown_frames()
        self.reader.device.save_screenshot.side_effect = OSError('测试目录不可写')
        with self.assertRaises(GamePageUnknownError):
            self.reader._mail_enter()

    def test_single_empty_frame_does_not_end_entry(self):
        self.frames(MailFrame(empty=True), MailFrame(1, manage=True), MailFrame(1.2, batch=True))
        self.assertTrue(self.reader._mail_enter())
        self.assertEqual(self.clicks, [(1, MAIL_MANAGE)])

    def test_empty_needs_multiple_observations_and_duration(self):
        self.frames(MailFrame(empty=True), MailFrame(0.4, empty=True), MailFrame(0.8, empty=True))
        self.assertFalse(self.reader._mail_enter())
        self.assertEqual(self.reader.device.screenshot.call_count, 2)
        self.reader.device.click.assert_not_called()
        self.reader.device.save_screenshot.assert_not_called()

    def test_two_empty_frames_on_slow_device_are_not_enough(self):
        self.frames(MailFrame(empty=True), MailFrame(1.2, empty=True), MailFrame(1.3, batch=True))
        self.assertTrue(self.reader._mail_enter())
        self.assertEqual(self.reader.device.screenshot.call_count, 2)

    def test_empty_duration_resets_after_unknown_frame(self):
        self.frames(
            MailFrame(empty=True), MailFrame(0.5, empty=True), MailFrame(0.7),
            MailFrame(1, empty=True), MailFrame(1.3, empty=True), MailFrame(1.7, empty=True),
        )
        self.assertFalse(self.reader._mail_enter())
        self.assertEqual(self.frame.elapsed, 1.7)

    def test_empty_duration_resets_when_mail_page_is_not_confirmed(self):
        self.frames(
            MailFrame(empty=True), MailFrame(0.5, empty=True), MailFrame(0.7, page=False, empty=True),
            MailFrame(1, empty=True), MailFrame(1.3, empty=True), MailFrame(1.7, empty=True),
        )
        self.assertFalse(self.reader._mail_enter())
        self.assertEqual(self.frame.elapsed, 1.7)

    def test_missing_home_icon_prevents_empty_and_management_actions(self):
        self.frames(
            MailFrame(home=False, empty=True, manage=True),
            MailFrame(1, home=False, empty=True, manage=True), MailFrame(1.2, batch=True),
        )
        self.assertTrue(self.reader._mail_enter())
        self.reader._mail_manage_button.assert_not_called()
        self.reader.device.click.assert_not_called()

    def test_management_has_priority_over_false_empty_match(self):
        self.frames(MailFrame(manage=True, empty=True), MailFrame(0.2, batch=True))
        self.assertTrue(self.reader._mail_enter())
        self.assertEqual(self.clicks, [(0, MAIL_MANAGE)])
        self.reader._mail_is_empty.assert_not_called()

    def test_management_cooldown_continues_screenshots_without_rapid_clicks(self):
        self.frames(
            MailFrame(manage=True), MailFrame(0.5, manage=True, empty=True),
            MailFrame(1, manage=True, empty=True), MailFrame(2.9, manage=True, empty=True),
            MailFrame(3.1, manage=True), MailFrame(3.3, batch=True),
        )
        self.assertTrue(self.reader._mail_enter())
        self.assertEqual(self.clicks, [(0, MAIL_MANAGE), (3.1, MAIL_MANAGE)])
        self.assertEqual(self.reader.device.screenshot.call_count, 5)

    def test_batch_page_returns_immediately_without_navigation(self):
        self.frames(MailFrame(batch=True))
        self.assertTrue(self.reader._mail_enter())
        self.reader.device.screenshot.assert_not_called()
        self.reader.ui_main_appear_then_click.assert_not_called()
        self.reader.device.click.assert_not_called()

    def test_reward_page_is_handled_before_empty_detection(self):
        self.frames(MailFrame(reward=True, empty=True), MailFrame(0.8, batch=True))
        self.assertTrue(self.reader._mail_enter())
        self.reader._mail_manage_button.assert_not_called()
        self.assertEqual(self.reader.device.screenshot.call_count, 1)

    def test_reward_interrupts_continuous_empty_confirmation(self):
        self.frames(
            MailFrame(empty=True), MailFrame(0.4, empty=True), MailFrame(0.7, reward=True),
            MailFrame(1, empty=True), MailFrame(1.3, empty=True), MailFrame(1.7, empty=True),
        )
        self.assertFalse(self.reader._mail_enter())
        self.assertEqual(self.frame.elapsed, 1.7)

    def test_navigation_from_main_keeps_capturing_until_mail_management(self):
        self.frames(MailFrame(page=False, home=False, main=True), MailFrame(0.8, manage=True), MailFrame(1, batch=True))
        self.assertTrue(self.reader._mail_enter())
        self.assertEqual(self.clicks, [(0.8, MAIL_MANAGE)])

    def test_can_request_fresh_first_screenshot(self):
        self.frames(MailFrame(), MailFrame(0.1, batch=True))
        self.assertTrue(self.reader._mail_enter(skip_first_screenshot=False))
        self.reader.device.screenshot.assert_called_once()


class TestMailClaimEmptyTransition(unittest.TestCase):
    def test_empty_on_any_reentry_never_filters_or_deletes_afterward(self):
        # 进入、功勋、维护两组、贸易、删除各阶段均可能遇到邮箱被清空。
        for failed_entry in range(6):
            with self.subTest(failed_entry=failed_entry):
                reader = mail.MailWhite.__new__(mail.MailWhite)
                reader._mail_enter = Mock(side_effect=[True] * failed_entry + [False])
                reader.mail_select_setting = Mock()
                reader.mail_select_all_setting = Mock()
                reader._mail_claim_execute = Mock()
                reader._mail_delete = Mock()
                reader._mail_quit = Mock()
                with patch.object(mail, 'logger', Mock()):
                    reader.mail_claim(merit=True, maintenance=True, trade_license=True, delete=True)
                self.assertEqual(reader._mail_enter.call_count, failed_entry + 1)
                self.assertEqual(reader.mail_select_setting.set.call_count, max(0, failed_entry - 1))
                self.assertEqual(reader._mail_claim_execute.call_count, max(0, failed_entry - 1))
                reader.mail_select_all_setting.set.assert_not_called()
                reader._mail_delete.assert_not_called()
                reader._mail_quit.assert_not_called()

    def test_complete_claim_retains_all_existing_filters_and_delete(self):
        reader = mail.MailWhite.__new__(mail.MailWhite)
        reader._mail_enter = Mock(return_value=True)
        reader.mail_select_setting = Mock()
        reader.mail_select_all_setting = Mock()
        reader._mail_claim_execute = Mock()
        reader._mail_delete = Mock()
        reader._mail_quit = Mock()
        with patch.object(mail, 'logger', Mock()):
            reader.mail_claim(merit=True, maintenance=True, trade_license=True, delete=True)
        self.assertEqual(
            [c.kwargs['contains'] for c in reader.mail_select_setting.set.call_args_list],
            [['merit'], ['coins', 'oil'], ['coins', 'oil', 'gems'], ['coins', 'oil', 'cube']],
        )
        self.assertEqual(reader._mail_claim_execute.call_count, 4)
        reader.mail_select_all_setting.set.assert_called_once_with(contains=['all'])
        reader._mail_delete.assert_called_once_with()
        reader._mail_quit.assert_called_once_with()


class TestMailManageRecognition(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(Resource, 'instances', {})
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def button(source, server):
        return Button(
            area=source.raw_area[server], color=source.raw_color[server],
            button=source.raw_button[server], file=source.raw_file[server], name=source.name,
        )

    @staticmethod
    def image(path):
        with Image.open(path) as source:
            return np.array(source.convert('RGB'))

    def reader(self, image, button):
        reader = mail.MailWhite.__new__(mail.MailWhite)
        reader.device = SimpleNamespace(image=image)
        reader.appear = Mock(side_effect=lambda target, **kwargs: target.match(image, **kwargs))
        reader.match_template_color = Mock(
            side_effect=lambda target, **kwargs: target.match_template_color(image, **kwargs),
        )
        return reader

    def current_image(self):
        path = Path(__file__).parent / 'fixtures' / 'mail_white' / 'cn_1440p_controls.png'
        image = self.image(path)
        self.assertEqual(image.shape, (1440, 2560, 3))
        image = cv2.resize(image, (1280, 720), interpolation=cv2.INTER_CUBIC)
        blur = cv2.GaussianBlur(image, (0, 0), sigmaX=1.0, sigmaY=1.0)
        return cv2.addWeighted(image, 0.90, blur, 0.10, 0)

    def test_current_1440p_old_template_fails_new_template_locates_actual_click(self):
        image = self.current_image()
        old = self.button(MAIL_MANAGE, 'cn')
        with patch.object(image_utils, 'TEMPLATE_MATCH_NON_NATIVE_720P', True):
            self.assertFalse(old.match(image, offset=(30, 30)))
            reader = self.reader(image, old)
            with patch.object(mail, 'MAIL_MANAGE', old), patch.object(mail.server, 'server', 'cn'):
                button = reader._mail_manage_button()
        self.assertIsNotNone(button)
        self.assertEqual(button.name, 'MAIL_MANAGE')
        self.assertTrue(390 <= button.button[0] < button.button[2] <= 510, button.button)
        self.assertTrue(620 <= button.button[1] < button.button[3] <= 680, button.button)
        self.assertNotEqual(tuple(button.button), tuple(old.button))

    def test_failed_old_template_offset_does_not_shift_fallback_click(self):
        image = self.current_image()
        old = self.button(MAIL_MANAGE, 'cn')
        reader = self.reader(image, old)

        def miss(*args, **kwargs):
            old._button_offset = (900, 100, 970, 119)
            return False

        reader.match_template_color.side_effect = miss
        with patch.object(mail, 'MAIL_MANAGE', old), patch.object(mail.server, 'server', 'cn'):
            button = reader._mail_manage_button()
        self.assertIsNotNone(button)
        self.assertLess(button.button[2], 520)
        self.assertGreater(button.button[1], 610)
        self.assertEqual(button.name, 'MAIL_MANAGE')

    def test_non_cn_does_not_use_current_cn_template(self):
        for server in ('en', 'jp', 'tw'):
            with self.subTest(server=server):
                reader = self.reader(np.zeros((720, 1280, 3), dtype=np.uint8), MAIL_MANAGE)
                reader.match_template_color.return_value = False
                reader.match_template_color.side_effect = None
                template = Mock()
                with patch.object(mail.server, 'server', server), patch.object(mail, 'TEMPLATE_MAIL_MANAGE_CURRENT', template):
                    self.assertIsNone(reader._mail_manage_button())
                template.match_result.assert_not_called()

    def test_blank_frame_never_produces_management_button(self):
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        old = self.button(MAIL_MANAGE, 'cn')
        for non_native in (False, True):
            with self.subTest(non_native=non_native), patch.object(image_utils, 'TEMPLATE_MATCH_NON_NATIVE_720P', non_native):
                with patch.object(mail, 'MAIL_MANAGE', old), patch.object(mail.server, 'server', 'cn'):
                    self.assertIsNone(self.reader(image, old)._mail_manage_button())

    def test_gray_original_management_shape_cannot_be_clicked(self):
        image = self.image(MAIL_MANAGE.raw_file['cn'])
        gray = cv2.cvtColor(cv2.cvtColor(image, cv2.COLOR_RGB2GRAY), cv2.COLOR_GRAY2RGB)
        for saturation in (0, 0.65):
            with self.subTest(saturation=saturation):
                old = self.button(MAIL_MANAGE, 'cn')
                disabled = cv2.addWeighted(image, saturation, gray, 1 - saturation, 0)
                reader = self.reader(disabled, old)
                with patch.object(mail, 'MAIL_MANAGE', old), patch.object(mail.server, 'server', 'cn'):
                    with patch.object(image_utils, 'TEMPLATE_MATCH_NON_NATIVE_720P', True):
                        if saturation:
                            # 部分灰化仍保留足以通过模板的轮廓，必须由颜色核验阻止点击。
                            self.assertTrue(old.match(disabled, offset=(30, 30)))
                        self.assertIsNone(reader._mail_manage_button())

    def test_gray_current_management_shape_cannot_be_clicked(self):
        image = self.current_image()
        gray = cv2.cvtColor(cv2.cvtColor(image, cv2.COLOR_RGB2GRAY), cv2.COLOR_GRAY2RGB)
        for saturation in (0, 0.65):
            with self.subTest(saturation=saturation):
                old = self.button(MAIL_MANAGE, 'cn')
                disabled = cv2.addWeighted(image, saturation, gray, 1 - saturation, 0)
                reader = self.reader(disabled, old)
                with patch.object(mail, 'MAIL_MANAGE', old), patch.object(mail.server, 'server', 'cn'):
                    with patch.object(image_utils, 'TEMPLATE_MATCH_NON_NATIVE_720P', True):
                        if saturation:
                            x1, y1, x2, y2 = old.area
                            similarity, _ = mail.TEMPLATE_MAIL_MANAGE_CURRENT.match_result(
                                disabled[y1 - 30:y2 + 30, x1 - 30:x2 + 30],
                            )
                            self.assertGreater(similarity, image_utils.lower_template_match_similarity(0.85))
                        self.assertIsNone(reader._mail_manage_button())

    def test_original_management_assets_still_work_for_each_server_and_threshold(self):
        for server in ('cn', 'en', 'jp', 'tw'):
            for non_native in (False, True):
                with self.subTest(server=server, non_native=non_native):
                    old = self.button(MAIL_MANAGE, server)
                    reader = self.reader(self.image(old.file), old)
                    template = Mock()
                    with patch.object(mail, 'MAIL_MANAGE', old), patch.object(mail.server, 'server', server):
                        with patch.object(image_utils, 'TEMPLATE_MATCH_NON_NATIVE_720P', non_native):
                            with patch.object(mail, 'TEMPLATE_MAIL_MANAGE_CURRENT', template):
                                self.assertIs(reader._mail_manage_button(), old)
                    template.match_result.assert_not_called()

    def test_original_batch_and_empty_templates_match_all_servers(self):
        for server in ('cn', 'en', 'jp', 'tw'):
            for source in (MAIL_BATCH_CLAIM, MAIL_WHITE_EMPTY):
                with self.subTest(server=server, source=source.name):
                    button = self.button(source, server)
                    image = self.image(button.file)
                    with patch.object(image_utils, 'TEMPLATE_MATCH_NON_NATIVE_720P', False):
                        self.assertTrue(button.match(image, offset=(20, 20)))


class TestMailEmptyEvidence(unittest.TestCase):
    def setUp(self):
        self.reader = mail.MailWhite.__new__(mail.MailWhite)
        self.reader.device = SimpleNamespace(image=object())
        self.reader.appear = Mock()

    def test_cn_requires_complete_zero_count_and_positive_capacity(self):
        for result in ('0/100', '0/1', '0/999', ' 0/100\n'):
            with self.subTest(result=result):
                count = Mock()
                count.ocr.return_value = result
                with patch.object(mail, 'MAIL_COUNT', count), patch.object(mail.server, 'server', 'cn'):
                    self.assertTrue(self.reader._mail_is_empty())
                count.ocr.assert_called_once_with(self.reader.device.image)
        self.reader.appear.assert_not_called()

    def test_cn_nonzero_incomplete_and_invalid_counts_are_not_empty(self):
        for result in ('62/100', '7/100', '', '0', '100', '/100', '0/', '0/0', '0/000',
                       '00/100', '0/0100', '0 /100', '0/ 100', 'O/100', '0/1000x', 'x0/100'):
            with self.subTest(result=result):
                count = Mock()
                count.ocr.return_value = result
                with patch.object(mail, 'MAIL_COUNT', count), patch.object(mail.server, 'server', 'cn'):
                    self.assertFalse(self.reader._mail_is_empty())
        self.reader.appear.assert_not_called()

    def test_non_cn_retains_server_empty_template_without_count_ocr(self):
        for server in ('en', 'jp', 'tw'):
            for empty in (False, True):
                with self.subTest(server=server, empty=empty):
                    count = Mock()
                    self.reader.appear.reset_mock()
                    self.reader.appear.return_value = empty
                    with patch.object(mail, 'MAIL_COUNT', count), patch.object(mail.server, 'server', server):
                        self.assertEqual(self.reader._mail_is_empty(), empty)
                    self.reader.appear.assert_called_once_with(MAIL_WHITE_EMPTY, offset=(20, 20))
                    count.ocr.assert_not_called()

    def test_count_preprocessing_preserves_both_white_and_black_text(self):
        image = np.array([[[255, 255, 255], [0, 0, 0], [128, 128, 128]]], dtype=np.uint8)
        output = mail.MAIL_COUNT.pre_process(image)
        self.assertEqual(output.shape, (1, 3))
        self.assertEqual(output.dtype, np.uint8)
        self.assertEqual(output.tolist(), [[255, 0, 128]])

    def test_current_fixture_count_62_over_100_is_not_empty(self):
        # 模型读取结果已由人工诊断单独验证，此处不在单元测试中加载 OCR 模型。
        path = Path(__file__).parent / 'fixtures' / 'mail_white' / 'cn_1440p_controls.png'
        with Image.open(path) as source:
            image = np.array(source.convert('RGB'))
        image = cv2.resize(image, (1280, 720), interpolation=cv2.INTER_CUBIC)
        blur = cv2.GaussianBlur(image, (0, 0), sigmaX=1.0, sigmaY=1.0)
        self.reader.device.image = cv2.addWeighted(image, 0.90, blur, 0.10, 0)
        count = Mock()
        count.ocr.return_value = '62/100'
        with patch.object(mail, 'MAIL_COUNT', count), patch.object(mail.server, 'server', 'cn'):
            self.assertFalse(self.reader._mail_is_empty())
        count.ocr.assert_called_once_with(self.reader.device.image)
        self.reader.appear.assert_not_called()


if __name__ == '__main__':
    unittest.main()
