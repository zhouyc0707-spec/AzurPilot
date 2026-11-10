"""养鸡日志 OCR 回放：匿名猪饲料标签仅承载版式，不代表养鸡原图或实机验收。

保留真实标签定位、材料文本解析、两帧派遣确认与库存扣账，仅替换 OCR 返回值和设备状态。
"""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from module.exception import GameStuckError
from module.island.island import Island, ISLAND_SELECT_PRODUCT_CHECK, ISLAND_WORKING, POST_ADD_ORDER
from module.island.item_ids import LOCAL_TO_ITEM_ID
from module.island.round_inventory import IslandRoundInventory


FIXTURE = Path(__file__).parent / 'fixtures/island_material_counter/pig_62_10_30.png'
COMPLETE = '52/(8+40)'
LOGGED_PAIR = ('52/8+40)', COMPLETE)
FEED = LOCAL_TO_ITEM_ID['chicken_feed']
REJECTED_PAIRS = (
    ('stock_digit', COMPLETE, '53/8+40)'),
    ('required_digit', COMPLETE, '52/8+41)'),
    ('missing_plus', COMPLETE, '52/840)'),
    ('extra_plus', COMPLETE, '52/8++40)'),
    ('missing_slash', COMPLETE, '528+40)'),
    ('extra_slash', COMPLETE, '52//8+40)'),
    ('both_incomplete', '52/8+40)', '52/(8+40'),
    ('same_sum_different_terms', COMPLETE, '52/9+39)'),
    ('different_complete_terms_same_sum', COMPLETE, '52/(9+39)'),
    ('missing_both_parentheses', COMPLETE, '52/8+40'),
    ('parenthesis_on_stock_side', COMPLETE, '(52/8+40)'),
    ('letter_substitution', COMPLETE, 'S2/8+40)'),
    ('whitespace_difference', COMPLETE, '52/8 +40)'),
    ('plain_counter_added_parenthesis', '52/8+40', '52/8+40)'),
)


class MaterialCounterParenthesisTests(unittest.TestCase):
    def setUp(self):
        with Image.open(FIXTURE) as image:
            self.image = np.asarray(image.convert('RGB'))
        self.enterContext(patch('module.island.material_counter.logger'))
        self.enterContext(patch('module.island.island.logger'))

    def make_island(self, pairs, *, return_to_working=True):
        """真实猪饲料标签作定位载体；只回放指定的双阈值 OCR 文本。"""
        island = Island.__new__(Island)
        island.island_round = IslandRoundInventory({'chicken_feed': 52})
        island._round_material_receipt = {'item_id': FEED}
        island.is_post_detail_visible = Mock(return_value=False)
        state = SimpleNamespace(frame=0, clicked=False)
        reads, clicks = [], []

        def read(images, **kwargs):
            self.assertEqual(len(images), 2)
            self.assertTrue(all(image.ndim == 2 for image in images))
            self.assertEqual(kwargs, {'direct_ocr': True})
            reads.append(state.frame)
            return list(pairs[min(max(state.frame - 1, 0), len(pairs) - 1)])

        reader = self.enterContext(patch('module.island.material_counter._MaterialCounterOcr'))
        reader.return_value.ocr.side_effect = read
        digit = SimpleNamespace(last_valid=True, ocr=Mock(return_value=6))
        self.enterContext(patch('module.island.island.Digit', return_value=digit))

        def frames(**kwargs):
            for frame in range(1, 9):
                state.frame = frame
                yield frame

        def appear(button, **kwargs):
            if state.clicked and return_to_working:
                return button is ISLAND_WORKING
            return button is ISLAND_SELECT_PRODUCT_CHECK or button is POST_ADD_ORDER

        def click(button):
            self.assertIs(button, POST_ADD_ORDER)
            clicks.append(state.frame)
            state.clicked = True

        island.loop = frames
        island.appear = appear
        island.device = SimpleNamespace(image=self.image, click=Mock(side_effect=click))
        return island, reads, clicks

    def test_logged_pair_and_reversed_order_reach_real_material_detail(self):
        for pair in (LOGGED_PAIR, LOGGED_PAIR[::-1]):
            with self.subTest(pair=pair):
                island, _, _ = self.make_island([pair])
                self.assertEqual(island.ocr_select_product_material_detail(), (52, COMPLETE))
                self.assertEqual(Island.parse_select_product_material_counter(COMPLETE), (52, 48))

    def test_single_missing_right_parenthesis_also_requires_complete_other_read(self):
        for pair in (('52/(8+40', COMPLETE), (COMPLETE, '52/(8+40')):
            with self.subTest(pair=pair):
                island, _, _ = self.make_island([pair])
                self.assertEqual(island.ocr_select_product_material_detail(), (52, COMPLETE))

    def test_logged_ocr_replay_confirms_two_frames_then_debits_48_once(self):
        for pairs in ([LOGGED_PAIR, LOGGED_PAIR[::-1]], [LOGGED_PAIR[::-1], LOGGED_PAIR]):
            with self.subTest(pairs=pairs):
                island, reads, clicks = self.make_island(pairs)
                self.assertTrue(island.confirm_post_add_order('养鸡日志回放', preserve_quantity=True))
                self.assertEqual(reads, [1, 2])
                self.assertEqual(clicks, [2])
                island.device.click.assert_called_once_with(POST_ADD_ORDER)
                self.assertEqual(island._round_dispatch_batches, 6)
                self.assertEqual(island._round_dispatch_materials, {FEED: 48})
                self.assertEqual(island.island_round.get('chicken_feed'), 4)
                self.assertIsNone(island._round_material_receipt)

    def test_disagreement_between_valid_frames_resets_confirmation(self):
        pairs = [LOGGED_PAIR, (COMPLETE, '52/8+41)'), LOGGED_PAIR, LOGGED_PAIR[::-1]]
        island, reads, clicks = self.make_island(pairs)
        self.assertTrue(island.confirm_post_add_order('养鸡日志回放', preserve_quantity=True))
        self.assertEqual(reads, [1, 2, 3, 4])
        self.assertEqual(clicks, [4])
        self.assertEqual(island.island_round.get('chicken_feed'), 4)

    def test_other_disagreements_remain_unknown_and_never_dispatch(self):
        for label, first, second in REJECTED_PAIRS:
            for pair in ((first, second), (second, first)):
                with self.subTest(reason=label, pair=pair):
                    island, _, clicks = self.make_island([pair])
                    self.assertEqual(island.ocr_select_product_material_detail(), (None, ''))
                    self.assertFalse(island.confirm_post_add_order('分歧日志回放', preserve_quantity=True))
                    self.assertEqual(clicks, [])
                    island.device.click.assert_not_called()
                    self.assertEqual(island.island_round.get('chicken_feed'), 52)
                    self.assertIsNone(island._round_dispatch_batches)
                    self.assertEqual(island._round_dispatch_materials, {})

    def test_matching_but_incomplete_reads_still_fail_the_existing_parser(self):
        for text in ('52/8+40)', '52/(8+40', '52840', '52/(8+)'):
            with self.subTest(text=text):
                island, _, _ = self.make_island([(text, text)])
                self.assertEqual(island.ocr_select_product_material_detail(), (None, text))
                self.assertFalse(island.confirm_post_add_order('残缺日志回放', preserve_quantity=True))
                island.device.click.assert_not_called()
                self.assertEqual(island.island_round.get('chicken_feed'), 52)

    def test_matching_complete_text_keeps_existing_dispatch_behavior(self):
        island, reads, clicks = self.make_island([(COMPLETE, COMPLETE)])
        self.assertTrue(island.confirm_post_add_order('一致读数回放', preserve_quantity=True))
        self.assertEqual(reads, [1, 2])
        self.assertEqual(clicks, [2])
        self.assertEqual(island.island_round.get('chicken_feed'), 4)

    def test_uncertain_dispatch_never_reclicks_or_commits_material(self):
        island, reads, clicks = self.make_island([LOGGED_PAIR], return_to_working=False)
        with self.assertRaises(GameStuckError):
            island.confirm_post_add_order('结果未知回放', preserve_quantity=True)
        self.assertEqual(reads, [1, 2])
        self.assertEqual(clicks, [2])
        island.device.click.assert_called_once_with(POST_ADD_ORDER)
        self.assertEqual(island.island_round.get('chicken_feed'), 52)
        self.assertIsNone(island._round_dispatch_batches)
        self.assertEqual(island._round_dispatch_materials, {})


if __name__ == '__main__':
    unittest.main()
