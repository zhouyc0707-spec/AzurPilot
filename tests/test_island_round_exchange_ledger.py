"""鱼肉兑换只在确认到账后同步共享现货，失败保留确认前账目。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from module.exception import GameStuckError
from module.island.data import DIC_ISLAND_EXCHANGE_RECIPE
from module.island.fish_exchange import FishExchangeSession
from module.island.round_inventory import IslandRoundInventory


class RoundFishExchangeLedgerTests(unittest.TestCase):
    def session(self):
        recipe = next(data for data in DIC_ISLAND_EXCHANGE_RECIPE.values()
                      if 5007 in data['resource_consume'])
        meat, output = next(iter(recipe['items'].items()))
        ledger = IslandRoundInventory({5007: 3, meat: 0})
        session = FishExchangeSession.__new__(FishExchangeSession)
        session.main = SimpleNamespace(island_round=ledger, device=Mock(), config=None)
        session._goto_group = Mock(return_value=0)
        session._cards = Mock(return_value={5007: {'stock': 3, 'button': (1, 2, 3, 4)}})
        session._panel = Mock(return_value=True)
        session._amounts = Mock(return_value=(0, 0))
        session._exchange_batch = Mock(return_value=output)
        return session, ledger, meat, output

    def test_success_consumes_selected_fish_and_observes_meat(self):
        session, ledger, meat, output = self.session()
        self.assertTrue(session.run({meat: output}, {}))
        self.assertEqual(ledger.get(5007), 2)
        self.assertEqual(ledger.get(meat), output)
        session._exchange_batch.assert_called_once()
        self.assertEqual(session._exchange_batch.call_args.args[3], {5007: 1})

    def test_uncertain_result_raises_without_committing_guessed_consumption(self):
        session, ledger, meat, output = self.session()
        session._exchange_batch.side_effect = GameStuckError('到账未确认')
        with self.assertRaises(GameStuckError):
            session.run({meat: output}, {})
        self.assertEqual(ledger.get(5007), 3)
        self.assertEqual(ledger.get(meat), 0)
        self.assertEqual(ledger.in_production, {})


if __name__ == '__main__':
    unittest.main()
