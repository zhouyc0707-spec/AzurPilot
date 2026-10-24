"""临时总库验证资源观察边界及战后合并保存，不连接真实配置或游戏。"""
import copy
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.persistence.database import BusinessDatabase, ClosingConnection, create_schema
from module.statistics import resource_flow as flow
from module.statistics import ship_exp_stats as ship


NOW = datetime(2026, 10, 24, 12)


class FixedDate(date):
    @classmethod
    def today(cls):
        return NOW.date()


class TemporaryDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def make_store(self, name):
        directory = self.directory / name
        directory.mkdir()
        store = BusinessDatabase(directory)
        with closing(sqlite3.connect(store.path)) as connection:
            create_schema(connection)
            connection.commit()
        store._ready = True
        return store


class ResourceObserveSmallWriteTests(TemporaryDatabaseTests):
    def setUp(self):
        super().setUp()
        self.config = SimpleNamespace(config_name='fixture', task=SimpleNamespace(command='Main'))

    @staticmethod
    def connect_factory(store, *, legacy=False, observed_queries=None):
        class Connection(ClosingConnection):
            def execute(self, sql, parameters=()):
                if 'SELECT COALESCE(SUM(amount)' in sql:
                    if observed_queries is not None:
                        observed_queries.append((sql, parameters))
                    if legacy:
                        # 以原版 SQL 作差分对照，其他事务、归因和游标代码保持相同。
                        sql = sql.replace(' AND ts>=?', '', 1)
                        parameters = (*parameters[:2], *parameters[3:])
                return super().execute(sql, parameters)
        return lambda: store.connect(factory=Connection)

    def snapshot(self, store):
        with store.transaction(write=False) as connection:
            balances = [tuple(row) for row in connection.execute(
                'SELECT * FROM resource_balances ORDER BY instance,resource')]
            entries = [tuple(row) for row in connection.execute('SELECT * FROM resource_flows ORDER BY id')]
        return balances, entries

    def exercise_observations(self, store, legacy):
        clock = [NOW - timedelta(seconds=1)]
        keys = iter(f'event-{index}' for index in range(100))
        with patch.object(flow, 'connect', self.connect_factory(store, legacy=legacy)), \
                patch.object(flow, 'now', side_effect=lambda: clock[0]), \
                patch.object(flow, 'uuid4', side_effect=lambda: SimpleNamespace(hex=next(keys))):
            flow.record(self.config, {'Oil': 30}, '较早交易', event_key='older')
            clock[0] = NOW + timedelta(seconds=4)
            flow.record(self.config, {'Oil': 50, 'Coin': 999}, '未来交易', event_key='future')
            clock[0] = NOW
            with flow.task_session('fixture', 'Main'):
                flow.observe(self.config, 'Oil', 1000)
                flow.observe(self.config, 'Coin', 500)
                flow.record(self.config, {'Oil': -100, 'Coin': 20}, '同刻交易', event_key='same-time')
                flow.record(self.config, {'Oil': -100, 'Coin': 20}, '重放', event_key='same-time')
                flow.observe(self.config, 'Oil', 900)
                flow.observe(self.config, 'Oil', 900)
                flow.observe(self.config, 'Coin', 520)
                flow.observe(self.config, 'Oil', 1, observed_at=NOW - timedelta(microseconds=1))
                clock[0] = NOW + timedelta(seconds=1)
                flow.record(self.config, {'Oil': -10}, '较后交易', event_key='later')
                flow.observe(self.config, 'Oil', 890)
            clock[0] = NOW + timedelta(seconds=2)
            with flow.task_session('fixture', 'Commission'):
                flow.observe(self.config, 'Oil', 950)
            with flow.task_session('fixture', 'Event'):
                flow.observe(self.config, 'Pt', 1000)
                flow.observe(self.config, 'Pt', 1200)
                flow.observe(self.config, 'Pt', 0)
            with flow.task_session('fixture', 'EventShop'):
                flow.observe(self.config, 'Pt', 1500)
                flow.observe(self.config, 'Pt', 900)
            other = SimpleNamespace(config_name='other', task=self.config.task)
            flow.record(other, {'Oil': 999}, '其他实例', event_key='other')
            clock[0] = NOW + timedelta(seconds=4)
            flow.observe(self.config, 'Oil', 1000)
        return self.snapshot(store)

    def test_timestamp_id_upper_boundary_and_attribution_match_original(self):
        optimized = self.exercise_observations(self.make_store('optimized'), legacy=False)
        original = self.exercise_observations(self.make_store('original'), legacy=True)
        self.assertEqual(original, optimized)
        balances, entries = optimized
        self.assertEqual(1000, next(row[2] for row in balances if row[:2] == ('fixture', 'Oil')))
        oil_adjustments = [row for row in entries if row[1] == 'fixture' and row[3] == 'Oil'
                           and row[7] == 'adjustment']
        self.assertEqual([60], [row[4] for row in oil_adjustments])

    def test_explicit_lower_bound_uses_existing_timestamp_index(self):
        store = self.make_store('plan')
        queries = []
        with patch.object(flow, 'connect', self.connect_factory(store, observed_queries=queries)), \
                patch.object(flow, 'now', return_value=NOW):
            flow.observe(self.config, 'Oil', 100)
            flow.observe(self.config, 'Oil', 100)
        sql, parameters = queries[0]
        with store.transaction(write=False) as connection:
            plan = ' '.join(row[3] for row in connection.execute('EXPLAIN QUERY PLAN ' + sql, parameters))
        self.assertIn('resource_flows_window', plan)
        self.assertIn('ts>?', plan)
        self.assertIn('ts<?', plan)

    def test_failed_balance_write_rolls_back_observed_delta(self):
        store = self.make_store('rollback')
        with patch.object(flow, 'connect', self.connect_factory(store)), patch.object(flow, 'now', return_value=NOW):
            flow.observe(self.config, 'Oil', 100)
            before = self.snapshot(store)
            with store.transaction() as connection:
                connection.execute('''CREATE TRIGGER reject_balance BEFORE UPDATE ON resource_balances
                    BEGIN SELECT RAISE(ABORT, 'fixture failure'); END''')
            with patch.object(flow.logger, 'warning') as warning:
                flow.observe(self.config, 'Oil', 120)
            self.assertEqual(before, self.snapshot(store))
            warning.assert_called_once()

    def test_parallel_same_observation_only_records_one_residual(self):
        store = self.make_store('parallel')
        with patch.object(flow, 'connect', self.connect_factory(store)), patch.object(flow, 'now', return_value=NOW):
            flow.observe(self.config, 'Oil', 0)
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda _: flow.observe(self.config, 'Oil', 100), range(12)))
        balances, entries = self.snapshot(store)
        self.assertEqual(100, balances[0][2])
        self.assertEqual([100], [row[4] for row in entries])

    def test_integer_overflow_still_rolls_back_without_balance_change(self):
        for legacy in (True, False):
            with self.subTest(legacy=legacy):
                store = self.make_store(f'overflow-{legacy}')
                clock = [NOW]
                with patch.object(flow, 'connect', self.connect_factory(store, legacy=legacy)), \
                        patch.object(flow, 'now', side_effect=lambda: clock[0]):
                    flow.observe(self.config, 'Oil', 0)
                    flow.record(self.config, {'Oil': 2 ** 63 - 1}, '大数', event_key='max')
                    clock[0] += timedelta(seconds=1)
                    flow.record(self.config, {'Oil': 1}, '溢出', event_key='overflow')
                    before = self.snapshot(store)
                    with patch.object(flow.logger, 'warning') as warning:
                        flow.observe(self.config, 'Oil', 0)
                    self.assertEqual(before, self.snapshot(store))
                    warning.assert_called_once()


class ShipBattleSmallWriteTests(TemporaryDatabaseTests):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.object(ship, 'date', FixedDate))
        self.enterContext(patch.object(ship, 'current_time', return_value=NOW))
        self.enterContext(patch.object(ship.logger, 'info'))

    def stats(self, name, seed=None):
        result = ship.ShipExpStats(instance_name='fixture', store=self.make_store(name))
        result.data = copy.deepcopy(seed or {})
        return result

    def end_battle(self, stats, *, duration=10, **kwargs):
        stats._battle_start_time = 100
        with patch.object(ship.time, 'time', return_value=100 + duration):
            return stats.on_battle_end(**kwargs)

    def test_cl1_final_data_matches_original_two_save_path_with_one_commit(self):
        seed = {
            'battle_times': {'samples': [float(i + 1) for i in range(100)], 'average': 50.5},
            'daily_stats': {
                (NOW.date() - timedelta(days=i)).isoformat(): {
                    'total_run_time': 20.0, 'total_exp_gained': 624,
                    'battle_count': 2, 'exp_per_hour': 112320.0,
                } for i in range(35)
            },
            'custom': {'keep': ['原始扩展']},
            'meow_battle_times': {'samples': [55.0], 'average': 55.0},
        }
        original = self.stats('original', seed)
        original._record_battle_time(10.25)
        original._update_daily_stats(312, 10.25)
        optimized = self.stats('optimized', seed)
        with patch.object(optimized, '_save', wraps=optimized._save) as save:
            self.assertEqual(10.25, self.end_battle(optimized, duration=10.25))
        save.assert_called_once()
        self.assertEqual(original.data, optimized.data)
        self.assertEqual(original._load(), optimized._load())
        self.assertEqual(100, len(optimized.data['battle_times']['samples']))
        self.assertEqual(30, len(optimized.data['daily_stats']))
        today = optimized.get_today_stats()
        self.assertEqual((30.25, 936, 3),
                         (today['total_run_time'], today['total_exp_gained'], today['battle_count']))

    def test_daily_event_switch_and_meow_are_preserved(self):
        for source, enabled in (('cl1', False), ('cl1', True), ('meow', True)):
            with self.subTest(source=source, enabled=enabled):
                stats = self.stats(f'{source}-{enabled}')
                summary = Mock()
                with patch('module.statistics.daily_summary_store.get_daily_summary_store', return_value=summary), \
                        patch.object(stats, '_save', wraps=stats._save) as save:
                    self.end_battle(stats, source=source, record_daily_summary=enabled)
                save.assert_called_once()
                self.assertEqual(source == 'cl1' and enabled, summary.record_cl1_battle_event.called)
                if source == 'cl1' and enabled:
                    summary.record_cl1_battle_event.assert_called_once_with(
                        instance='fixture', timestamp=NOW, duration_seconds=10, estimated_exp=312)
                self.assertEqual(source == 'cl1', 'daily_stats' in stats.data)
                self.assertEqual(stats.data, stats._load())

    def test_daily_event_failure_still_persists_sample_without_daily_increment(self):
        stats = self.stats('event-failure')
        summary = Mock()
        summary.record_cl1_battle_event.side_effect = RuntimeError('日报事件失败')
        with patch('module.statistics.daily_summary_store.get_daily_summary_store', return_value=summary), \
                self.assertRaisesRegex(RuntimeError, '日报事件失败'):
            self.end_battle(stats, record_daily_summary=True)
        self.assertEqual({'samples': [10], 'average': 10}, stats._load()['battle_times'])
        self.assertNotIn('daily_stats', stats._load())
        self.assertEqual(stats.data, stats._load())
        self.assertIsNone(stats._battle_start_time)

    def test_daily_update_failure_preserves_original_memory_and_sample_only_commit(self):
        seed = {'daily_stats': {NOW.date().isoformat(): {
            'total_run_time': 20, 'total_exp_gained': 624, 'battle_count': 2, 'exp_per_hour': 112320,
        }}}
        original = self.stats('original-daily-failure', seed)
        original._record_battle_time(10)
        with patch.object(original, '_cleanup_old_daily_stats', side_effect=RuntimeError('每日统计失败')), \
                self.assertRaisesRegex(RuntimeError, '每日统计失败'):
            original._update_daily_stats(312, 10)
        stats = self.stats('daily-failure', seed)
        with patch.object(stats, '_cleanup_old_daily_stats', side_effect=RuntimeError('每日统计失败')), \
                self.assertRaisesRegex(RuntimeError, '每日统计失败'):
            self.end_battle(stats)
        self.assertEqual(original.data, stats.data)
        self.assertEqual(original._load(), stats._load())
        self.assertEqual(seed['daily_stats'], stats._load()['daily_stats'])
        self.assertEqual(3, stats.get_today_stats()['battle_count'])
        self.assertEqual([10], stats._load()['battle_times']['samples'])

    def test_sample_and_daily_helpers_still_save_when_called_directly(self):
        stats = self.stats('direct')
        with patch.object(stats, '_save', wraps=stats._save) as save:
            stats._record_battle_time(10)
            self.assertEqual(1, save.call_count)
            self.assertEqual([10], stats._load()['battle_times']['samples'])
            stats._update_daily_stats(312, 10)
            self.assertEqual(2, save.call_count)
        self.assertEqual(stats.data, stats._load())

    def test_save_error_keeps_warning_and_transaction_rollback(self):
        stats = self.stats('save-failure', {'custom': '保留'})
        stats._save()
        before = stats._load()
        with stats.store.transaction() as connection:
            connection.execute('''CREATE TRIGGER reject_ship BEFORE INSERT ON ship_exp_checks
                BEGIN SELECT RAISE(ABORT, 'fixture failure'); END''')
        with patch.object(ship.logger, 'warning') as warning:
            self.assertEqual(10, self.end_battle(stats))
        warning.assert_called_once()
        self.assertEqual(before, stats._load())
        self.assertEqual([10], stats.data['battle_times']['samples'])
        self.assertEqual(1, stats.get_today_stats()['battle_count'])

    def test_missing_start_and_out_of_range_battles_do_not_write(self):
        stats = self.stats('invalid')
        with patch.object(stats, '_save') as save:
            self.assertIsNone(stats.on_battle_end())
            for duration in (0.5, 301):
                self.assertEqual(duration, self.end_battle(stats, duration=duration))
            save.assert_not_called()
        self.assertEqual({}, stats.data)


if __name__ == '__main__':
    unittest.main()
