"""鱼肉有限兑换的库存保护、OCR 歧义和提交闭环离线验证。"""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from module.base.utils import load_image, random_rectangle_point
from module.exception import GameStuckError, IslandFishExchangeError
from module.handler.info_handler import InfoHandler
from module.island.fish_exchange import (
    FishExchangeSession, get_fish_protection, parse_exchange_amounts,
    parse_exchange_cards, plan_fish_exchange,
)
from module.island.raw_catalog import get_extra_raw_items
from module.island.round_inventory import IslandRoundInventory


def detection(text, x, y, width=30, height=15, score=0.99):
    return text, [(x, y), (x + width, y), (x + width, y + height), (x, y + height)], score


class ReadConfig:
    def __init__(self, values):
        self.values = values

    def cross_get(self, path, default=None):
        return self.values.get(path, default)


class ExchangeMain:
    def __init__(self, missed=False, wrong_yield=False):
        self.current = 20
        self.preview = 0
        self.popup = False
        self.missed = missed
        self.wrong_yield = wrong_yield
        self.clicks = []
        self.device = SimpleNamespace(click=self.click, save_screenshot=Mock())

    def click(self, button):
        self.clicks.append(str(button))
        if str(button).startswith('FISH_EXCHANGE_CARD_'):
            if not self.missed:
                self.preview += 8 if self.wrong_yield else 4
        else:
            self.popup = True

    def loop(self, **kwargs):
        for _ in range(15):
            yield None

    def handle_popup_confirm(self, name):
        if not self.popup:
            return False
        self.popup = False
        self.current += self.preview
        self.preview = 0
        return True

    def appear_then_click(self, *args, **kwargs):
        return False


class TestFishExchange(unittest.TestCase):
    def popup_session(self):
        # 11月13日错误末帧只保留弹窗；OCR输出用同帧实测结果回放，按钮仍真实匹配。
        path = Path(__file__).parent / 'fixtures/island_fish_meat/exchange_confirm_cn.png'
        main = InfoHandler.__new__(InfoHandler)
        main.interval_timer = {}
        clicks = []
        main.device = SimpleNamespace(
            image=load_image(str(path)), stuck_record_add=Mock(), save_screenshot=Mock(),
            click=lambda button: clicks.append((button.name, random_rectangle_point(button.button))))
        session = FishExchangeSession(main)
        session._detect = Mock(return_value=[detection('是否确认进行加工?', 288, 123, score=0.9763)])
        return main, session, clicks

    def test_actual_processing_popup_requires_local_horizontal_search_and_clicks_blue_confirm(self):
        main, session, clicks = self.popup_session()
        self.assertFalse(main.handle_popup_confirm('ISLAND_FISH_EXCHANGE'))
        self.assertEqual(clicks, [])
        self.assertTrue(session._handle_exchange_popup())
        self.assertEqual(len(clicks), 1)
        name, (x, y) = clicks[0]
        self.assertEqual(name, 'POPUP_CONFIRM_WHITE_ISLAND_FISH_EXCHANGE')
        self.assertTrue(656 <= x <= 930 and 480 <= y <= 539)
        self.assertFalse(np.any(main.device.image[:144]))
        self.assertFalse(np.any(main.device.image[561:]))
        self.assertFalse(np.any(main.device.image[:, :325]))
        self.assertFalse(np.any(main.device.image[:, 955:]))

    def test_processing_popup_never_confirms_unknown_low_confidence_or_button_only(self):
        for detections in ([detection('是否确认购买?', 288, 123)],
                           [detection('是否确认进行加工?', 288, 123, score=0.89)], []):
            with self.subTest(detections=detections):
                _, session, clicks = self.popup_session()
                session._detect.return_value = detections
                self.assertFalse(session._handle_exchange_popup())
                self.assertEqual(clicks, [])

    def test_processing_text_without_confirm_button_never_clicks(self):
        main, session, clicks = self.popup_session()
        main.device.image[480:540, 656:931] = 0
        self.assertFalse(session._handle_exchange_popup())
        self.assertEqual(clicks, [])

    def test_actual_popup_flow_submits_once_and_records_only_exact_arrival(self):
        # 弹窗来自现场，其余页面/数量按状态模拟；不冒充加工后的实机截图验收。
        for arrival in ((21, 0), (20, 0), (21, 1), None):
            with self.subTest(arrival=arrival):
                main, session, clicks = self.popup_session()
                popup_frame = main.device.image.copy()
                blank = np.zeros_like(popup_frame)
                main.current, main.preview, main.popup = 9, 0, False
                main.settling, main.settled = 0, False
                main.device.image = blank
                ledger = main.island_round = IslandRoundInventory({2521: 9, 5007: 54})
                ledger.consume = Mock(wraps=ledger.consume)
                ledger.observe = Mock(wraps=ledger.observe)
                clock = [1000.0]

                def screenshot():
                    clock[0] += 0.2
                    self.assertLess(clock[0], 1050)
                    # 游戏到账确认完成前，始终不能提前扣鱼或加鱼肉。
                    self.assertEqual(ledger.get(2521), 9)
                    self.assertEqual(ledger.get(5007), 54)
                    ledger.consume.assert_not_called()
                    if main.settling:
                        main.settling -= 1
                        if not main.settling:
                            main.settled = True
                            if arrival is not None:
                                main.current, main.preview = arrival
                    main.device.image = popup_frame if main.popup else blank
                    return main.device.image

                def click(button):
                    name, point = button.name, random_rectangle_point(button.button)
                    clicks.append((name, point))
                    if name == 'FISH_EXCHANGE_CARD_5007':
                        main.preview += 3
                    elif name == 'ALAS_EXCHANGE_CONFIRM':
                        main.popup = True
                    elif name == 'POPUP_CONFIRM_WHITE_ISLAND_FISH_EXCHANGE':
                        x, y = point
                        self.assertTrue(656 <= x <= 930 and 480 <= y <= 539)
                        main.popup = False
                        main.settling = 2
                    else:
                        self.fail(f'加工期间出现预期之外的点击：{name}')

                def amounts():
                    if main.popup or main.settling or (main.settled and arrival is None):
                        return None
                    return main.current, main.preview

                main.device.click = click
                main.device.screenshot = screenshot
                session._goto_group = Mock(return_value=9)
                session._cards = Mock(return_value={5007: {'stock': 54, 'button': (213, 222, 321, 249)}})
                session._panel = Mock(return_value=True)
                session._amounts = amounts
                session._detect.side_effect = lambda area: (
                    [detection('是否确认进行加工?', 288, 123, score=0.9763)] if main.popup else [])
                with patch('module.base.timer.time', lambda: clock[0]):
                    if arrival == (21, 0):
                        self.assertTrue(session.run({2521: 21}, {}))
                    else:
                        with self.assertRaises(IslandFishExchangeError):
                            session.run({2521: 21}, {})
                names = [name for name, _ in clicks]
                self.assertEqual(names.count('FISH_EXCHANGE_CARD_5007'), 4)
                self.assertEqual(names.count('ALAS_EXCHANGE_CONFIRM'), 1)
                self.assertEqual(names.count('POPUP_CONFIRM_WHITE_ISLAND_FISH_EXCHANGE'), 1)
                session._goto_group.assert_called_once_with(2521)
                if arrival == (21, 0):
                    ledger.consume.assert_called_once_with({5007: 4})
                    self.assertEqual((ledger.get(5007), ledger.get(2521)), (50, 21))
                    main.device.save_screenshot.assert_not_called()
                else:
                    ledger.consume.assert_not_called()
                    self.assertEqual((ledger.get(5007), ledger.get(2521)), (54, 9))
                    main.device.save_screenshot.assert_called_once()

    def navigation_session(self, frames, initial=None):
        main = SimpleNamespace(
            device=SimpleNamespace(screenshot=Mock(), save_screenshot=Mock()),
            ui_goto=Mock(), ui_page_appear=Mock(return_value=False),
            appear_then_click=Mock(return_value=False), state=initial)

        def loop(**kwargs):
            for frame in frames:
                main.state = frame
                yield None

        main.loop = loop
        session = FishExchangeSession(main)
        session._panel = lambda item: main.state == item
        session._amounts = Mock(return_value=(0, 0))
        session._click_side_label = Mock(return_value=True)
        return main, session

    def test_open_processing_page_switches_groups_without_generic_navigation(self):
        main, session = self.navigation_session([2521, 2521, 2522], initial=2521)
        self.assertEqual(session._goto_group(2522), 0)
        main.ui_goto.assert_not_called()
        main.device.screenshot.assert_called_once()
        session._click_side_label.assert_called_once_with(('海水鱼', '海水魚'))

    def test_new_processing_visit_navigates_then_clears_selection(self):
        main, session = self.navigation_session([2522, 2521])
        self.assertEqual(session._goto_group(2521), 0)
        main.ui_goto.assert_called_once()
        self.assertFalse(main.ui_goto.call_args.kwargs['recover_unknown'])

    def test_unknown_amount_waits_without_reclicking_open_group(self):
        main, session = self.navigation_session([2521, 2522, 2522], initial=2521)
        session._amounts.return_value = None
        with self.assertRaisesRegex(IslandFishExchangeError, '数量无法确认'):
            session._goto_group(2522)
        session._click_side_label.assert_not_called()
        main.ui_goto.assert_not_called()

    def test_selection_must_be_cleared_before_returning_stock(self):
        _, session = self.navigation_session([2521, 2522, 2522], initial=2521)
        session._amounts.side_effect = [(25, 4), (25, 0)]
        self.assertEqual(session._goto_group(2522), 25)
        self.assertEqual(session._amounts.call_count, 2)

    def test_leave_processing_page_confirms_home_without_generic_navigation(self):
        from module.ui.page import page_island
        from module.ui_white.assets import BACK_ARROW_WHITE
        main, session = self.navigation_session([2522, 'home'])
        main.ui_page_appear.side_effect = lambda page: page == page_island and main.state == 'home'
        session.leave_shop()
        main.ui_goto.assert_not_called()
        self.assertIn(unittest.mock.call(BACK_ARROW_WHITE, offset=(20, 20), interval=2),
                      main.appear_then_click.call_args_list)

    def test_leave_unknown_page_does_not_click_unverified_back_button(self):
        from module.ui_white.assets import BACK_ARROW_WHITE
        main, session = self.navigation_session([None, None])
        with self.assertRaisesRegex(IslandFishExchangeError, '返回本岛'):
            session.leave_shop()
        self.assertNotIn(unittest.mock.call(BACK_ARROW_WHITE, offset=(20, 20), interval=2),
                         main.appear_then_click.call_args_list)

    def test_season_order_protection_survives_removal_of_old_auto_targets(self):
        config = ReadConfig({'IslandPlan.IslandProductionPlanner.HardFloorItems': 'catfish: 999',
                             'IslandPlan.IslandProductionPlanner.TaskTarget': 'catfish: 999'})
        with patch('module.island.fish_exchange.get_stuck_season_order_requirements', return_value={5002: 6}):
            protection = get_fish_protection(config)
        self.assertEqual(protection[5002], 6)
        self.assertEqual(plan_fish_exchange(2521, 4, {5002: 6}, protection), ({}, 0))
        self.assertEqual(plan_fish_exchange(2521, 4, {5002: 7}, protection), ({5002: 1}, 4))

    def test_exact_deficit_uses_fewest_excess_meat_not_select_all(self):
        selection, produced = plan_fish_exchange(2521, 5, {5002: 100, 5003: 100, 5004: 100})
        self.assertEqual(produced, 5)
        self.assertEqual(sum(selection.values()), 2)
        self.assertNotEqual(selection.get(5002), 100)

    def test_zero_demand_never_selects_fish(self):
        self.assertEqual(plan_fish_exchange(2522, 0, {5107: 100}), ({}, 0))

    def test_protected_fish_and_batch_bound(self):
        self.assertEqual(plan_fish_exchange(2521, 50, {5002: 100}, {5002: 98}), ({5002: 2}, 8))
        self.assertEqual(plan_fish_exchange(2522, 100, {5107: 10}), ({5107: 5}, 90))
        self.assertEqual(plan_fish_exchange(2522, 20, {5107: 4}, {5107: 4}), ({}, 0))

    def test_only_correct_group_can_be_consumed(self):
        self.assertEqual(plan_fish_exchange(2521, 50, {5107: 5, 5108: 5, 5005: 5}), ({}, 0))

    def test_indivisible_fish_overshoot_is_minimal(self):
        selection, produced = plan_fish_exchange(2522, 4, {5103: 1, 5104: 1})
        self.assertEqual((selection, produced), ({5104: 1}, 5))

    def test_invalid_values_do_not_become_zero(self):
        for stock in ({5002: -1}, {5002: True}, {5002: float('nan')}):
            with self.subTest(stock=stock), self.assertRaises(ValueError):
                plan_fish_exchange(2521, 3, stock)

    def test_amount_preview_requires_explicit_plus(self):
        self.assertEqual(parse_exchange_amounts([detection('25', 0, 0)]), (25, 0))
        self.assertEqual(parse_exchange_amounts([detection('25', 0, 0), detection('+4', 40, 0)]), (25, 4))
        self.assertEqual(parse_exchange_amounts([detection('25+4', 0, 0)]), (25, 4))
        self.assertIsNone(parse_exchange_amounts([]))
        self.assertIsNone(parse_exchange_amounts([detection('25', 0, 0), detection('4', 40, 0)]))
        self.assertIsNone(parse_exchange_amounts([detection('25', 0, 0, score=0.8)]))

    def test_cards_require_unambiguous_name_and_count(self):
        rows = [detection('鲶鱼', 50, 100, width=50), detection('100', 105, 65)]
        self.assertEqual(parse_exchange_cards(rows, 2521)[5002]['stock'], 100)
        self.assertEqual(parse_exchange_cards(rows + [detection('3', 108, 82)], 2521), {})
        self.assertEqual(parse_exchange_cards(rows + [detection('鲶鱼', 300, 100)], 2521), {})
        self.assertEqual(parse_exchange_cards(rows, 2522), {})
        self.assertEqual(parse_exchange_cards([detection('鲶', 50, 100), detection('100', 105, 65)], 2521), {})

    def test_shared_quantity_does_not_match_two_cards(self):
        rows = [detection('鲶鱼', 50, 100, width=50), detection('鲤鱼', 60, 100, width=50),
                detection('100', 105, 65)]
        self.assertEqual(parse_exchange_cards(rows, 2521), {})




    def session(self, main):
        session = FishExchangeSession(main)
        session._panel = lambda meat_id: not main.popup
        session._amounts = lambda: (main.current, main.preview)
        session._handle_exchange_popup = lambda: main.handle_popup_confirm('ISLAND_FISH_EXCHANGE')
        return session

    def test_one_click_one_preview_and_exact_arrival(self):
        main = ExchangeMain()
        session = self.session(main)
        result = session._exchange_batch(2521, 20, {5002: {'button': (10, 10, 20, 20)}}, {5002: 2}, 8)
        self.assertEqual(result, 28)
        self.assertEqual(main.clicks.count('FISH_EXCHANGE_CARD_5002'), 2)
        self.assertEqual(len(main.clicks), 3)
        main.device.save_screenshot.assert_not_called()

    def test_missed_selection_does_not_get_clicked_again(self):
        main = ExchangeMain(missed=True)
        session = self.session(main)
        with patch('module.island.fish_exchange.Timer') as timer:
            timer.return_value.reached.return_value = True
            timer.return_value.start.return_value = timer.return_value
            with self.assertRaises(GameStuckError):
                session._exchange_batch(2521, 20, {5002: {'button': (10, 10, 20, 20)}}, {5002: 1}, 4)
        self.assertEqual(main.clicks, ['FISH_EXCHANGE_CARD_5002'])
        main.device.save_screenshot.assert_called_once()

    def test_wrong_preview_stops_before_consumption(self):
        main = ExchangeMain(wrong_yield=True)
        session = self.session(main)
        with self.assertRaises(GameStuckError):
            session._exchange_batch(2521, 20, {5002: {'button': (10, 10, 20, 20)}}, {5002: 1}, 4)
        self.assertEqual(main.clicks, ['FISH_EXCHANGE_CARD_5002'])
        main.device.save_screenshot.assert_called_once()

    def test_unknown_amount_respects_action_timeout_without_selection(self):
        main = ExchangeMain()
        session = self.session(main)
        session._amounts = Mock(return_value=None)
        with patch('module.island.fish_exchange.Timer') as timer:
            timer.return_value.start.return_value = timer.return_value
            timer.return_value.reached.return_value = True
            with self.assertRaisesRegex(IslandFishExchangeError, '数量无法确认'):
                session._exchange_batch(2521, 20, {5002: {'button': (10, 10, 20, 20)}}, {5002: 1}, 4)
        self.assertEqual(main.clicks, [])

    def test_target_already_met_skips_cards(self):
        main = ExchangeMain()
        session = self.session(main)
        session._goto_group = Mock(return_value=20)
        session._cards = Mock()
        self.assertFalse(session.run({2521: 20}, {}))
        session._cards.assert_not_called()

    def test_unstable_card_inventory_stops_before_selection(self):
        main = ExchangeMain()
        main.device.screenshot = Mock()
        session = self.session(main)
        session._goto_group = Mock(return_value=20)
        session._cards = Mock(side_effect=[{5002: {'stock': 100}}, {5002: {'stock': 99}}])
        session._exchange_batch = Mock()
        with self.assertRaises(GameStuckError):
            session.run({2521: 24}, {})
        session._exchange_batch.assert_not_called()
        main.device.save_screenshot.assert_called_once()

    def test_stable_card_inventory_passes_only_target_deficit(self):
        main = ExchangeMain()
        main.device.screenshot = Mock()
        session = self.session(main)
        session._goto_group = Mock(return_value=20)
        cards = {5002: {'stock': 100, 'button': (10, 10, 20, 20)}}
        session._cards = Mock(return_value=cards)
        session._exchange_batch = Mock(return_value=24)
        self.assertTrue(session.run({2521: 24}, {5002: 98}))
        session._exchange_batch.assert_called_once_with(2521, 20, cards, {5002: 1}, 4)

    def test_raw_catalog_has_explicit_ids_and_no_fake_templates(self):
        entries = get_extra_raw_items('fishery')
        self.assertEqual(len(entries), 8)
        self.assertEqual({entry['item_id'] for entry in entries}, {5002, 5003, 5004, 5102, 5103, 5104, 5105, 5106})
        self.assertTrue(all(entry['shop_recipe_id'] and entry['template'] is None for entry in entries))
        self.assertEqual(get_extra_raw_items('nursery', 'autumn'), [])
        self.assertEqual({entry['item_id'] for entry in get_extra_raw_items('nursery', 'summer')}, {4033, 4035})
        self.assertEqual(get_extra_raw_items('mine')[0]['item_id'], 2700)
        self.assertEqual(get_extra_raw_items('forest')[0]['item_id'], 2800)

    def test_exchange_uses_real_local_task_ui_methods(self):
        from module.island.island_shop_base import IslandShopBase
        for name in ('loop', 'appear', 'appear_then_click', 'ui_goto', 'handle_popup_confirm'):
            self.assertTrue(callable(getattr(IslandShopBase, name)))


if __name__ == '__main__':
    unittest.main()
