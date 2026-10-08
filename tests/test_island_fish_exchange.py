"""鱼肉有限兑换的库存保护、OCR 歧义和提交闭环离线验证。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.exception import GameStuckError
from module.island.fish_exchange import (
    FishExchangeSession, get_fish_protection, parse_exchange_amounts,
    parse_exchange_cards, plan_fish_exchange,
)
from module.island.raw_catalog import get_extra_raw_items


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

    def test_protection_contains_hard_floor_and_direct_task_not_manual_min(self):
        config = ReadConfig({
            'IslandPlan.IslandProductionPlanner.HardFloorItems': 'catfish: 10',
            'IslandPlan.IslandProductionPlanner.TaskTarget': 'catfish: 7',
            'IslandRancher.IslandFishery.MinBass': 999,
        })
        protection = get_fish_protection(config)
        self.assertEqual(protection[5002], 17)
        self.assertEqual(protection[5007], 0)

    def test_nested_task_target_preserves_total_count_not_daily_rate(self):
        config = ReadConfig({
            'IslandPlan.IslandProductionPlanner.TaskTarget':
                'catfish: {count: 18, period: 6}\nkoi_carp: {count: 20, rate_per_day: 4}',
        })
        protection = get_fish_protection(config)
        self.assertEqual(protection[5002], 18)
        self.assertEqual(protection[5003], 20)

    def test_task_deadlines_and_season_request_add_to_fish_floor(self):
        config = ReadConfig({
            'IslandPlan.IslandProductionPlanner.HardFloorItems': 'catfish: 2',
            'IslandPlan.IslandProductionPlanner.TaskTarget':
                'catfish:\n  deadlines:\n    - {count: 3, period: 1}\n    - {count: 4, days: 5}',
        })
        with patch('module.island.fish_exchange.get_stuck_season_order_requirements',
                   return_value={5002: 6}):
            protection = get_fish_protection(config)
        self.assertEqual(protection[5002], 15)
        self.assertEqual(plan_fish_exchange(2521, 4, {5002: 15}, protection), ({}, 0))
        self.assertEqual(plan_fish_exchange(2521, 4, {5002: 16}, protection), ({5002: 1}, 4))

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
