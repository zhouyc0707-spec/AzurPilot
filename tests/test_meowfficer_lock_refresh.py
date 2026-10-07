"""周期快速双切：两次点击间只等待半秒，之后核验原猫和原锁态，不连接游戏。"""

import unittest
from contextlib import ExitStack
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.exception import GameStuckError, GameTooManyClickError, RequestHumanTakeover
from module.meowfficer.lock_refresh import new_refresh_action, refresh_lock_state
from module.meowfficer.scan_roster import STATIC_ATTRIBUTE_AREAS
from module.meowfficer.scan_utils import _crop, _mean_diff
from module.meowfficer.score_lock import LOCK_BUTTON, read_lock_state
from tests.test_meowfficer_scan_coverage import _animated_talent_frame
from tests.test_meowfficer_score_lock import DetailPage


MODULE = 'module.meowfficer.lock_refresh'


class _FrameTimer:
    """用有限截图次数模拟上限；不等待墙钟，不允许未确认分支续期。"""

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
    """复用真实局部模板，点击、等待和截图只记录内存事件。"""

    def __init__(self, *, locked=True, responds=(True, True), frames=None):
        self.original = locked
        self.responses = tuple(responds)
        self.frames = frames or {}
        self.stage = 0
        self.frame_indexes = {}
        self.events = []
        self.scanned = ['已有评分']
        self.results = ['已有报告']
        self.on_capture = Mock()
        self.on_identity = Mock()
        self.click_error_at = None
        self.click_error = GameTooManyClickError('匿名点击保护')
        self.wait_error = None
        self.row_visible = True
        super().__init__(locked=locked, responds=False)
        self.pause = Mock(side_effect=self.wait)
        self.device.swipe = Mock()
        self.device.click_record_remove = Mock(side_effect=self.clear_history)
        self.device.stuck_record_clear = Mock()

    def frame(self):
        image = super().frame()
        if self.row_visible:
            panel = _animated_talent_frame()
            image[152:588, 744:1244] = panel[152:588, 744:1244]
        for index, (x0, y0, x1, y1) in enumerate(STATIC_ATTRIBUTE_AREAS):
            image[y0:y1, x0:x1] = 11 + index
        return image

    def screenshot(self):
        self.events.append(('screenshot', self.stage))
        self.steps += 1
        if self.steps > 60:
            raise AssertionError('周期双切确认没有有限结束')
        frames = self.frames.get(self.stage)
        if frames:
            index = self.frame_indexes.get(self.stage, 0)
            frame = frames[min(index, len(frames) - 1)]
            self.frame_indexes[self.stage] = index + 1
            if 'raise' in frame:
                raise frame['raise']
            self.page_visible = frame.get('page', True)
            self.identity_changed = frame.get('other', False)
            if 'lock' in frame:
                self.locked = frame['lock']
        self.device.image = self.frame()

    def click(self, button):
        if button is not LOCK_BUTTON:
            raise AssertionError('夹具只允许锁按钮')
        self.events.append(('click', self.locked))
        if self.click_error_at == self.device.click.call_count:
            raise self.click_error
        if self.stage >= 2:
            raise AssertionError('禁止第三次切锁')
        responds = self.responses[self.stage]
        self.stage += 1
        if responds:
            self.locked = not self.locked

    def wait(self, seconds):
        self.events.append(('sleep', seconds))
        if self.wait_error is not None:
            raise self.wait_error

    def clear_history(self, button):
        self.events.append(('clear', button))


class _VisualRefreshPage(_RefreshPage):
    """匿名真实行框和静态标题；仅正文与图标具有独立动画。"""

    def __init__(self, **kwargs):
        self.changed_title = False
        self.changed_attributes = False
        self.body_animated = False
        self.small_title_change = False
        self.small_attribute_change = False
        self.row_offset = 0
        super().__init__(**kwargs)

    def frame(self):
        image = super().frame()
        panel = _animated_talent_frame(offset=self.row_offset, animated=self.body_animated,
                                       changed_title_indices=(0,) if self.changed_title else ())
        image[152:588, 744:1244] = panel[152:588, 744:1244]
        for index, (x0, y0, x1, y1) in enumerate(STATIC_ATTRIBUTE_AREAS):
            image[y0:y1, x0:x1] = 11 + index
        if self.changed_attributes:
            x0, y0, x1, y1 = STATIC_ATTRIBUTE_AREAS[0]
            image[y0:y1, x0:x1] = 60
        if self.small_title_change:
            # 只更改一个字宽的匿名笔画，整个标题条的平均差仍小于旧阈值。
            image[163:182, 870:884] = image[163:182, 870:884][:, ::-1].copy()
        if self.small_attribute_change:
            # 只改变数值区域里的 25 个笔画像素，整个小 ROI 的均值差仍低于 3。
            image[647:652, 230:235] = 123
        return image

    def screenshot(self):
        frames = self.frames.get(self.stage)
        if frames:
            index = self.frame_indexes.get(self.stage, 0)
            frame = frames[min(index, len(frames) - 1)]
            self.changed_title = frame.get('changed_title', False)
            self.changed_attributes = frame.get('changed_attributes', False)
            self.body_animated = frame.get('animated', False)
            self.small_title_change = frame.get('small_title_change', False)
            self.small_attribute_change = frame.get('small_attribute_change', False)
            self.row_offset = frame.get('row_offset', 0)
        super().screenshot()


class LockRefreshTests(unittest.TestCase):
    def setUp(self):
        server = patch('module.config.server.server', 'cn')
        server.start()
        self.addCleanup(server.stop)

    def _run(self, page, *, snapshot=None, entry=None, expires_at=12):
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

        forbidden = '已读完天赋，周期双切不得重新读取或滑动'
        with ExitStack() as stack:
            stack.enter_context(patch(f'{MODULE}.Timer', side_effect=timer_factory))
            stack.enter_context(patch(f'{MODULE}.sleep', create=True, new=page.pause))
            for name in ('set_lock_state', 'capture_current_cat', 'swipe_next_cat'):
                stack.enter_context(patch(f'{MODULE}.{name}', create=True,
                                          side_effect=AssertionError(forbidden)))
            stack.enter_context(patch('module.meowfficer.score_lock.set_lock_state',
                                      side_effect=AssertionError(forbidden)))
            stack.enter_context(patch('module.meowfficer.scan_capture.capture_current_cat',
                                      side_effect=AssertionError(forbidden)))
            action = refresh_lock_state(page, snapshot, entry=entry)
        self.assertIs(action, entry)
        for timer in timers:
            timer.start.assert_called_once_with()
            timer.reset.assert_not_called()
        return action, timers

    def assert_no_extra_work(self, page):
        page.device.swipe.assert_not_called()
        page.device.stuck_record_clear.assert_not_called()
        page.on_capture.assert_not_called()
        page.on_identity.assert_not_called()
        self.assertEqual(page.scanned, ['已有评分'])
        self.assertEqual(page.results, ['已有报告'])
        self.assertLessEqual(page.device.click.call_count, 2)
        self.assertNotIn(('screenshot', 1), page.events)
        for call in page.device.click.call_args_list:
            self.assertIs(call.args[0], LOCK_BUTTON)

    def assert_sent_steps(self, action):
        self.assertEqual(len(action['steps']), 2)
        self.assertEqual([step['status'] for step in action['steps']], ['sent', 'sent'])
        self.assertEqual([step['after'] for step in action['steps']], [None, None])

    def test_new_action_is_independent_pending_shared_report(self):
        capture = _RefreshPage().snapshot()
        first, second = new_refresh_action(capture), new_refresh_action(capture)
        self.assertEqual(first['name'], capture.display_name)
        self.assertIsNone(first['before'])
        self.assertIsNone(first['after'])
        self.assertEqual(first['status'], 'pending')
        self.assertIsInstance(first['reason'], str)
        self.assertEqual(first['steps'], [])
        self.assertIsNot(first['steps'], second['steps'])

    def test_both_original_states_use_exact_click_sleep_half_second_click_then_two_final_frames(self):
        for original in (False, True):
            with self.subTest(original=original):
                page = _RefreshPage(locked=original)
                action, timers = self._run(page)
                self.assertEqual(action['status'], 'verified')
                self.assertIs(action['before'], original)
                self.assertIs(action['after'], original)
                self.assertIs(page.locked, original)
                self.assertEqual(page.events, [('screenshot', 0), ('click', original),
                                               ('sleep', 0.5), ('click', not original),
                                               ('screenshot', 2), ('screenshot', 2),
                                               ('clear', LOCK_BUTTON)])
                self.assertEqual(page.device.click.call_count, 2)
                page.pause.assert_called_once_with(0.5)
                page.device.click_record_remove.assert_called_once_with(LOCK_BUTTON)
                self.assertEqual(len(timers), 2)
                self.assert_sent_steps(action)
                self.assert_no_extra_work(page)

    def test_click_steps_report_sent_without_claiming_unobserved_intermediate_change(self):
        page = _RefreshPage(responds=(False, False))
        action, _ = self._run(page)
        self.assertEqual(action['status'], 'verified')
        self.assertIs(action['before'], True)
        self.assertIs(action['after'], True)
        self.assert_sent_steps(action)
        self.assertEqual(page.device.click.call_count, 2)
        self.assert_no_extra_work(page)

    def test_existing_full_capture_and_recovery_context_are_not_read_again(self):
        page = _RefreshPage()
        context = SimpleNamespace(target_confirmed=Mock(side_effect=AssertionError('不得重读天赋')),
                                  restore=Mock(side_effect=AssertionError('不得恢复跳猫')))
        page._meowfficer_lock_recovery = context
        action, _ = self._run(page)
        self.assertEqual(action['status'], 'verified')
        context.target_confirmed.assert_not_called()
        context.restore.assert_not_called()
        self.assert_no_extra_work(page)

    def test_lock_status_is_read_only_before_and_after_pair_never_between_clicks(self):
        page = _RefreshPage()
        stages = []

        def record_read(image):
            stages.append(page.stage)
            return read_lock_state(image)

        with patch(f'{MODULE}.read_lock_state', side_effect=record_read):
            action, _ = self._run(page)
        self.assertEqual(action['status'], 'verified')
        self.assertEqual(stages, [0, 2, 2])

    def test_unknown_preflight_page_identity_or_lock_never_starts_pair(self):
        for frame in ({'page': False}, {'other': True}, {'lock': None}):
            with self.subTest(frame=frame):
                page = _RefreshPage(frames={0: [frame]})
                action, _ = self._run(page)
                self.assertNotEqual(action['status'], 'verified')
                self.assertEqual(action['steps'], [])
                page.device.click.assert_not_called()
                page.pause.assert_not_called()
                page.device.click_record_remove.assert_not_called()

    def test_preflight_unknown_lock_can_wait_for_confirmed_current_state(self):
        page = _RefreshPage(locked=False, frames={0: [{'lock': None}, {'lock': False}]})
        action, _ = self._run(page)
        self.assertEqual(action['status'], 'verified')
        self.assertIs(action['before'], False)
        self.assertIs(action['after'], False)
        self.assertEqual(page.events[2:5], [('click', False), ('sleep', 0.5), ('click', True)])

    def test_missing_or_unconfirmed_identity_snapshot_has_zero_clicks(self):
        for overrides in ({'identity_image': None}, {'identity_confirmed': False}):
            with self.subTest(overrides=overrides):
                page = _RefreshPage()
                snapshot = replace(page.snapshot(), **overrides)
                action, _ = self._run(page, snapshot=snapshot)
                self.assertNotEqual(action['status'], 'verified')
                self.assertEqual(action['steps'], [])
                page.device.click.assert_not_called()
                page.pause.assert_not_called()

    def test_missing_complete_visible_row_is_not_positive_preflight_evidence(self):
        page = _RefreshPage()
        page.row_visible = False
        action, _ = self._run(page)
        self.assertNotEqual(action['status'], 'verified')
        self.assertEqual(action['steps'], [])
        page.device.click.assert_not_called()
        page.pause.assert_not_called()
        page.device.click_record_remove.assert_not_called()

    def test_final_unknown_or_opposite_lock_resets_streak_before_two_new_original_frames(self):
        for frame in ({'lock': None}, {'lock': False}):
            with self.subTest(frame=frame):
                page = _RefreshPage(frames={2: [{'lock': True}, frame,
                                              {'lock': True}, {'lock': True}]})
                action, _ = self._run(page)
                self.assertEqual(action['status'], 'verified')
                self.assertEqual(page.device.screenshot.call_count, 5)
                self.assertEqual(page.device.click.call_count, 2)
                page.pause.assert_called_once_with(0.5)
                self.assert_sent_steps(action)
                self.assert_no_extra_work(page)

    def test_transient_final_page_or_identity_loss_resets_streak_then_needs_two_new_original_frames(self):
        for frame in ({'page': False}, {'other': True}):
            with self.subTest(frame=frame):
                page = _RefreshPage(frames={2: [{'lock': True}, frame,
                                              {'lock': True}, {'lock': True}]})
                action, _ = self._run(page)
                self.assertEqual(action['status'], 'verified')
                self.assertIs(action['after'], True)
                self.assertEqual(page.device.screenshot.call_count, 5)
                self.assertEqual(page.device.click.call_count, 2)
                page.device.click_record_remove.assert_called_once_with(LOCK_BUTTON)
                self.assert_no_extra_work(page)

    def test_persistent_final_page_or_identity_loss_stops_at_limit_without_extra_click(self):
        for frame in ({'page': False}, {'other': True}):
            with self.subTest(frame=frame):
                page = _RefreshPage(frames={2: [frame]})
                action, timers = self._run(page)
                self.assertNotEqual(action['status'], 'verified')
                self.assertIsNone(action['after'])
                self.assertEqual(timers[-1].calls, 12)
                self.assertEqual(page.device.screenshot.call_count, 13)
                self.assertEqual(page.device.click.call_count, 2)
                page.device.click_record_remove.assert_not_called()
                self.assert_no_extra_work(page)

    def test_same_identity_crop_with_different_static_attributes_or_visible_title_is_not_original(self):
        for frame in ({'changed_attributes': True}, {'changed_title': True}):
            with self.subTest(frame=frame):
                page = _VisualRefreshPage(frames={2: [frame]})
                stages = []

                def read_original_only(image):
                    stages.append(page.stage)
                    return read_lock_state(image)

                with patch(f'{MODULE}.read_lock_state', side_effect=read_original_only):
                    action, timers = self._run(page)
                self.assertNotEqual(action['status'], 'verified')
                self.assertIsNone(action['after'])
                self.assertEqual(stages, [0])
                self.assertEqual(timers[-1].calls, 12)
                self.assertEqual(page.device.screenshot.call_count, 13)
                self.assertEqual(page.device.click.call_count, 2)
                page.pause.assert_called_once_with(0.5)
                self.assert_no_extra_work(page)

    def test_body_and_icon_animation_does_not_invalidate_static_title_or_attribute_guards(self):
        page = _VisualRefreshPage(frames={2: [{'animated': True}, {'animated': False}]})
        action, _ = self._run(page)
        self.assertEqual(action['status'], 'verified')
        self.assertEqual(page.device.screenshot.call_count, 3)
        self.assert_sent_steps(action)
        self.assert_no_extra_work(page)

    def test_transient_visual_redraw_resets_streak_before_two_new_original_frames(self):
        for frame in ({'changed_attributes': True}, {'changed_title': True}):
            with self.subTest(frame=frame):
                page = _VisualRefreshPage(frames={2: [{}, frame, {}, {}]})
                action, _ = self._run(page)
                self.assertEqual(action['status'], 'verified')
                self.assertEqual(page.device.screenshot.call_count, 5)
                self.assert_sent_steps(action)
                self.assertEqual(page.device.click.call_count, 2)
                self.assert_no_extra_work(page)

    def test_one_changed_title_glyph_diluted_below_old_mean_threshold_is_rejected(self):
        page = _VisualRefreshPage(frames={2: [{'small_title_change': True}]})
        original = page.frame()
        page.small_title_change = True
        changed = page.frame()
        area = (858, 161, 1110, 188)
        self.assertGreater(_mean_diff(_crop(original, area), _crop(changed, area)), 0)
        self.assertLess(_mean_diff(_crop(original, area), _crop(changed, area)), 3)
        page.small_title_change = False
        action, timers = self._run(page)
        self.assertNotEqual(action['status'], 'verified')
        self.assertIsNone(action['after'])
        self.assertEqual(timers[-1].calls, 12)
        self.assertEqual(page.device.click.call_count, 2)
        page.device.click_record_remove.assert_not_called()
        self.assert_no_extra_work(page)

    def test_twenty_five_changed_attribute_pixels_below_old_mean_threshold_cannot_confirm_original(self):
        page = _VisualRefreshPage(frames={2: [{'small_attribute_change': True}]})
        original = page.frame()
        page.small_attribute_change = True
        changed = page.frame()
        area = STATIC_ATTRIBUTE_AREAS[0]
        before, after = _crop(original, area), _crop(changed, area)
        self.assertEqual(int((before != after).any(axis=2).sum()), 25)
        self.assertGreater(_mean_diff(before, after), 0)
        self.assertLess(_mean_diff(before, after), 3)
        page.small_attribute_change = False
        read_stages = []

        def read_original_only(image):
            read_stages.append(page.stage)
            return read_lock_state(image)

        with patch(f'{MODULE}.read_lock_state', side_effect=read_original_only):
            action, timers = self._run(page)
        self.assertNotEqual(action['status'], 'verified')
        self.assertIsNone(action['after'])
        self.assertEqual(read_stages, [0])
        self.assertEqual(timers[-1].calls, 12)
        self.assertEqual(page.device.screenshot.call_count, 13)
        self.assertEqual(page.device.click.call_count, 2)
        page.device.click_record_remove.assert_not_called()
        self.assert_no_extra_work(page)

    def test_changed_full_visible_row_geometry_cannot_be_accepted_as_original_page(self):
        page = _VisualRefreshPage(frames={2: [{'row_offset': 20}]})
        action, timers = self._run(page)
        self.assertNotEqual(action['status'], 'verified')
        self.assertIsNone(action['after'])
        self.assertEqual(timers[-1].calls, 12)
        self.assertEqual(page.device.click.call_count, 2)
        page.device.click_record_remove.assert_not_called()
        self.assert_no_extra_work(page)

    def test_persistent_unknown_or_opposite_final_state_is_bounded_without_third_click(self):
        for frame in ({'lock': None}, {'lock': False}):
            with self.subTest(frame=frame):
                page = _RefreshPage(frames={2: [frame]})
                action, timers = self._run(page)
                self.assertNotEqual(action['status'], 'verified')
                self.assertEqual(timers[-1].calls, 12)
                self.assertEqual(page.device.screenshot.call_count, 13)
                self.assertEqual(page.device.click.call_count, 2)
                self.assert_sent_steps(action)
                page.device.click_record_remove.assert_not_called()
                self.assert_no_extra_work(page)

    def test_one_responsive_click_does_not_restore_original_and_no_retry_is_added(self):
        for responses in ((False, True), (True, False)):
            with self.subTest(responses=responses):
                page = _RefreshPage(responds=responses)
                action, _ = self._run(page)
                self.assertNotEqual(action['status'], 'verified')
                self.assertIs(action['after'], False)
                self.assertEqual(page.device.click.call_count, 2)
                self.assert_sent_steps(action)
                self.assert_no_extra_work(page)

    def test_other_cat_lock_is_not_read_or_used_for_final_verification(self):
        page = _RefreshPage(frames={2: [{'other': True, 'lock': True}]})
        reads = []

        def read_original_only(image):
            self.assertFalse(page.identity_changed)
            reads.append(page.stage)
            return read_lock_state(image)

        with patch(f'{MODULE}.read_lock_state', side_effect=read_original_only):
            action, _ = self._run(page)
        self.assertNotEqual(action['status'], 'verified')
        self.assertEqual(reads, [0])
        page.device.click_record_remove.assert_not_called()

    def test_preflight_screenshot_exception_keeps_zero_steps_pending_and_propagates(self):
        page = _RefreshPage(frames={0: [{'raise': GameStuckError('匿名预检异常')}]})
        snapshot = page.snapshot()
        entry = new_refresh_action(snapshot)
        with self.assertRaises(GameStuckError):
            self._run(page, snapshot=snapshot, entry=entry)
        self.assertEqual(entry['status'], 'pending')
        self.assertEqual(entry['steps'], [])
        page.device.click.assert_not_called()
        page.pause.assert_not_called()

    def test_first_click_exception_preserves_unconfirmed_step_and_does_not_wait_or_retry(self):
        page = _RefreshPage()
        page.click_error_at = 1
        snapshot = page.snapshot()
        entry = new_refresh_action(snapshot)
        with self.assertRaises(GameTooManyClickError):
            self._run(page, snapshot=snapshot, entry=entry)
        self.assertEqual(entry['status'], 'unconfirmed')
        self.assertEqual(len(entry['steps']), 1)
        self.assertEqual(entry['steps'][0]['status'], 'unconfirmed')
        self.assertIsNone(entry['steps'][0]['after'])
        page.device.click.assert_called_once_with(LOCK_BUTTON)
        page.pause.assert_not_called()
        page.device.click_record_remove.assert_not_called()

    def test_between_click_wait_exception_preserves_sent_first_step_and_forbids_second_click(self):
        page = _RefreshPage()
        page.wait_error = RequestHumanTakeover('匿名等待异常')
        snapshot = page.snapshot()
        entry = new_refresh_action(snapshot)
        with self.assertRaises(RequestHumanTakeover):
            self._run(page, snapshot=snapshot, entry=entry)
        self.assertEqual(entry['status'], 'unconfirmed')
        self.assertEqual(len(entry['steps']), 1)
        self.assertEqual(entry['steps'][0]['status'], 'sent')
        self.assertIsNone(entry['steps'][0]['after'])
        page.device.click.assert_called_once_with(LOCK_BUTTON)
        page.pause.assert_called_once_with(0.5)
        page.device.click_record_remove.assert_not_called()

    def test_second_click_exception_preserves_sent_first_and_unconfirmed_second_without_third(self):
        page = _RefreshPage()
        page.click_error_at = 2
        snapshot = page.snapshot()
        entry = new_refresh_action(snapshot)
        with self.assertRaises(GameTooManyClickError):
            self._run(page, snapshot=snapshot, entry=entry)
        self.assertEqual(entry['status'], 'unconfirmed')
        self.assertEqual([step['status'] for step in entry['steps']], ['sent', 'unconfirmed'])
        self.assertEqual([step['after'] for step in entry['steps']], [None, None])
        self.assertEqual(page.device.click.call_count, 2)
        page.pause.assert_called_once_with(0.5)
        self.assertEqual(page.device.screenshot.call_count, 1)
        page.device.click_record_remove.assert_not_called()

    def test_final_screenshot_exception_preserves_both_sent_steps_and_propagates(self):
        page = _RefreshPage(frames={2: [{'raise': GameStuckError('匿名最终截图异常')}]})
        snapshot = page.snapshot()
        entry = new_refresh_action(snapshot)
        with self.assertRaises(GameStuckError):
            self._run(page, snapshot=snapshot, entry=entry)
        self.assertEqual(entry['status'], 'unconfirmed')
        self.assert_sent_steps(entry)
        self.assertEqual(page.device.click.call_count, 2)
        page.device.click_record_remove.assert_not_called()
        self.assert_no_extra_work(page)


if __name__ == '__main__':
    unittest.main()
