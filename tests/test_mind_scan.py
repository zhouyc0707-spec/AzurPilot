"""船坞 OCR 识别与持续触控自动扫描的离线回归测试。"""
import copy
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from module.api.protocol import MindShip
from module.runtime.mind_recognition import (
    Card, ScanMerger, color_rows, estimate_scroll, level_vote,
    recognize_cards, row_scroll_offset,
)


def ship(name='测试舰', level=100, rarity='SSR', **kwargs):
    return MindShip(name=name, level=level, rarity=rarity, **kwargs).model_dump()


class RecognitionTests(unittest.TestCase):
    def test_digit_count_rejects_lost_hundreds_and_conflicting_readings(self):
        self.assertEqual(level_vote(['Lv.25', 'Lv.125', '125'], 3)[0], 125)
        self.assertTrue(level_vote(['25', '25', '25'], 3)[1])
        self.assertEqual(level_vote(['Lv.120', 'Lv.120', '120'], 3), (120, ''))
        self.assertTrue(level_vote(['100', '101', '100'], 3)[1])
        self.assertEqual(level_vote(['noise', '126', '0'])[0], 0)

    def test_colored_header_follows_scrolling_and_unread_cards_survive(self):
        pixels = np.zeros((720, 1280, 3), dtype=np.uint8)
        pixels[103:107, 93:231] = (235, 185, 60)
        self.assertEqual(color_rows(pixels), [103])
        ocr = Mock()
        ocr.det.return_value = []
        ocr.ocr_for_single_lines.side_effect = lambda regions: [''] * len(regions)
        cards = recognize_cards(Image.fromarray(pixels), name_ocr=ocr, level_ocr=ocr)
        self.assertEqual(len(cards), 1)
        self.assertIn('未识别舰船', cards[0].ship['name'])
        self.assertTrue(cards[0].ship['review'])
        self.assertEqual(cards[0].ship['level'], 0)

    def test_third_row_is_read_and_gray_artwork_is_not_a_card_header(self):
        pixels = np.zeros((720, 1280, 3), dtype=np.uint8)
        for y in (76, 303, 530):
            pixels[y:y + 4, 93:231] = (235, 185, 60)
        # 实际错误现场中的灰色卡面条带位于列内部，不能生成一排虚假舰船。
        pixels[381:383, 444:535] = (190, 190, 190)
        self.assertEqual(color_rows(pixels), [76, 303, 530])
        ocr = Mock()
        ocr.det.return_value = []
        ocr.ocr_for_single_lines.side_effect = lambda regions: [''] * len(regions)
        cards = recognize_cards(Image.fromarray(pixels), name_ocr=ocr, level_ocr=ocr)
        self.assertEqual([card.y for card in cards], [76, 303, 530])

    def test_color_rows_survive_partial_level_detection(self):
        from module.runtime.mind_recognition import CARD_COLUMNS
        pixels = np.zeros((720, 1280, 3), dtype=np.uint8)
        # 色框圆角在不同排有像素偏差；整屏 OCR 可能只检测出其中两排的等级。
        rows = (75, 301, 528)
        for y in rows:
            for x in CARD_COLUMNS:
                pixels[y:y + 4, x:x + 138] = (235, 185, 60)
        for missing in rows:
            with self.subTest(missing=missing):
                ocr = Mock()
                ocr.det.return_value = [
                    ('Lv.120', [[170, y + 5], [218, y + 5], [218, y + 20], [170, y + 20]], .99)
                    for y in rows if y != missing]
                ocr.ocr_for_single_lines.side_effect = lambda regions: [''] * len(regions)
                cards = recognize_cards(Image.fromarray(pixels), name_ocr=ocr, level_ocr=ocr)
                self.assertEqual(sorted({card.y for card in cards}), list(rows))
                self.assertEqual(len(cards), 21)
                self.assertEqual(sum(card.y == missing for card in cards), 7)

        # 显式指定裁剪行时仍只读取调用者选择的行。
        cards = recognize_cards(Image.fromarray(pixels), name_ocr=ocr, level_ocr=ocr, row_origins=[301])
        self.assertEqual(len(cards), 7)
        self.assertEqual({card.y for card in cards}, {301})

    def test_row_alignment_corrects_accumulated_drift_with_a_clipped_first_row(self):
        pixels = np.zeros((720, 1280, 3), dtype=np.uint8)
        for y in (42, 268, 495):
            pixels[y:y + 4, 93:231] = (235, 185, 60)
        self.assertEqual(color_rows(pixels), [268, 495])
        self.assertEqual(row_scroll_offset(pixels, 75, 227, 679), 715)
        # 校准方向也支持少拖：第三排船名接近画面下边缘时，只用上面两排确认偏差。
        pixels[:] = 0
        for y in (86, 313, 540):
            pixels[y:y + 4, 93:231] = (235, 185, 60)
        self.assertEqual(row_scroll_offset(pixels, 75, 227, 681), 670)
        pixels[313:317] = 0
        self.assertIsNone(row_scroll_offset(pixels, 75, 227, 681))

    @staticmethod
    def scroll_frames(offsets):
        rng = np.random.default_rng(20261010)
        # 随机纹理模拟不同卡面，原图提供超过一屏的内容以核对实际位移。
        content = rng.integers(30, 230, (max(offsets) + 575, 1140, 3), dtype=np.uint8)
        frames = []
        for offset in offsets:
            frame = np.zeros((720, 1280, 3), dtype=np.uint8)
            frame[65:640, 85:1225] = content[offset:offset + 575]
            frames.append(frame)
        return frames

    @staticmethod
    def scroll_images():
        return RecognitionTests.scroll_frames([0, 227])

    def test_spatial_merge_keeps_distinct_copies_and_best_overlap(self):
        first, second = self.scroll_images()
        self.assertEqual(estimate_scroll(first, second), 227)
        self.assertEqual(estimate_scroll(second, first), -227)
        merger = ScanMerger()
        merger.add(Image.fromarray(first), [Card(93, 76, 0, ship('拉菲', 100), 6),
                                            Card(93, 303, 0, ship('拉菲', 100), 1)])
        merger.add(Image.fromarray(second), [Card(93, 76, 0, ship('拉菲', 101), 6),
                                             Card(93, 303, 0, ship('拉菲', 100), 6)])
        self.assertEqual(len(merger.ships()), 3)
        self.assertEqual(merger.ships()[1]['level'], 101)
        self.assertIn('跨屏读数不一致', merger.ships()[1]['source'])
        # 相同截图是零位移，重试截图不增加卡片。
        merger.add(Image.fromarray(second), [Card(93, 76, 0, ship('拉菲', 101), 6)])
        self.assertEqual(len(merger.ships()), 3)

    def test_unrelated_screens_are_not_guessed_as_a_scroll(self):
        first, _ = self.scroll_images()
        other = np.random.default_rng(42).integers(30, 230, (720, 1280, 3), dtype=np.uint8)
        with self.assertRaises(ValueError):
            estimate_scroll(first, other)

    def test_three_row_pages_merge_using_intermediate_scroll_evidence(self):
        frames = self.scroll_frames([0, 227, 454, 681])
        merger = ScanMerger()
        cards = [Card(93, y, 0, ship('拉菲'), 6) for y in (76, 303, 530)]
        merger.add(Image.fromarray(frames[0]), copy.deepcopy(cards))
        for frame in frames[1:]:
            merger.advance(Image.fromarray(frame))
        merger.add(Image.fromarray(frames[-1]), copy.deepcopy(cards))
        self.assertEqual(merger.offset, 681)
        self.assertEqual(len(merger.ships()), 6)

    def scan_live(self, *, gain=8, bottom_offset=1362, initial_offset=0, rows=(76, 303, 530), overshoot=False,
                  animate=False, fail=False, marker_delay=0, blocked=False, rounded=False, lost_once=False):
        from module.retire.mind_scan import MindCalculatorScan
        from types import SimpleNamespace
        class FrameTimer:
            def __init__(self, limit, *args, **kwargs):
                self.limit = limit
                self.frame = 0
            def start(self):
                return self.reset()
            def reset(self):
                self.frame = device.frames
                return self
            def reached(self):
                return device.frames - self.frame >= max(1, round(self.limit * 5))
        class DockDevice:
            def __init__(self):
                self.content = np.random.default_rng(20261010).integers(
                    30, 230, (bottom_offset + 575, 1140, 3), dtype=np.uint8)
                self.offset = self.goal = initial_offset
                self.frames = 0
                self.active = self.armed = False
                self.point = None
                self.events = []
                self.glide_goal = None
                self.speeds = []
                self.tracked_while_pressed = []
                self.click_record_clear = Mock()
                self.stuck_record_clear = Mock()
                self.screenshot = Mock(side_effect=self.capture)
                self.drag = Mock(side_effect=AssertionError('不能使用松手后才返回的拖拽'))
                self.swipe = Mock(side_effect=AssertionError('不能使用整段滑动'))
                self.overshoot = overshoot
            def live_drag(self, **kwargs):
                return self
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.up()
            def down(self, point):
                assert not self.active
                self.active, self.armed = True, False
                self.point = self.anchor = point
                self.mode = 'bar' if point[0] > 1230 else 'fine'
                self.marker_frames = marker_delay if self.mode == 'bar' else 0
                self.events.append(('down', self.mode, point))
            def move(self, point):
                self.hold()
                self._move(point)
            def _move(self, point):
                assert self.active
                self.events.append(('move', self.mode, point))
                if not self.armed:
                    self.armed = np.linalg.norm(np.subtract(point, self.anchor)) >= 20
                if self.armed:
                    delta = point[1] - self.point[1]
                    change = delta * gain if self.mode == 'bar' else -delta
                    if change and self.mode == 'fine' and self.overshoot:
                        change += 9
                        self.overshoot = False
                    self.goal = max(0, min(bottom_offset, self.goal + change))
                self.point = point
            def glide(self, point, speed):
                self.glide_goal, self.glide_speed = point, speed
                self.speeds.append(speed)
            def hold(self):
                self.glide_goal = None
            def check_error(self):
                pass
            def up(self):
                if self.active:
                    self.hold()
                    assert self.armed, '未跨过拖动阈值就松手会变成点击'
                    self.events.append(('up', self.mode, self.point))
                    self.active = False
            def capture(self):
                self.frames += 1
                assert self.frames < 1000, '实时定位没有结束'
                if self.active and self.glide_goal is not None:
                    # 每帧间隔内触点逐像素移动；截图读到的可以是尚未到达目标的中途位置。
                    for _ in range(max(1, int(self.glide_speed * .2))):
                        if self.point == self.glide_goal:
                            break
                        self._move(tuple(value + (1 if goal > value else -1 if goal < value else 0)
                                         for value, goal in zip(self.point, self.glide_goal)))
                if animate and self.goal != self.offset:
                    delta = self.goal - self.offset
                    self.offset += int(np.sign(delta) * max(1, abs(delta) // 2))
                else:
                    self.offset = self.goal
                image = np.zeros((720, 1280, 3), dtype=np.uint8)
                image[65:640, 85:1225] = self.content[self.offset:self.offset + 575]
                for index in range(bottom_offset // 227 + 4):
                    y = index // 3 * 681 + rows[index % 3] - self.offset
                    if 55 <= y <= 716:
                        image[y:y + 4, 93:231] = (235, 185, 60)
                start = 545 if self.offset == bottom_offset else max(0, min(544, round(self.offset / gain)))
                image[76 + start:96 + start, 1239:1248] = (247, 211, 66)
                if self.active:
                    # 游戏触控菱形会盖住滑块；模拟旧触点特效残留以及持续遮挡。
                    point = self.anchor if self.marker_frames else self.point
                    self.marker_frames = max(0, self.marker_frames - 1)
                    x, y = point
                    yy, xx = np.ogrid[max(0, y - 32):min(720, y + 33), max(0, x - 32):min(1280, x + 33)]
                    distance = abs(xx - x) + abs(yy - y)
                    region = image[max(0, y - 32):min(720, y + 33), max(0, x - 32):min(1280, x + 33)]
                    region[(distance >= 28) & (distance <= 31)] = (0, 255, 255)
                    if blocked:
                        image[76 + start + 8:76 + start + 12, 1239:1248] = (0, 255, 255)
                self.image = image
                if self.active:
                    self.tracked_while_pressed.append(self.offset)
                return image
        device = DockDevice()
        scanner = MindCalculatorScan.__new__(MindCalculatorScan)
        scanner.config = SimpleNamespace(Emulator_ControlMethod='MaaTouch')
        scanner.device = device
        scanner.appear = Mock(return_value=True)
        def thumb(_):
            # 船多时滑块不足通用 appear() 的 10% 门槛，仍须支持连续扫描。
            return np.any(np.all(device.image[76:641, 1239:1248] == (247, 211, 66), axis=2), axis=1)
        scrollbar = SimpleNamespace(area=(1239, 76, 1248, 641), total=565, match_color=thumb,
                                    color=(247, 211, 66), color_threshold=221)
        captured = []
        def recognize(*args, **kwargs):
            self.assertFalse(device.active, '整页 OCR 必须在已定位并松手后进行')
            captured.append(device.offset)
            if fail and len(captured) == 2:
                raise ValueError('识别中断')
            cards = []
            for index in range(bottom_offset // 227 + 4):
                absolute_y = index // 3 * 681 + rows[index % 3]
                y = absolute_y - device.offset
                if 65 <= y <= 537:
                    cards.append(Card(93, y, 0, ship('拉菲'), 6))
            return cards
        lost = False
        def measure(previous, current):
            nonlocal lost
            if lost_once and not lost and device.active and device.mode == 'bar' and device.offset > 100:
                lost = True
                raise ValueError('模拟快拖失去重叠')
            measured = estimate_scroll(previous, current)
            # 实际画面有亚像素重采样，逐帧整数匹配会累积取整误差。
            return measured - int(np.sign(measured)) if rounded and measured else measured
        with patch('module.retire.mind_scan.Timer', FrameTimer), patch('module.retire.mind_scan.DOCK_SCROLL', scrollbar), \
                patch('module.retire.mind_scan.recognize_cards', side_effect=recognize), \
                patch('module.runtime.mind_recognition.estimate_scroll', side_effect=measure), \
                patch('module.retire.mind_scan.logger'):
            result = scanner._scan_pages(Mock(), Mock())
        return scanner, captured, result

    def test_scan_tracks_pixels_before_releasing_scrollbar(self):
        scanner, captured, result = self.scan_live()
        self.assertTrue(all(abs(actual - expected) <= 3 for actual, expected in zip(captured, [0, 681, 1362])))
        self.assertEqual(len(result), 9)
        self.assertGreater(len(set(scanner.device.tracked_while_pressed)), 5)
        self.assertLess(scanner.device.frames, 100)
        self.assertTrue(any(abs(after - before) >= 100 for before, after in zip(
            scanner.device.tracked_while_pressed, scanner.device.tracked_while_pressed[1:])))
        self.assertTrue(any(event[:2] == ('down', 'bar') for event in scanner.device.events))
        self.assertFalse(scanner.device.active)
        scanner.device.drag.assert_not_called()
        scanner.device.swipe.assert_not_called()
        self.assertGreater(scanner.device.stuck_record_clear.call_count, 5)
        self.assertEqual(scanner.device.stuck_record_clear.call_count, scanner.device.click_record_clear.call_count)
        self.assertGreater(max(scanner.device.speeds), 4)
        # 截图帧率不控制触点移动步长；每次实际纵向注入最多一像素。
        previous = None
        for operation, mode, point in scanner.device.events:
            if operation == 'down':
                previous = point
            elif operation == 'move':
                self.assertLessEqual(abs(point[1] - previous[1]), 1)
                previous = point

    def test_live_scrollbar_moves_touch_effect_outside_color_detection(self):
        scanner, captured, result = self.scan_live(gain=300, marker_delay=2)
        self.assertTrue(all(abs(actual - expected) <= 3 for actual, expected in zip(captured, [0, 681, 1362])))
        self.assertEqual(len(result), 9)
        # 抓住滑块后纵向移动期间，特效的最右边仍在黄色滑块左侧。
        bar_moves = [point for operation, mode, point in scanner.device.events if (operation, mode) == ('move', 'bar')]
        self.assertTrue(bar_moves)
        self.assertTrue(all(point[0] + 32 < 1239 for point in bar_moves))

    def test_persistent_scrollbar_occlusion_still_aborts(self):
        from module.exception import MindCalculatorScanError
        with self.assertRaisesRegex(MindCalculatorScanError, '持续被遮挡'):
            self.scan_live(blocked=True)

    def test_color_band_offsets_do_not_accumulate_into_three_row_distance(self):
        _, captured, result = self.scan_live(rows=(75, 301, 528), gain=19)
        self.assertTrue(all(abs(actual - expected) <= 3 for actual, expected in zip(captured, [0, 681, 1362])))
        self.assertEqual(captured[-1], 1362)
        self.assertEqual(len(result), 9)

    def test_scan_corrects_small_overshoot_with_same_finger_and_reads_last_page(self):
        scanner, captured, result = self.scan_live(gain=19, bottom_offset=908, overshoot=True)
        self.assertTrue(all(abs(actual - expected) <= 3 for actual, expected in zip(captured, [0, 681, 908])))
        self.assertEqual(captured[-1], 908)
        self.assertEqual(len(result), 7)
        strokes = []
        for operation, mode, point in scanner.device.events:
            if mode != 'fine':
                continue
            if operation == 'down':
                strokes.append([])
            strokes[-1].append((operation, point))
        # 首次精调在一个触点内既向上移动又反向校正，不再松手重发 9px 短拖动。
        vertical = [point[1] for operation, point in strokes[0] if operation in ('down', 'move')]
        self.assertLess(min(vertical), vertical[0])
        self.assertGreater(vertical[-1], min(vertical))
        self.assertEqual(sum(operation == 'up' for operation, _ in strokes[0]), 1)

    def test_large_scrollbar_gain_still_has_overlap_and_exact_three_row_goals(self):
        scanner, captured, result = self.scan_live(gain=300)
        self.assertTrue(all(abs(actual - expected) <= 3 for actual, expected in zip(captured, [0, 681, 1362])))
        self.assertEqual(len(result), 9)
        # 未知倍率下快拖失去重叠，返回原触点并减速，再继续扫描。
        vertical = [point[1] for operation, mode, point in scanner.device.events if operation == 'move' and mode == 'bar']
        self.assertTrue(any(after < before for before, after in zip(vertical, vertical[1:])))
        self.assertIn(300, scanner.device.tracked_while_pressed)

    def test_near_goal_uses_card_rows_to_remove_frame_rounding_drift(self):
        scanner, captured, result = self.scan_live(gain=9, rounded=True)
        self.assertTrue(all(abs(actual - expected) <= 3 for actual, expected in zip(captured, [0, 681, 1362])))
        self.assertEqual(len(result), 9)
        self.assertFalse(scanner.device.active)

    def test_fast_motion_recovers_overlap_without_releasing_the_scrollbar(self):
        scanner, captured, result = self.scan_live(lost_once=True)
        self.assertTrue(all(abs(actual - expected) <= 3 for actual, expected in zip(captured, [0, 681, 1362])))
        self.assertEqual(len(result), 9)
        strokes = []
        for operation, mode, point in scanner.device.events:
            if mode == 'bar' and operation == 'down':
                strokes.append([])
            if mode == 'bar' and operation == 'move':
                strokes[-1].append(point[1])
        self.assertTrue(any(any(after < before for before, after in zip(stroke, stroke[1:])) for stroke in strokes[1:]))

    def test_near_top_is_rewound_exactly_before_scan(self):
        _, captured, result = self.scan_live(initial_offset=45, gain=9)
        self.assertEqual(captured[0], 0)
        self.assertEqual(len(result), 9)

    def test_motion_frames_are_tracked_while_finger_is_down(self):
        scanner, captured, result = self.scan_live(gain=300, animate=True)
        self.assertTrue(all(abs(actual - expected) <= 3 for actual, expected in zip(captured, [0, 681, 1362])))
        self.assertEqual(len(result), 9)
        self.assertTrue(any(0 < offset < 300 for offset in scanner.device.tracked_while_pressed))

    def test_adb_is_rejected_without_falling_back_to_clicks(self):
        from module.retire.mind_scan import MindCalculatorScan
        from module.exception import MindCalculatorScanError
        from types import SimpleNamespace
        scanner = MindCalculatorScan.__new__(MindCalculatorScan)
        scanner.config = SimpleNamespace(Emulator_ControlMethod='ADB')
        scanner.device = Mock()
        with self.assertRaisesRegex(MindCalculatorScanError, '持续触控'):
            scanner._scan_pages(Mock(), Mock())
        scanner.device.drag.assert_not_called()
        scanner.device.swipe.assert_not_called()
        scanner.device.screenshot.assert_not_called()

    def test_thumb_center_includes_pixels_above_legacy_button_area(self):
        from module.retire.mind_scan import MindCalculatorScan
        scanner = MindCalculatorScan.__new__(MindCalculatorScan)
        pixels = np.zeros((720, 1280, 3), dtype=np.uint8)
        pixels[67:169, 1239:1248] = (247, 211, 66)
        scanner.device = Mock(image=pixels)
        scanner.image_crop = lambda area, **kwargs: pixels[area[1]:area[3], area[0]:area[2]]
        thumb = scanner._scroll_thumb()
        self.assertEqual((thumb[0], thumb[-1], len(thumb)), (-9, 92, 102))
        self.assertEqual(76 + float(np.mean(thumb)), 117.5)

    def test_scan_recognition_failure_leaves_live_touch_released(self):
        from module.exception import MindCalculatorScanError
        with self.assertRaisesRegex(MindCalculatorScanError, '识别中断'):
            self.scan_live(gain=300, fail=True)


if __name__ == '__main__':
    unittest.main()
