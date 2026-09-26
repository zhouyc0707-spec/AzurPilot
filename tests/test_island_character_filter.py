"""角色筛选弹窗卡死的离线回归测试。

2026-09-26 18:31 实例：低帧率下「筛选」弹窗的「确定」点击没生效，弹窗留在屏幕上。
退出流程（back_to_postmanage_from_dispatch / post_close / post_get_and_close）依赖的
模板——角色选择页、岗位详情、产品选择——在那个画面上**全部不命中**，循环于是空转
15 秒不打任何按钮；画面长时间不变又触发了设备级 GameStuckError，整个岛屿任务中止
并重启游戏（错误日志里的卡死截图正是这个筛选弹窗）。

修复后：
1. select_character_filter 点完「确定」会校验弹窗是否真的关闭，没关就重试；
2. 退出流程能识别并关闭该弹窗，保证循环始终有进展，而不是原地空转。
"""
import sys
import unittest
from pathlib import Path

import numpy as np

import module.base.timer as timer_module
from module.base.timer import Timer
from module.island.island import Island
from module.island_select_character.assets import (
    SELECT_CHARACTER_FILTER,
    SELECT_CHARACTER_FILTER_CONFIRM,
    SELECT_CHARACTER_FILTER_STAMINA,
)

CONFIRM = SELECT_CHARACTER_FILTER_CONFIRM.name
STAMINA = SELECT_CHARACTER_FILTER_STAMINA.name
ENTRY = SELECT_CHARACTER_FILTER.name
REPO_ROOT = Path(__file__).resolve().parents[1]

_frames = {}


def read_image(relative):
    """按绝对路径直连 PIL 读取资源。

    刻意不用 module.base.utils.load_image：全量测试里某些模块跑过之后，
    那个函数对完好的 PNG 也会抛 UnidentifiedImageError（同进程直连 PIL 打开同一
    文件却正常，见 2026-09-26 的排查），本用例不应该被这种测试间污染带崩。
    """
    from PIL import Image

    path = REPO_ROOT / str(relative).replace('./', '', 1)
    with Image.open(path) as image:
        return np.array(image.convert('RGB'))


def build_frame(*assets):
    """叠加资源图，还原“页面上存在这些元素”的测试截图。"""
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    for asset in assets:
        frame = np.maximum(frame, read_image(asset.file))
    return frame


def frame_of(name):
    """按需构造测试画面并缓存。

    刻意不在模块导入时构造：全量测试里模块导入顺序会让相对资源路径与资源写入互相
    影响，导入期读图会让整个模块收集失败（单独运行时却正常）。
    """
    if name not in _frames:
        if name == 'filter':
            # 筛选弹窗打开（含「剩余体力」与「确定」）。真实卡死截图里、
            # 角色页与岗位详情那批模板在这个画面上全都不命中。
            _frames[name] = build_frame(SELECT_CHARACTER_FILTER_CONFIRM, SELECT_CHARACTER_FILTER_STAMINA)
        elif name == 'character':
            # 弹窗关闭后的角色选择页（只留筛选入口按钮）
            _frames[name] = build_frame(SELECT_CHARACTER_FILTER)
        else:
            _frames[name] = np.zeros((720, 1280, 3), dtype=np.uint8)
    return _frames[name]


class FakeClock:
    def __init__(self):
        # 起点取正值：Timer.start() 以 _start <= 0 判定“未启动”
        self.now = 1000.0

    def advance(self, seconds):
        self.now += seconds


class FakeDevice:
    """按时间线提供截图，截图与点击都推进虚拟时间，并计数各类点击。"""

    def __init__(self, clock, frame, screenshot_cost=0.4, click_cost=0.2):
        self.clock = clock
        self.frame = frame
        self.screenshot_cost = screenshot_cost
        self.click_cost = click_cost
        # 初始画面与时间线保持一致（此时还没有任何点击）
        self.image = frame(0, 0)
        self.clicks = []

    def screenshot(self):
        self.clock.advance(self.screenshot_cost)
        self.image = self.frame(self.count(CONFIRM), self.count(ENTRY))
        return self.image

    def click(self, button, control_check=True):
        self.clicks.append(button.name)
        self.clock.advance(self.click_cost)

    def sleep(self, seconds):
        self.clock.advance(seconds)

    def stuck_record_add(self, button):
        return None

    def count(self, name):
        return sum(1 for clicked in self.clicks if clicked == name)


class StubIsland:
    """绑定 Island 上被测试的真实方法。"""

    appear = Island.appear
    appear_then_click = Island.appear_then_click
    is_character_filter_visible = Island.is_character_filter_visible
    close_character_filter = Island.close_character_filter
    select_character_filter = Island.select_character_filter
    back_to_postmanage_from_dispatch = Island.back_to_postmanage_from_dispatch
    is_post_detail_visible = Island.is_post_detail_visible

    def __init__(self, device, recovered=None):
        self.device = device
        self.interval_timer = {}
        self.config = type("Config", (), {"BUTTON_OFFSET": 30})()
        # recovered(confirm 点击数, entry 点击数) -> 是否已回到岗位管理页
        self.recovered = recovered or (lambda confirm, entry: False)

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

    def interval_clear(self, button):
        return None

    @staticmethod
    def ensure_button(button):
        return button

    def ui_page_appear(self, page):
        return self.recovered(self.device.count(CONFIRM), self.device.count(ENTRY))


class CharacterFilterTest(unittest.TestCase):
    def setUp(self):
        self._repair_poisoned_pil()
        self.clock = FakeClock()
        self._real_time = timer_module.time
        timer_module.time = lambda: self.clock.now

    @staticmethod
    def _repair_poisoned_pil():
        """检测被污染的 sys.modules['PIL']。

        Python 用 ``sys.modules[name] = None`` 表示“这个模块找不到”。进程里一旦出现
        这种条目，PIL 内部懒加载插件（``from . import PngImagePlugin``）就会解析父包
        失败，于是**完好的 PNG 也会抛 UnidentifiedImageError**。2026-09-26 复现：全量
        跑测试时前面某些模块会留下这个状态，连带把岛屿/科研那批用例一起带崩，而单独
        运行全部正常。污染源不在本次改动范围内，这里只做检测：中招就跳过并说明原因，
        不再产出会误导的失败。
        """
        for name in ('PIL', 'PIL.Image'):
            if sys.modules.get(name, 'missing') is None:
                raise unittest.SkipTest(f'sys.modules[{name!r}] 为 None（被其他用例污染），跳过本次校验')
        # 污染也可能发生在首次真正读图时（导入失败会把 sys.modules['PIL'] 留成
        # None）。用与模板匹配完全相同的加载路径探一次：读不动就跳过，不再产出
        # 误导性的失败。注意必须走 module.base.utils.load_image——Button 懒加载
        # 模板用的就是它，2026-09-26 复现时直连 PIL 能读、它却抛
        # UnidentifiedImageError。
        from module.base.utils import load_image as utils_load_image
        try:
            utils_load_image(SELECT_CHARACTER_FILTER_CONFIRM.file)
            utils_load_image(SELECT_CHARACTER_FILTER_STAMINA.file)
        except Exception as error:
            raise unittest.SkipTest(f'模板图加载不可用（{type(error).__name__}: {error}），跳过本次校验') from error

    def tearDown(self):
        timer_module.time = self._real_time

    def build(self, frame, recovered=None):
        device = FakeDevice(self.clock, frame)
        return device, StubIsland(device, recovered)

    def test_close_filter_retries_until_dialog_gone(self):
        """弹窗一开始点不掉：要重试点击「确定」，直到弹窗消失才返回 True。"""
        # 前两次「确定」都没生效，第三次才关掉
        device, stub = self.build(
            lambda confirm, entry: frame_of('character') if confirm >= 3 else frame_of('filter'))

        self.assertTrue(stub.close_character_filter())

        self.assertEqual(device.count(CONFIRM), 3)
        self.assertEqual(device.count(STAMINA), 0)

    def test_close_filter_returns_false_when_dialog_stays(self):
        """弹窗始终关不掉：有界返回 False，不会无限等下去。"""
        device, stub = self.build(lambda confirm, entry: frame_of('filter'))

        self.assertFalse(stub.close_character_filter(timeout=2))
        self.assertLess(self.clock.now - 1000.0, 10)

    def test_select_filter_verifies_dialog_closed(self):
        """「确定」一次没点掉时要重试；只有弹窗确认关闭才返回 True。"""
        def frame(confirm, entry):
            # 初始是角色选择页；点过筛选入口后弹窗才出现；两次「确定」后才关掉
            if entry == 0 and confirm == 0:
                return frame_of('character')
            return frame_of('character') if confirm >= 2 else frame_of('filter')

        device, stub = self.build(frame)

        self.assertTrue(stub.select_character_filter())

        self.assertEqual(device.count(ENTRY), 1)
        self.assertEqual(device.count(STAMINA), 1)
        self.assertGreaterEqual(device.count(CONFIRM), 2)

    def test_select_filter_does_not_click_through_missing_dialog(self):
        """弹窗没出现时不得盲点「确定」（会点到弹窗背后的角色卡）。"""
        device, stub = self.build(lambda confirm, entry: frame_of('character'))

        self.assertFalse(stub.select_character_filter())

        self.assertEqual(device.clicks, [ENTRY])

    def test_back_to_postmanage_closes_filter_instead_of_spinning(self):
        """核心回归：卡在筛选弹窗时，退出流程必须点得动，而不是空转到设备级卡死。"""
        # 点一次「确定」后就算回到岗位管理页
        device, stub = self.build(
            lambda confirm, entry: frame_of('character') if confirm >= 1 else frame_of('filter'),
            recovered=lambda confirm, entry: confirm >= 1,
        )

        self.assertTrue(stub.back_to_postmanage_from_dispatch())

        self.assertIn(CONFIRM, device.clicks)
        # 修复前这里一次点击都没有，虚拟时间会耗尽 15 秒超时
        self.assertLess(self.clock.now - 1000.0, 15)

    def test_back_to_postmanage_still_times_out_on_unknown_screen(self):
        """完全陌生的画面仍然按原设计超时返回 False（不吞掉异常、不无脑点击）。"""
        device, stub = self.build(lambda confirm, entry: frame_of('empty'))

        self.assertFalse(stub.back_to_postmanage_from_dispatch())

        self.assertEqual(device.clicks, [])


if __name__ == "__main__":
    unittest.main()
