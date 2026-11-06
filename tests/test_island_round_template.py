"""用实际模板构造各生产任务，离线核对绑定及集中盘点物品映射。"""

import json
import unittest
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

from module.config.config import AzurLaneConfig
from module.device.device import Device
from module.island.data import DIC_ISLAND_RECIPE
from module.island.island_fishery import IslandFishery
from module.island.item_ids import resolve_item_id
from module.island.production_round import PRODUCTION_TASKS, collection_config
from module.island.round_recipe import recipe_for_product


class RoundTemplateSmokeTests(unittest.TestCase):
    def setUp(self):
        self.template = json.loads(Path('config/template.json').read_text(encoding='utf-8'))

    def build_runners(self, data):
        config = AzurLaneConfig('template')
        config.data = data
        sentinel = object()
        runners = {}
        with patch.object(AzurLaneConfig, 'load', side_effect=AssertionError('不得读用户配置')), \
                patch.object(AzurLaneConfig, 'save', side_effect=AssertionError('不得保存用户配置')), \
                patch.object(Device, '__init__', side_effect=AssertionError('不得构造真实设备')):
            for task, (module, name) in PRODUCTION_TASKS.items():
                cls = getattr(import_module(f'module.island.{module}'), name)
                runner = cls(config=collection_config(config, task), device=sentinel)
                if task == 'IslandRancher':
                    # 正常由 prepare_round 创建，此处仅构造以验证库存并集，不执行巡检。
                    runner._round_fishery = IslandFishery(config=runner.config, device=sentinel)
                self.assertEqual(runner.config.task.command, task)
                self.assertIs(runner.device, sentinel)
                runners[task] = runner
        return runners

    def test_all_nine_template_tasks_construct_and_resolve_inventory(self):
        runners = self.build_runners(self.template)
        self.assertEqual(len(runners), 9)
        all_items = set()
        for task, runner in runners.items():
            with self.subTest(task=task):
                items = runner.round_stock_items()
                self.assertTrue(items)
                all_items.update(resolve_item_id(item) for item in items)
        self.assertTrue({2000, 2001, 2008, 3000, 3001, 3002, 3003, 3004, 2600, 2602} <= all_items)

    def test_disabled_configuration_still_constructs_for_collection_only(self):
        for task in PRODUCTION_TASKS:
            self.template[task]['Scheduler']['Enable'] = False
            for group in self.template[task].values():
                for key in ('PostNumber', 'Positions'):
                    if key in group:
                        group[key] = 0
        self.template['IslandRancher']['IslandRancher'].update(Milk=False, Wool=False)
        runners = self.build_runners(self.template)
        for runner in runners.values():
            self.assertFalse(runner.config.Scheduler_Enable)
            for item in runner.round_stock_items():
                resolve_item_id(item)
        ranch = runners['IslandRancher']
        self.assertFalse(ranch.is_ranch_post_enabled('ISLAND_RANCH_POST3'))
        self.assertFalse(ranch.is_ranch_post_enabled('ISLAND_RANCH_POST4'))
        self.assertEqual(ranch._round_fishery.fishery_positions, 0)

    def test_food_catalog_has_unambiguous_flour_and_chicken_probe_recipes(self):
        runners = self.build_runners(self.template)
        covered = set()
        for runner in runners.values():
            if not hasattr(runner, 'chef_config'):
                continue
            for product in runner.name_to_config:
                recipe = DIC_ISLAND_RECIPE[recipe_for_product(product)]
                covered.update(set(recipe['commission_cost']) & {3004, 2602})
        self.assertEqual(covered, {3004, 2602})


if __name__ == '__main__':
    unittest.main()
