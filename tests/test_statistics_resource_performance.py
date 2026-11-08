"""资源查询加速仍保持历史、边界、字段继承和只读并发语义。"""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from module.persistence.database import BusinessDatabase, create_schema, register_instance
from module.statistics import resource_stats


class ResourcePerformanceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.folder.cleanup)
        self.store = BusinessDatabase(Path(self.folder.name))
        with closing(sqlite3.connect(self.store.path)) as connection:
            create_schema(connection)
            register_instance(connection, 'alpha')
            register_instance(connection, 'beta')
            connection.execute("INSERT INTO storage_migrations VALUES(1, 'now', 'test')")
            connection.commit()
        self.patch = patch.object(resource_stats, '_database', return_value=self.store)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def insert_rows(self, rows):
        with self.store.transaction() as connection:
            connection.executemany(
                'INSERT INTO resource_snapshots(instance,ts,oil,cube) VALUES(?,?,?,?)', rows,
            )

    def test_old_database_adds_index_once_without_changing_rows(self):
        with closing(sqlite3.connect(self.store.path)) as connection:
            connection.execute('DROP INDEX resource_snapshots_latest')
            connection.execute("INSERT INTO resource_snapshots(instance,ts,oil) VALUES('alpha','old',123)")
            connection.commit()
        self.store.ensure_ready()
        self.assertTrue(self.store.marker.exists())
        with self.store.transaction(write=False) as connection:
            self.assertEqual(123, connection.execute('SELECT oil FROM resource_snapshots').fetchone()[0])
            plan = connection.execute(
                'EXPLAIN QUERY PLAN SELECT * FROM resource_snapshots WHERE instance=? ORDER BY id DESC LIMIT 1',
                ('alpha',),
            ).fetchall()
            self.assertFalse(any('TEMP B-TREE' in row[3] for row in plan))
        # 已有索引时初始化只读，不等待其他连接的业务写锁。
        with self.store.transaction():
            BusinessDatabase(self.store.directory).ensure_ready()

    def test_carry_forward_keeps_id_order_and_unknown_columns(self):
        self.insert_rows([
            ('alpha', '2026-11-08T12:00:00', 10, 8),
            ('beta', '2026-11-08T12:01:00', 999, 999),
            ('alpha', '2026-11-08T11:00:00', 20, None),
        ])
        self.assertTrue(resource_stats.record_resource_snapshot(
            'alpha', {'Cube': 0}, observed_at=datetime(2026, 11, 8, 13), carry_forward=True,
        ))
        row = resource_stats.get_resource_timeline('alpha', limit=1)[0]
        self.assertEqual((20, 0, None), (row['oil'], row['cube'], row['gem']))

    def test_window_combinations_preserve_original_sql_order_and_limits(self):
        self.insert_rows([
            ('alpha', '2026-11-08T12:00:00', 10, 1),
            ('alpha', '2026-11-08T10:00:00', 20, 2),
            ('beta', '2026-11-08T11:00:00', 999, 9),
            ('alpha', '2026-11-08T11:00:00', 30, 3),
        ])
        for since in (None, '2026-11-08T11:00:00', ''):
            for until in (None, '2026-11-08T12:00:00', ''):
                with self.subTest(since=since, until=until), self.store.transaction(write=False) as connection:
                    rows = connection.execute(
                        'SELECT * FROM resource_snapshots WHERE instance=? '
                        'AND (? IS NULL OR ts>=?) AND (? IS NULL OR ts<=?) ORDER BY id DESC LIMIT 2',
                        ('alpha', since, since, until, until),
                    ).fetchall()
                    expected = [dict(row) for row in reversed(rows)]
                self.assertEqual(expected, resource_stats.get_resource_timeline(
                    'alpha', limit=2, since=since, until=until,
                ))

    def test_daily_summary_legacy_timestamps_ties_nulls_and_end_boundary(self):
        self.insert_rows([
            ('alpha', '2026-11-07T19:00:00', 10, 1),
            ('alpha', '2026-11-07 20:00:00', 20, None),
            ('alpha', '2026-11-08T19:00:00', 30, 3),
            ('alpha', '2026-11-08T19:00:00.000000', 40, None),
            ('alpha', '2026-11-08T20:00:00', 999, 999),
            ('alpha', '2026-11-07T19:30:00', 15, 2),
            ('alpha', '2026-11-08T19:59:00+00:00', 999, 999),
            ('alpha', 'broken', 999, 999),
            ('beta', '2026-11-08T19:59:00', 999, 999),
        ])
        # 日报使用 SQLite 只读快照，不再阻挡同进程资源采样线程。
        with resource_stats._local_lock:
            report = resource_stats.get_resource_interval_summary(
                'alpha', datetime(2026, 11, 7, 20), datetime(2026, 11, 8, 20),
            )['resources']
        self.assertEqual((20, 40, 20), tuple(report['Oil'][key] for key in ('start', 'end', 'delta')))
        self.assertEqual((2, 3, 1), tuple(report['Cube'][key] for key in ('start', 'end', 'delta')))
        self.assertFalse(report['Gem']['baseline_known'])
        self.assertIsNone(report['Gem']['delta'])


if __name__ == '__main__':
    unittest.main()
