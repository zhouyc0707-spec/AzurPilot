"""订单倒计时来源、严格格式及真实 CPU OCR 回归。"""

from contextlib import nullcontext
from datetime import datetime, timedelta
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from module.base.button import Button
from module.config.config import AzurLaneConfig
from module.island.order import IslandOrder
from module.island_daily_order.assets import ALAS_ORDER_COOLDOWN_SPEED_UP, ALAS_ORDER_COOLDOWN_TIME


FIXTURES = Path(__file__).parent / 'fixtures/island_order_time'
DARK = (57, 59, 61)
WHITE = (255, 255, 255)


class OrderTimeTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(AzurLaneConfig, '__init__',
                                      side_effect=AssertionError('禁止初始化真实配置')))
        self.enterContext(patch.object(AzurLaneConfig, 'save',
                                      side_effect=AssertionError('禁止保存真实配置')))
        self.enterContext(patch('module.island.order.logger'))
        self.now = datetime(2026, 11, 8, 1, 4)
        self.enterContext(patch('module.island.order.current_time', return_value=self.now))
        self.order = IslandOrder.__new__(IslandOrder)
        self.order.device = SimpleNamespace(image=np.zeros((26, 90, 3), dtype=np.uint8), screenshot=Mock())
        self.order.config = SimpleNamespace(IslandDailyOrder_UrgentDetectRefreshTime=self.now)
        self.order.next_runtime = []
        self.button = Button(area=(0, 0, 90, 26), color=(), button=(0, 0, 90, 26), name='TEST_ORDER_TIME')

    def read_raw(self, text, **kwargs):
        with patch('module.island.order.Ocr') as reader:
            reader.return_value.ocr.return_value = text
            result = self.order._read_time(self.button, **kwargs)
        return result, reader

    def test_complete_one_or_two_digit_hours(self):
        for text, expected in (
            ('07:59:46', timedelta(hours=7, minutes=59, seconds=46)),
            ('7:59:46', timedelta(hours=7, minutes=59, seconds=46)),
            ('0:09:13', timedelta(minutes=9, seconds=13)),
            ('99:59:59', timedelta(hours=99, minutes=59, seconds=59)),
        ):
            with self.subTest(text=text):
                result, _ = self.read_raw(text)
                self.assertEqual(result, expected)

    def test_incomplete_noisy_or_invalid_fields_stay_unknown(self):
        for text in ('', '075946', '7:5946', '007:59:46', '07:5:46', '07:59:6',
                     '07:60:46', '07:59:60', 'prefix07:59:46', '07:59:46suffix',
                     '07:59:46 00:00:01', 'I7:59:46', '1天07:59:46', '00:00:00', None):
            with self.subTest(text=text):
                result, _ = self.read_raw(text)
                self.assertIsNone(result)

    def test_color_is_explicit_and_raw_text_is_not_filtered(self):
        for kwargs, expected_color in (({}, WHITE), ({'letter': DARK}, DARK)):
            with self.subTest(letter=expected_color):
                result, reader = self.read_raw('00:09:13', **kwargs)
                self.assertEqual(result, timedelta(minutes=9, seconds=13))
                self.assertEqual(reader.call_args.kwargs['letter'], expected_color)
                self.assertIn('alphabet', reader.call_args.kwargs)
                self.assertIsNone(reader.call_args.kwargs['alphabet'])

    def test_selected_cooldown_reads_dark_detail(self):
        self.order._click_order = Mock(return_value='cooldown')
        self.order._read_time = Mock(return_value=timedelta(minutes=9))
        self.order.scan_current_order_requirements = Mock()
        self.assertFalse(self.order._process_order((200, 200), 'regular'))
        self.order._read_time.assert_called_once_with(ALAS_ORDER_COOLDOWN_TIME, letter=DARK)
        self.assertEqual(self.order.next_runtime, [self.now + timedelta(minutes=9)])
        self.order.scan_current_order_requirements.assert_not_called()

    def test_rejected_order_reads_dark_detail_after_positive_cooldown(self):
        self.order.reject_count = 0
        self.order.loop = Mock(return_value=iter(range(2)))
        self.order._handle_popups = Mock(return_value=False)
        self.order.appear = Mock(side_effect=lambda button, **kwargs: button is ALAS_ORDER_COOLDOWN_SPEED_UP)
        self.order.appear_then_click = Mock(return_value=True)
        self.order._read_time = Mock(return_value=timedelta(minutes=10))
        self.assertTrue(self.order._reject_order())
        self.assertEqual(self.order.reject_count, 1)
        self.order._read_time.assert_called_once_with(ALAS_ORDER_COOLDOWN_TIME, letter=DARK)
        self.assertEqual(self.order.next_runtime, [self.now + timedelta(minutes=10)])

    def prepare_cooldown_run(self, readings):
        self.order.config = SimpleNamespace(
            IslandDailyOrder_RejectCount=0, IslandDailyOrder_StuckSeasonOrderId=0,
            IslandDailyOrder_UrgentDetectRefreshTime=self.now + timedelta(days=1),
            multi_set=lambda: nullcontext(), task_delay=Mock())
        for method in ('ui_ensure', 'ui_goto', '_enter_daily_order', '_back_to_island_phone'):
            setattr(self.order, method, Mock())
        self.order._get_urgent_refresh_time = Mock(return_value=self.now + timedelta(days=1))
        self.order._handle_popups = Mock(return_value=False)
        self.order._order_popup_visible = Mock(return_value=False)
        self.order.appear = Mock(return_value=True)
        self.order.detect_all_orders = Mock(return_value={
            'urgent': [], 'regular': [], 'season': [], 'cooldown': [(200, 200), (400, 200)]})
        self.order._read_time = Mock(side_effect=readings)
        self.order._click_order = Mock(return_value='cooldown')
        self.enterContext(patch('module.island.order.get_menu_reserve_items', return_value={}))
        self.enterContext(patch('module.island.order.menu_reservations_known', return_value=True))

    def test_map_time_success_does_not_open_cooldown_details(self):
        self.prepare_cooldown_run([timedelta(minutes=5), timedelta(minutes=20)])
        self.order.run()
        self.order._click_order.assert_not_called()
        self.assertEqual(self.order._read_time.call_count, 2)
        self.assertTrue(all(not call.kwargs for call in self.order._read_time.call_args_list))
        self.order.config.task_delay.assert_called_once_with(
            target=self.now + timedelta(minutes=5), server_update=True)

    def test_map_time_failure_uses_dark_detail_and_earliest_deadline(self):
        self.prepare_cooldown_run([None, timedelta(minutes=9), timedelta(minutes=20)])
        self.order.run()
        self.order._click_order.assert_called_once()
        calls = self.order._read_time.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0].kwargs, {})
        self.assertIs(calls[1].args[0], ALAS_ORDER_COOLDOWN_TIME)
        self.assertEqual(calls[1].kwargs, {'letter': DARK})
        self.assertEqual(calls[2].kwargs, {})
        self.order.config.task_delay.assert_called_once_with(
            target=self.now + timedelta(minutes=9), server_update=True)

    def test_urgent_white_time_updates_deadline_and_keeps_unknown_fallback(self):
        self.order._click_order = Mock(return_value='detail')
        self.order.scan_current_order_requirements = Mock(return_value={2602: (0, 25, -25)})
        self.order.is_order_satisfied = Mock(return_value=False)
        for remaining, expected in ((timedelta(hours=7, minutes=59, seconds=46),
                                     timedelta(hours=7, minutes=59, seconds=46)),
                                    (None, timedelta(hours=8))):
            with self.subTest(remaining=remaining):
                self.order.next_runtime = []
                self.order._read_time = Mock(return_value=remaining)
                self.assertFalse(self.order._process_order((411, 331), 'urgent'))
                self.assertEqual(self.order._read_time.call_args.args[0].area, (369, 398, 459, 424))
                self.assertEqual(self.order._read_time.call_args.kwargs, {})
                self.assertEqual(self.order.config.IslandDailyOrder_UrgentDetectRefreshTime, self.now + expected)
                self.assertEqual(self.order.next_runtime, [self.now + expected])


class OrderTimeScreenshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.enterClassContext(patch.object(AzurLaneConfig, '__init__',
                                          side_effect=AssertionError('禁止初始化真实配置')))
        cls.enterClassContext(patch.object(AzurLaneConfig, 'save',
                                          side_effect=AssertionError('禁止保存真实配置')))
        from module.ocr.al_ocr import AlOcr, ONNX_MODEL_PARAMS, OcrSettings
        model_root = Path(os.environ.get('AZURPILOT_TEST_MODEL_ROOT', Path(__file__).resolve().parents[1]))
        model_path, dictionary, version = ONNX_MODEL_PARAMS['azur_lane']['alocr_en_v2_6']
        files = model_root / model_path, model_root / dictionary
        if not all(path.is_file() for path in files):
            raise unittest.SkipTest('本地未提供计时模型；可用 AZURPILOT_TEST_MODEL_ROOT 指定模型根目录')
        cls.enterClassContext(patch.dict(ONNX_MODEL_PARAMS['azur_lane'], {
            'alocr_en_v2_6': (str(files[0]), str(files[1]), version)}))
        settings = OcrSettings(backend='onnx', device='cpu',
                               allow_vendor_execution_providers=False, model_version='alocr_en_v2_6')
        cls.model = AlOcr(name='azur_lane', settings=settings)
        cls.enterClassContext(patch('module.ocr.ocr.OCR_MODEL', SimpleNamespace(azur_lane=cls.model)))

    def test_real_map_and_detail_times_with_cpu_model(self):
        order = IslandOrder.__new__(IslandOrder)
        for name, letter, expected in (
            ('urgent_075946.png', WHITE, timedelta(hours=7, minutes=59, seconds=46)),
            ('cooldown_map_001941.png', WHITE, timedelta(minutes=19, seconds=41)),
            ('cooldown_map_000939.png', WHITE, timedelta(minutes=9, seconds=39)),
            ('cooldown_detail_000913.png', DARK, timedelta(minutes=9, seconds=13)),
        ):
            with self.subTest(fixture=name):
                image = np.array(Image.open(FIXTURES / name).convert('RGB'))
                height, width = image.shape[:2]
                button = Button(area=(0, 0, width, height), color=(),
                                button=(0, 0, width, height), name=name)
                order.device = SimpleNamespace(image=image)
                self.assertEqual(order._read_time(button, letter=letter), expected)
        self.assertEqual(self.model.model.text_rec.session.session.get_providers(), ['CPUExecutionProvider'])


if __name__ == '__main__':
    unittest.main()
