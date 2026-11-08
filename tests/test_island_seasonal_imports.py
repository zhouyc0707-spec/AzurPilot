"""任务导入的编号边界、目标合并、接口校验与并发编辑保护。"""

from contextlib import nullcontext
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import tempfile

from module.api.config_service import ConfigService
from module.api.protocol import ApiError
from module.island.seasonal_targets import seasonal_targets
from module.island.seasonal_task_targets import (
    MAX_TARGET, build_target_updates, format_imported_targets, imported_item_catalog,
    merge_imported_targets, parse_imported_targets,
)
from module.island_planner.data import DIC_ISLAND_ITEM, DIC_ISLAND_SEASON, DIC_ISLAND_TASK


ROOT = Path(__file__).resolve().parents[1]
GROUP = 'IslandSeasonalProduction.IslandSeasonWinter'


def config_for(imported=None, season='winter', **values):
    values['IslandPlan.IslandPlan.Season'] = season
    values[f'{GROUP}.ImportedTargets'] = format_imported_targets(imported or {})
    return SimpleNamespace(cross_get=lambda path, default=None: values.get(path, default)), values


class SeasonalImportTests(unittest.TestCase):
    def test_all_exported_season_task_items_have_confirmed_category(self):
        expected = {item for season in DIC_ISLAND_SEASON.values() for task in season['task_list']
                    for item in (DIC_ISLAND_TASK[task].get('target') or {}) if item in DIC_ISLAND_ITEM}
        self.assertFalse(expected - imported_item_catalog().keys())
        self.assertEqual(imported_item_catalog()[3055], ('battery', 'electronic_processing'))
        self.assertEqual(imported_item_catalog()[2521], ('freshwater_fish_meat', 'fish_meat'))
        self.assertEqual(imported_item_catalog()[2705], ('Silver', 'mine'))

    def test_winter_planner_ids_are_not_local_legacy_ids(self):
        catalog = imported_item_catalog()
        self.assertEqual(catalog[4047], ('sugar_tangerine', 'orchard'))
        self.assertEqual(catalog[4051], ('shiitake_chicken_soup', 'restaurant'))
        self.assertEqual(catalog[4055], ('plum_blossom_sachet', 'handmade'))
        self.assertNotIn(4043, catalog)  # 原料梅花没有生产岗位。

    def test_import_merges_max_preserves_unrelated_and_is_idempotent(self):
        config, values = config_for({2014: 700, 3054: 20, 3039: 0})
        changes = build_target_updates(config, {2014: 500, 3039: 10, 3055: 10}, 'winter')
        self.assertEqual(set(changes), {f'{GROUP}.ImportedTargets'})
        self.assertEqual(parse_imported_targets(changes[f'{GROUP}.ImportedTargets']), {
            2014: 700, 3054: 20, 3039: 10, 3055: 10,
        })
        values.update(changes)
        self.assertEqual(build_target_updates(config, {2014: 500, 3039: 10, 3055: 10}, 'winter'), {})
        self.assertEqual(build_target_updates(config, {}, 'winter'), {})

    def test_limited_product_uses_existing_field_and_deduplicates(self):
        config, values = config_for({4051: 150})
        values[f'{GROUP}.shiitake_chicken_soup'] = 100
        changes = build_target_updates(config, {4051: 50}, 'winter')
        self.assertEqual(changes[f'{GROUP}.shiitake_chicken_soup'], 150)
        self.assertEqual(parse_imported_targets(changes[f'{GROUP}.ImportedTargets']), {})

    def test_original_stock_target_is_not_subtracted_twice(self):
        config, _ = config_for()
        changes = build_target_updates(config, {2014: 500}, 'winter')
        self.assertEqual(parse_imported_targets(changes[f'{GROUP}.ImportedTargets'])[2014], 500)

    def test_runtime_reads_only_current_season_category_and_positive_values(self):
        config, values = config_for({2014: 500, 2017: 250, 3055: 10, 3054: 0})
        self.assertEqual(seasonal_targets(config, 'nursery'), {'tea': 500})
        self.assertEqual(seasonal_targets(config, 'electronic_processing'), {'battery': 10})
        self.assertEqual(seasonal_targets(config, 'restaurant'), {})
        values['IslandPlan.IslandPlan.Season'] = 'spring'
        self.assertEqual(seasonal_targets(config, 'nursery'), {})

    def test_mismatched_season_or_unknown_item_rejects_entire_result(self):
        config, values = config_for({2014: 900})
        previous = dict(values)
        for targets, season in (({2014: 500}, 'spring'), ({2014: 500, 99999: 10}, 'winter'),
                                ({2014: -1}, 'winter'), ({True: 5}, 'winter'), ({4025: 20}, 'winter')):
            with self.subTest(targets=targets, season=season), self.assertRaises(ValueError):
                build_target_updates(config, targets, season)
            self.assertEqual(values, previous)

    def test_json_rejects_invalid_counts_names_duplicate_keys_and_raw_items(self):
        for text in ('[]', 'null', '{', '{"茶叶 (2014)":1,"茶叶 (2014)":2}',
                     '{"错误名称 (2014)":1}', '{"冬菇 (4045)":100}', '{"2014":1}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_imported_targets(text)
        for value in (-1, 1.5, True, None, '10', MAX_TARGET + 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_imported_targets(json.dumps({'茶叶 (2014)': value}))
        self.assertEqual(parse_imported_targets('{"茶叶 (2014)":0}'), {2014: 0})

    def test_api_rejects_corrupted_or_wrong_season_imported_targets(self):
        service = object.__new__(ConfigService)
        service.args = json.loads((ROOT / 'module/config/argument/args.json').read_text(encoding='utf-8'))
        path = f'{GROUP}.ImportedTargets'
        self.assertEqual(service.validate(path, format_imported_targets({2014: 500})), path.split('.'))
        for text in ('[]', '{"茶叶 (2014)":-1}', format_imported_targets({4025: 50})):
            with self.subTest(text=text), self.assertRaises(ApiError):
                service.validate(path, text)

    def test_merge_reloads_latest_values_while_holding_same_transaction(self):
        config, values = config_for({2014: 100})
        events = []
        config.config_name = 'isolated_test'
        config.load = lambda **kwargs: (events.append('load'), values.update({
            f'{GROUP}.ImportedTargets': format_imported_targets({2014: 800}),
        }))
        config.cross_set_many = lambda changes: (events.append('save'), values.update(changes))
        with patch('module.config.transaction.config_transaction', return_value=nullcontext()) as transaction:
            result = merge_imported_targets(config, {2014: 500, 3055: 10}, 'winter')
        self.assertEqual(events, ['load', 'save'])
        self.assertEqual(transaction.call_count, 1)
        self.assertEqual(parse_imported_targets(result[f'{GROUP}.ImportedTargets']), {2014: 800, 3055: 10})

    def test_mid_scan_season_change_prevents_writes(self):
        config, values = config_for()
        config.config_name = 'isolated_test'
        config.load = lambda **kwargs: values.update({'IslandPlan.IslandPlan.Season': 'spring'})
        config.cross_set_many = lambda changes: self.fail('不应写入其他季节')
        with patch('module.config.transaction.config_transaction', return_value=nullcontext()), self.assertRaises(ValueError):
            merge_imported_targets(config, {2014: 500}, 'winter')

    def test_real_config_transaction_preserves_newer_higher_target_and_other_settings(self):
        from module.config.config import AzurLaneConfig, name_to_function

        disk = json.loads((ROOT / 'config/template.json').read_text(encoding='utf-8'))
        disk['IslandPlan']['IslandPlan']['Season'] = 'winter'
        worker = AzurLaneConfig.__new__(AzurLaneConfig)
        worker.bound = {}
        worker.config_name = 'season_test'
        worker.data = copy.deepcopy(disk)
        worker._loaded_data = copy.deepcopy(disk)
        worker.modified = {}
        worker.overridden = {}
        worker.auto_update = True
        worker.task = name_to_function('IslandSeasonalProduction')
        worker.read_file = Mock(side_effect=lambda name: copy.deepcopy(disk))

        def save(name, data):
            disk.clear()
            disk.update(copy.deepcopy(data))

        worker.write_file = Mock(side_effect=save)
        worker.bind(worker.task)
        disk['IslandSeasonalProduction']['IslandSeasonWinter']['ImportedTargets'] = format_imported_targets({2014: 800})
        disk['IslandRestaurant']['IslandRestaurant']['MealNumber1'] = 123
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'season_test.json')
            with patch('module.config.utils.filepath_config', return_value=path), \
                    patch('module.config.config.filepath_config', return_value=path), \
                    patch.object(AzurLaneConfig, 'config_override'):
                merge_imported_targets(worker, {2014: 500, 3055: 10}, 'winter')
        worker.write_file.assert_called_once()
        self.assertEqual(parse_imported_targets(
            disk['IslandSeasonalProduction']['IslandSeasonWinter']['ImportedTargets']), {2014: 800, 3055: 10})
        self.assertEqual(disk['IslandRestaurant']['IslandRestaurant']['MealNumber1'], 123)
        self.assertNotIn('Scheduler', disk['IslandSeasonalProduction'])

    def test_tool_registration_and_instance_mutex(self):
        from module.api.runtime_service import RuntimeService
        from module.submodule.utils import get_available_func

        self.assertIn('IslandSeasonTargetScan', get_available_func())
        service = RuntimeService.__new__(RuntimeService)
        manager = SimpleNamespace(alive=True, start=Mock())
        service.manager = Mock(return_value=manager)
        with self.assertRaises(ApiError) as failure:
            service.start('isolated', 'IslandSeasonTargetScan')
        self.assertEqual(failure.exception.code, 'INSTANCE_RUNNING')
        manager.start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
