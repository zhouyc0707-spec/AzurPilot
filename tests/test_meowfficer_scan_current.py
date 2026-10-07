"""从当前天赋页连续读取：真实页标题和匿名识别序列，不连接游戏。"""

import unittest
from unittest.mock import Mock, patch

from module.exception import GameStuckError, RequestHumanTakeover
from module.meowfficer.scan import MeowfficerScanner
from module.meowfficer.scan_continuous import scan_continuous_detail
from tests.test_meowfficer_scan_continuous import (_ContinuousScanner, _FlowHarness,
                                                _MemoryDevice, _capture, _different_cats)
from tests.test_meowfficer_score_lock import DetailPage


class _CurrentDevice(_MemoryDevice):
    """实际页标题来自局部模板，每次只生成当前匿名猫的内存帧。"""

    def __init__(self, scanner):
        super().__init__(scanner)
        self.page = DetailPage().frame()
        self.image = self.frame()

    def frame(self):
        image = self.page.copy()
        if not self.scanner.detail_page:
            image[87:145, 775:945] = 0
        image[0, 0, :2] = self.scanner.index % 256, self.scanner.index // 256
        return image

    def screenshot(self):
        self.scanner.events.append(('screenshot', self.scanner.index))
        self.image = self.frame()


class _CurrentScanner(_ContinuousScanner):
    """当前入口绝不选择首猫或访问列表；身份稳定检查使用真实只读状态机。"""

    def __init__(self, captures, *, current=0, attributes=None, cn=True):
        super().__init__(captures, attributes=attributes, cn=cn)
        self.index = current
        self.in_detail = True
        self.detail_page = True
        self.identity_sequence = None
        self.identity_reads = 0
        self.ocr_error = None
        self.device = _CurrentDevice(self)

    def _load_ocr(self):
        self.events.append(('ocr',))
        if self.ocr_error is not None:
            raise self.ocr_error
        return self.ocr

    def _read_current_cat(self, ocr):
        self.identity_reads += 1
        if self.identity_sequence is not None:
            identity = self.identity_sequence[min(self.identity_reads - 1,
                                                   len(self.identity_sequence) - 1)]
        else:
            capture = self.captures[self.index]
            identity = capture.display_name, capture.level
        self.events.append(('identity', self.index, identity))
        return identity

    def _confirm_talent_identity(self, ocr, expected):
        self.events.append(('confirm', expected))
        if self.confirm_error is not None:
            raise self.confirm_error
        return MeowfficerScanner._confirm_talent_identity(self, ocr, expected)

    def _ensure_cattery(self):
        raise AssertionError('从当前开始不得导航猫窝')

    def _reset_swipe_cattery(self):
        raise AssertionError('从当前开始不得复位猫窝列表')

    def _reset_cattery_scroll(self):
        raise AssertionError('从当前开始不得滚动猫窝列表')

    def _select_verified_card(self, *args, **kwargs):
        raise AssertionError('从当前开始不得选择猫窝卡片')

    def _open_talent(self):
        raise AssertionError('从当前开始不得重新打开天赋页')


class _CurrentFlow(_FlowHarness):
    """保留既有完整资料比较，末猫手势停留原猫，由五次策略结束未知范围。"""

    def __enter__(self):
        super().__enter__()
        self.count.side_effect = AssertionError('当前起点不能读取猫窝拥有数')
        self.sort_check.side_effect = AssertionError('当前起点不能读取猫窝排序')
        return self

    def swipe(self, scanner, ocr, name, level, *, defer_same_name=True):
        if not defer_same_name:
            raise AssertionError('同名候选应交给完整资料比较')
        scanner.events.append(('next', scanner.index, name, level))
        scanner.device.records.update(('MEOWFFICER_NEXT', 'SWIPE'))
        if scanner.index in scanner.next_errors:
            raise scanner.next_errors[scanner.index]
        if scanner.index + 1 < len(scanner.captures):
            scanner.index += 1
        following = scanner.captures[scanner.index]
        identity = following.display_name, following.level
        return None if identity == (name, level) else identity


class CurrentStartScanTests(unittest.TestCase):
    def setUp(self):
        server = patch('module.config.server.server', 'cn')
        server.start()
        self.addCleanup(server.stop)

    def assert_no_cattery_input(self, flow):
        flow.count.assert_not_called()
        flow.sort_check.assert_not_called()

    def test_public_entry_starts_at_selected_middle_cat_and_limit_counts_only_this_run(self):
        scanner = _CurrentScanner(_different_cats(8), current=3)
        scanner.scanned = [('旧报告', [], 1)]
        callback = Mock(return_value={'status': 'unchanged'})
        with _CurrentFlow(scanner) as flow:
            rows = scanner.scan_all(limit=2, passes=1, on_cat=callback, start_current=True)
            self.assert_no_cattery_input(flow)
            self.assertEqual(flow.read.call_count, 2)
            self.assertEqual(flow.next.call_count, 1)
        self.assertEqual([row[0] for row in rows], ['匿名指挥喵3', '匿名指挥喵4'])
        self.assertEqual(callback.call_count, 2)
        self.assertEqual(scanner.identity_reads, 3)
        self.assertEqual(sum(event[0] == 'confirm' for event in scanner.events), 1)
        self.assertTrue(scanner.in_detail)

    def test_limit_one_reads_current_cat_without_portrait_swipe(self):
        scanner = _CurrentScanner(_different_cats(3), current=2)
        with _CurrentFlow(scanner) as flow:
            rows = scan_continuous_detail(scanner, scanner.ocr, limit=1, start_current=True)
            self.assertEqual(flow.read.call_count, 1)
            flow.next.assert_not_called()
            self.assert_no_cattery_input(flow)
        self.assertEqual([row[0] for row in rows], ['匿名指挥喵2'])

    def test_current_scan_limit_can_cross_twelve_records_and_ignores_cattery_passes(self):
        scanner = _CurrentScanner(_different_cats(30), current=5)
        with _CurrentFlow(scanner) as flow:
            rows = scanner.scan_all(limit=20, passes=1, start_current=True)
            self.assertEqual(flow.read.call_count, 20)
            self.assertEqual(flow.next.call_count, 19)
            self.assert_no_cattery_input(flow)
        self.assertEqual([row[0] for row in rows], [f'匿名指挥喵{index}' for index in range(5, 25)])

    def test_unlimited_unknown_total_ends_only_at_fifth_fully_identical_transition(self):
        scanner = _CurrentScanner([_capture()])
        accepted, published = [], []
        with _CurrentFlow(scanner) as flow:
            rows = scanner.scan_all(start_current=True,
                                    on_cat=lambda active, cat: accepted.append(active.capture_count),
                                    on_result=lambda active, row: published.append(active.capture_count))
            self.assertEqual(flow.read.call_count, 6)
            self.assertEqual(flow.next.call_count, 5)
            self.assert_no_cattery_input(flow)
            flow.logger.warning.assert_called_once()
        self.assertEqual(len(rows), 5)
        self.assertEqual(accepted, [1, 2, 3, 4, 5])
        self.assertEqual(published, accepted)
        self.assertNotIn(6, scanner.device.clears)
        self.assertIn('MEOWFFICER_NEXT', scanner.device.records)
        self.assertIn('SWIPE', scanner.device.records)

    def test_unknown_total_keeps_distinct_prefix_before_five_identical_last_cat_checks(self):
        scanner = _CurrentScanner(_different_cats(4), current=2)
        with _CurrentFlow(scanner) as flow:
            rows = scanner.scan_all(start_current=True)
            self.assertEqual(flow.read.call_count, 7)
            self.assertEqual(flow.next.call_count, 6)
        self.assertEqual([row[0] for row in rows], ['匿名指挥喵2'] + ['匿名指挥喵3'] * 5)

    def test_readonly_callback_publishes_each_record_before_next_swipe_without_sort_check(self):
        scanner = _CurrentScanner(_different_cats(4), current=1)
        published = []
        with _CurrentFlow(scanner) as flow:
            rows = scanner.scan_all(limit=3, start_current=True,
                on_result=lambda active, row: published.append((len(active.scanned), flow.next.call_count)))
            self.assert_no_cattery_input(flow)
        self.assertEqual(published, [(1, 0), (2, 1), (3, 2)])
        self.assertEqual(len(rows), 3)

    def test_non_cn_is_rejected_before_loading_ocr_or_taking_screenshot(self):
        scanner = _CurrentScanner(_different_cats(1), cn=False)
        with _CurrentFlow(scanner) as flow:
            with self.assertRaises(RequestHumanTakeover):
                scanner.scan_all(start_current=True)
            flow.read.assert_not_called()
            flow.next.assert_not_called()
            self.assert_no_cattery_input(flow)
        self.assertEqual(scanner.events, [])

    def test_unknown_initial_page_does_not_read_identity_or_talents_or_lock(self):
        scanner = _CurrentScanner(_different_cats(1))
        scanner.detail_page = False
        callback = Mock()
        with _CurrentFlow(scanner) as flow:
            with self.assertRaises(RequestHumanTakeover):
                scanner.scan_all(start_current=True, on_cat=callback)
            flow.read.assert_not_called()
            flow.next.assert_not_called()
        self.assertEqual(scanner.identity_reads, 0)
        callback.assert_not_called()
        self.assertEqual(scanner.scanned, [])

    def test_missing_initial_name_or_level_stops_before_capture_and_callback(self):
        for identity in (('', 30), ('奥古喵', None)):
            with self.subTest(identity=identity):
                scanner = _CurrentScanner(_different_cats(1))
                scanner.identity_sequence = [identity]
                callback = Mock()
                with _CurrentFlow(scanner) as flow:
                    with self.assertRaises(RequestHumanTakeover):
                        scanner.scan_all(start_current=True, on_cat=callback)
                    flow.read.assert_not_called()
                    flow.next.assert_not_called()
                callback.assert_not_called()
                self.assertEqual(scanner.scanned, [])

    def test_identity_must_remain_consistent_for_two_new_frames_before_first_capture(self):
        scanner = _CurrentScanner(_different_cats(1))
        original = scanner.captures[0].display_name, scanner.captures[0].level
        scanner.identity_sequence = [original, ('其他猫', 30)]
        with _CurrentFlow(scanner) as flow:
            with self.assertRaises(RequestHumanTakeover):
                scanner.scan_all(start_current=True)
            flow.read.assert_not_called()
            flow.next.assert_not_called()
        self.assertEqual(scanner.identity_reads, 13)
        self.assertEqual(scanner.scanned, [])

    def test_capture_identity_not_confirmed_is_not_accepted_or_locked(self):
        scanner = _CurrentScanner([_capture(identity_confirmed=False)])
        callback = Mock()
        with _CurrentFlow(scanner):
            with self.assertRaises(RequestHumanTakeover):
                scanner.scan_all(start_current=True, on_cat=callback)
        callback.assert_not_called()
        self.assertEqual(scanner.scanned, [])

    def test_same_data_with_unknown_attributes_cannot_increment_end_counter(self):
        scanner = _CurrentScanner([_capture()], attributes=[None])
        callback = Mock()
        with _CurrentFlow(scanner) as flow:
            with self.assertRaises(RequestHumanTakeover):
                scanner.scan_all(start_current=True, on_cat=callback)
            self.assertEqual(flow.read.call_count, 2)
            self.assertEqual(flow.next.call_count, 1)
        self.assertEqual(len(scanner.scanned), 1)
        self.assertEqual(callback.call_count, 1)
        self.assertNotIn(2, scanner.device.clears)

    def test_next_device_error_keeps_accepted_prefix_and_propagates_original_exception(self):
        scanner = _CurrentScanner(_different_cats(2))
        error = GameStuckError('匿名下一只手势异常')
        scanner.next_errors[0] = error
        with _CurrentFlow(scanner):
            with self.assertRaises(GameStuckError) as raised:
                scanner.scan_all(start_current=True)
        self.assertIs(raised.exception, error)
        self.assertEqual(len(scanner.scanned), 1)

    def test_current_callback_error_keeps_recovery_context_cleanup_and_no_extra_swipe(self):
        scanner = _CurrentScanner(_different_cats(2))
        original_context = object()
        scanner._meowfficer_lock_recovery = original_context
        error = GameStuckError('匿名当前猫改锁异常')

        def callback(active, capture):
            self.assertIsNot(active._meowfficer_lock_recovery, original_context)
            raise error

        with _CurrentFlow(scanner) as flow:
            with self.assertRaises(GameStuckError) as raised:
                scanner.scan_all(start_current=True, on_cat=callback)
            flow.next.assert_not_called()
        self.assertIs(raised.exception, error)
        self.assertIs(scanner._meowfficer_lock_recovery, original_context)
        self.assertEqual(scanner.scanned, [])


if __name__ == '__main__':
    unittest.main()
