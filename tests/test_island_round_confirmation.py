"""共享库存的派遣确认只扣实际耗材，结果不明不重复点击。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from module.exception import GameStuckError
from module.island.island import (
    Island, ISLAND_POSTMANAGE_CHECK, ISLAND_SELECT_PRODUCT_CHECK, POST_ADD_ORDER, POST_MAX,
)
from module.island.round_inventory import IslandRoundInventory


class RoundConfirmationTests(unittest.TestCase):
    def make_main(self, return_to_management=True):
        main = Island.__new__(Island)
        main.island_round = IslandRoundInventory({1000: 99})
        main._round_material_receipt = {'item_id': 1000}
        main._read_round_dispatch_amounts = Mock(return_value=(4, 99, 16))
        main.is_post_detail_visible = Mock(return_value=False)
        state = {'clicked': False}

        def appear(button, **kwargs):
            if button is ISLAND_SELECT_PRODUCT_CHECK:
                return not state['clicked'] or not return_to_management
            if button is ISLAND_POSTMANAGE_CHECK:
                return state['clicked'] and return_to_management
            return button is POST_ADD_ORDER

        def click(button):
            if button is POST_ADD_ORDER:
                state['clicked'] = True

        main.appear = Mock(side_effect=appear)
        main.appear_then_click = Mock(side_effect=lambda button, **kw: button is POST_MAX)
        main.device = SimpleNamespace(click=Mock(side_effect=click))
        main.loop = lambda **kw: iter(range(8))
        return main

    def test_actual_total_is_deducted_once_after_positive_return(self):
        main = self.make_main()
        self.assertTrue(main.confirm_post_add_order('小麦'))
        self.assertEqual(main.island_round.get(1000), 83)
        self.assertEqual(main._round_dispatch_batches, 4)
        self.assertEqual(main._round_dispatch_materials, {1000: 16})
        main.device.click.assert_called_once_with(POST_ADD_ORDER)
        main.appear_then_click.assert_called_once_with(POST_MAX, interval=2)

    def test_requested_small_mining_quantity_is_not_replaced_with_maximum(self):
        main = self.make_main()
        main._round_material_receipt = None
        main._read_round_dispatch_amounts.return_value = (1, None, None)
        self.assertTrue(main.confirm_post_add_order('铜矿', preserve_quantity=True))
        main.appear_then_click.assert_not_called()
        main.device.click.assert_called_once_with(POST_ADD_ORDER)
        self.assertEqual(main._round_dispatch_batches, 1)
        self.assertEqual(main._round_dispatch_materials, {})

    def test_purchase_survives_dispatch_failure(self):
        main = self.make_main()
        main.island_round.observe(1000, 104)
        main._read_round_dispatch_amounts.return_value = None
        self.assertFalse(main.confirm_post_add_order('小麦'))
        self.assertEqual(main.island_round.get(1000), 104)
        main.device.click.assert_not_called()

    def test_uncertain_dispatch_never_repeats_or_commits_estimated_debit(self):
        main = self.make_main(return_to_management=False)
        with self.assertRaises(GameStuckError):
            main.confirm_post_add_order('小麦')
        self.assertEqual(main.island_round.get(1000), 99)
        main.device.click.assert_called_once_with(POST_ADD_ORDER)

    def test_consumable_zero_does_not_submit(self):
        main = self.make_main()
        main._read_round_dispatch_amounts.return_value = (4, 0, 16)
        self.assertFalse(main.confirm_post_add_order('小麦'))
        main.device.click.assert_not_called()

    def test_mining_without_materials_records_batches_only(self):
        main = self.make_main()
        main._round_material_receipt = None
        main._read_round_dispatch_amounts.return_value = (12, None, None)
        self.assertTrue(main.confirm_post_add_order('铜矿'))
        self.assertEqual(main._round_dispatch_batches, 12)
        self.assertEqual(main._round_dispatch_materials, {})


if __name__ == '__main__':
    unittest.main()
