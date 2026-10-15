"""船坞重叠位移失败只复核新帧，不猜位移、不继续拖动或提交半份清单。"""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from module.exception import MindCalculatorScanError
from module.retire.mind_scan import MindCalculatorScan
from module.runtime.mind_recognition import ScanMerger, ScanPolicy, ScrollAmbiguityError, estimate_scroll
from tests.test_mind_scan import cards_for_levels


class ScanHarness:
    """沿用真实计时器和纹理位移，用受控识别结果隔离滚动恢复流程。"""

    def __init__(self, case):
        from tests.test_mind_calculator import RecognitionTests
        self.scanner = MindCalculatorScan.__new__(MindCalculatorScan)
        self.scanner.config = SimpleNamespace(
            Emulator_ControlMethod='MaaTouch', config_name='testpilot', save=Mock(),
            modified={'existing': '保留旧清单'})
        self.scanner.device = Mock()
        self.scanner.appear = Mock(return_value=True)
        self.scanner.handle_popup_confirm = Mock(return_value=False)
        self.scanner._policy = ScanPolicy(95, 125, True)
        self.scanner._save_scan_evidence = Mock(return_value='')
        self.scanner._save_scroll_evidence = Mock(side_effect=self.evidence)
        self.position, self.now = 0, 1.
        self.bottom = 2043
        self.captures, self.reads, self.failures, self.successes = 0, [], [], []
        self.accepted_captures = []
        self.outcomes = {}
        self.stalled = False
        self.unstall_after_zero = False
        self.unstable_after_failure = False
        self.last_failure_state = None
        offsets = list(range(0, self.bottom + 1, 227))
        self.images = dict(zip(offsets, RecognitionTests.scroll_frames(offsets)))
        harness = self

        class ObservedMerger(ScanMerger):
            def advance(self, image):
                if harness.last_failure_state is not None:
                    case.assertEqual(harness.state(self), harness.last_failure_state)
                queued = harness.outcomes.get(harness.position, [])
                if self.slots and queued:
                    outcome = queued.pop(0)
                    if outcome is not None:
                        raise outcome
                result = super().advance(image)
                harness.successes.append((harness.position, result, self.offset))
                harness.accepted_captures.append((harness.position, harness.captures))
                harness.last_failure_state = None
                if (harness.unstall_after_zero and harness.position == 0 and self.slots
                        and not harness.outcomes.get(0)):
                    harness.stalled = False
                return result

        self.merger = ObservedMerger()
        self.scanner._make_merger = Mock(return_value=self.merger)
        self.scanner.device.screenshot.side_effect = self.screenshot
        self.scanner.device.drag.side_effect = self.drag
        scrollbar = SimpleNamespace(appear=lambda _: True, total=565, match_color=self.thumb)
        case.enterContext(patch('module.base.timer.time', side_effect=lambda: self.now))
        case.enterContext(patch('module.retire.mind_scan.DOCK_SCROLL', scrollbar))
        case.enterContext(patch('module.retire.mind_scan.grid_rows', return_value=[65, 292, 519]))
        case.enterContext(patch('module.retire.mind_scan.recognize_cards', side_effect=self.recognize))

    def screenshot(self):
        self.now += .25
        self.captures += 1
        self.scanner.device.image = self.images[self.position]

    def drag(self, start, end, **kwargs):
        if not self.stalled:
            self.position = min(self.bottom, max(0, self.position + start[1] - end[1]))

    def thumb(self, _):
        start = 475 if self.position == self.bottom else 2
        return (np.arange(565) >= start) & (np.arange(565) < start + 90)

    def recognize(self, *args, **kwargs):
        self.reads.append(self.position)
        page = self.position // 681
        levels = ([125] * 21 if page == 0 else [110] * 7 + [100] * 7 + [95] * 7 if page == 1
                  else [94 - page] * 21)
        cards = cards_for_levels(levels, fleet=int(page == 0))
        for index, card in enumerate(cards):
            card.ship.update(name=f'测试舰{page}-{index}', base_rarity='SR', excluded=False)
        return cards

    def state(self, merger):
        return (merger.offset, id(merger.previous), copy.deepcopy(merger.slots),
                self.scanner.device.drag.call_count, list(self.reads),
                self.scanner.device.click_record_clear.call_count)

    def evidence(self, merger, image, error, attempt):
        self.last_failure_state = self.state(merger)
        self.failures.append((self.position, attempt, self.captures, self.last_failure_state))
        if self.unstable_after_failure:
            self.scanner._stable_pixels = lambda _: np.full((2, 2), self.captures % 2 * 255, np.uint8)

    def scan(self):
        return self.scanner._scan_pages(Mock(), Mock())


def ambiguity():
    return ScrollAmbiguityError(19.8, 20.1, 217)


class ScrollRecoveryLoopTests(unittest.TestCase):
    def test_one_or_two_ambiguous_frames_recover_without_extra_drag_or_read(self):
        for count in (1, 2):
            with self.subTest(rechecks=count):
                harness = ScanHarness(self)
                harness.outcomes[227] = [ambiguity() for _ in range(count)]
                ships = harness.scan()
                self.assertEqual(harness.reads, [0, 681, 1362])
                self.assertEqual(len(ships), 42)
                self.assertEqual(harness.scanner.device.drag.call_count, 6)
                self.assertEqual(harness.merger.offset, 1362)
                self.assertEqual([item[1] for item in harness.failures], list(range(1, count + 1)))
                self.assertTrue(all(item[3] == harness.failures[0][3] for item in harness.failures))
                captures = [item[2] for item in harness.failures]
                self.assertTrue(all(b - a >= 4 for a, b in zip(captures, captures[1:])))
                accepted = next(capture for position, capture in harness.accepted_captures if position == 227)
                self.assertGreaterEqual(accepted - captures[-1], 4)
                self.assertEqual(sum(delta == 227 for _, delta, _ in harness.successes), 6)
                harness.scanner.config.save.assert_not_called()
                self.assertEqual(harness.scanner.config.modified, {'existing': '保留旧清单'})

    def test_third_failure_stops_without_mutating_the_last_accepted_page(self):
        harness = ScanHarness(self)
        harness.outcomes[227] = [ambiguity() for _ in range(3)]
        with self.assertRaisesRegex(MindCalculatorScanError, '三次新帧核验仍未通过') as raised:
            harness.scan()
        self.assertIsInstance(raised.exception.__cause__, ScrollAmbiguityError)
        self.assertEqual([item[1] for item in harness.failures], [1, 2, 3])
        self.assertEqual(harness.state(harness.merger), harness.failures[0][3])
        self.assertEqual(harness.merger.offset, 0)
        np.testing.assert_array_equal(harness.merger.previous, harness.images[0])
        self.assertEqual(len(harness.merger.slots), 21)
        self.assertEqual(harness.reads, [0])
        self.assertEqual(harness.scanner.device.drag.call_count, 1)
        harness.scanner.config.save.assert_not_called()

    def test_each_new_displacement_has_its_own_three_frame_budget(self):
        harness = ScanHarness(self)
        harness.outcomes = {227: [ambiguity(), ambiguity()], 454: [ambiguity(), ambiguity()]}
        harness.scan()
        self.assertEqual([(position, attempt) for position, attempt, _, _ in harness.failures],
                         [(227, 1), (227, 2), (454, 1), (454, 2)])
        self.assertEqual(harness.scanner.device.drag.call_count, 6)
        self.assertEqual(harness.reads, [0, 681, 1362])

    def test_successful_zero_displacement_also_resets_the_recheck_budget(self):
        harness = ScanHarness(self)
        harness.stalled = harness.unstall_after_zero = True
        harness.outcomes = {0: [ambiguity(), ambiguity(), None, ambiguity(), ambiguity(), None]}
        harness.scan()
        self.assertEqual([item[1] for item in harness.failures], [1, 2, 1, 2])
        self.assertTrue(all(item[0] == 0 for item in harness.failures))
        self.assertEqual(harness.reads, [0, 681, 1362])
        self.assertEqual(harness.merger.offset, 1362)

    def test_generic_value_error_is_not_retried_as_a_scroll_ambiguity(self):
        harness = ScanHarness(self)
        error = ValueError('截图尺寸不适合识别')
        harness.outcomes[227] = [error]
        with self.assertRaisesRegex(MindCalculatorScanError, '截图尺寸不适合识别') as raised:
            harness.scan()
        self.assertIs(raised.exception.__cause__, error)
        harness.scanner._save_scroll_evidence.assert_not_called()
        self.assertEqual(harness.reads, [0])
        self.assertEqual(harness.scanner.device.drag.call_count, 1)
        self.assertEqual(harness.merger.offset, 0)

    def test_unstable_recheck_hits_soft_deadline_without_another_drag(self):
        harness = ScanHarness(self)
        harness.outcomes[227] = [ambiguity()]
        harness.unstable_after_failure = True
        with self.assertRaisesRegex(MindCalculatorScanError, '滚动位移复核超时'):
            harness.scan()
        self.assertLess(harness.now, 25)
        self.assertEqual(len(harness.failures), 1)
        self.assertEqual(harness.scanner.device.drag.call_count, 1)
        self.assertEqual(harness.reads, [0])
        self.assertEqual(harness.merger.offset, 0)
        harness.scanner.config.save.assert_not_called()


class ScrollEstimatorSafetyTests(unittest.TestCase):
    def test_failed_real_advance_keeps_previous_offset_and_slots_atomic(self):
        from tests.test_mind_calculator import RecognitionTests
        first, second = RecognitionTests.scroll_frames([0, 227])
        merger = ScanMerger()
        merger.add(Image.fromarray(first), cards_for_levels([125] * 21))
        merger.advance(Image.fromarray(second))
        previous, offset, slots = merger.previous, merger.offset, copy.deepcopy(merger.slots)
        unrelated = np.random.default_rng(42).integers(30, 230, (720, 1280, 3), dtype=np.uint8)
        with self.assertRaises(ScrollAmbiguityError) as raised:
            merger.advance(Image.fromarray(unrelated))
        self.assertIs(merger.previous, previous)
        self.assertEqual(merger.offset, offset)
        self.assertEqual(merger.slots, slots)
        self.assertEqual(str(raised.exception), '相邻船坞截图没有明确的重叠位移，停止扫描以免漏船')
        self.assertTrue(raised.exception.best_error > 20
                        or raised.exception.competing_error < raised.exception.best_error + 2)

    def test_error_twenty_boundary_is_preserved_and_twenty_one_is_rejected(self):
        rng = np.random.default_rng(20261015)
        content = rng.integers(30, 200, (792, 1140, 3), dtype=np.uint8)
        before = np.zeros((720, 1280, 3), np.uint8)
        before[65:640, 85:1225] = content[:575]
        for error in (20, 21):
            after = np.zeros_like(before)
            after[65:640, 85:1225] = content[217:792] + error
            if error == 20:
                self.assertEqual(estimate_scroll(before, after), 217)
            else:
                with self.assertRaises(ScrollAmbiguityError) as raised:
                    estimate_scroll(before, after)
                self.assertAlmostEqual(raised.exception.best_error, 21)
                self.assertEqual(raised.exception.shift, 217)

    def test_equal_competing_shifts_are_rejected_even_at_an_acceptable_error(self):
        before = np.full((720, 1280, 3), 80, np.uint8)
        after = np.full((720, 1280, 3), 100, np.uint8)
        with self.assertRaises(ScrollAmbiguityError) as raised:
            estimate_scroll(before, after)
        self.assertEqual(raised.exception.best_error, 20)
        self.assertEqual(raised.exception.competing_error, 20)

    def test_real_tail_frames_are_zero_motion_and_known_translation_is_synthetic(self):
        fixture = Path(__file__).parent / 'fixtures' / 'mind_scroll_recovery'
        before = np.array(Image.open(fixture / 'before_tail.png').convert('RGB'))
        after = np.array(Image.open(fixture / 'after_tail.png').convert('RGB'))
        self.assertEqual(estimate_scroll(before, after), 0)
        # 这只是已知平移的真实纹理回放，不是当时缺失的失败截图对。
        translated = after.copy()
        translated[65:423, 85:1225] = before[282:640, 85:1225]
        translated[423:640, 85:1225] = 0
        self.assertEqual(estimate_scroll(before, translated), 217)


class ScrollEvidenceTests(unittest.TestCase):
    def make_scanner(self):
        scanner = MindCalculatorScan.__new__(MindCalculatorScan)
        scanner.config = SimpleNamespace(config_name='testpilot')
        return scanner

    def test_pair_and_json_preserve_scores_offset_and_reference_to_previous_frame(self):
        scanner, merger = self.make_scanner(), ScanMerger()
        previous = Image.new('RGB', (1280, 720), (50, 80, 110))
        current = Image.new('RGB', (1280, 720), (100, 120, 140))
        merger.previous, merger.offset = np.array(previous), 681
        with tempfile.TemporaryDirectory() as temporary:
            with patch('module.retire.mind_scan.logger.log_file', str(Path(temporary) / 'alas.txt')):
                scanner._save_scroll_evidence(merger, current, ambiguity(), 2)
            root = Path(temporary) / 'mind_scan' / 'testpilot'
            files = list(root.glob('*.json'))
            self.assertEqual(len(files), 1)
            metadata = json.loads(files[0].read_text(encoding='utf-8'))
            self.assertEqual(metadata, dict(kind='scroll_overlap_failure', attempt=2, offset=681,
                                            best_error=19.8, competing_error=20.1, candidate_shift=217,
                                            previous_path=metadata['previous_path']))
            np.testing.assert_array_equal(np.array(Image.open(metadata['previous_path'])), np.array(previous))
            np.testing.assert_array_equal(np.array(Image.open(files[0].with_suffix('.png'))), np.array(current))
            self.assertEqual(len(scanner._scan_evidence_paths), 2)
        self.assertEqual(merger.offset, 681)
        np.testing.assert_array_equal(merger.previous, np.array(previous))

    def test_image_or_metadata_write_failure_does_not_mask_the_scroll_failure(self):
        for failure in ('image', 'metadata'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                scanner, merger = self.make_scanner(), ScanMerger()
                merger.previous = np.full((720, 1280, 3), 80, np.uint8)
                error = ambiguity()
                target = 'PIL.Image.Image.save' if failure == 'image' else 'pathlib.Path.write_text'
                with patch('module.retire.mind_scan.logger.log_file', str(Path(temporary) / 'alas.txt')), \
                        patch(target, side_effect=OSError('测试磁盘写入失败')), \
                        patch('module.retire.mind_scan.logger.warning') as warning:
                    scanner._save_scroll_evidence(merger, Image.fromarray(merger.previous), error, 3)
                warning.assert_called()
                self.assertEqual(merger.offset, 0)
                self.assertEqual(error.shift, 217)


if __name__ == '__main__':
    unittest.main()
