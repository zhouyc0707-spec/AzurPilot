"""逐猫锁操作的遍历时机和恢复边界，不连接真实设备。"""

import unittest
from unittest.mock import patch

import numpy as np

from module.exception import RequestHumanTakeover
from module.meowfficer.scan import MeowfficerScanner
from module.meowfficer.scan_capture import ScanCapture
from module.meowfficer.scan_utils import CATTERY_PANEL_AREA
from tests.test_meowfficer_scan import _StubScanner, _talents


class FlowScanner(_StubScanner):
    def __init__(self, names):
        super().__init__([list(names) + [''] * (12 - len(names))])
        self.current_cat = ''
        self.in_detail = False
        self.events = []

    def _select_card(self, button, ocr, previous=''):
        self.assert_in_list()
        self.current_cat = super()._select_card(button, ocr, previous)
        return self.current_cat

    def appear(self, button, **kwargs):
        return not self.in_detail

    def assert_in_list(self):
        if self.in_detail:
            raise AssertionError('尚未返回列表就点击了下一张卡片')

    def _open_talent(self):
        self.in_detail = True
        return True

    def _read_current_cat(self, ocr):
        return self.current_cat, 10

    def _read_talents(self, ocr):
        raise AssertionError('锁模式不应走宽松的旧读取器')

    def _back_to_cattery(self):
        self.events.append(('back', self.current_cat))
        self.in_detail = False
        return True

    def scan_with_callback(self, callback, limit=0):
        with patch('module.meowfficer.score_lock.detail_page_confirmed', return_value=True), \
                patch('module.meowfficer.scan_capture.capture_current_cat', side_effect=self.capture):
            return MeowfficerScanner.scan_all(self, limit=limit, passes=1, on_cat=callback)

    def capture(self, scanner, ocr, cat, level):
        if not self.in_detail or scanner is not self:
            raise AssertionError('读取猫天赋的页面或扫描器不一致')
        return ScanCapture(cat, _talents('炮击新手·主力'), level, '奥古喵', 'SSR',
                           True, True)


class ScanLockFlowTests(unittest.TestCase):
    def test_same_name_cats_are_processed_before_each_return(self):
        scanner = FlowScanner(['重名猫', '重名猫'])

        def callback(current, capture):
            self.assertTrue(current.in_detail)
            current.events.append(('lock', capture.display_name))
            return {'status': 'unchanged'}

        result = scanner.scan_with_callback(callback)
        self.assertEqual(len(result), 2)
        self.assertEqual(scanner.events, [('lock', '重名猫'), ('back', '重名猫')] * 2)

    def test_blue_without_talents_counts_toward_scan_limit(self):
        scanner = FlowScanner(['蓝猫', '金猫'])
        scanner.capture = lambda *_args: ScanCapture('蓝猫', [], 10, None, 'R', True, True)
        calls = []
        result = scanner.scan_with_callback(lambda current, capture: calls.append(capture), limit=1)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result[0][0], '蓝猫')

    def test_back_failure_stops_instead_of_reentering_and_using_old_positions(self):
        scanner = FlowScanner(['猫A', '猫B'])
        scanner._back_to_cattery = lambda: False
        with self.assertRaises(RequestHumanTakeover):
            scanner.scan_with_callback(lambda *_args: {'status': 'unchanged'})

    def test_unknown_list_does_not_click_cards(self):
        scanner = FlowScanner(['猫A'])
        scanner.appear = lambda *_args, **_kwargs: False
        with self.assertRaises(RequestHumanTakeover):
            scanner.scan_with_callback(lambda *_args: self.fail('未知列表不能评分操作'))
        self.assertEqual(scanner.current_cat, '')

    def test_page_identity_mismatch_and_back_failure_stop(self):
        scanner = FlowScanner(['猫A'])
        scanner._read_current_cat = lambda ocr: ('其他猫', 10)
        scanner._back_to_cattery = lambda: False
        with self.assertRaises(RequestHumanTakeover):
            scanner.scan_with_callback(lambda *_args: self.fail('身份不一致不应回调'))

    def test_small_change_in_other_card_is_not_diluted_by_whole_panel(self):
        scanner = FlowScanner([])
        before = scanner.device.image[110:565, 700:1275].copy()
        scanner.device.image[200:220, 925:945] = 100
        self.assertFalse(scanner._cattery_order_unchanged(before, selected_index=0))
        # 单张卡片文字变化占全列表很小，旧的整图阈值会漏掉。
        x1, y1, x2, y2 = CATTERY_PANEL_AREA
        self.assertLess(np.abs(scanner.device.image[y1:y2, x1:x2].astype(float) - before).mean(), 6)

    def test_only_current_card_changes_do_not_report_reordering(self):
        scanner = FlowScanner([])
        before = scanner.device.image[110:565, 700:1275].copy()
        scanner.device.image[210:230, 800:820] = 100
        self.assertTrue(scanner._cattery_order_unchanged(before, selected_index=0))


if __name__ == '__main__':
    unittest.main()
