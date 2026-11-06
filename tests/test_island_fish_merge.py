"""上游鱼肉入口与本地库存保护的融合回归，全部使用离线截图或模拟设备。"""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import module.config.server as server
from module.island import fish_exchange as local
from module.island import island_fish_meat as fm
from module.island import island_fishery as fishery
from tests.test_island_fish_meat import image


class FishEntryTest(unittest.TestCase):
    def setUp(self):
        self.main = object.__new__(fishery.IslandFishery)
        self.main.config = SimpleNamespace(IslandFishery_MinFreshwaterFishMeat=30,
                                           IslandFishery_MinSaltwaterFishMeat=20)
        self.main.run_fish_meat_exchange = Mock()
        self.main.ui_ensure = Mock()
        self.main.fishery_threshold = {'koi_carp': 30, 'yellowfin_tuna': 5}


    def test_cn_tw_manual_targets_reuse_strict_executor_and_protection(self):
        for language in ('cn', 'tw'):
            with self.subTest(server=language), patch.object(server, 'server', language), \
                    patch.object(local, 'get_fish_protection', return_value={5003: 20}) as protection, \
                    patch.object(local, 'FishExchangeSession') as session:
                self.main.run_fish_meat_targets()
                protection.assert_called_once_with(self.main.config)
                expected = {fish_id: 0 for fish_id in fishery.FISH_MEAT_FISH_IDS.values()}
                expected.update({5003: 30, 5107: 5})
                session.return_value.run.assert_called_once_with({2521: 30, 2522: 20}, expected)
                self.main.run_fish_meat_exchange.assert_not_called()

    def test_en_jp_manual_targets_forward_all_protected_stock(self):
        for language in ('en', 'jp'):
            self.main.run_fish_meat_exchange.reset_mock()
            with self.subTest(server=language), patch.object(server, 'server', language), \
                    patch.object(local, 'get_fish_protection', return_value={5003: 20, 5107: 8}):
                self.main.run_fish_meat_targets()
            expected = {fish_id: 0 for fish_id in fishery.FISH_MEAT_FISH_IDS.values()}
            expected.update({5003: 30, 5107: 8})
            self.main.run_fish_meat_exchange.assert_called_once_with({2521: 30, 2522: 20}, keep=expected)

    def test_default_zero_targets_never_open_exchange(self):
        self.main.config.IslandFishery_MinFreshwaterFishMeat = 0
        self.main.config.IslandFishery_MinSaltwaterFishMeat = 0
        with patch.object(local, 'FishExchangeSession') as session:
            self.main.run_fish_meat_targets()
        session.assert_not_called()
        self.main.run_fish_meat_exchange.assert_not_called()


class StrictRecognitionTest(unittest.TestCase):
    def test_unknown_number_remains_unknown_but_literal_zero_is_valid(self):
        reader = fm.FishQuantityDigit([], name='FISH_TEST')
        self.assertIsNone(reader.after_process(''))
        self.assertEqual(reader.after_process('0'), 0)
        self.assertEqual(reader.after_process('26'), 26)

    def test_only_complete_fish_names_allow_selection(self):
        with patch.object(server, 'server', 'en'):
            self.assertEqual(fm.fish_name_to_id('Koi Carp', 2521), 5003)
            self.assertEqual(fm.fish_name_to_id('koicarp', 2521), 5003)
            for text in ('', 'garbled', 'Carp', 'Koi Carx'):
                self.assertIsNone(fm.fish_name_to_id(text, 2521), text)

    def test_selected_card_quantity_from_en_fixture(self):
        with patch.object(server, 'server', 'en'):
            self.assertEqual(fm.read_fish_counts(image('exchange_page'), [0]), {0: 0})
            self.assertEqual(fm.read_fish_counts(image('exchange_selected'), [0]), {0: 1})

    def test_unknown_stock_and_duplicate_identity_never_become_a_plan(self):
        exchange = fm.IslandFishMeatExchange()
        for cards in ([(0, 5003, None)], [(0, None, 26)], [(0, 5003, 26), (1, 5003, 26)]):
            exchange.loop = lambda **kwargs: iter([None, None])
            with self.subTest(cards=cards), patch.object(fm, 'read_fish_cards', return_value=cards):
                self.assertIsNone(exchange._read_fish_cards_stable(2521))

    def test_unknown_owned_quantity_cannot_be_cleared_as_zero(self):
        exchange = fm.IslandFishMeatExchange()
        exchange.device = Mock()
        exchange.loop = lambda **kwargs: iter([None, None])
        with patch.object(fm, 'read_fish_meat', return_value=(None, 0)):
            self.assertIsNone(exchange._clear_fish_selection())
        exchange.device.click.assert_not_called()

    def test_receipt_without_confirmed_popup_does_not_count(self):
        exchange = fm.IslandFishMeatExchange()
        exchange.loop = lambda **kwargs: iter([None, None])
        exchange.appear_then_click = Mock(return_value=False)
        with patch.object(fm, 'exchange_popup_appear', return_value=False), \
                patch.object(fm, 'reward_popup_appear', return_value=False), \
                patch.object(fm, 'read_fish_meat', return_value=(9, 0)):
            self.assertIsNone(exchange._confirm_exchange(6, 3))

    def test_same_total_yield_on_wrong_card_never_submits(self):
        exchange = fm.IslandFishMeatExchange()
        exchange._goto_fish_meat_page = Mock(return_value=True)
        exchange._clear_fish_selection = Mock(return_value=0)
        exchange._read_fish_cards_stable = Mock(return_value=[(0, 5003, 26), (1, 5004, 26)])
        exchange._set_fish_count = Mock(return_value=True)
        exchange._confirm_exchange = Mock()
        exchange.loop = lambda **kwargs: iter([None, None])
        with patch.object(fm, 'read_fish_meat', return_value=(0, 3)), \
                patch.object(fm, 'read_fish_counts', return_value={0: 0, 1: 1}):
            self.assertIsNone(exchange.exchange_fish_meat(2521, 3))
        exchange._confirm_exchange.assert_not_called()

    def test_unconfirmed_first_exchange_stops_second_currency(self):
        exchange = fm.IslandFishMeatExchange()
        exchange.exchange_fish_meat = Mock(return_value=None)
        exchange._leave_fish_meat_page = Mock()
        exchange.run_fish_meat_exchange({2521: 30, 2522: 20}, {5003: 20})
        exchange.exchange_fish_meat.assert_called_once_with(2521, 30, {5003: 20})
        exchange._leave_fish_meat_page.assert_called_once()


if __name__ == '__main__':
    unittest.main()
