"""仓库详情只读观测：严格名称、明确零值、页面闭环及同任务新截图。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from module.exception import GameStuckError
from module.island.data import DIC_ISLAND_ITEM
from module.island.stock_probe import (
    StockProbeSession, find_warehouse_item_cards, parse_item_detail_stock, read_item_stocks,
)


def text_box(text, x, y, width=80, height=18, score=0.99):
    return text, [(x, y), (x + width, y), (x + width, y + height), (x, y + height)], score


def detail_frame(item=2604, stock=17, language='cn'):
    titles = {'cn': '详情', 'en': 'Details', 'jp': '詳細', 'tw': '詳情'}
    own = {'cn': '已拥有:', 'en': 'Owned:', 'jp': '所持中：', 'tw': '已擁有:'}
    return [text_box(titles[language], 510, 140), text_box(DIC_ISLAND_ITEM[item]['name'][language], 640, 190),
            text_box(own[language] + str(stock), 640, 250, width=140)]


class ProbeMain:
    def __init__(self, stocks, pages=None, wrong_detail=False, edit_mode=False):
        self.stocks = dict(stocks)
        self.pages = pages or [list(stocks)]
        self.page = 0
        self.state = 'edit' if edit_mode else 'warehouse'
        self.edit_mode = edit_mode
        self.wrong_detail = wrong_detail
        self.selected = None
        self.clicks = []
        self.filters = []
        self.screenshots = 0
        self.swipes = 0
        self.device = SimpleNamespace(
            image=np.zeros((720, 1280, 3), dtype=np.uint8), click=self.click,
            screenshot=self.screenshot, swipe_vector=self.swipe, save_screenshot=Mock(),
        )

    def screenshot(self):
        self.screenshots += 1
        return self.device.image

    def click(self, button):
        name = str(button)
        self.clicks.append(name)
        if name.startswith('STOCK_PROBE_ITEM_'):
            self.selected = int(name.rsplit('_', 1)[1])
            self.state = 'detail'
        else:
            self.state = 'warehouse'

    def swipe(self, **kwargs):
        self.swipes += 1
        self.page = min(self.page + 1, len(self.pages) - 1)

    def loop(self, **kwargs):
        for _ in range(40):
            self.screenshot()
            yield self.device.image

    def match_template_color(self, *args, **kwargs):
        return self.state == 'warehouse'

    def warehouse_filter(self, kind, origin):
        self.filters.append((kind, origin))
        self.state = 'edit' if self.edit_mode else 'warehouse'
        self.page = 0

    def detections(self):
        if self.state == 'detail':
            item = 2601 if self.wrong_detail else self.selected
            return detail_frame(item=item, stock=self.stocks[self.selected])
        return [text_box(DIC_ISLAND_ITEM[item]['name']['cn'], 310 + index * 142, 273)
                for index, item in enumerate(self.pages[self.page])]


class StockProbeTests(unittest.TestCase):
    def session(self, main):
        session = StockProbeSession(main)
        session._detect = main.detections
        return session

    def test_detail_has_real_total_stock_and_explicit_zero(self):
        self.assertEqual(parse_item_detail_stock(detail_frame(stock=17), 2604), 17)
        self.assertEqual(parse_item_detail_stock(detail_frame(stock=0), 2604), 0)

    def test_four_languages_use_verified_own_label(self):
        for language in ('cn', 'en', 'jp', 'tw'):
            with self.subTest(language=language):
                self.assertEqual(parse_item_detail_stock(detail_frame(stock=23, language=language),
                                                        2604, language), 23)

    def test_unknown_or_wrong_item_does_not_become_zero(self):
        self.assertIsNone(parse_item_detail_stock([], 2604))
        self.assertIsNone(parse_item_detail_stock(detail_frame(item=2601), 2604))
        self.assertIsNone(parse_item_detail_stock(detail_frame()[:2], 2604))
        self.assertIsNone(parse_item_detail_stock(detail_frame()[1:], 2604))

    def test_background_card_is_not_detail_identity(self):
        frame = detail_frame(item=2601) + [text_box('皮料', 310, 273)]
        self.assertIsNone(parse_item_detail_stock(frame, 2604))

    def test_split_own_label_requires_unique_same_line_number(self):
        frame = detail_frame()[:2] + [text_box('已拥有:', 640, 250, width=100), text_box('21', 765, 250)]
        self.assertEqual(parse_item_detail_stock(frame, 2604), 21)
        self.assertIsNone(parse_item_detail_stock(frame + [text_box('5', 755, 250, width=10)], 2604))

    def test_duplicate_or_low_confidence_counter_is_unknown(self):
        frame = detail_frame() + [text_box('已拥有:99', 640, 280)]
        self.assertIsNone(parse_item_detail_stock(frame, 2604))
        frame = detail_frame()[:2] + [text_box('已拥有:17', 640, 250, score=0.6)]
        self.assertIsNone(parse_item_detail_stock(frame, 2604))

    def test_warehouse_full_name_inside_grid_required(self):
        rows = [text_box('皮料', 310, 273), text_box('羊毛', 454, 440)]
        self.assertEqual(set(find_warehouse_item_cards(rows, (2604, 2605))), {2604, 2605})
        self.assertEqual(find_warehouse_item_cards([text_box('猪皮', 310, 273)], (2604,)), {})
        self.assertEqual(find_warehouse_item_cards([text_box('皮料', 50, 70)], (2604,)), {})

    def test_duplicate_stacks_can_use_one_card_for_total_detail(self):
        rows = [text_box('皮料', 310, 273), text_box('皮料', 454, 273)]
        self.assertEqual(len(find_warehouse_item_cards(rows, (2604,))), 1)

    def test_read_session_closes_details_and_returns_observed_only(self):
        main = ProbeMain({2604: 17, 2605: 0})
        self.assertEqual(self.session(main).run((2604, 2605)), {2604: 17, 2605: 0})
        self.assertEqual(main.state, 'warehouse')
        self.assertEqual(main.filters, [('all_kind', 'ranch')])
        self.assertEqual(main.clicks, ['STOCK_PROBE_ITEM_2604', 'ISLAND_CLICK_SAFE_AREA',
                                      'STOCK_PROBE_ITEM_2605', 'ISLAND_CLICK_SAFE_AREA'])
        self.assertGreaterEqual(main.screenshots, 10)
        main.device.save_screenshot.assert_not_called()

    def test_missing_card_is_none_after_finite_search(self):
        main = ProbeMain({2605: 12})
        result = self.session(main).run((2604, 2605))
        self.assertEqual(result, {2605: 12})
        self.assertIsNone(result.get(2604))
        self.assertEqual(main.swipes, 1)
        main.device.save_screenshot.assert_called_once()

    def test_next_viewport_can_supply_missing_item(self):
        main = ProbeMain({2604: 3, 2605: 8}, pages=[[2605], [2604]])
        self.assertEqual(self.session(main).run((2604, 2605)), {2604: 3, 2605: 8})
        self.assertEqual(main.swipes, 1)

    def test_wrong_detail_name_does_not_confirm_quantity(self):
        main = ProbeMain({2604: 17}, wrong_detail=True)
        self.assertEqual(self.session(main).run((2604,)), {})
        self.assertEqual(main.state, 'warehouse')
        self.assertEqual(main.clicks.count('STOCK_PROBE_ITEM_2604'), 1)
        self.assertTrue(all('CONFIRM' not in name and 'CONVERT' not in name for name in main.clicks))

    def test_edit_mode_never_clicks_item_cards(self):
        main = ProbeMain({2604: 17}, edit_mode=True)
        with self.assertRaises(GameStuckError):
            self.session(main).run((2604,))
        self.assertEqual(main.clicks, [])
        main.device.save_screenshot.assert_called_once()

    def test_new_call_reads_new_stock_without_cache(self):
        main = ProbeMain({2604: 17})
        with patch('module.island.stock_probe.StockProbeSession', side_effect=lambda task: self.session(task)):
            self.assertEqual(read_item_stocks(main, ('raw_leather',)), {2604: 17})
            main.stocks[2604] = 4
            self.assertEqual(read_item_stocks(main, (2604,)), {2604: 4})

    def test_empty_request_does_not_touch_game(self):
        main = ProbeMain({2604: 17})
        self.assertEqual(read_item_stocks(main, ()), {})
        self.assertEqual(main.screenshots, 0)
        self.assertEqual(main.filters, [])


if __name__ == '__main__':
    unittest.main()
