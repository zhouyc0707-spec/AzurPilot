"""战役资源 OCR 的格式、固定探针与失败保护回归，全部使用离线替身。"""

import copy
import itertools
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from module.base.button import Button
from module.base.utils import color_similar, get_color
from module.campaign import campaign_status
from module.campaign.campaign_status import CampaignStatus
from module.exception import GameStuckError
from module.log_res.log_res import LogRes
from module.ocr.ocr import Digit


class CampaignResourceObservationTests(unittest.TestCase):
    def setUp(self):
        self.previous = {
            resource: {'Value': 200, 'Limit': 500, 'Record': datetime(2026, 1, 1, 8)}
            for resource in ('Coin', 'Oil', 'Pt')
        }
        self.config = SimpleNamespace(
            data={'Dashboard': copy.deepcopy(self.previous)},
            modified={'Other.Setting.Value': True}, update=Mock(),
        )
        self.runner = CampaignStatus.__new__(CampaignStatus)
        self.runner.config = self.config
        self.runner.device = SimpleNamespace(
            image=None, screenshot=Mock(), sleep=Mock(side_effect=AssertionError('不得固定休眠')),
        )
        self.clock = 100.0
        self.enterContext(patch('module.base.timer.time', side_effect=lambda: self.clock))
        self.enterContext(patch.object(LogRes, 'groups', {resource: {} for resource in self.previous}))
        self.observe = self.enterContext(patch.object(LogRes, '_observe'))
        self.snapshot = self.enterContext(patch.object(LogRes, '_record_all_resource_snapshot'))

    def assert_no_resource_write(self):
        self.assertEqual({'Other.Setting.Value': True}, self.config.modified)
        self.assertEqual(self.previous, self.config.data['Dashboard'])
        self.observe.assert_not_called()
        self.snapshot.assert_not_called()

    def read_resource(self, resource, frames, repeat=False, **kwargs):
        """首帧复用旧截图，其余帧由假设备截图推进时间，不访问真实游戏。"""
        remaining = itertools.cycle(frames) if repeat else itertools.chain(frames, itertools.repeat(frames[-1]))
        self.runner.device.image = next(remaining)

        def screenshot():
            self.clock += 0.4
            self.runner.device.image = next(remaining)

        def read_number(button, name, letter, *, require_valid):
            self.assertTrue(require_valid)
            return self.runner.device.image[1 if name.endswith('_LIMIT') else 0]

        self.runner.device.screenshot.side_effect = screenshot
        with patch.object(self.runner, '_get_num', side_effect=read_number):
            return getattr(self.runner, f'get_{resource.lower()}')(**kwargs)

    def test_pt_accepts_prefixed_and_plain_digits_without_warning(self):
        for text, expected in (('X33840', 33840), ('33840', 33840), ('  X123  ', 123), ('\n33840\t', 33840), ('0', 0)):
            with self.subTest(text=text), patch.object(campaign_status.OCR_PT, 'ocr', return_value=text), \
                    patch.object(campaign_status.logger, 'warning') as warning:
                self.assertEqual(expected, self.runner.get_event_pt(update=True))
                warning.assert_not_called()
                self.assertEqual(expected, self.config.modified['Dashboard.Pt.Value'])
                self.observe.assert_called_with('Pt', expected)
        self.assertEqual(5, self.config.update.call_count)

    def test_pt_rejects_entire_malformed_text_without_recording(self):
        for text in ('', ' ', 'X', 'X123X456', '123 456', '12a34', 'X 123', 'ABC33840', '-123', 'X123\n456'):
            with self.subTest(text=text), patch.object(campaign_status.OCR_PT, 'ocr', return_value=text):
                self.assertEqual(0, self.runner.get_event_pt())
                self.assert_no_resource_write()

    def test_stale_button_offset_cannot_move_background_probe(self):
        source = campaign_status.OCR_OIL_CHECK
        probe = Button(area=source.area, color=source.color, button=source.area, name='离线资源探针')
        probe._button_offset = tuple(np.array(probe.area) + (10, 2, 10, 2))
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        x1, y1, x2, y2 = probe.area
        image[y1:y2, x1:x2] = probe.color
        self.assertFalse(color_similar(get_color(image, probe.button), probe.color))
        self.runner.device.image = image
        ocr = Mock(last_valid=True)
        ocr.ocr.return_value = 1234
        with patch.object(campaign_status, 'OCR_OIL_CHECK', probe), \
                patch.object(campaign_status, 'get_color', wraps=get_color) as sample, \
                patch.object(campaign_status, 'Digit', return_value=ocr):
            self.assertEqual(1234, self.runner._get_num(campaign_status.OCR_OIL, 'OCR_OIL', require_valid=True))
        self.assertIs(image, sample.call_args.args[0])
        self.assertEqual(probe.area, sample.call_args.args[1])
        ocr.ocr.assert_called_once_with(image)

    def test_known_backgrounds_preserve_cn_and_jp_ocr_parameters(self):
        cases = (
            ('cn', (82, 82, 82), (239, 239, 239)),
            ('jp', (82, 82, 82), (201, 201, 201)),
            ('cn', (59, 59, 64), (165, 165, 165)),
            ('jp', (59, 59, 64), (165, 165, 165)),
        )
        for region, color, expected_letter in cases:
            with self.subTest(region=region, color=color), \
                    patch.object(campaign_status.server, 'server', region), \
                    patch.object(campaign_status, 'get_color', return_value=color), \
                    patch.object(campaign_status, 'Digit') as factory:
                factory.return_value.last_valid = True
                factory.return_value.ocr.return_value = 1234
                self.assertEqual(1234, self.runner._get_num(
                    campaign_status.OCR_COIN, 'OCR_COIN', letter=(239, 239, 239), require_valid=True,
                ))
                factory.assert_called_once_with(
                    campaign_status.OCR_COIN, name='OCR_COIN', letter=expected_letter, threshold=128,
                )

    def test_unknown_background_does_not_attempt_digit_ocr(self):
        with patch.object(campaign_status, 'get_color', return_value=(35, 35, 35)), \
                patch.object(campaign_status, 'Digit') as factory, \
                patch.object(campaign_status.logger, 'warning') as warning:
            self.assertIsNone(self.runner._get_num(campaign_status.OCR_OIL, 'OCR_OIL', require_valid=True))
        factory.assert_not_called()
        warning.assert_not_called()

    def test_empty_digit_ocr_on_normal_background_is_invalid(self):
        ocr = Digit.__new__(Digit)
        ocr.SHOW_REVISE_WARNING = False
        with patch.object(campaign_status, 'get_color', return_value=(82, 82, 82)), \
                patch.object(campaign_status, 'Digit', return_value=ocr), \
                patch.object(ocr, 'ocr', side_effect=lambda image: ocr.after_process('')):
            self.assertIsNone(self.runner._get_num(campaign_status.OCR_OIL, 'OCR_OIL', require_valid=True))
        self.assertFalse(ocr.last_valid)

    def test_resource_recovers_on_fresh_screenshot_and_records_only_valid_frame(self):
        for resource in ('Coin', 'Oil'):
            with self.subTest(resource=resource):
                self.observe.reset_mock()
                self.snapshot.reset_mock()
                self.runner.device.screenshot.reset_mock()
                self.assertEqual(300, self.read_resource(resource, [(None, None), (300, 600)], update=True))
                self.runner.device.screenshot.assert_called_once()
                self.observe.assert_called_once_with(resource, {'Value': 300, 'Limit': 600})
                self.snapshot.assert_called_once_with({resource: 300})
                self.assertEqual(300, self.config.modified[f'Dashboard.{resource}.Value'])
                self.assertEqual(600, self.config.modified[f'Dashboard.{resource}.Limit'])
        self.runner.device.sleep.assert_not_called()

    def test_resource_timeout_preserves_balance_timestamp_limit_and_history(self):
        for resource in ('Coin', 'Oil'):
            with self.subTest(resource=resource):
                self.runner.device.screenshot.reset_mock()
                start = self.clock
                self.assertEqual(0, self.read_resource(resource, [(None, 600)], update=True))
                self.assert_no_resource_write()
                self.assertGreaterEqual(self.runner.device.screenshot.call_count, 2)
                self.assertLess(self.clock - start, 2)
        self.runner.device.sleep.assert_not_called()

    def test_stable_low_balances_are_recorded_after_confirmation(self):
        for resource in ('Coin', 'Oil'):
            for value in (0, 7, 99):
                with self.subTest(resource=resource, value=value):
                    self.observe.reset_mock()
                    self.snapshot.reset_mock()
                    self.runner.device.screenshot.reset_mock()
                    self.assertEqual(value, self.read_resource(resource, [(value, 600)]))
                    self.observe.assert_called_once_with(resource, {'Value': value, 'Limit': 600})
                    self.snapshot.assert_called_once_with({resource: value})
                    self.assertEqual(value, self.config.modified[f'Dashboard.{resource}.Value'])
                    self.assertGreaterEqual(self.runner.device.screenshot.call_count, 2)

    def test_transient_low_balance_followed_by_normal_balance_only_records_recovery(self):
        for resource in ('Coin', 'Oil'):
            with self.subTest(resource=resource):
                self.observe.reset_mock()
                self.snapshot.reset_mock()
                self.assertEqual(300, self.read_resource(resource, [(7, 600), (300, 600)]))
                self.observe.assert_called_once_with(resource, {'Value': 300, 'Limit': 600})
                self.snapshot.assert_called_once_with({resource: 300})

    def test_changing_low_balances_time_out_without_recording(self):
        for resource in ('Coin', 'Oil'):
            with self.subTest(resource=resource):
                self.assertEqual(0, self.read_resource(resource, [(7, 600), (99, 600)], repeat=True))
                self.assert_no_resource_write()

    def test_invalid_frame_interrupts_low_balance_confirmation(self):
        for resource in ('Coin', 'Oil'):
            # 第二组末尾两帧同为 0，不能跨过空帧把首个 0 累计为第三次确认。
            for values in ((0, None, 0, 7), (0, None, 0, 0)):
                with self.subTest(resource=resource, values=values):
                    frames = [(value, 600) for value in values]
                    self.assertEqual(0, self.read_resource(resource, frames, repeat=True))
                    self.assert_no_resource_write()

    def test_valid_balance_at_existing_threshold_is_recorded(self):
        for resource in ('Coin', 'Oil'):
            with self.subTest(resource=resource):
                self.observe.reset_mock()
                self.assertEqual(100, self.read_resource(resource, [(100, 600)]))
                self.observe.assert_called_once_with(resource, {'Value': 100, 'Limit': 600})

    def test_unreadable_limit_does_not_prevent_valid_balance_or_replace_existing_limit(self):
        for resource in ('Coin', 'Oil'):
            with self.subTest(resource=resource):
                self.observe.reset_mock()
                self.snapshot.reset_mock()
                self.assertEqual(300, self.read_resource(resource, [(300, None)]))
                self.observe.assert_called_once_with(resource, {'Value': 300})
                self.snapshot.assert_called_once_with({resource: 300})
                self.assertNotIn(f'Dashboard.{resource}.Limit', self.config.modified)

    def test_explicit_fresh_screenshot_does_not_use_stale_arrival_frame(self):
        for resource in ('Coin', 'Oil'):
            with self.subTest(resource=resource):
                self.observe.reset_mock()
                self.runner.device.screenshot.reset_mock()
                self.assertEqual(300, self.read_resource(
                    resource, [(9999, 600), (300, 600)], skip_first_screenshot=False,
                ))
                self.runner.device.screenshot.assert_called_once()
                self.observe.assert_called_once_with(resource, {'Value': 300, 'Limit': 600})

    def test_device_failure_propagates_without_recording_failed_read(self):
        self.runner.device.screenshot.side_effect = GameStuckError('截图失败')
        with self.assertRaises(GameStuckError):
            self.runner.get_oil(skip_first_screenshot=False)
        self.assert_no_resource_write()


if __name__ == '__main__':
    unittest.main()
