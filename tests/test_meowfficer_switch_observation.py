"""一次左滑后的有界持续观察，不继承缺失等级、不由装饰动画断言切换。"""

import unittest
from unittest.mock import Mock, patch

from module.exception import GameStuckError, GameTooManyClickError, RequestHumanTakeover
from module.meowfficer.scan_next import (MAX_SWITCH_OBSERVATIONS, MIN_SWITCH_OBSERVATIONS,
                                        _switch_view_unchanged, swipe_next_cat)
from module.meowfficer.scan_utils import CURRENT_CAT_LEVEL_AREA, CURRENT_CAT_NAME_AREA
from tests.test_meowfficer_scan_next import NEW_NAME, OLD_LEVEL, OLD_NAME, _Scanner, _Shot, _shot
from tests.test_meowfficer_scan_coverage import _animated_talent_frame


def _animated_shot(*, name=NEW_NAME, level=8, animated=False, background=30,
                   changed_title=False, offset=0):
    """只在姓名等级之外改变背景与按钮，右侧使用匿名静态标题及实际列表行距。"""
    shot = _shot(name, level)
    image = shot.image.copy()
    image[566:599, 360:610] = background
    image[600:618, 450:630] = background
    panel = _animated_talent_frame(offset=offset, animated=animated,
                                   changed_title_indices={2} if changed_title else ())
    image[152:588, 744:1244] = panel[152:588, 744:1244]
    return _Shot(image, name, level)


class SwitchViewEvidenceTests(unittest.TestCase):
    """字段及实际列表保留原严格阈值，背景、功能按钮与正文动画不承担身份。"""

    def test_wide_background_buttons_portrait_and_body_animation_do_not_break_static_fields(self):
        before = _animated_shot(background=20)
        after = _animated_shot(background=180, animated=True)
        after.image[180:520, 150:630] = 190
        self.assertTrue(_switch_view_unchanged(before.image, after.image))

    def test_name_or_level_field_at_original_three_pixel_threshold_is_not_stable(self):
        for area in (CURRENT_CAT_NAME_AREA, CURRENT_CAT_LEVEL_AREA):
            with self.subTest(area=area):
                before = _animated_shot()
                after = _animated_shot()
                x0, y0, x1, y1 = area
                after.image[y0:y1, x0:x1] += 3
                self.assertFalse(_switch_view_unchanged(before.image, after.image))

    def test_real_row_motion_or_conflicting_static_title_does_not_pass_as_body_animation(self):
        before = _animated_shot()
        for after in (_animated_shot(offset=75, animated=True),
                      _animated_shot(animated=True, changed_title=True)):
            with self.subTest(changed=after.level):
                self.assertFalse(_switch_view_unchanged(before.image, after.image))

    def test_missing_previous_frame_never_claims_stability(self):
        self.assertFalse(_switch_view_unchanged(None, _shot().image))


class BoundedSwitchObservationTests(unittest.TestCase):
    """短暂识别失败可继续到原时间及新帧数上限，但整个观察始终只有一个手势。"""

    def setUp(self):
        guard = patch('module.meowfficer.scan_next.detail_page_confirmed',
                      side_effect=lambda image: bool(image[0, 0, 0]))
        guard.start()
        self.addCleanup(guard.stop)
        self.ocr = object()

    def _run(self, scanner, *, level=OLD_LEVEL, defer_same_name=False, expires=False):
        timer = Mock()
        timer.start.return_value = timer
        timer.reached.return_value = expires
        with patch('module.meowfficer.scan_next.Timer', return_value=timer) as factory:
            try:
                result = swipe_next_cat(scanner, self.ocr, OLD_NAME, level,
                                        defer_same_name=defer_same_name)
            finally:
                # 到限与异常路径同样不能通过重复重置延长观察；手势异常前尚未创建计时器。
                if factory.called:
                    factory.assert_called_once_with(8)
                    timer.start.assert_called_once_with()
                else:
                    timer.start.assert_not_called()
                timer.reset.assert_not_called()
        return result

    @staticmethod
    def _assert_one_swipe(scanner):
        if scanner.device.swipes != [((560, 350), (220, 350), 0.45, 'MEOWFFICER_NEXT')]:
            raise AssertionError(f'观察阶段重复发送或改变切猫手势：{scanner.device.swipes!r}')

    def _assert_history_kept(self, scanner):
        self._assert_one_swipe(scanner)
        self.assertEqual(scanner.device.removed, [])
        self.assertEqual(scanner.device.history, ['OTHER_BUTTON', 'SWIPE', 'SWIPE', 'MEOWFFICER_NEXT'])

    def test_more_than_twelve_unknown_frames_can_recover_with_only_one_swipe(self):
        scanner = _Scanner([_shot(page=False)] * 20 + [_shot(NEW_NAME, 8)] * 4)
        self.assertEqual(self._run(scanner), (NEW_NAME, 8))
        self.assertEqual(scanner.device.screenshots, 25)
        self._assert_one_swipe(scanner)
        self.assertEqual(scanner.device.removed, ['MEOWFFICER_NEXT', 'SWIPE'])

    def test_elapsed_timer_still_observes_at_least_twelve_frames_without_repeating_swipe(self):
        scanner = _Scanner([_shot(page=False)] * MAX_SWITCH_OBSERVATIONS)
        with self.assertRaisesRegex(RequestHumanTakeover, '天赋页未能正向确认'):
            self._run(scanner, expires=True)
        self.assertEqual(scanner.device.screenshots, MIN_SWITCH_OBSERVATIONS + 1)
        self.assertEqual(scanner.reads, [(OLD_NAME, OLD_LEVEL)])
        self._assert_history_kept(scanner)

    def test_unknown_page_stops_at_forty_frames_even_when_timer_never_expires(self):
        self.assertEqual(MAX_SWITCH_OBSERVATIONS, 40)
        scanner = _Scanner([_shot(page=False)] * MAX_SWITCH_OBSERVATIONS)
        with patch('module.meowfficer.scan_next.logger.attr') as diagnostic, \
                self.assertRaisesRegex(RequestHumanTakeover, '天赋页未能正向确认'):
            self._run(scanner)
        self.assertEqual(scanner.device.screenshots, MAX_SWITCH_OBSERVATIONS + 1)
        self._assert_history_kept(scanner)
        payload = diagnostic.call_args.args[1]
        self.assertEqual(payload['frames'], 40)
        self.assertEqual(payload['pageUnknown'], 40)
        self.assertEqual(payload['lastRead'], None)

    def test_changed_name_returns_before_minimum_same_name_observation_budget(self):
        scanner = _Scanner([_shot(NEW_NAME, 8)] * 4)
        self.assertEqual(self._run(scanner, expires=True), (NEW_NAME, 8))
        self.assertEqual(scanner.device.screenshots, 5)
        self._assert_one_swipe(scanner)
        self.assertEqual(scanner.device.history, ['OTHER_BUTTON'])

    def test_identical_candidate_waits_twelve_frames_and_keeps_same_name_history(self):
        scanner = _Scanner([_shot()] * MAX_SWITCH_OBSERVATIONS)
        self.assertIsNone(self._run(scanner))
        self.assertEqual(scanner.device.screenshots, MIN_SWITCH_OBSERVATIONS + 1)
        self._assert_history_kept(scanner)

    def test_unknown_level_endpoints_return_actual_candidate_only_when_deferred(self):
        for old_level, new_level in ((5, None), (None, 9)):
            for deferred in (False, True):
                with self.subTest(old_level=old_level, new_level=new_level, deferred=deferred):
                    scanner = _Scanner([_shot(level=new_level)] * MAX_SWITCH_OBSERVATIONS,
                                       before=_shot(level=old_level))
                    if deferred:
                        self.assertEqual(self._run(scanner, level=old_level, defer_same_name=True),
                                         (OLD_NAME, new_level))
                        self.assertEqual(scanner.device.screenshots, MIN_SWITCH_OBSERVATIONS + 1)
                    else:
                        with self.assertRaisesRegex(RequestHumanTakeover, '同名猫等级尚未完整确认'):
                            self._run(scanner, level=old_level)
                        self.assertEqual(scanner.device.screenshots, MAX_SWITCH_OBSERVATIONS + 1)
                    self._assert_history_kept(scanner)

    def test_both_unknown_and_identical_candidate_returns_none_without_imputing_level(self):
        for deferred in (False, True):
            with self.subTest(deferred=deferred):
                scanner = _Scanner([_shot(level=None)] * MAX_SWITCH_OBSERVATIONS,
                                   before=_shot(level=None))
                self.assertIsNone(self._run(scanner, level=None, defer_same_name=deferred))
                self.assertTrue(all(level is None for _name, level in scanner.reads))
                self.assertEqual(scanner.device.screenshots, MIN_SWITCH_OBSERVATIONS + 1)
                self._assert_history_kept(scanner)

    def test_same_name_different_confirmed_levels_are_immediate_proven_progress(self):
        scanner = _Scanner([_shot(level=9)] * 4)
        self.assertEqual(self._run(scanner, defer_same_name=True), (OLD_NAME, 9))
        self.assertEqual(scanner.device.screenshots, 5)
        self.assertEqual(scanner.device.history, ['OTHER_BUTTON'])

    def test_alternating_identity_or_empty_name_cannot_be_confirmed_before_cap(self):
        for frames, expected_reason in (([_shot(NEW_NAME, 8), _shot('蒂奇喵', 8)] * 20,
                                         '当前猫身份尚未连续两次一致'),
                                        ([_shot(name='')] * 40, '当前猫名未能读清')):
            with self.subTest(reason=expected_reason):
                scanner = _Scanner(frames)
                with self.assertRaisesRegex(RequestHumanTakeover, expected_reason):
                    self._run(scanner, defer_same_name=True)
                self.assertEqual(scanner.device.screenshots, 41)
                self._assert_history_kept(scanner)

    def test_only_animation_outside_fields_and_static_titles_allows_new_identity(self):
        scanner = _Scanner([_animated_shot(animated=False, background=20),
                            _animated_shot(animated=True, background=180)] * 2)
        self.assertEqual(self._run(scanner), (NEW_NAME, 8))
        self.assertEqual(scanner.device.screenshots, 5)
        self._assert_one_swipe(scanner)

    def test_true_field_title_or_row_changes_remain_unstable_and_do_not_clear_history(self):
        frame_pairs = ((_shot(NEW_NAME, 8, identity_color=20),
                         _shot(NEW_NAME, 8, identity_color=180)),
                       (_animated_shot(), _animated_shot(offset=75, animated=True)),
                       (_animated_shot(), _animated_shot(animated=True, changed_title=True)))
        for pair in frame_pairs:
            with self.subTest(level=pair[0].level):
                scanner = _Scanner(list(pair) * 20)
                with self.assertRaisesRegex(RequestHumanTakeover, '尚未连续稳定'):
                    self._run(scanner)
                self.assertEqual(scanner.device.screenshots, 41)
                self._assert_history_kept(scanner)

    def test_control_errors_propagate_without_second_swipe_or_history_cleanup(self):
        for source in ('screenshot', 'swipe', 'identity'):
            for error_type in (GameStuckError, GameTooManyClickError, RequestHumanTakeover):
                with self.subTest(source=source, error_type=error_type):
                    error = error_type('切猫观察期间流程异常')
                    kwargs = {f'{source}_error': error} if source != 'identity' else {}
                    scanner = _Scanner([_shot(NEW_NAME)] * 4, **kwargs)
                    if source == 'identity':
                        scanner.read_error = error
                    with self.assertRaises(error_type) as caught:
                        self._run(scanner)
                    self.assertIs(caught.exception, error)
                    self.assertEqual(scanner.device.removed, [])
                    self.assertLessEqual(len(scanner.device.swipes), 1)


if __name__ == '__main__':
    unittest.main()
