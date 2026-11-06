"""换季后仓库缺卡，通过选品页明确零库存恢复需求。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.exception import GameStuckError
from module.island.round_inventory import IslandRoundInventory
from module.island.round_recipe import observe_selected_recipe, probe_round_missing_stocks, recipe_for_product


class RoundRecipeTests(unittest.TestCase):
    def test_recipe_lookup_accepts_id_and_local_name(self):
        self.assertEqual(recipe_for_product('wheat'), recipe_for_product(2000))

    def test_unknown_or_non_producible_item_does_not_guess_recipe(self):
        for item in ('不存在的产品', 1):
            with self.subTest(item=item), self.assertRaises(ValueError):
                recipe_for_product(item)

    @patch('module.island.manufacture_selector.read_selected_recipe_inventory')
    def test_empty_season_stock_can_be_confirmed_zero(self, read):
        ledger = IslandRoundInventory()
        main = SimpleNamespace(island_round=ledger, device=Mock())
        read.return_value = {2000: {'stock': 0, 'cost': 0}, 1000: {'stock': 0, 'cost': 1}}
        self.assertTrue(observe_selected_recipe(main, 'wheat'))
        self.assertEqual(ledger.get('wheat'), 0)
        self.assertEqual(ledger.get(1000), 0)
        self.assertFalse(ledger.is_blocked('wheat'))
        main.device.click.assert_not_called()

    @patch('module.island.manufacture_selector.read_selected_recipe_inventory')
    def test_actual_empty_food_material_updates_later_tasks(self, read):
        ledger = IslandRoundInventory({'coffee_bean': 20})
        main = SimpleNamespace(island_round=ledger)
        read.return_value = {3005: {'stock': 0, 'cost': 0}, 2009: {'stock': 0, 'cost': 2}}
        self.assertTrue(observe_selected_recipe(main, 'iced_coffee'))
        self.assertEqual(ledger.get('iced_coffee'), 0)
        self.assertEqual(ledger.get('coffee_bean'), 0)

    @patch('module.island.manufacture_selector.read_selected_recipe_inventory', return_value=None)
    def test_unknown_page_does_not_fabricate_or_erase_stocks(self, read):
        ledger = IslandRoundInventory({'wheat': 12})
        main = SimpleNamespace(island_round=ledger)
        self.assertFalse(observe_selected_recipe(main, 'wheat'))
        self.assertEqual(ledger.get('wheat'), 12)
        self.assertIsNone(ledger.get(1000))

    @patch('module.island.manufacture_selector.read_selected_recipe_inventory')
    def test_non_round_call_performs_no_ocr(self, read):
        self.assertFalse(observe_selected_recipe(SimpleNamespace(), 'wheat'))
        read.assert_not_called()


def probe_runner(ledger, **attributes):
    return SimpleNamespace(island_round=ledger, goto_postmanage=Mock(), post_manage_mode=Mock(),
                           post_close=Mock(), post_open=Mock(return_value=True),
                           select_character=Mock(return_value=True), confirm_selected_character=Mock(return_value=True),
                           back_to_postmanage_from_dispatch=Mock(return_value=True), confirm_post_add_order=Mock(),
                           confirm_food_dispatch=Mock(), **attributes)


class MillUnknownRecoveryTests(unittest.TestCase):
    def test_empty_season_reads_mill_inputs_and_products_without_dispatch(self):
        ledger = IslandRoundInventory()
        farm = probe_runner(ledger, _round_idle_posts={'farm': [{'button': 'farm1'}]},
                            worker_filters={'farm': ['WorkerJuu']})
        ranch = probe_runner(
            ledger, _round_ranch_states={f'ISLAND_RANCH_POST{i}': 'idle' for i in range(1, 5)},
            posts_ranch={f'ISLAND_RANCH_POST{i}': f'ranch{i}' for i in range(1, 5)},
            config=SimpleNamespace(IslandRancher_ChickenFilter=['WorkerJuu'], IslandRancher_PigFilter=['WorkerJuu'],
                                   IslandRancher_RancherFilter=['WorkerJuu'], IslandRancher_WoolWorkerFilter=['WorkerJuu']))
        coffee = probe_runner(ledger, posts={'coffee1': {'status': 'idle', 'button': 'coffee1'}},
                              name_to_config={'apple_pie': {}}, chef_config=['WorkerJuu'])

        def stock_for_recipe(main, recipe_id):
            from module.island.data import DIC_ISLAND_RECIPE
            recipe = DIC_ISLAND_RECIPE[recipe_id]
            return {item: {'stock': 0, 'cost': recipe['commission_cost'].get(item, 0)}
                    for item in set(recipe['commission_product']) | set(recipe['commission_cost'])}

        with patch('module.island.planned_dispatch.PlannedProductionMixin._planned_dispatch_stage',
                   side_effect=['characters', 'products'] * 8), \
                patch('module.island.manufacture_selector.select_manufacture_recipe', return_value=True), \
                patch('module.island.manufacture_selector.read_selected_recipe_inventory', side_effect=stock_for_recipe):
            missing = probe_round_missing_stocks([farm, ranch, coffee], ledger,
                                                (2000, 2001, 2008, 3000, 3001, 3002, 3003, 3004))
        self.assertEqual(missing, set())
        self.assertEqual(ledger.get('wheat_flour'), 0)
        self.assertEqual(ledger.get('chicken_feed'), 0)
        self.assertEqual(ledger.get('wheat'), 0)
        self.assertEqual(ledger.in_production, {})
        for runner in (farm, ranch, coffee):
            runner.confirm_post_add_order.assert_not_called()
            runner.confirm_food_dispatch.assert_not_called()
        self.assertEqual(ranch.back_to_postmanage_from_dispatch.call_count, 4)

    def test_known_stock_does_not_open_any_extra_page(self):
        ledger = IslandRoundInventory({'wheat_flour': 20})
        runner = probe_runner(ledger, posts={'coffee1': {'status': 'idle', 'button': 'coffee1'}},
                              name_to_config={'apple_pie': {}}, chef_config=['WorkerJuu'])
        self.assertEqual(probe_round_missing_stocks([runner], ledger, (3004,)), set())
        runner.post_open.assert_not_called()

    def test_ranch_secondary_chicken_is_observed_from_food_ingredient(self):
        ledger = IslandRoundInventory()
        runner = probe_runner(ledger, posts={'grill1': {'status': 'idle', 'button': 'grill1'}},
                              name_to_config={'chicken_potato': {}}, chef_config=['WorkerJuu'])
        inventory = {3030: {'stock': 0, 'cost': 0}, 2602: {'stock': 0, 'cost': 2},
                     2005: {'stock': 10, 'cost': 1}}
        with patch('module.island.planned_dispatch.PlannedProductionMixin._planned_dispatch_stage',
                   return_value='products'), \
                patch('module.island.manufacture_selector.select_manufacture_recipe', return_value=True), \
                patch('module.island.manufacture_selector.read_selected_recipe_inventory', return_value=inventory):
            self.assertEqual(probe_round_missing_stocks([runner], ledger, (2602,)), set())
        self.assertEqual(ledger.get('chicken'), 0)
        runner.confirm_food_dispatch.assert_not_called()

    def test_known_feed_still_allows_unknown_pork_target_probe(self):
        ledger = IslandRoundInventory({'pig_feed': 50})
        runner = probe_runner(ledger, _round_ranch_states={'ISLAND_RANCH_POST2': 'idle'},
                              posts_ranch={'ISLAND_RANCH_POST2': 'ranch2'},
                              config=SimpleNamespace(IslandRancher_PigFilter=['WorkerJuu']))
        inventory = {2600: {'stock': 0, 'cost': 0}, 3001: {'stock': 50, 'cost': 2}}
        with patch('module.island.planned_dispatch.PlannedProductionMixin._planned_dispatch_stage',
                   return_value='products'), \
                patch('module.island.manufacture_selector.select_manufacture_recipe', return_value=True), \
                patch('module.island.manufacture_selector.read_selected_recipe_inventory', return_value=inventory):
            self.assertEqual(probe_round_missing_stocks([runner], ledger, (2600,)), set())
        self.assertEqual(ledger.get('pork'), 0)
        self.assertEqual(ledger.get('pig_feed'), 50)

    def test_busy_and_disabled_posts_are_not_borrowed_for_probe(self):
        ledger = IslandRoundInventory()
        runner = probe_runner(ledger,
                              posts={'busy': {'status': 'working', 'button': 'busy'},
                                     'off': {'status': 'idle', 'button': 'off', 'round_enabled': False}},
                              name_to_config={'apple_pie': {}}, chef_config=['WorkerJuu'])
        self.assertEqual(probe_round_missing_stocks([runner], ledger, (3004,)), {3004})
        self.assertIsNone(ledger.get(3004))
        runner.post_open.assert_not_called()

    def test_no_available_worker_keeps_unknown_and_returns_from_selection(self):
        ledger = IslandRoundInventory()
        runner = probe_runner(ledger, posts={'coffee1': {'status': 'idle', 'button': 'coffee1'}},
                              name_to_config={'apple_pie': {}}, chef_config=['WorkerJuu'])
        runner.select_character.return_value = False
        with patch('module.island.planned_dispatch.PlannedProductionMixin._planned_dispatch_stage',
                   return_value='characters'):
            self.assertEqual(probe_round_missing_stocks([runner], ledger, (3004,)), {3004})
        runner.back_to_postmanage_from_dispatch.assert_called_once()
        runner.confirm_selected_character.assert_not_called()
        runner.confirm_food_dispatch.assert_not_called()

    def test_failed_return_from_readonly_page_aborts_before_next_candidate(self):
        ledger = IslandRoundInventory()
        runner = probe_runner(ledger, posts={'coffee1': {'status': 'idle', 'button': 'coffee1'}},
                              name_to_config={'apple_pie': {}}, chef_config=['WorkerJuu'])
        runner.back_to_postmanage_from_dispatch.return_value = False
        next_runner = probe_runner(ledger)
        with patch('module.island.planned_dispatch.PlannedProductionMixin._planned_dispatch_stage',
                   return_value='products'), \
                patch('module.island.manufacture_selector.select_manufacture_recipe', return_value=False):
            with self.assertRaises(GameStuckError):
                probe_round_missing_stocks([runner, next_runner], ledger, (3004,))
        next_runner.goto_postmanage.assert_not_called()
        runner.confirm_food_dispatch.assert_not_called()

    def test_failed_return_after_worker_selection_failure_aborts(self):
        ledger = IslandRoundInventory()
        runner = probe_runner(ledger, posts={'coffee1': {'status': 'idle', 'button': 'coffee1'}},
                              name_to_config={'apple_pie': {}}, chef_config=['WorkerJuu'])
        runner.select_character.return_value = False
        runner.back_to_postmanage_from_dispatch.return_value = False
        with patch('module.island.planned_dispatch.PlannedProductionMixin._planned_dispatch_stage',
                   return_value='characters'):
            with self.assertRaises(GameStuckError):
                probe_round_missing_stocks([runner], ledger, (3004,))
        runner.confirm_selected_character.assert_not_called()


if __name__ == '__main__':
    unittest.main()
