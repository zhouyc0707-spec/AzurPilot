"""鱼肉加工数字区域的真实截图回归和未知值保护。"""

import unittest
from pathlib import Path
from unittest.mock import Mock, PropertyMock, patch

import numpy as np

from module.base.utils import load_image
from module.config.config import AzurLaneConfig
from module.config.utils import DEFAULT_CONFIG_NAME
from module.island import fish_exchange_ocr as exchange
from module.ocr import al_ocr


FIXTURES = Path(__file__).parent / 'fixtures' / 'island_fish_meat'


class FishExchangeOcrTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 只读模板，防止真实 OCR 首次调用自动创建或保存账号配置。
        config = AzurLaneConfig('template_fish_exchange_ocr_test')
        cls.config_patch = patch.object(al_ocr, '_default_config', config)
        cls.name_patch = patch.object(al_ocr, '_default_config_name', DEFAULT_CONFIG_NAME)
        cls.config_patch.start()
        cls.name_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls.name_patch.stop()
        cls.config_patch.stop()

    @staticmethod
    def image(name):
        return load_image(str(FIXTURES / f'{name}.png'))

    def test_actual_cn_zero_and_separate_fish_stock(self):
        frame = self.image('exchange_cn_zero')
        self.assertEqual(exchange.read_exchange_amounts(frame), (0, 0))
        self.assertEqual(exchange.read_exchange_cards(frame, 2522), {
            5107: {'stock': 18, 'button': (213, 222, 321, 249)},
        })

    def test_real_nonzero_and_blue_preview(self):
        self.assertEqual(exchange.read_exchange_amounts(self.image('exchange_page')), (6, 0))
        self.assertEqual(exchange.read_exchange_amounts(self.image('exchange_selected')), (6, 3))
        self.assertEqual(exchange.read_exchange_cards(self.image('exchange_page'), 2521, 'en'), {
            5003: {'stock': 26, 'button': (213, 222, 321, 249)},
        })

    def test_number_region_does_not_silently_strip_letters(self):
        for text in ('', 'C18', 'd6', 'O?', '12 34', 'None', '-2', '1.5'):
            with self.subTest(text=text):
                self.assertIsNone(exchange.parse_quantity_text(text))
        self.assertEqual(exchange.parse_quantity_text('O'), 0)
        self.assertEqual(exchange.parse_quantity_text('1o0'), 100)
        self.assertEqual(exchange.parse_quantity_text('25'), 25)

    def test_preview_requires_explicit_plus(self):
        self.assertEqual(exchange.parse_quantity_text('+3', preview=True), 3)
        for text in ('3', '', 'O', '+C18', '++3'):
            with self.subTest(text=text):
                self.assertIsNone(exchange.parse_quantity_text(text, preview=True))

    def test_blank_owned_and_failed_ocr_do_not_become_zero(self):
        frame = self.image('exchange_cn_zero')
        frame[530:560, 990:1185] = 225
        self.assertIsNone(exchange.read_exchange_amounts(frame))
        model = Mock()
        model.atomic_ocr_for_single_lines.return_value = ['']
        with patch.object(exchange.Ocr, 'cnocr', new_callable=PropertyMock, return_value=model):
            self.assertIsNone(exchange.read_exchange_amounts(self.image('exchange_cn_zero')))

    def test_blue_text_recognition_failure_is_not_clear_selection(self):
        model = Mock()
        model.atomic_ocr_for_single_lines.side_effect = [['6'], ['']]
        with patch.object(exchange.Ocr, 'cnocr', new_callable=PropertyMock, return_value=model):
            self.assertIsNone(exchange.read_exchange_amounts(self.image('exchange_selected')))

    def test_cropped_number_boundary_is_unknown(self):
        for x in (990, 1184):
            frame = self.image('exchange_cn_zero')
            frame[538:550, x] = (60, 60, 60)
            self.assertIsNone(exchange.read_exchange_amounts(frame))

    def test_unknown_and_wrong_group_names_never_select_a_fish(self):
        frame = self.image('exchange_cn_zero')
        self.assertEqual(exchange.read_exchange_cards(frame, 2521), {})
        with patch.object(exchange.Ocr, 'ocr', return_value=['黄鳍']), \
                patch.object(exchange, '_read_numbers', return_value=[18]):
            self.assertEqual(exchange.read_exchange_cards(frame, 2522), {})

    def test_duplicate_name_is_ambiguous_even_with_only_one_valid_number(self):
        frame = self.image('exchange_cn_zero')
        for grid in (exchange.EXCHANGE_FISH_NAME_GRID, exchange.EXCHANGE_FISH_STOCK_GRID):
            first, second = grid.buttons[:2]
            x1, y1, x2, y2 = first.area
            xx1, yy1, xx2, yy2 = second.area
            frame[yy1:yy2, xx1:xx2] = frame[y1:y2, x1:x2]
        with patch.object(exchange.Ocr, 'ocr', return_value=['黄鳍金枪鱼', '黄鳍金枪鱼']), \
                patch.object(exchange, '_read_numbers', return_value=[18, None]):
            self.assertEqual(exchange.read_exchange_cards(frame, 2522), {})

    def test_blank_card_stock_is_not_expendable(self):
        frame = self.image('exchange_cn_zero')
        frame[197:221, 271:321] = np.array((200, 200, 200), dtype=np.uint8)
        self.assertEqual(exchange.read_exchange_cards(frame, 2522), {})


if __name__ == '__main__':
    unittest.main()
