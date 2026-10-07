"""单次锁切换后的过渡画面持续核验，所有局部模板与操作只发生在内存。"""

import unittest
from unittest.mock import Mock, patch

from module.exception import GameStuckError, GameTooManyClickError, RequestHumanTakeover
from module.meowfficer.score_lock import LOCK_BUTTON, read_lock_state, set_lock_state
from tests.test_meowfficer_score_lock import DetailPage


class _FrameTimer:
    """用有限截图周期模拟原计时器到限，不等待墙钟且不吞设备异常。"""

    def __init__(self, expires_at=12):
        self.expires_at = expires_at
        self.calls = 0
        self.start = Mock(return_value=self)
        self.reset = Mock(side_effect=self._reset)

    def _reset(self):
        self.calls = 0
        return self

    def reached(self):
        self.calls += 1
        return self.calls >= self.expires_at


class _TransitionPage(DetailPage):
    """复用真实局部资源，点击之后按指定帧顺序显示页标题、身份和锁按钮。"""

    def __init__(self, *, target=False, frames=()):
        self.target = target
        self.transitions = list(frames)
        self.transition_index = 0
        self.clicked = False
        self.observed = []
        super().__init__(locked=not target, responds=False)
        self.after_click = lambda: setattr(self, 'clicked', True)

    def screenshot(self):
        if self.clicked:
            frame = self.transitions[min(self.transition_index, len(self.transitions) - 1)]
            self.transition_index += 1
            self.page_visible = frame.get('page', True)
            self.identity_changed = frame.get('other', False)
            self.locked = frame.get('lock', self.target)
        super().screenshot()
        self.observed.append((self.page_visible, self.identity_changed, self.locked))


class LockTransitionTests(unittest.TestCase):
    """切换后等待原身份恢复，目标锁状态必须连续两帧，不重复点击或使用别猫状态。"""

    def setUp(self):
        server = patch('module.config.server.server', 'cn')
        server.start()
        self.addCleanup(server.stop)

    def _run(self, page, *, expires_at=12, snapshot=None, entry=None):
        timer = _FrameTimer(expires_at)
        with patch('module.meowfficer.score_lock.Timer', return_value=timer) as timer_factory:
            action = set_lock_state(page, page.snapshot() if snapshot is None else snapshot,
                                    page.target, '培养建议', entry=entry)
        timer_factory.assert_called_once_with(8, count=12)
        timer.start.assert_called_once_with()
        if page.device.click.called:
            timer.reset.assert_called_once_with()
        else:
            timer.reset.assert_not_called()
        return action, timer

    def test_transient_other_identity_returns_to_original_then_confirms_two_target_frames(self):
        for target in (False, True):
            with self.subTest(target=target):
                page = _TransitionPage(target=target, frames=(
                    {'other': True, 'lock': target}, {'lock': target}, {'lock': target}))
                with patch('module.meowfficer.score_lock.read_lock_state', wraps=read_lock_state) as read:
                    action, _timer = self._run(page)
                self.assertEqual(action['status'], 'changed')
                self.assertIs(action['before'], not target)
                self.assertIs(action['after'], target)
                page.device.click.assert_called_once_with(LOCK_BUTTON)
                self.assertEqual(page.device.screenshot.call_count, 4)
                self.assertEqual(read.call_count, 3)

    def test_several_unknown_page_lock_and_identity_frames_can_recover_without_second_click(self):
        page = _TransitionPage(frames=(
            {'page': False, 'lock': False}, {'lock': None}, {'other': True, 'lock': False},
            {'lock': False}, {'lock': False}))
        with patch('module.meowfficer.score_lock.read_lock_state', wraps=read_lock_state) as read:
            action, timer = self._run(page)
        self.assertEqual(action['status'], 'changed')
        self.assertIs(action['after'], False)
        self.assertEqual(page.device.screenshot.call_count, 6)
        self.assertEqual(read.call_count, 4)
        self.assertLess(timer.calls, timer.expires_at)
        page.device.click.assert_called_once_with(LOCK_BUTTON)

    def test_persistent_other_cat_target_state_is_never_used_as_the_original_result(self):
        page = _TransitionPage(frames=({'other': True, 'lock': False},))
        with patch('module.meowfficer.score_lock.read_lock_state', wraps=read_lock_state) as read:
            action, timer = self._run(page)
        self.assertEqual(action['status'], 'unconfirmed')
        self.assertIs(action['before'], True)
        self.assertIsNone(action['after'])
        self.assertEqual(read.call_count, 1)
        self.assertEqual(timer.calls, 12)
        self.assertEqual(page.device.screenshot.call_count, 13)
        page.device.click.assert_called_once_with(LOCK_BUTTON)

    def test_persistent_unknown_page_or_lock_stops_at_existing_limit_after_one_click(self):
        for frame in ({'page': False}, {'lock': None}):
            with self.subTest(frame=frame):
                page = _TransitionPage(frames=(frame,))
                action, timer = self._run(page)
                self.assertEqual(action['status'], 'unconfirmed')
                self.assertIsNone(action['after'])
                self.assertEqual(timer.calls, 12)
                self.assertEqual(page.device.screenshot.call_count, 13)
                page.device.click.assert_called_once_with(LOCK_BUTTON)

    def test_preclick_page_or_identity_mismatch_skips_immediately_and_never_clicks(self):
        for change in ('page', 'identity'):
            with self.subTest(change=change):
                page = _TransitionPage(frames=({'lock': False},))
                snapshot = page.snapshot()
                if change == 'page':
                    page.page_visible = False
                else:
                    page.identity_changed = True
                action, timer = self._run(page, snapshot=snapshot)
                self.assertEqual(action['status'], 'skipped')
                self.assertIsNone(action['before'])
                self.assertIsNone(action['after'])
                self.assertEqual(timer.calls, 0)
                self.assertEqual(page.device.screenshot.call_count, 1)
                page.device.click.assert_not_called()

    def test_first_target_frame_followed_by_mismatch_resets_confirmation_streak(self):
        for interruption in ({'page': False}, {'other': True}, {'lock': None}, {'lock': True}):
            with self.subTest(interruption=interruption):
                page = _TransitionPage(frames=(
                    {'lock': False}, interruption, {'lock': False}, {'lock': False}))
                action, _timer = self._run(page)
                self.assertEqual(action['status'], 'changed')
                self.assertIs(action['after'], False)
                self.assertEqual(page.device.screenshot.call_count, 5)
                page.device.click.assert_called_once_with(LOCK_BUTTON)

    def test_one_target_frame_before_persistent_other_identity_is_not_success(self):
        page = _TransitionPage(frames=({'lock': False}, {'other': True, 'lock': False}))
        action, timer = self._run(page)
        self.assertEqual(action['status'], 'unconfirmed')
        self.assertIsNone(action['after'])
        self.assertEqual(timer.calls, 12)
        page.device.click.assert_called_once_with(LOCK_BUTTON)

    def test_opposite_lock_state_never_triggers_second_toggle_even_when_stable(self):
        page = _TransitionPage(frames=({'lock': True},))
        action, timer = self._run(page)
        self.assertEqual(action['status'], 'unconfirmed')
        self.assertIs(action['before'], True)
        self.assertIs(action['after'], True)
        self.assertEqual(timer.calls, 12)
        page.device.click.assert_called_once_with(LOCK_BUTTON)

    def test_already_target_before_click_remains_unchanged_without_waiting_two_frames(self):
        page = _TransitionPage(frames=({'lock': False},))
        page.locked = False
        page.device.image = page.frame()
        action, timer = self._run(page)
        self.assertEqual(action['status'], 'unchanged')
        self.assertIs(action['before'], False)
        self.assertIs(action['after'], False)
        self.assertEqual(page.device.screenshot.call_count, 1)
        self.assertEqual(timer.calls, 0)
        page.device.click.assert_not_called()

    def test_flow_or_device_errors_during_transition_propagate_with_pending_action(self):
        for error_type in (GameStuckError, GameTooManyClickError, RequestHumanTakeover, ConnectionError):
            with self.subTest(error_type=error_type):
                page = _TransitionPage(frames=({'lock': False},))
                original_screenshot = page.device.screenshot.side_effect
                error = error_type('锁切换后的离线异常')

                def failed_screenshot():
                    if page.clicked:
                        raise error
                    original_screenshot()

                page.device.screenshot.side_effect = failed_screenshot
                entry = dict(name='奥古喵', before=None, after=None, target=False,
                             status='skipped', reason='培养建议')
                with self.assertRaises(error_type) as caught:
                    self._run(page, entry=entry)
                self.assertIs(caught.exception, error)
                self.assertEqual(entry['status'], 'unconfirmed')
                self.assertIs(entry['before'], True)
                self.assertIsNone(entry['after'])
                page.device.click.assert_called_once_with(LOCK_BUTTON)


if __name__ == '__main__':
    unittest.main()
