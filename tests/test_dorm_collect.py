"""宿舍收取（dorm_collect）等待与日志分级的离线回归测试。

原实现：`Timer(1.5, count=3)`（约 2~4 秒）内既没点到「快捷收取」也没检测到信息栏，
就统一报「收取超时，可能未检测到信息栏」。慢模拟器上按钮还没出现就结束了
（2026-09-28 07:22 实例：按钮一次都没出现），而本来就没东西可收时也会报同一条警告，
看起来像宿舍任务什么都没做。修复后：等待放宽到 4 秒×3，且区分「没东西可收」（info）
与「点了没收成」（warning）。
"""
import unittest

import module.base.timer as timer_module
from module.base.timer import Timer
from module.dorm.dorm import RewardDorm


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def advance(self, seconds):
        self.now += seconds


class StubDorm:
    """绑定 RewardDorm 上被测试的真实方法。

    桩要模拟 `appear_then_click` 的 interval 节流：否则每轮都算「点到」，
    timeout 会被一直 reset，方法就永远不结束。
    """

    dorm_collect = RewardDorm.dorm_collect

    def __init__(self, clock, button_after=None, info_bar_after_click=False, max_iterations=200):
        self.clock = clock
        self.button_after = button_after          # 多少秒后快捷收取按钮才出现；None 表示永不出现
        self.info_bar_after_click = info_bar_after_click
        self.max_iterations = max_iterations
        self.elapsed = 0.0
        self.clicks = 0
        self.last_click_at = -99.0

    def loop(self, skip_first=True, timeout=None):
        # 有界：方法本身跑飞时让测试失败而不是挂住
        for _ in range(self.max_iterations):
            yield None
            self.clock.advance(0.8)
            self.elapsed += 0.8

    def ensure_no_info_bar(self, timeout=1):
        return True

    def ui_additional(self, get_ship=False):
        return False

    def appear_then_click(self, button, offset=0, interval=1):
        if self.button_after is None or self.elapsed < self.button_after:
            return False
        if self.elapsed - self.last_click_at < interval:
            return False
        self.last_click_at = self.elapsed
        self.clicks += 1
        return True

    def info_bar_count(self):
        return 1 if (self.clicks and self.info_bar_after_click) else 0


class DormCollectTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self._real_time = timer_module.time
        timer_module.time = lambda: self.clock.now

    def tearDown(self):
        timer_module.time = self._real_time

    def build(self, **kwargs):
        return StubDorm(self.clock, **kwargs)

    def test_late_button_is_still_collected(self):
        """按钮晚了几秒才出现：仍应点到并正常结束（修复前会超时放弃）。"""
        stub = self.build(button_after=3.2, info_bar_after_click=True)

        stub.dorm_collect()

        self.assertGreaterEqual(stub.clicks, 1, '按钮出现后应当点到快捷收取')
        self.assertLess(stub.elapsed, 30)

    def test_nothing_to_collect_is_not_a_warning(self):
        """按钮始终不出现（没东西可收）：应有界结束，不点任何按钮。"""
        stub = self.build(button_after=None)

        stub.dorm_collect()

        self.assertEqual(stub.clicks, 0)
        self.assertLess(stub.elapsed, 30)

    def test_click_without_info_bar_still_ends(self):
        """点到了但信息栏一直没出现：应有界结束（真正「可能未收取」的情况）。"""
        stub = self.build(button_after=0.0, info_bar_after_click=False)

        stub.dorm_collect()

        self.assertGreaterEqual(stub.clicks, 1)
        self.assertLess(stub.elapsed, 40)


if __name__ == '__main__':
    unittest.main()
