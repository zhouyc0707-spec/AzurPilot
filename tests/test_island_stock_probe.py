"""仓库详情只读观测：严格名称、明确零值、页面闭环及同任务新截图。"""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from module.exception import GameStuckError
from module.island.assets import ISLAND_INFO_EXIT
from module.island.data import DIC_ISLAND_ITEM
from module.island.stock_probe import (
    StockProbeSession, find_warehouse_item_cards, parse_item_detail_stock, read_item_stocks,
)
from module.ui.assets import ISLAND_WAREHOUSE_CHECK


FIXTURES = Path(__file__).parent / 'fixtures' / 'island_stock_probe'


def text_box(text, x, y, width=80, height=18, score=0.99):
    return text, [(x, y), (x + width, y), (x + width, y + height), (x, y + height)], score


def detail_frame(item=2604, stock=17, language='cn'):
    titles = {'cn': '详情', 'en': 'Details', 'jp': '詳細', 'tw': '詳情'}
    own = {'cn': '已拥有:', 'en': 'Owned:', 'jp': '所持中：', 'tw': '已擁有:'}
    return [text_box(titles[language], 360, 158), text_box(DIC_ISLAND_ITEM[item]['name'][language], 490, 251, width=150),
            text_box(own[language] + str(stock), 790, 255, width=140)]


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
        self.ignored_close_clicks = 0
        self.close_intervals = []
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
        elif self.ignored_close_clicks:
            self.ignored_close_clicks -= 1
        else:
            self.state = 'warehouse'

    def swipe(self, **kwargs):
        self.swipes += 1
        step = -1 if kwargs.get('vector', (0, -1))[1] > 0 else 1
        self.page = max(0, min(self.page + step, len(self.pages) - 1))

    def loop(self, **kwargs):
        for _ in range(40):
            self.screenshot()
            yield self.device.image

    def match_template_color(self, button, **kwargs):
        if button is ISLAND_INFO_EXIT:
            return self.state == 'detail'
        return self.state == 'warehouse'

    def appear_then_click(self, button, interval=0, **kwargs):
        self.close_intervals.append(interval)
        if self.match_template_color(button, **kwargs):
            self.click(button)
            return True
        return False

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

    def test_verified_blue_title_bar_prefix_is_accepted_only_in_title_area(self):
        frame = detail_frame()
        frame[0] = text_box('I详情', 344, 158, width=68, height=32)
        self.assertEqual(parse_item_detail_stock(frame, 2604), 17)
        for title in ('物品详情', '详情页', 'XX详情', '详情I'):
            with self.subTest(title=title):
                self.assertIsNone(parse_item_detail_stock([text_box(title, 344, 158)] + frame[1:], 2604))
        self.assertIsNone(parse_item_detail_stock([text_box('I详情', 10, 600)] + frame[1:], 2604))

    def test_real_failed_frame_ocr_reads_wheat_without_accepting_wrong_item(self):
        fixture = json.loads((FIXTURES / 'detail_wheat_50_ocr.json').read_text(encoding='utf-8'))
        rows = fixture['detections']
        self.assertEqual(rows[0][0], 'I详情')
        self.assertEqual(parse_item_detail_stock(rows, 2000), 50)
        self.assertIsNone(parse_item_detail_stock(rows, 2001))

    def test_real_detail_close_button_is_identifiable_without_ocr(self):
        # 只保留真实标题、按钮及小麦字段像素，其他区域已遮蔽；不对脱敏画布重跑 OCR。
        with Image.open(FIXTURES / 'detail_wheat_50.png') as source:
            image = np.asarray(source.convert('RGB'))
        self.assertTrue(ISLAND_INFO_EXIT.match_template_color(
            image, offset=(30, 30), similarity=0.85, threshold=30))
        self.assertFalse(ISLAND_WAREHOUSE_CHECK.match_template_color(
            image, offset=(30, 30), similarity=0.85, threshold=30))

    def test_same_row_name_can_start_at_or_below_own_label(self):
        for name_y in (251, 255, 260):
            with self.subTest(name_y=name_y):
                frame = detail_frame()
                frame[1] = text_box('皮料', 490, name_y, width=100)
                self.assertEqual(parse_item_detail_stock(frame, 2604), 17)

    def test_name_or_counter_outside_detail_body_is_not_accepted(self):
        frame = detail_frame()
        self.assertIsNone(parse_item_detail_stock([frame[0], text_box('皮料', 490, 380), frame[2]], 2604))
        self.assertIsNone(parse_item_detail_stock(frame[:2] + [text_box('已拥有:17', 100, 255)], 2604))

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
        self.assertEqual(main.clicks, ['STOCK_PROBE_ITEM_2604', 'ISLAND_INFO_EXIT',
                                      'STOCK_PROBE_ITEM_2605', 'ISLAND_INFO_EXIT'])
        self.assertGreaterEqual(main.screenshots, 10)
        main.device.save_screenshot.assert_not_called()

    def test_close_detail_does_not_need_ocr_and_retries_ignored_click(self):
        main = ProbeMain({2604: 17})
        main.state = 'detail'
        main.ignored_close_clicks = 1
        session = self.session(main)
        session._detect = Mock(side_effect=AssertionError('关闭详情不应调用OCR'))
        session._close_detail()
        self.assertEqual(main.clicks, ['ISLAND_INFO_EXIT', 'ISLAND_INFO_EXIT'])
        self.assertEqual(main.close_intervals, [2, 2])
        self.assertEqual(main.state, 'warehouse')
        self.assertEqual(main.screenshots, 3)

    def test_close_detail_without_positive_page_stays_bounded_and_does_not_guess_click(self):
        main = ProbeMain({2604: 17})
        main.state = 'unknown'
        session = self.session(main)
        session._detect = Mock(side_effect=AssertionError('关闭详情不应调用OCR'))
        with self.assertRaises(GameStuckError):
            session._close_detail()
        self.assertEqual(main.clicks, [])
        main.device.save_screenshot.assert_called_once()

    def test_unreadable_detail_still_closes_and_leaves_stock_unknown(self):
        main = ProbeMain({2604: 17})
        session = self.session(main)
        original = main.detections
        session._detect = lambda: [] if main.state == 'detail' else original()
        self.assertEqual(session.run((2604,)), {})
        self.assertEqual(main.clicks, ['STOCK_PROBE_ITEM_2604', 'ISLAND_INFO_EXIT'])
        self.assertEqual(main.state, 'warehouse')

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

    def test_full_round_rewinds_retained_scroll_before_scanning(self):
        main = ProbeMain({2604: 3, 2605: 8}, pages=[[2605], [2604]])
        original_filter = main.warehouse_filter

        def retained_filter(*args):
            original_filter(*args)
            main.page = 1

        main.warehouse_filter = retained_filter
        self.assertEqual(self.session(main).run((2604, 2605), reset_to_top=True), {2604: 3, 2605: 8})
        self.assertEqual(main.swipes, 3)

    def test_full_round_can_scan_beyond_original_eight_swipes(self):
        page_ids = list(DIC_ISLAND_ITEM)[:11]
        main = ProbeMain({item: index for index, item in enumerate(page_ids)}, pages=[[item] for item in page_ids])
        result = self.session(main).run((page_ids[-1],), max_swipes=16, reset_to_top=True)
        self.assertEqual(result, {page_ids[-1]: 10})
        self.assertEqual(main.swipes, 11)

    def test_duplicate_stack_on_later_page_does_not_add_detail_total_twice(self):
        main = ProbeMain({2604: 17, 2605: 8}, pages=[[2604], [2604, 2605]])
        self.assertEqual(self.session(main).run((2604, 2605)), {2604: 17, 2605: 8})
        self.assertEqual(main.clicks.count('STOCK_PROBE_ITEM_2604'), 1)

    def test_round_scan_bound_does_not_convert_unseen_item_to_zero(self):
        main = ProbeMain({2604: 3, 2605: 8}, pages=[[2605], [2604]])
        result = self.session(main).run((2604,), max_swipes=0)
        self.assertEqual(result, {})
        self.assertEqual(main.swipes, 0)

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
