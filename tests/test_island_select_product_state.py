"""选品点击后的过渡帧不得触发翻页，搜索、补点与未知页面均有界。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from module.base import utils
from module.base.button import Button
from module.island import island as island_module
from module.island.island import Island
from module.island_restaurant.assets import (
    SELECT_GREEN_JUJUBE_PORRIDGE, SELECT_GREEN_JUJUBE_PORRIDGE_CHECK,
)


def cn_button(button):
    """不改变全局服务器，独立使用国服资源和坐标。"""
    return Button(area=button.raw_area['cn'], color=button.raw_color['cn'],
                  button=button.raw_button['cn'], file=button.raw_file['cn'], name=button.name)


class ProductFlow:
    """保留真实 loop 和 Timer，仅替换截图、设备操作与可见状态。"""

    def __init__(self, frame, images=False):
        self.now = 1000.0
        self.started = self.now
        self.step = 0.2
        self.reads = 0
        self.frame = frame
        self.images = images
        self.clicks = []
        self.swipes = []
        self.page = cn_button(island_module.ISLAND_SELECT_PRODUCT_CHECK)
        self.target = cn_button(SELECT_GREEN_JUJUBE_PORRIDGE)
        self.selected = cn_button(SELECT_GREEN_JUJUBE_PORRIDGE_CHECK)
        self.task = Island.__new__(Island)
        self.task.match_template_color = self.match
        self.task.device = SimpleNamespace(
            image=None, screenshot=self.screenshot, click=self.click,
            swipe_vector=self.swipe, click_record_remove=Mock(), sleep=Mock(),
        )

    def screenshot(self):
        self.now += self.step
        self.reads += 1
        self.task.device.image = self.frame(self)

    def match(self, button, **kwargs):
        if self.images:
            return button.match_template_color(self.task.device.image, **kwargs)
        return button in self.task.device.image

    def click(self, button, **kwargs):
        self.clicks.append((button, self.now, self.reads, kwargs))

    def swipe(self, **kwargs):
        self.swipes.append((self.now, self.reads, kwargs))

    def target_clicks(self):
        return [event for event in self.clicks if event[0] is self.target]

    def stop_clicks(self):
        return [event for event in self.clicks if event[0] is island_module.SELECT_PRODUCT_INERTIA_STOP]

    def run(self):
        with patch('module.base.timer.time', lambda: self.now), patch.object(
                island_module, 'ISLAND_SELECT_PRODUCT_CHECK', self.page), patch.object(island_module, 'logger'):
            return self.task.select_product(self.target, self.selected)


class SelectProductStateTests(unittest.TestCase):
    def test_already_selected_returns_on_first_frame_without_actions(self):
        flow = ProductFlow(lambda f: {f.page, f.selected})
        self.assertTrue(flow.run())
        self.assertEqual(flow.reads, 1)
        self.assertEqual(flow.clicks, [])
        self.assertEqual(flow.swipes, [])

    def test_normal_selection_keeps_next_frame_fast_path(self):
        flow = ProductFlow(lambda f: {f.page, f.selected if f.target_clicks() else f.target})
        self.assertTrue(flow.run())
        self.assertEqual(flow.reads, 2)
        self.assertEqual(len(flow.target_clicks()), 1)
        self.assertEqual(flow.swipes, [])
        flow.task.device.sleep.assert_not_called()

    def test_icons_disappear_after_click_then_selected_without_swiping(self):
        def frame(flow):
            if not flow.target_clicks():
                return {flow.page, flow.target}
            if flow.now - flow.target_clicks()[0][1] < 0.8:
                return set()
            return {flow.page, flow.selected}

        flow = ProductFlow(frame)
        self.assertTrue(flow.run())
        self.assertEqual(len(flow.target_clicks()), 1)
        self.assertEqual(flow.swipes, [])
        flow.task.device.sleep.assert_not_called()

    def test_ignored_first_click_retries_visible_target_after_two_seconds(self):
        flow = ProductFlow(lambda f: {f.page, f.selected if len(f.target_clicks()) >= 2 else f.target})
        self.assertTrue(flow.run())
        clicks = flow.target_clicks()
        self.assertEqual(len(clicks), 2)
        self.assertGreaterEqual(clicks[1][1] - clicks[0][1], 2)
        self.assertEqual(flow.swipes, [])

    def test_three_ignored_clicks_fail_without_using_swipe_budget(self):
        flow = ProductFlow(lambda f: {f.page, f.target})
        self.assertFalse(flow.run())
        clicks = flow.target_clicks()
        self.assertEqual(len(clicks), 3)
        self.assertTrue(all(b[1] - a[1] >= 2 for a, b in zip(clicks, clicks[1:])))
        self.assertGreaterEqual(flow.now - clicks[-1][1], 2)
        self.assertLess(flow.now - flow.started, 8)
        self.assertEqual(flow.swipes, [])

    def test_unknown_page_waits_until_total_timeout_without_any_operation(self):
        flow = ProductFlow(lambda f: set())
        self.assertFalse(flow.run())
        self.assertGreaterEqual(flow.now - flow.started, 30)
        self.assertLess(flow.now - flow.started, 30.3)
        self.assertEqual(flow.clicks, [])
        self.assertEqual(flow.swipes, [])
        flow.task.device.sleep.assert_not_called()

    def test_target_icon_without_product_page_does_not_allow_click_or_swipe(self):
        flow = ProductFlow(lambda f: {f.target})
        self.assertFalse(flow.run())
        self.assertEqual(flow.clicks, [])
        self.assertEqual(flow.swipes, [])

    def test_page_and_target_disappear_after_click_wait_without_old_coordinate_retry(self):
        flow = ProductFlow(lambda f: set() if f.target_clicks() else {f.page, f.target})
        self.assertFalse(flow.run())
        self.assertEqual(len(flow.target_clicks()), 1)
        self.assertEqual(flow.swipes, [])
        self.assertLess(flow.now - flow.started, 30.3)

    def test_target_can_be_found_and_confirmed_after_eighth_actual_swipe(self):
        def frame(flow):
            if flow.target_clicks():
                return {flow.page, flow.selected}
            if len(flow.swipes) == 8:
                return {flow.page, flow.target}
            return {flow.page}

        flow = ProductFlow(frame)
        self.assertTrue(flow.run())
        self.assertEqual(len(flow.swipes), 8)
        self.assertEqual(len(flow.stop_clicks()), 8)
        self.assertEqual(len(flow.target_clicks()), 1)
        self.assertGreater(flow.target_clicks()[0][2], flow.stop_clicks()[-1][2])
        flow.task.device.sleep.assert_not_called()

    def test_missing_target_stops_after_eight_actual_swipes_and_checks_final_page(self):
        flow = ProductFlow(lambda f: {f.page})
        self.assertFalse(flow.run())
        self.assertEqual(len(flow.swipes), 8)
        self.assertEqual(len(flow.stop_clicks()), 8)
        self.assertEqual(flow.target_clicks(), [])
        self.assertGreater(flow.reads, flow.stop_clicks()[-1][2])
        for swipe, stop in zip(flow.swipes, flow.stop_clicks()):
            self.assertGreaterEqual(stop[1] - swipe[0], 0.3)
            self.assertEqual(stop[3], {'control_check': False})
            self.assertEqual(swipe[2]['name'], island_module.SELECTION_UP_SWIPE_NAME)
        flow.task.device.click_record_remove.assert_called_once_with(island_module.SELECTION_UP_SWIPE_NAME)

    def test_swipe_inertia_stop_waits_for_product_page_to_return(self):
        def frame(flow):
            if not flow.swipes:
                return {flow.page}
            if flow.now - flow.swipes[0][0] < 1:
                return set()
            return {flow.page, flow.selected}

        flow = ProductFlow(frame)
        self.assertTrue(flow.run())
        self.assertEqual(len(flow.swipes), 1)
        self.assertEqual(len(flow.stop_clicks()), 1)
        self.assertGreaterEqual(flow.stop_clicks()[0][1] - flow.swipes[0][0], 1)
        self.assertGreaterEqual(flow.now - flow.stop_clicks()[0][1], 0.2)


class SelectProductImageTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(utils.set_template_match_non_native_720p,
                        utils.TEMPLATE_MATCH_NON_NATIVE_720P,
                        utils.TEMPLATE_MATCH_NON_NATIVE_720P_RESOLUTION)
        utils.set_template_match_non_native_720p(False)

    @staticmethod
    def image_with(*buttons):
        # 仅由 CN 模板合成状态；不冒充故障现场的连续截图。
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        for button in buttons:
            original = utils.load_image(button.file)
            x1, y1, x2, y2 = button.area
            frame[y1:y2, x1:x2] = original[y1:y2, x1:x2]
        return frame

    def test_cn_jujube_template_transition_keeps_target_until_positive_selection(self):
        flow = ProductFlow(None, images=True)
        normal = self.image_with(flow.page, flow.target)
        selected = self.image_with(flow.page, flow.selected)
        blank = np.zeros_like(normal)
        self.assertEqual(flow.page.area, (123, 19, 221, 48))
        self.assertTrue(flow.page.match_template_color(normal, offset=(20, 20)))
        self.assertTrue(flow.target.match_template_color(normal, offset=300, similarity=0.85, threshold=10))
        self.assertTrue(flow.selected.match_template_color(selected, offset=20, similarity=0.85, threshold=10))

        def frame(current):
            if not current.target_clicks():
                return normal
            # 过渡时提前滑动将目标移走，复现旧流程的可证伪失败条件。
            if current.swipes or current.now - current.target_clicks()[0][1] < 0.8:
                return blank
            return selected

        flow.frame = frame
        self.assertTrue(flow.run())
        self.assertEqual(len(flow.target_clicks()), 1)
        self.assertEqual(flow.swipes, [])
        self.assertIs(flow.task.device.image, selected)
        flow.task.device.sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()
