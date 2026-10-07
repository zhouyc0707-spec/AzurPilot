"""扫描锁定审计的只读报告接口，所有报告及实例均位于临时目录。"""

import json
import tempfile
import unittest

from module.api.config_service import ConfigService
from module.api.meowfficer_service import report, report_path
from module.api.protocol import ApiError
from tests.test_api import fixture


def action(name='蓝猫', **changes):
    result = dict(name=name, before=True, after=False, target=False,
                  status='changed', reason='蓝猫不评分，保持未锁定')
    result.update(changes)
    return result


class MeowfficerLockReportApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='meow-report-api-')
        self.addCleanup(temporary.cleanup)
        self.root = fixture(temporary.name)
        self.configs = ConfigService(self.root)

    def write_report(self, data):
        path = report_path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        return path

    def test_old_report_response_keeps_original_fields(self):
        self.write_report({'generatedAt': 'T', 'cats': [{'cat': 'A'}]})
        self.assertEqual(report(self.configs, 'testpilot'),
                         {'instance': 'testpilot', 'generatedAt': 'T', 'count': 1, 'cats': [{'cat': 'A'}]})

    def test_scan_progress_counts_blue_cats_without_fabricating_scores(self):
        self.write_report({'generatedAt': 'T', 'cats': [], 'scannedCount': 3})
        self.assertEqual(report(self.configs, 'testpilot'),
                         {'instance': 'testpilot', 'generatedAt': 'T', 'count': 0,
                          'cats': [], 'scannedCount': 3})
        self.write_report({'cats': [], 'scannedCount': 0})
        self.assertEqual(report(self.configs, 'testpilot')['scannedCount'], 0)

    def test_read_progress_is_not_reduced_by_the_display_limit(self):
        self.write_report({'cats': [{'cat': 'A'}, {'cat': 'B'}], 'scannedCount': 12})
        result = report(self.configs, 'testpilot', limit=1)
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['cats'], [{'cat': 'B'}])
        self.assertEqual(result['scannedCount'], 12)

    def test_invalid_progress_is_rejected_instead_of_claiming_a_read_count(self):
        for value in (True, False, -1, 1.5, '3', None, []):
            with self.subTest(value=value):
                self.write_report({'cats': [], 'scannedCount': value})
                with self.assertRaises(ApiError) as caught:
                    report(self.configs, 'testpilot')
                self.assertEqual(caught.exception.code, 'INTERNAL')

    def test_zero_scores_preserve_blue_and_failed_actions_with_latest_limit(self):
        first = action('更早的蓝猫')
        second = action('同名蓝猫')
        third = action('同名蓝猫', before=None, after=None, target=None, status='skipped', reason='身份未确认')
        self.write_report({'generatedAt': 'T', 'cats': [], 'lockActions': [first, second, third]})
        result = report(self.configs, 'testpilot', limit=2)
        self.assertEqual(result['count'], 0)
        self.assertEqual(result['cats'], [])
        self.assertEqual(result['lockActions'], [second, third])

    def test_only_known_fields_with_strict_state_and_status_are_forwarded(self):
        valid = action(extra='不能转发的额外字段')
        malformed = [None, '文字', {}, action(name=42), action(reason=[]), action(status=[]),
                     action(status='not-a-state'), action(before=1), action(after='false'), action(target=0)]
        self.write_report({'cats': [], 'lockActions': [*malformed, valid]})
        self.assertEqual(report(self.configs, 'testpilot')['lockActions'], [action()])

    def test_optional_empty_actions_are_preserved(self):
        self.write_report({'cats': [], 'lockActions': []})
        self.assertEqual(report(self.configs, 'testpilot')['lockActions'], [])

    def test_non_list_actions_are_reported_as_invalid_structure(self):
        for value in (None, {}, 'bad'):
            with self.subTest(value=value):
                self.write_report({'cats': [], 'lockActions': value})
                with self.assertRaises(ApiError) as caught:
                    report(self.configs, 'testpilot')
                self.assertEqual(caught.exception.code, 'INTERNAL')

    def test_read_keeps_original_report_bytes_and_modification_time(self):
        path = self.write_report({'cats': [], 'lockActions': [action()]})
        before = path.read_bytes(), path.stat().st_mtime_ns
        report(self.configs, 'testpilot')
        self.assertEqual(before, (path.read_bytes(), path.stat().st_mtime_ns))

    def test_router_forwards_action_records_and_applies_limit(self):
        from module.api.router import Router

        self.write_report({'cats': [], 'lockActions': [action('早'), action('新')]})
        result = Router(self.configs, None).dispatch('meowfficer.scoreReport', {'instance': 'testpilot', 'limit': 1})
        self.assertEqual(result['lockActions'], [action('新')])


if __name__ == '__main__':
    unittest.main()
