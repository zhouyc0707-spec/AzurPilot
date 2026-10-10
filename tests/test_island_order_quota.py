"""验证订单次数未知时不会被当作零，以及 CN 顶部真实计数截图。"""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from module.base.utils import load_image
from module.island.order_quota import DailyOrderQuotaOcr
from module.island_daily_order.assets import DAILY_ORDER_CHECK


FIXTURES = Path(__file__).parent / 'fixtures' / 'island_order_quota'


def order_page(counter=None):
    """匿名页面保留订单标题和顶部计数，其他内容全部清空。"""
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    DAILY_ORDER_CHECK.ensure_template()
    image[21:45, 127:219] = DAILY_ORDER_CHECK.image
    if counter is not None:
        image[23:46, 955:1015] = load_image(str(FIXTURES / counter))
    return image


class DailyOrderQuotaParserTests(unittest.TestCase):
    def test_complete_remaining_counter_includes_zero_and_upper_limit(self):
        ocr = DailyOrderQuotaOcr()
        for value in range(16):
            with self.subTest(value=value):
                self.assertEqual(ocr.after_process(f'{value}/15'), value)
        self.assertEqual(ocr.after_process(' 0 / 15 '), 0)

    def test_incomplete_ambiguous_or_illegal_counter_remains_unknown(self):
        ocr = DailyOrderQuotaOcr()
        for text in ('', ' ', '0', '/15', '0/', '015', '0/0', '0/1', '0/5', '0/16',
                     '-0/15', '-1/15', '16/15', '99/15', '100/15', '00/15', '01/15',
                     'D/15', 'O/15', '0/I5', '0/1S', '0/15?', '?0/15', 'x0/15x',
                     '10/150', '10/15/0', '今日剩余订单0/15', '0.0/15', '０/１５', None, 0):
            with self.subTest(text=text):
                self.assertIsNone(ocr.after_process(text))

    def test_wrong_server_or_invalid_image_does_not_call_model(self):
        ocr = DailyOrderQuotaOcr()
        with patch('module.ocr.ocr.Ocr.ocr') as recognize:
            for language in ('en', 'jp', 'tw'):
                with self.subTest(language=language), patch('module.island.order_quota.server.server', language):
                    self.assertIsNone(ocr.ocr(order_page()))
            with patch('module.island.order_quota.server.server', 'cn'):
                for image in (None, [], np.zeros((720, 1280), dtype=np.uint8),
                              np.zeros((360, 640, 3), dtype=np.uint8)):
                    self.assertIsNone(ocr.ocr(image))
            recognize.assert_not_called()

    def test_page_must_be_positively_identified_before_reading_counter(self):
        with patch('module.island.order_quota.server.server', 'cn'), patch('module.ocr.ocr.Ocr.ocr') as recognize:
            self.assertIsNone(DailyOrderQuotaOcr().ocr(np.zeros((720, 1280, 3), dtype=np.uint8)))
            recognize.assert_not_called()

    def test_empty_and_invalid_model_outputs_are_not_zero(self):
        ocr = DailyOrderQuotaOcr()
        for raw in ('', '0', '0/0', '-0/15', '16/15', 'D/15', '0/15?'):
            model = SimpleNamespace(atomic_ocr_for_single_lines=lambda images, alphabet: [list(raw)])
            with self.subTest(raw=raw), patch('module.island.order_quota.server.server', 'cn'), \
                    patch('module.ocr.ocr.OCR_MODEL', SimpleNamespace(azur_lane=model)):
                self.assertIsNone(ocr.ocr(order_page('remaining_10.png')))

    def test_model_exception_uses_existing_recovery_instead_of_becoming_zero(self):
        model = SimpleNamespace(atomic_ocr_for_single_lines=lambda images, alphabet: (_ for _ in ()).throw(
            RuntimeError('模拟 OCR 服务故障')))
        with patch('module.island.order_quota.server.server', 'cn'), \
                patch('module.ocr.ocr.OCR_MODEL', SimpleNamespace(azur_lane=model)):
            with self.assertRaisesRegex(RuntimeError, 'OCR'):
                DailyOrderQuotaOcr().ocr(order_page('remaining_10.png'))


class DailyOrderQuotaScreenshotTests(unittest.TestCase):
    def test_real_anonymous_counter_crops_with_fixed_local_cpu_model(self):
        from module.ocr.al_ocr import AlOcr, OcrSettings

        settings = OcrSettings(backend='onnx', device='cpu',
                               allow_vendor_execution_providers=False, model_version='alocr_en_v2_6')
        model = AlOcr(name='azur_lane', settings=settings)
        ocr = DailyOrderQuotaOcr()
        with patch('module.island.order_quota.server.server', 'cn'), \
                patch('module.ocr.ocr.OCR_MODEL', SimpleNamespace(azur_lane=model)):
            for name, expected in (('remaining_10.png', 10), ('remaining_11.png', 11)):
                with self.subTest(name=name):
                    self.assertEqual(ocr.ocr(order_page(name)), expected)
            self.assertIsNone(ocr.ocr(order_page()))

    def test_explicitly_synthetic_zero_crop_with_fixed_local_cpu_model(self):
        """验证模型能读取零；此夹具为真实 10/15 去掉首位 1，并非游戏零值现场。"""
        from module.ocr.al_ocr import AlOcr, OcrSettings

        settings = OcrSettings(backend='onnx', device='cpu',
                               allow_vendor_execution_providers=False, model_version='alocr_en_v2_6')
        model = AlOcr(name='azur_lane', settings=settings)
        with patch('module.island.order_quota.server.server', 'cn'), \
                patch('module.ocr.ocr.OCR_MODEL', SimpleNamespace(azur_lane=model)):
            self.assertEqual(DailyOrderQuotaOcr().ocr(order_page('synthetic_remaining_0.png')), 0)


if __name__ == '__main__':
    unittest.main()
