"""进入岛屿管理页入口按钮点击行为的离线回归测试。

`goto_management()` 在非岛屿页面时会先导航到岛屿页，再点击右上角“管理”入口
（ISLAND_GOTO_MANAGEMENT）。原实现只判断页面（ISLAND_CHECK）就点击，没有任何
间隔限制，云机场景转场慢时会在同一入口按钮上反复点击。管理入口须在连续新截图中
稳定可见后才点击，两次点击仍遵守 ISLAND_ENTRY_RETRY_WAIT，管理页出现即停止。
"""
import unittest
from types import SimpleNamespace

import numpy as np

import module.base.timer as timer_module
from module.base.timer import Timer
from module.base.utils import load_image
from module.exception import GameStuckError
from module.island.island import (
    ISLAND_CHECK,
    ISLAND_ENTRY_RETRY_WAIT,
    ISLAND_GOTO_MANAGEMENT,
    ISLAND_MANAGEMENT_CHECK,
    Island,
)
from module.ui.ui import UI


def build_frame(*assets):
    """叠加资源图，还原“页面上存在这些元素”的测试截图。"""
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    for asset in assets:
        frame = np.maximum(frame, load_image(asset.file))
    return frame


# 岛屿页面（右上角管理入口可见）
ISLAND_PAGE = build_frame(ISLAND_CHECK, ISLAND_GOTO_MANAGEMENT)
# 场景顶部图标已出现，但右上角管理入口仍未完成加载。
LOADING_PAGE = build_frame(ISLAND_CHECK)
# 岗位管理页面（已到达目标）
POSTMANAGE_PAGE = build_frame(ISLAND_MANAGEMENT_CHECK)


class FakeClock:
    def __init__(self):
        # 起点取正值：Timer.start() 以 _start <= 0 判定“未启动”
        self.now = 1000.0

    def advance(self, seconds):
        self.now += seconds


class FakeDevice:
    """按时间线提供截图，截图与点击都推进虚拟时间。"""

    def __init__(self, clock, timeline, screenshot_cost=0.4, click_cost=0.2, max_screenshots=100):
        self.clock = clock
        self.t0 = clock.now
        self.timeline = timeline
        self.screenshot_cost = screenshot_cost
        self.click_cost = click_cost
        self.image = timeline(0.0)
        self.clicks = []
        self.screenshot_count = 0
        self.max_screenshots = max_screenshots

    def screenshot(self):
        self.screenshot_count += 1
        if self.screenshot_count > self.max_screenshots:
            raise AssertionError("超过离线截图上限，状态循环未按预期结束")
        self.clock.advance(self.screenshot_cost)
        self.image = self.timeline(self.clock.now - self.t0)
        return self.image

    def click(self, button, control_check=True):
        self.clicks.append((round(self.clock.now - self.t0, 2), button.name))
        self.clock.advance(self.click_cost)

    def sleep(self, seconds):
        raise AssertionError("入口确认应由持续截图驱动，不应固定休眠")

    def stuck_record_add(self, button):
        pass


class StubIsland:
    """绑定真实 goto_management 的测试替身。"""

    goto_management = Island.goto_management

    # read_run_param 对 None config 回退默认值,测试默认使用内置节奏
    config = None
    def __init__(self, device, current_page="page_main"):
        self.device = device
        self.interval_timer = {}
        self.current_page = current_page
        self.ui_goto_calls = []

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

    def appear(self, button, offset=0, interval=0, similarity=0.85, threshold=10):
        if offset:
            return bool(button.match(self.device.image, offset=offset, similarity=similarity))
        return bool(button.appear_on(self.device.image, threshold=threshold))

    def match_template_color(self, button, offset=(20, 20), interval=0, similarity=0.85, threshold=30):
        return bool(button.match_template_color(
            self.device.image, offset=offset, similarity=similarity, threshold=threshold))

    def ui_island_management_entry_ready(self, confirm_timer):
        return UI.ui_island_management_entry_ready(self, confirm_timer)

    def ui_get_current_page(self, *args, **kwargs):
        return SimpleNamespace(name=self.current_page)

    def ui_goto(self, destination, get_ship=True, **kwargs):
        self.ui_goto_calls.append(str(destination))

    def ui_additional(self, get_ship=True):
        return False


class GotoManagementTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self._real_time = timer_module.time
        timer_module.time = lambda: self.clock.now

    def tearDown(self):
        timer_module.time = self._real_time

    def make_stub(self, timeline, current_page="page_main"):
        device = FakeDevice(self.clock, timeline)
        return StubIsland(device, current_page=current_page), device

    def test_entry_click_keeps_interval_until_page_switches(self):
        """岛屿场景转场期间不得反复点击入口按钮，转场完成后立即停止。"""
        stub, device = self.make_stub(lambda t: ISLAND_PAGE if t < 5.0 else POSTMANAGE_PAGE)

        stub.goto_management()

        names = [name for _, name in device.clicks]
        self.assertTrue(names, "应当点击过管理入口按钮")
        self.assertEqual(set(names), {"ISLAND_GOTO_MANAGEMENT"})
        timestamps = [stamp for stamp, _ in device.clicks]
        for previous, current in zip(timestamps, timestamps[1:]):
            self.assertGreaterEqual(current - previous, ISLAND_ENTRY_RETRY_WAIT)

    def test_entry_click_is_bounded_when_page_never_switches(self):
        """始终没进入管理页时按间隔补点，不会每轮都点，超时抛 GameStuckError。"""
        stub, device = self.make_stub(lambda t: ISLAND_PAGE)

        with self.assertRaises(GameStuckError):
            stub.goto_management()

        clicks = [stamp for stamp, _ in device.clicks]
        self.assertGreaterEqual(len(clicks), 2)
        self.assertLessEqual(len(clicks), 8)
        for previous, current in zip(clicks, clicks[1:]):
            self.assertGreaterEqual(current - previous, ISLAND_ENTRY_RETRY_WAIT)

    def test_skips_entry_click_when_already_in_island_page(self):
        """已在岛屿相关页面时直接走 ui_goto，不点击管理入口按钮。"""
        stub, device = self.make_stub(lambda t: ISLAND_PAGE, current_page="page_island")

        stub.goto_management()

        self.assertEqual(device.clicks, [])
        self.assertEqual(stub.ui_goto_calls, ["page_island_management"])

    def test_custom_retry_interval_is_preserved(self):
        """新增入口确认不覆盖用户配置的补点间隔。"""
        stub, device = self.make_stub(lambda t: ISLAND_PAGE if t < 9.0 else POSTMANAGE_PAGE)
        stub.config = SimpleNamespace(SERVER='cn', UiWait_IslandEntryRetryWait=5.0)
        stub.goto_management()

        timestamps = [stamp for stamp, _ in device.clicks]
        self.assertEqual(len(timestamps), 2)
        self.assertGreaterEqual(timestamps[1] - timestamps[0], 5.0)

    def test_page_icon_without_entry_never_clicks(self):
        """仅识别到岛屿页不足以判定可操作管理入口。"""
        stub, device = self.make_stub(lambda t: LOADING_PAGE)

        with self.assertRaises(GameStuckError):
            stub.goto_management()

        self.assertEqual(device.clicks, [])

    def test_transient_entry_during_loading_does_not_click(self):
        """入口短暂闪现后消失，应从最终稳定画面重新确认。"""
        def timeline(t):
            if t < 0.9:
                return ISLAND_PAGE
            if t < 2.0:
                return LOADING_PAGE
            if t < 3.7:
                return ISLAND_PAGE
            return POSTMANAGE_PAGE

        stub, device = self.make_stub(timeline)
        stub.goto_management()

        self.assertEqual(len(device.clicks), 1)
        self.assertGreater(device.clicks[0][0], 3.0)

    def test_stable_entry_clicks_once_then_stops_on_management(self):
        """稳定入口点击一次后，下一张管理页截图即完成导航。"""
        stub, device = self.make_stub(lambda t: ISLAND_PAGE if t < 1.8 else POSTMANAGE_PAGE)
        stub.goto_management()

        self.assertEqual(device.clicks, [(1.6, "ISLAND_GOTO_MANAGEMENT")])
        self.assertEqual(device.screenshot_count, 5)

    def test_management_page_arrives_before_entry_ready(self):
        """已到管理页不应补点，即使入口确认尚未完成。"""
        stub, device = self.make_stub(lambda t: POSTMANAGE_PAGE)
        stub.goto_management()

        self.assertEqual(device.clicks, [])
        self.assertEqual(device.screenshot_count, 1)

    def test_popup_handled_resets_entry_confirmation(self):
        """关闭弹窗后需使用新截图重新确认按钮稳定。"""
        stub, device = self.make_stub(lambda t: ISLAND_PAGE if t < 3.0 else POSTMANAGE_PAGE)
        popup_handled = False

        def handle_popup(get_ship=False):
            nonlocal popup_handled
            if not popup_handled and self.clock.now - device.t0 >= 0.79:
                popup_handled = True
                return True
            return False

        stub.ui_additional = handle_popup
        stub.goto_management()

        self.assertTrue(popup_handled)
        self.assertEqual(len(device.clicks), 1)
        self.assertGreater(device.clicks[0][0], 2.0)


if __name__ == "__main__":
    unittest.main()
