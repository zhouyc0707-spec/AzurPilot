"""当前起点首只和第十二只刷新：真实评分、临时实时报告，不操作游戏。"""

import unittest
from unittest.mock import Mock, patch

from module.exception import GameStuckError, RequestHumanTakeover
from tests import test_meowfficer_live_report as live_report_tests


_capture = live_report_tests._capture


class CurrentStartLockTests(unittest.TestCase):
    _payload = live_report_tests.LiveReportTests._payload
    _scanner = live_report_tests.LiveReportTests._scanner

    def setUp(self):
        live_report_tests.LiveReportTests.setUp(self)
        self.task.config.MeowfficerScore_LockByAdvice = True
        self.task.config.MeowfficerScore_ScanStart = 'current'
        self.state = True
        self.events = []
        self.created_scanner = None

    def normal(self, scanner, capture, target, reason, entry):
        ordinal = len(self.task.lock_actions)
        self.events.append(('advice', ordinal))
        before = self.state
        self.state = target
        entry.update(before=before, after=target,
                     status='changed' if before is not target else 'unchanged')
        return entry

    def refresh(self, scanner, capture, entry):
        self.events.append(('refresh', len(self.task.lock_actions)))
        entry.update(before=self.state, after=self.state, status='verified', reason='原锁状态已恢复')
        return entry

    def install_scan(self, count, *, blue=False, names=None):
        def scan(scanner, **kwargs):
            expected_current = self.task.config.MeowfficerScore_ScanStart == 'current'
            if expected_current:
                self.assertIs(kwargs.get('start_current'), True)
            else:
                self.assertNotIn('start_current', kwargs)
            scanner.device = self.task.device
            for index in range(count):
                current = _capture(rarity='R', names=()) if blue else _capture()
                if names is not None:
                    current = _capture(names=names)
                if 'on_cat' in kwargs:
                    kwargs['on_cat'](scanner, current)
                    scanner.scanned.append((current.display_name, current.talents, current.level))
                else:
                    row = current.display_name, current.talents, current.level
                    scanner.scanned.append(row)
                    kwargs['on_result'](scanner, row)
            return scanner.scanned

        self.created_scanner = self._scanner(scan)
        return self.created_scanner

    def run_many(self, count, **kwargs):
        scanner = self.install_scan(count, **kwargs)
        with patch('module.meowfficer.score_lock.set_lock_state', side_effect=self.normal) as normal, \
                patch('module.meowfficer.lock_refresh.refresh_lock_state', side_effect=self.refresh) as refresh:
            self.task._run_scan()
        return scanner, normal, refresh

    def test_current_bonus_is_first_then_existing_twelve_twenty_four_not_thirteen_twenty_five(self):
        for count, expected in ((1, [1]), (11, [1]), (12, [1, 12]),
                                (13, [1, 12]), (24, [1, 12, 24]), (25, [1, 12, 24])):
            with self.subTest(count=count):
                self.setUp()
                scanner, normal, refresh = self.run_many(count)
                self.assertEqual([ordinal for kind, ordinal in self.events if kind == 'refresh'], expected)
                self.assertEqual(refresh.call_count, len(expected))
                self.assertEqual(normal.call_count, count)
                self.assertEqual(len(scanner.scanned), count)
                self.assertEqual(len(self.task.lock_actions), count)
                self.assertEqual(len(self.task.results), count)
                for ordinal in expected:
                    position = self.events.index(('refresh', ordinal))
                    self.assertEqual(self.events[position - 1], ('advice', ordinal))
                    metadata = self.task.lock_actions[ordinal - 1]['periodicRefresh']
                    self.assertEqual(metadata['ordinal'], ordinal)
                    self.assertEqual(metadata['trigger'], 'current_start' if ordinal == 1 else 'periodic')
                payload = self._payload()
                self.assertEqual(payload['scannedCount'], count)
                self.assertEqual(payload['count'], count)

    def test_first_refresh_preserves_advice_final_unlock_instead_of_initial_lock(self):
        _scanner, normal, refresh = self.run_many(1, names=('炮击新手·主力', '装填新手·战列'))
        normal.assert_called_once()
        refresh.assert_called_once()
        action = self._payload()['lockActions'][0]
        self.assertIs(action['before'], True)
        self.assertIs(action['target'], False)
        self.assertIs(action['after'], False)
        self.assertIs(action['periodicRefresh']['before'], False)
        self.assertIs(action['periodicRefresh']['after'], False)
        self.assertEqual(action['periodicRefresh']['trigger'], 'current_start')
        self.assertIn('当前起点首只刷新', action['reason'])
        self.assertNotIn('每 12 只刷新', action['reason'])

    def test_current_blue_first_cat_updates_progress_and_unlocked_refresh_without_score(self):
        scanner, normal, refresh = self.run_many(1, blue=True)
        self.assertEqual(len(scanner.scanned), 1)
        self.assertEqual(self.task.results, [])
        normal.assert_called_once()
        refresh.assert_called_once()
        payload = self._payload()
        self.assertEqual(payload['count'], 0)
        self.assertEqual(payload['scannedCount'], 1)
        action = payload['lockActions'][0]
        self.assertIs(action['target'], False)
        self.assertIs(action['periodicRefresh']['before'], False)
        self.assertIs(action['periodicRefresh']['after'], False)

    def test_cattery_start_has_no_first_bonus_or_new_scanner_keyword(self):
        self.task.config.MeowfficerScore_ScanStart = 'cattery'
        _scanner, normal, refresh = self.run_many(13)
        self.assertEqual([ordinal for kind, ordinal in self.events if kind == 'refresh'], [12])
        self.assertEqual(normal.call_count, 13)
        refresh.assert_called_once()
        self.assertNotIn('periodicRefresh', self.task.lock_actions[0])
        self.assertEqual(self.task.lock_actions[11]['periodicRefresh']['trigger'], 'periodic')

    def test_missing_start_setting_keeps_legacy_cattery_call_and_periodic_behavior(self):
        del self.task.config.MeowfficerScore_ScanStart

        def scan(scanner, **kwargs):
            self.assertNotIn('start_current', kwargs)
            scanner.device = self.task.device
            for _ in range(12):
                kwargs['on_cat'](scanner, _capture())
            return scanner.scanned

        scanner = self._scanner(scan)
        with patch('module.meowfficer.score_lock.set_lock_state', side_effect=self.normal), \
                patch('module.meowfficer.lock_refresh.refresh_lock_state', side_effect=self.refresh) as refresh:
            self.task._run_scan()
        scanner.scan_all.assert_called_once()
        refresh.assert_called_once()
        self.assertEqual([ordinal for kind, ordinal in self.events if kind == 'refresh'], [12])

    def test_current_readonly_advice_disabled_never_runs_first_or_periodic_pair(self):
        self.task.config.MeowfficerScore_LockByAdvice = False
        scanner, normal, refresh = self.run_many(24)
        normal.assert_not_called()
        refresh.assert_not_called()
        self.assertEqual(len(scanner.scanned), 24)
        payload = self._payload()
        self.assertEqual(payload['count'], 24)
        self.assertEqual(payload['scannedCount'], 24)
        self.assertEqual(payload.get('lockActions', []), [])
        self.assertEqual(self.task.lock_actions, [])
        self.task.device.click.assert_not_called()
        self.task.device.swipe.assert_not_called()

    def test_first_pair_device_error_preserves_partial_shared_steps_json_and_original_exception(self):
        scanner = self.install_scan(2)
        error = GameStuckError('匿名当前首只双切异常')

        def partial(_scanner, capture, entry):
            entry.update(before=True, after=None, status='unconfirmed', reason='点击后设备异常')
            entry['steps'].append({'name': capture.display_name, 'status': 'sent', 'after': None})
            raise error

        with patch('module.meowfficer.score_lock.set_lock_state', side_effect=self.normal), \
                patch('module.meowfficer.lock_refresh.refresh_lock_state', side_effect=partial) as refresh:
            with self.assertRaises(GameStuckError) as raised:
                self.task._run_scan()
        self.assertIs(raised.exception, error)
        refresh.assert_called_once()
        self.assertEqual(scanner.scanned, [])
        payload = self._payload()
        self.assertEqual(payload['scannedCount'], 1)
        self.assertEqual(payload['count'], 1)
        action = payload['lockActions'][0]
        self.assertEqual(action['status'], 'unconfirmed')
        self.assertIsNone(action['after'])
        self.assertEqual(action['periodicRefresh']['ordinal'], 1)
        self.assertEqual(action['periodicRefresh']['trigger'], 'current_start')
        self.assertEqual(action['periodicRefresh']['steps'][0]['status'], 'sent')

    def test_first_pair_preflight_error_is_published_as_unconfirmed_without_click_steps(self):
        self.install_scan(1)
        error = GameStuckError('匿名当前首只预检异常')
        with patch('module.meowfficer.score_lock.set_lock_state', side_effect=self.normal), \
                patch('module.meowfficer.lock_refresh.refresh_lock_state', side_effect=error):
            with self.assertRaises(GameStuckError) as raised:
                self.task._run_scan()
        self.assertIs(raised.exception, error)
        action = self._payload()['lockActions'][0]
        self.assertEqual(action['status'], 'unconfirmed')
        self.assertEqual(action['periodicRefresh']['status'], 'unconfirmed')
        self.assertEqual(action['periodicRefresh']['steps'], [])
        self.assertEqual(action['periodicRefresh']['trigger'], 'current_start')
        self.assertIn('未开始切换', action['reason'])

    def test_unconfirmed_normal_advice_stops_before_current_first_pair(self):
        scanner = self.install_scan(2)

        def failed(_scanner, capture, target, reason, entry):
            entry.update(before=True, after=None, status='unconfirmed')

        with patch('module.meowfficer.score_lock.set_lock_state', side_effect=failed), \
                patch('module.meowfficer.lock_refresh.refresh_lock_state') as refresh:
            with self.assertRaises(RequestHumanTakeover):
                self.task._run_scan()
        refresh.assert_not_called()
        self.assertEqual(scanner.scanned, [])
        action = self._payload()['lockActions'][0]
        self.assertEqual(action['status'], 'unconfirmed')
        self.assertNotIn('periodicRefresh', action)
        self.task.device.click_record_remove.assert_not_called()

    def test_skipped_normal_advice_cannot_silently_skip_required_current_first_pair(self):
        self.install_scan(2)
        with patch('module.meowfficer.score_lock.set_lock_state'), \
                patch('module.meowfficer.lock_refresh.refresh_lock_state') as refresh:
            with self.assertRaises(RequestHumanTakeover):
                self.task._run_scan()
        refresh.assert_not_called()
        action = self._payload()['lockActions'][0]
        self.assertEqual(action['status'], 'unconfirmed')
        self.assertEqual(action['periodicRefresh']['ordinal'], 1)
        self.assertEqual(action['periodicRefresh']['trigger'], 'current_start')
        self.assertEqual(action['periodicRefresh']['steps'], [])
        self.task.device.click_record_remove.assert_not_called()

    def test_first_current_page_read_error_publishes_empty_scan_and_propagates_original_error(self):
        error = GameStuckError('匿名当前页首次身份读取异常')

        def failed_read(scanner, **kwargs):
            self.assertIs(kwargs.get('start_current'), True)
            raise error

        self._scanner(failed_read)
        with patch('module.meowfficer.lock_refresh.refresh_lock_state') as refresh:
            with self.assertRaises(GameStuckError) as raised:
                self.task.run()
        self.assertIs(raised.exception, error)
        refresh.assert_not_called()
        payload = self._payload()
        self.assertEqual(payload['count'], 0)
        self.assertEqual(payload['scannedCount'], 0)
        self.assertEqual(payload.get('lockActions', []), [])
        self.task.device.click.assert_not_called()

    def test_invalid_start_option_does_not_enter_scanner_or_lock_flow(self):
        self.task.config.MeowfficerScore_ScanStart = 'unknown'
        scanner = self._scanner(lambda *_args, **_kwargs: self.fail('配置无效不得开始扫描'))
        with patch('module.meowfficer.lock_refresh.refresh_lock_state') as refresh:
            with self.assertRaises(RequestHumanTakeover):
                self.task._run_scan()
        scanner.scan_all.assert_not_called()
        refresh.assert_not_called()
        self.task.device.click.assert_not_called()

    def test_scan_start_does_not_change_non_scan_source_or_enable_game_lock_operations(self):
        for source, method in (('device', '_run_device'), ('screenshot', '_run_screenshots')):
            with self.subTest(source=source):
                self.task.config.MeowfficerScore_Source = source
                self.task._run_scan = Mock()
                self.task._run_device = Mock()
                self.task._run_screenshots = Mock()
                self.task._log_summary = Mock()
                self.task._save_report = Mock()
                with patch('module.meowfficer.lock_refresh.refresh_lock_state') as refresh:
                    self.task.run()
                getattr(self.task, method).assert_called_once()
                self.task._run_scan.assert_not_called()
                refresh.assert_not_called()
                self.task.device.click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
