"""立绘左滑切猫的状态与身份保护回归；设备、截图和 OCR 均为内存夹具。"""

from dataclasses import dataclass
import unittest
from unittest.mock import patch

import numpy as np

from module.exception import GameStuckError, RequestHumanTakeover
from module.meowfficer.scan_next import MAX_SWITCH_OBSERVATIONS, swipe_next_cat
from module.meowfficer.scan_utils import CURRENT_CAT_AREA, TALENT_PANEL_AREA


OLD_NAME = '林德喵'
OLD_LEVEL = 5
NEW_NAME = '埃弗喵'


@dataclass
class _Shot:
    """画面与 OCR 身份分开，允许模拟 OCR 抖动及同名同级猫。"""

    image: np.ndarray
    name: str = OLD_NAME
    level: int | None = OLD_LEVEL


def _shot(name=OLD_NAME, level=OLD_LEVEL, identity_color=30, panel_color=50,
          page=True, portrait_color=10):
    """匿名色块只覆盖待验证区域，不保存游戏或账号图片。"""
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[0, 0, 0] = 255 if page else 0
    for area, color in ((CURRENT_CAT_AREA, identity_color), (TALENT_PANEL_AREA, panel_color)):
        x0, y0, x1, y1 = area
        image[y0:y1, x0:x1] = color
    image[180:520, 150:630] = portrait_color
    return _Shot(image, name, level)


class _Device:
    """一次手势后逐帧展示队列，耗尽时保留最后一帧。"""

    def __init__(self, before, after, screenshot_error=None, swipe_error=None):
        self.current = before
        self.image = before.image.copy()
        self.after = list(after)
        self.screenshots = 0
        self.swipes = []
        self.removed = []
        self.history = ['OTHER_BUTTON', 'SWIPE', 'SWIPE']
        self.events = []
        self.screenshot_error = screenshot_error
        self.swipe_error = swipe_error

    def screenshot(self):
        self.screenshots += 1
        if self.screenshot_error is not None and self.screenshots >= 2:
            raise self.screenshot_error
        if self.screenshots >= 2 and self.after:
            self.current = self.after.pop(0)
        self.image = self.current.image.copy()
        self.events.append(('screenshot', self.screenshots))

    def swipe(self, start, end, duration, name):
        if self.swipe_error is not None:
            raise self.swipe_error
        self.swipes.append((start, end, duration, name))
        self.history.append(name)
        self.events.append(('swipe', name))

    def click_record_remove(self, name):
        self.removed.append(name)
        self.events.append(('remove', name))
        count = self.history.count(name)
        self.history = [item for item in self.history if item != name]
        return count

    def stuck_record_clear(self):
        self.events.append(('stuck_clear',))


class _Scanner:
    """仅提供切猫需要的接口，读数直接对应当前截图。"""

    def __init__(self, after, before=None, **device_kwargs):
        self.device = _Device(_shot() if before is None else before, after, **device_kwargs)
        self.reads = []
        self.read_error = None

    def _read_current_cat(self, ocr):
        if self.read_error is not None and self.device.screenshots >= 2:
            raise self.read_error
        identity = (self.device.current.name, self.device.current.level)
        self.reads.append(identity)
        self.device.events.append(('read', identity))
        return identity


class SwipeNextCatTests(unittest.TestCase):
    """确认身份变化才能清理保护；同名候选交给完整天赋核验。"""

    def setUp(self):
        guard = patch('module.meowfficer.scan_next.detail_page_confirmed',
                      side_effect=lambda image: bool(image[0, 0, 0]))
        guard.start()
        self.addCleanup(guard.stop)
        self.ocr = object()

    def _run(self, scanner, name=OLD_NAME, level=OLD_LEVEL):
        return swipe_next_cat(scanner, self.ocr, name, level)

    def _assert_one_swipe(self, scanner):
        self.assertEqual(scanner.device.swipes,
                         [((560, 350), (220, 350), 0.45, 'MEOWFFICER_NEXT')])

    def _assert_history_kept(self, scanner):
        self.assertEqual(scanner.device.removed, [])
        self.assertIn('OTHER_BUTTON', scanner.device.history)
        self.assertEqual(scanner.device.history.count('SWIPE'), 2)

    def test_new_name_requires_stable_page_and_two_equal_reads_before_cleanup(self):
        scanner = _Scanner([_shot(NEW_NAME, 8, identity_color=80, panel_color=100)] * 4)
        self.assertEqual(self._run(scanner), (NEW_NAME, 8))
        self._assert_one_swipe(scanner)
        self.assertEqual(scanner.device.removed, ['MEOWFFICER_NEXT', 'SWIPE'])
        self.assertEqual(scanner.device.history, ['OTHER_BUTTON'])
        first_remove = next(index for index, event in enumerate(scanner.device.events)
                            if event[0] == 'remove')
        prior_reads = [event[1] for event in scanner.device.events[:first_remove]
                       if event[0] == 'read']
        self.assertEqual(prior_reads[-2:], [(NEW_NAME, 8), (NEW_NAME, 8)])

    def test_delayed_old_frames_do_not_prematurely_report_last_cat(self):
        scanner = _Scanner([_shot()] * 5 + [_shot(NEW_NAME, 8, panel_color=100)] * 4)
        self.assertEqual(self._run(scanner), (NEW_NAME, 8))
        self.assertGreater(scanner.device.screenshots, 6)
        self._assert_one_swipe(scanner)

    def test_transition_page_can_recover_without_a_second_swipe(self):
        scanner = _Scanner([_shot(page=False)] * 3 + [_shot(NEW_NAME, 8)] * 4)
        self.assertEqual(self._run(scanner), (NEW_NAME, 8))
        self._assert_one_swipe(scanner)

    def test_same_name_with_readable_different_level_is_confirmed_progress(self):
        scanner = _Scanner([_shot(level=9, identity_color=100)] * 4)
        self.assertEqual(self._run(scanner), (OLD_NAME, 9))
        self._assert_one_swipe(scanner)

    def test_unchanged_last_cat_exhausts_budget_without_clearing_history(self):
        scanner = _Scanner([_shot()] * 12)
        self.assertIsNone(self._run(scanner))
        self.assertEqual(scanner.device.screenshots, 13)
        self._assert_one_swipe(scanner)
        self._assert_history_kept(scanner)
        self.assertIn('MEOWFFICER_NEXT', scanner.device.history)

    def test_portrait_animation_does_not_prove_another_cat(self):
        scanner = _Scanner([_shot(portrait_color=20 if index % 2 else 180)
                            for index in range(12)])
        self.assertIsNone(self._run(scanner))
        self._assert_history_kept(scanner)

    def test_same_name_same_level_with_changed_talents_requires_complete_comparison(self):
        scanner = _Scanner([_shot(identity_color=120, panel_color=180)] * 12)
        self.assertIsNone(self._run(scanner))
        self._assert_one_swipe(scanner)
        self._assert_history_kept(scanner)

    def test_missing_name_never_counts_as_end_or_next_cat(self):
        scanner = _Scanner([_shot(name='')] * 12)
        with self.assertRaises(RequestHumanTakeover):
            self._run(scanner)
        self._assert_one_swipe(scanner)
        self._assert_history_kept(scanner)

    def test_unreadable_level_cannot_prove_same_name_progress(self):
        scanner = _Scanner([_shot(level=None)] * 12)
        with self.assertRaises(RequestHumanTakeover):
            self._run(scanner)
        self._assert_history_kept(scanner)

    def test_unknown_level_is_allowed_when_changed_name_is_confirmed(self):
        scanner = _Scanner([_shot(NEW_NAME, None)] * 4)
        self.assertEqual(self._run(scanner), (NEW_NAME, None))
        self._assert_one_swipe(scanner)

    def test_previously_unknown_level_alone_does_not_prove_progress(self):
        scanner = _Scanner([_shot(level=9)] * 12, before=_shot(level=None))
        with self.assertRaises(RequestHumanTakeover):
            self._run(scanner, level=None)
        self._assert_history_kept(scanner)

    def test_unchanged_unknown_level_can_return_for_list_verification(self):
        scanner = _Scanner([_shot(level=None)] * 12, before=_shot(level=None))
        self.assertIsNone(self._run(scanner, level=None))
        self._assert_history_kept(scanner)

    def test_permanent_page_loss_stops_after_bounded_observation(self):
        scanner = _Scanner([_shot(page=False)] * 12)
        with self.assertRaises(RequestHumanTakeover):
            self._run(scanner)
        self.assertEqual(scanner.device.screenshots, MAX_SWITCH_OBSERVATIONS + 1)
        self._assert_one_swipe(scanner)
        self._assert_history_kept(scanner)
        self.assertEqual(scanner.reads, [(OLD_NAME, OLD_LEVEL)])

    def test_page_loss_interrupts_stability_and_identity_confirmation(self):
        scanner = _Scanner([_shot(NEW_NAME, 8)] * 3 + [_shot(page=False)]
                           + [_shot(NEW_NAME, 8)] * 4)
        self.assertEqual(self._run(scanner), (NEW_NAME, 8))
        self.assertGreaterEqual(scanner.device.screenshots, 9)
        self._assert_one_swipe(scanner)

    def test_unknown_start_page_does_not_send_a_gesture(self):
        scanner = _Scanner([_shot(NEW_NAME)], before=_shot(page=False))
        with self.assertRaises(RequestHumanTakeover):
            self._run(scanner)
        self.assertEqual(scanner.device.swipes, [])
        self._assert_history_kept(scanner)

    def test_mismatched_start_name_or_level_does_not_send_a_gesture(self):
        for before in (_shot(NEW_NAME), _shot(level=9), _shot(level=None), _shot(name='')):
            with self.subTest(name=before.name, level=before.level):
                scanner = _Scanner([_shot(NEW_NAME)], before=before)
                with self.assertRaises(RequestHumanTakeover):
                    self._run(scanner)
                self.assertEqual(scanner.device.swipes, [])
                self._assert_history_kept(scanner)

    def test_either_identity_or_talent_animation_blocks_unstable_progress(self):
        for changing in ('identity', 'talent'):
            with self.subTest(changing=changing):
                frames = []
                for index in range(MAX_SWITCH_OBSERVATIONS):
                    color = 20 if index % 2 else 180
                    frames.append(_shot(NEW_NAME, 8,
                                        identity_color=color if changing == 'identity' else 80,
                                        panel_color=color if changing == 'talent' else 80))
                scanner = _Scanner(frames)
                with self.assertRaises(RequestHumanTakeover):
                    self._run(scanner)
                self._assert_one_swipe(scanner)
                self._assert_history_kept(scanner)

    def test_ocr_identity_flicker_on_stable_pixels_is_not_confirmed(self):
        scanner = _Scanner([_shot(NEW_NAME if index % 2 else '蒂奇喵', 8)
                            for index in range(MAX_SWITCH_OBSERVATIONS)])
        with self.assertRaises(RequestHumanTakeover):
            self._run(scanner)
        self._assert_one_swipe(scanner)
        self._assert_history_kept(scanner)

    def test_device_screenshot_and_swipe_errors_propagate_unchanged(self):
        for source in ('screenshot', 'swipe'):
            with self.subTest(source=source):
                error = GameStuckError(f'{source} 测试异常')
                scanner = _Scanner([_shot(NEW_NAME)], **{f'{source}_error': error})
                with self.assertRaises(GameStuckError) as raised:
                    self._run(scanner)
                self.assertIs(raised.exception, error)
                self._assert_history_kept(scanner)

    def test_ocr_control_error_propagates_without_cleaning_history(self):
        scanner = _Scanner([_shot(NEW_NAME)] * 4)
        scanner.read_error = GameStuckError('读取身份测试异常')
        with self.assertRaises(GameStuckError) as raised:
            self._run(scanner)
        self.assertIs(raised.exception, scanner.read_error)
        self._assert_one_swipe(scanner)
        self._assert_history_kept(scanner)


if __name__ == '__main__':
    unittest.main()
