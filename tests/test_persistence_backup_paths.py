"""迁移备份的长路径及拒绝访问回归，全部使用临时目录。"""
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from module.persistence.database import BusinessDatabase
from module.persistence.migration import backup_sources, io_path, sqlite_uri


class BackupPathTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory(ignore_cleanup_errors=True))
        self.root = Path(self.directory)
        self.config = self.root / 'config'
        self.config.mkdir()

    def test_security_materials_with_long_paths_are_copied_without_manifest_prefix(self):
        folder = self.config / 'opsi_secure' / ('plaintext-backup-' + 'a' * 90) / 'original' / 'log'
        csv = folder / ('azurstat_meowofficer_farming.instance-' + 'b' * 64 + '.csv')
        io_path(csv.parent).mkdir(parents=True)
        io_path(csv).write_bytes(b'historical ciphertext remains unchanged')
        old_db = folder / ('c' * 85 + '.db')
        with closing(sqlite3.connect(io_path(old_db))) as connection, connection:
            connection.execute('CREATE TABLE history(value INTEGER)')
            connection.execute('INSERT INTO history VALUES(42)')
        encrypted_db = folder / 'original-encrypted.db'
        io_path(encrypted_db).write_bytes(b'OPSIV2.frozen legacy database ciphertext')
        source = self.root / 'log' / 'ship.json'
        source.parent.mkdir()
        source.write_bytes(b'{}')
        target = self.config / 'storage-backups' / ('pre-v1-' + 'd' * 45)
        database = BusinessDatabase(self.config)
        copies, manifest = backup_sources(database, {source: 'ships'}, target)
        self.assertEqual(copies[source], target / 'log' / 'ship.json')
        self.assertEqual(manifest[0]['path'], str(Path('log') / 'ship.json'))
        self.assertNotIn('\\\\?\\', manifest[0]['path'])
        copied_csv = target / 'config' / csv.relative_to(self.config)
        self.assertGreater(len(str(copied_csv)), 260)
        self.assertEqual(io_path(copied_csv).read_bytes(), io_path(csv).read_bytes())
        copied_db = target / 'config' / old_db.relative_to(self.config)
        with closing(sqlite3.connect(sqlite_uri(copied_db, 'ro'), uri=True)) as connection:
            self.assertEqual(connection.execute('SELECT value FROM history').fetchone()[0], 42)
        copied_cipher = target / 'config' / encrypted_db.relative_to(self.config)
        self.assertEqual(io_path(copied_cipher).read_bytes(), io_path(encrypted_db).read_bytes())

    def test_unreadable_security_directory_prevents_native_publication(self):
        folder = self.config / 'stock-exchange'
        folder.mkdir()
        original = folder / 'registry.json'
        original.write_bytes(b'{"keep": true}')
        scan = os.scandir

        def denied(path):
            if Path(path) == folder:
                raise PermissionError('fixture permission denied')
            return scan(path)

        database = BusinessDatabase(self.config)
        with patch('module.persistence.migration.assert_no_workers'), \
                patch('module.persistence.migration.os.scandir', side_effect=denied):
            with self.assertRaises(PermissionError):
                database.ensure_ready()
        self.assertEqual(original.read_bytes(), b'{"keep": true}')
        self.assertFalse(database.path.exists())
        self.assertFalse(database.marker.exists())


if __name__ == '__main__':
    unittest.main()
