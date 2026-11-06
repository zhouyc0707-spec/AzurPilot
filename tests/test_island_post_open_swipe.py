"""岗位定位从当前画面开始，只有缺少方向依据时才归顶的状态回归。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from module.base.button import Button
from module.base.timer import Timer
from module.exception import GameStuckError
from module.island.island import Island


class PostLocateTests(unittest.TestCase):
    def setUp(self):
        self.clock = SimpleNamespace(now=1000.0)
        self.enterContext(patch('module.base.timer.time', side_effect=lambda: self.clock.now))
        self.enterContext(patch('module.island.island.logger'))
        self.enterContext(patch('module.island.island.TEMPLATE_POST_LOCK.match', return_value=False))

    def make_task(self, row, scroll=0, direction=True):
        with patch.object(Island, '__init__', lambda self: None):
            task = Island()
        task.config = SimpleNamespace(SERVER='cn')
        task.post_open_full_retry_limit = 1
        post = Button(area=(200, 100, 280, 125), color=(), button=(260, 150, 330, 225), name='TARGET')
        state = SimpleNamespace(scroll=scroll, opened=False, rows=row, missing=False)
        task.device = Mock()
        task.device.image = np.zeros((720, 1280, 3), dtype=np.uint8)

        def screenshot():
            self.clock.now += 0.2
            return task.device.image

        task.device.screenshot.side_effect = screenshot

        def loop(skip_first=True, timeout=None):
            timer = Timer.from_seconds(timeout).start()
            while not timer.reached():
                if skip_first:
                    skip_first = False
                else:
                    screenshot()
                yield task.device.image

        task.loop = loop
        task.is_post_detail_visible = lambda: state.opened
        task.ui_page_appear = Mock(return_value=True)
        task.post_close = Mock()
        task.goto_postmanage = Mock()
        task.post_manage_mode = Mock()
        task.post_manage_swipe = Mock(side_effect=AssertionError('不得调用固定区域滑动'))
        task.post_manage_swipe_to_top = Mock(side_effect=AssertionError('不得嵌套旧归顶循环'))

        def move(amount):
            state.scroll = min(900, max(0, state.scroll + amount))
            task._post_list_recently_scrolled = True

        task.post_manage_up_swipe = Mock(side_effect=lambda distance: move(distance))
        task.post_manage_down_swipe = Mock(side_effect=lambda distance: move(-distance))

        def match(image, button):
            if state.missing:
                return False
            target_row = row if button is post else 0
            y = 100 + 184 * target_row - state.scroll
            if 69 <= y and y + 25 <= 655:
                button._button_offset = (260, y + 50, 330, y + 125)
                return True
            return False

        self.enterContext(patch('module.island.island.match_post_title', side_effect=match))
        self.enterContext(patch('module.island.island.post_scroll_direction', side_effect=lambda *a, **kw:
                                (1 if 100 + 184 * row - state.scroll > 655 else -1) if direction else 0))
        task.device.click.side_effect = lambda button: setattr(state, 'opened', True)
        return task, post, state

    def test_current_visible_post_opens_without_scrolling_or_reset(self):
        task, post, _ = self.make_task(6, scroll=900)
        self.assertTrue(task.post_open(post))
        task.device.click.assert_called_once_with(post)
        task.post_manage_up_swipe.assert_not_called()
        task.post_manage_down_swipe.assert_not_called()
        task.goto_postmanage.assert_not_called()

    def test_visible_empty_inspection_only_locates_and_never_opens(self):
        task, post, _ = self.make_task(5, scroll=900)
        self.assertTrue(task.post_locate(post))
        task.device.click.assert_not_called()
        task.post_manage_up_swipe.assert_not_called()
        task.post_manage_down_swipe.assert_not_called()

    def test_existing_other_post_detail_is_closed_before_target_is_opened(self):
        task, post, state = self.make_task(6, scroll=900)
        state.opened = True
        task.post_close.side_effect = lambda: setattr(state, 'opened', False)
        self.assertTrue(task.post_open(post))
        task.post_close.assert_called_once()
        task.device.click.assert_called_once_with(post)
        task.post_manage_up_swipe.assert_not_called()
        task.post_manage_down_swipe.assert_not_called()

    def test_nearby_lower_target_does_not_return_to_top(self):
        task, post, _ = self.make_task(7, scroll=450)
        self.assertTrue(task.post_locate(post))
        task.post_manage_up_swipe.assert_called_once_with(450)
        task.post_manage_down_swipe.assert_not_called()
        task.device.click.assert_not_called()
        task.goto_postmanage.assert_not_called()

    def test_nearby_upper_target_scrolls_back_only_as_far_as_needed(self):
        task, post, _ = self.make_task(3, scroll=900)
        self.assertTrue(task.post_locate(post))
        task.post_manage_down_swipe.assert_called_once_with(450)
        task.post_manage_up_swipe.assert_not_called()
        task.goto_postmanage.assert_not_called()

    def test_unknown_direction_returns_top_then_stops_at_target(self):
        task, post, _ = self.make_task(0, scroll=900, direction=False)
        self.assertTrue(task.post_locate(post))
        self.assertEqual(task.post_manage_down_swipe.call_count, 2)
        task.post_manage_up_swipe.assert_not_called()
        task.device.click.assert_not_called()

    def test_top_search_checks_before_each_swipe(self):
        task, post, _ = self.make_task(7, direction=False)
        self.assertTrue(task.post_locate(post))
        self.assertEqual(task.post_manage_up_swipe.call_count, 2)
        task.post_manage_down_swipe.assert_not_called()
        task.goto_postmanage.assert_not_called()

    def test_repeated_missing_title_has_bounded_reentry_and_never_clicks(self):
        task, post, state = self.make_task(7, direction=False)
        state.missing = True
        with self.assertRaisesRegex(GameStuckError, '完整重试'):
            task.post_open(post)
        self.assertEqual(task.post_manage_down_swipe.call_count, 10)
        task.goto_postmanage.assert_called_once()
        task.device.click.assert_not_called()

    def test_unrecognized_page_does_not_swipe_or_use_stale_position(self):
        task, post, _ = self.make_task(4)
        task.ui_page_appear.return_value = False
        post._button_offset = (260, 150, 330, 225)
        with self.assertRaises(GameStuckError):
            task.post_locate(post)
        task.post_manage_up_swipe.assert_not_called()
        task.post_manage_down_swipe.assert_not_called()
        task.device.click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
