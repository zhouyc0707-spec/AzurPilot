"""每十二条已接受记录的刷新时机、报告与真实点击历史保护，均不连接游戏。"""

from collections import deque
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.device.device import Device
from module.exception import GameStuckError, RequestHumanTakeover
from module.meowfficer.score_lock import LOCK_BUTTON
from module.meowfficer.score_task import MeowfficerScore
from tests import test_meowfficer_live_report as live_report_tests
from tests.test_meowfficer_lock_transition import _FrameTimer
from tests.test_meowfficer_score_lock import DetailPage, runner


_capture = live_report_tests._capture


class PeriodicRefreshTests(unittest.TestCase):
    def setUp(self):
        self.task = runner()
        self.task._publish_report = Mock()
        self.scanner = SimpleNamespace(device=SimpleNamespace(click_record_remove=Mock()), scanned=[])
        self.state = True
        self.events = []
        cn = patch('module.config.server.server', 'cn')
        cn.start()
        self.addCleanup(cn.stop)

    def apply(self, _scanner, _capture, target, _reason, entry):
        self.events.append(('advice', len(self.task.lock_actions)))
        before = self.state
        self.state = target
        entry.update(before=before, after=target, status='changed' if before != target else 'unchanged')
        return entry

    def refresh(self, _scanner, _capture, entry):
        self.events.append(('refresh', len(self.task.lock_actions)))
        entry.update(before=self.state, after=self.state, status='verified', reason='原锁状态已恢复')
        return entry

    def run_cats(self, count, *, blue=False):
        with patch('module.meowfficer.score_lock.set_lock_state', side_effect=self.apply), \
                patch('module.meowfficer.lock_refresh.refresh_lock_state', side_effect=self.refresh) as refresh, \
                patch('module.meowfficer.score_task.logger'):
            for _ in range(count):
                self.task._score_and_apply_lock(self.scanner,
                                                _capture(rarity='R', names=()) if blue else _capture())
        return refresh

    def test_fixed_record_boundaries_include_exact_last_cat_and_reset_for_new_run(self):
        for count, expected in ((1, []), (11, []), (12, [12]), (13, [12]),
                                (24, [12, 24]), (36, [12, 24, 36])):
            with self.subTest(count=count):
                self.setUp()
                refresh = self.run_cats(count)
                self.assertEqual([number for kind, number in self.events if kind == 'refresh'], expected)
                self.assertEqual(refresh.call_count, len(expected))
                self.assertEqual(len(self.task.lock_actions), count)
                self.assertEqual(len(self.task.results), count)
                self.assertEqual(self.task.scanned_count, count)
                self.assertEqual(self.task._publish_report.call_count, count)
                for ordinal in expected:
                    position = self.events.index(('refresh', ordinal))
                    self.assertEqual(self.events[position - 1], ('advice', ordinal))
                    self.assertEqual(self.task.lock_actions[ordinal - 1]['periodicRefresh']['ordinal'], ordinal)

    def test_blue_cats_count_despite_having_no_scoring_results(self):
        refresh = self.run_cats(24, blue=True)
        self.assertEqual(refresh.call_count, 2)
        self.assertEqual(self.task.scanned_count, 24)
        self.assertEqual(self.task.results, [])
        self.assertEqual(len(self.task.lock_actions), 24)
        self.assertIs(self.task.lock_actions[-1]['periodicRefresh']['before'], False)

    def test_original_is_after_advice_not_before_and_pair_does_not_add_lock_actions(self):
        self.task.lock_actions = [{}] * 11
        self.state = True
        with patch('module.meowfficer.score_lock.set_lock_state', side_effect=self.apply), \
                patch('module.meowfficer.lock_refresh.refresh_lock_state', side_effect=self.refresh):
            action = self.task._score_and_apply_lock(self.scanner,
                _capture(names=('炮击新手·主力', '装填新手·战列')))
        self.assertIs(action['before'], True)
        self.assertIs(action['target'], False)
        self.assertIs(action['after'], False)
        self.assertIs(action['periodicRefresh']['before'], False)
        self.assertIs(action['periodicRefresh']['after'], False)
        self.assertEqual(len(self.task.lock_actions), 12)

    def test_pair_failure_is_published_as_unconfirmed_with_actual_temporary_state(self):
        self.task.lock_actions = [{}] * 11

        def failure(_scanner, _capture, entry):
            entry.update(before=True, after=False, status='unconfirmed', reason='恢复点击未能确认')
            return entry

        with patch('module.meowfficer.score_lock.set_lock_state', side_effect=self.apply), \
                patch('module.meowfficer.lock_refresh.refresh_lock_state', side_effect=failure):
            with self.assertRaises(RequestHumanTakeover):
                self.task._score_and_apply_lock(self.scanner, _capture())
        action = self.task.lock_actions[-1]
        self.assertEqual(action['status'], 'unconfirmed')
        self.assertIs(action['after'], False)
        self.assertIn('恢复点击未能确认', action['reason'])
        self.task._publish_report.assert_called_once_with()
        self.assertEqual(len(self.task.results), 1)

    def test_normal_unconfirmed_lock_never_starts_checkpoint_or_cleans_failed_history(self):
        self.task.lock_actions = [{}] * 11

        def failure(_scanner, _capture, _target, _reason, entry):
            entry.update(before=True, after=None, status='unconfirmed')

        with patch('module.meowfficer.score_lock.set_lock_state', side_effect=failure), \
                patch('module.meowfficer.lock_refresh.refresh_lock_state') as refresh:
            with self.assertRaises(RequestHumanTakeover):
                self.task._score_and_apply_lock(self.scanner, _capture())
        refresh.assert_not_called()
        self.scanner.device.click_record_remove.assert_not_called()
        self.assertNotIn('periodicRefresh', self.task.lock_actions[-1])

    def test_skipped_normal_lock_at_checkpoint_stops_instead_of_ignoring_boundary(self):
        self.task.lock_actions = [{}] * 11
        with patch('module.meowfficer.score_lock.set_lock_state'), \
                patch('module.meowfficer.lock_refresh.refresh_lock_state') as refresh:
            with self.assertRaises(RequestHumanTakeover):
                self.task._score_and_apply_lock(self.scanner, _capture())
        refresh.assert_not_called()
        self.assertEqual(self.task.lock_actions[-1]['periodicRefresh']['status'], 'unconfirmed')
        self.scanner.device.click_record_remove.assert_not_called()


class PeriodicLiveReportTests(unittest.TestCase):
    """只新增周期异常报告用例，不重复继承执行父类所有用例。"""

    setUp = live_report_tests.LiveReportTests.setUp
    _payload = live_report_tests.LiveReportTests._payload
    _scanner = live_report_tests.LiveReportTests._scanner

    def test_checkpoint_device_error_persists_partial_pair_and_keeps_original_error(self):
        self.task.config.MeowfficerScore_LockByAdvice = True
        error = GameStuckError('隔离双切设备异常')

        def normal(_scanner, _capture, target, _reason, entry):
            entry.update(before=target, after=target, status='unchanged')

        def refresh(_scanner, capture, entry):
            entry.update(before=True, after=None, status='unconfirmed', reason='恢复阶段设备异常')
            entry['steps'].append({'name': capture.display_name, 'status': 'changed', 'after': False})
            raise error

        def scan(scanner, *, on_cat, **kwargs):
            scanner.device = self.task.device
            for _ in range(12):
                on_cat(scanner, _capture())

        self._scanner(scan)
        with patch('module.meowfficer.score_lock.set_lock_state', side_effect=normal), \
                patch('module.meowfficer.lock_refresh.refresh_lock_state', side_effect=refresh):
            with self.assertRaises(GameStuckError) as raised:
                self.task._run_scan()
        self.assertIs(raised.exception, error)
        payload = self._payload()
        self.assertEqual(payload['scannedCount'], 12)
        self.assertEqual(payload['count'], 12)
        self.assertEqual(payload['lockActions'][-1]['status'], 'unconfirmed')
        self.assertEqual(len(payload['lockActions'][-1]['periodicRefresh']['steps']), 1)

    def test_advice_disabled_does_not_refresh_even_after_twenty_four_records(self):
        self.task.config.MeowfficerScore_LockByAdvice = False

        def scan(scanner, *, on_result, **kwargs):
            self.assertNotIn('on_cat', kwargs)
            for _ in range(24):
                capture = _capture()
                entry = (capture.display_name, capture.talents, capture.level)
                scanner.scanned.append(entry)
                on_result(scanner, entry)
            return scanner.scanned

        self._scanner(scan)
        with patch('module.meowfficer.lock_refresh.refresh_lock_state') as refresh:
            self.task._run_scan()
        refresh.assert_not_called()
        self.assertEqual(self._payload()['scannedCount'], 24)
        self.task.device.click.assert_not_called()
        self.task.device.swipe.assert_not_called()

    def test_checkpoint_preflight_device_error_is_finalized_in_report_without_pending_state(self):
        self.task.config.MeowfficerScore_LockByAdvice = True
        error = GameStuckError('隔离首次截图错误')

        def normal(_scanner, _capture, target, _reason, entry):
            entry.update(before=target, after=target, status='unchanged')

        def scan(scanner, *, on_cat, **kwargs):
            scanner.device = self.task.device
            for _ in range(12):
                on_cat(scanner, _capture())

        self._scanner(scan)
        with patch('module.meowfficer.score_lock.set_lock_state', side_effect=normal), \
                patch('module.meowfficer.lock_refresh.refresh_lock_state', side_effect=error):
            with self.assertRaises(GameStuckError) as raised:
                self.task._run_scan()
        self.assertIs(raised.exception, error)
        action = self._payload()['lockActions'][-1]
        self.assertEqual(action['status'], 'unconfirmed')
        self.assertEqual(action['periodicRefresh']['status'], 'unconfirmed')
        self.assertEqual(action['periodicRefresh']['steps'], [])
        self.assertIn('未开始切换', action['reason'])


class _HistoryPage(DetailPage):
    """复用真实 Device 点击队列算法，匿名模板和按钮全部在内存中运行。"""

    click_record_add = Device.click_record_add
    click_record_remove = Device.click_record_remove
    click_record_check = Device.click_record_check
    click_record_clear = Device.click_record_clear

    def __init__(self):
        super().__init__()
        self.click_record = deque(['OTHER_KEEP'], maxlen=15)
        self.device.click_record_remove = Mock(side_effect=self.click_record_remove)

    def click(self, button):
        self.click_record_add(button)
        self.click_record_check()
        super().click(button)

    def frame(self):
        image = super().frame()
        # 已读蓝猫也有完整空槽行框，周期预检不把黑画布当作真实静态证据。
        image[154:241, 756:842] = (192, 227, 236)
        image[162:190, 858:1110] = (244, 235, 221)
        return image


class PeriodicClickHistoryTests(unittest.TestCase):
    def test_thirty_six_normal_unlocks_and_three_pairs_keep_click_protection_working(self):
        page = _HistoryPage()
        task = runner()
        task._publish_report = Mock()
        with patch('module.config.server.server', 'cn'), \
                patch('module.meowfficer.score_lock.Timer', side_effect=lambda *a, **kw: _FrameTimer(4)), \
                patch('module.meowfficer.lock_refresh.Timer', side_effect=lambda *a, **kw: _FrameTimer(4)), \
                patch('module.meowfficer.lock_refresh.sleep') as pause, \
                patch('module.meowfficer.score_task.logger'), patch('module.meowfficer.lock_refresh.logger'):
            for _ in range(36):
                page.steps = 0
                page.locked = True
                page.device.image = page.frame()
                action = task._score_and_apply_lock(page, page.snapshot(rarity='R', talents=[]))
                self.assertIs(action['after'], False)
                self.assertEqual(tuple(page.click_record), ('OTHER_KEEP',))
        self.assertEqual(page.device.click.call_count, 42)
        self.assertEqual(pause.call_count, 3)
        self.assertTrue(all(call.args == (0.5,) for call in pause.call_args_list))
        self.assertEqual(len(task.lock_actions), 36)
        self.assertEqual([action['periodicRefresh']['ordinal'] for action in task.lock_actions
                          if 'periodicRefresh' in action], [12, 24, 36])
        self.assertEqual(task.results, [])
        self.assertTrue(all(button == LOCK_BUTTON for (button,), _ in page.device.click.call_args_list))


if __name__ == '__main__':
    unittest.main()
