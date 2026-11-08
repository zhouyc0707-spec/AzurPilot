"""季节作物目标与已有补种、未知库存及种子选择的回归。"""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.island.island_farm import IslandFarm
from module.island.island import Island, ISLAND_SHOPPING_CHECK, ISLAND_SHOP_SEED_TAB_CHECK


class SeasonalFarmTests(unittest.TestCase):
    def make_farm(self, stock, target=40, regular=None, posts=None):
        values = {
            'IslandPlan.IslandPlan.Season': 'winter',
            'IslandSeasonalProduction.IslandSeasonWinter.green_jujube': target,
        }
        inventories = {'farm': {}, 'orchard': stock, 'nursery': {}}
        farm = SimpleNamespace(
            config=SimpleNamespace(cross_get=lambda key, default=None: values.get(key, default)),
            warehouse_inventory=lambda category: inventories[category],
            INVENTORY_CONFIG={key: {'threshold': 100} for key in inventories},
            inventory_counts={}, to_plant_lists={}, posts=posts or {},
            crop_targets={'farm': {}, 'orchard': regular or {}, 'nursery': {}},
            ignore_avocado=False, ignore_pineapple=False, season_config=object(),
            _is_orchard_crop_in_season=lambda name: name != 'pear',
            _is_nursery_crop_in_season=lambda name: True,
            _item_cn=lambda name: name,
        )
        return farm, values

    def test_regular_shortage_precedes_larger_seasonal_shortage(self):
        farm, _ = self.make_farm({'apple': 99, 'green_jujube': 0}, target=200)
        IslandFarm.check_inventory_and_prepare_lists(farm)
        self.assertEqual(farm.to_plant_lists['orchard'], ['apple', 'green_jujube'])

    def test_seasonal_target_replaces_implicit_area_floor(self):
        farm, _ = self.make_farm({'apple': 100, 'green_jujube': 50}, target=40)
        IslandFarm.check_inventory_and_prepare_lists(farm)
        self.assertEqual(farm.to_plant_lists['orchard'], [])

    def test_explicit_regular_target_keeps_priority_and_is_not_duplicated(self):
        farm, _ = self.make_farm({'apple': 99, 'green_jujube': 0}, target=200,
                                 regular={'green_jujube': 5})
        IslandFarm.check_inventory_and_prepare_lists(farm)
        self.assertEqual(farm.to_plant_lists['orchard'], ['green_jujube', 'apple'])
        self.assertEqual(farm.crop_targets['orchard'], {'green_jujube': 5})

    def test_zero_additional_target_preserves_existing_area_floor(self):
        farm, _ = self.make_farm({'green_jujube': 50}, target=0)
        IslandFarm.check_inventory_and_prepare_lists(farm)
        self.assertEqual(farm.to_plant_lists['orchard'], ['green_jujube'])

    def test_unknown_stock_does_not_become_zero(self):
        farm, _ = self.make_farm({'apple': 100}, target=40)
        IslandFarm.check_inventory_and_prepare_lists(farm)
        self.assertEqual(farm.to_plant_lists['orchard'], [])

    def test_unknown_working_product_defers_only_seasonal_additions(self):
        farm, _ = self.make_farm({'apple': 99, 'green_jujube': 0}, posts={
            'ISLAND_ORCHARD_POST1': {'state': 'working', 'crop': None},
        })
        IslandFarm.check_inventory_and_prepare_lists(farm)
        self.assertEqual(farm.to_plant_lists['orchard'], ['apple'])

    def test_inactive_season_target_is_not_used(self):
        farm, values = self.make_farm({'green_jujube': 150}, target=200)
        values['IslandPlan.IslandPlan.Season'] = 'autumn'
        IslandFarm.check_inventory_and_prepare_lists(farm)
        self.assertEqual(farm.to_plant_lists['orchard'], [])

    def test_unknown_summer_work_defers_unidentifiable_crop_even_with_explicit_target(self):
        farm, values = self.make_farm({})
        values.update({'IslandPlan.IslandPlan.Season': 'summer',
                       'IslandSeasonalProduction.IslandSeasonSummer.tomato': 40})
        farm.warehouse_inventory = lambda category: {'tomato': 0, 'carrot': 99} if category == 'nursery' else {}
        farm.crop_targets['nursery'] = {'tomato': 80}
        farm.INVENTORY_CONFIG['nursery']['items'] = [
            {'name': 'tomato', 'post_action': None}, {'name': 'carrot', 'post_action': object()},
        ]
        farm.posts = {'ISLAND_NURSERY_POST1': {'state': 'working', 'crop': None}}
        IslandFarm.check_inventory_and_prepare_lists(farm)
        self.assertEqual(farm.to_plant_lists['nursery'], ['carrot'])

    def test_summer_runner_registers_enabled_crops_in_warehouse_catalog(self):
        from module.config.config_generated import GeneratedConfig
        from module.island.round_warehouse import warehouse_catalog
        values = {'IslandPlan.IslandPlan.Season': 'summer',
                  'IslandSeasonalProduction.IslandSeasonSummer.tomato': 40,
                  'IslandSeasonalProduction.IslandSeasonSummer.cucumber': 50}
        config = GeneratedConfig()
        config.cross_get = lambda key, default=None: values.get(key, default)

        def init(main, **kwargs):
            main.config = kwargs['config']
            main.device = kwargs['device']

        with patch.object(Island, '__init__', init), \
                patch('module.island.island_season._global_season_config', None):
            farm = IslandFarm(config=config, device=Mock())
        for crop in ('tomato', 'cucumber'):
            self.assertIn(crop, farm.round_stock_items())
            self.assertIsNone(farm.name_to_config[crop]['template'])
        catalog = warehouse_catalog([farm])
        self.assertIn(4033, catalog['nursery'])
        self.assertIn(4035, catalog['nursery'])

    def test_post_scan_does_not_pass_missing_template_to_appear(self):
        farm = SimpleNamespace(INVENTORY_CONFIG={'nursery': {'items': [
            {'name': 'tomato', 'post_action': None},
        ]}}, appear=Mock())
        self.assertIsNone(IslandFarm.post_plant_check(farm, 'nursery'))
        farm.appear.assert_not_called()

    def test_standalone_defers_after_later_post_scan_without_rereading_stock(self):
        initial, _ = self.make_farm({'green_jujube': 0})
        farm = object.__new__(IslandFarm)
        farm.__dict__.update(initial.__dict__)
        farm.warehouse_inventory = Mock(side_effect=initial.warehouse_inventory)
        farm.config.task_delay = Mock()
        farm.farm_positions, farm.orchard_positions, farm.nursery_positions = 0, 2, 0
        farm.time_vars = {'farm': [], 'orchard': [None, None], 'nursery': []}
        farm.plant_config = {'orchard': {'plant_default': 0, 'default_crop': 'rubber'}}
        farm._resolve_default_crop = Mock(return_value='rubber')
        farm._inv_cn = str
        farm.ui_ensure = Mock()
        farm.post_plant = Mock()

        def scan(**kwargs):
            farm.posts = {
                'ISLAND_ORCHARD_POST1': {'state': 'working', 'crop': None},
                'ISLAND_ORCHARD_POST2': {'state': 'idle', 'crop': None},
            }
            farm._round_idle_posts = {'farm': [], 'nursery': [], 'orchard': [{
                'post_id': 'ISLAND_ORCHARD_POST2', 'button': Mock(), 'time_var_name': 'orchard_time_1',
            }]}

        farm.prepare_round = Mock(side_effect=scan)
        farm.run()
        farm.post_plant.assert_not_called()
        self.assertEqual(farm.warehouse_inventory.call_count, 3)


class NamedSeasonalSeedTests(unittest.TestCase):
    def test_missing_named_seed_never_clicks_or_opens_payment(self):
        main = object.__new__(Island)
        main.device = Mock()
        main.loop = lambda **kwargs: iter(range(2))
        main._item_cn = str
        main.appear = lambda button, **kwargs: button is not ISLAND_SHOPPING_CHECK
        main.set_buy_number = Mock()
        with patch('module.island.shop_selector._find_shop_card', return_value=None), \
                patch('module.island.island.Timer') as timer:
            timer.return_value.reached.return_value = True
            self.assertFalse(main.buy_shop_item(4034, 12, ISLAND_SHOP_SEED_TAB_CHECK, '番茄种子'))
        main.device.click.assert_not_called()
        main.set_buy_number.assert_not_called()

    def test_tomato_seed_uses_exact_official_shop_identity(self):
        main = object.__new__(Island)
        main.device = Mock()
        main.loop = lambda **kwargs: iter(range(2))
        main._item_cn = str
        opened = [False]
        main.appear = lambda button, **kwargs: (opened[0] if button is ISLAND_SHOPPING_CHECK else True)
        card = object()
        main.device.click.side_effect = lambda button: opened.__setitem__(0, True)
        main.set_buy_number = Mock()
        with patch('module.island.shop_selector._find_shop_card', return_value=card) as find, \
                patch('module.island.island.Timer') as timer:
            timer.return_value.reached.return_value = True
            # 在设置数量前结束，后续付款逻辑由原商店回归覆盖。
            main.set_buy_number.side_effect = RuntimeError('selected')
            with self.assertRaisesRegex(RuntimeError, 'selected'):
                main.buy_shop_item(4034, 12, ISLAND_SHOP_SEED_TAB_CHECK, '番茄种子')
        find.assert_called_once_with(main, 411027)
        main.device.click.assert_called_once_with(card)


if __name__ == '__main__':
    unittest.main()
