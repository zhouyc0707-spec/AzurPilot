"""真实岗位定位循环：贴边先补滑，完整可见零滑动，越界不得盲点。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from module.base.button import Button
from module.base.utils import area_offset
from module.exception import GameStuckError
from module.island import island as island_module
from module.island.island import Island

POST_AREA = (737, 499, 868, 525)


class EdgeDevice:
    """只推进截图时间和帧号，保持真实 Island.loop 与 Timer。"""

    def __init__(self, clock):
        self.clock = clock
        self.image = np.zeros((720, 1280, 3), np.uint8)
        self.frame = -1
        self.opened = False
        self.click_frames = []

    def screenshot(self):
        self.clock.now += 0.25
        self.frame += 1
        return self.image


def make_island(clock, bottoms, *, hidden_frames=(), left_offset=0):
    """每张新截图确定当帧岗位坐标；重复匹配不会推进画面。"""
    island = Island.__new__(Island)
    island.config = SimpleNamespace(SERVER='cn')
    island.device = EdgeDevice(clock)
    island._post_list_recently_scrolled = False
    post = Button(area=POST_AREA, color=(0, 0, 0), button=POST_AREA, name='POST')
    matched = []

    def match(image, button):
        if button is not post:
            return False
        index = max(0, island.device.frame)
        bottom = bottoms[min(index, len(bottoms) - 1)]
        post._button_offset = area_offset(POST_AREA, (left_offset, bottom - POST_AREA[3]))
        matched.append((clock.now, index, tuple(post.button)))
        return True

    def click(button, **kwargs):
        if button is post:
            island.device.click_frames.append(island.device.frame)
            island.device.opened = True

    def scroll(distance):
        island._post_list_recently_scrolled = True

    island.match_title = match
    island.matched_positions = matched
    island.device.click = Mock(side_effect=click)
    island.post_manage_up_swipe = Mock(side_effect=scroll)
    island.post_manage_down_swipe = Mock(side_effect=scroll)
    island.ui_page_appear = Mock(side_effect=lambda page: island.device.frame not in hidden_frames)
    island.is_post_detail_visible = Mock(side_effect=lambda: island.device.opened)
    island.post_close = Mock()
    island.goto_postmanage = Mock()
    island.post_manage_mode = Mock()
    return island, post


class PostOpenEdgeTest(unittest.TestCase):
    def setUp(self):
        self.clock = SimpleNamespace(now=1000.0)
        self.enterContext(patch('module.base.timer.time', side_effect=lambda: self.clock.now))
        self.lock = self.enterContext(patch.object(island_module.TEMPLATE_POST_LOCK, 'match', return_value=False))
        self.direction = self.enterContext(patch.object(island_module, 'post_scroll_direction', return_value=0))

    def build(self, bottoms, **kwargs):
        island, post = make_island(self.clock, bottoms, **kwargs)
        self.enterContext(patch.object(island_module, 'match_post_title', side_effect=island.match_title))
        return island, post

    def test_nudges_list_before_clicking_edge_post(self):
        island, post = self.build([706, 560, 560, 560])
        self.assertTrue(island.post_open(post))
        center_y = 706 - (POST_AREA[3] - POST_AREA[1]) / 2
        island.post_manage_up_swipe.assert_called_once_with(int(center_y + 45 - 640 + 40))
        island.post_manage_down_swipe.assert_not_called()
        island.device.click.assert_called_once_with(post)
        # 补滑后的 0.4 秒窗口不读取旧坐标；之后还须两帧位置稳定才点击。
        self.assertGreaterEqual(island.matched_positions[1][0] - island.matched_positions[0][0], 0.4)
        self.assertEqual(island.device.click_frames, [3])

    def test_clicks_directly_when_post_visible(self):
        island, post = self.build([560])
        self.assertTrue(island.post_open(post))
        island.post_manage_up_swipe.assert_not_called()
        island.post_manage_down_swipe.assert_not_called()
        island.device.click.assert_called_once_with(post)
        self.assertEqual(island.device.click_frames, [0])
        self.direction.assert_not_called()

    def test_locate_visible_post_does_not_click_or_scroll(self):
        island, post = self.build([560])
        self.assertTrue(island.post_locate(post))
        island.device.click.assert_not_called()
        island.post_manage_up_swipe.assert_not_called()
        island.post_manage_down_swipe.assert_not_called()

    def test_three_nudges_still_outside_raise_without_clicking(self):
        for open_post in (True, False):
            with self.subTest(open_post=open_post):
                island, post = self.build([706])
                action = island.post_open if open_post else island.post_locate
                with self.assertRaisesRegex(GameStuckError, '边缘补滑后仍不可完整点击'):
                    action(post)
                self.assertEqual(island.post_manage_up_swipe.call_count, island_module.POST_EDGE_NUDGE_LIMIT)
                island.post_manage_down_swipe.assert_not_called()
                island.device.click.assert_not_called()
                island.goto_postmanage.assert_not_called()

    def test_top_edge_nudges_down_before_clicking(self):
        island, post = self.build([100, 560, 560, 560])
        self.assertTrue(island.post_open(post))
        center_y = 100 - (POST_AREA[3] - POST_AREA[1]) / 2
        island.post_manage_down_swipe.assert_called_once_with(int(115 - (center_y - 45) + 40))
        island.post_manage_up_swipe.assert_not_called()
        island.device.click.assert_called_once_with(post)

    def test_rebound_waits_for_two_new_stable_positions(self):
        island, post = self.build([706, 560, 560, 568, 568])
        self.assertTrue(island.post_open(post))
        self.assertEqual(island.device.click_frames, [4])
        island.post_manage_up_swipe.assert_called_once()
        island.post_manage_down_swipe.assert_not_called()

    def test_unknown_page_breaks_position_stability_without_clicking_or_scrolling(self):
        island, post = self.build([706, 560, 560, 560, 560, 560], hidden_frames={3})
        self.assertTrue(island.post_open(post))
        self.assertEqual(island.device.click_frames, [5])
        island.post_manage_up_swipe.assert_called_once()
        island.post_manage_down_swipe.assert_not_called()
        self.direction.assert_not_called()

    def test_horizontal_out_of_bounds_never_scrolls_or_clicks(self):
        island, post = self.build([560], left_offset=600)
        with self.assertRaisesRegex(GameStuckError, '横向范围'):
            island.post_open(post)
        island.post_manage_up_swipe.assert_not_called()
        island.post_manage_down_swipe.assert_not_called()
        island.device.click.assert_not_called()

    def test_visible_locked_post_returns_false_without_clicking(self):
        island, post = self.build([560])
        self.lock.return_value = True
        self.assertFalse(island.post_open(post))
        island.post_manage_up_swipe.assert_not_called()
        island.post_manage_down_swipe.assert_not_called()
        island.device.click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
