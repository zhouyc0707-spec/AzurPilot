"""耄耋截图目录 API：只用临时归档，系统打开操作全部替换为 mock。"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pydantic import ValidationError

from module.api import background_service, meow_screenshot_service as service
from module.api.protocol import ApiError, MeowScreenshotFolderParams
from module.api.router import Router
from module.statistics.azurstats import AzurStats


class MeowScreenshotFolderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.configs = SimpleNamespace(
            root=self.root,
            path=Mock(return_value=self.root / 'config/testpilot.json'),
            read=Mock(return_value=({'Alas': {'DropRecord': {'SaveFolder': './screenshots'}}}, 'revision')),
        )
        self.open = self.enterContext(patch.object(background_service, 'open_local_directory'))

    def folder(self, category='金菜', month='26年10月'):
        return self.root / 'screenshots/opsi_meowfficer_farming' / category / month

    def request(self, item='Plate', month='2026-10'):
        return service.open_folder(self.configs, 'testpilot', item, month)

    def test_month_folder_opens_with_exact_archive_path(self):
        path = self.folder()
        path.mkdir(parents=True)
        result = self.request()
        self.assertEqual(result, {
            'opened': True, 'path': str(path), 'requestedPath': str(path),
            'scope': 'month', 'item': 'Plate', 'month': '2026-10',
        })
        self.open.assert_called_once_with(path)
        self.configs.path.assert_called_once_with('testpilot')

    def test_each_allowed_category_uses_shared_classification_rules(self):
        expected = {'GearDesignPlanT5', 'OrdnanceTestingReportT4', 'Plate',
                    'CoordinateObscure', 'CoordinateAbyssal', 'CatT3'}
        self.assertEqual(set(MeowScreenshotFolderParams.model_fields['item'].annotation.__args__), expected)
        self.assertEqual({key for key, _, _ in AzurStats.MEOW_LOOT_RULES}, expected)
        for key, category, _ in AzurStats.MEOW_LOOT_RULES:
            with self.subTest(key=key):
                path = self.folder(category)
                path.mkdir(parents=True)
                result = self.request(key)
                self.assertEqual(result['path'], str(path))
                self.assertEqual(result['scope'], 'month')

    def test_missing_month_falls_back_to_existing_category(self):
        category = self.folder().parent
        old_month = category / '26年9月'
        old_month.mkdir(parents=True)
        result = self.request()
        self.assertEqual(result['scope'], 'category')
        self.assertTrue(result['opened'])
        self.assertEqual(result['path'], str(category))
        self.assertEqual(result['requestedPath'], str(self.folder()))
        self.assertFalse(self.folder().exists())
        self.open.assert_called_once_with(category)

    def test_missing_category_does_not_create_or_open_directory(self):
        result = self.request()
        self.assertEqual(result['scope'], 'missing')
        self.assertFalse(result['opened'])
        self.assertIsNone(result['path'])
        self.assertEqual(result['requestedPath'], str(self.folder()))
        self.assertFalse((self.root / 'screenshots').exists())
        self.open.assert_not_called()

    def test_file_cannot_masquerade_as_month_directory(self):
        path = self.folder()
        path.parent.mkdir(parents=True)
        path.write_bytes(b'not-a-directory')
        result = self.request()
        self.assertEqual(result['scope'], 'category')
        self.open.assert_called_once_with(path.parent)

    def test_configured_relative_directory_is_used(self):
        self.configs.read.return_value = ({'Alas': {'DropRecord': {'SaveFolder': './archive'}}}, 'revision')
        path = self.root / 'archive/opsi_meowfficer_farming/金菜/26年2月'
        path.mkdir(parents=True)
        result = self.request(month='2026-02')
        self.assertEqual(result['path'], str(path))
        self.open.assert_called_once_with(path)

    def test_configured_absolute_directory_is_used(self):
        base = self.root / 'external-archive'
        self.configs.read.return_value = ({'Alas': {'DropRecord': {'SaveFolder': str(base)}}}, 'revision')
        path = base / 'opsi_meowfficer_farming/金菜/27年2月'
        path.mkdir(parents=True)
        result = self.request(month='2027-02')
        self.assertEqual(result['path'], str(path))
        self.open.assert_called_once_with(path)

    def test_invalid_items_and_months_are_rejected_before_reading_config(self):
        for item in ('unknown', '../config', '金菜', '', None):
            with self.subTest(item=item), self.assertRaises(ApiError) as raised:
                self.request(item=item)
            self.assertEqual(raised.exception.code, 'INVALID_PARAMS')
        for month in ('2026-00', '2026-13', '2026-2', '2026-10/../../config', '0000-01', None):
            with self.subTest(month=month), self.assertRaises(ApiError) as raised:
                self.request(month=month)
            self.assertEqual(raised.exception.code, 'INVALID_PARAMS')
        self.configs.read.assert_not_called()
        self.open.assert_not_called()

    def test_invalid_instance_uses_existing_config_validation(self):
        self.configs.path.side_effect = ApiError('NOT_FOUND', '实例不存在')
        with self.assertRaises(ApiError) as raised:
            self.request()
        self.assertEqual(raised.exception.code, 'NOT_FOUND')
        self.configs.read.assert_not_called()
        self.open.assert_not_called()

    def test_invalid_saved_directory_reports_config_error(self):
        for value in ('', '  ', None, 42):
            self.configs.read.return_value = ({'Alas': {'DropRecord': {'SaveFolder': value}}}, 'revision')
            with self.subTest(value=value), self.assertRaises(ApiError) as raised:
                self.request()
            self.assertEqual(raised.exception.code, 'CONFIG_INVALID')
        self.open.assert_not_called()

    def test_open_error_has_safe_user_message_and_path(self):
        path = self.folder()
        path.mkdir(parents=True)
        self.open.side_effect = background_service.BackgroundError('打开文件夹失败：OSError')
        with self.assertRaises(ApiError) as raised:
            self.request()
        self.assertEqual(raised.exception.code, 'FOLDER_OPEN_FAILED')
        self.assertEqual(str(raised.exception), '打开文件夹失败：OSError')
        self.assertEqual(raised.exception.details, {'path': str(path)})

    def test_escaping_category_link_cannot_open_arbitrary_directory(self):
        outside = self.root / 'unrelated'
        outside.mkdir()
        category = self.folder().parent
        category.parent.mkdir(parents=True)
        try:
            category.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            if getattr(error, 'winerror', None) == 1314:
                self.skipTest('当前 Windows 用户没有创建符号链接的权限')
            raise
        with self.assertRaises(ApiError) as raised:
            self.request()
        self.assertEqual(raised.exception.code, 'INVALID_PARAMS')
        self.open.assert_not_called()

    def test_router_validates_params_and_marks_opening_as_side_effect(self):
        router = Router(self.configs, Mock())
        method = 'statistics.meowScreenshotFolder.open'
        self.assertTrue(router.methods[method].mutates)
        with patch.object(service, 'open_folder', return_value={'scope': 'missing'}) as handler:
            self.assertEqual(router.dispatch(method, {'instance': 'testpilot', 'item': 'Plate', 'month': '2026-10'}),
                             {'scope': 'missing'})
            handler.assert_called_once_with(self.configs, 'testpilot', 'Plate', '2026-10')
        for params in (
            {'instance': 'testpilot', 'item': '../config', 'month': '2026-10'},
            {'instance': 'testpilot', 'item': 'Plate', 'month': '2026-13'},
            {'instance': 'testpilot', 'item': 'Plate', 'month': '2026-10', 'path': '../config'},
        ):
            with self.subTest(params=params), self.assertRaises(ValidationError):
                router.dispatch(method, params)
        with patch.dict('os.environ', {'DEMO': '1'}), self.assertRaises(ApiError) as raised:
            router.dispatch(method, {'instance': 'testpilot', 'item': 'Plate', 'month': '2026-10'})
        self.assertEqual(raised.exception.code, 'READ_ONLY')


class LocalDirectoryOpenerTests(unittest.TestCase):
    def test_gallery_keeps_directory_creation_and_uses_common_opener(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'gallery'
            with patch.object(background_service, 'LIBRARY_DIR', path), \
                    patch.object(background_service, 'open_local_directory') as opener:
                self.assertEqual(background_service.gallery_open(), {'path': str(path)})
            self.assertTrue(path.is_dir())
            opener.assert_called_once_with(path)

    def test_common_opener_uses_windows_shell_without_creating_directory(self):
        path = Path('archive')
        with patch.object(background_service.os, 'name', 'nt'), \
                patch('module.api.windows_directory.open_directory') as opener:
            background_service.open_local_directory(path)
        opener.assert_called_once_with(path)

    def test_common_opener_uses_macos_command_arguments(self):
        path = Path('archive with spaces')
        with patch.object(background_service.os, 'name', 'posix'), \
                patch.object(background_service.sys, 'platform', 'darwin'), \
                patch.object(background_service.subprocess, 'Popen') as opener:
            background_service.open_local_directory(path)
        opener.assert_called_once_with(['open', str(path)])

    def test_common_opener_uses_linux_command_arguments(self):
        path = Path('archive with spaces')
        with patch.object(background_service.os, 'name', 'posix'), \
                patch.object(background_service.sys, 'platform', 'linux'), \
                patch.object(background_service.subprocess, 'Popen') as opener:
            background_service.open_local_directory(path)
        opener.assert_called_once_with(['xdg-open', str(path)])

    def test_common_opener_wraps_system_error(self):
        path = Path('archive')
        with patch.object(background_service.os, 'name', 'nt'), \
                patch('module.api.windows_directory.open_directory', side_effect=OSError('private-detail')):
            with self.assertRaises(background_service.BackgroundError) as raised:
                background_service.open_local_directory(path)
        self.assertEqual(str(raised.exception), '打开文件夹失败：OSError')

    def test_common_opener_reports_opened_directory_without_confirmed_foreground(self):
        from module.api.windows_directory import DirectoryForegroundError, FOREGROUND_FAILURE_MESSAGE

        path = Path('archive')
        with patch.object(background_service.os, 'name', 'nt'), \
                patch('module.api.windows_directory.open_directory',
                      side_effect=DirectoryForegroundError(FOREGROUND_FAILURE_MESSAGE)):
            with self.assertRaises(background_service.BackgroundError) as raised:
                background_service.open_local_directory(path)
        self.assertEqual(str(raised.exception), FOREGROUND_FAILURE_MESSAGE)


if __name__ == '__main__':
    unittest.main()
