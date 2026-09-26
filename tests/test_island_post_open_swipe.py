"""岗位列表滑动定位（post_open 恢复分支）的离线回归测试。

旧实现在恢复时无论目标岗位在哪都按 swipe_count 下滑一步：目标正好在列表顶部时
（例如餐厅 ISLAND_RESTAURANT_POST1）会被这一滑推出可视区，而重试次数只有 1 次，
于是干等到 45 秒超时并抛「打开岗位详情超时」（2026-09-20/22/25 三次实例，日志里
retry_swipe_used 始终为 0）。

修复后：先回到列表顶部，**先检测再滑**——顶部就能看到目标岗位时不滑动；
看不到才用 post_manage_swipe_until_appear(min_swipes=0) 边滑边找。
"""
import unittest

import numpy as np

import module.base.timer as timer_module
from module.base.timer import Timer
from module.island.island import Island
from module.island_restaurant.assets import ISLAND_RESTAURANT_POST1

POST = ISLAND_RESTAURANT_POST1


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def advance(self, seconds):
        self.now += seconds


class FakeDevice:
    def __init__(self, clock, appear_after=None):
        self.clock = clock
        self.appear_after = appear_after or 0
        self.image = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.calls = []
        self.swipes = 0

    def screenshot(self):
        self.clock.advance(0.2)
        return self.image

    def click(self, button, control_check=True):
        self.calls.append(('click', getattr(button, 'name', str(button))))
        self.clock.advance(0.1)

    def sleep(self, seconds):
        self.clock.advance(seconds)

    def swipe_vector(self, vector=None, box=None, name=None, **kwargs):
        self.swipes += 1
        self.calls.append(('swipe', name))
        self.clock.advance(0.3)

    def stuck_record_add(self, button):
        return None


class StubIsland:
    """绑定 Island 上被测试的真实方法。"""

    post_open = Island.post_open
    post_manage_swipe_to_top = Island.post_manage_swipe_to_top
    post_manage_swipe_until_appear = Island.post_manage_swipe_until_appear
    post_manage_swipe = Island.post_manage_swipe
    post_manage_up_swipe = Island.post_manage_up_swipe
    post_manage_down_swipe = Island.post_manage_down_swipe

    def __init__(self, device):
        self.device = device
        self.interval_timer = {}
        self.config = type("Config", (), {"BUTTON_OFFSET": 30})()
        self.post_open_retry_swipe = True
        self.post_open_retry_swipe_limit = 1
        self.post_open_full_retry_limit = 1
        self.post_manage_swipe_count = 1
        self.state = {'visible': False, 'swipes_before_visible': 0, 'recovered': False}
        self.swipe_to_top_calls = 0
        self.search_calls = []

    def loop(self, skip_first=True, timeout=None):
        timeout = Timer.from_seconds(timeout).start() if timeout is not None else None
        while 1:
            if timeout is not None and timeout.reached():
                return
            if skip_first:
                skip_first = False
            else:
                self.device.screenshot()
            yield self.device.image

    @staticmethod
    def ensure_button(button):
        return button

    def appear(self, button, offset=0):
        # 只认目标岗位；其余模板一律不命中，模拟“卡在岗位管理页找不到目标”
        return self.state['visible']

    def post_manage_swipe_to_top(self, max_swipes=None):
        self.swipe_to_top_calls += 1
        self.state['visible'] = self.state['swipes_before_visible'] <= 0
        return True

    def post_manage_swipe_until_appear(self, post, min_swipes=1, max_swipes=None, offset=300):
        self.search_calls.append(min_swipes)
        while self.device.swipes < self.state['swipes_before_visible']:
            self.post_manage_up_swipe(100)
        self.state['visible'] = True
        return True

    def interval_clear(self, button):
        return None


class PostOpenRetrySwipeTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self._real_time = timer_module.time
        timer_module.time = lambda: self.clock.now

    def tearDown(self):
        timer_module.time = self._real_time

    def build(self):
        device = FakeDevice(self.clock)
        return device, StubIsland(device)

    def test_top_post_is_not_swiped_away(self):
        """目标岗位就在列表顶部：只回顶部，不得再下滑把它推出可视区。"""
        device, stub = self.build()
        stub.state['swipes_before_visible'] = 0

        stub.post_open(POST)

        self.assertEqual(stub.swipe_to_top_calls, 1)
        self.assertEqual(stub.search_calls, [])          # 顶部已看到 → 不触发下滑搜索
        self.assertEqual(device.swipes, 0)               # 一次下滑都没有

    def test_deep_post_searches_downwards_while_checking(self):
        """目标岗位在列表深处：回顶部后边滑边找，且 min_swipes=0（先看再滑）。"""
        device, stub = self.build()
        stub.state['swipes_before_visible'] = 3

        stub.post_open(POST)

        self.assertEqual(stub.swipe_to_top_calls, 1)
        self.assertEqual(stub.search_calls, [0])
        self.assertGreaterEqual(device.swipes, 3)


if __name__ == "__main__":
    unittest.main()
