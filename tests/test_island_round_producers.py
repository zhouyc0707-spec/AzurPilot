"""统一收获后生产任务复用现货，零库存补读和实际加工量不互相串账。"""

import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.exception import GameStuckError
from module.island.island import Island, ISLAND_SELECT_PRODUCT_CHECK, MATERIAL_READY
from module.island.island_farm import IslandFarm
from module.island.island_fishery import IslandFishery
from module.island.island_mine_forest import IslandMineForest
from module.island.island_rancher import IslandRancher
from module.island.item_ids import resolve_item_id
from module.island.round_inventory import IslandRoundInventory
from tests.test_island_fishery_quantities import FisheryUI


class RoundProducerTests(unittest.TestCase):
    def setUp(self):
        for name in ('island', 'island_farm', 'island_fishery', 'island_rancher', 'island_mine_forest'):
            self.enterContext(patch(f'module.island.{name}.logger'))
        self.enterContext(patch('module.island.production_planner.refresh_production_plan'))
        self.scan = self.enterContext(patch('module.island.round_recipe.scan_post_product_stocks', return_value={}))

    def test_farm_shared_stock_keeps_unknown_distinct_from_real_zero(self):
        farm = object.__new__(IslandFarm)
        farm.INVENTORY_CONFIG = {'farm': {'items': [
            {'name': name, 'var_name': name} for name in ('wheat', 'corn', 'pasture')]}}
        farm.island_round = IslandRoundInventory({'wheat': 0, 'corn': 8})
        farm.warehouse_filter = Mock(side_effect=AssertionError('不能重复进仓库'))
        self.assertEqual(farm.warehouse_inventory('farm'), {'wheat': 0, 'corn': 8})
        self.assertIsNone(farm.island_round.get('pasture'))

    def test_farm_prepares_all_defined_posts_but_only_dispatches_enabled_slot(self):
        farm = object.__new__(IslandFarm)
        farm.device = Mock()
        farm.farm_positions, farm.orchard_positions, farm.nursery_positions = 1, 0, 0
        farm.posts = {f'ISLAND_{category.upper()}_POST{index}': {'button': Mock()}
                      for category, count in (('farm', 4), ('orchard', 4), ('nursery', 2))
                      for index in range(1, count + 1)}
        for name in ('goto_postmanage', 'post_manage_mode', 'post_close', 'post_manage_swipe',
                     'post_manage_swipe_until_appear'):
            setattr(farm, name, Mock())

        def collect(button, post_id, category, time_var):
            farm.posts[post_id]['state'] = 'idle'
            return True

        farm.decided_lists = Mock(side_effect=collect)
        farm.prepare_round()
        self.assertEqual(farm.decided_lists.call_count, 10)
        self.assertEqual([len(farm._round_idle_posts[name]) for name in ('farm', 'orchard', 'nursery')],
                         [1, 0, 0])

    def test_farm_run_returns_to_management_and_keeps_working_crop_out_of_restock(self):
        farm = object.__new__(IslandFarm)
        farm.device, farm.config = Mock(), SimpleNamespace(task_delay=Mock())
        farm.island_round = IslandRoundInventory({'wheat': 0, 'corn': 0})
        farm.farm_positions, farm.orchard_positions, farm.nursery_positions = 2, 0, 0
        farm.posts = {'ISLAND_FARM_POST1': {'crop': 'wheat', 'state': 'working'},
                      'ISLAND_FARM_POST2': {'crop': None, 'state': 'idle'}}
        farm.inventory_counts = {'farm': {'wheat': 0, 'corn': 0}, 'orchard': {}, 'nursery': {}}
        farm.to_plant_lists = {'farm': ['wheat', 'corn'], 'orchard': [], 'nursery': []}
        farm.time_vars = {'farm': [None, None], 'orchard': [], 'nursery': []}
        farm._round_idle_posts = {'farm': [{'post_id': 'ISLAND_FARM_POST2'}], 'orchard': [], 'nursery': []}
        farm.plant_config = {'farm': {'plant_default': 0}}
        farm._resolve_default_crop = Mock(return_value='wheat')
        farm.check_inventory_and_prepare_lists = Mock()
        farm._read_round_production_stocks = Mock()
        farm.prepare_round = Mock(side_effect=AssertionError('不能再次巡检领取'))
        farm._inv_cn = str
        page = []
        farm.ui_ensure = lambda target: page.append('island')
        farm.goto_postmanage = lambda: page.append('postmanage')
        for name in ('post_manage_mode', 'post_close', 'post_manage_swipe'):
            setattr(farm, name, Mock())

        def plant(category, posts, crops):
            self.assertEqual(page[-1], 'postmanage')
            self.assertEqual(crops, ['corn'])

        farm._plant_idle_posts = Mock(side_effect=plant)
        farm.run()
        farm._plant_idle_posts.assert_called_once()
        self.assertEqual(page, ['island', 'postmanage'])

    def make_round_fishery(self, demand=6):
        ui = FisheryUI({'bass': demand}, positions=1)
        fishery = ui.fishery
        fishery.decided_lists = Mock(side_effect=ui.scan_post)
        fishery.prepare_round()
        self.assertEqual(fishery.decided_lists.call_count, 3)
        self.assertEqual(len(fishery._round_idle_posts), 1)
        fishery.decided_lists = Mock(side_effect=AssertionError('不能重复收获'))
        fishery.warehouse_inventory = Mock(side_effect=AssertionError('共享模式不得调用仓库读数'))
        fishery.warehouse_filter = Mock(side_effect=AssertionError('不能重复进仓库'))
        fishery.run_fish_meat_targets = Mock()
        fishery.island_round = IslandRoundInventory()

        def confirm(context):
            confirmed = ui.confirm(context)
            if confirmed:
                fishery._round_dispatch_batches = ui.actual_runs
                item = fishery._round_material_receipt['item_id']
                fishery.island_round.consume({item: ui.actual_runs})
            return confirmed

        fishery.confirm_post_add_order = confirm
        return ui

    def test_unknown_zero_fish_and_fry_are_read_then_original_shop_replenishes(self):
        ui = self.make_round_fishery()

        def observe(main, product):
            main.island_round.observe(product, 0)
            return True

        with patch('module.island.round_recipe.observe_selected_recipe', side_effect=observe):
            ui.run()
        self.assertEqual(ui.buys, [('bass', 5)])
        self.assertEqual(ui.orders, [('bass', 5)])
        self.assertEqual(ui.fishery.island_round.get('bass'), 0)
        self.assertEqual(ui.fishery.island_round.get(ui.fishery._round_material_receipt['item_id']), 0)
        self.assertEqual(ui.fishery.island_round.in_production[resolve_item_id('bass')],
                         5 * ui.fishery.name_to_config['bass']['yield'])

    def test_fish_production_page_stock_precedes_target_decision(self):
        ui = self.make_round_fishery()

        def scan(main, button, products, characters):
            main.island_round.observe('bass', main.fishery_threshold['bass'])
            return {resolve_item_id('bass'): main.fishery_threshold['bass']}

        self.scan.side_effect = scan
        with patch('module.island.round_recipe.observe_selected_recipe') as selected:
            ui.run()
        self.scan.assert_called_once()
        selected.assert_not_called()
        self.assertEqual(ui.buys, [])
        self.assertEqual(ui.orders, [])
        ui.fishery.warehouse_inventory.assert_not_called()

    def test_farm_scans_each_available_category_before_reading_shared_targets(self):
        farm = object.__new__(IslandFarm)
        farm.config = SimpleNamespace()
        farm.island_round = IslandRoundInventory()
        farm.INVENTORY_CONFIG = {
            'farm': {'threshold': 10, 'items': [{'name': name} for name in ('wheat', 'corn', 'pasture')]},
            'orchard': {'threshold': 10, 'items': [{'name': 'apple'}]},
            'nursery': {'threshold': 10, 'items': [{'name': 'carrot'}]},
        }
        farm._round_idle_posts = {'farm': [{'button': 'farm_slot'}], 'orchard': [],
                                  'nursery': [{'button': 'nursery_slot'}]}
        farm.worker_filters = {'farm': 'farmer', 'nursery': 'gardener'}
        farm.inventory_counts, farm.to_plant_lists = {}, {}
        farm.crop_targets = {category: {} for category in farm.INVENTORY_CONFIG}
        farm.ignore_avocado = farm.ignore_pineapple = False
        farm.warehouse_inventory = Mock(side_effect=AssertionError('不能前置查仓库'))

        def scan(main, button, products, characters):
            for product, quantity in {'wheat': 20, 'corn': 0, 'carrot': 12}.items():
                if product in products:
                    main.island_round.observe(product, quantity)

        self.scan.side_effect = scan
        farm._read_round_production_stocks()
        farm.check_inventory_and_prepare_lists()
        self.assertEqual(self.scan.call_count, 2)
        self.assertEqual(farm.to_plant_lists['farm'], ['corn', 'pasture'])
        self.assertEqual(farm.to_plant_lists['nursery'], [])
        self.assertIsNone(farm.island_round.get('pasture'))
        farm.warehouse_inventory.assert_not_called()

    def test_farm_success_changes_current_post_state_before_later_stock_borrowing(self):
        farm = IslandFarm.__new__(IslandFarm)
        farm.device = Mock()
        farm.island_round = IslandRoundInventory({'wheat': 0})
        farm.name_to_config = {'wheat': {'selection': Mock(), 'selection_check': Mock(),
                                       'shop': Mock(), 'seed_number': 4}}
        button = Mock()
        post = {'button': button, 'state': 'idle', 'crop': None, 'runs': 0}
        farm.posts = {'ISLAND_FARM_POST1': post}
        farm.post_close = Mock()
        farm.post_open = Mock(return_value=True)
        farm.loop = lambda **kwargs: iter([None])
        farm.appear = lambda target, **kwargs: target is ISLAND_SELECT_PRODUCT_CHECK
        farm.appear_then_click = Mock(return_value=False)
        farm.select_product = Mock(return_value=True)
        farm.ensure_select_product_material = Mock(return_value=MATERIAL_READY)
        farm.confirm_post_add_order = Mock(return_value=True)
        farm._round_dispatch_batches = 2
        with patch('module.island.island_farm.Duration') as duration:
            duration.return_value.ocr.return_value = timedelta(hours=1)
            self.assertTrue(farm.post_plant(button, 'wheat', 'farm', 'time_farm1'))
        self.assertIs(farm.posts['ISLAND_FARM_POST1'], post)
        self.assertEqual((post['state'], post['crop'], post['runs']), ('working', 'wheat', 2))

    def test_selected_fish_already_at_target_does_not_buy_or_dispatch(self):
        ui = self.make_round_fishery()

        def observe(main, product):
            main.island_round.observe(product, main.fishery_threshold[product])
            return True

        with patch('module.island.round_recipe.observe_selected_recipe', side_effect=observe):
            ui.run()
        self.assertEqual(ui.buys, [])
        self.assertEqual(ui.orders, [])
        self.assertEqual(ui.fishery.to_plant_list, [])

    def test_failed_fish_observation_never_assumes_zero_or_buys(self):
        ui = self.make_round_fishery()
        with patch('module.island.round_recipe.observe_selected_recipe', return_value=False):
            ui.run()
        self.assertEqual(ui.buys, [])
        self.assertEqual(ui.orders, [])
        self.assertTrue(ui.fishery.island_round.is_blocked('bass'))
        self.assertIsNone(ui.fishery.island_round.get('bass'))

    def make_mill(self, stocks):
        task = object.__new__(IslandRancher)
        task.config = SimpleNamespace()
        task.island_round = IslandRoundInventory(stocks)
        task.inventory_counts = {'farm': {}, 'mill': {}, 'ranch': {}}
        task.name_to_config = {
            'chicken_feed': {'required_material': 'wheat'},
            'wheat_flour': {'required_material': 'wheat'},
        }
        task._item_cn = lambda value: value
        return task

    def test_partial_mill_purchase_consumes_only_confirmed_batches(self):
        task = self.make_mill({'wheat': 200, 'chicken_feed': 0, 'wheat_flour': 0})

        def process(item, quantity):
            task._round_mill_batches = 2 if item == 'chicken_feed' else 3
            return True

        task.process_mill_item = Mock(side_effect=process)
        with patch('module.island.production_planner.load_production_protection', return_value={'wheat': 20}):
            self.assertTrue(task.process_mill_item_with_inventory('chicken_feed', 11))
            self.assertTrue(task.process_mill_item_with_inventory('wheat_flour', 200))
        self.assertEqual(task.process_mill_item.call_args_list[0].kwargs, {'quantity': 6})
        self.assertEqual(task.process_mill_item.call_args_list[1].kwargs, {'quantity': 20})
        self.assertEqual(task.island_round.get('wheat'), 122)
        self.assertEqual(task.island_round.get('chicken_feed'), 20)
        self.assertEqual(task.island_round.get('wheat_flour'), 3)
        self.assertEqual(task.inventory_counts['farm']['wheat'], 122)

    def test_uncertain_mill_purchase_aborts_round_without_fabricated_balance(self):
        task = self.make_mill({'wheat': 300, 'chicken_feed': 0})
        task.process_mill_item = Mock(return_value=False)
        with self.assertRaises(GameStuckError):
            task.process_mill_item_with_inventory('chicken_feed', 5)
        self.assertEqual(task.island_round.get('wheat'), 300)
        self.assertEqual(task.island_round.get('chicken_feed'), 0)

    def test_unknown_mill_output_is_not_treated_as_empty(self):
        task = self.make_mill({'wheat': 300})
        task.process_mill_item = Mock()
        self.assertFalse(task.process_mill_item_with_inventory('chicken_feed', 5))
        task.process_mill_item.assert_not_called()

    def test_ranch_unknown_meat_waits_for_confirmed_zero(self):
        task = self.make_mill({})
        task.ranch_chicken_threshold = task.ranch_pork_threshold = 20
        task.is_ranch_post_enabled = Mock(return_value=False)
        self.assertEqual(task.check_ranch_needs(), [])
        task.island_round.observe('chicken', 0)
        task.island_round.observe('pork', 0)
        self.assertEqual(task.check_ranch_needs(), ['ISLAND_RANCH_POST1', 'ISLAND_RANCH_POST2'])

    def test_ranch_reuses_prepared_fishery_and_preserves_finish_times(self):
        task = self.make_mill({})
        task._round_fishery = fishery = Mock()
        task.device = Mock()
        for index in range(1, 5):
            setattr(task, f'time_ranch{index}', None)
        for name in ('ui_ensure', 'warehouse_mill_ranch', 'goto_postmanage', 'post_manage_mode',
                     'post_close', 'post_manage_swipe'):
            setattr(task, name, Mock())
        task.process_mill_supplements = Mock(return_value=[])
        task.check_ranch_needs = Mock(return_value=[])
        task._read_round_production_stocks = Mock()
        task._inv_cn = str
        with patch('module.island.island_fishery.IslandFishery', side_effect=AssertionError('不能重建渔场')):
            task.run()
        fishery.run.assert_called_once()
        self.assertIs(fishery.island_round, task.island_round)
        self.assertIs(fishery.config, task.config)
        task.warehouse_mill_ranch.assert_not_called()

    def test_ranch_reads_own_feed_then_borrows_only_needed_unknown_mill_items(self):
        config = SimpleNamespace(IslandRancher_MinChicken=20, IslandRancher_MinPork=0,
                                 IslandRancher_ChickenFilter='WorkerJuu')

        def init(task, config):
            task.config, task.device = config, Mock()

        with patch.object(Island, '__init__', init):
            task = IslandRancher(config)
        task.island_round = IslandRoundInventory()
        task._round_ranch_states = {'ISLAND_RANCH_POST1': 'idle'}
        task.is_ranch_post_enabled = lambda post_id: post_id == 'ISLAND_RANCH_POST1'
        task._round_stock_runners = providers = (object(), task, object())
        task.warehouse_mill_ranch = Mock(side_effect=AssertionError('不能查仓库'))

        def observe(main, product):
            main.island_round.observe('egg', 8)
            main.island_round.observe('chicken_feed', 0)
            return True

        def borrow(runners, ledger, requested):
            self.assertIs(runners, providers)
            for product in requested:
                ledger.observe(product, {'chicken': 0, 'wheat_flour': 200, 'wheat': 300}[product])

        with patch('module.island.round_recipe._open_probe_product_page', return_value=True) as opened, \
                patch('module.island.round_recipe._close_probe_product_page') as closed, \
                patch('module.island.round_recipe.scan_recipe_product_stocks') as scan, \
                patch('module.island.manufacture_selector.select_manufacture_recipe', return_value=True), \
                patch('module.island.round_recipe.observe_selected_recipe', side_effect=observe), \
                patch('module.island.round_recipe.probe_round_missing_stocks', side_effect=borrow) as probe:
            task._read_round_production_stocks()
        opened.assert_called_once()
        closed.assert_called_once_with(task)
        scan.assert_called_once_with(task, ['egg'])
        self.assertEqual([entry.args[2] for entry in probe.call_args_list],
                         [{'wheat_flour', 'chicken'}, {'wheat'}])
        self.assertEqual(task.inventory_counts['mill']['chicken_feed'], 0)
        self.assertEqual(task.inventory_counts['farm']['wheat'], 300)
        self.assertIsNone(task.island_round.get('corn'))
        task.warehouse_mill_ranch.assert_not_called()

    def test_mine_collects_all_posts_but_unknown_stock_only_uses_enabled_slot(self):
        config = SimpleNamespace(IslandMine_WorkerFilter='WorkerJuu', IslandForest_WorkerFilter='WorkerJuu',
                                 IslandMine_Positions=1, IslandForest_Positions=0, IslandMine_MinCopper=4,
                                 IslandMine_MineSilver=0, IslandForest_CutElegant=0, task_delay=Mock())

        def init(task, config):
            task.config, task.device = config, Mock()

        with patch.object(Island, '__init__', init):
            task = IslandMineForest(config)
        for name in ('goto_postmanage', 'post_manage_mode', 'post_close', 'post_manage_swipe_to_top',
                     'post_manage_down_swipe', 'ui_goto'):
            setattr(task, name, Mock())

        def collect(button, post_id, category, time_var):
            task.posts[post_id].update(state='idle')
            return False

        task.collect_and_detect_post = Mock(side_effect=collect)
        task.prepare_round()
        self.assertEqual(task.collect_and_detect_post.call_count, 8)
        task.collect_and_detect_post = Mock(side_effect=AssertionError('不能重复收获'))
        task.island_round = IslandRoundInventory()
        task.warehouse_filter = Mock(side_effect=AssertionError('不能重复进仓库'))
        task.warehouse_inventory = Mock(side_effect=AssertionError('共享模式不得调用仓库读数'))
        task.post_plant = Mock(return_value=True)
        task.run()
        self.assertEqual(task.post_plant.call_count, 1)
        self.assertEqual(task.post_plant.call_args.args[1:3], ('Copper', 'mine'))
        self.assertIsNone(task.post_plant.call_args.kwargs['need_count'])
        task.ui_goto.assert_not_called()
        task.warehouse_inventory.assert_not_called()
        self.scan.assert_called_once()

    def test_mine_small_deficit_preserves_its_selected_single_batch(self):
        task = IslandMineForest.__new__(IslandMineForest)
        task.device = Mock()
        task.island_round = IslandRoundInventory({'Copper': 0})
        task.inventory_config = {'mine': {'items': [
            {'name': 'Copper', 'selection': Mock(), 'selection_check': Mock()}]}}
        button = Mock()
        task.posts = {'ISLAND_MINE_POST1': {'button': button}}
        task.post_close = Mock()
        task.post_open = Mock(return_value=True)
        task.loop = lambda **kwargs: iter([None])
        task.appear = lambda target, **kwargs: target is ISLAND_SELECT_PRODUCT_CHECK
        task.appear_then_click = Mock(return_value=False)
        task.select_product = Mock(return_value=True)
        task.is_product_confirmed = Mock(return_value=True)
        task.confirm_post_add_order = Mock(return_value=True)
        task._round_dispatch_batches = 1
        with patch('module.island.island_mine_forest.Duration') as duration:
            duration.return_value.ocr.return_value = timedelta(hours=1)
            self.assertTrue(task.post_plant(button, 'Copper', 'mine', 'time_mine1', need_count=4))
        task.confirm_post_add_order.assert_called_once_with('Copper矿林派遣', preserve_quantity=True)
        task.device.click.assert_not_called()
        self.assertEqual(task.island_round.in_production[resolve_item_id('Copper')], 4)


if __name__ == '__main__':
    unittest.main()
