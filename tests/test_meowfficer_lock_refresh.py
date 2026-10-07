"""每十二只猫的双切刷新：真实局部模板、有限模拟帧，不连接游戏。"""

import unittest
from contextlib import ExitStack
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.exception import GameStuckError, GameTooManyClickError, RequestHumanTakeover
from module.meowfficer.lock_refresh import new_refresh_action, refresh_lock_state
from module.meowfficer.score_lock import LOCK_BUTTON, read_lock_state, set_lock_state
from tests.test_meowfficer_score_lock import DetailPage


MODULE = 'module.meowfficer.lock_refresh'


class _FrameTimer:
    """只在等待分支累计有限观察次数；真实操作阶段仍须正向状态确认。"""

    def __init__(self, expires_at=12):
        self.calls = 0
        self.expires_at = expires_at
        self.start = Mock(return_value=self)
        self.reset = Mock(side_effect=self._reset)

    def _reset(self):
        self.calls = 0
        return self

    def reached(self):
        self.calls += 1
        return self.calls >= self.expires_at


class _RefreshPage(DetailPage):
    """按点击次数分阶段提供状态，所有模拟按钮仅改变内存中的锁状态。"""

    def __init__(self, *, locked=True, responds=(True, True), frames=None):
        self.original = locked
        self.responses = tuple(responds)
        self.events = frames or {}
        self.stage = 0
        self.event_indexes = {}
        self.observations = []
        self.click_states = []
        self.scanned = ['已有评分']
        self.results = ['已有报告']
        self.on_capture = Mock()
        self.on_identity = Mock()
        self.click_error = None
        super().__init__(locked=locked, responds=False)
        self.device.swipe = Mock()
        self.device.click_record_remove = Mock()
        self.device.stuck_record_clear = Mock()

    def screenshot(self):
        self.steps += 1
        if self.steps > 80:
            raise AssertionError('双切刷新没有有限结束')
        frames = self.events.get(self.stage)
        if frames:
            index = self.event_indexes.get(self.stage, 0)
            frame = frames[min(index, len(frames) - 1)]
            self.event_indexes[self.stage] = index + 1
            if 'raise' in frame:
                raise frame['raise']
            self.page_visible = frame.get('page', True)
            self.identity_changed = frame.get('other', False)
            if 'lock' in frame:
                self.locked = frame['lock']
        self.device.image = self.frame()
        self.observations.append((self.stage, self.page_visible, self.identity_changed, self.locked))

    def click(self, button):
        if button is not LOCK_BUTTON:
            raise AssertionError('刷新夹具只允许局部锁按钮')
        self.click_states.append(self.locked)
        if self.click_error is not None:
            raise self.click_error
        if self.stage >= 2:
            raise AssertionError('禁止第三次切锁')
        responds = self.responses[self.stage]
        self.stage += 1
        if responds:
            self.locked = not self.locked


class LockRefreshTests(unittest.TestCase):
    def setUp(self):
        server = patch('module.config.server.server', 'cn')
        server.start()
        self.addCleanup(server.stop)

    def _run(self, page, *, snapshot=None, entry=None, expires_at=12, set_side_effect=None):
        snapshot = page.snapshot(talents_complete=True) if snapshot is None else snapshot
        if entry is None:
            entry = new_refresh_action(snapshot)
        timers = []

        def timer_factory(*args, **kwargs):
            self.assertEqual(args, (8,))
            self.assertEqual(kwargs, {'count': 12})
            timer = _FrameTimer(expires_at)
            timers.append(timer)
            return timer

        with ExitStack() as stack:
            stack.enter_context(patch(f'{MODULE}.Timer', side_effect=timer_factory))
            stack.enter_context(patch('module.meowfficer.score_lock.Timer', side_effect=timer_factory))
            setter = stack.enter_context(patch(f'{MODULE}.set_lock_state',
                                              wraps=None if set_side_effect else set_lock_state,
                                              side_effect=set_side_effect))
            action = refresh_lock_state(page, snapshot, entry=entry)
        self.assertIs(action, entry)
        return action, setter, timers

    def assert_no_extra_game_work(self, page):
        page.device.swipe.assert_not_called()
        page.on_capture.assert_not_called()
        page.on_identity.assert_not_called()
        self.assertEqual(page.scanned, ['已有评分'])
        self.assertEqual(page.results, ['已有报告'])
        self.assertLessEqual(page.device.click.call_count, 2)
        for call in page.device.click.call_args_list:
            self.assertIs(call.args[0], LOCK_BUTTON)

    def test_new_action_is_independent_pending_shared_report_record(self):
        capture = _RefreshPage().snapshot()
        first = new_refresh_action(capture)
        second = new_refresh_action(capture)
        self.assertEqual(first['name'], capture.display_name)
        self.assertIsNone(first['before'])
        self.assertIsNone(first['after'])
        self.assertEqual(first['status'], 'pending')
        self.assertIsInstance(first['reason'], str)
        self.assertEqual(first['steps'], [])
        self.assertIsNot(first['steps'], second['steps'])

    def test_both_original_lock_states_restore_after_exactly_two_serial_confirmed_clicks(self):
        for original in (False, True):
            with self.subTest(original=original):
                page = _RefreshPage(locked=original)
                action, setter, timers = self._run(page)
                self.assertEqual(action['status'], 'verified')
                self.assertIs(action['before'], original)
                self.assertIs(action['after'], original)
                self.assertIs(page.locked, original)
                self.assertEqual(page.click_states, [original, not original])
                self.assertEqual(page.device.click.call_count, 2)
                self.assertEqual(setter.call_count, 2)
                self.assertEqual(len(action['steps']), 2)
                self.assertEqual(len(timers), 4)
                self.assertEqual(sorted(timer.reset.call_count for timer in timers), [0, 0, 1, 1])
                for timer in timers:
                    timer.start.assert_called_once_with()
                for index, target in enumerate((not original, original)):
                    call = setter.call_args_list[index]
                    self.assertIs(call.args[2], target)
                    self.assertIs(call.kwargs['entry'], action['steps'][index])
                    step = action['steps'][index]
                    self.assertEqual(step['status'], 'changed')
                    self.assertIs(step['before'], not target)
                    self.assertIs(step['after'], target)
                self.assertGreaterEqual(sum(item[0] == 2 for item in page.observations), 4)
                page.device.click_record_remove.assert_called_once_with(LOCK_BUTTON)
                self.assert_no_extra_game_work(page)

    def test_first_leg_without_response_never_starts_second_leg(self):
        for original in (False, True):
            with self.subTest(original=original):
                page = _RefreshPage(locked=original, responds=(False, True))
                action, setter, _ = self._run(page)
                self.assertNotEqual(action['status'], 'verified')
                self.assertEqual(len(action['steps']), 1)
                self.assertEqual(setter.call_count, 1)
                self.assertEqual(page.device.click.call_count, 1)
                self.assertIs(page.locked, original)
                self.assertEqual(action['steps'][0]['status'], 'unconfirmed')
                page.device.click_record_remove.assert_not_called()
                self.assert_no_extra_game_work(page)

    def test_unknown_page_lock_or_other_identity_after_first_click_forbids_second_click(self):
        for frame in ({'page': False}, {'lock': None}, {'other': True, 'lock': False}):
            with self.subTest(frame=frame):
                page = _RefreshPage(frames={1: [frame]})
                action, setter, _ = self._run(page)
                self.assertNotEqual(action['status'], 'verified')
                self.assertEqual(page.device.click.call_count, 1)
                self.assertEqual(setter.call_count, 1)
                self.assertEqual(len(action['steps']), 1)
                page.device.click_record_remove.assert_not_called()
                self.assert_no_extra_game_work(page)

    def test_one_target_frame_then_other_cat_is_not_a_completed_first_leg(self):
        page = _RefreshPage(frames={1: [{'lock': False}, {'other': True, 'lock': False}]})
        action, setter, _ = self._run(page)
        self.assertNotEqual(action['status'], 'verified')
        self.assertEqual(setter.call_count, 1)
        self.assertEqual(page.device.click.call_count, 1)
        self.assertIsNone(action['steps'][0]['after'])

    def test_first_leg_completed_but_fresh_state_or_identity_loss_forbids_second_leg(self):
        for frame in ({'page': False}, {'other': True}, {'lock': None}, {'lock': True}):
            with self.subTest(frame=frame):
                page = _RefreshPage(frames={1: [{'lock': False}, {'lock': False}, frame]})
                action, setter, _ = self._run(page)
                self.assertNotEqual(action['status'], 'verified')
                self.assertEqual(action['steps'][0]['status'], 'changed')
                self.assertEqual(setter.call_count, 1)
                self.assertEqual(page.device.click.call_count, 1)
                page.device.click_record_remove.assert_not_called()

    def test_first_leg_transient_unknown_can_finish_before_second_confirmed_click(self):
        page = _RefreshPage(frames={1: [{'page': False}, {'other': True, 'lock': False},
                                      {'lock': None}, {'lock': False}, {'lock': False}]})
        action, setter, _ = self._run(page)
        self.assertEqual(action['status'], 'verified')
        self.assertEqual(setter.call_count, 2)
        self.assertEqual(page.click_states, [True, False])
        self.assert_no_extra_game_work(page)

    def test_second_leg_no_response_never_retries_or_makes_third_click(self):
        for original in (False, True):
            with self.subTest(original=original):
                page = _RefreshPage(locked=original, responds=(True, False))
                action, setter, _ = self._run(page)
                self.assertNotEqual(action['status'], 'verified')
                self.assertEqual(setter.call_count, 2)
                self.assertEqual(len(action['steps']), 2)
                self.assertEqual(action['steps'][0]['status'], 'changed')
                self.assertEqual(action['steps'][1]['status'], 'unconfirmed')
                self.assertEqual(page.device.click.call_count, 2)
                page.device.click_record_remove.assert_not_called()
                self.assertIs(page.locked, not original)
                self.assert_no_extra_game_work(page)

    def test_second_leg_unknown_page_lock_or_other_cat_stops_with_at_most_two_clicks(self):
        for frame in ({'page': False}, {'lock': None}, {'other': True, 'lock': True}):
            with self.subTest(frame=frame):
                page = _RefreshPage(frames={2: [frame]})
                action, setter, _ = self._run(page)
                self.assertNotEqual(action['status'], 'verified')
                self.assertEqual(setter.call_count, 2)
                self.assertEqual(page.device.click.call_count, 2)
                self.assert_no_extra_game_work(page)

    def test_final_unknown_lock_or_opposite_state_resets_streak_before_two_original_frames(self):
        for frame in ({'lock': None}, {'lock': False}):
            with self.subTest(frame=frame):
                page = _RefreshPage(frames={2: [{'lock': True}, {'lock': True},
                                              {'lock': True}, frame,
                                              {'lock': True}, {'lock': True}]})
                action, _setter, _ = self._run(page)
                self.assertEqual(action['status'], 'verified')
                self.assertEqual(sum(item[0] == 2 for item in page.observations), 6)
                self.assertEqual(page.device.click.call_count, 2)
                self.assert_no_extra_game_work(page)

    def test_final_page_or_identity_loss_stops_immediately_without_reading_later_good_frames(self):
        for frame in ({'page': False}, {'other': True}):
            with self.subTest(frame=frame):
                page = _RefreshPage(frames={2: [{'lock': True}, {'lock': True},
                                              {'lock': True}, frame,
                                              {'lock': True}, {'lock': True}]})
                action, _, _ = self._run(page)
                self.assertNotEqual(action['status'], 'verified')
                self.assertEqual(sum(item[0] == 2 for item in page.observations), 4)
                self.assertEqual(page.device.click.call_count, 2)
                page.device.click_record_remove.assert_not_called()

    def test_final_confirmation_persistent_unknown_cannot_claim_verified(self):
        for frame in ({'page': False}, {'lock': None}, {'other': True}, {'lock': False}):
            with self.subTest(frame=frame):
                page = _RefreshPage(frames={2: [{'lock': True}, {'lock': True}, frame]})
                action, setter, _ = self._run(page)
                self.assertNotEqual(action['status'], 'verified')
                self.assertEqual(setter.call_count, 2)
                self.assertEqual([step['status'] for step in action['steps']], ['changed', 'changed'])
                self.assertEqual(page.device.click.call_count, 2)
                self.assert_no_extra_game_work(page)

    def test_other_cat_lock_is_never_read_or_used_for_confirmation(self):
        page = _RefreshPage(frames={1: [{'other': True, 'lock': False}]})
        reads = []

        def read_original_only(image):
            self.assertFalse(page.identity_changed)
            reads.append(page.stage)
            return read_lock_state(image)

        with patch(f'{MODULE}.read_lock_state', side_effect=read_original_only), \
                patch('module.meowfficer.score_lock.read_lock_state', side_effect=read_original_only):
            action, _, _ = self._run(page)
        self.assertNotEqual(action['status'], 'verified')
        self.assertEqual(reads, [0, 0])
        self.assertEqual(page.device.click.call_count, 1)

    def test_preflight_unknown_page_lock_or_identity_never_clicks(self):
        for frame in ({'page': False}, {'lock': None}, {'other': True}):
            with self.subTest(frame=frame):
                page = _RefreshPage(frames={0: [frame]})
                action, setter, _ = self._run(page)
                self.assertNotEqual(action['status'], 'verified')
                self.assertEqual(action['steps'], [])
                setter.assert_not_called()
                page.device.click.assert_not_called()
                self.assert_no_extra_game_work(page)

    def test_preflight_transient_unknown_state_uses_fresh_confirmed_original_state(self):
        page = _RefreshPage(locked=False, frames={0: [{'lock': None}, {'lock': False}]})
        action, _, _ = self._run(page)
        self.assertEqual(action['status'], 'verified')
        self.assertIs(action['before'], False)
        self.assertIs(action['after'], False)
        self.assertEqual(page.click_states, [False, True])

    def test_missing_or_unconfirmed_original_identity_snapshot_has_zero_clicks(self):
        for overrides in ({'identity_image': None}, {'identity_confirmed': False}):
            with self.subTest(overrides=overrides):
                page = _RefreshPage()
                snapshot = replace(page.snapshot(), **overrides)
                action, setter, _ = self._run(page, snapshot=snapshot)
                self.assertNotEqual(action['status'], 'verified')
                self.assertEqual(action['steps'], [])
                setter.assert_not_called()
                page.device.click.assert_not_called()

    def test_first_leg_inconsistent_shared_result_is_rejected_before_second_leg(self):
        for changed in ({'status': 'unchanged'}, {'before': False}, {'after': True}):
            with self.subTest(changed=changed):
                page = _RefreshPage()

                def inconsistent(scanner, capture, target, reason, entry=None):
                    entry.update(status='changed', before=True, after=False)
                    entry.update(changed)
                    return entry

                action, setter, _ = self._run(page, set_side_effect=inconsistent)
                self.assertNotEqual(action['status'], 'verified')
                self.assertEqual(setter.call_count, 1)
                self.assertEqual(len(action['steps']), 1)
                page.device.click.assert_not_called()

    def test_second_leg_inconsistent_result_never_marks_verified_or_starts_third_leg(self):
        for changed in ({'status': 'unchanged'}, {'before': True}, {'after': False}):
            with self.subTest(changed=changed):
                page = _RefreshPage()
                calls = 0

                def inconsistent(scanner, capture, target, reason, entry=None):
                    nonlocal calls
                    calls += 1
                    entry.update(status='changed', before=not target, after=target)
                    page.locked = target
                    page.device.image = page.frame()
                    if calls == 2:
                        entry.update(changed)
                    return entry

                action, setter, _ = self._run(page, set_side_effect=inconsistent)
                self.assertNotEqual(action['status'], 'verified')
                self.assertEqual(setter.call_count, 2)
                self.assertEqual(len(action['steps']), 2)
                page.device.click.assert_not_called()

    def test_existing_recovery_context_must_confirm_full_target_before_second_leg(self):
        page = _RefreshPage()
        context = SimpleNamespace(target_confirmed=Mock(side_effect=[True, False]), restore=Mock())
        page._meowfficer_lock_recovery = context
        action, setter, _ = self._run(page)
        self.assertNotEqual(action['status'], 'verified')
        self.assertEqual(setter.call_count, 1)
        self.assertEqual(page.device.click.call_count, 1)
        self.assertEqual(context.target_confirmed.call_count, 2)
        context.restore.assert_not_called()
        page.device.click_record_remove.assert_not_called()

    def test_incomplete_full_target_at_preflight_forbids_even_first_temporary_toggle(self):
        page = _RefreshPage()
        context = SimpleNamespace(target_confirmed=Mock(return_value=False), restore=Mock())
        page._meowfficer_lock_recovery = context
        action, setter, _ = self._run(page)
        self.assertNotEqual(action['status'], 'verified')
        self.assertEqual(action['steps'], [])
        setter.assert_not_called()
        page.device.click.assert_not_called()
        context.target_confirmed.assert_called_once()
        context.restore.assert_not_called()
        page.device.click_record_remove.assert_not_called()

    def test_context_full_confirmation_is_required_before_second_leg_and_on_both_final_frames(self):
        page = _RefreshPage()
        captures = []

        def full_target(capture):
            captures.append(capture)
            page.device.screenshot()
            return True

        context = SimpleNamespace(target_confirmed=Mock(side_effect=full_target), restore=Mock())
        page._meowfficer_lock_recovery = context
        snapshot = page.snapshot(talents_complete=True)
        action, setter, _ = self._run(page, snapshot=snapshot)
        self.assertEqual(action['status'], 'verified')
        self.assertEqual(setter.call_count, 2)
        self.assertEqual(len(captures), 4)
        self.assertTrue(all(capture is snapshot for capture in captures))
        self.assertEqual(page.device.click.call_count, 2)
        context.restore.assert_not_called()
        page.device.click_record_remove.assert_called_once_with(LOCK_BUTTON)

    def test_failed_second_full_target_confirmation_cannot_mark_final_pair_verified(self):
        page = _RefreshPage()
        context = SimpleNamespace(target_confirmed=Mock(side_effect=[True, True, True, False]),
                                  restore=Mock())
        page._meowfficer_lock_recovery = context
        action, setter, _ = self._run(page)
        self.assertNotEqual(action['status'], 'verified')
        self.assertEqual(setter.call_count, 2)
        self.assertEqual(context.target_confirmed.call_count, 4)
        self.assertEqual(page.device.click.call_count, 2)
        page.device.click_record_remove.assert_not_called()
        self.assert_no_extra_game_work(page)

    def test_device_exceptions_preserve_unfinished_action_and_live_step_reference(self):
        for stage, frames, count in (
                ('preflight', {0: [{'raise': GameStuckError('匿名预检截图异常')}]}, 0),
                ('first', {1: [{'raise': RequestHumanTakeover('匿名第一腿异常')}]}, 1),
                ('second', {2: [{'raise': GameTooManyClickError('匿名第二腿异常')}]}, 2),
                ('final', {2: [{'lock': True}, {'lock': True},
                               {'raise': GameStuckError('匿名最终截图异常')}]}, 2)):
            with self.subTest(stage=stage):
                page = _RefreshPage(frames=frames)
                snapshot = page.snapshot()
                entry = new_refresh_action(snapshot)
                with self.assertRaises((GameStuckError, RequestHumanTakeover, GameTooManyClickError)):
                    self._run(page, snapshot=snapshot, entry=entry)
                self.assertEqual(entry['status'], 'pending' if stage == 'preflight' else 'unconfirmed')
                self.assertEqual(len(entry['steps']), count)
                self.assertEqual(page.device.click.call_count, count)
                if stage == 'final':
                    self.assertEqual([step['status'] for step in entry['steps']],
                                     ['changed', 'changed'])
                elif count:
                    self.assertEqual(entry['steps'][-1]['status'], 'unconfirmed')
                self.assert_no_extra_game_work(page)

    def test_click_exception_is_propagated_without_second_attempt(self):
        page = _RefreshPage()
        page.click_error = GameTooManyClickError('匿名点击保护')
        snapshot = page.snapshot()
        entry = new_refresh_action(snapshot)
        with self.assertRaises(GameTooManyClickError):
            self._run(page, snapshot=snapshot, entry=entry)
        self.assertEqual(entry['status'], 'unconfirmed')
        self.assertEqual(len(entry['steps']), 1)
        self.assertEqual(entry['steps'][0]['status'], 'unconfirmed')
        page.device.click.assert_called_once_with(LOCK_BUTTON)


if __name__ == '__main__':
    unittest.main()
