"""统一收获后复用仓库现货，确认零库存可补料，未知不派遣。"""

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
        self.scan = self.enterContext(patch('module.island.round_recipe.scan_post_product_stocks',
                                           side_effect=AssertionError('不能再从生产列表盘点')))
        self.selected = self.enterContext(patch('module.island.round_recipe.observe_selected_recipe',
                                               side_effect=AssertionError('不能再从选品页补读库存')))
        self.borrow = self.enterContext(patch('module.island.round_recipe.probe_round_missing_stocks',
                                             side_effect=AssertionError('不能借用其他岗位盘点')))
        self.enterContext(patch('module.island.manufacture_selector.select_manufacture_recipe',
                                side_effect=AssertionError('手工派遣不使用ALAS配方库存确认')))

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
        farm.post_manage_swipe.assert_not_called()
        farm.post_manage_swipe_until_appear.assert_not_called()
        farm.device.sleep.assert_not_called()

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
        self.assertEqual(page, ['postmanage'])
        farm.post_manage_swipe.assert_not_called()
        farm.device.sleep.assert_not_called()

    def make_round_fishery(self, demand=6, stocks=None):
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
        fishery.island_round = IslandRoundInventory({'bass': 0} if stocks is None else stocks)

        def confirm(context):
            confirmed = ui.confirm(context)
            if confirmed:
                fishery._round_dispatch_batches = ui.actual_runs
                item = fishery._round_material_receipt['item_id']
                fishery.island_round.consume({item: ui.actual_runs})
            return confirmed

        fishery.confirm_post_add_order = confirm
        return ui

    def test_warehouse_zero_fish_and_fry_allow_original_shop_replenishment(self):
        ui = self.make_round_fishery()
        ui.run()
        self.assertEqual(ui.buys, [('bass', 5)])
        self.assertEqual(ui.orders, [('bass', 5)])
        self.assertEqual(ui.fishery.island_round.get('bass'), 0)
        self.assertEqual(ui.fishery.island_round.get(ui.fishery._round_material_receipt['item_id']), 0)
        self.assertEqual(ui.fishery.island_round.in_production[resolve_item_id('bass')],
                         5 * ui.fishery.name_to_config['bass']['yield'])

    def test_fish_warehouse_stock_precedes_target_decision(self):
        ui = self.make_round_fishery()
        ui.fishery.island_round.observe('bass', ui.fishery.fishery_threshold['bass'])
        ui.run()
        self.scan.assert_not_called()
        self.selected.assert_not_called()
        self.assertEqual(ui.buys, [])
        self.assertEqual(ui.orders, [])
        ui.fishery.warehouse_inventory.assert_not_called()

    def test_successful_fry_purchase_survives_failed_dispatch(self):
        ui = self.make_round_fishery()
        ui.failed_posts = {0}
        ui.run()
        self.assertEqual(ui.buys, [('bass', 5)])
        self.assertEqual(ui.orders, [])
        material = ui.fishery._round_material_receipt['item_id']
        self.assertEqual(ui.fishery.island_round.get(material), 5)
        self.assertEqual(ui.fishery.island_round.get('bass'), 0)
        self.assertEqual(ui.fishery.posts['ISLAND_FISHERY_POST1']['state'], 'idle')
        self.assertEqual(ui.fishery.island_round.in_production, {})

    def test_farm_uses_confirmed_warehouse_stock_and_does_not_queue_unknown(self):
        farm = object.__new__(IslandFarm)
        farm.config = SimpleNamespace()
        farm.island_round = IslandRoundInventory({'wheat': 20, 'corn': 0, 'carrot': 12})
        farm.INVENTORY_CONFIG = {
            'farm': {'threshold': 10, 'items': [{'name': name} for name in ('wheat', 'corn', 'pasture')]},
            'orchard': {'threshold': 10, 'items': [{'name': 'apple'}]},
            'nursery': {'threshold': 10, 'items': [{'name': 'carrot'}]},
        }
        farm.inventory_counts, farm.to_plant_lists = {}, {}
        farm.crop_targets = {category: {} for category in farm.INVENTORY_CONFIG}
        farm.ignore_avocado = farm.ignore_pineapple = False
        farm.warehouse_inventory = Mock(side_effect=AssertionError('不能重复查仓库'))
        farm.check_inventory_and_prepare_lists()
        self.scan.assert_not_called()
        self.assertEqual(farm.to_plant_lists['farm'], ['corn'])
        self.assertEqual(farm.to_plant_lists['orchard'], [])
        self.assertEqual(farm.to_plant_lists['nursery'], [])
        self.assertIsNone(farm.island_round.get('pasture'))
        farm.warehouse_inventory.assert_not_called()

    def test_farm_success_records_actual_batches_and_working_state(self):
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

    def test_unknown_fish_stock_never_opens_recipe_or_assumes_zero(self):
        ui = self.make_round_fishery(stocks={})
        ui.fishery.post_open = Mock(side_effect=AssertionError('未知库存不能开岗补读'))
        ui.run()
        self.assertEqual(ui.buys, [])
        self.assertEqual(ui.orders, [])
        self.assertEqual(ui.fishery.to_plant_list, [])
        self.assertIsNone(ui.fishery.island_round.get('bass'))
        ui.fishery.post_open.assert_not_called()

    def test_unknown_stock_rejects_direct_farm_fish_and_mine_dispatch_before_opening(self):
        definitions = ((IslandFarm, 'wheat', ('farm', 'time_farm1')),
                       (IslandFishery, 'bass', (0,)),
                       (IslandMineForest, 'Copper', ('mine', 'time_mine1')))
        for task_class, product, arguments in definitions:
            with self.subTest(task=task_class.__name__):
                task = task_class.__new__(task_class)
                task.island_round = IslandRoundInventory()
                task.post_close = Mock()
                task.post_open = Mock(side_effect=AssertionError('未知库存不能进入选品页'))
                self.assertFalse(task.post_plant('post', product, *arguments))
                task.post_close.assert_not_called()
                task.post_open.assert_not_called()

    def make_mill(self, stocks):
        task = object.__new__(IslandRancher)
        task.config = SimpleNamespace()
        task.island_round = IslandRoundInventory(stocks)
        task.inventory_counts = {'farm': {}, 'mill': {}, 'ranch': {}}
        task.INVENTORY_CONFIG = {
            'farm': {'items': [{'name': 'wheat'}]},
            'mill': {'items': [{'name': 'chicken_feed'}, {'name': 'wheat_flour'}]},
            'ranch': {'items': [{'name': 'chicken'}, {'name': 'pork'}]},
        }
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
        self.assertTrue(task.process_mill_item_with_inventory('chicken_feed', 11))
        self.assertTrue(task.process_mill_item_with_inventory('wheat_flour', 200))
        self.assertEqual(task.process_mill_item.call_args_list[0].kwargs, {'quantity': 6})
        self.assertEqual(task.process_mill_item.call_args_list[1].kwargs, {'quantity': 23})
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
        task.process_mill_supplements = Mock(return_value=['chicken_feed'])
        task.check_ranch_needs = Mock(return_value=['ISLAND_RANCH_POST1'])
        task.ranch_post = Mock(return_value=True)
        task._inv_cn = str
        with patch('module.island.island_fishery.IslandFishery', side_effect=AssertionError('不能重建渔场')):
            task.run()
        fishery.run.assert_called_once()
        self.assertIs(fishery.island_round, task.island_round)
        self.assertIs(fishery.config, task.config)
        task.warehouse_mill_ranch.assert_not_called()
        task.ranch_post.assert_called_once_with('ISLAND_RANCH_POST1', 'time_ranch1')
        task.post_manage_swipe.assert_not_called()

    def test_ranch_collection_checks_each_post_without_pre_scrolling(self):
        task = IslandRancher.__new__(IslandRancher)
        task.config, task.device = Mock(), Mock()
        task.posts_ranch = {f'ISLAND_RANCH_POST{index}': Mock() for index in range(1, 5)}
        for name in ('goto_postmanage', 'post_manage_mode', 'post_close',
                     'post_manage_swipe_to_top', 'post_open'):
            setattr(task, name, Mock())
        task.post_list_is_empty = Mock(return_value=True)
        fishery = Mock()
        with patch('module.island.island_fishery.IslandFishery', return_value=fishery):
            task.prepare_round()
        self.assertEqual([call.args[0] for call in task.post_list_is_empty.call_args_list],
                         list(task.posts_ranch.values()))
        self.assertEqual(task._round_ranch_states, {post: 'idle' for post in task.posts_ranch})
        task.post_open.assert_not_called()
        task.post_manage_swipe_to_top.assert_not_called()
        fishery.prepare_round.assert_called_once_with()

    def test_ranch_loads_warehouse_feed_and_materials_without_borrowing_posts(self):
        config = SimpleNamespace(IslandRancher_MinChicken=20, IslandRancher_MinPork=0,
                                 IslandRancher_ChickenFilter='WorkerJuu')

        def init(task, config):
            task.config, task.device = config, Mock()

        with patch.object(Island, '__init__', init):
            task = IslandRancher(config)
        task.island_round = IslandRoundInventory({'egg': 8, 'chicken_feed': 0, 'chicken': 0,
                                                'wheat_flour': 200, 'wheat': 300})
        task.is_ranch_post_enabled = lambda post_id: post_id == 'ISLAND_RANCH_POST1'
        task.warehouse_filter = Mock(side_effect=AssertionError('不能重复进仓库'))
        task.warehouse_mill_ranch()
        self.assertEqual(task.inventory_counts['mill']['chicken_feed'], 0)
        self.assertEqual(task.inventory_counts['farm']['wheat'], 300)
        self.assertIsNone(task.island_round.get('corn'))
        self.assertEqual(task.check_mill_supplement_needs(), [('chicken_feed', 11)])
        self.assertEqual(task.check_ranch_needs(), ['ISLAND_RANCH_POST1'])
        self.borrow.assert_not_called()
        self.selected.assert_not_called()
        task.warehouse_filter.assert_not_called()

    def test_mine_collects_all_posts_and_only_dispatches_confirmed_zero_stock(self):
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
        task.post_plant.assert_not_called()
        task.island_round.observe('Copper', 0)
        task.run()
        self.assertEqual(task.post_plant.call_count, 1)
        self.assertEqual(task.post_plant.call_args.args[1:3], ('Copper', 'mine'))
        self.assertEqual(task.post_plant.call_args.kwargs['need_count'], 4)
        task.ui_goto.assert_not_called()
        task.warehouse_inventory.assert_not_called()
        self.scan.assert_not_called()
        task.post_manage_swipe_to_top.assert_not_called()
        task.post_manage_down_swipe.assert_not_called()
        task.device.sleep.assert_not_called()

    def test_upstream_default_ore_and_wood_are_executed_after_targets(self):
        config = SimpleNamespace(IslandMine_WorkerFilter='WorkerJuu', IslandForest_WorkerFilter='WorkerJuu',
                                 IslandMine_Positions=1, IslandForest_Positions=1,
                                 IslandMine_MineSilver=1, IslandForest_CutElegant=1,
                                 IslandMine_DefaultOre='Copper', IslandForest_DefaultWood='Practical',
                                 task_delay=Mock())
        def init(task, config):
            task.config, task.device = config, Mock()
        with patch.object(Island, '__init__', init):
            task = IslandMineForest(config)
        for name in ('goto_postmanage', 'post_manage_mode', 'post_close', 'post_manage_swipe_to_top',
                     'post_manage_down_swipe', 'ui_goto'):
            setattr(task, name, Mock())
        task.collect_and_detect_post = Mock(side_effect=lambda button, post_id, *args:
                                            task.posts[post_id].update(state='idle'))
        task.prepare_round()
        task.island_round = IslandRoundInventory({'Copper': 50, 'Practical': 50})
        task.post_plant = Mock(return_value=True)
        task.run()
        self.assertEqual([call.args[1:3] for call in task.post_plant.call_args_list],
                         [('Copper', 'mine'), ('Practical', 'forest')])
        self.assertTrue(all(call.kwargs['need_count'] is None for call in task.post_plant.call_args_list))
        task.post_manage_swipe_to_top.assert_not_called()
        task.post_manage_down_swipe.assert_not_called()

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
        task.confirm_post_add_order.assert_called_once_with(
            'Copper矿林派遣', preserve_quantity=True, max_batches=1)
        task.device.click.assert_not_called()
        self.assertEqual(task.island_round.in_production[resolve_item_id('Copper')], 4)


if __name__ == '__main__':
    unittest.main()
