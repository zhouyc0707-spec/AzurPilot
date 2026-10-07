"""改锁刷新后的天赋页恢复：匿名资料、模拟左滑，不连接游戏或点击锁按钮。"""

import unittest
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from module.exception import GameStuckError, GameTooManyClickError
from module.meowfficer.lock_recovery import TalentPageRecovery
from module.meowfficer.scan_capture import IDENTITY_AREA, ScanCapture
from module.meowfficer.scan_roster import STATIC_ATTRIBUTE_AREAS
from module.meowfficer.score import Talent


MODULE = 'module.meowfficer.lock_recovery'
NAMES = ('林德喵', '约翰喵', '奥古喵', '埃弗喵')


def _cat(index, **overrides):
    """只在身份和三块属性 ROI 中写入匿名标记，模拟真实读取所需的小块资料。"""
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    x0, y0, x1, y1 = IDENTITY_AREA
    image[y0:y1, x0:x1] = 20 * (index + 1)
    for number, (x0, y0, x1, y1) in enumerate(STATIC_ATTRIBUTE_AREAS):
        image[y0:y1, x0:x1] = 10 * (index + 1) + number
    values = dict(display_name=NAMES[index % len(NAMES)], level=10, breed='奥古喵',
                  rarity='SSR', complete=True, identity_confirmed=True, reasons=[],
                  talents=[Talent('侵略如火', '侵略如火', 1, kind='special', raw='侵略如火')],
                  talents_complete=True,
                  identity_image=image[565:625, 160:455].copy())
    values.update(overrides)
    return ScanCapture(**values), image


def _entry():
    return dict(before='已锁定', after='未知', status='unconfirmed', reason='等待只读核验')


class _Scenario:
    """capture/左滑只改变内存中的当前猫；真正的点击方法一律不会被调用。"""

    def __init__(self, cats=None, *, current=0, transitions=None):
        self.cats = cats if cats is not None else [_cat(index) for index in range(3)]
        self.current = current
        self.transitions = list(transitions) if transitions is not None else None
        self.transition_count = 0
        self.swipe_result = 'identity'
        self.page = True
        self.attribute_values = {}
        self.device = SimpleNamespace(image=self.cats[current][1].copy(),
                                      screenshot=Mock(), click=Mock(), swipe=Mock(),
                                      click_record_remove=Mock(), stuck_record_clear=Mock())
        self.device.screenshot.side_effect = self.screenshot
        self.scanner = SimpleNamespace(device=self.device, scanned=['已有记录'],
                                       on_capture=Mock(), on_identity=Mock(),
                                       _read_current_cat=Mock(side_effect=self.identity))
        self.ocr = Mock()
        self.capture = Mock(side_effect=self.read_capture)
        self.swipe = Mock(side_effect=self.next_cat)
        self.attributes = Mock(side_effect=self.read_attributes)
        self.page_check = Mock(side_effect=lambda image: self.page)
        self.recovery = TalentPageRecovery(self.scanner, self.ocr)
        for capture, image in self.cats:
            self.recovery.remember(capture, image)

    def screenshot(self):
        self.device.image = self.cats[self.current][1].copy()

    def identity(self, ocr):
        capture = self.cats[self.current][0]
        return capture.display_name, capture.level

    def read_capture(self, scanner, ocr, name, level, **kwargs):
        return self.cats[self.current][0]

    def next_cat(self, scanner, ocr, name, level, **kwargs):
        if self.transitions is None:
            self.current = min(self.current + 1, len(self.cats) - 1)
        else:
            self.current = self.transitions[self.transition_count]
        self.transition_count += 1
        self.device.image = self.cats[self.current][1].copy()
        return self.identity(ocr) if self.swipe_result == 'identity' else self.swipe_result

    def read_attributes(self, image, ocr):
        x0, y0, _, _ = STATIC_ATTRIBUTE_AREAS[0]
        marker = int(image[y0, x0, 0])
        return self.attribute_values.get(marker, (marker, marker + 1, marker + 2))

    @contextmanager
    def patched(self):
        with ExitStack() as stack:
            for name, value in (('capture_current_cat', self.capture),
                                ('swipe_next_cat', self.swipe),
                                ('read_static_attributes', self.attributes),
                                ('detail_page_confirmed', self.page_check)):
                stack.enter_context(patch(f'{MODULE}.{name}', value))
            yield self

    def restore(self, entry=None):
        if entry is None:
            entry = _entry()
        with self.patched():
            result = self.recovery.restore(self.cats[-1][0], entry)
        return result, entry


class TalentPageRecoveryTests(unittest.TestCase):
    def assert_read_only_lock(self, scenario, entry):
        """恢复只调整报告恢复字段，不替代改锁后的只读核验或新增扫描结果。"""
        self.assertEqual((entry['before'], entry['after'], entry['status']),
                         ('已锁定', '未知', 'unconfirmed'))
        scenario.device.click.assert_not_called()
        scenario.device.swipe.assert_not_called()
        scenario.scanner.on_capture.assert_not_called()
        scenario.scanner.on_identity.assert_not_called()
        self.assertEqual(scenario.scanner.scanned, ['已有记录'])
        self.assertEqual(len(scenario.recovery.records), len(scenario.cats))

    def test_unique_anchor_restores_last_target_with_one_verified_left_swipe_per_cat(self):
        scenario = _Scenario()
        result, entry = scenario.restore()
        self.assertTrue(result)
        self.assertEqual(scenario.current, 2)
        self.assertEqual(scenario.swipe.call_count, 2)
        self.assertEqual(scenario.capture.call_count, 3)
        self.assertEqual(scenario.device.screenshot.call_count, 3)
        self.assertEqual(entry['recovery']['status'], 'restored')
        self.assertEqual(entry['recovery']['fromOrdinal'], 1)
        self.assertEqual(entry['recovery']['toOrdinal'], 3)
        self.assertEqual(entry['recovery']['swipes'], 2)
        self.assertTrue(entry['recovery']['reason'])
        for call in scenario.capture.call_args_list:
            self.assertIs(call.kwargs.get('reset_history'), False)
        for call in scenario.swipe.call_args_list:
            self.assertIs(call.kwargs.get('defer_same_name'), True)
            self.assertIs(call.kwargs.get('reset_history'), False)
        self.assertEqual(scenario.device.click_record_remove.call_count, 6)
        self.assertEqual(scenario.device.stuck_record_clear.call_count, 3)
        self.assert_read_only_lock(scenario, entry)

    def test_anchor_near_target_uses_only_remaining_distance(self):
        scenario = _Scenario(current=1)
        result, entry = scenario.restore()
        self.assertTrue(result)
        self.assertEqual(scenario.swipe.call_count, 1)
        self.assertEqual(entry['recovery']['fromOrdinal'], 2)
        self.assertEqual(entry['recovery']['swipes'], 1)

    def test_current_target_does_not_restart_or_slide_the_scan(self):
        scenario = _Scenario(current=2)
        result, entry = scenario.restore()
        self.assertFalse(result)
        scenario.swipe.assert_not_called()
        scenario.device.stuck_record_clear.assert_not_called()
        self.assertEqual(entry['recovery']['status'], 'failed')
        self.assert_read_only_lock(scenario, entry)

    def test_same_name_level_talents_with_distinct_exact_attributes_can_be_recovered(self):
        cats = [_cat(index, display_name='奥古喵') for index in range(3)]
        scenario = _Scenario(cats)
        result, entry = scenario.restore()
        self.assertTrue(result)
        self.assertEqual(scenario.swipe.call_count, 2)
        self.assert_read_only_lock(scenario, entry)

    def test_none_switch_result_still_needs_and_accepts_full_distinct_cat_evidence(self):
        scenario = _Scenario([_cat(index, display_name='奥古喵') for index in range(3)])
        scenario.swipe_result = None
        result, entry = scenario.restore()
        self.assertTrue(result)
        self.assertEqual(scenario.swipe.call_count, 2)
        self.assertEqual(entry['recovery']['status'], 'restored')

    def test_target_confirmed_rechecks_full_capture_and_attributes_on_each_fresh_frame(self):
        scenario = _Scenario(current=2)
        target = scenario.cats[-1][0]
        with scenario.patched():
            self.assertTrue(scenario.recovery.target_confirmed(target))
            self.assertTrue(scenario.recovery.target_confirmed(target))
        self.assertEqual(scenario.device.screenshot.call_count, 2)
        self.assertEqual(scenario.capture.call_count, 2)
        self.assertEqual(scenario.device.stuck_record_clear.call_count, 2)
        for call in scenario.capture.call_args_list:
            self.assertIs(call.kwargs.get('reset_history'), False)
        scenario.swipe.assert_not_called()
        scenario.device.click.assert_not_called()
        self.assertEqual(scenario.recovery.attempted, set())

    def test_target_confirmed_rejects_same_identity_pixels_name_level_but_different_full_data(self):
        for change in ('talents', 'attributes', 'talents_unknown'):
            with self.subTest(change=change):
                scenario = _Scenario(current=2)
                target, image = scenario.cats[-1]
                if change == 'attributes':
                    changed = image.copy()
                    x0, y0, x1, y1 = STATIC_ATTRIBUTE_AREAS[0]
                    changed[y0:y1, x0:x1] += 20
                    scenario.cats[-1] = (target, changed)
                elif change == 'talents':
                    other = replace(target, talents=[Talent('不动如山', '不动如山', 1,
                                                           kind='special')])
                    scenario.cats[-1] = (other, image)
                else:
                    scenario.cats[-1] = (replace(target, talents_complete=False), image)
                with scenario.patched():
                    self.assertFalse(scenario.recovery.target_confirmed(target))
                self.assertEqual(scenario.device.screenshot.call_count, 1)
                self.assertEqual(scenario.capture.call_count, 1)
                scenario.swipe.assert_not_called()
                scenario.device.click.assert_not_called()
                scenario.device.stuck_record_clear.assert_not_called()

    def test_target_confirmed_requires_latest_original_capture_and_positive_detail_page(self):
        scenario = _Scenario(current=2)
        with scenario.patched():
            for target in (replace(scenario.cats[-1][0]), scenario.cats[0][0]):
                self.assertFalse(scenario.recovery.target_confirmed(target))
            scenario.device.screenshot.assert_not_called()
            scenario.page = False
            self.assertFalse(scenario.recovery.target_confirmed(scenario.cats[-1][0]))
        scenario.capture.assert_not_called()
        scenario.swipe.assert_not_called()
        scenario.device.stuck_record_clear.assert_not_called()

    def test_same_name_level_attributes_with_distinct_complete_talents_are_unique(self):
        cats = [_cat(index, display_name='奥古喵') for index in range(3)]
        for index, (capture, image) in enumerate(cats):
            capture.talents = [Talent(('侵略如火', '不动如山', '一发入魂')[index],
                                      ('侵略如火', '不动如山', '一发入魂')[index], 1,
                                      kind='special')]
        scenario = _Scenario(cats)
        scenario.attribute_values.update({10: (1, 2, 3), 20: (1, 2, 3), 30: (1, 2, 3)})
        result, _ = scenario.restore()
        self.assertTrue(result)

    def test_duplicate_target_is_not_disambiguated_by_identity_pixels(self):
        cats = [_cat(0, display_name='奥古喵'), _cat(1), _cat(2)]
        scenario = _Scenario(cats)
        scenario.attribute_values.update({10: (1, 2, 3), 30: (1, 2, 3)})
        result, entry = scenario.restore()
        self.assertFalse(result)
        scenario.device.screenshot.assert_not_called()
        scenario.swipe.assert_not_called()
        self.assertEqual(entry['recovery']['status'], 'failed')

    def test_duplicate_anchor_anywhere_in_known_records_prevents_first_swipe(self):
        cats = [_cat(0), _cat(1, display_name='林德喵'), _cat(2)]
        scenario = _Scenario(cats)
        scenario.attribute_values.update({10: (1, 2, 3), 20: (1, 2, 3)})
        result, entry = scenario.restore()
        self.assertFalse(result)
        scenario.swipe.assert_not_called()
        scenario.device.stuck_record_clear.assert_not_called()
        self.assertIn('重复', entry['recovery']['reason'])

    def test_unknown_same_name_candidate_cannot_be_treated_as_another_cat(self):
        for overrides in ({'level': None}, {'rarity': None}, {'talents_complete': False},
                          {'identity_confirmed': False}):
            with self.subTest(overrides=overrides):
                cats = [_cat(0), _cat(1, display_name='奥古喵', **overrides), _cat(2)]
                scenario = _Scenario(cats)
                result, _ = scenario.restore()
                self.assertFalse(result)
                scenario.device.screenshot.assert_not_called()
                scenario.swipe.assert_not_called()

    def test_incomplete_path_record_prevents_first_recovery_gesture(self):
        for overrides in ({'level': None}, {'rarity': None}, {'talents_complete': False},
                          {'identity_confirmed': False}, {'display_name': ''}):
            with self.subTest(overrides=overrides):
                scenario = _Scenario([_cat(0), _cat(1, **overrides), _cat(2)])
                result, _ = scenario.restore()
                self.assertFalse(result)
                scenario.swipe.assert_not_called()
                scenario.device.stuck_record_clear.assert_not_called()

    def test_unknown_saved_path_attributes_prevent_first_recovery_gesture(self):
        for marker in (20, 30):
            with self.subTest(marker=marker):
                scenario = _Scenario()
                scenario.attribute_values[marker] = None
                result, _ = scenario.restore()
                self.assertFalse(result)
                scenario.swipe.assert_not_called()
                scenario.device.stuck_record_clear.assert_not_called()

    def test_page_unknown_stops_before_identity_or_capture_and_keeps_history(self):
        scenario = _Scenario()
        scenario.page = False
        result, _ = scenario.restore()
        self.assertFalse(result)
        scenario.scanner._read_current_cat.assert_not_called()
        scenario.capture.assert_not_called()
        scenario.swipe.assert_not_called()
        scenario.device.stuck_record_clear.assert_not_called()

    def test_unknown_live_identity_or_talents_or_attributes_stops_before_swipe(self):
        for stage in ('name', 'level', 'talents', 'rarity', 'attributes'):
            with self.subTest(stage=stage):
                scenario = _Scenario()
                if stage == 'name':
                    scenario.scanner._read_current_cat.side_effect = None
                    scenario.scanner._read_current_cat.return_value = ('', 10)
                elif stage == 'level':
                    scenario.scanner._read_current_cat.side_effect = None
                    scenario.scanner._read_current_cat.return_value = ('林德喵', None)
                elif stage == 'attributes':
                    scenario.attribute_values[10] = None
                else:
                    original = scenario.cats[0][0]
                    bad = replace(original, **({'talents_complete': False} if stage == 'talents'
                                               else {'rarity': None}))
                    scenario.capture.side_effect = None
                    scenario.capture.return_value = bad
                result, _ = scenario.restore()
                self.assertFalse(result)
                scenario.swipe.assert_not_called()
                scenario.device.stuck_record_clear.assert_not_called()

    def test_unremembered_live_cat_and_changed_live_identity_pixels_are_rejected(self):
        for change in ('name', 'identity', 'attributes'):
            with self.subTest(change=change):
                scenario = _Scenario()
                capture, image = scenario.cats[0]
                if change == 'name':
                    capture = replace(capture, display_name='舰猫')
                else:
                    image = image.copy()
                    area = IDENTITY_AREA if change == 'identity' else STATIC_ATTRIBUTE_AREAS[0]
                    x0, y0, x1, y1 = area
                    image[y0:y1, x0:x1] += 20
                scenario.cats[0] = (capture, image)
                result, _ = scenario.restore()
                self.assertFalse(result)
                scenario.swipe.assert_not_called()
                scenario.device.stuck_record_clear.assert_not_called()

    def test_wrong_jump_or_failed_gesture_stops_after_first_attempt(self):
        for next_index in (0, 2):
            with self.subTest(next_index=next_index):
                scenario = _Scenario(transitions=[next_index])
                scenario.swipe_result = None
                result, entry = scenario.restore()
                self.assertFalse(result)
                self.assertEqual(scenario.swipe.call_count, 1)
                self.assertEqual(entry['recovery']['swipes'], 1)
                self.assertEqual(scenario.device.stuck_record_clear.call_count, 1)
                self.assert_read_only_lock(scenario, entry)

    def test_full_data_mismatch_after_first_jump_does_not_allow_second_swipe(self):
        for change in ('talents', 'attributes', 'identity'):
            with self.subTest(change=change):
                scenario = _Scenario()
                original_capture, original_image = scenario.cats[1]
                if change == 'talents':
                    changed = replace(original_capture, talents=[Talent('不动如山', '不动如山',
                                                                        1, kind='special')])
                    scenario.cats[1] = (changed, original_image)
                else:
                    changed_image = original_image.copy()
                    area = IDENTITY_AREA if change == 'identity' else STATIC_ATTRIBUTE_AREAS[0]
                    x0, y0, x1, y1 = area
                    changed_image[y0:y1, x0:x1] += 20
                    scenario.cats[1] = (original_capture, changed_image)
                result, entry = scenario.restore()
                self.assertFalse(result)
                self.assertEqual(scenario.swipe.call_count, 1)
                self.assertEqual(entry['recovery']['status'], 'failed')

    def test_same_target_has_only_one_attempt_even_after_failed_recovery(self):
        for succeeds in (False, True):
            with self.subTest(succeeds=succeeds):
                scenario = _Scenario(transitions=None if succeeds else [0])
                entry = _entry()
                with scenario.patched():
                    self.assertEqual(scenario.recovery.restore(scenario.cats[-1][0], entry), succeeds)
                    calls = (scenario.device.screenshot.call_count, scenario.capture.call_count,
                             scenario.swipe.call_count, scenario.attributes.call_count)
                    report = entry['recovery'].copy()
                    self.assertFalse(scenario.recovery.restore(scenario.cats[-1][0], entry))
                    self.assertEqual(calls, (scenario.device.screenshot.call_count,
                                            scenario.capture.call_count, scenario.swipe.call_count,
                                            scenario.attributes.call_count))
                    self.assertEqual(entry['recovery'], report)

    def test_each_new_last_target_may_have_its_own_single_attempt(self):
        scenario = _Scenario([_cat(0), _cat(1)])
        first_result, _ = scenario.restore()
        self.assertTrue(first_result)
        target, image = _cat(2)
        scenario.cats.append((target, image))
        scenario.recovery.remember(target, image)
        second_result, entry = scenario.restore()
        self.assertTrue(second_result)
        self.assertEqual(entry['recovery']['fromOrdinal'], 2)
        self.assertEqual(entry['recovery']['toOrdinal'], 3)
        self.assertEqual(scenario.recovery.attempted, {1, 2})

    def test_unknown_or_nonlast_or_repeated_original_target_is_not_attempted(self):
        scenario = _Scenario()
        target = scenario.cats[-1][0]
        for capture in (replace(target), scenario.cats[0][0]):
            with self.subTest(capture=capture.display_name):
                entry = _entry()
                with scenario.patched():
                    self.assertFalse(scenario.recovery.restore(capture, entry))
                self.assertNotIn('recovery', entry)
        scenario.recovery.remember(target, scenario.cats[-1][1])
        with scenario.patched():
            self.assertFalse(scenario.recovery.restore(target, _entry()))
        scenario.device.screenshot.assert_not_called()
        scenario.swipe.assert_not_called()

    def test_game_control_exceptions_propagate_without_retry_or_lock_operation(self):
        for stage in ('screenshot', 'identity', 'capture', 'attributes', 'swipe'):
            with self.subTest(stage=stage):
                scenario = _Scenario()
                failure = GameStuckError(f'匿名{stage}异常')
                method = dict(screenshot=scenario.device.screenshot,
                              identity=scenario.scanner._read_current_cat,
                              capture=scenario.capture, attributes=scenario.attributes,
                              swipe=scenario.swipe)[stage]
                method.side_effect = failure
                entry = _entry()
                with self.assertRaises(GameStuckError):
                    scenario.restore(entry)
                self.assertLessEqual(scenario.swipe.call_count, 1)
                self.assert_read_only_lock(scenario, entry)

    def test_click_protection_exception_is_not_swallowed_or_repeated(self):
        scenario = _Scenario()
        scenario.swipe.side_effect = GameTooManyClickError('匿名手势保护')
        with self.assertRaises(GameTooManyClickError):
            scenario.restore()
        self.assertEqual(scenario.swipe.call_count, 1)
        scenario.device.click.assert_not_called()

    def test_remember_has_no_ocr_and_stores_only_independent_small_crops(self):
        capture, image = _cat(0)
        scanner = SimpleNamespace(device=SimpleNamespace(image=image))
        ocr = Mock()
        recovery = TalentPageRecovery(scanner, ocr)
        with patch(f'{MODULE}.read_static_attributes') as attributes:
            recovery.remember(capture, image)
            attributes.assert_not_called()
        ocr.det.assert_not_called()
        record = recovery.records[0]
        self.assertIs(record.original, capture)
        self.assertIsNot(record.capture, capture)
        self.assertIsNot(record.capture.talents, capture.talents)
        self.assertIsNot(record.capture.reasons, capture.reasons)
        self.assertEqual(record.capture.identity_image.shape, (60, 295, 3))
        self.assertFalse(np.shares_memory(record.capture.identity_image, image))
        self.assertEqual(len(record.attribute_crops), 3)
        for area, crop in zip(STATIC_ATTRIBUTE_AREAS, record.attribute_crops):
            x0, y0, x1, y1 = area
            self.assertEqual(crop.shape, (y1 - y0, x1 - x0, 3))
            self.assertFalse(np.shares_memory(crop, image))
        saved_identity = record.capture.identity_image.copy()
        saved_attributes = [crop.copy() for crop in record.attribute_crops]
        image[:] = 255
        np.testing.assert_array_equal(record.capture.identity_image, saved_identity)
        for crop, saved in zip(record.attribute_crops, saved_attributes):
            np.testing.assert_array_equal(crop, saved)
        self.assertLess(sum(crop.nbytes for crop in record.attribute_crops)
                        + record.capture.identity_image.nbytes, 80000)

    def test_remembered_talent_and_reason_evidence_is_independent_of_original_mutation(self):
        scenario = _Scenario()
        original = scenario.cats[-1][0]
        record = scenario.recovery.records[-1]
        original.talents[0].name = '被修改的天赋'
        original.reasons.append('后续报告原因')
        self.assertEqual(record.capture.talents[0].name, '侵略如火')
        self.assertEqual(record.capture.reasons, [])

    def test_reference_attribute_ocr_reuses_exact_crops_and_is_cached(self):
        scenario = _Scenario([_cat(index, display_name='奥古喵') for index in range(3)])
        with scenario.patched():
            record = scenario.recovery.records[1]
            first = record.read_attributes(scenario.ocr)
            second = record.read_attributes(scenario.ocr)
        self.assertEqual(first, (20, 21, 22))
        self.assertEqual(second, first)
        self.assertEqual(scenario.attributes.call_count, 1)
        rebuilt = scenario.attributes.call_args.args[0]
        self.assertEqual(rebuilt.shape, (720, 1280, 3))
        self.assertEqual(int(rebuilt[100, 100, 0]), 0)
        for area, crop in zip(STATIC_ATTRIBUTE_AREAS, record.attribute_crops):
            x0, y0, x1, y1 = area
            np.testing.assert_array_equal(rebuilt[y0:y1, x0:x1], crop)

    def test_unknown_reference_attributes_are_cached_without_ocr_retry(self):
        scenario = _Scenario()
        scenario.attribute_values[20] = None
        with scenario.patched():
            record = scenario.recovery.records[1]
            self.assertIsNone(record.read_attributes(scenario.ocr))
            self.assertIsNone(record.read_attributes(scenario.ocr))
        self.assertEqual(scenario.attributes.call_count, 1)

    def test_invalid_saved_image_is_not_a_usable_recovery_target(self):
        for image in (None, np.zeros((360, 640, 3), dtype=np.uint8),
                      np.zeros((720, 1280, 3), dtype=np.float32)):
            with self.subTest(shape=getattr(image, 'shape', None)):
                scenario = _Scenario()
                target = scenario.cats[-1][0]
                scenario.recovery.records.pop()
                scenario.recovery.remember(target, image)
                result, _ = scenario.restore()
                self.assertFalse(result)
                self.assertEqual(scenario.recovery.records[-1].attribute_crops, ())
                scenario.device.screenshot.assert_not_called()
                scenario.swipe.assert_not_called()


if __name__ == '__main__':
    unittest.main()
