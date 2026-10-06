"""猫窝翻屏、末页重叠与顶部复位回归，只使用匿名内存截图。"""

from collections import Counter
import unittest
from unittest.mock import patch

import numpy as np

from module.exception import GameStuckError, RequestHumanTakeover
from module.meowfficer.scan import MeowfficerScanner
from module.meowfficer.scan_list import card_center, selected_card
from module.meowfficer.scan_utils import scroll_offset
from tests import test_meowfficer_swipe_flow as flow_fixtures


_GRID_AREA = (718, 133, 1245, 550)
_RNG = np.random.default_rng(20261007)
_TEXTURE = _RNG.integers(30, 200, size=(9000, 527, 1), dtype=np.uint8)


def _frame(position, selected=None, scrollbar=True):
    """纵向随机灰度纹理没有账号信息，也不会误中黄色选中环。"""
    image = np.full((720, 1280, 3), 220, dtype=np.uint8)
    x0, y0, x1, y1 = _GRID_AREA
    image[y0:y1, x0:x1] = np.repeat(_TEXTURE[position:position + y1 - y0], 3, axis=2)
    if selected is not None:
        cx, cy = card_center(selected)
        for dy in (-24, 22):
            image[cy + dy:cy + dy + 4, cx + 50:cx + 54] = (245, 190, 50)
    if scrollbar:
        row = 140 if position == 0 else 220
        image[row:row + 20, 1248:1253] = (245, 190, 50)
    return image


class _MemoryDevice:
    """手势推进匿名列表位置，截图仅返回内存中的当前画面。"""

    def __init__(self, steps=(), position=0, selected_after=None, scrollbar=True):
        self.steps = list(steps)
        self.position = position
        self.selected_after = selected_after
        self.scrollbar = scrollbar
        self.swipes = []
        self.removed = []
        self.history = ['OTHER_BUTTON', 'SWIPE']
        self.screenshots = 0
        self.image = _frame(position, scrollbar=scrollbar)
        self.swipe_error = None

    def screenshot(self):
        self.screenshots += 1

    def swipe(self, start, end, duration, name):
        if self.swipe_error:
            raise self.swipe_error
        self.swipes.append((start, end, duration, name))
        self.history.append(name)
        step = self.steps.pop(0) if self.steps else 0
        self.position = max(0, self.position + step)
        self.image = _frame(self.position, self.selected_after, self.scrollbar)

    def click_record_remove(self, name):
        self.removed.append(name)
        self.history = [item for item in self.history if item != name]


class _ScrollScanner(MeowfficerScanner):
    """保留真实稳定截图、复位与翻屏方法，仅替代设备及页面模板检测。"""

    def __init__(self, steps=(), **kwargs):
        self.device = _MemoryDevice(steps, **kwargs)
        self.list_page = True

    def appear(self, button, **kwargs):
        return self.list_page


class SwipeScrollTests(unittest.TestCase):
    def _advance(self, steps, offsets=None, selected=None):
        scanner = _ScrollScanner(steps, selected_after=selected)
        if offsets is None:
            return scanner, scanner._advance_verified_page()
        with patch('module.meowfficer.scan.scroll_offset', side_effect=offsets):
            rows = scanner._advance_verified_page()
        return scanner, rows

    def test_full_page_measures_three_rows_with_real_texture_direction(self):
        scanner, rows = self._advance([146, 146, 146])
        self.assertEqual(rows, 3)
        self.assertEqual(scanner.device.position, 438)
        self.assertEqual(len(scanner.device.swipes), 3)
        self.assertTrue(all(start[1] > end[1] for start, end, _, _ in scanner.device.swipes))
        self.assertEqual(scanner.device.history, ['OTHER_BUTTON', 'SWIPE'])

    def test_scroll_offset_uses_content_moving_up_as_forward_progress(self):
        before = np.repeat(_TEXTURE[0:417], 3, axis=2)
        after = np.repeat(_TEXTURE[146:563], 3, axis=2)
        self.assertEqual(scroll_offset(before, after), 146)
        backward_before, backward_after = after, before
        self.assertEqual(scroll_offset(backward_after, backward_before), 146)

    def test_overshoot_is_corrected_with_reverse_swipe_before_accepting_page(self):
        scanner, rows = self._advance([180, 180, 100, -22], [180, 180, 100, 22])
        self.assertEqual(rows, 3)
        self.assertEqual(scanner.device.position, 438)
        self.assertEqual(len(scanner.device.swipes), 4)
        start, end, _, _ = scanner.device.swipes[-1]
        self.assertLess(start[1], end[1])
        self.assertEqual(scanner.device.history, ['OTHER_BUTTON', 'SWIPE'])

    def test_real_measured_forward_and_reverse_textures_pass_overlap_quality(self):
        scanner, rows = self._advance([180, 180, 100, -22])
        self.assertEqual(rows, 3)
        self.assertEqual(scanner.device.position, 438)
        self.assertEqual(len(scanner.device.swipes), 4)
        self.assertEqual(scanner.device.history, ['OTHER_BUTTON', 'SWIPE'])

    def test_incorrect_best_nonzero_offset_is_rejected_before_progress_cleanup(self):
        for actual in (0, 30, 280):
            with self.subTest(actual_motion=actual):
                scanner = _ScrollScanner([actual])
                with patch('module.meowfficer.scan.scroll_offset', return_value=146):
                    with self.assertRaisesRegex(RequestHumanTakeover, '滚动前后内容不匹配'):
                        scanner._advance_verified_page()
                self.assertEqual(len(scanner.device.swipes), 1)
                self.assertEqual(scanner.device.removed, [])
                self.assertIn('MEOWFFICER_LIST_SCROLL', scanner.device.history)

    def test_reverse_best_offset_must_also_match_overlap_content(self):
        scanner = _ScrollScanner([180, 180, 100, -25])
        with patch('module.meowfficer.scan.scroll_offset', side_effect=[180, 180, 100, 22]):
            with self.assertRaisesRegex(RequestHumanTakeover, '滚动前后内容不匹配'):
                scanner._advance_verified_page()
        self.assertEqual(len(scanner.device.swipes), 4)
        self.assertEqual(len(scanner.device.removed), 3)
        self.assertEqual(scanner.device.history.count('MEOWFFICER_LIST_SCROLL'), 1)

    def test_local_content_change_cannot_hide_behind_small_global_mean(self):
        scanner = _ScrollScanner([146])
        real_swipe = scanner.device.swipe

        def changed_swipe(*args, **kwargs):
            real_swipe(*args, **kwargs)
            # 31/271 的重叠区域改变 30 灰度，整体均差不足 5，局部变化仍须拒绝。
            scanner.device.image[133:164, 718:1245] += 30

        with patch.object(scanner.device, 'swipe', side_effect=changed_swipe), \
                patch('module.meowfficer.scan.scroll_offset', return_value=146):
            with self.assertRaisesRegex(RequestHumanTakeover, '滚动前后内容不匹配'):
                scanner._advance_verified_page()
        self.assertEqual(scanner.device.removed, [])

    def test_last_one_or_two_rows_require_correct_old_last_cat_position(self):
        for rows in (1, 2):
            with self.subTest(rows=rows):
                steps = [146] * rows + [0]
                scanner, actual = self._advance(steps, steps, selected=11 - rows * 4)
                self.assertEqual(actual, rows)
                self.assertEqual(selected_card(scanner.device.image), 11 - rows * 4)
                self.assertEqual(scanner.device.position, rows * 146)
                self.assertEqual(scanner.device.history.count('MEOWFFICER_LIST_SCROLL'), 1)

    def test_no_motion_on_stable_list_is_confirmed_end(self):
        scanner, rows = self._advance([0], [0], selected=11)
        self.assertEqual(rows, 0)
        self.assertEqual(len(scanner.device.swipes), 1)
        self.assertEqual(scanner.device.removed, [])

    def test_wrong_or_missing_selected_anchor_stops_partial_page(self):
        for selected in (None, 0, 6, 11):
            with self.subTest(selected=selected):
                scanner = _ScrollScanner([146, 0], selected_after=selected)
                with patch('module.meowfficer.scan.scroll_offset', side_effect=[146, 0]):
                    with self.assertRaises(RequestHumanTakeover):
                        scanner._advance_verified_page()

    def test_non_integral_end_position_is_not_accepted_as_partial_page(self):
        scanner = _ScrollScanner([160, 0], selected_after=7)
        with patch('module.meowfficer.scan.scroll_offset', side_effect=[160, 0]):
            with self.assertRaises(RequestHumanTakeover):
                scanner._advance_verified_page()

    def test_zero_offset_with_changed_viewport_is_unknown_not_end(self):
        scanner = _ScrollScanner([146], selected_after=7)
        with patch('module.meowfficer.scan.scroll_offset', return_value=0):
            with self.assertRaises(RequestHumanTakeover):
                scanner._advance_verified_page()
        self.assertEqual(scanner.device.removed, [])

    def test_reverse_no_progress_is_not_accepted_as_bottom(self):
        scanner = _ScrollScanner([180, 180, 100, 0])
        with patch('module.meowfficer.scan.scroll_offset', side_effect=[180, 180, 100, 0]):
            with self.assertRaises(RequestHumanTakeover):
                scanner._advance_verified_page()
        self.assertEqual(len(scanner.device.swipes), 4)

    def test_unaligned_after_bounded_adjustments_stops_instead_of_skip(self):
        scanner = _ScrollScanner([50] * 8)
        with patch('module.meowfficer.scan.scroll_offset', return_value=50):
            with self.assertRaises(RequestHumanTakeover):
                scanner._advance_verified_page()
        self.assertEqual(len(scanner.device.swipes), 8)

    def test_lost_list_page_stops_before_first_swipe(self):
        scanner = _ScrollScanner([146])
        scanner.list_page = False
        with self.assertRaises(RequestHumanTakeover):
            scanner._advance_verified_page()
        self.assertEqual(scanner.device.swipes, [])

    def test_device_control_errors_propagate_without_clearing_protection(self):
        scanner = _ScrollScanner([146])
        error = GameStuckError('匿名设备手势失败')
        scanner.device.swipe_error = error
        with self.assertRaises(GameStuckError) as raised:
            scanner._advance_verified_page()
        self.assertIs(raised.exception, error)
        self.assertEqual(scanner.device.removed, [])


class SwipeResetTests(unittest.TestCase):
    def test_positive_top_scrollbar_needs_no_reset_gesture(self):
        scanner = _ScrollScanner(position=0)
        scanner._reset_swipe_cattery()
        self.assertEqual(scanner.device.swipes, [])
        self.assertGreaterEqual(scanner.device.screenshots, 3)

    def test_long_list_keeps_making_progress_until_top_is_positive(self):
        scanner = _ScrollScanner([-320] * 15, position=4500)
        scanner._reset_swipe_cattery()
        self.assertEqual(scanner.device.position, 0)
        self.assertEqual(len(scanner.device.swipes), 15)
        self.assertEqual(scanner.device.history, ['OTHER_BUTTON', 'SWIPE'])
        self.assertTrue(all(start[1] < end[1] for start, end, _, _ in scanner.device.swipes))

    def test_no_scrollbar_and_unchanged_confirmed_page_can_be_small_list(self):
        scanner = _ScrollScanner([0], scrollbar=False)
        scanner._reset_swipe_cattery()
        self.assertEqual(len(scanner.device.swipes), 1)
        self.assertEqual(scanner.device.removed, [])

    def test_unchanged_list_with_positive_non_top_scrollbar_must_stop(self):
        scanner = _ScrollScanner([0], position=900)
        with self.assertRaises(RequestHumanTakeover):
            scanner._reset_swipe_cattery()
        self.assertEqual(len(scanner.device.swipes), 1)
        self.assertEqual(scanner.device.removed, [])

    def test_reset_limit_stops_if_top_is_still_not_confirmed(self):
        scanner = _ScrollScanner([-1] * 40, position=2000)
        with self.assertRaises(RequestHumanTakeover):
            scanner._reset_swipe_cattery()
        self.assertEqual(len(scanner.device.swipes), 40)
        self.assertEqual(scanner.device.position, 1960)

    def test_unknown_page_never_uses_no_motion_as_top_confirmation(self):
        scanner = _ScrollScanner([0], scrollbar=False)
        scanner.list_page = False
        with self.assertRaises(RequestHumanTakeover):
            scanner._reset_swipe_cattery()
        self.assertEqual(scanner.device.swipes, [])


class OverlappingLastPageTests(unittest.TestCase):
    def test_forty_cats_partial_scroll_only_reads_new_last_row(self):
        cats = flow_fixtures._cats(40)
        scanner = flow_fixtures._FlowScanner([cats[0:12], cats[12:24], cats[24:36], cats[28:40]])
        rows = iter((3, 3, 1))

        def advance():
            scanner.events.append(('advance', scanner.page))
            scanner.page += 1
            return next(rows)

        with patch.object(scanner, '_advance_verified_page', side_effect=advance):
            result = flow_fixtures.SwipeFlowTests()._run(scanner, passes=4)
        counts = Counter(name for name, _, _ in result)
        self.assertEqual(len(result), 40)
        self.assertTrue(all(count == 1 for count in counts.values()))
        self.assertEqual([name for name, _, _ in result], [name for name, _ in cats])
        selected_last = [event[2] for event in scanner.events if event[0] == 'select' and event[1] == 3]
        self.assertEqual(selected_last, [8])

    def test_forty_four_cats_partial_scroll_only_reads_new_last_two_rows(self):
        cats = flow_fixtures._cats(44)
        scanner = flow_fixtures._FlowScanner([cats[0:12], cats[12:24], cats[24:36], cats[32:44]])
        rows = iter((3, 3, 2))

        def advance():
            scanner.events.append(('advance', scanner.page))
            scanner.page += 1
            return next(rows)

        with patch.object(scanner, '_advance_verified_page', side_effect=advance):
            result = flow_fixtures.SwipeFlowTests()._run(scanner, passes=4)
        self.assertEqual([name for name, _, _ in result], [name for name, _ in cats])
        selected_last = [event[2] for event in scanner.events if event[0] == 'select' and event[1] == 3]
        self.assertEqual(selected_last, [4])


if __name__ == '__main__':
    unittest.main()
