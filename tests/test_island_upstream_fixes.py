"""验证上游岛屿修复与本地生产规划的融合，不连接游戏账号。"""

import json
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.base.utils import load_image
from module.island.assets import ISLAND_POST_SELECT, ISLAND_SELECT_PRODUCT_CHECK
from module.island.island_mine_forest import IslandMineForest, POST_ADD_ORDER
from module.island.island_rancher import IslandRancher
from module.island.item_ids import LOCAL_TO_ITEM_ID
from module.island.production_planner import CONFIG_PREFIX
from module.island_mine_forest.assets import (
    SELECT_ALUMINIUM, SELECT_ALUMINIUM_CHECK, SELECT_COPPER_CHECK,
)
from tests.test_island_production_planner import MemoryConfig


class RanchSwitchMergeTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.island.island_rancher.logger'))
        self.task = IslandRancher.__new__(IslandRancher)
        self.task.config = MemoryConfig(**{f'{CONFIG_PREFIX}.Enabled': False})
        self.task.config.IslandRancher_Milk = False
        self.task.config.IslandRancher_Wool = False
        self.task.ranch_chicken_threshold = self.task.ranch_pork_threshold = 100
        self.task.ranch_feed_map = {
            f'ISLAND_RANCH_POST{i}': name for i, name in enumerate(
                ('chicken_feed', 'pig_feed', 'cattle_feed', 'sheep_feed'), 1)
        }
        self.task.inventory_counts = {'mill': {'wheat_flour': 1000}, 'ranch': {}}
        self.task.name_to_config = {name: {'number': 11} for name in self.task.ranch_feed_map.values()}

    def test_manual_switches_disable_cow_and_sheep_dispatch_and_feed(self):
        self.assertEqual(self.task.check_ranch_needs(), ['ISLAND_RANCH_POST1', 'ISLAND_RANCH_POST2'])
        self.assertEqual(self.task.check_mill_supplement_needs(), [('chicken_feed', 11), ('pig_feed', 11)])
        self.task.config.IslandRancher_Milk = True
        self.assertEqual(self.task.check_ranch_needs(), [
            'ISLAND_RANCH_POST1', 'ISLAND_RANCH_POST2', 'ISLAND_RANCH_POST3'])
        self.assertIn(('cattle_feed', 11), self.task.check_mill_supplement_needs())
        self.assertNotIn(('sheep_feed', 11), self.task.check_mill_supplement_needs())

    def test_valid_planner_keeps_feed_dependency_then_restores_manual_switches(self):
        config = self.task.config
        config.values[f'{CONFIG_PREFIX}.Enabled'] = True
        config.values[f'{CONFIG_PREFIX}.PlanFingerprint'] = 'valid'
        config.values[f'{CONFIG_PREFIX}.PlannerTargets'] = json.dumps({
            LOCAL_TO_ITEM_ID[name]: 50 for name in ('cattle_feed', 'sheep_feed')
        })
        self.assertTrue(self.task.is_ranch_post_enabled('ISLAND_RANCH_POST3'))
        self.assertTrue(self.task.is_ranch_post_enabled('ISLAND_RANCH_POST4'))
        needs = self.task.check_mill_supplement_needs()
        self.assertIn(('cattle_feed', 5), needs)
        self.assertIn(('sheep_feed', 5), needs)
        self.assertFalse(config.IslandRancher_Milk)
        self.assertFalse(config.IslandRancher_Wool)
        self.assertEqual(config.writes, [])
        config.values[f'{CONFIG_PREFIX}.Enabled'] = False
        self.assertFalse(self.task.is_ranch_post_enabled('ISLAND_RANCH_POST3'))
        self.assertFalse(self.task.is_ranch_post_enabled('ISLAND_RANCH_POST4'))
        self.assertEqual(self.task.check_mill_supplement_needs(), [('chicken_feed', 11), ('pig_feed', 11)])

    def test_planned_leather_demand_keeps_existing_cow_dispatch_with_manual_switch_off(self):
        config = self.task.config
        config.values.update({f'{CONFIG_PREFIX}.Enabled': True,
                              f'{CONFIG_PREFIX}.PlanFingerprint': 'valid',
                              f'{CONFIG_PREFIX}.PlannerTargets': '{"2604":5}'})
        for name in ('ChickenFilter', 'PigFilter', 'RancherFilter', 'WoolWorkerFilter'):
            setattr(config, f'IslandRancher_{name}', 'WorkerJuu')
        configs = [(f'ISLAND_RANCH_POST{i}', f'time_ranch{i}') for i in range(1, 5)]
        self.task.posts_ranch = {pid: object() for pid, _ in configs}
        for name in ('goto_postmanage', 'post_manage_mode', 'post_manage_swipe_to_top', 'post_close'):
            setattr(self.task, name, Mock())
        self.task.post_open = self.task.post_get_and_close = Mock(return_value=True)
        self.task.loop = Mock(side_effect=lambda **kwargs: iter((object(),)))
        self.task.appear = Mock(side_effect=lambda button, **kwargs: button is ISLAND_POST_SELECT)
        self.task._probe_ranch_secondary_stock = Mock(return_value=0)
        self.task._planned_dispatch_recipe = Mock(return_value=16)
        with patch('module.island.production_planner.planner_idle_products', return_value=[]):
            self.task.run_planned_ranch(configs)
        self.task._planned_dispatch_recipe.assert_called_once_with(
            'ISLAND_RANCH_POST3', 'milk', 0, 'time_ranch3', 'WorkerJuu', extra_stocks={2604: 0})
        self.assertFalse(config.IslandRancher_Milk)


class MineProductMergeTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.island.island_mine_forest.logger'))
        self.task = IslandMineForest.__new__(IslandMineForest)
        copper = load_image(SELECT_COPPER_CHECK.file)
        self.task.device = SimpleNamespace(
            image=copper, screenshot=Mock(return_value=copper), click=Mock(),
            sleep=Mock(), stuck_record_add=Mock())
        self.task.config = SimpleNamespace(BUTTON_OFFSET=30)
        self.task.inventory_config = {'mine': {'items': [
            {'name': 'Copper', 'selection_check': SELECT_COPPER_CHECK},
            {'name': 'Aluminium', 'selection': SELECT_ALUMINIUM, 'selection_check': SELECT_ALUMINIUM_CHECK},
        ]}}
        self.task.post_close = Mock()
        self.task.post_open = Mock(return_value=True)
        self.task.back_to_postmanage_from_dispatch = Mock()
        self.task.appear_then_click = Mock(return_value=False)
        self.task.appear = Mock(side_effect=lambda button, **kwargs: button is ISLAND_SELECT_PRODUCT_CHECK)
        self.task.loop = Mock(return_value=iter((object(),)))

    def test_actual_ore_resources_require_correct_color_confirmation(self):
        self.assertTrue(self.task.is_product_confirmed('mine', 'Copper'))
        self.assertFalse(self.task.is_product_confirmed('mine', 'Aluminium'))
        self.assertFalse(self.task.is_product_confirmed('mine', 'unknown'))

    def test_framework_false_positive_exits_without_production(self):
        self.task.select_product = Mock(return_value=True)
        self.assertFalse(self.task.post_plant(object(), 'Aluminium', 'mine', 'time_mine1'))
        self.task.select_product.assert_called_once_with(SELECT_ALUMINIUM, SELECT_ALUMINIUM_CHECK)
        self.task.back_to_postmanage_from_dispatch.assert_called_once_with()
        self.task.device.click.assert_not_called()

    def test_product_stage_timeout_exits_without_blind_dispatch(self):
        self.task.loop.return_value = iter(())
        self.assertFalse(self.task.post_plant(object(), 'Aluminium', 'mine', 'time_mine1'))
        self.task.back_to_postmanage_from_dispatch.assert_called_once_with()
        self.task.device.click.assert_not_called()

    def test_color_confirmed_selection_keeps_dispatch_despite_framework_miss(self):
        image = load_image(SELECT_ALUMINIUM_CHECK.file)
        self.task.device.image = image
        self.task.device.screenshot.return_value = image
        self.task.select_product = Mock(return_value=False)
        button = object()
        self.task.posts = {'mine1': {'button': button}}
        with patch('module.island.island_mine_forest.Duration') as duration, \
                patch('module.island.island_mine_forest.current_time', return_value=datetime(2026, 10, 9, 12)):
            duration.return_value.ocr.return_value = timedelta(minutes=10)
            self.assertTrue(self.task.post_plant(button, 'Aluminium', 'mine', 'time_mine1'))
        self.assertIn(POST_ADD_ORDER, [call.args[0] for call in self.task.device.click.call_args_list])
        self.assertEqual(self.task.time_mine1, datetime(2026, 10, 9, 12, 10))
        self.assertEqual(self.task.posts['mine1']['state'], 'working')
        self.task.back_to_postmanage_from_dispatch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
