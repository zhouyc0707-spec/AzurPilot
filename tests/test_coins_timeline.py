"""凭证曲线合并已有黄币历史，保留原始观测和月初显示边界。"""

from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from module.api.legacy_stats_service import _ap_panel
from module.api.statistics_service import report
from module.statistics.opsi_month import get_coins_timeline


class CoinTimelineTests(unittest.TestCase):
    def setUp(self):
        self.data = {
            'coins_snapshots': [
                {'ts': '2026-11-17T10:08:44.368146', 'yellow_coins': 51656,
                 'purple_coins': 17171, 'source': 'cl1'},
            ],
            'yellow_coin_snapshots': [
                {'ts': '2026-11-17T10:13:26.712859', 'yellow_coin': 53221, 'source': 'dashboard'},
            ],
        }
        self.database = self.enterContext(patch('module.statistics.opsi_month.cl1_db'))
        self.database.get_stats.return_value = self.data

    def timeline(self):
        return get_coins_timeline(2026, 11, 'alas')

    def test_latest_dashboard_yellow_is_available_without_a_new_paired_read(self):
        original = deepcopy(self.data)
        points = self.timeline()
        self.assertEqual([row['yellow_coins'] for row in points], [51656, 53221])
        self.assertEqual(points[-1], {'ts': '2026-11-17T10:13:26.712859',
                                    'yellow_coins': 53221, 'source': 'dashboard'})
        self.database.get_stats.assert_called_once_with('alas', '2026-11')
        self.assertEqual(self.data, original)

    def test_missing_and_null_histories_remain_compatible(self):
        for data in ({}, {'coins_snapshots': None, 'yellow_coin_snapshots': None},
                     {'coins_snapshots': None, 'yellow_coin_snapshots': self.data['yellow_coin_snapshots']}):
            with self.subTest(data=data):
                self.database.get_stats.return_value = data
                expected = [53221] if data.get('yellow_coin_snapshots') else []
                self.assertEqual([row['yellow_coins'] for row in self.timeline()], expected)

    def test_invalid_legacy_times_do_not_define_a_month_start_boundary(self):
        self.data['coins_month_start_residue'] = [{'ts': '2026-11-01T00:01:00'}]
        self.data['coins_snapshots'].append({'ts': 'invalid', 'yellow_coins': 999})
        self.data['yellow_coin_snapshots'].append({'ts': 'invalid', 'yellow_coin': 1})
        self.assertEqual(self.timeline()[-1]['yellow_coins'], 53221)
        self.data['coins_month_start_residue'] = [{'ts': 'invalid'}]
        self.data['coins_snapshots'] = []
        self.assertEqual(self.timeline()[-1]['yellow_coins'], 53221)

    def test_same_time_prefers_original_paired_record_and_preserves_other_points(self):
        self.data['yellow_coin_snapshots'] = [
            {'ts': '2026-11-17 10:08:44.368146', 'yellow_coin': 99999, 'source': 'dashboard'},
            {'ts': '2026-11-17T10:00:00', 'yellow_coin': 50000},
            self.data['yellow_coin_snapshots'][0],
        ]
        points = self.timeline()
        self.assertEqual([row['yellow_coins'] for row in points], [50000, 51656, 53221])
        self.assertEqual(points[1], self.data['coins_snapshots'][0])
        self.assertNotIn('source', points[0])

    def test_zero_and_yellow_only_history_remain_visible(self):
        self.data['coins_snapshots'] = []
        self.data['yellow_coin_snapshots'].append(
            {'ts': '2026-11-17 10:14:00', 'yellow_coin': 0, 'source': 'dashboard'})
        self.assertEqual([row['yellow_coins'] for row in self.timeline()], [53221, 0])
        self.assertTrue(all('purple_coins' not in row for row in self.timeline()))

    def test_empty_and_incomplete_yellow_history_preserve_existing_records(self):
        for yellow in ([], [{'ts': '2026-11-17T10:15:00', 'yellow_coin': None},
                            {'yellow_coin': 0}]):
            with self.subTest(yellow=yellow):
                self.data['yellow_coin_snapshots'] = yellow
                self.assertEqual(self.timeline(), self.data['coins_snapshots'])

    def test_duplicate_yellow_time_uses_last_observation(self):
        self.data['coins_snapshots'] = []
        self.data['yellow_coin_snapshots'] += [
            {'ts': '2026-11-17 10:13:26.712859', 'yellow_coin': 53222, 'source': 'other'},
        ]
        self.assertEqual(self.timeline(), [
            {'ts': '2026-11-17 10:13:26.712859', 'yellow_coins': 53222, 'source': 'other'},
        ])

    def test_month_start_residue_stays_outside_the_display_curve(self):
        self.data['coins_month_start_residue'] = [
            {'ts': '2026-11-01T00:01:00', 'yellow_coins': 510, 'purple_coins': 8},
        ]
        self.data['coins_snapshots'] = [
            {'ts': '2026-11-01T00:03:00', 'yellow_coins': 520, 'purple_coins': 1},
        ]
        self.data['yellow_coin_snapshots'] = [
            {'ts': '2026-11-01T00:00:59', 'yellow_coin': 500},
            {'ts': '2026-11-01T00:01:00', 'yellow_coin': 510},
            {'ts': '2026-11-01T00:03:00', 'yellow_coin': 520},
            {'ts': '2026-11-01T00:04:00', 'yellow_coin': 530},
        ]
        original = deepcopy(self.data)
        self.assertEqual([row['yellow_coins'] for row in self.timeline()], [520, 530])
        self.data['coins_snapshots'] = []
        self.assertEqual(self.timeline(), [])
        self.data['coins_snapshots'] = original['coins_snapshots']
        self.assertEqual(self.data, original)

    def test_legacy_residue_extension_does_not_hide_independent_yellow(self):
        self.data['coins_snapshots'] = []
        self.data['coins_month_start_residue'] = {'custom_marker': 'preserve'}
        self.assertEqual(self.timeline()[-1]['yellow_coins'], 53221)

    def test_legacy_and_modern_reports_share_latest_yellow_and_real_purple_times(self):
        clock = self.enterContext(patch('module.api.legacy_stats_service.datetime', wraps=datetime))
        clock.now.return_value = datetime(2026, 11, 17, 10, 14)
        self.enterContext(patch('module.statistics.opsi_month.get_ap_timeline', return_value=[]))
        original = deepcopy(self.data)
        legacy = {row['key']: row['points'] for row in _ap_panel('alas')['series']}
        self.assertEqual(legacy['yellow_coins'][-1]['value'], 53221)
        self.assertEqual(legacy['purple_coins'], [
            {'time': '2026-11-17 10:08:44.368146', 'value': 17171.0},
        ])
        modern = report(SimpleNamespace(path=lambda _: None), 'alas', 'action', '2026-11', 7, 'month')
        series = {row['key']: row['points'] for row in modern['series']}
        self.assertEqual(series['yellow_coins'][-1]['v'], 53221)
        self.assertEqual(len(series['purple_coins']), 1)
        self.assertEqual(self.data, original)


if __name__ == '__main__':
    unittest.main()
