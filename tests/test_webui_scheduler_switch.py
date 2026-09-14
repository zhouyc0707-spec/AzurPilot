"""概览页调度器启停按钮的行为测试。

停止/启动成功后按钮文案必须立刻翻转：它原本只由 1 秒间隔的轮询任务刷新，
点击后要等下一帧才变化，表现为停止按钮迟滞 1 秒以上。
"""

import unittest
from unittest.mock import Mock, patch

from module.webui.app_overview import OverviewMixin

LABEL_STOP = "Gui.Button.Stop"
LABEL_START = "Gui.Button.Start"


class _DummyOverview(OverviewMixin):
    """只用于挂载按钮的壳，避免依赖完整的 WebUI 会话。"""

    def __init__(self) -> None:
        self.alas = Mock()
        self.alas.alive = True
        self.alas_config = Mock()
        self.alas_config.Optimization_WhenSchedulerStopped = "stay_there"


class TestSchedulerSwitchRefresh(unittest.TestCase):
    def setUp(self):
        self.gui = _DummyOverview()
        patchers = [
            # 只关心按钮文案与重绘时机，不加载真实翻译和 PyWebIO 输出
            patch("module.webui.app_overview.t", side_effect=lambda key: key),
            patch("module.webui.widgets.clear"),
            patch("module.webui.widgets.put_button"),
        ]
        self.translate, self.clear, self.put_button = [
            patcher.start() for patcher in patchers
        ]
        for patcher in patchers:
            self.addCleanup(patcher.stop)

    def _mount(self, start=None):
        """挂载按钮并取出两个状态各自绑定的 (文案, 回调)。"""
        switch = self.gui._mount_scheduler_switch(start or (lambda: None))
        return switch, switch.status[1]["args"], switch.status[0]["args"]

    def _last_label(self):
        return self.put_button.call_args.kwargs["label"]

    def test_stop_click_repaints_button_before_next_poll(self):
        """点击停止后立即重绘为「启动」，不等 1 秒轮询任务。"""
        switch, (label_on, onclick_on, _), _ = self._mount()
        # 轮询任务的第一帧：按初始状态画出「停止」
        switch.switch()
        self.assertEqual(self._last_label(), LABEL_STOP)
        self.assertEqual(label_on, LABEL_STOP)

        self.gui.alas.alive = False
        onclick_on()

        self.gui.alas.stop_by_user.assert_called_once_with("stay_there")
        self.assertEqual(self._last_label(), LABEL_START)

    def test_start_click_repaints_button_before_next_poll(self):
        """点击启动后立即重绘为「停止」，不等 1 秒轮询任务。"""

        def start():
            self.gui.alas.alive = True

        switch, _, (label_off, onclick_off, _) = self._mount(start)
        self.gui.alas.alive = False
        switch.switch()
        self.assertEqual(self._last_label(), LABEL_START)
        self.assertEqual(label_off, LABEL_START)

        onclick_off()

        self.gui.alas.alive = True
        self.assertEqual(self._last_label(), LABEL_STOP)

    def test_stop_that_did_not_take_effect_keeps_stop_label(self):
        """停止没生效（alive 仍为 True）时不重绘，按钮保持「停止」。"""
        switch, _, (_, onclick_on, _) = self._mount()
        # 首帧画出「停止」，之后状态没变化就不该再有任何输出
        switch.switch()
        self.put_button.reset_mock()
        self.clear.reset_mock()

        onclick_on()

        self.put_button.assert_not_called()
        self.clear.assert_not_called()

    def test_daemon_start_callback_receives_task(self):
        """守护页的启动回调带着任务名，并且同样会立即重绘。"""
        task = "OpsiHazard1Leveling"
        switch, _, (_, onclick_off, _) = self._mount(
            lambda: self.gui.alas.start(task)
        )
        self.gui.alas.alive = False
        switch.switch()
        self.assertEqual(self._last_label(), LABEL_START)

        self.gui.alas.alive = True
        onclick_off()

        self.gui.alas.start.assert_called_once_with(task)
        self.assertEqual(self._last_label(), LABEL_STOP)


if __name__ == "__main__":
    unittest.main()
