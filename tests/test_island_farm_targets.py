"""按作物设置的农田补种目标：解析配置文本与计算补种顺序。"""

import unittest
import json
from types import SimpleNamespace

from module.island.farm_targets import (
    crop_target,
    format_crop_targets,
    parse_crop_targets,
    plan_crops_to_restock,
)

FARM_CROPS = ['wheat', 'corn', 'rice', 'chinese_cabbage', 'potato', 'soybean', 'pasture', 'coffee_bean']


class ParseCropTargetsTest(unittest.TestCase):
    def test_empty_values(self):
        self.assertEqual(parse_crop_targets(None), {})
        self.assertEqual(parse_crop_targets(''), {})

    def test_lines_and_separators(self):
        text = 'corn: 200\nWheat : 80, chinese cabbage=40；coffee-bean：10\n# 注释\n'
        self.assertEqual(
            parse_crop_targets(text, known_crops=FARM_CROPS),
            {'corn': 200, 'wheat': 80, 'chinese_cabbage': 40, 'coffee_bean': 10},
        )

    def test_unknown_and_malformed_entries_are_skipped(self):
        text = 'corn: 200\napple: 50\npotato: lots\n: 5'
        self.assertEqual(parse_crop_targets(text, known_crops=FARM_CROPS), {'corn': 200})

    def test_round_trip(self):
        targets = {'wheat': 80, 'corn': 200}
        self.assertEqual(format_crop_targets(targets), 'corn: 200\nwheat: 80')
        self.assertEqual(parse_crop_targets(format_crop_targets(targets)), targets)


class PlanCropsToRestockTest(unittest.TestCase):
    def test_without_targets_matches_area_threshold(self):
        inventory = {'wheat': 700, 'corn': 100, 'rice': 300}
        self.assertEqual(plan_crops_to_restock(inventory, {}, 660), ['corn', 'rice'])

    def test_per_crop_targets_override_threshold(self):
        inventory = {'wheat': 700, 'corn': 100, 'rice': 300}
        targets = {'wheat': 1000, 'corn': 50}
        self.assertEqual(crop_target('corn', targets, 660), 50)
        self.assertEqual(crop_target('rice', targets, 660), 660)
        # 小麦 70%、水稻 45%，玉米已达自己的目标
        self.assertEqual(plan_crops_to_restock(inventory, targets, 660), ['rice', 'wheat'])

    def test_zero_target_never_restocks(self):
        inventory = {'rubber': 0, 'apple': 10}
        self.assertEqual(plan_crops_to_restock(inventory, {'rubber': 0}, 300), ['apple'])

    def test_skip_and_tie_break(self):
        inventory = {'carrot': 20, 'onion': 10, 'pineapple': 0}
        targets = {'carrot': 40, 'onion': 20}
        # 胡萝卜与洋葱同为 50%，库存少的洋葱在前；菠萝被忽略
        self.assertEqual(
            plan_crops_to_restock(inventory, targets, 100, skip={'pineapple'}),
            ['onion', 'carrot'],
        )


class FarmPlannerIntegrationTest(unittest.TestCase):
    """实际农场入口同时保留自动目标、手工目标以及季节/忽略保护。"""

    def make_farm(self, enabled=False, fingerprint='ready'):
        values = {
            'IslandPlan.IslandProductionPlanner.Enabled': enabled,
            'IslandPlan.IslandProductionPlanner.PlanFingerprint': fingerprint,
            'IslandPlan.IslandProductionPlanner.PlannerTargets': json.dumps({
                '2000': 10, '2001': 200, '2016': 200, '2021': 200,
                '4005': 200, '2004': 200, '4021': 200, '4019': 200,
            }),
        }
        inventory = {
            'farm': {'wheat': 20, 'corn': 40, 'rice': 50},
            'orchard': {'apple': 20, 'avocado': 0, 'pear': 0},
            'nursery': {'carrot': 20, 'pineapple': 0, 'asparagus': 0},
        }
        farm = SimpleNamespace(
            config=SimpleNamespace(cross_get=lambda key, default=None: values.get(key, default)),
            warehouse_inventory=lambda category: inventory[category],
            INVENTORY_CONFIG={category: {'threshold': 100} for category in inventory},
            inventory_counts={}, to_plant_lists={},
            crop_targets={'farm': {'wheat': 80, 'corn': 0}, 'orchard': {}, 'nursery': {}},
            ignore_avocado=True, ignore_pineapple=True, season_config=object(),
            _is_orchard_crop_in_season=lambda name: name != 'pear',
            _is_nursery_crop_in_season=lambda name: name != 'asparagus',
            _item_cn=lambda name: name,
        )
        return farm, values

    def check(self, farm):
        from module.island.island_farm import IslandFarm
        IslandFarm.check_inventory_and_prepare_lists(farm)

    def test_manual_targets_and_area_fallback(self):
        farm, _ = self.make_farm()
        self.check(farm)
        self.assertEqual(farm.to_plant_lists, {
            'farm': ['wheat', 'rice'], 'orchard': ['apple'], 'nursery': ['carrot'],
        })

    def test_active_plan_overrides_manual_targets_without_modifying_them(self):
        farm, values = self.make_farm(enabled=True)
        original = dict(values)
        self.check(farm)
        # 手工 corn=0 不截断自动订单需求，自动未需要 rice 不回落到手工阈值。
        self.assertEqual(farm.to_plant_lists, {
            'farm': ['corn'], 'orchard': ['apple'], 'nursery': ['carrot'],
        })
        self.assertEqual(farm.crop_targets['farm'], {'wheat': 80, 'corn': 0})
        self.assertEqual(values, original)

    def test_no_valid_plan_keeps_manual_targets(self):
        farm, _ = self.make_farm(enabled=True, fingerprint='')
        self.check(farm)
        self.assertEqual(farm.to_plant_lists['farm'], ['wheat', 'rice'])


if __name__ == '__main__':
    unittest.main()
