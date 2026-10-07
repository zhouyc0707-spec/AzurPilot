"""连续切猫继承滚动位置：当前稳定布局决定是否回顶，不缓存上一只猫的位置。"""

import unittest
from unittest.mock import Mock, patch

from module.exception import GameStuckError, RequestHumanTakeover
from module.meowfficer.scan import MeowfficerScanner
from module.meowfficer.scan_capture import IDENTITY_AREA, capture_current_cat
from module.meowfficer.scan_continuous import scan_continuous_detail
from tests.test_meowfficer_empty_tail import _EmptyOCR, _empty_frame
from tests.test_meowfficer_scan_capture import BOX
from tests.test_meowfficer_score_lock import DetailPage


def _gold(learned=3, level=30):
    return dict(name='限定蒂奇喵', level=level, learned=learned, rarity='SSR')


def _blue(level=30):
    return dict(name='蓝猫', level=level, learned=3, rarity='R')


class _InheritedDevice:
    """切猫保留 offset，垂直手势改变 offset；所有状态均发生在匿名内存帧。"""

    def __init__(self, cats, offset=0):
        self.cats = cats
        self.index = 0
        self.offset = offset
        self.swipes = []
        self.screenshots = 0
        self.page = DetailPage().frame()
        self.page_visible = True
        self.up_responds = True
        self.missing_middle = False
        self.unstable = False
        self.identity_changes = False
        self.swipe_error = None
        self.screenshot_error = None
        self.click = Mock(side_effect=AssertionError('读取天赋不应点击按钮'))
        self.click_record_remove = Mock()
        self.stuck_record_clear = Mock()
        self.image = self.frame()

    def frame(self):
        offset = self.offset
        if self.unstable:
            offset = 20 if self.screenshots % 2 else 0
        image = _empty_frame(learned=self.cats[self.index]['learned'], offset=offset)
        image[87:145, 775:945] = self.page[87:145, 775:945] if self.page_visible else 0
        if self.missing_middle:
            image[max(152, 255 - offset):342 - offset, 744:1244] = (231, 223, 222)
        if self.identity_changes and self.screenshots:
            x0, y0, x1, y1 = IDENTITY_AREA
            image[y0:y1, x0:x1] = 40
        return image

    def screenshot(self):
        self.screenshots += 1
        if self.screenshot_error is not None:
            raise self.screenshot_error
        self.image = self.frame()

    def swipe(self, start, end, duration):
        self.swipes.append((self.index, start, end, duration))
        if self.swipe_error is not None:
            raise self.swipe_error
        if start[1] > end[1]:
            self.offset = 75
        elif self.up_responds:
            self.offset = 0

    def next_cat(self):
        if self.index + 1 >= len(self.cats):
            raise AssertionError('专项上限结束后不得继续切猫')
        self.index += 1
        self.image = self.frame()
        cat = self.cats[self.index]
        return cat['name'], cat['level']


class _InheritedOCR(_EmptyOCR):
    """仅替代模型读数；完整行框、当前布局、实际位移和空栏证据均走真实识别。"""

    def __init__(self, device, **kwargs):
        self.device = device
        super().__init__(**kwargs)

    def det(self, image):
        if image.shape[:2] == (186, 345):
            return [(self.device.cats[self.device.index]['rarity'], BOX, 0.99)]
        return super().det(image)


class _InheritedScanner(MeowfficerScanner):
    def __init__(self, cats, *, offset=0, ocr_kwargs=None):
        self.device = _InheritedDevice(cats, offset)
        self.ocr = _InheritedOCR(self.device, **(ocr_kwargs or {}))
        self.scanned = []

    def _read_current_cat(self, ocr):
        cat = self.device.cats[self.device.index]
        return cat['name'], cat['level']

    def _load_ocr(self):
        return self.ocr

    def _ensure_cattery(self):
        raise AssertionError('当前位置专项不得进入猫窝')

    def _reset_swipe_cattery(self):
        raise AssertionError('当前位置专项不得复位猫窝')

    def _select_verified_card(self, *args, **kwargs):
        raise AssertionError('当前位置专项不得选猫窝卡片')

    def _open_talent(self):
        raise AssertionError('当前位置专项不得重新打开天赋页')


class ScrollInheritanceTests(unittest.TestCase):
    def setUp(self):
        cn = patch('module.config.server.server', 'cn')
        cn.start()
        self.addCleanup(cn.stop)
        diagnostics = patch('module.meowfficer.scan_diagnostics.save_incomplete_capture')
        self.diagnostics = diagnostics.start()
        self.addCleanup(diagnostics.stop)

    @staticmethod
    def ups(scanner, index=None):
        return [event for event in scanner.device.swipes
                if event[1][1] < event[2][1] and (index is None or event[0] == index)]

    @staticmethod
    def downs(scanner, index=None):
        return [event for event in scanner.device.swipes
                if event[1][1] > event[2][1] and (index is None or event[0] == index)]

    def capture(self, scanner):
        name, level = scanner._read_current_cat(scanner.ocr)
        return capture_current_cat(scanner, scanner.ocr, name, level, reset_history=False)

    def assert_complete(self, capture):
        self.assertTrue(capture.identity_confirmed, capture.reasons)
        self.assertTrue(capture.talents_complete, capture.reasons)
        self.assertTrue(capture.complete, capture.reasons)

    def continuous(self, scanner, on_cat=None):
        with patch('module.meowfficer.scan_continuous.swipe_next_cat',
                   side_effect=lambda *_args, **_kwargs: scanner.device.next_cat()) as following, \
                patch('module.meowfficer.scan_continuous.read_roster_count',
                      side_effect=AssertionError('不能读取猫窝总数')), \
                patch('module.meowfficer.scan_continuous.lock_independent_sort',
                      side_effect=AssertionError('不能读取猫窝排序')):
            result = scan_continuous_detail(scanner, scanner.ocr, limit=len(scanner.device.cats),
                                            start_current=True, on_cat=on_cat)
        self.assertEqual(following.call_count, len(scanner.device.cats) - 1)
        scanner.device.click.assert_not_called()
        return result

    def test_first_cat_already_at_confirmed_top_reads_three_talents_and_empty_without_scroll(self):
        scanner = _InheritedScanner([_gold()])
        capture = self.capture(scanner)
        self.assert_complete(capture)
        self.assertEqual(len(capture.talents), 3)
        self.assertEqual(scanner.device.swipes, [])
        self.assertGreaterEqual(scanner.device.screenshots, 2)

    def test_first_cat_at_bottom_needs_exactly_one_top_gesture(self):
        scanner = _InheritedScanner([_gold()], offset=75)
        capture = self.capture(scanner)
        self.assert_complete(capture)
        self.assertEqual(len(self.ups(scanner)), 1)
        self.assertEqual(self.downs(scanner), [])

    def test_three_talent_cats_inherit_top_and_continuously_read_with_no_vertical_gestures(self):
        scanner = _InheritedScanner([_gold(level=30), _gold(level=29), _gold(level=28)])
        rows = self.continuous(scanner)
        self.assertEqual(len(rows), 3)
        self.assertEqual([len(row[1]) for row in rows], [3, 3, 3])
        self.assertEqual(scanner.device.swipes, [])

    def test_cat_that_scrolled_down_makes_next_three_talent_cat_return_top_once(self):
        scanner = _InheritedScanner([_gold(learned=5), _gold(level=29)])
        rows = self.continuous(scanner)
        self.assertEqual([len(row[1]) for row in rows], [5, 3])
        self.assertEqual(self.ups(scanner, 0), [])
        self.assertEqual(len(self.downs(scanner, 0)), 1)
        self.assertEqual(len(self.ups(scanner, 1)), 1)
        self.assertEqual(self.downs(scanner, 1), [])

    def test_first_bottom_cat_resets_once_then_next_top_cat_keeps_zero_extra_gestures(self):
        scanner = _InheritedScanner([_gold(), _gold(level=29)], offset=75)
        self.continuous(scanner)
        self.assertEqual(len(self.ups(scanner, 0)), 1)
        self.assertEqual(self.ups(scanner, 1), [])
        self.assertEqual(self.downs(scanner), [])

    def test_blue_chain_keeps_known_top_without_reading_talents_or_adding_scroll(self):
        scanner = _InheritedScanner([_gold(), _blue(), _blue(level=29), _gold(level=28)])
        rows = self.continuous(scanner)
        self.assertEqual([len(row[1]) for row in rows], [3, 0, 0, 3])
        self.assertEqual(scanner.device.swipes, [])

    def test_blue_chain_after_down_scroll_does_not_claim_gold_panel_is_top(self):
        scanner = _InheritedScanner([_gold(learned=5), _blue(), _blue(level=29), _gold(level=28)])
        rows = self.continuous(scanner)
        self.assertEqual([len(row[1]) for row in rows], [5, 0, 0, 3])
        self.assertEqual(len(self.downs(scanner, 0)), 1)
        self.assertEqual(self.ups(scanner, 1), [])
        self.assertEqual(self.ups(scanner, 2), [])
        self.assertEqual(len(self.ups(scanner, 3)), 1)

    def test_first_blue_at_unknown_bottom_does_not_create_cached_top_for_next_gold(self):
        scanner = _InheritedScanner([_blue(), _gold(level=29)], offset=75)
        rows = self.continuous(scanner)
        self.assertEqual([len(row[1]) for row in rows], [0, 3])
        self.assertEqual(self.ups(scanner, 0), [])
        self.assertEqual(len(self.ups(scanner, 1)), 1)

    def test_callback_recovery_changed_position_does_not_reuse_previous_top_evidence(self):
        scanner = _InheritedScanner([_gold(), _gold(level=29)])
        callbacks = []

        def callback(active, capture):
            callbacks.append(capture)
            if len(callbacks) == 1:
                # 模拟恢复流程读取完后停在下方，不把上一只的顶部当跨猫缓存。
                active.device.offset = 75
                active.device.image = active.device.frame()

        self.continuous(scanner, on_cat=callback)
        self.assertEqual(len(callbacks), 2)
        self.assertEqual(self.ups(scanner, 0), [])
        self.assertEqual(len(self.ups(scanner, 1)), 1)

    def test_saved_top_image_cannot_override_fresh_actual_bottom_layout(self):
        scanner = _InheritedScanner([_gold()])
        scanner.device.offset = 75
        capture = self.capture(scanner)
        self.assert_complete(capture)
        self.assertEqual(len(self.ups(scanner)), 1)

    def test_new_capture_after_position_change_rechecks_layout_without_cross_call_cache(self):
        scanner = _InheritedScanner([_gold()])
        self.assert_complete(self.capture(scanner))
        self.assertEqual(scanner.device.swipes, [])
        scanner.device.offset = 75
        scanner.device.image = scanner.device.frame()
        self.assert_complete(self.capture(scanner))
        self.assertEqual(len(self.ups(scanner)), 1)

    def test_missing_row_geometry_cannot_skip_top_fallback_or_protective_incomplete_result(self):
        scanner = _InheritedScanner([_gold()])
        scanner.device.missing_middle = True
        scanner.device.image = scanner.device.frame()
        capture = self.capture(scanner)
        self.assertFalse(capture.complete)
        self.assertFalse(capture.talents_complete)
        self.assertGreaterEqual(len(self.ups(scanner)), 2)
        self.assertTrue(capture.reasons)

    def test_non_cn_layout_uses_original_top_confirmation_even_if_anonymous_frame_is_top(self):
        scanner = _InheritedScanner([_gold()])
        with patch('module.config.server.server', 'en'), \
                patch('module.meowfficer.score_lock.detail_page_confirmed', return_value=True):
            capture = self.capture(scanner)
        self.assert_complete(capture)
        self.assertEqual(len(self.ups(scanner)), 2)

    def test_unstable_panel_never_uses_fast_top_proof(self):
        scanner = _InheritedScanner([_gold()])
        scanner.device.unstable = True
        scanner.device.image = scanner.device.frame()
        capture = self.capture(scanner)
        self.assertFalse(capture.complete)
        self.assertFalse(capture.talents_complete)
        self.assertIn('天赋面板未稳定', capture.reasons)
        self.assertGreater(len(self.ups(scanner)), 0)

    def test_unknown_empty_label_does_not_skip_required_bottom_checks_or_allow_complete(self):
        scanner = _InheritedScanner([_gold()], ocr_kwargs={'confidence': 0.89})
        capture = self.capture(scanner)
        self.assertFalse(capture.complete)
        self.assertFalse(capture.talents_complete)
        self.assertEqual(self.ups(scanner), [])
        self.assertGreater(len(self.downs(scanner)), 0)

    def test_identity_changed_during_stable_top_observation_stops_before_any_swipe(self):
        scanner = _InheritedScanner([_gold()])
        scanner.device.identity_changes = True
        with self.assertRaises(RequestHumanTakeover):
            self.capture(scanner)
        self.assertEqual(scanner.device.swipes, [])

    def test_unknown_initial_page_stops_before_talent_read_or_vertical_gesture(self):
        scanner = _InheritedScanner([_gold()])
        scanner.device.page_visible = False
        scanner.device.image = scanner.device.frame()
        with self.assertRaises(RequestHumanTakeover):
            self.capture(scanner)
        self.assertEqual(scanner.device.swipes, [])

    def test_device_swipe_error_from_required_top_reset_propagates_without_retry(self):
        scanner = _InheritedScanner([_gold()], offset=75)
        error = GameStuckError('匿名回顶手势错误')
        scanner.device.swipe_error = error
        with self.assertRaises(GameStuckError) as raised:
            self.capture(scanner)
        self.assertIs(raised.exception, error)
        self.assertEqual(len(self.ups(scanner)), 1)

    def test_screenshot_error_during_top_observation_propagates_without_guessing_position(self):
        scanner = _InheritedScanner([_gold()])
        error = GameStuckError('匿名顶部截图错误')
        scanner.device.screenshot_error = error
        with self.assertRaises(GameStuckError) as raised:
            self.capture(scanner)
        self.assertIs(raised.exception, error)
        self.assertEqual(scanner.device.swipes, [])


if __name__ == '__main__':
    unittest.main()
