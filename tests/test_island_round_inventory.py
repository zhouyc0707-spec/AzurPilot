"""单轮共用库存的采购、加工、派遣与未知值语义。"""

import unittest

from module.island.round_inventory import IslandRoundInventory


class IslandRoundInventoryTests(unittest.TestCase):
    def test_local_keys_and_item_ids_share_one_balance(self):
        stock = IslandRoundInventory({'wheat': 100})
        stock.consume({2000: 30})
        self.assertEqual(stock.get('wheat'), 70)
        stock.observe('小麦', 60)
        self.assertEqual(stock.get(2000), 60)

    def test_unknown_and_confirmed_zero_remain_distinct(self):
        stock = IslandRoundInventory({'wheat': 0})
        self.assertEqual(stock.get('wheat'), 0)
        self.assertIsNone(stock.get('corn'))
        stock.consume({'corn': 30})
        stock.add({'corn': 10})
        self.assertIsNone(stock.get('corn'))
        stock.observe('corn', 0)
        stock.add({'corn': 10})
        self.assertEqual(stock.get('corn'), 10)

    def test_mill_consumption_changes_later_food_materials(self):
        stock = IslandRoundInventory({'wheat': 100, 'chicken_feed': 0, 'wheat_flour': 0})
        stock.consume({'wheat': 30})
        stock.add({'chicken_feed': 10})
        stock.consume({'wheat': 60})
        stock.add({'wheat_flour': 10})
        self.assertEqual(stock.get('wheat'), 10)
        self.assertEqual(stock.get('chicken_feed'), 10)
        self.assertEqual(stock.get('wheat_flour'), 10)

    def test_successful_purchase_is_kept_without_dispatch(self):
        stock = IslandRoundInventory({1: 1000, 1101: 0})
        stock.consume({1: 500})
        stock.observe(1101, 5)
        stock.block('shell')
        self.assertEqual(stock.get(1), 500)
        self.assertEqual(stock.get(1101), 5)
        self.assertEqual(stock.in_production, {})

    def test_in_production_does_not_increase_available_materials(self):
        stock = IslandRoundInventory({'tofu': 0, 'soybean': 12})
        stock.consume({'soybean': 12})
        stock.production_started('tofu', 6)
        self.assertEqual(stock.in_production, {3011: 6})
        self.assertEqual(stock.get('tofu'), 0)

    def test_inconsistent_consumption_invalidates_only_affected_balance(self):
        stock = IslandRoundInventory({'wheat': 2, 'corn': 20})
        stock.consume({'wheat': 3, 'corn': 5})
        self.assertIsNone(stock.get('wheat'))
        self.assertEqual(stock.get('corn'), 15)
        stock.observe('wheat', 7)
        self.assertEqual(stock.get('wheat'), 7)

    def test_failed_product_cannot_escape_block_by_using_alias(self):
        stock = IslandRoundInventory()
        stock.block('tofu')
        self.assertTrue(stock.is_blocked(3011))
        self.assertTrue(stock.is_blocked('豆腐'))
        self.assertFalse(stock.is_blocked('iced_coffee'))

    def test_next_round_discards_previous_stocks_and_failures(self):
        old = IslandRoundInventory({'tofu': 4})
        old.block('tofu')
        old.production_started('tofu', 6)
        new = IslandRoundInventory()
        self.assertIsNone(new.get('tofu'))
        self.assertEqual(new.in_production, {})
        self.assertFalse(new.is_blocked('tofu'))

    def test_invalid_changes_do_not_partially_apply(self):
        stock = IslandRoundInventory({'wheat': 10, 'corn': 10})
        for invalid in (-1, 1.5, True, '2'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                stock.consume({'wheat': 1, 'corn': invalid})
            self.assertEqual(stock.get('wheat'), 10)
        with self.assertRaises(ValueError):
            stock.add({'wheat': 1, 'unknown-item': 1})
        self.assertEqual(stock.get('wheat'), 10)


if __name__ == '__main__':
    unittest.main()
