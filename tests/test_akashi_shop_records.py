"""明石商店冠军截图、月界、事务隔离与统计兼容性。"""

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np

from module.persistence.database import BusinessDatabase, VERSION, create_schema
from module.statistics.akashi_shop_records import AkashiShopRecords, FIELD, current_server_month
from module.statistics.cl1_database import Cl1Database


class AkashiShopRecordsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / 'config'
        self.config.mkdir()
        self.path = self.config / 'azurpilot.db'
        with closing(sqlite3.connect(self.path)) as connection, connection:
            create_schema(connection)
            connection.execute('INSERT INTO storage_migrations VALUES(?,?,?)', (VERSION, '2026-10-23', 'fixture'))
        (self.config / 'azurpilot.migrated').write_text(f'{VERSION}\nfixture\n', encoding='utf-8')
        self.service = AkashiShopRecords(self.config)
        self.month = patch('module.statistics.akashi_shop_records.current_server_month', return_value='2026-10')
        self.month.start()
        self.addCleanup(self.month.stop)
        self.image = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.image[:, :, 1] = 130
        self.time = datetime(2026, 10, 23, 10, tzinfo=timezone.utc)

    def add(self, total=100, instance='test', server='cn', time=None, image=None):
        return self.service.record(instance, server, total, {'20': 0, '50': 0, '100': total // 100},
                                   self.image if image is None else image, time or self.time)

    def pngs(self):
        return list(self.service.screenshot_root.rglob('*.png'))

    def test_empty_read_then_first_record_and_full_rgb_screenshot(self):
        self.assertEqual(self.service.records('test', 'cn'), {'month': '2026-10', 'monthly': None, 'history': None})
        self.assertTrue(self.add())
        result = self.service.records('test', 'cn')
        self.assertEqual(result['monthly'], result['history'])
        self.assertEqual(result['monthly']['totalAp'], 100)
        self.assertEqual(result['monthly']['boxes'], {'20': 0, '50': 0, '100': 1})
        self.assertEqual(result['monthly']['time'], '2026-10-23T18:00:00+08:00')
        self.assertTrue(self.service.image(result['monthly']).startswith(b'\x89PNG\r\n\x1a\n'))
        from PIL import Image
        with Image.open(self.pngs()[0]) as image:
            self.assertEqual(image.mode, 'RGB')
            self.assertEqual(image.size, (1280, 720))
            np.testing.assert_array_equal(np.asarray(image), self.image)

    def test_lower_total_does_not_write_or_validate_unused_image(self):
        self.add(300)
        with patch.object(self.service, '_write_image', side_effect=AssertionError('无须截图')):
            self.assertFalse(self.add(100, image='无效但不应使用'))
        self.assertEqual(len(self.pngs()), 1)
        self.assertEqual(self.service.records('test', 'cn')['monthly']['totalAp'], 300)

    def test_equal_total_keeps_latest_encounter_and_rejects_late_old_result(self):
        self.add()
        previous = self.service.records('test', 'cn')['monthly']
        later = datetime(2026, 10, 23, 11, tzinfo=timezone.utc)
        self.assertTrue(self.add(time=later))
        current = self.service.records('test', 'cn')['monthly']
        self.assertNotEqual(current['screenshot'], previous['screenshot'])
        self.assertGreater(current['time'], previous['time'])
        self.assertFalse(self.service._path(previous['screenshot']).exists())
        self.assertEqual(len(self.pngs()), 1)
        self.assertFalse(self.add(time=self.time))
        self.assertEqual(current, self.service.records('test', 'cn')['monthly'])

    def test_replaced_image_cleanup_preserves_other_month_instance_and_unrelated_file(self):
        self.add(time=datetime(2026, 9, 23, 10, tzinfo=timezone.utc))
        self.add(instance='other')
        self.add()
        unrelated = self.service.screenshot_root / 'private.png'
        unrelated.write_bytes('不属于本功能的文件'.encode('utf-8'))
        keep = set(self.pngs())
        previous = self.service.records('test', 'cn')['monthly']
        previous_path = self.service._path(previous['screenshot'])
        self.add(300)
        self.assertFalse(previous_path.exists())
        self.assertTrue(all(path.exists() for path in keep - {previous_path}))
        self.assertEqual(len(self.pngs()), 4)

    def test_old_image_still_referenced_by_another_record_is_not_removed(self):
        self.add()
        previous = self.service.records('test', 'cn')['monthly']
        self.add(200, server='en')
        stats = Cl1Database(store=BusinessDatabase(self.config))
        data = stats.get_stats('test', '2026-10')
        data[FIELD]['en']['screenshot'] = previous['screenshot']
        stats.save_stats('test', '2026-10', data)
        self.add(300)
        self.assertTrue(self.service._path(previous['screenshot']).exists())

    def test_cleanup_failure_warns_and_keeps_new_record_published(self):
        self.add()
        previous = self.service.records('test', 'cn')['monthly']
        previous_path = self.service._path(previous['screenshot'])
        original = Path.unlink

        def unlink(path, *args, **kwargs):
            if path == previous_path:
                raise PermissionError('截图仍打开')
            return original(path, *args, **kwargs)

        with patch.object(Path, 'unlink', autospec=True, side_effect=unlink), \
                patch('module.statistics.akashi_shop_records.logger.warning') as warning:
            self.assertTrue(self.add(300))
        warning.assert_called_once()
        self.assertTrue(previous_path.exists())
        current = self.service.records('test', 'cn')['monthly']
        self.assertEqual(current['totalAp'], 300)
        self.assertTrue(self.service.image(current))

    def test_history_spans_months_and_equal_cross_month_uses_latest(self):
        self.add(300, time=datetime(2026, 9, 30, 12, tzinfo=timezone.utc))
        self.add(200)
        result = self.service.records('test', 'cn')
        self.assertEqual(result['monthly']['totalAp'], 200)
        self.assertEqual(result['history']['month'], '2026-09')
        self.add(300)
        result = self.service.records('test', 'cn')
        self.assertEqual(result['history'], result['monthly'])
        with patch('module.statistics.akashi_shop_records.current_server_month', return_value='2026-11'):
            result = self.service.records('test', 'cn')
        self.assertIsNone(result['monthly'])
        self.assertEqual(result['history']['totalAp'], 300)

    def test_instance_and_server_are_isolated_and_instance_is_hashed_in_filename(self):
        self.add(instance='../../用户 A')
        self.add(200, instance='other')
        self.add(300, instance='../../用户 A', server='en')
        self.assertEqual(self.service.records('../../用户 A', 'cn')['history']['totalAp'], 100)
        self.assertEqual(self.service.records('../../用户 A', 'en')['history']['totalAp'], 300)
        self.assertEqual(self.service.records('other', 'cn')['history']['totalAp'], 200)
        relative = self.service.records('../../用户 A', 'cn')['monthly']['screenshot']
        self.assertNotIn('用户', relative)
        self.assertEqual(len(Path(relative).parts[0]), 64)
        self.assertTrue(all(path.resolve().is_relative_to(self.service.screenshot_root.resolve()) for path in self.pngs()))

    def test_month_uses_server_timezone_including_observed_time(self):
        self.month.stop()
        instant = datetime(2026, 10, 31, 20, tzinfo=timezone.utc)
        with patch('module.statistics.akashi_shop_records.current_time', side_effect=lambda tz: instant.astimezone(tz)):
            self.assertEqual(current_server_month('cn'), '2026-11')
            self.assertEqual(current_server_month('en'), '2026-10')
            self.add(time=instant)
            self.add(server='en', time=instant)
            self.assertEqual(self.service.records('test', 'cn')['monthly']['month'], '2026-11')
            self.assertEqual(self.service.records('test', 'en')['monthly']['month'], '2026-10')
        with self.assertRaises(ValueError):
            current_server_month('unknown')

    def test_naive_observation_is_explicitly_server_local(self):
        self.add(time=datetime(2026, 10, 23, 10))
        self.assertEqual(self.service.records('test', 'cn')['monthly']['time'], '2026-10-23T10:00:00+08:00')

    def test_failed_image_write_does_not_change_existing_record(self):
        self.add()
        previous = self.service.records('test', 'cn')
        with patch('module.statistics.akashi_shop_records.os.replace', side_effect=OSError('磁盘不可写')):
            with self.assertRaises(OSError):
                self.add(300)
        self.assertEqual(self.service.records('test', 'cn'), previous)
        self.assertEqual(len(self.pngs()), 1)
        self.assertEqual(list(self.service.screenshot_root.rglob('*.tmp')), [])

    def test_failed_database_publish_rolls_back_and_removes_unpublished_image(self):
        self.add()
        previous = self.service.records('test', 'cn')
        with patch('module.statistics.akashi_shop_records.write_value', side_effect=sqlite3.OperationalError('写入失败')):
            with self.assertRaises(sqlite3.OperationalError):
                self.add(300)
        self.assertEqual(self.service.records('test', 'cn'), previous)
        self.assertEqual(len(self.pngs()), 1)

    def test_bad_inputs_do_not_publish_records_or_images(self):
        for total, boxes, image in [(True, {'20': 0, '50': 0, '100': 1}, self.image),
                                    (0, {'20': 0, '50': 0, '100': 0}, self.image),
                                    (100, {'100': 1}, self.image),
                                    (100, {'20': 0, '50': 0, '100': -1}, self.image),
                                    (200, {'20': 0, '50': 0, '100': 1}, self.image),
                                    (100, {'20': 0, '50': 0, '100': 1}, self.image[:100])]:
            with self.subTest(total=total, boxes=boxes, shape=getattr(image, 'shape', None)):
                with self.assertRaises(ValueError):
                    self.service.record('test', 'cn', total, boxes, image, self.time)
        self.assertEqual(self.pngs(), [])
        self.assertIsNone(self.service.records('test', 'cn')['history'])

    def test_only_own_metadata_is_updated_without_reading_or_rewriting_month(self):
        stats = Cl1Database(store=BusinessDatabase(self.config))
        source = {'battle_count': 7, 'ap_snapshots': [{'ts': '2026-10-23', 'ap': 100}],
                  'another_custom': {'list': [True, '完整历史', None]}}
        stats.save_stats('test', '2026-10', source)
        with self.service.store.transaction(write=False) as connection:
            original = dict(connection.execute('SELECT * FROM action_point_snapshots').fetchone())
        with patch('module.persistence.snapshots.read_month', side_effect=AssertionError('不应读取整月')), \
                patch('module.persistence.snapshots.save_month', side_effect=AssertionError('不应重写整月')):
            self.add()
            self.service.records('test', 'cn')
            self.add(200)
        result = stats.get_stats('test', '2026-10')
        self.assertEqual({name: result[name] for name in source}, source)
        self.assertIn(FIELD, result)
        with self.service.store.transaction(write=False) as connection:
            self.assertEqual(dict(connection.execute('SELECT * FROM action_point_snapshots').fetchone()), original)
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_month_roundtrip_preserves_champion_and_extension_shape(self):
        self.add()
        previous = self.service.records('test', 'cn')
        stats = Cl1Database(store=BusinessDatabase(self.config))
        data = stats.get_stats('test', '2026-10')
        data['battle_count'] = 9
        stats.save_stats('test', '2026-10', data)
        self.assertEqual(self.service.records('test', 'cn'), previous)
        self.add(200)
        self.assertEqual(stats.get_stats('test', '2026-10')['battle_count'], 9)
        with self.service.store.transaction(write=False) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM typed_value_sets').fetchone()[0], 1)

    def test_concurrent_instances_and_tied_records_keep_latest_complete_reference(self):
        def save(hour):
            service = AkashiShopRecords(self.config)
            return service.record('test', 'cn', 200, {'20': 0, '50': 0, '100': 2}, self.image,
                                  datetime(2026, 10, 23, hour, tzinfo=timezone.utc))
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(save, [7, 3, 10, 4, 12, 8]))
        record = self.service.records('test', 'cn')['monthly']
        self.assertEqual(record['time'], '2026-10-23T20:00:00+08:00')
        self.assertTrue(self.service.image(record))
        with self.service.store.transaction(write=False) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM typed_value_sets').fetchone()[0], 1)
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_image_path_rejects_traversal_absolute_path_and_non_png(self):
        self.add()
        record = self.service.records('test', 'cn')['monthly']
        for path in ('../outside.png', 'D:/outside.png', record['screenshot'].replace('/', '\\')):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.service.image({**record, 'screenshot': path})
        self.service._path(record['screenshot']).write_bytes(b'not PNG')
        with self.assertRaises(ValueError):
            self.service.image(record)

    def test_outside_directory_link_is_rejected(self):
        self.add()
        record = self.service.records('test', 'cn')['monthly']
        path = self.service._path(record['screenshot'])
        target = self.root / 'outside'
        target.mkdir()
        path.unlink()
        path.parent.rmdir()
        try:
            path.parent.symlink_to(target, target_is_directory=True)
        except OSError as error:
            self.skipTest(f'当前 Windows 不允许创建目录符号链接：{error}')
        self.addCleanup(path.parent.unlink)
        with self.assertRaises(ValueError):
            self.service.image(record)


if __name__ == '__main__':
    unittest.main()
