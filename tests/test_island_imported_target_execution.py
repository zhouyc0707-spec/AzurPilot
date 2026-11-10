"""扫描导入的普通任务物品由原生产任务消费，不连接游戏或真实配置。"""

from collections import defaultdict
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import module.config.server as server
from module.config.config_generated import GeneratedConfig
from module.island.island import Island
from module.island.island_farm import IslandFarm
from module.island.island_fishery import IslandFishery
from module.island.island_grill import IslandGrill
from module.island.island_juu_coffee import IslandJuuCoffee
from module.island.island_juu_eatery import IslandJuuEatery
from module.island.island_manufacture import IslandManufacture, PRODUCT_LINES
from module.island.island_mine_forest import IslandMineForest
from module.island.island_rancher import IslandRancher
from module.island.island_restaurant import IslandRestaurant
from module.island.island_season import SeasonConfig
from module.island.island_teahouse import IslandTeahouse
from module.island.item_ids import LOCAL_TO_ITEM_ID
from module.island.round_inventory import IslandRoundInventory
from module.island.round_warehouse import warehouse_catalog
from module.island.seasonal_task_targets import format_imported_targets, imported_item_catalog
from module.island_planner.data import DIC_ISLAND_SEASON, DIC_ISLAND_TASK
from tests.test_island_seasonal_shop_targets import prepare_shop


def make_runner(kind, targets, **settings):
    config = GeneratedConfig()
    values = {'IslandPlan.IslandPlan.Season': 'winter',
              'IslandSeasonalProduction.IslandSeasonWinter.ImportedTargets': format_imported_targets(targets)}
    config.cross_get = lambda path, default=None: values.get(path, default)
    config.task_delay = Mock()
    config.IslandRestaurant_DoubleBambooShoots = False
    config.IslandTeahouse_Seasonal = False
    config.IslandRancher_MinChicken = config.IslandRancher_MinPork = 0
    config.IslandRancher_Milk = config.IslandRancher_Wool = False
    config.IslandMine_MineSilver = config.IslandForest_CutElegant = 0
    config.IslandOrchard_IgnoreAvocado = config.IslandNursery_IgnorePineapple = False
    for area, names in (('Mine', ('Copper', 'Aluminium', 'Iron', 'Sulphur', 'Silver')),
                        ('Forest', ('Elegant', 'Practical', 'Selected'))):
        for name in names:
            setattr(config, f'Island{area}_Min{name}', 0)
    for line in PRODUCT_LINES:
        setattr(config, f'{line}_Positions', 2)
        setattr(config, f'{line}_Products', '')
    for shop in ('Restaurant', 'Teahouse', 'JuuEatery', 'Grill', 'JuuCoffee'):
        for index in range(1, 9):
            setattr(config, f'Island{shop}_Meal{index}', 'None')
            setattr(config, f'Island{shop}_MealNumber{index}', 0)
        setattr(config, f'Island{shop}NextTask_AwayCook', None)
    for name, value in settings.items():
        setattr(config, name, value)

    def initialize(runner, config, device=None, **kwargs):
        runner.config, runner.device = config, Mock()
        runner.unavailable_characters = set()

    with patch.object(Island, '__init__', initialize), patch(
            'module.island.island_shop_base.get_global_season_config', side_effect=SeasonConfig), patch(
            'module.island.island_season.get_global_season_config', side_effect=SeasonConfig):
        return kind(config=config)


class ImportedCatalogExecutionTests(unittest.TestCase):
    def test_all_current_season_task_products_have_real_runner_and_stock_catalog(self):
        task_items = {item for season in DIC_ISLAND_SEASON.values() for task in season['task_list']
                      for item in (DIC_ISLAND_TASK[task].get('target') or {})}
        self.assertEqual(len(task_items), 59)
        catalog = imported_item_catalog()
        categories = defaultdict(list)
        for item in task_items:
            name, category = catalog[item]
            categories[category].append(name)
        runner_types = {'farm': IslandFarm, 'orchard': IslandFarm, 'nursery': IslandFarm,
                        'mine': IslandMineForest, 'forest': IslandMineForest, 'ranch': IslandRancher,
                        'restaurant': IslandRestaurant, 'teahouse': IslandTeahouse,
                        'juu_eatery': IslandJuuEatery, 'grill': IslandGrill, 'juu_coffee': IslandJuuCoffee,
                        'fish_meat': IslandFishery,
                        **{category: IslandManufacture for category in PRODUCT_LINES.values()}}
        for category, names in categories.items():
            with self.subTest(category=category):
                runner = make_runner(runner_types[category], dict.fromkeys(task_items, 1))
                stock_items = {item for group in warehouse_catalog((runner,)).values() for item in group}
                for name in names:
                    self.assertIn(LOCAL_TO_ITEM_ID[name], stock_items, name)
                    if hasattr(runner, 'shop_items'):
                        self.assertIn(name, runner.name_to_config)
                        if category in PRODUCT_LINES.values():
                            self.assertIn(name, {item['name'] for item in runner.manufacture[category]['items']})
                    elif category in ('farm', 'orchard', 'nursery'):
                        self.assertIn(name, runner.name_to_config)

    def test_missing_manufacture_dependencies_have_correct_warehouse_origin(self):
        factory = make_runner(IslandManufacture, {3039: 5, 3054: 5, 3055: 5})
        groups = warehouse_catalog((factory,))
        for name in ('aroma_sachet', 'clock', 'battery', 'chemicals', 'cloth'):
            self.assertIn(name, factory.name_to_config)
            self.assertIn(LOCAL_TO_ITEM_ID[name], groups['factory'])
        self.assertNotIn('chemicals', factory.seasonal_stock_targets())


class ImportedFoodAndManufactureTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.island.island_shop_base.logger'))
        self.enterContext(patch('module.island.island_manufacture.logger'))
        self.enterContext(patch('module.island.production_recipe.ProductionRecipeMixin._open_product_page',
                                return_value=True))

    def test_imported_cuisine_targets_keep_manual_priority_and_do_not_need_free_slots(self):
        cases = ((IslandJuuEatery, 3025, 'rice_mango', 'corn_cup'),
                 (IslandJuuEatery, 3028, 'strawberry_charlotte', 'corn_cup'),
                 (IslandGrill, 3030, 'chicken_potato', 'roasted_skewer'))
        for kind, item, name, regular in cases:
            with self.subTest(name=name):
                shop = prepare_shop(make_runner(kind, {item: 2}))
                shop.post_products = [(regular, 1)] * 8
                shop.run()
                self.assertEqual(shop.selections, [regular, name])
                self.assertEqual([call.args[0] for call in shop.confirm_food_dispatch.call_args_list], [1, 2])
                self.assertEqual(len(shop.post_products), 8)

    def test_each_manufacture_line_runs_finite_imported_target_before_original_fallback(self):
        cases = ((3039, 'aroma_sachet', 'handmade', 'leather', {'cloth': 20}),
                 (3047, 'cutlery', 'industrial_production', 'iron_nail', {}),
                 (3053, 'ink_cartridge', 'electronic_processing', 'filter_element', {}),
                 (3054, 'clock', 'electronic_processing', 'filter_element', {}),
                 (3055, 'battery', 'electronic_processing', 'filter_element', {'chemicals': 20}),
                 (3117, 'ornamental_painting', 'wood_processing', 'file_cabinet',
                  {'paper': 20, 'iron_nail': 20}))
        for item, name, category, fallback, stocks in cases:
            with self.subTest(name=name):
                factory = prepare_shop(make_runner(IslandManufacture, {item: 2}), stocks)
                line = next(line for line, value in PRODUCT_LINES.items() if value == category)
                setattr(factory.config, f'{line}_Products', fallback)
                getattr(factory, f'schedule_{category}')()
                self.assertEqual(factory.selections, [name, fallback])
                self.assertEqual(factory.confirm_food_dispatch.call_args_list[0].args[0], 2)
                self.assertEqual(factory.island_round.in_production[LOCAL_TO_ITEM_ID[name]], 2)

    def test_manufacture_unknown_dependency_does_not_become_zero_or_trigger_auto_chain(self):
        factory = prepare_shop(make_runner(IslandManufacture, {3055: 2}))
        factory.island_round.observe('chemicals', None)
        factory._schedule_seasonal_production('electronic_processing')
        self.assertEqual(factory.selections, [])
        self.assertIsNone(factory.island_round.get('chemicals'))

    def test_unknown_inflight_blocks_only_its_manufacture_line(self):
        factory = prepare_shop(make_runner(IslandManufacture, {3047: 2, 3054: 2}))
        factory._unknown_working_categories.add('electronic_processing')
        factory._schedule_seasonal_production('electronic_processing')
        factory._schedule_seasonal_production('industrial_production')
        self.assertEqual(factory.selections, ['cutlery'])


class ImportedCropTargetsTests(unittest.TestCase):
    def farm(self, targets, stocks):
        farm = make_runner(IslandFarm, targets, IslandFarm_MinFarm=100,
                           IslandOrchard_MinOrchard=100, IslandNursery_MinNursery=100)
        current = {name: 500 for name in farm.name_to_config}
        current.update(stocks)
        farm.island_round = IslandRoundInventory(current)
        return farm

    def test_imported_ordinary_target_does_not_remove_existing_area_minimum(self):
        farm = self.farm({2014: 50, 2017: 50, 2021: 50}, {'tea': 80, 'citrus': 80, 'avocado': 80})
        farm.check_inventory_and_prepare_lists()
        self.assertEqual(farm.to_plant_lists['nursery'], ['tea'])
        self.assertEqual(set(farm.to_plant_lists['orchard']), {'citrus', 'avocado'})

    def test_regular_shortage_precedes_imported_crop_and_seasonal_avocado_overrides_ignore(self):
        farm = self.farm({2017: 200, 2021: 200, 2019: 200},
                         {'apple': 99, 'citrus': 150, 'avocado': 150, 'mango': 150})
        farm.ignore_avocado = True
        farm.check_inventory_and_prepare_lists()
        self.assertEqual(farm.to_plant_lists['orchard'][0], 'apple')
        self.assertEqual(set(farm.to_plant_lists['orchard'][1:]), {'citrus', 'mango', 'avocado'})
        self.assertTrue(farm.ignore_avocado)

    def test_ignored_avocado_only_follows_seasonal_target_not_higher_regular_floor(self):
        farm = self.farm({2021: 50}, {'avocado': 80})
        farm.ignore_avocado = True
        farm.check_inventory_and_prepare_lists()
        self.assertNotIn('avocado', farm.to_plant_lists['orchard'])
        farm.island_round.observe('avocado', 49)
        farm.check_inventory_and_prepare_lists()
        self.assertEqual(farm.to_plant_lists['orchard'], ['avocado'])
        farm.island_round.observe('avocado', 50)
        farm.check_inventory_and_prepare_lists()
        self.assertNotIn('avocado', farm.to_plant_lists['orchard'])

    def test_zero_or_inactive_target_keeps_avocado_ignored(self):
        for target, season in ((0, 'winter'), (100, 'summer')):
            with self.subTest(target=target, season=season):
                farm = self.farm({2021: target}, {'avocado': 0})
                farm.ignore_avocado = True
                original_get = farm.config.cross_get
                farm.config.cross_get = lambda path, default=None: (
                    season if path == 'IslandPlan.IslandPlan.Season' else original_get(path, default))
                farm.check_inventory_and_prepare_lists()
                self.assertNotIn('avocado', farm.to_plant_lists['orchard'])

    def test_seasonal_avocado_override_still_requires_known_stock_and_working_identity(self):
        farm = self.farm({2021: 100}, {'avocado': 0})
        farm.ignore_avocado = True
        farm.island_round.observe('avocado', None)
        farm.check_inventory_and_prepare_lists()
        self.assertNotIn('avocado', farm.to_plant_lists['orchard'])
        farm.island_round.observe('avocado', 0)
        farm.posts = {'ISLAND_ORCHARD_POST1': {'state': 'working', 'crop': None}}
        farm.check_inventory_and_prepare_lists()
        self.assertNotIn('avocado', farm.to_plant_lists['orchard'])


class ImportedMineTargetsTests(unittest.TestCase):
    def miner(self, targets, stocks=None, mine_posts=4, forest_posts=1, **settings):
        miner = make_runner(IslandMineForest, targets, **settings)
        miner.mine_post_ids = [f'ISLAND_MINE_POST{i+1}' for i in range(mine_posts)]
        miner.forest_post_ids = [f'ISLAND_FOREST_POST{i+1}' for i in range(forest_posts)]
        miner.posts = {post: {'button': Mock(), 'crop': None, 'runs': 0,
                             'state': 'idle', 'round_enabled': True}
                       for post in miner.mine_post_ids + miner.forest_post_ids}
        miner.island_round = IslandRoundInventory(stocks or {})
        for method in ('goto_postmanage', 'post_manage_mode', 'post_close'):
            setattr(miner, method, Mock())
        miner.warehouse_inventory = Mock(side_effect=AssertionError('共享轮不得重读仓库'))
        miner.post_plant = Mock(return_value=True)
        return miner

    def test_regular_full_shortage_then_imported_target_then_default(self):
        miner = self.miner({2705: 3, 2803: 5}, {'Copper': 0, 'Silver': 0, 'Elegant': 0},
                           IslandMine_MinCopper=60, IslandMine_MineSilver=1)
        miner.run()
        self.assertEqual([(call.args[1], call.kwargs['need_count']) for call in miner.post_plant.call_args_list],
                         [('Copper', 48), ('Copper', 12), ('Silver', 3), ('Silver', None), ('Elegant', 5)])
        miner.warehouse_inventory.assert_not_called()

    def test_same_product_target_subtracts_rounded_regular_output_once(self):
        miner = self.miner({2705: 4}, {'Silver': 0}, IslandMine_MinSilver=1)
        miner.run()
        self.assertEqual([(call.args[1], call.kwargs['need_count']) for call in miner.post_plant.call_args_list],
                         [('Silver', 1)])

    def test_known_stock_and_inflight_satisfy_imported_target(self):
        miner = self.miner({2705: 8}, {'Silver': 4})
        miner.posts[miner.mine_post_ids[0]].update(state='working', crop='Silver', runs=1)
        miner.run()
        miner.post_plant.assert_not_called()

    def test_unknown_finished_or_inflight_and_disabled_posts_do_not_dispatch(self):
        for state in ('unknown_stock', 'unknown_working', 'disabled'):
            with self.subTest(state=state):
                miner = self.miner({2705: 8}, {} if state == 'unknown_stock' else {'Silver': 0})
                if state == 'unknown_working':
                    miner.posts[miner.mine_post_ids[0]].update(state='working', crop='unknown', runs=1)
                if state == 'disabled':
                    for post in miner.posts.values():
                        post['round_enabled'] = False
                miner.run()
                miner.post_plant.assert_not_called()

    def test_standalone_imported_ore_and_wood_read_origin_without_filling_unknown_stock(self):
        miner = make_runner(IslandMineForest, {2705: 8, 2803: 9})
        for category, item, name in (('mine', 2705, 'Silver'), ('forest', 2803, 'Elegant')):
            with self.subTest(category=category), patch(
                    'module.island.round_warehouse.RoundWarehouseReader') as reader:
                reader.return_value.read_group.return_value = {item: 3}
                self.assertEqual(miner.warehouse_inventory(category), {name: 3})
                source, templates, requested = reader.return_value.read_group.call_args.args
                self.assertEqual(source, category)
                self.assertIn(item, templates)
                self.assertEqual(requested, set(templates))


class ImportedFishAndMilkTests(unittest.TestCase):
    def test_imported_fish_meat_targets_merge_max_and_retain_raw_fish_protection(self):
        fishery = make_runner(IslandFishery, {2521: 100, 2522: 50},
                               IslandFishery_MinFreshwaterFishMeat=120, IslandFishery_MinSaltwaterFishMeat=20)
        fishery.fishery_threshold = {'koi_carp': 30}
        fishery.ui_ensure = Mock()
        with patch.object(server, 'server', 'cn'), patch(
                'module.island.fish_exchange.FishExchangeSession') as exchange, patch(
                'module.island.fish_exchange.get_fish_protection', return_value={5003: 20}):
            fishery.run_fish_meat_targets()
        targets, protected = exchange.return_value.run.call_args.args
        self.assertEqual(targets, {2521: 120, 2522: 50})
        self.assertEqual(protected[5003], 30)
        self.assertEqual(fishery.config.IslandFishery_MinSaltwaterFishMeat, 20)
        self.assertEqual(fishery.fishery_threshold, {'koi_carp': 30})

    def test_imported_fish_targets_work_when_manual_meat_targets_are_zero(self):
        fishery = make_runner(IslandFishery, {2521: 7},
                               IslandFishery_MinFreshwaterFishMeat=0, IslandFishery_MinSaltwaterFishMeat=0)
        fishery.run_fish_meat_exchange = Mock()
        with patch.object(server, 'server', 'en'), patch(
                'module.island.fish_exchange.get_fish_protection', return_value={}):
            fishery.run_fish_meat_targets()
        self.assertEqual(fishery.run_fish_meat_exchange.call_args.args[0], {2521: 7, 2522: 0})

    def test_imported_milk_does_not_enable_disabled_cow_and_reports_reason(self):
        ranch = make_runner(IslandRancher, {2603: 20})
        with patch('module.island.island_rancher.logger') as log:
            self.assertEqual(ranch.check_ranch_needs(), [])
        self.assertTrue(any('养牛开关关闭' in call.args[0] for call in log.warning.call_args_list))
        self.assertFalse(ranch.config.IslandRancher_Milk)
        ranch.config.IslandRancher_Milk = True
        self.assertEqual(ranch.check_ranch_needs(), ['ISLAND_RANCH_POST3'])


if __name__ == '__main__':
    unittest.main()
