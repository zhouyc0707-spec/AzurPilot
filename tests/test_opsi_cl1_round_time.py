"""侵蚀一计时回退兼容：原始历史隔离及总库存取，不连接真实设备。"""

import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from module.persistence.database import BusinessDatabase, _directory
from module.statistics import opsi_runtime
from module.statistics.ship_exp_stats import ShipExpStats


class RoundTimeCompatibilityTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='cl1-round-compat-', ignore_cleanup_errors=True)
        self.addCleanup(directory.cleanup)
        self.config_directory = Path(directory.name) / 'config'
        self.config_directory.mkdir()
        token = _directory.set(self.config_directory)
        self.addCleanup(_directory.reset, token)
        self.store = BusinessDatabase(self.config_directory)
        self.stats = ShipExpStats(instance_name='leveling', store=self.store)

    def reload(self, instance='leveling'):
        return ShipExpStats(instance_name=instance, store=BusinessDatabase(self.config_directory))

    @staticmethod
    def full_cycle_group():
        return {
            'scope': 'full_cycle',
            'samples': [160.5 + index for index in range(22)],
            'round_counts': [0.5 if index % 2 else 2.0 for index in range(22)],
            'average': 171.13,
            'legacy': {'samples': [70.0, 90.0], 'average': 80.0, 'extra': [None, False, {'v': 3}]},
            'custom_metadata': {'keep': True, 'notes': ['原始权重与旧口径历史']},
        }

    def seed_full_cycle(self):
        original = self.full_cycle_group()
        self.stats.data['round_times'] = copy.deepcopy(original)
        self.stats.data['battle_times'] = {'samples': [19.0], 'average': 19.0}
        self.stats._save()
        return original

    def test_first_search_sample_preserves_entire_full_cycle_group_after_reload(self):
        original = self.seed_full_cycle()
        self.stats = self.reload()
        self.assertEqual(self.stats.data['round_times'], original)
        self.assertEqual(self.stats.get_average_round_time(), original['average'])

        self.stats.record_round_time(100.0)
        group = self.reload().data['round_times']
        self.assertEqual(group, {
            'scope': 'search_cycle', 'samples': [100.0], 'average': 100.0, 'legacy': original,
        })
        self.assertEqual(self.reload().data['battle_times'], {'samples': [19.0], 'average': 19.0})
        self.assertEqual(self.reload().get_exp_per_hour(), 22464.0)
        with sqlite3.connect(self.store.path) as connection:
            self.assertEqual(connection.execute('PRAGMA quick_check').fetchone()[0], 'ok')

    def test_recent_100_search_samples_do_not_trim_or_renest_archived_full_cycle(self):
        original = self.seed_full_cycle()
        for duration in range(1, 126):
            self.stats.record_round_time(duration)
        group = self.reload().data['round_times']
        self.assertEqual(group['scope'], 'search_cycle')
        self.assertEqual(group['samples'], list(range(26, 126)))
        self.assertEqual(group['average'], 75.5)
        self.assertEqual(group['legacy'], original)
        self.assertEqual(len(group['legacy']['samples']), 22)
        self.assertEqual(len(group['legacy']['round_counts']), 22)

    def test_existing_search_samples_follow_upstream_without_reclassification(self):
        group = {'samples': [100.0, 200.0], 'average': 150.0, 'custom_metadata': {'keep': True}}
        self.stats.data['round_times'] = copy.deepcopy(group)
        self.stats._save()
        self.stats = self.reload()
        self.stats.record_round_time(300.0)
        group['samples'].append(300.0)
        group['average'] = 200.0
        self.assertEqual(self.reload().data['round_times'], group)

    def test_existing_search_window_still_keeps_recent_100(self):
        self.stats.data['round_times'] = {'samples': list(range(1, 101)), 'average': 50.5}
        self.stats._save()
        self.stats.record_round_time(101)
        self.assertEqual(self.reload().data['round_times'], {
            'samples': list(range(2, 102)), 'average': 51.5,
        })

    def test_first_sample_for_new_instance_keeps_upstream_structure(self):
        self.stats.record_round_time(80.126)
        self.assertEqual(self.reload().data['round_times'], {'samples': [80.13], 'average': 80.13})

    def test_compatibility_change_does_not_touch_another_instance(self):
        original = self.seed_full_cycle()
        another = ShipExpStats(instance_name='other', store=self.store)
        another.data['round_times'] = copy.deepcopy(original)
        another._save()
        self.stats.record_round_time(100)
        self.assertEqual(self.reload('other').data['round_times'], original)
        self.assertEqual(self.reload().data['round_times']['legacy'], original)

    def test_runtime_odd_battles_close_search_cycle_and_even_battle_only_counts(self):
        original = self.seed_full_cycle()
        config = SimpleNamespace(config_name='leveling')
        now = 1000.0
        with patch('module.statistics.cl1_database.db.async_increment_battle_count') as monthly, \
                patch('module.statistics.ship_exp_stats.get_ship_exp_stats', return_value=self.stats), \
                patch('module.statistics.opsi_runtime.time', SimpleNamespace(time=lambda: now)):
            started = opsi_runtime.record_cl1_auto_search_battle(config, 1, 0)
            self.assertEqual(started, 1000.0)
            self.assertEqual(self.reload().data['round_times'], original)
            started = opsi_runtime.record_cl1_auto_search_battle(config, 2, started)
            self.assertEqual(started, 1000.0)
            self.assertEqual(self.reload().data['round_times'], original)
            now = 1120.0
            started = opsi_runtime.record_cl1_auto_search_battle(config, 3, started)
        self.assertEqual(started, 1120.0)
        self.assertEqual(monthly.call_count, 3)
        self.assertEqual(self.reload().data['round_times'], {
            'scope': 'search_cycle', 'samples': [120.0], 'average': 120.0, 'legacy': original,
        })


if __name__ == '__main__':
    unittest.main()
