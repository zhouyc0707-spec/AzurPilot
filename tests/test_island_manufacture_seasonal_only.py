"""关闭常规制造时仅补季节有限目标，复用真实排产与共享扣账。"""

import unittest
from unittest.mock import Mock, patch

from module.island.island_manufacture import IslandManufacture, PRODUCT_LINES
from module.island.item_ids import LOCAL_TO_ITEM_ID
from tests.test_island_imported_target_execution import make_runner
from tests.test_island_seasonal_shop_targets import prepare_shop


class ManufactureSeasonalOnlyTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.island.island_shop_base.logger'))
        self.enterContext(patch('module.island.island_manufacture.logger'))
        self.enterContext(patch('module.island.production_recipe.ProductionRecipeMixin._open_product_page',
                                return_value=True))

    def factory(self, targets, stock=None, *, enabled=False, **settings):
        factory = make_runner(IslandManufacture, targets, **settings)
        original_get = factory.config.cross_get
        factory.config.cross_get = lambda path, default=None: (
            enabled if path == 'IslandManufacture.Scheduler.Enable' else original_get(path, default))
        # 调度器可临时投影启用；执行模式必须仍以保存的开关为准。
        factory.config.Scheduler_Enable = True
        factory.config.cross_set = Mock(side_effect=AssertionError('不得改写持久开关或生产设置'))
        return prepare_shop(factory, stock)

    def forbid_fallback(self, factory):
        for name in ('schedule_wood_processing', 'schedule_electronic_processing',
                     'schedule_industrial_production', 'schedule_handmade'):
            setattr(factory, name, Mock(side_effect=AssertionError('不得执行常驻或默认候选')))

    def test_disabled_run_produces_only_four_line_targets_and_preserves_settings(self):
        factory = self.factory({3117: 2, 3054: 3, 3047: 4, 3039: 5},
                               {'paper': 100, 'iron_nail': 100, 'cloth': 100})
        defaults = {'WoodProcessing': 'file_cabinet', 'ElectronicProcessing': 'filter_element',
                    'Industrial': 'iron_nail', 'Handmade': 'leather'}
        for line, name in defaults.items():
            setattr(factory.config, f'{line}_Products', name)
        self.forbid_fallback(factory)
        factory.run()
        self.assertEqual(factory.selections, ['ornamental_painting', 'clock', 'cutlery', 'aroma_sachet'])
        self.assertEqual([call.args[0] for call in factory.confirm_food_dispatch.call_args_list], [2, 3, 4, 5])
        self.assertFalse(factory.config.cross_get('IslandManufacture.Scheduler.Enable'))
        for line, name in defaults.items():
            self.assertEqual(getattr(factory.config, f'{line}_Products'), name)
            self.assertEqual(getattr(factory.config, f'{line}_Positions'), 2)
        factory.config.cross_set.assert_not_called()
        factory.warehouse_filter.assert_not_called()
        factory.post_check.assert_not_called()

    def test_target_met_by_stock_and_both_kinds_of_inflight_stops(self):
        factory = self.factory({3054: 10}, {'clock': 6})
        factory.post_check_meal['clock'] = 3
        factory.island_round.production_started('clock', 1)
        self.forbid_fallback(factory)
        factory.run()
        self.assertEqual(factory.selections, [])
        factory.confirm_food_dispatch.assert_not_called()

    def test_partial_deficit_dispatches_once_and_second_run_does_not_fill_spare_posts(self):
        factory = self.factory({3054: 10}, {'clock': 6})
        factory.post_check_meal['clock'] = 3
        self.forbid_fallback(factory)
        factory.run()
        factory.run()
        self.assertEqual(factory.selections, ['clock'])
        factory.confirm_food_dispatch.assert_called_once()
        self.assertEqual(factory.confirm_food_dispatch.call_args.args[0], 1)
        self.assertEqual(factory.island_round.in_production[LOCAL_TO_ITEM_ID['clock']], 1)

    def test_zero_or_no_targets_never_runs_defaults(self):
        for targets in ({}, {3054: 0, 3039: 0}):
            with self.subTest(targets=targets):
                factory = self.factory(targets)
                self.forbid_fallback(factory)
                factory.run()
                self.assertEqual(factory.selections, [])
                factory.confirm_food_dispatch.assert_not_called()

    def test_zero_positions_remain_closed_even_with_positive_target(self):
        factory = self.factory({3039: 10}, {'cloth': 100}, Handmade_Positions=0)
        self.forbid_fallback(factory)
        factory.run()
        self.assertEqual(factory.selections, [])
        self.assertEqual(factory.config.Handmade_Positions, 0)

    def test_unknown_product_stock_does_not_become_zero_deficit(self):
        factory = self.factory({3054: 10})
        factory.island_round.observe('clock', None)
        self.forbid_fallback(factory)
        factory.run()
        self.assertEqual(factory.selections, [])
        self.assertIsNone(factory.island_round.get('clock'))
        factory._prepare_recipe_page.assert_not_called()

    def test_unknown_or_missing_material_does_not_create_material_chain_or_fallback(self):
        for stock in (None, 0):
            with self.subTest(stock=stock):
                factory = self.factory({3039: 10}, {'cloth': stock})
                self.forbid_fallback(factory)
                factory.run()
                self.assertEqual(factory.selections, [])
                self.assertEqual(factory.island_round.get('cloth'), stock)
                factory._prepare_recipe_page.assert_not_called()

    def test_no_available_character_leaves_target_pending_without_default_dispatch(self):
        factory = self.factory({3054: 10})
        factory._prepare_recipe_page.return_value = False
        self.forbid_fallback(factory)
        factory.run()
        self.assertEqual(factory.selections, [])
        factory.confirm_food_dispatch.assert_not_called()

    def test_unknown_existing_production_blocks_its_line_without_fallback(self):
        factory = self.factory({3054: 10})
        factory._unknown_working_categories.add('electronic_processing')
        self.forbid_fallback(factory)
        factory.run()
        self.assertEqual(factory.selections, [])
        factory._prepare_recipe_page.assert_not_called()

    def test_enabled_manufacture_retains_all_original_line_entrypoints(self):
        factory = self.factory({3054: 10}, enabled=True)
        entries = []
        for category in PRODUCT_LINES.values():
            setattr(factory, f'schedule_{category}', Mock(side_effect=lambda key=category: entries.append(key)))
        factory.run()
        self.assertEqual(entries, list(PRODUCT_LINES.values()))


if __name__ == '__main__':
    unittest.main()
