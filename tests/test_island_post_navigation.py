"""岗位整列定位与国服实屏方向锚点回归，不操作设备。"""

import unittest
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from module.base.button import Button
from module.base.utils import load_image
from module.island.assets import ISLAND_FARM_POST1
from module.island.island import Island
from module.island.post_list import match_post_title, post_scroll_direction
from module.island_farm.assets import ISLAND_NURSERY_POST1, ISLAND_ORCHARD_POST1
from module.island_grill.assets import ISLAND_GRILL_POST1
from module.island_juu_coffee.assets import ISLAND_JUU_COFFEE_POST1
from module.island_juu_eatery.assets import ISLAND_JUU_EATERY_POST1
from module.island_manufacture.assets import (
    ISLAND_ELECTRONIC_PROCESSING_POST1, ISLAND_HANDMADE_POST1,
    ISLAND_INDUSTRIAL_POST1, ISLAND_WOOD_PROCESSING_POST1,
)
from module.island_mine_forest.assets import ISLAND_FOREST_POST1, ISLAND_MINE_POST1
from module.island_rancher.assets import ISLAND_FISHERY_POST1, ISLAND_RANCH_POST1
from module.island_restaurant.assets import ISLAND_RESTAURANT_POST1
from module.island_teahouse.assets import ISLAND_TEAHOUSE_POST1


FIXTURES = Path(__file__).parent / 'fixtures/island_post_list'
ROWS = (
    (ISLAND_FARM_POST1, ISLAND_RANCH_POST1),
    (ISLAND_FISHERY_POST1, ISLAND_MINE_POST1),
    (ISLAND_FOREST_POST1, ISLAND_ORCHARD_POST1),
    (ISLAND_NURSERY_POST1, ISLAND_RESTAURANT_POST1),
    (ISLAND_TEAHOUSE_POST1, ISLAND_JUU_EATERY_POST1),
    (ISLAND_GRILL_POST1, ISLAND_WOOD_PROCESSING_POST1),
    (ISLAND_INDUSTRIAL_POST1, ISLAND_ELECTRONIC_PROCESSING_POST1),
    (ISLAND_HANDMADE_POST1, ISLAND_JUU_COFFEE_POST1),
)


class PostNavigationImageTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.base.utils.TEMPLATE_MATCH_NON_NATIVE_720P', True))
        self.enterContext(patch('module.base.utils.TEMPLATE_MATCH_NON_NATIVE_720P_THRESHOLD', 0.75))
        self.addCleanup(lambda: [post.clear_offset() for row in ROWS for post in row])

    def frame(self, name):
        return load_image(str(FIXTURES / f'current_navigation_{name}.png'))

    def test_real_frames_recognize_all_eight_rows_in_visual_order(self):
        for name, indices in [('upper', range(3)), ('middle', range(3, 6)), ('bottom', range(5, 8))]:
            with self.subTest(frame=name):
                image = self.frame(name)
                previous_y = None
                for index in indices:
                    ys = []
                    for post in ROWS[index]:
                        self.assertTrue(match_post_title(image, post), post.name)
                        ys.append(post.area[1] + post.button[1] - post._button[1])
                    self.assertLessEqual(abs(ys[0] - ys[1]), 4)
                    if previous_y is not None:
                        self.assertGreater(min(ys), previous_y + 150)
                    previous_y = max(ys)

    def test_real_title_matches_at_both_ends_of_full_visible_height(self):
        # 使用现有实屏农田标题移位，证明搜索不受原始 y=79 加减300的窗口限制。
        title = load_image(str(FIXTURES / 'farm_title.png'))
        for top in (69, 655 - title.shape[0]):
            with self.subTest(top=top):
                image = np.full((720, 1280, 3), 255, dtype=np.uint8)
                image[top:top + title.shape[0], 198:198 + title.shape[1]] = title
                self.assertTrue(match_post_title(image, ISLAND_FARM_POST1))
                actual = ISLAND_FARM_POST1.area[1] + ISLAND_FARM_POST1.button[1] - ISLAND_FARM_POST1._button[1]
                self.assertEqual(actual, top)

    def test_cn_visible_rows_give_both_directions(self):
        for post in ROWS[7]:
            self.assertEqual(post_scroll_direction(self.frame('upper'), post), 1)
        for post in ROWS[0]:
            self.assertEqual(post_scroll_direction(self.frame('bottom'), post), -1)
        self.assertEqual(post_scroll_direction(self.frame('middle'), ISLAND_FARM_POST1), -1)
        self.assertEqual(post_scroll_direction(self.frame('middle'), ISLAND_JUU_COFFEE_POST1), 1)
        self.assertEqual(post_scroll_direction(self.frame('middle'), ISLAND_TEAHOUSE_POST1), 0)

    def test_unknown_target_missing_titles_and_other_servers_have_no_direction(self):
        unknown = Button(area=(200, 80, 280, 105), color=(), button=(260, 135, 335, 210), name='UNKNOWN_POST1')
        self.assertEqual(post_scroll_direction(self.frame('bottom'), unknown), 0)
        blank = np.full((720, 1280, 3), 255, dtype=np.uint8)
        self.assertEqual(post_scroll_direction(blank, ISLAND_FARM_POST1), 0)
        for server in ('en', 'jp', 'tw', 'unknown'):
            with self.subTest(server=server):
                self.assertEqual(post_scroll_direction(self.frame('bottom'), ISLAND_FARM_POST1, server), 0)

    def test_wrong_workshop_title_is_rejected_even_when_global_threshold_is_low(self):
        post = ISLAND_ELECTRONIC_PROCESSING_POST1
        post.ensure_template()
        image = np.full((720, 1280, 3), 255, dtype=np.uint8)
        x1, y1, x2, y2 = post.area
        image[y1:y2, x1:x2] = post.image
        self.assertFalse(match_post_title(image, ISLAND_WOOD_PROCESSING_POST1))
        self.assertIsNone(ISLAND_WOOD_PROCESSING_POST1._button_offset)

    def test_failed_frame_discards_previously_confirmed_offset(self):
        post = ISLAND_GRILL_POST1
        self.assertTrue(match_post_title(self.frame('middle'), post))
        self.assertNotEqual(post.button, post._button)
        self.assertFalse(match_post_title(np.full((720, 1280, 3), 255, dtype=np.uint8), post))
        self.assertIsNone(post._button_offset)
        self.assertFalse(match_post_title(np.zeros((10, 10, 3), dtype=np.uint8), post))
        self.assertIsNone(post._button_offset)

    def test_all_fully_visible_real_posts_locate_without_swiping_or_clicking(self):
        # 真实页面、类别标题和全部槽位坐标均走生产识别；只替代设备截图来源。
        modules = {Path(post.raw_file['cn']).parent.name for row in ROWS for post in row}
        posts = {name: value for module in modules
                 for name, value in vars(import_module(f'module.{module}.assets')).items()
                 if '_POST' in name and name.rsplit('_POST', 1)[1].isdigit()}
        checked = 0
        for frame, indices in [('upper', range(3)), ('middle', range(3, 5)), ('bottom', range(5, 8))]:
            image = self.frame(frame)
            for index in indices:
                for first in ROWS[index]:
                    prefix = first.name.rsplit('_POST', 1)[0] + '_POST'
                    for name, source in sorted(posts.items()):
                        if not name.startswith(prefix):
                            continue
                        with self.subTest(frame=frame, post=name):
                            # 前序 EN 截图测试会重载 assets，并留下已缓存 EN 文件的新对象。
                            # 本夹具明确属于 CN，按原始 CN 定义创建独立真实按钮，避免语言缓存串用。
                            post = Button(area=source.raw_area['cn'], color=source.raw_color['cn'],
                                          button=source.raw_button['cn'], file=source.raw_file['cn'], name=name)
                            task = Island.__new__(Island)
                            task.config = SimpleNamespace(SERVER='cn', BUTTON_OFFSET=30)
                            task.interval_timer = {}
                            task.device = SimpleNamespace(image=image, stuck_record_add=Mock(), click=Mock())
                            task.loop = Mock(return_value=iter([image, image.copy()]))
                            task.post_manage_up_swipe = Mock(side_effect=AssertionError('完整可见岗位不应上滑'))
                            task.post_manage_down_swipe = Mock(side_effect=AssertionError('完整可见岗位不应下滑'))
                            task.post_close = Mock(side_effect=AssertionError('列表定位不应关闭详情'))
                            self.assertTrue(task.post_locate(post))
                            task.post_manage_up_swipe.assert_not_called()
                            task.post_manage_down_swipe.assert_not_called()
                            task.device.click.assert_not_called()
                            post.clear_offset()
                            checked += 1
        self.assertEqual(checked, 43)


if __name__ == '__main__':
    unittest.main()
