"""猫王接口只读受控截图，不接受用户路径或触发设备操作。"""
import base64
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from module.api.akashi_record_service import report
from module.api.config_service import ConfigService
from module.api.protocol import AkashiRecordParams, ApiError
from module.api.router import Router
from module.statistics.akashi_shop_records import AkashiShopRecords
from tests.test_api import fixture


class AkashiRecordApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='akashi-record-api-')
        self.addCleanup(temporary.cleanup)
        self.root = fixture(temporary.name)
        self.configs = ConfigService(self.root)
        self.store = AkashiShopRecords(self.configs.directory)

    def save(self):
        self.store.record('testpilot', 'cn', 340, {'20': 2, '50': 2, '100': 2},
                          np.zeros((720, 1280, 3), dtype=np.uint8))

    def test_empty_record_is_explicit_and_does_not_start_a_device(self):
        result = report(self.configs, 'testpilot', 'monthly')
        self.assertEqual(result['instance'], 'testpilot')
        self.assertEqual(result['scope'], 'monthly')
        self.assertIsNone(result['record'])
        self.assertEqual(len(result['month']), 7)

    def test_full_image_and_metadata_are_served_without_local_path(self):
        self.save()
        result = report(self.configs, 'testpilot', 'history')
        record = result['record']
        self.assertEqual(record['totalAp'], 340)
        self.assertEqual(record['boxes'], {'20': 2, '50': 2, '100': 2})
        self.assertNotIn('screenshot', record)
        self.assertTrue(record['image'].startswith('data:image/png;base64,'))
        content = base64.b64decode(record['image'].split(',', 1)[1])
        self.assertEqual(content[:8], b'\x89PNG\r\n\x1a\n')

    def test_missing_image_preserves_record_and_marks_unavailable(self):
        self.save()
        selected = self.store.records('testpilot', 'cn')['monthly']
        (self.store.screenshot_root / selected['screenshot']).unlink()
        result = report(self.configs, 'testpilot', 'monthly')['record']
        self.assertEqual(result['totalAp'], 340)
        self.assertIsNone(result['image'])
        self.assertTrue(result['imageMissing'])

    def test_instance_must_exist_and_cannot_escape_config_directory(self):
        for name in ('missing', '../testpilot'):
            with self.subTest(instance=name), self.assertRaises(ApiError):
                report(self.configs, name, 'history')

    def test_scope_and_extra_paths_are_rejected(self):
        for params in (
            {'instance': 'testpilot', 'scope': 'other'},
            {'instance': 'testpilot', 'scope': 'history', 'path': '../../secret'},
        ):
            with self.subTest(params=params), self.assertRaises(ValueError):
                AkashiRecordParams.model_validate(params)

    def test_rpc_is_read_only_and_calls_only_record_service(self):
        router = Router(self.configs, None)
        self.assertFalse(router.methods['statistics.akashiRecord'].mutates)
        with patch('module.api.akashi_record_service.report', return_value={'record': None}) as reader:
            result = router.dispatch('statistics.akashiRecord', {'instance': 'testpilot', 'scope': 'history'})
        self.assertEqual(result, {'record': None})
        reader.assert_called_once_with(self.configs, 'testpilot', 'history')

    def test_image_path_failure_does_not_expose_or_replace_the_record(self):
        self.save()
        with patch.object(AkashiShopRecords, 'image', side_effect=ValueError('路径不合法')):
            result = report(self.configs, 'testpilot', 'history')
        self.assertTrue(result['record']['imageMissing'])
        self.assertNotIn('screenshot', result['record'])


if __name__ == '__main__':
    unittest.main()
