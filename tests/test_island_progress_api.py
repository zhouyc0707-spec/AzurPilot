"""季节进度接口的编号、实例隔离与只读契约。"""
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.api.island_progress import season_progress
from module.api.protocol import InstanceParams
from module.api.router import Router
from module.island.item_ids import LOCAL_TO_ITEM_ID
from module.island.seasonal_task_targets import format_imported_targets


class IslandProgressApiTests(unittest.TestCase):
    def setUp(self):
        self.data = {
            'Alas': {'Emulator': {'PackageName': 'com.bilibili.azurlane'}},
            'IslandPlan': {'IslandPlan': {'Season': 'winter'}},
            'IslandSeasonalProduction': {'IslandSeasonWinter': {
                'shiitake_chicken_soup': 5,
                'ImportedTargets': format_imported_targets({2014: 500, 3055: 10}),
            }},
        }
        self.configs = SimpleNamespace(root=Path('isolated'), read=Mock(return_value=(self.data, 'rev')))

    def test_missing_snapshot_includes_all_current_fields_with_unknown_quantities(self):
        with patch('module.island.seasonal_progress.load_progress', return_value=None) as load:
            result = season_progress(self.configs, 'demo')
        self.assertEqual(len(result['items']), 10)
        self.assertEqual(result['status'], 'missing')
        self.assertIsNone(result['updatedAt'])
        self.assertTrue(all(row['stock'] is None and row['inProduction'] is None for row in result['items']))
        load.assert_called_once_with('demo', 'winter', server='cn', root=Path('isolated'))

    def test_winter_local_ids_translate_without_conflating_planner_ids(self):
        old = copy.deepcopy(self.data)
        snapshot = {'status': 'observed', 'updated_at': '2026-11-10T14:00:00+08:00', 'items': {
            str(LOCAL_TO_ITEM_ID['shiitake_chicken_soup']): {'stock': 3, 'in_production': 2},
            str(LOCAL_TO_ITEM_ID['tea']): {'stock': 300, 'in_production': 100},
            str(LOCAL_TO_ITEM_ID['battery']): {'stock': 0, 'in_production': None},
        }}
        with patch('module.island.seasonal_progress.load_progress', return_value=snapshot):
            result = season_progress(self.configs, 'demo')
        rows = {row['itemId']: row for row in result['items']}
        self.assertEqual(rows[4051], {'itemId': 4051, 'key': 'shiitake_chicken_soup', 'stock': 3, 'inProduction': 2})
        self.assertEqual(rows[2014]['stock'], 300)
        self.assertEqual(rows[2014]['inProduction'], 100)
        self.assertEqual(rows[3055]['stock'], 0)
        self.assertIsNone(rows[3055]['inProduction'])
        self.assertEqual(self.data, old)

    def test_invalidated_snapshot_stays_historical(self):
        snapshot = {'status': 'invalidated', 'updated_at': '2026-11-10T14:00:00+08:00',
                    'items': {'2014': {'stock': 300, 'in_production': 100}}}
        with patch('module.island.seasonal_progress.load_progress', return_value=snapshot):
            result = season_progress(self.configs, 'demo')
        self.assertEqual(result['status'], 'invalidated')
        self.assertEqual(result['updatedAt'], snapshot['updated_at'])

    def test_instance_validation_precedes_reading_cache(self):
        self.configs.read.side_effect = ValueError('配置实例无效')
        with patch('module.island.seasonal_progress.load_progress') as load:
            with self.assertRaises(ValueError):
                season_progress(self.configs, '../other')
        load.assert_not_called()

    def test_route_is_read_only_and_takes_instance_parameters(self):
        router = Router(self.configs, None)
        route = router.methods['island.seasonProgress']
        self.assertFalse(route.mutates)
        self.assertIs(route.params, InstanceParams)
        with patch('module.api.island_progress.season_progress', return_value={'status': 'missing'}) as read:
            route.handler(InstanceParams(instance='demo'))
        read.assert_called_once_with(self.configs, 'demo')


if __name__ == '__main__':
    unittest.main()
