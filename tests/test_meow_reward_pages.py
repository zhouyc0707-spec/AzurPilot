"""结算奖励多页（滚动补截）合并的单元测试。

覆盖 ``AutoSearchReward.parse_auto_search_reward_pages`` 的行对齐与去重：
奖励面板放不下时脚本会滚动补截多页，重叠行不能重复计数，也不能漏。
同时用脱敏结算面板验证真实定位、滚动后无标题和第六行补识别。
"""

import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image

from module.azur_stats.image.auto_search_reward import AutoSearchReward, AutoSearchRewardNoTitle
from module.azur_stats.scene.operation_siren import SceneOperationSiren
from module.base.button import ButtonGrid
from module.statistics.azurstats import AzurStats
from module.statistics.utils import ImageError

ORIGIN = (397, 183)
DELTA = (72.67, 75.33)
SHAPE = (64, 64)


def make_reward():
    """构造一个绕过 __init__ 的 AutoSearchReward 实例（测试用）。"""
    reward = object.__new__(AutoSearchReward)
    grid = ButtonGrid(origin=ORIGIN, delta=DELTA, button_shape=SHAPE, grid_shape=(7, 5))
    reward.auto_search_item_group.grids = grid
    return reward


def make_item(row, col, name, amount):
    """按网格行/列生成一个假的物品（只用到 area）。"""
    x1 = int(ORIGIN[0] + col * DELTA[0])
    y1 = int(ORIGIN[1] + row * DELTA[1])
    return SimpleNamespace(area=(x1, y1, x1 + SHAPE[0], y1 + SHAPE[1]),
                           name=name, amount=amount)


class TestMergeRewardPages(unittest.TestCase):
    def setUp(self):
        self.reward = make_reward()

    def patch(self, pages, shifts):
        """把解析与位移估计替换成假数据。

        Args:
            pages (list[list]): 每页返回的物品列表，页码即假截图的序号。
            shifts (list): 每页相对上一页的位移（像素）。
        """
        page_map = {index: items for index, items in enumerate(pages)}
        shifts = list(shifts)

        def fake_parse(image, name=True, amount=True, tag=True, grid=None):
            return iter(page_map[image])

        self.reward._auto_search_get_items_load = lambda image: None
        self.reward.parse_auto_search_reward = fake_parse
        self.reward.reward_list_shift = lambda reference, image, grid: (shifts.pop(0), 0.9)

    def test_overlapping_pages_are_deduplicated(self):
        # 第 1 页 2 行；第 2 页整体上移 2 行（可见行 3~7，与第 1 页无重叠）
        page1 = [make_item(0, c, f'item{c}', 1) for c in range(7)]
        page1 += [make_item(1, c, f'item{c}', 2) for c in range(7)]
        page2 = [make_item(0, c, f'item{c}', 3) for c in range(7)]
        self.patch([page1, page2], [-2 * DELTA[1]])
        merged = list(self.reward.parse_auto_search_reward_pages([0, 1]))
        self.assertEqual([(i.name, i.amount) for i in merged],
                         [(f'item{c}', 1) for c in range(7)]
                         + [(f'item{c}', 2) for c in range(7)]
                         + [(f'item{c}', 3) for c in range(7)])

    def test_overlap_rows_are_merged_not_duplicated(self):
        # 第 2 页只上移 1 行：第 2 页第 1 行就是第 1 页第 2 行，不能算两次
        page1 = [make_item(0, c, f'item{c}', 1) for c in range(7)]
        page1 += [make_item(1, c, f'item{c}', 2) for c in range(7)]
        page2 = [make_item(0, c, f'item{c}', 2) for c in range(7)]
        page2 += [make_item(1, c, f'item{c}', 5) for c in range(7)]
        self.patch([page1, page2], [-DELTA[1]])
        merged = list(self.reward.parse_auto_search_reward_pages([0, 1]))
        self.assertEqual([(i.name, i.amount) for i in merged],
                         [(f'item{c}', 1) for c in range(7)]
                         + [(f'item{c}', 2) for c in range(7)]
                         + [(f'item{c}', 5) for c in range(7)])

    def test_unknown_shift_keeps_confirmed_items_without_guessing_rows(self):
        # 无法定位的页不能按未知行号追加，否则同一物品会重复计数。
        page1 = [make_item(0, 0, 'plate', 1)]
        page2 = [make_item(0, 1, 'plan', 1)]
        page_map = {0: page1, 1: page2}
        self.reward._auto_search_get_items_load = lambda image: None
        self.reward.parse_auto_search_reward = lambda image, **kwargs: iter(page_map[image])
        self.reward.reward_list_shift = lambda reference, image, grid: None
        merged = list(self.reward.parse_auto_search_reward_pages([0, 1]))
        self.assertEqual([i.name for i in merged], ['plate'])

    def test_failed_shift_does_not_advance_reference(self):
        self.patch([[make_item(0, 0, 'plate', 1)], [], [make_item(0, 1, 'plan', 2)]], [])
        calls = []

        def measure(reference, image, grid):
            calls.append((reference, image))
            return None if image == 1 else (-DELTA[1], 0.9)

        self.reward.reward_list_shift = measure
        self.assertEqual([i.name for i in self.reward.parse_auto_search_reward_pages([0, 1, 2])],
                         ['plate', 'plan'])
        self.assertEqual(calls, [(0, 1), (0, 2)])

    def test_parse_failure_does_not_accumulate_uncommitted_shift(self):
        self.patch([[make_item(0, 0, 'plate', 1)], [], [make_item(0, 1, 'plan', 2)]], [])
        parse = self.reward.parse_auto_search_reward
        calls = []

        def measure(reference, image, grid):
            calls.append((reference, image))
            return -DELTA[1], 0.9

        def with_bad_page(image, **kwargs):
            if image == 1:
                raise ImageError('bad reward frame')
            if image == 2:
                self.assertAlmostEqual(kwargs['grid'].origin[1], ORIGIN[1] - DELTA[1])
            return parse(image, **kwargs)

        self.reward.reward_list_shift = measure
        self.reward.parse_auto_search_reward = with_bad_page
        self.assertEqual([i.name for i in self.reward.parse_auto_search_reward_pages([0, 1, 2])],
                         ['plate', 'plan'])
        self.assertEqual(calls, [(0, 1), (0, 2)])

    def test_single_page_uses_plain_parse(self):
        page = [make_item(0, 0, 'plate', 2)]
        self.patch([page], [])
        merged = list(self.reward.parse_auto_search_reward_pages([0]))
        self.assertEqual([(i.name, i.amount) for i in merged], [('plate', 2)])

    def test_empty_images(self):
        self.assertEqual(list(self.reward.parse_auto_search_reward_pages([])), [])

    def test_three_pages_accumulate_shift_and_keep_same_item_in_different_cells(self):
        self.reward._auto_search_get_items_load = lambda image: None
        self.reward.reward_list_shift = lambda reference, image, grid: (-151.0, 0.9)

        def parse(image, grid=None, **kwargs):
            return [SimpleNamespace(area=button.area, name='same_material', amount=1)
                    for button in grid.buttons
                    if round((button.area[1] - grid.origin[1]) / grid.delta[1]) < 9]

        self.reward.parse_auto_search_reward = parse
        merged = list(self.reward.parse_auto_search_reward_pages([0, 1, 2]))
        self.assertEqual(len(merged), 9 * 7)
        self.assertEqual(sum(item.amount for item in merged), 63)

    def test_bad_later_page_keeps_deduplicated_valid_pages(self):
        page1 = [make_item(0, 0, 'plate', 1)]
        page2 = [make_item(0, 1, 'plan', 2)]
        self.patch([page1, page2, []], [0, 0])
        parse = self.reward.parse_auto_search_reward

        def with_bad_page(image, **kwargs):
            if image == 2:
                raise ImageError('bad reward frame')
            return parse(image, **kwargs)

        self.reward.parse_auto_search_reward = with_bad_page
        self.assertEqual([i.name for i in self.reward.parse_auto_search_reward_pages([0, 1, 2])],
                         ['plate', 'plan'])

    def test_bad_first_page_uses_the_next_valid_grid(self):
        self.patch([[], [make_item(0, 0, 'plate', 1)]], [])

        def load(image):
            if image == 0:
                raise ImageError('covered first frame')

        self.reward._auto_search_get_items_load = load
        self.assertEqual([i.name for i in self.reward.parse_auto_search_reward_pages([0, 1])], ['plate'])


class TestScrolledRewardImages(unittest.TestCase):
    @staticmethod
    def load_panel(name):
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        path = Path(__file__).parent / 'fixtures' / 'opsi_reward_scroll' / name
        with Image.open(path) as panel:
            image[66:658, 363:935] = np.asarray(panel.convert('RGB'))
        return image

    def test_scrolled_title_is_not_needed_and_sixth_row_is_counted_once(self):
        first = self.load_panel('first.png')
        last = self.load_panel('last.png')
        scene = SceneOperationSiren()
        # 样本来自非原生720p截图，沿用运行时既有匹配模式，不改产品阈值。
        with patch('module.base.utils.TEMPLATE_MATCH_NON_NATIVE_720P', True):
            with self.assertRaises(AutoSearchRewardNoTitle):
                list(scene.parse_auto_search_reward(last, amount=False))
            first_items = list(scene.parse_auto_search_reward(first, amount=False))
            self.assertEqual(len(first_items), 35)
            self.assertNotIn('CoordinateObscure', [item.name for item in first_items])
            merged = list(scene.parse_auto_search_reward_pages([first, last, last], amount=False))
        self.assertEqual(len(merged), 36)
        obscure = [item for item in merged if item.name == 'CoordinateObscure']
        self.assertEqual(len(obscure), 1)
        self.assertEqual(obscure[0].tag, 'meow')
        self.assertEqual(tuple(obscure[0].area), (397, 517, 461, 581))
        # 保留每个原有格子，包括不同格里同名的材料，不按物品名去重。
        self.assertEqual([item.name for item in merged[:35]], [item.name for item in first_items])

    def test_grid_excludes_clipped_rows_and_includes_new_complete_rows(self):
        grid = make_reward().auto_search_item_group.grids
        for shift in (-43, -151, -302):
            with self.subTest(shift=shift):
                scrolled = AutoSearchReward._scrolled_reward_grid(grid, shift)
                self.assertTrue(scrolled.buttons)
                self.assertTrue(all(button.area[1] >= 130 and button.area[3] <= 590
                                    for button in scrolled.buttons))
                self.assertTrue(any(round((button.area[1] - scrolled.origin[1]) / DELTA[1]) >= 5
                                    for button in scrolled.buttons))

    def test_shift_measurement_covers_normal_capture_step(self):
        reward = make_reward()
        reference = np.zeros((720, 1280, 3), dtype=np.uint8)
        x1, y1, x2, y2 = reward._reward_list_area(reward.auto_search_item_group.grids)
        rng = np.random.default_rng(20261105)
        reference[y1:y2, x1:x2] = rng.integers(0, 256, (y2-y1, x2-x1, 3), dtype=np.uint8)
        for shift in (-151, -43, 0, 151):
            with self.subTest(shift=shift):
                page = np.zeros_like(reference)
                source_top, source_bottom = max(y1, y1-shift), min(y2, y2-shift)
                page[source_top+shift:source_bottom+shift, x1:x2] = reference[source_top:source_bottom, x1:x2]
                measured = reward.reward_list_shift(reference, page, reward.auto_search_item_group.grids)
                self.assertIsNotNone(measured)
                self.assertEqual(measured[0], shift)


class TestDropPageFilenames(unittest.TestCase):
    def test_single_page(self):
        self.assertEqual(AzurStats._drop_page_filenames('1789.png', 1), ['1789.png'])
        self.assertEqual(AzurStats._drop_page_filenames('1789.png', 0), ['1789.png'])

    def test_multiple_pages(self):
        self.assertEqual(AzurStats._drop_page_filenames('1789.png', 3),
                         ['1789.png', '1789_p2.png', '1789_p3.png'])
        self.assertEqual(AzurStats._drop_page_filenames('1789_x.png', 2),
                         ['1789_x.png', '1789_x_p2.png'])

    def test_page_name_keeps_imgid_prefix(self):
        # 数据库按文件名前 8 位关联，追加页不能改动前缀
        names = AzurStats._drop_page_filenames('1790323051734.png', 3)
        for name in names:
            self.assertEqual(name[:8], '17903230')
            self.assertEqual(os.path.splitext(name)[1], '.png')


if __name__ == '__main__':
    unittest.main()
