"""天赋页连续扫描回归：仅使用匿名内存画面，不连接设备或 OCR 模型。"""

import unittest
from contextlib import ExitStack
from dataclasses import replace
from unittest.mock import Mock, patch

import numpy as np

from module.exception import GameStuckError, RequestHumanTakeover
from module.meowfficer.scan import MeowfficerScanner
from module.meowfficer.scan_capture import ScanCapture
from module.meowfficer.scan_continuous import identical_capture, scan_continuous_detail
from module.meowfficer.scan_next import swipe_next_cat
from module.meowfficer.scan_utils import CURRENT_CAT_LEVEL_AREA, CURRENT_CAT_NAME_AREA
from module.meowfficer.score import Talent


MODULE = 'module.meowfficer.scan_continuous'


def _capture(name='匿名指挥喵', level=30, talent_level=1, **kwargs):
    """完整性与猫种分别提供，避免把自定义名称当成不完整天赋。"""
    fields = dict(display_name=name, level=level,
                  talents=[Talent('侵略如火', '侵略如火', talent_level)],
                  breed='毗沙丸', rarity='SSR', complete=True,
                  identity_confirmed=True, talents_complete=True)
    fields.update(kwargs)
    return ScanCapture(**fields)


def _different_cats(count):
    return [_capture(name=f'匿名指挥喵{index}') for index in range(count)]


class _MemoryDevice:
    """小画面只编码读取序号；实际截图、鼠标和手势均禁止。"""

    def __init__(self, scanner):
        self.scanner = scanner
        self.image = np.zeros((16, 16, 3), dtype=np.uint8)
        self.records = {'OTHER'}
        self.removals = []
        self.clears = []

    def click_record_remove(self, name):
        stage = self.scanner.capture_count
        self.removals.append((stage, name))
        self.scanner.events.append(('remove', stage, name))
        self.records.discard(name)

    def stuck_record_clear(self):
        self.clears.append(self.scanner.capture_count)
        self.scanner.events.append(('clear', self.scanner.capture_count))

    def screenshot(self):
        raise AssertionError('编排测试不允许获取真实截图')

    def click(self, *args, **kwargs):
        raise AssertionError('编排测试不允许发送真实点击')

    def swipe(self, *args, **kwargs):
        raise AssertionError('编排测试不允许发送真实手势')


class _ContinuousScanner(MeowfficerScanner):
    """首猫只选择一次；任何后续猫窝访问都会立即使测试失败。"""

    def __init__(self, captures, attributes=None, cn=True):
        self.captures = captures
        self.attributes = attributes or [(131, 180, 220)] * len(captures)
        self.index = 0
        self.capture_count = 0
        self.events = []
        self.scanned = []
        self.in_detail = False
        self.device = _MemoryDevice(self)
        self.ocr = object()
        self.cn = cn
        self.open_ok = True
        self.confirm_error = None
        self.capture_errors = {}
        self.next_errors = {}

    def _load_ocr(self):
        return self.ocr

    def _supports_talent_swipe(self):
        return self.cn

    def _ensure_cattery(self):
        self.events.append(('ensure',))

    def _reset_swipe_cattery(self):
        self.events.append(('reset_cn',))

    def _reset_cattery_scroll(self):
        self.events.append(('reset_legacy',))

    def _select_verified_card(self, index, ocr, expected):
        if self.in_detail or index != 0 or any(event[0] == 'select' for event in self.events):
            raise AssertionError('连续读取只能在猫窝选择一次首猫')
        self.events.append(('select', index, expected))
        capture = self.captures[0]
        return capture.display_name, capture.level

    def _open_talent(self):
        self.events.append(('open', self.index))
        self.in_detail = self.open_ok
        return self.open_ok

    def _confirm_talent_identity(self, ocr, identity):
        self.events.append(('confirm', identity))
        if self.confirm_error is not None:
            raise self.confirm_error

    def _select_card(self, *args, **kwargs):
        raise AssertionError('连续读取不应预读后续猫窝卡片')

    def _back_to_cattery(self):
        raise AssertionError('连续读取不应返回猫窝')

    def _return_verified_list(self, *args, **kwargs):
        raise AssertionError('连续读取不应核验或返回猫窝')

    def _advance_cattery_screen(self):
        raise AssertionError('连续读取不应翻动猫窝列表')

    def _advance_verified_page(self):
        raise AssertionError('连续读取不应依赖每屏十二个卡片位置')


class _DetailSequenceDevice(_MemoryDevice):
    """匿名天赋页按一次立绘手势切换，供真实切猫原语持续截图。"""

    def __init__(self, scanner):
        super().__init__(scanner)
        self.image = self.frame()

    def frame(self):
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        image[0, 0, 0] = 255
        for area in (CURRENT_CAT_NAME_AREA, CURRENT_CAT_LEVEL_AREA):
            x0, y0, x1, y1 = area
            image[y0:y1, x0:x1] = 30 + self.scanner.index
        return image

    def screenshot(self):
        self.image = self.frame()
        self.scanner.events.append(('screenshot', self.scanner.index))

    def swipe(self, start, end, duration, name):
        if ((start, end, duration, name)
                != ((560, 350), (220, 350), 0.45, 'MEOWFFICER_NEXT')):
            raise AssertionError('集成验证只允许一次既有立绘左滑')
        if self.scanner.index + 1 >= len(self.scanner.captures):
            raise AssertionError('已读取目标数量后仍发送立绘手势')
        self.records.update(('MEOWFFICER_NEXT', 'SWIPE'))
        self.scanner.events.append(('portrait_swipe', self.scanner.capture_count))
        self.scanner.index += 1


class _RealNextScanner(_ContinuousScanner):
    """完整天赋使用夹具，切猫由真实状态机读取每帧身份。"""

    def __init__(self, captures):
        super().__init__(captures)
        self.device = _DetailSequenceDevice(self)

    def _read_current_cat(self, ocr):
        capture = self.captures[self.index]
        identity = capture.display_name, capture.level
        self.events.append(('current_identity', self.index, identity))
        return identity


class _FlowHarness:
    """仅替换输入识别和操作原语，保留实际连续比较与结束编排。"""

    def __init__(self, scanner, total=None, sort=True, real_next=False):
        self.scanner = scanner
        self.total = len(scanner.captures) if total is None else total
        self.sort = sort
        self.real_next = real_next

    def __enter__(self):
        self.stack = ExitStack()
        self.count = self.stack.enter_context(patch(f'{MODULE}.read_roster_count', return_value=self.total))
        self.sort_check = self.stack.enter_context(patch(f'{MODULE}.lock_independent_sort', return_value=self.sort))
        self.read = self.stack.enter_context(patch(f'{MODULE}.capture_current_cat', side_effect=self.capture))
        self.next = self.stack.enter_context(patch(
            f'{MODULE}.swipe_next_cat', side_effect=self.observe_next if self.real_next else self.swipe))
        if self.real_next:
            self.stack.enter_context(patch('module.meowfficer.scan_next.detail_page_confirmed',
                                           side_effect=lambda image: bool(image[0, 0, 0])))
        self.attr = self.stack.enter_context(patch(f'{MODULE}.read_static_attributes', side_effect=self.attributes))
        self.logger = self.stack.enter_context(patch(f'{MODULE}.logger'))
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def capture(self, scanner, ocr, name, level, *, reset_history=False):
        if not scanner.in_detail:
            raise AssertionError('读取天赋时必须保持天赋页')
        if reset_history:
            raise AssertionError('同内容比较完成前不能由读取原语清理历史')
        scanner.capture_count += 1
        scanner.events.append(('capture', scanner.index, name, level))
        scanner.device.image.fill(0)
        scanner.device.image[0, 0, :2] = scanner.index % 256, scanner.index // 256
        # 模拟读取天赋时产生的滚动记录，只有接受本次读取后才能清理。
        scanner.device.records.add('SWIPE')
        if scanner.index in scanner.capture_errors:
            raise scanner.capture_errors[scanner.index]
        capture = scanner.captures[scanner.index]
        if (capture.display_name, capture.level) != (name, level):
            raise AssertionError('编排把新猫内容配给了错误的姓名或等级')
        return capture

    def swipe(self, scanner, ocr, name, level, *, defer_same_name=True):
        if not defer_same_name:
            raise AssertionError('连续读取必须把同名未知等级交给完整天赋核验')
        scanner.events.append(('next', scanner.index, name, level))
        scanner.device.records.update(('MEOWFFICER_NEXT', 'SWIPE'))
        if scanner.index in scanner.next_errors:
            raise scanner.next_errors[scanner.index]
        if scanner.index + 1 >= len(scanner.captures):
            raise AssertionError('读取拥有数或扫描上限后仍多发了手势')
        scanner.index += 1
        following = scanner.captures[scanner.index]
        identity = following.display_name, following.level
        return None if identity == (name, level) else identity

    def observe_next(self, scanner, ocr, name, level, *, defer_same_name=True):
        following = swipe_next_cat(scanner, ocr, name, level, defer_same_name=defer_same_name)
        scanner.events.append(('next_observed', following))
        return following

    def attributes(self, image, ocr):
        index = int(image[0, 0, 0]) + 256 * int(image[0, 0, 1])
        return self.scanner.attributes[index]


class IdenticalCaptureTests(unittest.TestCase):
    """同名同级比较依据完整天赋、品质和精确属性，不依据立绘动画。"""

    def setUp(self):
        self.image = np.zeros((16, 16, 3), dtype=np.uint8)
        self.ocr = object()

    def compare(self, previous, current, attributes=((131, 180, 220), (131, 180, 220))):
        with patch(f'{MODULE}.read_static_attributes', side_effect=attributes):
            return identical_capture(previous, current, self.image, self.image.copy(), self.ocr)

    def test_different_identity_does_not_require_same_identity_evidence(self):
        for current in (_capture(name='另一只匿名喵'), _capture(level=29)):
            with self.subTest(identity=(current.display_name, current.level)):
                with patch(f'{MODULE}.read_static_attributes') as attributes:
                    self.assertFalse(identical_capture(
                        _capture(talents_complete=False), current, self.image, self.image, self.ocr))
                attributes.assert_not_called()

    def test_talent_order_is_irrelevant_but_level_line_and_kind_are_not(self):
        talents = [Talent('侵略如火', '侵略如火', 1), Talent('其徐如林', '其徐如林', 1)]
        previous = _capture(talents=talents)
        self.assertTrue(self.compare(previous, _capture(talents=list(reversed(talents)))))
        for change in (replace(talents[0], level=2), replace(talents[0], line='另一条天赋线'),
                       replace(talents[0], kind='special'), replace(talents[0], name='另一项天赋')):
            with self.subTest(change=change):
                self.assertFalse(self.compare(previous, _capture(talents=[change, talents[1]])))

    def test_attribute_or_rarity_difference_proves_different_content(self):
        self.assertFalse(self.compare(_capture(), _capture(), ((131, 180, 220), (132, 180, 220))))
        self.assertFalse(self.compare(_capture(), _capture(rarity='SR')))

    def test_complete_talent_or_known_rarity_difference_does_not_read_attributes(self):
        previous = _capture(name='约翰喵', talents=[Talent('新人雷击士·驱逐', '雷击士·驱逐', 1)])
        differing = (_capture(name='约翰喵', talents=[Talent('装填新手·战列', '装填手·战列', 1)]),
                     replace(previous, rarity='SR'))
        for current in differing:
            with self.subTest(current=current):
                with patch(f'{MODULE}.read_static_attributes',
                           side_effect=AssertionError('已确认不同猫不应读取属性')) as attributes:
                    self.assertFalse(identical_capture(previous, current, self.image, self.image, self.ocr))
                attributes.assert_not_called()

    def test_incomplete_talents_cannot_prove_switch_even_if_talents_or_rarity_differ(self):
        for current in (_capture(talent_level=2, talents_complete=False),
                        _capture(rarity='SR', talents_complete=False)):
            with self.subTest(current=current):
                with patch(f'{MODULE}.read_static_attributes') as attributes:
                    with self.assertRaisesRegex(RequestHumanTakeover, '全部天赋未能完整确认'):
                        identical_capture(_capture(), current, self.image, self.image, self.ocr)
                attributes.assert_not_called()

    def test_unknown_rarity_with_same_talents_is_protected_before_reading_attributes(self):
        for previous, current in ((_capture(rarity=None), _capture()),
                                  (_capture(), _capture(rarity=None)),
                                  (_capture(rarity=None), _capture(rarity=None)),
                                  (_capture(rarity='未知品质'), _capture())):
            with self.subTest(previous=previous.rarity, current=current.rarity):
                with patch(f'{MODULE}.read_static_attributes') as attributes:
                    with self.assertRaisesRegex(RequestHumanTakeover, '品质未能精确确认'):
                        identical_capture(previous, current, self.image, self.image, self.ocr)
                attributes.assert_not_called()

    def test_complete_talent_difference_is_conclusive_without_rarity_evidence(self):
        with patch(f'{MODULE}.read_static_attributes') as attributes:
            self.assertFalse(identical_capture(_capture(rarity=None),
                                              _capture(rarity=None, talent_level=2),
                                              self.image, self.image, self.ocr))
        attributes.assert_not_called()

    def test_complete_talent_difference_proves_switch_without_one_or_both_levels(self):
        first = [Talent('炮击新手·主力', '炮击新手·主力', 1),
                 Talent('炮击新手·巡洋', '炮击新手·巡洋', 1),
                 Talent('精锐指挥官·白鹰', '新晋指挥官·白鹰', 2)]
        second = [Talent('炮击新手·主力', '炮击新手·主力', 1),
                  Talent('精锐指挥官·白鹰', '新晋指挥官·白鹰', 2)]
        for levels in ((None, 1), (1, None), (None, None)):
            with self.subTest(levels=levels):
                with patch(f'{MODULE}.read_static_attributes') as attributes:
                    self.assertFalse(identical_capture(
                        _capture(name='奥古喵', level=levels[0], talents=first),
                        _capture(name='奥古喵', level=levels[1], talents=second),
                        self.image, self.image, self.ocr))
                attributes.assert_not_called()

    def test_same_complete_talents_with_unknown_level_cannot_count_as_identical(self):
        for levels in ((None, 1), (1, None), (None, None)):
            with self.subTest(levels=levels):
                with patch(f'{MODULE}.read_static_attributes') as attributes:
                    with self.assertRaisesRegex(RequestHumanTakeover, '等级未能确认'):
                        identical_capture(_capture(level=levels[0]), _capture(level=levels[1]),
                                          self.image, self.image, self.ocr)
                attributes.assert_not_called()

    def test_incomplete_different_subset_cannot_prove_switch_when_levels_are_unknown(self):
        for levels in ((None, 1), (1, None), (None, None)):
            for incomplete_first in (False, True):
                with self.subTest(levels=levels, incomplete_first=incomplete_first):
                    previous = _capture(level=levels[0], talents_complete=not incomplete_first)
                    current = _capture(level=levels[1], talents=[],
                                       talents_complete=incomplete_first)
                    with patch(f'{MODULE}.read_static_attributes') as attributes:
                        with self.assertRaisesRegex(RequestHumanTakeover, '全部天赋未能完整确认'):
                            identical_capture(previous, current, self.image, self.image, self.ocr)
                    attributes.assert_not_called()

    def test_unrelated_image_change_does_not_prove_different_cat(self):
        changed = np.full_like(self.image, 255)
        with patch(f'{MODULE}.read_static_attributes', return_value=(131, 180, 220)):
            self.assertTrue(identical_capture(_capture(), _capture(), self.image, changed, self.ocr))

    def test_unknown_breed_and_incomplete_score_can_have_complete_talents(self):
        capture = _capture(breed=None, complete=False, reasons=['自定义猫名'])
        self.assertTrue(self.compare(capture, capture))

    def test_blue_cat_compares_known_rarity_and_attributes_without_scoring_talents(self):
        blue = _capture(breed=None, rarity='R', talents=[], complete=False)
        self.assertTrue(self.compare(blue, blue))
        self.assertFalse(self.compare(blue, blue, ((131, 180, 220), (131, 181, 220))))

    def test_unknown_complete_talents_attributes_or_level_cannot_count_as_identical(self):
        for previous, current in ((_capture(talents_complete=False), _capture()),
                                  (_capture(), _capture(talents_complete=False))):
            with self.subTest(previous=previous.talents_complete, current=current.talents_complete):
                with self.assertRaisesRegex(RequestHumanTakeover, '全部天赋未能完整确认'):
                    self.compare(previous, current)
        for attrs in ((None, (131, 180, 220)), ((131, 180, 220), None)):
            with self.subTest(attributes=attrs):
                with self.assertRaisesRegex(RequestHumanTakeover, '三项属性未能精确确认'):
                    self.compare(_capture(), _capture(), attrs)
        with self.assertRaisesRegex(RequestHumanTakeover, '等级未能确认'):
            self.compare(_capture(level=None), _capture(level=None))

    def test_attribute_device_error_propagates(self):
        error = GameStuckError('匿名设备异常')
        with patch(f'{MODULE}.read_static_attributes', side_effect=error):
            with self.assertRaises(GameStuckError) as caught:
                identical_capture(_capture(), _capture(), self.image, self.image, self.ocr)
        self.assertIs(caught.exception, error)


class ContinuousDetailTests(unittest.TestCase):
    """拥有数、扫描上限、锁操作和异常边界均不触发返回列表。"""

    def assert_single_entry(self, scanner):
        self.assertEqual([event for event in scanner.events if event[0] == 'select'], [('select', 0, None)])
        self.assertEqual(sum(event[0] == 'open' for event in scanner.events), 1)
        self.assertEqual(sum(event[0] == 'confirm' for event in scanner.events), 1)
        self.assertTrue(scanner.in_detail)

    def assert_unaccepted_stage_protected(self, scanner, stage):
        self.assertNotIn(stage, scanner.device.clears)
        self.assertFalse(any(cleared_stage == stage for cleared_stage, _ in scanner.device.removals))
        self.assertIn('MEOWFFICER_NEXT', scanner.device.records)
        self.assertIn('SWIPE', scanner.device.records)
        self.assertIn('OTHER', scanner.device.records)

    def test_limit_twenty_with_three_hundred_fifty_owned_ignores_screen_boundaries(self):
        scanner = _ContinuousScanner(_different_cats(350))
        on_cat = Mock(return_value={'status': 'changed'})
        with _FlowHarness(scanner) as flow:
            result = scan_continuous_detail(scanner, scanner.ocr, limit=20, on_cat=on_cat)
            self.assertEqual(flow.read.call_count, 20)
            self.assertEqual(flow.next.call_count, 19)
            flow.sort_check.assert_called_once()
            flow.attr.assert_not_called()
        self.assertIs(result, scanner.scanned)
        self.assertEqual(len(result), 20)
        self.assertEqual(on_cat.call_count, 20)
        self.assertEqual([row[0] for row in result], [f'匿名指挥喵{i}' for i in range(20)])
        self.assert_single_entry(scanner)

    def test_all_three_hundred_fifty_one_owned_have_no_twelve_screen_cap(self):
        scanner = _ContinuousScanner(_different_cats(351))
        with _FlowHarness(scanner) as flow:
            result = scan_continuous_detail(scanner, scanner.ocr)
            self.assertEqual(flow.read.call_count, 351)
            self.assertEqual(flow.next.call_count, 350)
            flow.sort_check.assert_not_called()
        self.assertEqual(len(result), 351)
        self.assertEqual(result[-1][0], '匿名指挥喵350')
        self.assert_single_entry(scanner)

    def test_readonly_progress_is_published_before_next_swipe_without_lock_sort_requirement(self):
        scanner = _ContinuousScanner(_different_cats(3))
        published = []
        with _FlowHarness(scanner) as flow:
            flow.sort_check.return_value = False

            def on_result(current, entry):
                self.assertIs(current, scanner)
                self.assertEqual(entry, current.scanned[-1])
                published.append((len(current.scanned), flow.next.call_count))

            result = scan_continuous_detail(scanner, scanner.ocr, on_result=on_result)
            flow.sort_check.assert_not_called()
        self.assertEqual(published, [(1, 0), (2, 1), (3, 2)])
        self.assertEqual(len(result), 3)
        self.assert_single_entry(scanner)

    def test_total_or_limit_finishes_without_extra_last_cat_gesture(self):
        for total, limit, expected in ((1, 0, 1), (3, 20, 3), (5, 1, 1), (5, 0, 5)):
            with self.subTest(total=total, limit=limit):
                scanner = _ContinuousScanner(_different_cats(total))
                with _FlowHarness(scanner) as flow:
                    result = scan_continuous_detail(scanner, scanner.ocr, limit=limit)
                    self.assertEqual(flow.next.call_count, expected - 1)
                    self.assertEqual(flow.read.call_count, expected)
                self.assertEqual(len(result), expected)
                self.assert_single_entry(scanner)

    def test_fifth_identical_transition_is_not_recorded_or_locked_and_keeps_protection(self):
        scanner = _ContinuousScanner([_capture() for _ in range(10)])
        locked = []
        published = []

        def on_cat(active, capture):
            locked.append(active.capture_count)
            active.events.append(('lock', active.capture_count))
            return {'status': 'changed'}

        with _FlowHarness(scanner) as flow:
            result = scan_continuous_detail(scanner, scanner.ocr, on_cat=on_cat,
                                            on_result=lambda active, entry: published.append(active.capture_count))
            self.assertEqual(flow.read.call_count, 6)
            self.assertEqual(flow.next.call_count, 5)
            flow.logger.warning.assert_called_once()
            self.assertFalse(any('连续读取结束' in str(call) for call in flow.logger.info.call_args_list))
            self.assertTrue(any('5/10' in str(call) for call in flow.logger.attr.call_args_list))
        self.assertEqual(len(result), 5)
        self.assertEqual(locked, [1, 2, 3, 4, 5])
        self.assertEqual(published, [1, 2, 3, 4, 5])
        self.assert_unaccepted_stage_protected(scanner, 6)
        self.assert_single_entry(scanner)

    def test_known_total_can_end_after_four_identical_transitions_without_an_extra_probe(self):
        scanner = _ContinuousScanner([_capture() for _ in range(5)])
        with _FlowHarness(scanner) as flow:
            result = scan_continuous_detail(scanner, scanner.ocr)
            self.assertEqual(flow.next.call_count, 4)
            self.assertEqual(flow.read.call_count, 5)
            flow.logger.warning.assert_not_called()
        self.assertEqual(len(result), 5)
        self.assertEqual(scanner.device.records, {'OTHER'})
        self.assert_single_entry(scanner)

    def test_complete_talent_change_resets_identical_run(self):
        captures = [_capture() for _ in range(5)] + [_capture(talent_level=2) for _ in range(6)]
        scanner = _ContinuousScanner(captures)
        with _FlowHarness(scanner) as flow:
            result = scan_continuous_detail(scanner, scanner.ocr)
            self.assertEqual(flow.next.call_count, 10)
        self.assertEqual(len(result), 10)
        self.assertEqual([row[1][0].level for row in result], [1] * 5 + [2] * 5)
        self.assert_unaccepted_stage_protected(scanner, 11)

    def test_exact_attribute_change_resets_identical_run(self):
        attributes = [(131, 180, 220)] * 5 + [(132, 180, 220)] * 6
        scanner = _ContinuousScanner([_capture() for _ in range(11)], attributes=attributes)
        with _FlowHarness(scanner):
            result = scan_continuous_detail(scanner, scanner.ocr)
        self.assertEqual(len(result), 10)
        self.assert_unaccepted_stage_protected(scanner, 11)

    def test_same_name_level_with_distinct_talents_or_numbers_is_not_a_duplicate(self):
        for differing in ('talents', 'attributes'):
            with self.subTest(differing=differing):
                talents = [_capture(talents=[Talent(f'匿名天赋{i}', f'匿名天赋线{i}', 1)])
                           for i in range(14)] if differing == 'talents' else [_capture() for _ in range(14)]
                attrs = [(131 + i, 180, 220) for i in range(14)] if differing == 'attributes' else None
                scanner = _ContinuousScanner(talents, attributes=attrs)
                with _FlowHarness(scanner) as flow:
                    result = scan_continuous_detail(scanner, scanner.ocr)
                    self.assertEqual(flow.next.call_count, 13)
                    flow.logger.warning.assert_not_called()
                self.assertEqual(len(result), 14)
                self.assert_single_entry(scanner)

    def test_complete_different_talents_with_unreadable_attributes_are_accepted_and_published(self):
        captures = [_capture(name='约翰喵', talents=[Talent('新人雷击士·驱逐', '雷击士·驱逐', 1)]),
                    _capture(name='约翰喵', talents=[Talent('装填新手·战列', '装填手·战列', 1)])]
        scanner = _ContinuousScanner(captures, attributes=[None, None])
        locked = []
        published = []

        def on_cat(active, capture):
            locked.append(capture.talents[0].name)
            active.events.append(('lock', active.capture_count))

        def on_result(active, entry):
            published.append((entry[1][0].name, len(active.scanned)))
            self.assertEqual(locked[-1], entry[1][0].name)

        with _FlowHarness(scanner) as flow:
            result = scan_continuous_detail(scanner, scanner.ocr, on_cat=on_cat, on_result=on_result)
            flow.attr.assert_not_called()
            self.assertEqual(flow.next.call_count, 1)
            flow.logger.warning.assert_not_called()
        self.assertEqual(len(result), 2)
        self.assertEqual(locked, ['新人雷击士·驱逐', '装填新手·战列'])
        self.assertEqual(published, [('新人雷击士·驱逐', 1), ('装填新手·战列', 2)])
        self.assertEqual(scanner.device.records, {'OTHER'})
        self.assert_single_entry(scanner)

    def test_same_name_different_level_is_counted_without_attribute_compare(self):
        scanner = _ContinuousScanner([_capture(level=i) for i in range(1, 21)])
        with _FlowHarness(scanner) as flow:
            result = scan_continuous_detail(scanner, scanner.ocr)
            flow.attr.assert_not_called()
        self.assertEqual([row[2] for row in result], list(range(1, 21)))

    def test_unknown_levels_with_distinct_complete_talents_still_publish_in_detail(self):
        for levels in ((None, 1), (1, None), (None, None)):
            with self.subTest(levels=levels):
                scanner = _ContinuousScanner([
                    _capture(name='奥古喵', level=levels[0], talent_level=1),
                    _capture(name='奥古喵', level=levels[1], talent_level=2)],
                    attributes=[None, None])
                on_cat = Mock()
                on_result = Mock()
                with _FlowHarness(scanner) as flow:
                    result = scan_continuous_detail(
                        scanner, scanner.ocr, on_cat=on_cat, on_result=on_result)
                    flow.attr.assert_not_called()
                    self.assertEqual(flow.next.call_count, 1)
                    flow.logger.warning.assert_not_called()
                self.assertEqual(len(result), 2)
                self.assertEqual([entry[2] for entry in result], list(levels))
                self.assertEqual(on_cat.call_count, 2)
                self.assertEqual(on_result.call_count, 2)
                self.assertEqual(scanner.device.records, {'OTHER'})
                self.assert_single_entry(scanner)

    def test_same_complete_talents_with_unknown_level_stop_before_second_callback(self):
        scanner = _ContinuousScanner([_capture(level=None), _capture(level=None)])
        on_cat = Mock()
        on_result = Mock()
        with _FlowHarness(scanner) as flow:
            with self.assertRaisesRegex(RequestHumanTakeover, '等级未能确认'):
                scan_continuous_detail(
                    scanner, scanner.ocr, on_cat=on_cat, on_result=on_result)
            flow.attr.assert_not_called()
        self.assertEqual(len(scanner.scanned), 1)
        self.assertEqual(on_cat.call_count, 1)
        self.assertEqual(on_result.call_count, 1)
        self.assert_unaccepted_stage_protected(scanner, 2)
        self.assert_single_entry(scanner)

    def test_real_swipe_unknown_grade_handoff_reads_complete_different_talents_before_cleanup(self):
        for levels in ((1, None), (None, 1), (None, None)):
            with self.subTest(levels=levels):
                scanner = _RealNextScanner([
                    _capture(name='莫里喵', level=levels[0], talent_level=1),
                    _capture(name='莫里喵', level=levels[1], talent_level=2)])
                observed = []

                def on_cat(active, capture):
                    observed.append((capture.level, set(active.device.records)))
                    active.events.append(('lock', active.capture_count))

                on_result = Mock()
                with _FlowHarness(scanner, real_next=True) as flow:
                    result = scan_continuous_detail(
                        scanner, scanner.ocr, on_cat=on_cat, on_result=on_result)
                    flow.next.assert_called_once_with(
                        scanner, scanner.ocr, '莫里喵', levels[0], defer_same_name=True)
                    self.assertEqual(flow.read.call_args_list[1].args[2:], ('莫里喵', levels[1]))
                    flow.attr.assert_not_called()
                    flow.logger.warning.assert_not_called()
                self.assertEqual([entry[2] for entry in result], list(levels))
                self.assertEqual([level for level, _ in observed], list(levels))
                self.assertEqual(on_result.call_count, 2)
                handoff = None if levels[0] == levels[1] else ('莫里喵', levels[1])
                self.assertIn(('next_observed', handoff), scanner.events)
                self.assertIn('MEOWFFICER_NEXT', observed[1][1])
                self.assertIn('SWIPE', observed[1][1])
                sent = scanner.events.index(('portrait_swipe', 1))
                accepted = scanner.events.index(('lock', 2))
                self.assertFalse(any(event[0] in ('remove', 'clear')
                                     for event in scanner.events[sent:accepted]))
                self.assertLess(accepted, scanner.events.index(('remove', 2, 'MEOWFFICER_NEXT')))
                self.assertEqual(scanner.device.records, {'OTHER'})
                self.assert_single_entry(scanner)

    def test_real_swipe_same_complete_talents_unknown_grade_stops_without_counting_identical(self):
        scanner = _RealNextScanner([
            _capture(name='莫里喵', level=1), _capture(name='莫里喵', level=None)])
        on_cat = Mock()
        on_result = Mock()
        with _FlowHarness(scanner, real_next=True) as flow:
            with self.assertRaisesRegex(RequestHumanTakeover, '等级未能确认'):
                scan_continuous_detail(scanner, scanner.ocr, on_cat=on_cat, on_result=on_result)
            flow.next.assert_called_once_with(
                scanner, scanner.ocr, '莫里喵', 1, defer_same_name=True)
            self.assertEqual(flow.read.call_args_list[1].args[2:], ('莫里喵', None))
            flow.attr.assert_not_called()
            flow.logger.warning.assert_not_called()
            self.assertFalse(any('连续第' in str(call) for call in flow.logger.info.call_args_list))
        self.assertIn(('next_observed', ('莫里喵', None)), scanner.events)
        self.assertEqual([entry[2] for entry in scanner.scanned], [1])
        self.assertEqual(on_cat.call_count, 1)
        self.assertEqual(on_result.call_count, 1)
        self.assert_unaccepted_stage_protected(scanner, 2)
        self.assert_single_entry(scanner)

    def test_real_swipe_unknown_grade_different_incomplete_subset_keeps_pending_stage(self):
        talents = [Talent('侵略如火', '侵略如火', 1), Talent('其徐如林', '其徐如林', 1)]
        for incomplete_first in (False, True):
            with self.subTest(incomplete_first=incomplete_first):
                scanner = _RealNextScanner([
                    _capture(name='莫里喵', level=1, talents=talents,
                             talents_complete=not incomplete_first),
                    _capture(name='莫里喵', level=None, talents=talents[:1],
                             talents_complete=incomplete_first)])
                on_cat = Mock()
                on_result = Mock()
                with _FlowHarness(scanner, real_next=True) as flow:
                    with self.assertRaisesRegex(RequestHumanTakeover, '全部天赋未能完整确认'):
                        scan_continuous_detail(scanner, scanner.ocr, on_cat=on_cat, on_result=on_result)
                    flow.next.assert_called_once_with(
                        scanner, scanner.ocr, '莫里喵', 1, defer_same_name=True)
                    self.assertEqual(flow.read.call_args_list[1].args[2:], ('莫里喵', None))
                    flow.attr.assert_not_called()
                    flow.logger.warning.assert_not_called()
                self.assertIn(('next_observed', ('莫里喵', None)), scanner.events)
                self.assertEqual(len(scanner.scanned), 1)
                self.assertEqual(on_cat.call_count, 1)
                self.assertEqual(on_result.call_count, 1)
                self.assert_unaccepted_stage_protected(scanner, 2)
                self.assert_single_entry(scanner)

    def test_unknown_breed_still_uses_complete_talents_for_continuous_comparison(self):
        scanner = _ContinuousScanner([_capture(breed=None, complete=False) for _ in range(3)])
        with _FlowHarness(scanner):
            result = scan_continuous_detail(scanner, scanner.ocr)
        self.assertEqual(len(result), 3)
        self.assert_single_entry(scanner)

    def test_blue_cats_count_towards_scan_limit_without_talent_scoring(self):
        captures = [_capture(name=f'匿名蓝猫{i}', breed=None, rarity='R', talents=[], complete=False)
                    for i in range(20)]
        scanner = _ContinuousScanner(captures)
        with _FlowHarness(scanner) as flow:
            result = scan_continuous_detail(scanner, scanner.ocr, limit=13)
            self.assertEqual(flow.next.call_count, 12)
        self.assertEqual(len(result), 13)
        self.assertTrue(all(not talents for _, talents, _ in result))

    def test_unknown_sort_stops_before_first_selection_and_any_lock_operation(self):
        for sort in (False, None):
            with self.subTest(sort=sort):
                scanner = _ContinuousScanner(_different_cats(3))
                on_cat = Mock()
                with _FlowHarness(scanner, sort=sort) as flow:
                    with self.assertRaisesRegex(RequestHumanTakeover, '按等级排序'):
                        scan_continuous_detail(scanner, scanner.ocr, on_cat=on_cat)
                    flow.read.assert_not_called()
                    flow.next.assert_not_called()
                on_cat.assert_not_called()
                self.assertEqual(scanner.events, [])
                self.assertFalse(scanner.in_detail)

    def test_report_only_scanning_does_not_require_lock_independent_sort(self):
        scanner = _ContinuousScanner(_different_cats(3))
        with _FlowHarness(scanner, sort=None) as flow:
            result = scan_continuous_detail(scanner, scanner.ocr)
            flow.sort_check.assert_not_called()
        self.assertEqual(len(result), 3)

    def test_unknown_or_zero_owned_count_never_opens_first_cat(self):
        for total in (None, 0):
            with self.subTest(total=total):
                scanner = _ContinuousScanner(_different_cats(3))
                on_cat = Mock()
                with _FlowHarness(scanner) as flow:
                    flow.count.return_value = total
                    if total is None:
                        with self.assertRaisesRegex(RequestHumanTakeover, '拥有数量未能精确确认'):
                            scan_continuous_detail(scanner, scanner.ocr, on_cat=on_cat)
                    else:
                        self.assertIs(scan_continuous_detail(scanner, scanner.ocr, on_cat=on_cat), scanner.scanned)
                    flow.read.assert_not_called()
                    flow.next.assert_not_called()
                    flow.sort_check.assert_not_called()
                on_cat.assert_not_called()
                self.assertEqual(scanner.events, [])
                self.assertFalse(scanner.in_detail)

    def test_incomplete_same_identity_talents_stop_before_callback_and_keep_current_stage(self):
        scanner = _ContinuousScanner([_capture(), _capture(talents_complete=False)])
        on_cat = Mock(return_value={'status': 'changed'})
        with _FlowHarness(scanner):
            with self.assertRaisesRegex(RequestHumanTakeover, '全部天赋未能完整确认'):
                scan_continuous_detail(scanner, scanner.ocr, on_cat=on_cat)
        self.assertEqual(len(scanner.scanned), 1)
        self.assertEqual(on_cat.call_count, 1)
        self.assert_unaccepted_stage_protected(scanner, 2)
        self.assert_single_entry(scanner)

    def test_incomplete_different_talents_stop_before_lock_and_report_callback(self):
        scanner = _ContinuousScanner([_capture(), _capture(talent_level=2, talents_complete=False)],
                                     attributes=[None, None])
        on_cat = Mock()
        on_result = Mock()
        with _FlowHarness(scanner) as flow:
            with self.assertRaisesRegex(RequestHumanTakeover, '全部天赋未能完整确认'):
                scan_continuous_detail(scanner, scanner.ocr, on_cat=on_cat, on_result=on_result)
            flow.attr.assert_not_called()
        self.assertEqual(len(scanner.scanned), 1)
        self.assertEqual(on_cat.call_count, 1)
        self.assertEqual(on_result.call_count, 1)
        self.assert_unaccepted_stage_protected(scanner, 2)
        self.assert_single_entry(scanner)

    def test_unknown_same_identity_attributes_stop_without_accepting_or_clearing(self):
        for attributes in ((None, (131, 180, 220)), ((131, 180, 220), None)):
            with self.subTest(attributes=attributes):
                scanner = _ContinuousScanner([_capture(), _capture()], attributes=list(attributes))
                on_cat = Mock(return_value={'status': 'changed'})
                on_result = Mock()
                with _FlowHarness(scanner):
                    with self.assertRaisesRegex(RequestHumanTakeover, '三项属性未能精确确认'):
                        scan_continuous_detail(scanner, scanner.ocr, on_cat=on_cat, on_result=on_result)
                self.assertEqual(len(scanner.scanned), 1)
                self.assertEqual(on_cat.call_count, 1)
                self.assertEqual(on_result.call_count, 1)
                self.assert_unaccepted_stage_protected(scanner, 2)

    def test_unknown_current_identity_stops_and_preserves_partial_results(self):
        for failure_index in (0, 1):
            with self.subTest(failure_index=failure_index):
                captures = _different_cats(3)
                captures[failure_index].identity_confirmed = False
                scanner = _ContinuousScanner(captures)
                on_cat = Mock()
                with _FlowHarness(scanner):
                    with self.assertRaisesRegex(RequestHumanTakeover, '当前猫身份无法确认'):
                        scan_continuous_detail(scanner, scanner.ocr, on_cat=on_cat)
                self.assertEqual(len(scanner.scanned), failure_index)
                self.assertEqual(on_cat.call_count, failure_index)
                if failure_index:
                    self.assert_unaccepted_stage_protected(scanner, 2)
                self.assertTrue(scanner.in_detail)

    def test_control_errors_propagate_without_returning_or_discarding_partial_results(self):
        for location in ('capture', 'next', 'callback'):
            with self.subTest(location=location):
                scanner = _ContinuousScanner(_different_cats(3))
                error = GameStuckError(f'匿名{location}异常')
                on_cat = Mock(return_value={'status': 'changed'})
                if location == 'capture':
                    scanner.capture_errors[1] = error
                elif location == 'next':
                    scanner.next_errors[0] = error
                else:
                    on_cat.side_effect = [None, error]
                with _FlowHarness(scanner):
                    with self.assertRaises(GameStuckError) as caught:
                        scan_continuous_detail(scanner, scanner.ocr, on_cat=on_cat)
                self.assertIs(caught.exception, error)
                self.assertEqual(len(scanner.scanned), 1)
                self.assertTrue(scanner.in_detail)
                self.assertIn('MEOWFFICER_NEXT', scanner.device.records)
                self.assertIn('SWIPE', scanner.device.records)

    def test_initial_open_or_identity_failure_does_not_read_or_unlock(self):
        for location in ('open', 'confirm'):
            with self.subTest(location=location):
                scanner = _ContinuousScanner(_different_cats(3))
                error = RequestHumanTakeover('匿名首猫身份无法确认')
                if location == 'open':
                    scanner.open_ok = False
                else:
                    scanner.confirm_error = error
                scanner.device.records.update(('MEOWFFICER_NEXT', 'SWIPE'))
                on_cat = Mock()
                with _FlowHarness(scanner) as flow:
                    with self.assertRaises(RequestHumanTakeover):
                        scan_continuous_detail(scanner, scanner.ocr, on_cat=on_cat)
                    flow.read.assert_not_called()
                    flow.next.assert_not_called()
                on_cat.assert_not_called()
                self.assertEqual(scanner.scanned, [])
                self.assertEqual(scanner.device.removals, [])
                self.assertEqual(scanner.device.clears, [])
                self.assertEqual(scanner.device.records, {'OTHER', 'MEOWFFICER_NEXT', 'SWIPE'})

    def test_initial_stage_is_cleared_only_after_first_detail_identity_confirmation(self):
        scanner = _ContinuousScanner([_capture()])
        scanner.device.records.update(('MEOWFFICER_NEXT', 'SWIPE'))
        with _FlowHarness(scanner):
            scan_continuous_detail(scanner, scanner.ocr)
        confirmed = scanner.events.index(('confirm', ('匿名指挥喵', 30)))
        captured = scanner.events.index(('capture', 0, '匿名指挥喵', 30))
        for name in ('MEOWFFICER_NEXT', 'SWIPE'):
            cleaned = scanner.events.index(('remove', 0, name))
            self.assertLess(confirmed, cleaned)
            self.assertLess(cleaned, captured)
        self.assertEqual(scanner.device.records, {'OTHER'})

    def test_callback_acceptance_precedes_history_cleanup_and_other_records_remain(self):
        scanner = _ContinuousScanner([_capture(), _capture()])
        observed = []

        def on_cat(active, capture):
            observed.append((active.capture_count, set(active.device.records)))
            active.events.append(('lock', active.capture_count))
            return {'status': 'changed'}

        with _FlowHarness(scanner) as flow:
            scan_continuous_detail(scanner, scanner.ocr, on_cat=on_cat)
            self.assertTrue(all(call.kwargs.get('reset_history') is False
                                for call in flow.read.call_args_list))
        self.assertIn('SWIPE', observed[0][1])
        self.assertIn('MEOWFFICER_NEXT', observed[1][1])
        for stage in (1, 2):
            lock_index = scanner.events.index(('lock', stage))
            self.assertLess(lock_index, scanner.events.index(('remove', stage, 'MEOWFFICER_NEXT')))
            self.assertLess(lock_index, scanner.events.index(('remove', stage, 'SWIPE')))
        self.assertEqual(scanner.device.records, {'OTHER'})


class ScanAllRoutingTests(unittest.TestCase):
    """国服接入连续模块；其他服务器保留原卡片遍历。"""

    def test_cn_scan_all_uses_continuous_entry_and_verified_top_without_pass_cap(self):
        scanner = _ContinuousScanner(_different_cats(20))
        on_cat = Mock()
        scanner.scanned = [('不应遗留旧结果', [], 1)]
        sentinel = []
        with patch(f'{MODULE}.scan_continuous_detail', return_value=sentinel) as continuous:
            with patch('module.meowfficer.scan.logger'):
                result = scanner.scan_all(limit=20, passes=1, on_cat=on_cat)
        self.assertIs(result, sentinel)
        continuous.assert_called_once_with(scanner, scanner.ocr, limit=20, on_cat=on_cat, on_result=None)
        self.assertEqual(scanner.scanned, [])
        self.assertEqual(scanner.events[:2], [('ensure',), ('reset_cn',)])
        self.assertNotIn(('reset_legacy',), scanner.events)

    def test_non_cn_scans_original_card_path_without_calling_continuous_module(self):
        scanner = _ContinuousScanner([_capture()], cn=False)
        original_talents = _capture().talents
        with ExitStack() as stack:
            continuous = stack.enter_context(patch(f'{MODULE}.scan_continuous_detail'))
            stack.enter_context(patch('module.meowfficer.scan.logger'))
            select = stack.enter_context(patch.object(scanner, '_select_card', return_value='匿名指挥喵'))
            stack.enter_context(patch.object(scanner, '_read_current_cat', return_value=('匿名指挥喵', 30)))
            read = stack.enter_context(patch.object(scanner, '_read_talents', return_value=original_talents))
            back = stack.enter_context(patch.object(scanner, '_back_to_cattery', return_value=True))
            result = scanner.scan_all(limit=1, passes=1)
        continuous.assert_not_called()
        select.assert_called_once()
        read.assert_called_once()
        back.assert_called_once()
        self.assertEqual(result, [('匿名指挥喵', original_talents, 30)])
        self.assertEqual(scanner.events[:2], [('ensure',), ('reset_legacy',)])
        self.assertNotIn(('reset_cn',), scanner.events)


if __name__ == '__main__':
    unittest.main()
