"""改锁刷新后的恢复与最终锁核验；仅使用内存模板，不连接游戏。"""

import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from module.exception import GameStuckError
from module.meowfficer.lock_recovery import TalentPageRecovery
from module.meowfficer.scan_roster import STATIC_ATTRIBUTE_AREAS
from module.meowfficer.scan_utils import _crop
from module.meowfficer.score import Talent
from module.meowfficer.score_lock import IDENTITY_AREA, LOCK_BUTTON, read_lock_state, set_lock_state
from tests.test_meowfficer_lock_transition import _FrameTimer, _TransitionPage
from tests.test_meowfficer_score_lock import DetailPage


class _SequencePage(DetailPage):
    """真实锁与页模板：第三只切锁后返回首只，立绘手势再逐只恢复。"""

    def __init__(self, *, response=True, jump_on_swipe=False):
        self.index = 2
        self.response = response
        self.jump_on_swipe = jump_on_swipe
        self.states = [False, True, True]
        super().__init__(responds=False)
        self.device.swipe = Mock(side_effect=self.swipe)
        self.device.click_record_remove = Mock()
        self.device.stuck_record_clear = Mock()
        self.scanned = ['已发布前缀']
        self.captures = [self.snapshot(display_name=f'匿名喵{i}', level=20 - i,
                                       talents_complete=True) for i in range(3)]
        recovery = TalentPageRecovery(self, object())
        for index, capture in enumerate(self.captures):
            self.index = index
            capture.identity_image = _crop(self.frame(), IDENTITY_AREA).copy()
            recovery.remember(capture, self.frame())
        self.index = 2
        self.device.image = self.frame()
        self._meowfficer_lock_recovery = recovery

    def frame(self):
        self.locked = self.states[self.index]
        image = super().frame()
        x0, y0, x1, y1 = IDENTITY_AREA
        image[y0:y1, x0:x1] = 30 * (self.index + 1)
        for x0, y0, x1, y1 in STATIC_ATTRIBUTE_AREAS:
            image[y0:y1, x0:x1] = 10 + self.index
        return image

    def screenshot(self):
        self.steps += 1
        if self.steps > 100:
            raise AssertionError('隔离恢复必须有限结束')
        self.device.image = self.frame()

    def click(self, button):
        if button is not LOCK_BUTTON or self.index != 2:
            raise AssertionError('恢复途中禁止给其他猫改锁')
        if self.response:
            self.states[2] = False
        self.index = 0

    def swipe(self, _start, _end, **kwargs):
        if kwargs.get('name') != 'MEOWFFICER_NEXT':
            raise AssertionError('只允许天赋页立绘左滑')
        self.index = 2 if self.jump_on_swipe else min(self.index + 1, 2)

    def _read_current_cat(self, _ocr):
        return self.captures[self.index].display_name, self.captures[self.index].level

    def capture(self, scanner, _ocr, name, level, *, reset_history):
        self_test = self.captures[self.index]
        if scanner is not self or reset_history or (name, level) != self._read_current_cat(_ocr):
            raise AssertionError('恢复读取不得猜猫身份或先清除历史')
        return replace(self_test, identity_image=_crop(self.device.image, IDENTITY_AREA).copy())

    @staticmethod
    def attributes(image, _ocr):
        x, y, _, _ = STATIC_ATTRIBUTE_AREAS[0]
        value = int(image[y, x, 0])
        return value, value + 1, value + 2


class RecoveryFlowTests(unittest.TestCase):
    def setUp(self):
        server = patch('module.config.server.server', 'cn')
        server.start()
        self.addCleanup(server.stop)

    def run_sequence(self, page):
        timers = [_FrameTimer(4), _FrameTimer(4)]
        with patch('module.meowfficer.score_lock.Timer', side_effect=timers), \
                patch('module.meowfficer.lock_recovery.capture_current_cat', side_effect=page.capture), \
                patch('module.meowfficer.lock_recovery.read_static_attributes', side_effect=page.attributes):
            action = set_lock_state(page, page.captures[-1], False, '建议喂掉')
        return action

    def test_real_swipe_and_lock_primitives_restore_target_without_reclick_or_scoring(self):
        page = _SequencePage()
        with patch('module.meowfficer.score_lock.read_lock_state', wraps=read_lock_state) as read:
            action = self.run_sequence(page)
        self.assertEqual(action['status'], 'changed')
        self.assertIs(action['before'], True)
        self.assertIs(action['after'], False)
        self.assertEqual(action['recovery'], {
            'status': 'verified', 'reason': '原目标完整资料及目标锁状态连续两次确认',
            'fromOrdinal': 1, 'toOrdinal': 3, 'swipes': 2})
        page.device.click.assert_called_once_with(LOCK_BUTTON)
        self.assertEqual(page.device.swipe.call_count, 2)
        self.assertEqual(read.call_count, 3)  # 旧猫虽然也是解锁，不能提供目标锁证据。
        self.assertEqual(page.index, 2)
        self.assertEqual(page.scanned, ['已发布前缀'])

    def test_target_still_opposite_after_recovery_stops_without_second_click(self):
        page = _SequencePage(response=False)
        action = self.run_sequence(page)
        self.assertEqual(action['status'], 'unconfirmed')
        self.assertIs(action['before'], True)
        self.assertIs(action['after'], True)
        self.assertEqual(action['recovery']['status'], 'failed')
        self.assertIn('目标锁状态', action['reason'])
        page.device.click.assert_called_once_with(LOCK_BUTTON)
        self.assertEqual(page.device.swipe.call_count, 2)

    def test_unexpected_jump_during_recovery_does_not_accept_even_correct_target_lock(self):
        page = _SequencePage(jump_on_swipe=True)
        action = self.run_sequence(page)
        self.assertEqual(action['status'], 'unconfirmed')
        self.assertIsNone(action['after'])
        self.assertEqual(action['recovery']['status'], 'failed')
        self.assertEqual(page.device.swipe.call_count, 1)
        self.assertEqual(page.device.stuck_record_clear.call_count, 1)  # 错误候选不能清理历史。
        page.device.click.assert_called_once_with(LOCK_BUTTON)

    def test_same_name_level_and_identity_pixels_cannot_hide_changed_talents_in_final_check(self):
        page = _SequencePage()
        calls = 0

        def current_capture(*args, **kwargs):
            nonlocal calls
            calls += 1
            captured = page.capture(*args, **kwargs)
            if calls == 5:  # 最后两次核验的第二次，底部名与级仍相同。
                return replace(captured, talents=[Talent('另一项已完整天赋', '另一线', 1)])
            return captured

        with patch('module.meowfficer.score_lock.Timer', return_value=_FrameTimer(4)), \
                patch('module.meowfficer.lock_recovery.capture_current_cat', side_effect=current_capture), \
                patch('module.meowfficer.lock_recovery.read_static_attributes', side_effect=page.attributes), \
                patch('module.meowfficer.score_lock.read_lock_state', wraps=read_lock_state) as read:
            action = set_lock_state(page, page.captures[-1], False, '建议喂掉')
        self.assertEqual(calls, 5)
        self.assertEqual(action['status'], 'unconfirmed')
        self.assertIsNone(action['after'])
        self.assertEqual(action['recovery']['status'], 'failed')
        self.assertEqual(read.call_count, 2)  # 初次锁读取 + 一次完整原目标；不能接受伪第二帧。
        page.device.click.assert_called_once_with(LOCK_BUTTON)

    def test_same_identity_pixels_cannot_hide_changed_attributes_in_final_check(self):
        page = _SequencePage()
        calls = 0

        def current_capture(*args, **kwargs):
            nonlocal calls
            calls += 1
            return page.capture(*args, **kwargs)

        def attributes(image, ocr):
            return (999, 998, 997) if calls >= 4 else page.attributes(image, ocr)

        with patch('module.meowfficer.score_lock.Timer', return_value=_FrameTimer(4)), \
                patch('module.meowfficer.lock_recovery.capture_current_cat', side_effect=current_capture), \
                patch('module.meowfficer.lock_recovery.read_static_attributes', side_effect=attributes), \
                patch('module.meowfficer.score_lock.read_lock_state', wraps=read_lock_state) as read:
            action = set_lock_state(page, page.captures[-1], False, '建议喂掉')
        self.assertEqual(action['status'], 'unconfirmed')
        self.assertIsNone(action['after'])
        self.assertEqual(read.call_count, 1)
        page.device.click.assert_called_once_with(LOCK_BUTTON)

    def test_device_error_during_recovery_keeps_original_pending_action_and_propagates(self):
        page = _SequencePage()
        page.device.swipe.side_effect = GameStuckError('隔离设备异常')
        action = {'name': page.captures[-1].display_name, 'before': None, 'after': None,
                  'target': False, 'status': 'skipped', 'reason': '建议喂掉'}
        with patch('module.meowfficer.score_lock.Timer', return_value=_FrameTimer(4)), \
                patch('module.meowfficer.lock_recovery.capture_current_cat', side_effect=page.capture), \
                patch('module.meowfficer.lock_recovery.read_static_attributes', side_effect=page.attributes):
            with self.assertRaises(GameStuckError):
                set_lock_state(page, page.captures[-1], False, '建议喂掉', entry=action)
        self.assertEqual(action['status'], 'unconfirmed')
        self.assertIs(action['before'], True)
        self.assertIsNone(action['after'])
        page.device.click.assert_called_once_with(LOCK_BUTTON)

    def test_recovery_is_not_called_for_unknown_page_or_original_lock_state(self):
        for frame in ({'page': False}, {'lock': None}, {'lock': True}):
            with self.subTest(frame=frame):
                page = _TransitionPage(frames=(frame,))
                page._meowfficer_lock_recovery = Mock()
                with patch('module.meowfficer.score_lock.Timer', return_value=_FrameTimer(4)):
                    action = set_lock_state(page, page.snapshot(), False, '培养建议')
                self.assertEqual(action['status'], 'unconfirmed')
                page._meowfficer_lock_recovery.restore.assert_not_called()
                page.device.click.assert_called_once_with(LOCK_BUTTON)

    def test_transient_return_to_original_uses_normal_confirmation_without_recovery(self):
        page = _TransitionPage(frames=({'other': True}, {'lock': False}, {'lock': False}))
        page._meowfficer_lock_recovery = Mock()
        with patch('module.meowfficer.score_lock.Timer', return_value=_FrameTimer(4)):
            action = set_lock_state(page, page.snapshot(), False, '培养建议')
        self.assertEqual(action['status'], 'changed')
        page._meowfficer_lock_recovery.restore.assert_not_called()


if __name__ == '__main__':
    unittest.main()
