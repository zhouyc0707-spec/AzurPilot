"""结算报告排除纸角时，凭证仍保留完整五位数量。"""

import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from module.azur_stats.scene.operation_siren import SceneOperationSiren
from module.base.button import Button
from module.statistics.item import Item, resolve_amount_max


FIXTURES = Path(__file__).parent / 'fixtures/statistics_amounts'


class TestOpsiReportAmount(unittest.TestCase):
    def setUp(self):
        self.grid = SceneOperationSiren().auto_search_item_group

    def test_report_uses_full_height_without_left_paper_corner(self):
        for name in ('OrdnanceTestingReportT3', 'OrdnanceTestingReportT4'):
            with self.subTest(name=name):
                self.assertEqual(self.grid.amount_area_for(name), (35, 49, 63, 63))
                self.assertEqual(resolve_amount_max(name, self.grid.amount_max), 5)
        self.assertTrue(self.grid.amount_ocr.strict_amount_max)

    def test_real_report_quantity_is_one_on_both_scroll_pages(self):
        folder = Path(__file__).parent / 'fixtures/opsi_reward_scroll'
        for filename, top in (('first.png', 343), ('last.png', 300)):
            with self.subTest(filename=filename), Image.open(folder / filename) as panel:
                # 脱敏面板内第4行第7格；旧宽数量区会把左侧纸角一起读成21。
                icon = np.array(panel.convert('RGB').crop((470, top, 534, top + 64)))
                item = Item(icon, Button(area=(0, 0, 64, 64), color=(),
                                         button=(0, 0, 64, 64), name='report'))
                amount_image = item.crop(self.grid.amount_area_for('OrdnanceTestingReportT3'))
                self.assertEqual(self.grid.amount_ocr.ocr_with_validation(
                    amount_image, item_name='OrdnanceTestingReportT3', direct_ocr=True), 1)

    def test_five_digit_currency_still_reads_the_leading_digits(self):
        # 将真实凭证数量切片放回原生 64px 物品格，再走按物品裁剪与数量校验。
        for filename, expected in (('opsi_1692.png', 14202), ('opsi_2750.png', 10532)):
            with self.subTest(filename=filename):
                canvas = np.zeros((64, 64, 3), dtype=np.uint8)
                canvas[49:63, 15:63] = np.array(Image.open(FIXTURES / filename).convert('RGB'))
                item = Item(canvas, Button(area=(0, 0, 64, 64), color=(),
                                          button=(0, 0, 64, 64), name='currency'))
                image = item.crop(self.grid.amount_area_for('OperationCoin'))
                self.assertEqual(self.grid.amount_ocr.ocr_with_validation(
                    image, item_name='OperationCoin', direct_ocr=True), expected)

    def test_report_rule_does_not_change_other_items_or_popup_layout(self):
        for name in ('GearDesignPlanGunT5', 'CoordinateObscure', 'OperationCoin', 'SpecialItemTokens'):
            with self.subTest(name=name):
                self.assertEqual(self.grid.amount_area_for(name), (15, 49, 63, 63))
        popup = SceneOperationSiren().item_grid
        self.assertEqual(popup.amount_area_for('OrdnanceTestingReportT3'), (50, 76, 94, 94))


if __name__ == '__main__':
    unittest.main()
