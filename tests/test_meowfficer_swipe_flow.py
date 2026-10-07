"""天赋页连续切猫的编排及猫窝位置核验回归，不连接游戏设备。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from module.exception import RequestHumanTakeover
from module.meowfficer.scan import MeowfficerScanner


EMPTY = object()


def _cats(count, prefix='指挥喵', start=1):
    return [(f'{prefix}{index}', index) for index in range(start, start + count)]


class _FlowDevice:
    """画面只包含当前列表页编号，用于检查两屏的读取范围。"""

    def __init__(self, scanner):
        self.scanner = scanner
        self.image = np.zeros((720, 1280, 3), dtype=np.uint8)

    def screenshot(self):
        self.image[0, 0, 0] = self.scanner.page


class _FlowScanner(MeowfficerScanner):
    """所有游戏操作都替换为事件记录，保留实际快速遍历编排。"""

    def __init__(self, pages, talents=None):
        self.pages = pages
        self.page = 0
        self.index = None
        self.in_detail = False
        self.scanned = []
        self.events = []
        self.device = _FlowDevice(self)
        self.talents = talents or {}
        self.expected_overrides = {}
        self.next_outputs = []
        self.return_error = None
        self.open_ok = True
        self.confirm_error = None
        self.list_ok = True
        self.advance_rows = []

    def appear(self, button, **kwargs):
        return self.list_ok and not self.in_detail

    def _select_verified_card(self, index, ocr, expected):
        if self.in_detail:
            raise AssertionError('在天赋页选择了猫窝卡片')
        self.index = index
        actual = self.pages[self.page][index]
        if actual is EMPTY:
            raise AssertionError('选择了已确认的空格')
        self.events.append(('select', self.page, index, expected))
        return actual

    def _open_talent(self):
        self.events.append(('open', self.page, self.index))
        if self.open_ok:
            self.in_detail = True
        return self.open_ok

    def _confirm_talent_identity(self, ocr, expected):
        self.events.append(('confirm', self.page, self.index, expected))
        if self.confirm_error is not None:
            raise self.confirm_error

    def _return_verified_list(self, before, index):
        self.events.append(('return', self.page, index))
        if self.return_error is not None:
            raise self.return_error
        self.in_detail = False

    def _advance_verified_page(self):
        if self.in_detail:
            raise AssertionError('在天赋页滚动了猫窝列表')
        self.events.append(('advance', self.page))
        if self.page >= len(self.pages) - 1:
            return 0
        self.page += 1
        return self.advance_rows.pop(0) if self.advance_rows else 3

    def _read_talents(self, ocr):
        self.events.append(('read', self.page, self.index))
        return self.talents.get((self.page, self.index), ['已识别天赋'])

    def read_card(self, image, index, ocr):
        page = int(image[0, 0, 0])
        if (page, index) in self.expected_overrides:
            return self.expected_overrides[page, index]
        if index >= len(self.pages[page]) or self.pages[page][index] is EMPTY:
            return None
        return self.pages[page][index]

    def empty_card(self, image, index):
        page = int(image[0, 0, 0])
        return index >= len(self.pages[page]) or self.pages[page][index] is EMPTY

    def next_cat(self, scanner, ocr, current_name, current_level):
        if scanner is not self or not self.in_detail:
            raise AssertionError('左滑没有发生在当前猫的天赋页')
        self.events.append(('next', self.page, self.index))
        if self.next_outputs:
            result = self.next_outputs.pop(0)
            if result is None:
                return None
        else:
            result = self.pages[self.page][self.index + 1]
        self.index += 1
        return result

    def capture(self, scanner, ocr, name, level):
        if scanner is not self or not self.in_detail:
            raise AssertionError('捕获没有发生在天赋页')
        self.events.append(('capture', self.page, self.index, name, level))
        return SimpleNamespace(display_name=name, level=level,
                               talents=self.talents.get((self.page, self.index), ['已识别天赋']))


class SwipeFlowTests(unittest.TestCase):
    """减少返回次数，但锁操作、同名猫及不可证明的切换仍按位置核验。"""

    def _run(self, scanner, limit=0, passes=12, strict=True, changed=()):
        def on_cat(current, capture):
            current.events.append(('callback', current.page, current.index,
                                   capture.display_name, capture.level))
            return {'status': 'changed' if (current.page, current.index) in changed else 'unchanged'}

        with patch('module.meowfficer.scan_list.read_card_identity', side_effect=scanner.read_card), \
                patch('module.meowfficer.scan_list.card_is_empty', side_effect=scanner.empty_card), \
                patch('module.meowfficer.scan_next.swipe_next_cat', side_effect=scanner.next_cat), \
                patch('module.meowfficer.scan_capture.capture_current_cat', side_effect=scanner.capture):
            return scanner._scan_by_swipe(object(), limit, passes, on_cat if strict else None)

    @staticmethod
    def _events(scanner, kind):
        return [event for event in scanner.events if event[0] == kind]

    def test_twelve_distinct_neighbors_only_open_and_return_once(self):
        cats = _cats(12)
        scanner = _FlowScanner([cats])
        self.assertEqual([item[0] for item in self._run(scanner)], [item[0] for item in cats])
        self.assertEqual(len(self._events(scanner, 'open')), 1)
        self.assertEqual(self._events(scanner, 'return'), [('return', 0, 11)])
        self.assertEqual(len(self._events(scanner, 'next')), 11)
        self.assertEqual(len(self._events(scanner, 'callback')), 12)

    def test_same_name_same_level_uses_both_positions_without_dedup_or_swipe(self):
        scanner = _FlowScanner([[('林德喵', 5), ('林德喵', 5)]],
                               talents={(0, 0): ['第一只天赋'], (0, 1): ['第二只天赋']})
        result = self._run(scanner)
        self.assertEqual(result, [('林德喵', ['第一只天赋'], 5), ('林德喵', ['第二只天赋'], 5)])
        self.assertEqual(self._events(scanner, 'next'), [])
        self.assertEqual(len(self._events(scanner, 'open')), 2)
        self.assertEqual([event[2] for event in self._events(scanner, 'select')], [0, 1])

    def test_same_name_different_levels_keeps_fast_path(self):
        scanner = _FlowScanner([[('林德喵', 5), ('林德喵', 9)]])
        self.assertEqual([item[2] for item in self._run(scanner)], [5, 9])
        self.assertEqual(len(self._events(scanner, 'next')), 1)
        self.assertEqual(len(self._events(scanner, 'open')), 1)

    def test_different_name_with_unknown_expected_level_still_uses_fast_path(self):
        scanner = _FlowScanner([[('林德喵', 5), ('埃弗喵', 9)]])
        scanner.expected_overrides[0, 1] = ('埃弗喵', None)
        self.assertEqual([item[2] for item in self._run(scanner)], [5, 9])
        self.assertEqual(len(self._events(scanner, 'next')), 1)
        self.assertEqual(len(self._events(scanner, 'open')), 1)

    def test_same_name_with_unknown_expected_or_current_level_uses_list(self):
        for unknown in ('expected', 'current'):
            with self.subTest(unknown=unknown):
                first = ('林德喵', None if unknown == 'current' else 5)
                scanner = _FlowScanner([[first, ('林德喵', 9)]])
                if unknown == 'expected':
                    scanner.expected_overrides[0, 1] = ('林德喵', None)
                self.assertEqual(len(self._run(scanner)), 2)
                self.assertEqual(self._events(scanner, 'next'), [])
                self.assertEqual(len(self._events(scanner, 'open')), 2)

    def test_changed_name_with_wrong_known_expected_level_stops(self):
        scanner = _FlowScanner([[('林德喵', 5), ('埃弗喵', 9)]])
        scanner.next_outputs = [('埃弗喵', 8)]
        with self.assertRaises(RequestHumanTakeover):
            self._run(scanner)
        self.assertEqual(len(scanner.scanned), 1)
        self.assertEqual(len(self._events(scanner, 'callback')), 1)

    def test_changed_lock_returns_before_the_next_card_and_never_swipes(self):
        scanner = _FlowScanner([_cats(2)])
        self._run(scanner, changed={(0, 0)})
        kinds = [event[0] for event in scanner.events]
        first_callback = kinds.index('callback')
        self.assertEqual(kinds[first_callback + 1:first_callback + 3], ['return', 'select'])
        self.assertEqual(self._events(scanner, 'next'), [])
        self.assertEqual(len(self._events(scanner, 'open')), 2)

    def test_blue_cat_without_talents_still_counts_towards_strict_limit(self):
        scanner = _FlowScanner([_cats(2)], talents={(0, 0): []})
        result = self._run(scanner, limit=1)
        self.assertEqual(result, [(scanner.pages[0][0][0], [], scanner.pages[0][0][1])])
        self.assertEqual(len(self._events(scanner, 'callback')), 1)
        self.assertEqual(self._events(scanner, 'next'), [])
        self.assertEqual(self._events(scanner, 'return'), [('return', 0, 0)])

    def test_unconfirmed_swipe_returns_to_next_position_without_duplicate_data(self):
        scanner = _FlowScanner([_cats(2)])
        scanner.next_outputs = [None]
        result = self._run(scanner)
        self.assertEqual(len(result), 2)
        self.assertEqual([item[0] for item in result], [item[0] for item in scanner.pages[0]])
        self.assertEqual(self._events(scanner, 'next'), [('next', 0, 0)])
        self.assertEqual([event[2] for event in self._events(scanner, 'select')], [0, 1])
        self.assertEqual(self._events(scanner, 'return'), [('return', 0, 0), ('return', 0, 1)])

    def test_unexpected_next_identity_stops_before_scoring_wrong_cat(self):
        scanner = _FlowScanner([_cats(3)])
        scanner.next_outputs = [('意外指挥喵', 30)]
        with self.assertRaises(RequestHumanTakeover):
            self._run(scanner)
        self.assertEqual(len(scanner.scanned), 1)
        self.assertEqual(len(self._events(scanner, 'callback')), 1)
        self.assertEqual(self._events(scanner, 'return'), [])

    def test_return_order_failure_keeps_scanned_result_and_stops_later_callbacks(self):
        scanner = _FlowScanner([_cats(3)])
        scanner.return_error = RequestHumanTakeover('列表重排')
        with self.assertRaises(RequestHumanTakeover) as raised:
            self._run(scanner, changed={(0, 0)})
        self.assertIs(raised.exception, scanner.return_error)
        self.assertEqual(len(scanner.scanned), 1)
        self.assertEqual(len(self._events(scanner, 'callback')), 1)
        self.assertEqual(self._events(scanner, 'next'), [])

    def test_one_pass_does_not_scroll_to_a_second_screen(self):
        scanner = _FlowScanner([_cats(12), _cats(12, start=13)])
        self.assertEqual(len(self._run(scanner, passes=1)), 12)
        self.assertEqual(self._events(scanner, 'advance'), [])

    def test_two_passes_retain_twelve_cards_per_screen_and_one_list_scroll(self):
        scanner = _FlowScanner([_cats(12), _cats(12, start=13), _cats(2, start=25)])
        result = self._run(scanner, passes=2)
        self.assertEqual(len(result), 24)
        self.assertEqual(self._events(scanner, 'advance'), [('advance', 0)])
        self.assertEqual(len(self._events(scanner, 'open')), 2)
        self.assertEqual(len(self._events(scanner, 'return')), 2)

    def test_second_partial_screen_does_not_repeat_last_cat_or_scroll_again(self):
        scanner = _FlowScanner([_cats(12), _cats(3, start=13)])
        result = self._run(scanner)
        self.assertEqual(len(result), 15)
        self.assertEqual([item[0] for item in result], [item[0] for item in _cats(15)])
        self.assertEqual(self._events(scanner, 'advance'), [('advance', 0)])
        self.assertEqual(self._events(scanner, 'return')[-1], ('return', 1, 2))

    def test_last_viewport_overlap_only_reads_new_rows_after_previous_last_cat(self):
        for rows in (1, 2):
            with self.subTest(rows=rows):
                first = _cats(12)
                overlap = first[rows * 4:]
                new = _cats(rows * 4, start=13)
                scanner = _FlowScanner([first, overlap + new])
                scanner.advance_rows = [rows]
                result = self._run(scanner)
                expected = first + new
                self.assertEqual([item[0] for item in result], [item[0] for item in expected])
                self.assertEqual(len(result), 12 + rows * 4)
                selections = self._events(scanner, 'select')
                self.assertEqual(selections[1][2], 12 - rows * 4)
                self.assertEqual(self._events(scanner, 'advance'), [('advance', 0)])

    def test_empty_tail_is_not_selected_and_last_cat_is_not_repeated(self):
        scanner = _FlowScanner([_cats(3)])
        self.assertEqual(len(self._run(scanner)), 3)
        self.assertEqual(len(self._events(scanner, 'next')), 2)
        self.assertEqual(len(self._events(scanner, 'callback')), 3)
        self.assertEqual(self._events(scanner, 'advance'), [])

    def test_entire_empty_screen_finishes_without_opening_talents(self):
        scanner = _FlowScanner([[]])
        self.assertEqual(self._run(scanner), [])
        self.assertEqual(scanner.events, [])

    def test_gap_between_occupied_cards_requires_position_selection(self):
        scanner = _FlowScanner([[('林德喵', 5), EMPTY, ('埃弗喵', 8)]])
        self.assertEqual(len(self._run(scanner)), 2)
        self.assertEqual(self._events(scanner, 'next'), [])
        self.assertEqual([event[2] for event in self._events(scanner, 'select')], [0, 2])

    def test_unknown_next_list_identity_falls_back_to_verified_selection(self):
        scanner = _FlowScanner([_cats(2)])
        scanner.expected_overrides[0, 1] = None
        self.assertEqual(len(self._run(scanner)), 2)
        self.assertEqual(self._events(scanner, 'next'), [])
        self.assertIsNone(self._events(scanner, 'select')[1][3])

    def test_loose_empty_talents_do_not_count_but_visits_remain_bounded_by_passes(self):
        pages = [_cats(12), _cats(12, start=13), _cats(12, start=25)]
        talents = {(page, index): [] for page in range(3) for index in range(12)}
        scanner = _FlowScanner(pages, talents=talents)
        self.assertEqual(self._run(scanner, strict=False, limit=1, passes=2), [])
        self.assertEqual(len(self._events(scanner, 'read')), 24)
        self.assertEqual(self._events(scanner, 'advance'), [('advance', 0)])

    def test_loose_limit_counts_only_cats_with_talents(self):
        scanner = _FlowScanner([_cats(4)], talents={(0, 0): []})
        result = self._run(scanner, strict=False, limit=2)
        self.assertEqual([item[0] for item in result], [item[0] for item in _cats(2, start=2)])
        self.assertEqual(len(self._events(scanner, 'read')), 3)
        self.assertEqual(self._events(scanner, 'return'), [('return', 0, 2)])

    def test_zero_passes_do_not_visit_any_card(self):
        scanner = _FlowScanner([_cats(2)])
        self.assertEqual(self._run(scanner, passes=0), [])
        self.assertEqual(scanner.events, [])

    def test_cn_scan_initialization_uses_verified_top_reset(self):
        scanner = _FlowScanner([_cats(2)])
        with patch.object(scanner, '_load_ocr', return_value=object()), \
                patch.object(scanner, '_ensure_cattery'), \
                patch.object(scanner, '_supports_talent_swipe', return_value=True), \
                patch.object(scanner, '_reset_swipe_cattery') as reset_top, \
                patch.object(scanner, '_reset_cattery_scroll') as legacy_reset, \
                patch.object(scanner.device, 'stuck_record_clear', create=True), \
                patch.object(scanner, '_scan_by_swipe', return_value=[]) as fast_scan:
            self.assertEqual(scanner.scan_all(), [])
        reset_top.assert_called_once_with()
        legacy_reset.assert_not_called()
        fast_scan.assert_called_once()

    def test_unknown_list_page_stops_before_card_selection(self):
        scanner = _FlowScanner([_cats(2)])
        scanner.list_ok = False
        with self.assertRaises(RequestHumanTakeover):
            self._run(scanner)
        self.assertEqual(scanner.events, [])

    def test_open_or_identity_confirmation_failure_prevents_callbacks(self):
        for failure in ('open', 'identity'):
            with self.subTest(failure=failure):
                scanner = _FlowScanner([_cats(2)])
                if failure == 'open':
                    scanner.open_ok = False
                else:
                    scanner.confirm_error = RequestHumanTakeover('天赋身份未知')
                with self.assertRaises(RequestHumanTakeover):
                    self._run(scanner)
                self.assertEqual(self._events(scanner, 'callback'), [])
                self.assertEqual(scanner.scanned, [])


class _HelperDevice:
    """按帧更新选中标记和身份，用于实际位置核验方法。"""

    def __init__(self, scanner, frames):
        self.scanner = scanner
        self.frames = list(frames)
        self.image = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.clicks = []
        self.screenshots = 0
        self.clears = 0

    def click(self, button):
        self.clicks.append(button)

    def screenshot(self):
        self.screenshots += 1
        if self.frames:
            self.scanner.marker, self.scanner.identity, self.scanner.page_ok = self.frames.pop(0)

    def stuck_record_clear(self):
        self.clears += 1


class _HelperScanner(MeowfficerScanner):
    def __init__(self, frames, back_ok=True):
        self.marker = 0
        self.identity = ('林德喵', 5)
        self.page_ok = True
        self.device = _HelperDevice(self, frames)
        self._back_to_cattery = Mock(return_value=back_ok)
        self.reads = []

    def appear(self, button, **kwargs):
        return self.page_ok

    def _read_current_cat(self, ocr):
        self.reads.append(self.identity)
        return self.identity


class VerifiedCardHelpersTests(unittest.TestCase):
    """姓名相同也必须有目标格选择标记，返回后必须核验整个视口顺序。"""

    def test_select_waits_for_target_marker_and_stable_expected_identity(self):
        identity = ('林德喵', 5)
        scanner = _HelperScanner([(0, identity, True)] * 3 + [(2, identity, True)] * 2)
        with patch('module.meowfficer.scan_list.selected_card', side_effect=lambda image: scanner.marker):
            result = scanner._select_verified_card(2, object(), identity)
        self.assertEqual(result, identity)
        self.assertEqual(scanner.reads, [identity, identity])
        self.assertEqual(len(scanner.device.clicks), 1)
        self.assertEqual(scanner.device.clears, 1)

    def test_stable_name_without_target_marker_cannot_confirm_selection(self):
        scanner = _HelperScanner([(0, ('林德喵', 5), True)] * 16)
        with patch('module.meowfficer.scan_list.selected_card', side_effect=lambda image: scanner.marker):
            with self.assertRaises(RequestHumanTakeover):
                scanner._select_verified_card(2, object(), ('林德喵', 5))
        self.assertEqual(len(scanner.device.clicks), 2)
        self.assertEqual(scanner.reads, [])
        self.assertEqual(scanner.device.clears, 0)

    def test_target_marker_with_wrong_expected_identity_stops(self):
        scanner = _HelperScanner([(2, ('埃弗喵', 8), True)] * 16)
        with patch('module.meowfficer.scan_list.selected_card', side_effect=lambda image: scanner.marker):
            with self.assertRaises(RequestHumanTakeover):
                scanner._select_verified_card(2, object(), ('林德喵', 5))
        self.assertEqual(scanner.device.clears, 0)

    def test_target_marker_matches_known_name_when_expected_level_is_unknown(self):
        identity = ('林德喵', 9)
        scanner = _HelperScanner([(2, identity, True)] * 2)
        with patch('module.meowfficer.scan_list.selected_card', side_effect=lambda image: scanner.marker):
            self.assertEqual(scanner._select_verified_card(2, object(), ('林德喵', None)), identity)
        self.assertEqual(scanner.device.clears, 1)

    def test_known_expected_level_mismatch_stops_despite_matching_name(self):
        scanner = _HelperScanner([(2, ('林德喵', 9), True)] * 16)
        with patch('module.meowfficer.scan_list.selected_card', side_effect=lambda image: scanner.marker):
            with self.assertRaises(RequestHumanTakeover):
                scanner._select_verified_card(2, object(), ('林德喵', 5))
        self.assertEqual(scanner.device.clears, 0)

    def test_selection_without_list_page_does_not_click(self):
        scanner = _HelperScanner([])
        scanner.page_ok = False
        with self.assertRaises(RequestHumanTakeover):
            scanner._select_verified_card(2, object(), None)
        self.assertEqual(scanner.device.clicks, [])

    def test_return_requires_two_selected_marker_and_order_confirmations(self):
        scanner = _HelperScanner([(2, ('林德喵', 5), True)] * 2)
        before = scanner.device.image.copy()
        with patch('module.meowfficer.scan_list.selected_card', side_effect=lambda image: scanner.marker), \
                patch('module.meowfficer.scan_list.cattery_order_unchanged', return_value=True) as order:
            scanner._return_verified_list(before, 2)
        self.assertEqual(scanner.device.screenshots, 2)
        self.assertEqual(order.call_count, 2)
        self.assertTrue(all(call.args[0] is before for call in order.call_args_list))

    def test_return_transient_missing_selection_ring_recovers_without_game_actions(self):
        identity = ('林德喵', 5)
        scanner = _HelperScanner([(None, identity, True)] + [(2, identity, True)] * 2)
        with patch('module.meowfficer.scan_list.selected_card', side_effect=lambda image: scanner.marker), \
                patch('module.meowfficer.scan_list.cattery_order_unchanged', return_value=True):
            scanner._return_verified_list(scanner.device.image.copy(), 2)
        self.assertEqual(scanner.device.screenshots, 3)
        self.assertEqual(scanner.device.clicks, [])
        self.assertEqual(scanner.device.clears, 0)

    def test_return_transient_page_transition_recovers(self):
        identity = ('林德喵', 5)
        scanner = _HelperScanner([(2, identity, False)] + [(2, identity, True)] * 2)
        with patch('module.meowfficer.scan_list.selected_card', side_effect=lambda image: scanner.marker), \
                patch('module.meowfficer.scan_list.cattery_order_unchanged', return_value=True):
            scanner._return_verified_list(scanner.device.image.copy(), 2)
        self.assertEqual(scanner.device.screenshots, 3)
        self.assertEqual(scanner.device.clicks, [])

    def test_return_one_frame_order_comparison_failure_can_recover(self):
        scanner = _HelperScanner([(2, ('林德喵', 5), True)] * 3)
        with patch('module.meowfficer.scan_list.selected_card', side_effect=lambda image: scanner.marker), \
                patch('module.meowfficer.scan_list.cattery_order_unchanged',
                      side_effect=[False, True, True]):
            scanner._return_verified_list(scanner.device.image.copy(), 2)
        self.assertEqual(scanner.device.screenshots, 3)
        self.assertEqual(scanner.device.clicks, [])

    def test_return_valid_confirmations_separated_by_unknown_frame_are_not_consecutive(self):
        identity = ('林德喵', 5)
        for interruption in ('position', 'page', 'order'):
            with self.subTest(interruption=interruption):
                frames = [(2, identity, True),
                          (None if interruption == 'position' else 2, identity, interruption != 'page'),
                          (2, identity, True), (2, identity, True)]
                scanner = _HelperScanner(frames)
                with patch('module.meowfficer.scan_list.selected_card',
                           side_effect=lambda image: scanner.marker), \
                        patch('module.meowfficer.scan_list.cattery_order_unchanged',
                              side_effect=[True, False, True, True] if interruption == 'order' else None,
                              return_value=True):
                    scanner._return_verified_list(scanner.device.image.copy(), 2)
                self.assertEqual(scanner.device.screenshots, 4)
                self.assertEqual(scanner.device.clicks, [])

    def test_return_one_valid_confirmation_at_budget_end_still_stops(self):
        identity = ('林德喵', 5)
        scanner = _HelperScanner([(None, identity, True)] * 11 + [(2, identity, True)])
        with patch('module.meowfficer.scan_list.selected_card', side_effect=lambda image: scanner.marker), \
                patch('module.meowfficer.scan_list.cattery_order_unchanged', return_value=True):
            with self.assertRaises(RequestHumanTakeover):
                scanner._return_verified_list(scanner.device.image.copy(), 2)
        self.assertEqual(scanner.device.screenshots, 12)
        self.assertEqual(scanner.device.clicks, [])

    def test_return_persistent_order_position_or_page_failure_is_bounded_and_explained(self):
        for failure in ('order', 'position', 'page'):
            with self.subTest(failure=failure):
                frames = [(0 if failure == 'position' else 2, ('林德喵', 5), failure != 'page')] * 12
                scanner = _HelperScanner(frames)
                with patch('module.meowfficer.scan_list.selected_card',
                           side_effect=lambda image: scanner.marker), \
                        patch('module.meowfficer.scan_list.cattery_order_unchanged',
                              return_value=failure != 'order'):
                    with self.assertRaises(RequestHumanTakeover) as raised:
                        scanner._return_verified_list(scanner.device.image.copy(), 2)
                self.assertEqual(scanner.device.screenshots, 12)
                self.assertEqual(scanner.device.clicks, [])
                reason = str(raised.exception)
                if failure == 'page':
                    self.assertRegex(reason, r'猫窝列表(?:页面|页)')
                    self.assertTrue('未确认' in reason or '无法确认' in reason
                                    or '未能正向确认' in reason, reason)
                elif failure == 'position':
                    self.assertIn('选中', reason)
                    self.assertRegex(reason, r'实际.*(?:第\s*)?1')
                    self.assertRegex(reason, r'预期.*(?:第\s*)?3')
                else:
                    self.assertTrue('顺序' in reason or '视口' in reason, reason)

    def test_return_persistent_unknown_selection_reports_unknown_actual_position(self):
        scanner = _HelperScanner([(None, ('林德喵', 5), True)] * 12)
        with patch('module.meowfficer.scan_list.selected_card', side_effect=lambda image: scanner.marker), \
                patch('module.meowfficer.scan_list.cattery_order_unchanged', return_value=True):
            with self.assertRaises(RequestHumanTakeover) as raised:
                scanner._return_verified_list(scanner.device.image.copy(), 2)
        self.assertEqual(scanner.device.screenshots, 12)
        reason = str(raised.exception)
        self.assertIn('选中', reason)
        self.assertTrue('未知' in reason or '未确认' in reason, reason)
        self.assertRegex(reason, r'预期.*(?:第\s*)?3')

    def test_failed_return_never_checks_or_assumes_list_order(self):
        scanner = _HelperScanner([], back_ok=False)
        with patch('module.meowfficer.scan_list.cattery_order_unchanged') as order:
            with self.assertRaises(RequestHumanTakeover):
                scanner._return_verified_list(scanner.device.image.copy(), 2)
        self.assertEqual(scanner.device.screenshots, 0)
        order.assert_not_called()


if __name__ == '__main__':
    unittest.main()
