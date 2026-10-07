"""指挥喵逐只报告回归：真实临时报告配合扫描桩，不操作游戏。"""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from deploy.atomic import file_write
from module.exception import GameStuckError, RequestHumanTakeover
from module.meowfficer.scan_capture import ScanCapture
from module.meowfficer.score import evaluate
from module.meowfficer.score_task import MeowfficerScore


def _capture(name='奥古喵', names=('侵略如火',), rarity='SSR'):
    """使用实际评分库构造天赋，身份、完整性和品质由扫描桩确认。"""
    return ScanCapture(display_name=name, talents=evaluate(list(names), cat=name).talents,
                       level=30, breed=name, rarity=rarity, complete=True,
                       identity_confirmed=True, talents_complete=True)


class LiveReportTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.report = self.root / 'reports/meowfficer_score.md'
        self.json_report = self.report.with_suffix('.json')
        self.task = object.__new__(MeowfficerScore)
        self.task.config = SimpleNamespace(MeowfficerScore_Source='scan',
                                           MeowfficerScore_LockByAdvice=False,
                                           MeowfficerScore_ScanLimit=0,
                                           MeowfficerScore_ScanPasses=12,
                                           MeowfficerScore_ReportPath=str(self.report))
        self.task.device = SimpleNamespace(screenshot=Mock(), click=Mock(), swipe=Mock(),
                                           click_record_remove=Mock())
        self.task.results = []
        self.task.lock_actions = []
        self.task.scanned_count = 0
        logs = patch('module.meowfficer.score_task.logger')
        self.logger = logs.start()
        self.addCleanup(logs.stop)
        cn = patch('module.config.server.server', 'cn')
        cn.start()
        self.addCleanup(cn.stop)

    def _payload(self):
        return json.loads(self.json_report.read_text(encoding='utf-8'))

    def _scanner(self, effect):
        scanner = SimpleNamespace(scanned=[], scan_all=Mock())
        scanner.scan_all.side_effect = lambda **kwargs: effect(scanner, **kwargs)
        constructor = patch('module.meowfficer.scan.MeowfficerScanner', return_value=scanner)
        constructor.start()
        self.addCleanup(constructor.stop)
        return scanner

    def _accept(self, scanner, callback, entry):
        scanner.scanned.append(entry)
        callback(scanner, entry)

    def _assert_no_game_actions(self):
        self.task.device.screenshot.assert_not_called()
        self.task.device.click.assert_not_called()
        self.task.device.swipe.assert_not_called()

    def test_first_and_second_same_name_cats_are_visible_before_scan_returns(self):
        snapshots = []

        def scan(scanner, *, on_result, **kwargs):
            for talent in ('侵略如火', '不动如山'):
                self._accept(scanner, on_result, ('林德喵', [talent], 30))
                snapshots.append(self._payload())
                # 浏览器用 JSON，导出文件也应在下一只猫开始前写好。
                self.assertIn(talent, self.report.read_text(encoding='utf-8'))
                self.assertIn(talent, self.report.with_suffix('.html').read_text(encoding='utf-8'))
            return scanner.scanned

        self._scanner(scan)
        with patch('module.meowfficer.score_task.evaluate', wraps=evaluate) as scoring:
            self.task._run_scan()

        self.assertEqual([payload['count'] for payload in snapshots], [1, 2])
        self.assertEqual([payload['scannedCount'] for payload in snapshots], [1, 2])
        self.assertEqual([cat['source'] for cat in snapshots[1]['cats']], ['林德喵', '林德喵'])
        self.assertEqual([cat['talents'][0]['name'] for cat in snapshots[1]['cats']],
                         ['侵略如火', '不动如山'])
        self.assertEqual(len(self.task.results), 2)
        self.assertEqual(scoring.call_count, 2)
        self._assert_no_game_actions()

    def test_live_result_is_not_added_again_when_next_cat_requires_takeover(self):
        original = RequestHumanTakeover('下一只身份无法确认')

        def scan(scanner, *, on_result, **kwargs):
            self._accept(scanner, on_result, ('林德喵', ['侵略如火'], 30))
            self.assertEqual(self._payload()['count'], 1)
            raise original

        self._scanner(scan)
        with self.assertRaises(RequestHumanTakeover) as raised:
            self.task._run_scan()

        self.assertIs(raised.exception, original)
        self.assertEqual(len(self.task.results), 1)
        self.assertEqual(self._payload()['count'], 1)
        self.assertEqual(self._payload()['scannedCount'], 1)
        self._assert_no_game_actions()

    def test_exception_fallback_only_scores_suffix_that_had_no_callback(self):
        original = GameStuckError('旧扫描流程在返回前异常')

        def scan(scanner, *, on_result, **kwargs):
            self._accept(scanner, on_result, ('林德喵', ['侵略如火'], 30))
            scanner.scanned.extend([('蓝猫', [], 10), ('林德喵', ['不动如山'], 30)])
            raise original

        self._scanner(scan)
        with patch('module.meowfficer.score_task.evaluate', wraps=evaluate) as scoring:
            with self.assertRaises(GameStuckError) as raised:
                self.task._run_scan()

        self.assertIs(raised.exception, original)
        self.assertEqual(scoring.call_count, 2)
        payload = self._payload()
        self.assertEqual(payload['count'], 2)
        self.assertEqual(payload['scannedCount'], 3)
        self.assertEqual([cat['talents'][0]['name'] for cat in payload['cats']],
                         ['侵略如火', '不动如山'])

    def test_blue_cat_updates_scan_progress_without_creating_a_score(self):
        def scan(scanner, *, on_result, **kwargs):
            self._accept(scanner, on_result, ('蓝猫', [], 10))
            payload = self._payload()
            self.assertEqual(payload['count'], 0)
            self.assertEqual(payload['scannedCount'], 1)
            self.assertEqual(payload['cats'], [])
            return scanner.scanned

        self._scanner(scan)
        with patch('module.meowfficer.score_task.evaluate') as scoring:
            self.task._run_scan()
        scoring.assert_not_called()
        self.assertEqual(self.task.results, [])

    def test_run_replaces_previous_report_with_empty_current_scan_before_first_cat(self):
        self.task.results = [('上次扫描', evaluate(['侵略如火'], cat='奥古喵'))]
        self.task.scanned_count = 1
        self.task._save_report()

        def scan():
            payload = self._payload()
            self.assertEqual(payload['count'], 0)
            self.assertEqual(payload['scannedCount'], 0)
            self.assertEqual(payload['cats'], [])
            self.assertEqual(self.task.results, [])

        self.task._run_scan = Mock(side_effect=scan)
        self.task.run()
        self.task._run_scan.assert_called_once_with()

    def test_quiet_per_cat_publish_does_not_repeat_export_log_messages(self):
        self.task.results.append(('奥古喵', evaluate(['侵略如火'], cat='奥古喵')))
        self.task._save_report(quiet=True)
        self.assertEqual(self._payload()['count'], 1)
        self.logger.info.assert_not_called()
        self.logger.warning.assert_not_called()

    def test_non_scan_report_keeps_optional_scan_progress_out_of_payload(self):
        del self.task.scanned_count
        self.task.config.MeowfficerScore_Source = 'screenshot'
        self.task.results.append(('截图.png', evaluate(['侵略如火'], cat='奥古喵')))
        self.task._save_report(quiet=True)
        self.assertNotIn('scannedCount', self._payload())

    def test_screenshot_is_published_immediately_after_successful_score(self):
        del self.task.scanned_count
        talents = _capture().talents
        with patch('module.meowfficer.score_ocr.recognize', return_value=(talents, '奥古喵')):
            result = self.task._score_image(object(), object(), '第一张.png')
        self.assertIsNotNone(result)
        self.assertEqual(self._payload()['count'], 1)
        self.assertEqual(self._payload()['cats'][0]['source'], '第一张.png')

    def test_failed_file_write_warns_but_does_not_interrupt_reading_next_cat(self):
        def scan(scanner, *, on_result, **kwargs):
            self._accept(scanner, on_result, ('林德喵', ['侵略如火'], 30))
            self._accept(scanner, on_result, ('林德喵', ['不动如山'], 30))
            return scanner.scanned

        self._scanner(scan)
        with patch('deploy.atomic.file_write', side_effect=OSError('临时磁盘不可写')):
            self.task._run_scan()
        self.assertEqual(len(self.task.results), 2)
        self.assertEqual(self.task.scanned_count, 2)
        self.assertTrue(self.logger.warning.called)
        self.task._save_report(quiet=True)
        self.assertEqual(self._payload()['count'], 2)

    def test_reader_at_atomic_replace_sees_complete_old_or_new_json(self):
        self.task.results.append(('第一只', evaluate(['侵略如火'], cat='奥古喵')))
        self.task.scanned_count = 1
        self.task._save_report(quiet=True)
        self.task.results.append(('第二只', evaluate(['不动如山'], cat='奥古喵')))
        self.task.scanned_count = 2
        replaced = []
        observed = []
        original_replace = os.replace

        def replace(source, destination):
            replaced.append(Path(destination).suffix)
            if Path(destination) == self.json_report:
                observed.append(self._payload())
                # 源文件应已完整落盘，目标仍保留原报告。
                pending = json.loads(Path(source).read_text(encoding='utf-8'))
                self.assertEqual(pending['count'], 2)
                self.assertEqual(observed[-1]['count'], 1)
            original_replace(source, destination)
            if Path(destination) == self.json_report:
                observed.append(self._payload())

        with patch('deploy.atomic.os.replace', side_effect=replace):
            self.task._save_report(quiet=True)
        self.assertEqual(replaced, ['.md', '.html', '.json'])
        self.assertEqual([payload['count'] for payload in observed], [1, 2])
        self.assertEqual([len(payload['cats']) for payload in observed], [1, 2])

    def test_partial_json_temp_write_preserves_existing_report_and_later_recovers(self):
        self.task.results.append(('第一只', evaluate(['侵略如火'], cat='奥古喵')))
        self.task.scanned_count = 1
        self.task._save_report(quiet=True)
        original = self.json_report.read_bytes()
        self.task.results.append(('第二只', evaluate(['不动如山'], cat='奥古喵')))
        self.task.scanned_count = 2

        def fail_json(file, data):
            if str(file).startswith(str(self.json_report) + '.'):
                Path(file).write_text('{"cats":', encoding='utf-8')
                raise OSError('写入中断')
            return file_write(file, data)

        with patch('deploy.atomic.file_write', side_effect=fail_json):
            self.task._save_report(quiet=True)
        self.assertEqual(self.json_report.read_bytes(), original)
        self.assertEqual(self._payload()['count'], 1)
        self.assertTrue(self.logger.warning.called)
        self.task._save_report(quiet=True)
        self.assertEqual(self._payload()['count'], 2)

    def test_lock_report_publishes_after_actual_state_confirmation(self):
        self.task.results.append(('上一只', evaluate(['侵略如火'], cat='奥古喵')))
        self.task.scanned_count = 1
        self.task._save_report(quiet=True)
        current = _capture(names=('炮击新手·主力', '装填新手·战列'))

        def confirm(scanner, capture, target, reason, entry):
            # 评分已经算好，但核验中的锁动作不得提前呈现为完成。
            self.assertEqual(self._payload()['count'], 1)
            self.assertNotIn('lockActions', self._payload())
            self.assertIs(target, False)
            entry.update(before=True, after=False, status='changed')
            return entry

        with patch('module.meowfficer.score_lock.set_lock_state', side_effect=confirm):
            self.task._score_and_apply_lock(SimpleNamespace(device=self.task.device), current)
        payload = self._payload()
        self.assertEqual(payload['count'], 2)
        self.assertEqual(payload['lockActions'][0]['status'], 'changed')
        self.assertIs(payload['lockActions'][0]['after'], False)

    def test_unconfirmed_lock_remains_unconfirmed_in_live_report_and_stops(self):
        current = _capture(names=('炮击新手·主力', '装填新手·战列'))

        def confirm(scanner, capture, target, reason, entry):
            entry.update(before=True, after=None, status='unconfirmed')
            return entry

        with patch('module.meowfficer.score_lock.set_lock_state', side_effect=confirm):
            with self.assertRaises(RequestHumanTakeover):
                self.task._score_and_apply_lock(SimpleNamespace(device=self.task.device), current)
        payload = self._payload()
        self.assertEqual(payload['count'], 1)
        self.assertEqual(payload['lockActions'][0]['status'], 'unconfirmed')
        self.assertIsNone(payload['lockActions'][0]['after'])

    def test_device_failure_during_lock_keeps_original_error_and_current_action(self):
        self.task.config.MeowfficerScore_LockByAdvice = True
        original = GameStuckError('核验锁状态时设备异常')
        current = _capture(names=('炮击新手·主力', '装填新手·战列'))

        def confirm(scanner, capture, target, reason, entry):
            entry.update(before=True, after=None, status='unconfirmed')
            raise original

        def scan(scanner, *, on_cat, **kwargs):
            on_cat(scanner, current)

        self._scanner(scan)
        with patch('module.meowfficer.score_lock.set_lock_state', side_effect=confirm):
            with self.assertRaises(GameStuckError) as raised:
                self.task._run_scan()
        self.assertIs(raised.exception, original)
        payload = self._payload()
        self.assertEqual(payload['count'], 1)
        self.assertEqual(payload['lockActions'][0]['status'], 'unconfirmed')
        self.assertIsNone(payload['lockActions'][0]['after'])

    def test_blue_lock_action_is_published_without_scoring(self):
        current = _capture(name='蓝猫', names=(), rarity='R')

        def confirm(scanner, capture, target, reason, entry):
            self.assertIs(target, False)
            entry.update(before=False, after=False, status='unchanged')
            return entry

        with patch('module.meowfficer.score_task.evaluate') as scoring, \
                patch('module.meowfficer.score_lock.set_lock_state', side_effect=confirm):
            self.task._score_and_apply_lock(SimpleNamespace(device=self.task.device), current)
        scoring.assert_not_called()
        payload = self._payload()
        self.assertEqual(payload['count'], 0)
        self.assertEqual(payload['scannedCount'], 1)
        self.assertEqual(payload['lockActions'][0]['name'], '蓝猫')
        self.assertEqual(payload['lockActions'][0]['status'], 'unchanged')


if __name__ == '__main__':
    unittest.main()
