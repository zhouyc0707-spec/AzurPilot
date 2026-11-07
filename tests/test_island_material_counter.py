"""完整材料计数必须贯通补货检查与派遣扣账，残缺读数保持未知。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.island.island import (
    ISLAND_SELECT_PRODUCT_CHECK,
    ISLAND_WORKING,
    MATERIAL_INSUFFICIENT,
    MATERIAL_READY,
    POST_ADD_ORDER,
    Island,
)
from module.island.round_inventory import IslandRoundInventory


MATERIAL_ID = 1000


class MaterialCounterParsingTests(unittest.TestCase):
    def test_complete_counters_accept_zero_stock_and_sum_required_terms(self):
        cases = {
            '0/9': (0, 9),
            '62/(2)': (62, 2),
            '110/(2+6)': (110, 8),
            '112/2+6': (112, 8),
            '99/99': (99, 99),
            ' 110 / ( 2 + 6 ) ': (110, 8),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(Island.parse_select_product_material_counter(text), expected)

    def test_existing_ocr_character_corrections_remain_supported(self):
        for text, stock in (('I/9', 1), ('D/9', 0), ('S/9', 5), ('B/9', 8)):
            with self.subTest(text=text):
                self.assertEqual(Island.parse_select_product_material_counter(text), (stock, 9))

    def test_incomplete_or_invalid_counters_remain_unknown(self):
        for text in (
            '', '69', '/9', '9/', '110/(2+6', '110/2+6)', '110/((2+6))',
            '1/2/3', '-1/9', '1/-9', '1.5/9', '1/2.5', 'a/9', '1/a',
            '1/seed', '1/0', '1/(0+0)', '110/2+', '110/(2+)', '110/+2',
        ):
            with self.subTest(text=text):
                self.assertIsNone(Island.parse_select_product_material_counter(text))


class SharedMaterialCounterTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.island.island.logger'))
        self.island = Island.__new__(Island)
        self.island.device = SimpleNamespace(image=object(), screenshot=Mock(), click=Mock())
        self.island.island_round = IslandRoundInventory({MATERIAL_ID: 20, 1: 1000})
        self.island.appear = Mock(return_value=True)
        self.island.loop = Mock(side_effect=lambda **kwargs: iter(range(5)))
        self.island.goto_shop_from_select_product = Mock()
        self.island.buy_shop_item = Mock()
        self.island.back_to_select_product_after_shop = Mock()

    def ensure(self, target=99):
        return self.island.ensure_select_product_material(
            item_button=object(), required_quantity=target, shop_check=object(),
            item_name='wheat', material_id=MATERIAL_ID)

    def test_parenthesized_stock_above_target_does_not_visit_shop(self):
        self.island.ocr_select_product_material_detail = Mock(return_value=(110, '110/(2+6)'))

        self.assertEqual(self.ensure(), MATERIAL_READY)

        self.assertEqual(self.island.island_round.get(MATERIAL_ID), 110)
        self.assertEqual(self.island._round_material_receipt, {'item_id': MATERIAL_ID})
        self.island.goto_shop_from_select_product.assert_not_called()
        self.island.buy_shop_item.assert_not_called()

    def test_ambiguous_or_inconsistent_counter_never_buys_or_invents_zero(self):
        for observed in ((69, '69'), (0, '/9'), (61, '62/(2)'), (0, '0/')):
            with self.subTest(observed=observed):
                self.island.island_round.observe(MATERIAL_ID, 20)
                self.island.ocr_select_product_material_detail = Mock(return_value=observed)

                self.assertEqual(self.ensure(), MATERIAL_INSUFFICIENT)

                self.assertIsNone(self.island.island_round.get(MATERIAL_ID))
                self.assertEqual(self.island.island_round.get(1), 1000)
                self.island.goto_shop_from_select_product.assert_not_called()
                self.island.buy_shop_item.assert_not_called()
                self.assertIsNone(getattr(self.island, '_round_material_receipt', None))

    def test_real_dispatch_read_returns_total_required_material(self):
        self.island._round_material_receipt = {'item_id': MATERIAL_ID}
        digit = SimpleNamespace(last_valid=True, ocr=Mock(return_value=3))
        self.enterContext(patch('module.island.island.Digit', return_value=digit))
        for stock, text, expected in (
            (110, '110/(2+6)', (3, 110, 8)),
            (62, '62/(2)', (3, 62, 2)),
            (112, '112/2+6', (3, 112, 8)),
            (0, '0/9', (3, 0, 9)),
            (69, '69', None),
            (61, '62/(2)', None),
        ):
            with self.subTest(text=text, stock=stock):
                self.island.ocr_select_product_material_detail = Mock(return_value=(stock, text))
                self.assertEqual(self.island._read_round_dispatch_amounts(), expected)

    def test_confirm_requires_two_equal_frames_then_deducts_total_once(self):
        self.island._round_material_receipt = {'item_id': MATERIAL_ID}
        state = SimpleNamespace(frame=0, clicked=False)
        read_frames = []
        click_frames = []
        digit = SimpleNamespace(last_valid=True, ocr=Mock(return_value=3))
        self.enterContext(patch('module.island.island.Digit', return_value=digit))

        def frames(**kwargs):
            for frame in range(1, 6):
                state.frame = frame
                self.island.device.image = frame
                yield frame

        def appear(button, **kwargs):
            if state.clicked:
                return button is ISLAND_WORKING
            return button is ISLAND_SELECT_PRODUCT_CHECK or button is POST_ADD_ORDER

        def read_material(**kwargs):
            read_frames.append(state.frame)
            return 110, '110/(2+7)' if state.frame == 1 else '110/(2+6)'

        def click(button):
            self.assertIs(button, POST_ADD_ORDER)
            click_frames.append(state.frame)
            state.clicked = True

        self.island.loop = frames
        self.island.appear = appear
        self.island.ocr_select_product_material_detail = read_material
        self.island.device.click = click

        self.assertTrue(self.island.confirm_post_add_order(
            preserve_quantity=True, max_batches=3))

        self.assertEqual(read_frames, [1, 2, 3])
        self.assertEqual(click_frames, [3])
        self.assertEqual(self.island._round_dispatch_batches, 3)
        self.assertEqual(self.island._round_dispatch_materials, {MATERIAL_ID: 8})
        self.assertEqual(self.island.island_round.get(MATERIAL_ID), 102)
        self.assertIsNone(self.island._round_material_receipt)
        self.assertEqual(self.island.island_round.in_production, {})


if __name__ == '__main__':
    unittest.main()
