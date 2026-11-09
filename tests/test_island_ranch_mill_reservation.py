"""磨坊先为启用牧场预留小麦，再按剩余现货加工面粉。"""

import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from module.exception import GameStuckError
from module.island.island import Island
from module.island.island_rancher import IslandRancher
from module.island.round_inventory import IslandRoundInventory


class RanchMillReservationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.island.island_rancher.logger'))

    def make_task(self, wheat=1136, **stocks):
        config = SimpleNamespace(IslandRancher_MinChicken=100, IslandRancher_MinPork=100,
                                 IslandRancher_Milk=False, IslandRancher_Wool=False)

        def init(task, config):
            task.config, task.device = config, Mock()

        with patch.object(Island, '__init__', init):
            task = IslandRancher(config)
        inventory = {'wheat': wheat, 'corn': 600, 'pasture': 600,
                     'wheat_flour': 0, 'chicken_feed': 0, 'pig_feed': 50,
                     'cattle_feed': 50, 'sheep_feed': 50}
        inventory.update(stocks)
        task.island_round = IslandRoundInventory(inventory)
        task._load_round_inventory()
        for name in ('goto_postmanage', 'post_manage_mode', 'post_close'):
            setattr(task, name, Mock())
        task.goto_mill_from_any_ranch_post = Mock(return_value=True)
        task.back_to_postmanage_after_mill_purchase = Mock(return_value=True)
        task.warehouse_filter = Mock(side_effect=AssertionError('不得额外进入仓库'))

        def purchase(item, quantity):
            task._round_mill_batches = quantity
            return True

        task.process_mill_item = Mock(side_effect=purchase)
        return task

    def test_logged_wheat_balances_leave_feed_before_processing_flour(self):
        for wheat, flour, feed_batches in ((1136, 134, 11), (326, 4, 5)):
            with self.subTest(wheat=wheat):
                task = self.make_task(wheat)
                config_before = deepcopy(vars(task.config))
                self.assertEqual(task.process_mill_supplements(), ['wheat_flour', 'chicken_feed'])
                self.assertEqual(task.process_mill_item.call_args_list, [
                    call('wheat_flour', quantity=flour), call('chicken_feed', quantity=feed_batches)])
                self.assertEqual(task.island_round.get('wheat'), wheat - flour * 6 - feed_batches * 30)
                self.assertEqual(task.island_round.get('wheat_flour'), flour)
                self.assertEqual(task.island_round.get('chicken_feed'), feed_batches * 10)
                self.assertEqual(vars(task.config), config_before)
                task.warehouse_filter.assert_not_called()

    def test_standalone_entry_processes_reserved_feed_with_both_logged_balances(self):
        for wheat, flour, feed_batches in ((1136, 134, 11), (326, 4, 5)):
            with self.subTest(wheat=wheat):
                task = self.make_task(wheat)
                del task.island_round
                config_before = deepcopy(vars(task.config))
                self.assertEqual(task.process_mill_supplements(), ['wheat_flour', 'chicken_feed'])
                self.assertEqual(task.process_mill_item.call_args_list, [
                    call('wheat_flour', quantity=flour), call('chicken_feed', quantity=feed_batches)])
                self.assertEqual(task.inventory_counts['farm']['wheat'], wheat - flour * 6 - feed_batches * 30)
                self.assertEqual(task.inventory_counts['mill']['wheat_flour'], flour)
                self.assertEqual(task.inventory_counts['mill']['chicken_feed'], feed_batches * 10)
                self.assertEqual(vars(task.config), config_before)
                task.warehouse_filter.assert_not_called()

    def test_standalone_feed_can_use_partial_batches_when_flour_is_sufficient(self):
        task = self.make_task(326, wheat_flour=150)
        del task.island_round
        self.assertEqual(task.process_mill_supplements(), ['chicken_feed'])
        task.process_mill_item.assert_called_once_with('chicken_feed', quantity=5)
        self.assertEqual(task.inventory_counts['farm']['wheat'], 176)
        self.assertEqual(task.inventory_counts['mill']['wheat_flour'], 150)
        self.assertEqual(task.inventory_counts['mill']['chicken_feed'], 50)

    def test_standalone_unknown_and_zero_material_never_buy_or_change_observation(self):
        for wheat in (None, 0):
            with self.subTest(wheat=wheat):
                task = self.make_task()
                del task.island_round
                task.inventory_counts['farm']['wheat'] = wheat
                self.assertEqual(task.process_mill_supplements(), [])
                task.process_mill_item.assert_not_called()
                self.assertEqual(task.inventory_counts['farm']['wheat'], wheat)
                self.assertEqual(task.inventory_counts['mill']['chicken_feed'], 0)

    def test_standalone_unknown_enabled_feed_keeps_wheat_for_later_confirmation(self):
        task = self.make_task()
        del task.island_round
        task.inventory_counts['mill']['chicken_feed'] = None
        self.assertEqual(task.process_mill_supplements(), [])
        task.process_mill_item.assert_not_called()
        self.assertEqual(task.inventory_counts['farm']['wheat'], 1136)
        self.assertIsNone(task.inventory_counts['mill']['chicken_feed'])

    def test_reservation_uses_only_affordable_whole_feed_batches(self):
        for wheat, flour, feed in ((0, 0, 0), (29, 4, 0), (30, 0, 10),
                                  (31, 0, 10), (59, 4, 10), (60, 0, 20)):
            with self.subTest(wheat=wheat):
                task = self.make_task(wheat)
                task.process_mill_supplements()
                self.assertEqual(task.island_round.get('wheat_flour'), flour)
                self.assertEqual(task.island_round.get('chicken_feed'), feed)
                self.assertEqual(task.island_round.get('wheat'), wheat - flour * 6 - feed * 3)
                self.assertGreaterEqual(task.island_round.get('wheat'), 0)

    def test_sufficient_feed_does_not_reserve_wheat(self):
        task = self.make_task(chicken_feed=50)
        self.assertEqual(task.process_mill_supplements(), ['wheat_flour'])
        task.process_mill_item.assert_called_once_with('wheat_flour', quantity=189)
        self.assertEqual(task.island_round.get('wheat'), 2)

    def test_disabled_meat_targets_do_not_request_feed_or_reserve_wheat(self):
        task = self.make_task(pig_feed=0)
        task.ranch_chicken_threshold = task.config.IslandRancher_MinChicken = 0
        task.ranch_pork_threshold = task.config.IslandRancher_MinPork = 0
        self.assertEqual(task.process_mill_supplements(), ['wheat_flour'])
        task.process_mill_item.assert_called_once_with('wheat_flour', quantity=189)
        self.assertEqual(task.island_round.get('chicken_feed'), 0)
        self.assertEqual(task.island_round.get('pig_feed'), 0)

    def test_disabled_milk_and_wool_never_request_feed(self):
        task = self.make_task(chicken_feed=50, cattle_feed=0, sheep_feed=0)
        self.assertEqual(task.process_mill_supplements(), ['wheat_flour'])
        self.assertEqual(task.island_round.get('pasture'), 600)

    def test_other_feed_materials_do_not_reserve_wheat(self):
        task = self.make_task(chicken_feed=50, pig_feed=0)
        self.assertEqual(task.process_mill_supplements(), ['wheat_flour', 'pig_feed'])
        self.assertEqual(task.island_round.get('wheat_flour'), 189)
        self.assertEqual(task.island_round.get('pig_feed'), 110)
        self.assertEqual(task.island_round.get('corn'), 270)

    def test_unknown_wheat_never_allocates_flour_or_buys_feed(self):
        task = self.make_task(wheat=None)
        self.assertNotIn('wheat_flour', dict(task.check_mill_supplement_needs()))
        self.assertEqual(task.process_mill_supplements(), [])
        task.process_mill_item.assert_not_called()
        self.assertIsNone(task.island_round.get('wheat'))

    def test_unknown_enabled_chicken_feed_does_not_release_wheat_to_flour(self):
        task = self.make_task(chicken_feed=None)
        self.assertEqual(task.check_mill_supplement_needs(), [])
        self.assertEqual(task.process_mill_supplements(), [])
        task.process_mill_item.assert_not_called()
        self.assertEqual(task.island_round.get('wheat'), 1136)
        self.assertIsNone(task.island_round.get('chicken_feed'))

    def test_disabled_unknown_feed_does_not_hold_flour(self):
        task = self.make_task(chicken_feed=None)
        task.ranch_chicken_threshold = task.config.IslandRancher_MinChicken = 0
        self.assertEqual(task.process_mill_supplements(), ['wheat_flour'])
        self.assertEqual(task.island_round.get('wheat_flour'), 189)

    def test_current_ledger_overrides_stale_local_feed_and_flour_counts(self):
        task = self.make_task(chicken_feed=0)
        task.island_round.observe('chicken_feed', 50)
        task.island_round.observe('wheat_flour', 200)
        self.assertEqual(task.check_mill_supplement_needs(), [])
        self.assertEqual(task.island_round.get('wheat'), 1136)

    def test_existing_minimum_feed_target_is_preserved_below_threshold(self):
        task = self.make_task(chicken_feed=49)
        self.assertEqual(task.check_mill_supplement_needs(), [
            ('wheat_flour', 134), ('chicken_feed', 11)])

    def test_confirmed_partial_flour_purchase_only_consumes_actual_amount(self):
        task = self.make_task()

        def purchase(item, quantity):
            task._round_mill_batches = 3 if item == 'wheat_flour' else quantity
            return True

        task.process_mill_item.side_effect = purchase
        self.assertEqual(task.process_mill_supplements(), ['wheat_flour', 'chicken_feed'])
        self.assertEqual(task.island_round.get('wheat'), 788)
        self.assertEqual(task.island_round.get('wheat_flour'), 3)
        self.assertEqual(task.island_round.get('chicken_feed'), 110)

    def test_unconfirmed_feed_purchase_preserves_confirmed_flour_receipt(self):
        task = self.make_task()

        def purchase(item, quantity):
            task._round_mill_batches = quantity
            return item == 'wheat_flour'

        task.process_mill_item.side_effect = purchase
        with self.assertRaises(GameStuckError):
            task.process_mill_supplements()
        self.assertEqual(task.island_round.get('wheat'), 332)
        self.assertEqual(task.island_round.get('wheat_flour'), 134)
        self.assertEqual(task.island_round.get('chicken_feed'), 0)


if __name__ == '__main__':
    unittest.main()
