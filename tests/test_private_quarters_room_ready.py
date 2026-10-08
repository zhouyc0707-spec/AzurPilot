"""私人休息室入房就绪检测的离线回归，不连接或操作真实设备。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.base.timer import Timer
from module.private_quarters.interact import PQInteract
from module.private_quarters.private_quarters import PrivateQuarters


ROOM = 'PRIVATE_QUARTERS_ROOM_CHECK'
LOADING = 'PRIVATE_QUARTERS_LOADING_CHECK'
BUBBLES = tuple(f'PRIVATE_QUARTERS_ROOM_TARGET_CHECK_{number}' for number in (1, 2, 3))


class FakeClock:
    """使用非零起点，与真实 Timer 的启动约定一致。"""

    def __init__(self):
        self.value = 100.0

    def now(self):
        return self.value


class FakeDevice:
    """截图推进假时钟；操作只记录，点击和固定休眠直接报错。"""

    def __init__(self, clock, frames, screenshot_seconds=0.5, reveal_after_drag=None):
        self.clock = clock
        self.frames = frames
        self.screenshot_seconds = screenshot_seconds
        self.reveal_after_drag = reveal_after_drag
        self.frame = 0
        self.image = frames[0]
        self.drags = []

    def screenshot(self):
        self.clock.value += self.screenshot_seconds
        self.frame += 1
        self.image = self.frames[min(self.frame, len(self.frames) - 1)]
        if self.reveal_after_drag and self.drags:
            self.image = {ROOM, self.reveal_after_drag}

    def drag(self, p1, p2, **kwargs):
        self.drags.append((self.clock.value, self.frame, p1, p2, kwargs))

    def click(self, *args, **kwargs):
        raise AssertionError('就绪检测不得点击未知画面或调用真实设备')

    def sleep(self, *args, **kwargs):
        raise AssertionError('就绪检测必须通过截图等待，不能固定休眠')


class StubPrivateQuarters(PrivateQuarters):
    """保留真实继承入口，只替换截图、识别和房间导航。"""

    def __init__(self, frames, screenshot_seconds=0.5, reveal_after_drag=None):
        self.clock = FakeClock()
        self.device = FakeDevice(self.clock, frames, screenshot_seconds, reveal_after_drag)
        self.config = SimpleNamespace()
        self.checked = []
        self.loop_timeouts = []
        self._pq_goto_room_seek = Mock(return_value=True)
        self._pq_goto_room_enter = Mock(return_value=True)
        self._pq_goto_room_exit = Mock()

    def appear(self, button, **kwargs):
        self.checked.append((button.name, self.device.frame, kwargs))
        return button.name in self.device.image

    def loop(self, skip_first=True, timeout=None):
        # 按 Base.loop 的 Timer 语义推进；硬上限只用于揭露意外无限循环。
        if not isinstance(timeout, Timer):
            raise AssertionError('总等待必须使用显式 Timer，避免截图计数延长超时')
        timeout.reset()
        self.loop_timeouts.append(timeout)
        for _ in range(200):
            if timeout.reached():
                return
            if skip_first:
                skip_first = False
            else:
                self.device.screenshot()
            yield self.device.image
        raise AssertionError('就绪等待超过离线循环上限')

    def _pq_handle_dialogue(self):
        raise AssertionError('入房已经处理对话，就绪检测不得嵌套对话循环')


class PrivateQuartersRoomReadyTest(unittest.TestCase):
    def run_ready(self, stub):
        with patch('module.base.timer.time', side_effect=stub.clock.now):
            return stub._pq_target_appear()

    def test_each_bubble_is_sufficient_and_uses_large_offset(self):
        """三种气泡分别出现时都可确认，且立即复用当前截图。"""
        for bubble in BUBBLES:
            with self.subTest(bubble=bubble):
                stub = StubPrivateQuarters([{ROOM, bubble}])
                self.assertTrue(self.run_ready(stub))
                checks = [(name, kwargs) for name, _, kwargs in stub.checked if name in BUBBLES]
                self.assertTrue(any(name == bubble for name, _ in checks))
                self.assertTrue(all(kwargs.get('offset') == (100, 100) for _, kwargs in checks))
                self.assertTrue(all(not kwargs.get('interval') for _, kwargs in checks))
                self.assertEqual(stub.device.frame, 0)
                self.assertEqual(stub.device.drags, [])

    def test_private_quarters_entry_uses_inherited_ready_method(self):
        """复现实际 pq_goto_room 调用链，防止只补测试替身而漏掉继承方法。"""
        self.assertIs(PrivateQuarters._pq_target_appear, PQInteract._pq_target_appear)
        stub = StubPrivateQuarters([{ROOM, BUBBLES[0]}])
        with patch('module.base.timer.time', side_effect=stub.clock.now):
            self.assertTrue(stub.pq_goto_room('taihou'))
        stub._pq_goto_room_seek.assert_called_once_with('taihou')
        stub._pq_goto_room_enter.assert_called_once_with('taihou')
        stub._pq_goto_room_exit.assert_not_called()

    def test_drag_is_followed_by_new_screenshot_before_success(self):
        """调整镜头后才出现气泡，必须获取新截图才能确认。"""
        stub = StubPrivateQuarters([{ROOM}], reveal_after_drag=BUBBLES[2])
        with patch('module.private_quarters.interact.random_rectangle_vector',
                   return_value=((1100, 300), (1100, 270))) as vector:
            self.assertTrue(self.run_ready(stub))
        self.assertEqual(len(stub.device.drags), 1)
        self.assertGreater(stub.device.frame, stub.device.drags[0][1])
        bubble_frames = [frame for name, frame, _ in stub.checked if name == BUBBLES[2]]
        self.assertGreater(bubble_frames[-1], stub.device.drags[0][1])
        self.assertEqual(vector.call_args.args, ((0, -30),))
        self.assertEqual(stub.device.drags[0][2:4], ((1100, 300), (1100, 270)))

    def test_loading_has_priority_over_stale_room_or_bubble_marker(self):
        """加载中即使留有旧房间标记，也只等待新截图。"""
        stub = StubPrivateQuarters([{LOADING, ROOM, BUBBLES[0]}, {ROOM, BUBBLES[0]}])
        self.assertTrue(self.run_ready(stub))
        self.assertEqual(stub.device.frame, 1)
        self.assertFalse(any(name in BUBBLES and frame == 0 for name, frame, _ in stub.checked))
        self.assertEqual(stub.device.drags, [])

    def test_persistent_loading_or_unknown_screen_never_operates(self):
        """加载及无法识别的画面超时返回 False，不盲点或拖动。"""
        for markers in ({LOADING, ROOM}, set()):
            with self.subTest(markers=markers):
                stub = StubPrivateQuarters([markers])
                self.assertFalse(self.run_ready(stub))
                self.assertEqual(stub.device.drags, [])
                self.assertGreater(stub.device.frame, 0)
                self.assertLessEqual(stub.clock.value - 100.0, 8.5)

    def test_unknown_screen_can_become_ready_on_next_screenshot(self):
        stub = StubPrivateQuarters([set(), {ROOM, BUBBLES[1]}])
        self.assertTrue(self.run_ready(stub))
        self.assertEqual(stub.device.frame, 1)
        self.assertEqual(stub.device.drags, [])

    def test_room_without_bubble_times_out_despite_repeated_drags(self):
        """多次纠偏不能刷新八秒总期限，也不能缩短拖动间隔。"""
        stub = StubPrivateQuarters([{ROOM}])
        self.assertFalse(self.run_ready(stub))
        self.assertEqual(len(stub.loop_timeouts), 1)
        self.assertEqual(stub.loop_timeouts[0].limit, 8)
        self.assertEqual(stub.loop_timeouts[0].count, 0)
        self.assertGreaterEqual(len(stub.device.drags), 3)
        self.assertLessEqual(len(stub.device.drags), 6)
        self.assertLessEqual(stub.clock.value - 100.0, 8.5)
        moments = [moment for moment, *_ in stub.device.drags]
        self.assertTrue(all(right - left >= 1.5 for left, right in zip(moments, moments[1:])))

    def test_slow_screenshots_do_not_extend_timeout_to_many_frames(self):
        """慢设备允许最后一张截图跨越期限，但不额外等十几帧。"""
        stub = StubPrivateQuarters([set()], screenshot_seconds=3)
        self.assertFalse(self.run_ready(stub))
        self.assertLessEqual(stub.device.frame, 3)
        self.assertLessEqual(stub.clock.value - 100.0, 11)
        self.assertEqual(stub.device.drags, [])

    def test_bubble_after_deadline_is_not_used(self):
        stub = StubPrivateQuarters([{ROOM}] * 18 + [{ROOM, BUBBLES[0]}])
        self.assertFalse(self.run_ready(stub))
        self.assertLess(stub.device.frame, 18)

    def test_unready_room_retries_exactly_three_times(self):
        """真实就绪检测一直失败时，原入口只重试三轮并返回 False。"""
        stub = StubPrivateQuarters([set()])
        with patch('module.base.timer.time', side_effect=stub.clock.now):
            self.assertFalse(stub.pq_goto_room('taihou'))
        self.assertEqual(stub._pq_goto_room_enter.call_count, 3)
        self.assertEqual(stub._pq_goto_room_exit.call_count, 3)
        self.assertEqual(len(stub.loop_timeouts), 3)
        self.assertLessEqual(stub.clock.value - 100.0, 25.5)

    def test_retry_count_is_respected_and_success_stops_retries(self):
        for retry in (0, 1, 2):
            with self.subTest(retry=retry):
                stub = StubPrivateQuarters([set()])
                with patch.object(stub, '_pq_target_appear', return_value=False) as ready:
                    self.assertFalse(stub.pq_goto_room('taihou', retry=retry))
                self.assertEqual(ready.call_count, retry)
                self.assertEqual(stub._pq_goto_room_enter.call_count, retry)
                self.assertEqual(stub._pq_goto_room_exit.call_count, retry)
        stub = StubPrivateQuarters([set()])
        with patch.object(stub, '_pq_target_appear', side_effect=[False, True]) as ready:
            self.assertTrue(stub.pq_goto_room('taihou'))
        self.assertEqual(ready.call_count, 2)
        self.assertEqual(stub._pq_goto_room_enter.call_count, 2)
        self.assertEqual(stub._pq_goto_room_exit.call_count, 1)

    def test_entry_failure_does_not_check_ready_or_reenter(self):
        stub = StubPrivateQuarters([set()])
        stub._pq_goto_room_enter.return_value = False
        with patch.object(stub, '_pq_target_appear') as ready:
            self.assertFalse(stub.pq_goto_room('taihou'))
        stub._pq_goto_room_enter.assert_called_once_with('taihou')
        ready.assert_not_called()
        stub._pq_goto_room_exit.assert_not_called()

    def test_seek_failure_does_not_enter_room(self):
        stub = StubPrivateQuarters([set()])
        stub._pq_goto_room_seek.return_value = False
        self.assertFalse(stub.pq_goto_room('taihou'))
        stub._pq_goto_room_enter.assert_not_called()
        stub._pq_goto_room_exit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
