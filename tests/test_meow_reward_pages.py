"""结算奖励多页（滚动补截）合并的单元测试。

覆盖 ``AutoSearchReward.parse_auto_search_reward_pages`` 的行对齐与去重：
奖励面板放不下时脚本会滚动补截多页，重叠行不能重复计数，也不能漏。
解析与位移估计在测试里替换成假数据，只验证合并算法本身。
"""

import os
import unittest
from types import SimpleNamespace

from module.azur_stats.image.auto_search_reward import AutoSearchReward
from module.base.button import ButtonGrid
from module.statistics.azurstats import AzurStats

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

        def fake_parse(image, name=True, amount=True, tag=True):
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

    def test_unknown_shift_keeps_all_items(self):
        # 位移估计失败时按整页追加（允许重复），不能丢物品
        page1 = [make_item(0, 0, 'plate', 1)]
        page2 = [make_item(0, 1, 'plan', 1)]
        page_map = {0: page1, 1: page2}
        self.reward._auto_search_get_items_load = lambda image: None
        self.reward.parse_auto_search_reward = lambda image, **kwargs: iter(page_map[image])
        self.reward.reward_list_shift = lambda reference, image, grid: None
        merged = list(self.reward.parse_auto_search_reward_pages([0, 1]))
        self.assertEqual([i.name for i in merged], ['plate', 'plan'])

    def test_single_page_uses_plain_parse(self):
        page = [make_item(0, 0, 'plate', 2)]
        self.patch([page], [])
        merged = list(self.reward.parse_auto_search_reward_pages([0]))
        self.assertEqual([(i.name, i.amount) for i in merged], [('plate', 2)])

    def test_empty_images(self):
        self.assertEqual(list(self.reward.parse_auto_search_reward_pages([])), [])


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
