"""运行期完整保留凭证点，离线显式抽稀时验证上游边界。"""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from module.statistics.cl1_database import (
    COINS_EXACT_DAYS,
    COINS_HISTORY_VERSION,
    Cl1Database,
    thin_coins_snapshots,
)


@pytest.fixture
def coins_database(tmp_path):
    database = Cl1Database(db_path=tmp_path / 'cl1_data.db')
    # 每个临时库拥有自己的迁移状态，避免进程级缓存跨用例跳过迁移。
    database._coins_history_checked = set()
    database._coins_cleanup_checked = set()
    return database


def _at(days_ago: int, hour: int, minute: int) -> dict:
    stamp = (datetime.now() - timedelta(days=days_ago)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return {'ts': stamp.isoformat(), 'yellow_coins': f'{days_ago}d{hour:02d}:{minute:02d}'}


def test_thin_coins_snapshots_keeps_recent_and_thins_older():
    snapshots = [
        _at(0, 10, 5), _at(0, 10, 55),        # 3 天内：两条都留
        _at(COINS_EXACT_DAYS + 2, 9, 10),     # 超期同小时
        _at(COINS_EXACT_DAYS + 2, 9, 50),     # 超期同小时的末条，应取代上一条
        _at(COINS_EXACT_DAYS + 2, 11, 30),    # 另一个小时，独立保留
    ]

    kept = [item['yellow_coins'] for item in thin_coins_snapshots(snapshots, enabled=True)]

    assert '0d10:05' in kept and '0d10:55' in kept
    assert '5d09:50' in kept and '5d09:10' not in kept
    assert '5d11:30' in kept
    assert len(kept) == 4


def test_thin_coins_snapshots_stays_bounded_for_repeated_hours():
    snapshots = [_at(30, 9, minute) for minute in (5, 15, 25, 35, 45, 55)]

    kept = thin_coins_snapshots(snapshots, enabled=True)

    assert len(kept) == 1
    assert kept[0]['yellow_coins'] == '30d09:55'


def test_runtime_default_preserves_every_original_old_point():
    snapshots = [_at(30, 9, minute) for minute in (5, 15, 25, 35, 45, 55)]
    assert thin_coins_snapshots(snapshots) == snapshots
    assert len(thin_coins_snapshots(snapshots)) == 6


def test_runtime_default_sorts_backfilled_points_without_discarding_them():
    snapshots = [_at(30, 9, minute) for minute in (55, 5, 45, 15, 35, 25)]
    assert thin_coins_snapshots(snapshots) == sorted(snapshots, key=lambda point: point['ts'])
    assert len(thin_coins_snapshots(snapshots)) == 6


def test_database_write_preserves_all_old_points_and_custom_fields(coins_database):
    original = [
        {'ts': f'2026-08-12T09:{minute:02d}:00', 'yellow_coins': minute, 'purple_coins': 10}
        for minute in (5, 15, 25, 35, 45, 55)
    ]
    coins_database.save_stats('probe', '2026-08', {
        'coins_snapshots': original,
        'coins_history_version': COINS_HISTORY_VERSION,
        'commission_income_entries': [{'coins': 200, 'source': 'custom'}],
    })

    with patch('module.statistics.cl1_database.datetime', wraps=datetime) as clock:
        clock.now.return_value = datetime(2026, 8, 20, 10)
        coins_database.add_coins_snapshot('probe', 100, 11)

    stored = coins_database.get_stats('probe', '2026-08')
    assert stored['coins_snapshots'][:6] == original
    assert len(stored['coins_snapshots']) == 7
    assert stored['commission_income_entries'] == [{'coins': 200, 'source': 'custom'}]


def test_history_backfill_keeps_every_same_hour_point(coins_database):
    original = {'ts': '2026-08-12T09:55:00', 'yellow_coins': 55, 'source': 'custom'}
    coins_database.save_stats('probe', '2026-08', {
        'coins_snapshots': [original], 'custom_marker': 'keep',
    })
    timeline = [
        {'ts': f'2026-08-12T09:{minute:02d}:00', 'yellow_coin': minute, 'purple_coin': 10}
        for minute in (5, 15, 25, 35, 45, 55)
    ]
    with patch('module.statistics.resource_stats.get_resource_timeline', return_value=timeline):
        with coins_database._stats_transaction() as conn:
            coins_database.ensure_coins_history('probe', conn)

    stored = coins_database.get_stats('probe', '2026-08')
    assert len(stored['coins_snapshots']) == 6
    assert stored['coins_snapshots'][-1] == original
    assert [point['yellow_coins'] for point in stored['coins_snapshots']] == [5, 15, 25, 35, 45, 55]
    assert stored['custom_marker'] == 'keep'


def test_month_start_cleanup_archives_originals_without_losing_data(coins_database):
    previous = {'ts': '2026-08-31T23:59:00', 'yellow_coins': 500, 'purple_coins': 8}
    originals = [
        {'ts': '2026-09-01T00:01:00', 'yellow_coins': 500, 'purple_coins': 8},
        {'ts': '2026-09-01T00:02:00', 'yellow_coins': 510, 'purple_coins': 8},
        {'ts': '2026-09-01T00:03:00', 'yellow_coins': 520, 'purple_coins': 1},
    ]
    coins_database.save_stats('probe', '2026-08', {'coins_snapshots': [previous]})
    coins_database.save_stats('probe', '2026-09', {
        'coins_snapshots': originals, 'custom_marker': 'keep',
    })
    for _ in range(2):
        with coins_database._stats_transaction() as conn:
            coins_database.ensure_coins_cleanup('probe', conn)

    stored = coins_database.get_stats('probe', '2026-09')
    assert stored['coins_snapshots'] == originals[2:]
    assert stored['coins_month_start_residue'] + stored['coins_snapshots'] == originals
    assert stored['custom_marker'] == 'keep'
    assert coins_database.get_stats('probe', '2026-08')['coins_snapshots'] == [previous]


def test_new_month_residue_is_recorded_outside_display_curve(coins_database):
    previous = {'ts': '2026-08-31T23:59:00', 'yellow_coins': 500, 'purple_coins': 8}
    coins_database.save_stats('probe', '2026-08', {'coins_snapshots': [previous]})
    coins_database.save_stats('probe', '2026-09', {'coins_history_version': COINS_HISTORY_VERSION})
    with patch('module.statistics.cl1_database.datetime', wraps=datetime) as clock:
        clock.now.return_value = datetime(2026, 9, 1, 0, 1)
        coins_database.add_coins_snapshot('probe', 510, 8)
        coins_database.add_coins_snapshot('probe', 510, 8)
        clock.now.return_value = datetime(2026, 9, 1, 0, 3)
        coins_database.add_coins_snapshot('probe', 520, 1)

    stored = coins_database.get_stats('probe', '2026-09')
    assert [point['yellow_coins'] for point in stored['coins_month_start_residue']] == [510]
    assert [point['purple_coins'] for point in stored['coins_month_start_residue']] == [8]
    assert [point['purple_coins'] for point in stored['coins_snapshots']] == [1]
