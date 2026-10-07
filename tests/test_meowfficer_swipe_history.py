"""跨猫滑动历史误报回归：使用真实 Device 队列算法，所有画面与操作留在内存。"""

from collections import Counter, deque
import unittest
from unittest.mock import Mock, patch

from module.device.device import Device
from module.exception import GameTooManyClickError, RequestHumanTakeover
from module.meowfficer.assets import MEOWFFICER_TALENT_TAB
from module.meowfficer.scan import MeowfficerScanner
from module.meowfficer.scan_capture import capture_current_cat
from module.ui.assets import MEOWFFICER_GOTO_DORMMENU
from tests.test_meowfficer_scan_capture import _Device, _OCR, _Scanner, _coverage_frame, _frame


class _HistoryDevice(_Device):
    """只绑定 Device 的队列方法，不调用设备构造器、ADB 或截图后端。"""

    click_record_add = Device.click_record_add
    click_record_check = Device.click_record_check
    click_record_clear = Device.click_record_clear

    def __init__(self):
        super().__init__(_frame())
        self.click_record = deque(maxlen=15)
        self.removals = []
        self.checks = []
        self.events = []

    def click_record_remove(self, button):
        before = tuple(self.click_record)
        removed = Device.click_record_remove(self, button)
        self.removals.append((str(button), before, removed, tuple(self.click_record)))
        self.events.append(('remove', str(button)))
        return removed

    def record(self, button):
        self.click_record_add(button)
        self.checks.append(tuple(self.click_record))
        self.events.append(('control', str(button)))
        self.click_record_check()

    def swipe(self, start, end, duration):
        self.record('SWIPE')
        super().swipe(start, end, duration)

    def click(self, button):
        self.record(button)

    def stuck_record_clear(self):
        """本回归只检查点击历史，不模拟时间卡死检测。"""

    def prepare_cat(self, swipes):
        """顶部两次不动，底部移动若干次后两次不动，共产生指定数量手势。"""
        self.image = _coverage_frame()
        self.pending = None
        self.top_frames = []
        self.bottom_frames = []
        for index in range(swipes - 4):
            # 同一匿名长图真实平移，保留可独立验证的重叠，不把颜色变化当作滚动。
            self.bottom_frames.append(_coverage_frame(offset=50 * (index + 1)))


class SwipeStageHistoryTests(unittest.TestCase):
    def setUp(self):
        # 真正标题识别有独立资源回归；这里用正向页状态隔离真实队列的边界。
        self.guard_patch = patch('module.meowfficer.score_lock.detail_page_confirmed', return_value=True)
        self.guard = self.guard_patch.start()
        self.addCleanup(self.guard_patch.stop)
        stack_patch = patch('module.device.device.show_function_call')
        stack_patch.start()
        self.addCleanup(stack_patch.stop)

    def scanner(self, device=None):
        scanner = _Scanner()
        scanner.device = _HistoryDevice() if device is None else device
        return scanner

    def capture(self, scanner, swipes):
        scanner.device.prepare_cat(swipes)
        before = len(scanner.device.swipes)
        result = capture_current_cat(scanner, _OCR(), '林德喵', 5)
        self.assertTrue(result.identity_confirmed)
        self.assertEqual(len(scanner.device.swipes) - before, swipes)
        return result

    @staticmethod
    def transition(device, index):
        # 猫间实际三个动作：返回猫窝、选择下一张卡片、打开天赋页。
        for button in ('MEOWFFICER_GOTO_DORMMENU', f'CAT_CARD_{index}', 'MEOWFFICER_TALENT_TAB'):
            device.record(button)

    def test_legacy_history_reproduces_third_cat_sixth_swipe_failure(self):
        scanner = self.scanner()
        device = scanner.device
        with patch.object(device, 'click_record_remove', return_value=0):
            # 禁止边界清理即还原旧行为；首猫实况为五次，后续每猫六次。
            self.capture(scanner, 5)
            self.transition(device, 2)
            self.capture(scanner, 6)
            self.transition(device, 3)
            completed_swipes = len(device.swipes)
            with self.assertRaisesRegex(GameTooManyClickError, 'SWIPE'):
                self.capture(scanner, 6)
        self.assertEqual(len(device.swipes) - completed_swipes, 5)
        rejected = device.checks[-1]
        self.assertEqual(len(rejected), 15)
        self.assertEqual(Counter(rejected)['SWIPE'], 12)
        self.assertEqual(rejected[6:9], ('MEOWFFICER_GOTO_DORMMENU', 'CAT_CARD_3', 'MEOWFFICER_TALENT_TAB'))

    def test_confirmed_cat_boundary_allows_many_six_swipe_cats_without_global_reset(self):
        scanner = self.scanner()
        device = scanner.device
        for index in range(1, 9):
            if index > 1:
                self.transition(device, index)
            self.capture(scanner, 5 if index == 1 else 6)
            self.assertLessEqual(Counter(device.click_record)['SWIPE'], 6)
        self.assertEqual(len(device.removals), 8)
        self.assertEqual([entry[0] for entry in device.removals], ['SWIPE'] * 8)
        self.assertEqual([entry[2] for entry in device.removals], [0, 5] + [6] * 6)
        for _button, before, _removed, after in device.removals:
            self.assertEqual(after, tuple(item for item in before if item != 'SWIPE'))
        self.assertIn('CAT_CARD_8', device.click_record)

    def test_cattery_reset_swipes_do_not_carry_into_first_confirmed_cat(self):
        scanner = self.scanner()
        device = scanner.device
        for button in ['SWIPE'] * 4 + ['CAT_CARD_1', 'MEOWFFICER_TALENT_TAB']:
            device.record(button)
        self.capture(scanner, 8)
        self.assertEqual(device.removals[0][2], 4)
        self.assertEqual(device.removals[0][3], ('CAT_CARD_1', 'MEOWFFICER_TALENT_TAB'))
        self.assertEqual(Counter(device.click_record)['SWIPE'], 8)
        self.assertEqual([button for button in device.click_record if button != 'SWIPE'],
                         ['CAT_CARD_1', 'MEOWFFICER_TALENT_TAB'])

    def test_unknown_page_identity_level_and_resolution_never_remove_existing_history(self):
        for failure in ('page', 'name', 'level', 'resolution'):
            with self.subTest(failure=failure):
                scanner = self.scanner()
                device = scanner.device
                device.click_record.extend(['SWIPE', 'OTHER_BUTTON', 'SWIPE'])
                original = tuple(device.click_record)
                self.guard.return_value = failure != 'page'
                if failure == 'name':
                    scanner._read_current_cat.return_value = ('上一只猫', 5)
                elif failure == 'level':
                    scanner._read_current_cat.return_value = ('林德喵', 10)
                elif failure == 'resolution':
                    device.image = device.image[:360, :640]
                if failure == 'page':
                    with self.assertRaises(RequestHumanTakeover):
                        capture_current_cat(scanner, _OCR(), '林德喵', 5)
                else:
                    result = capture_current_cat(scanner, _OCR(), '林德喵', 5)
                    self.assertFalse(result.identity_confirmed)
                self.assertEqual(device.removals, [])
                self.assertEqual(tuple(device.click_record), original)
                self.assertEqual(device.swipes, [])

    def test_other_button_history_is_preserved_and_its_real_protection_remains_active(self):
        scanner = self.scanner()
        device = scanner.device
        device.click_record.extend(['OTHER_BUTTON'] * 11 + ['SWIPE'] * 4)
        # 蓝猫无新滑动，可以直接检查边界清理后仍有全部十一条按钮记录。
        scanner._read_current_cat.return_value = ('乔治喵', 5)
        result = capture_current_cat(scanner, _OCR(rarity='R'), '乔治喵', 5)
        self.assertEqual(result.rarity, 'R')
        self.assertEqual(list(device.click_record), ['OTHER_BUTTON'] * 11)
        with self.assertRaisesRegex(GameTooManyClickError, 'OTHER_BUTTON'):
            device.record('OTHER_BUTTON')

    def test_excessive_swipes_inside_one_stage_still_raise_through_capture(self):
        scanner = self.scanner()
        device = scanner.device

        def repeated_swipe(*_args, **_kwargs):
            # 一个故障操作内部重复发送，模拟同阶段真实过量手势，不能被捕获层吞掉。
            for _ in range(12):
                device.record('SWIPE')

        device.swipe = repeated_swipe
        with self.assertRaisesRegex(GameTooManyClickError, 'SWIPE'):
            capture_current_cat(scanner, _OCR(), '林德喵', 5)
        self.assertEqual(len(device.removals), 1)
        self.assertEqual(Counter(device.checks[-1])['SWIPE'], 12)

    def test_mid_capture_page_loss_does_not_clear_the_current_stage_again(self):
        scanner = self.scanner()
        device = scanner.device
        device.click_record.extend(['SWIPE', 'BEFORE_BUTTON'])
        self.guard.side_effect = [True, True, False]
        with self.assertRaises(RequestHumanTakeover):
            capture_current_cat(scanner, _OCR(), '林德喵', 5)
        self.assertEqual(len(device.removals), 1)
        self.assertEqual(list(device.click_record), ['BEFORE_BUTTON', 'SWIPE'])


class ReturnToCatteryHistoryTests(unittest.TestCase):
    def scanner(self):
        # 使用真正 _back_to_cattery 编排，替换页面等待，不构造真实配置或设备。
        scanner = object.__new__(MeowfficerScanner)
        scanner.device = _HistoryDevice()
        scanner.device.click_record.extend(['KEEP_BUTTON', 'SWIPE', 'SWIPE', 'SWIPE'])
        scanner.appear = Mock(return_value=True)
        scanner._wait_talent_tab = Mock(return_value=True)
        scanner._wait_stable = Mock(return_value=True)
        return scanner

    def test_successful_return_clears_only_swipes_after_positive_list_recheck(self):
        scanner = self.scanner()
        device = scanner.device

        def appeared(button, **_kwargs):
            device.events.append(('appear', str(button)))
            return True

        scanner.appear.side_effect = appeared
        self.assertTrue(scanner._back_to_cattery())
        scanner._wait_talent_tab.assert_called_once_with(appear=True)
        scanner._wait_stable.assert_called_once()
        self.assertEqual(device.removals[0][2], 3)
        self.assertEqual(list(device.click_record), ['KEEP_BUTTON', str(MEOWFFICER_GOTO_DORMMENU)])
        self.assertEqual(device.events[-2:], [('appear', str(MEOWFFICER_TALENT_TAB)), ('remove', 'SWIPE')])

    def test_last_cat_six_swipes_do_not_accumulate_into_cattery_paging(self):
        for legacy in (True, False):
            with self.subTest(legacy=legacy), patch('module.device.device.show_function_call'):
                scanner = self.scanner()
                device = scanner.device
                device.click_record = deque(['KEEP_BUTTON'] + ['SWIPE'] * 6, maxlen=15)
                if legacy:
                    # 只关闭返回边界的清理，还原末猫手势带入列表翻页的旧行为。
                    with patch.object(device, 'click_record_remove', return_value=0):
                        self.assertTrue(scanner._back_to_cattery())
                    with self.assertRaisesRegex(GameTooManyClickError, 'SWIPE'):
                        for _ in range(6):
                            device.record('SWIPE')
                    self.assertEqual(Counter(device.checks[-1])['SWIPE'], 12)
                else:
                    self.assertTrue(scanner._back_to_cattery())
                    for _ in range(6):
                        device.record('SWIPE')
                    self.assertEqual(device.removals[0][2], 6)
                    self.assertEqual(Counter(device.click_record)['SWIPE'], 6)
                    self.assertIn('KEEP_BUTTON', device.click_record)

    def test_missing_return_arrow_does_not_clear_anything_or_click(self):
        scanner = self.scanner()
        scanner.appear.return_value = False
        original = tuple(scanner.device.click_record)
        self.assertFalse(scanner._back_to_cattery())
        self.assertEqual(tuple(scanner.device.click_record), original)
        self.assertEqual(scanner.device.removals, [])
        scanner._wait_talent_tab.assert_not_called()

    def test_return_wait_failure_keeps_swipes_and_other_buttons(self):
        scanner = self.scanner()
        scanner._wait_talent_tab.return_value = False
        self.assertFalse(scanner._back_to_cattery())
        self.assertEqual(Counter(scanner.device.click_record)['SWIPE'], 3)
        self.assertIn('KEEP_BUTTON', scanner.device.click_record)
        self.assertEqual(scanner.device.removals, [])
        scanner._wait_stable.assert_not_called()

    def test_list_marker_lost_during_stability_wait_does_not_clear_swipes(self):
        scanner = self.scanner()

        def wait_stable(*_args, **_kwargs):
            scanner.appear.return_value = False
            return True

        scanner._wait_stable.side_effect = wait_stable
        self.assertFalse(scanner._back_to_cattery())
        self.assertEqual(Counter(scanner.device.click_record)['SWIPE'], 3)
        self.assertEqual(scanner.device.removals, [])

    def test_return_stability_exception_propagates_without_clearing(self):
        scanner = self.scanner()
        scanner._wait_stable.side_effect = RequestHumanTakeover('返回时页面丢失')
        with self.assertRaises(RequestHumanTakeover):
            scanner._back_to_cattery()
        self.assertEqual(Counter(scanner.device.click_record)['SWIPE'], 3)
        self.assertEqual(scanner.device.removals, [])


if __name__ == '__main__':
    unittest.main()
