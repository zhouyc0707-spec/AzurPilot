"""remove_small_fragments 单元测试。

覆盖：图标碎块过滤、数字笔画保留、空白图像、全碎块图像等场景。
"""
import unittest

import numpy as np

from module.statistics.item import remove_small_fragments


class TestRemoveSmallFragments(unittest.TestCase):
    def _make_image(self, height=21, width=41):
        return np.full((height, width), 255, dtype=np.uint8)

    def test_digit_strokes_are_kept(self):
        """高数字笔画（h>=6）应保留。"""
        image = self._make_image()
        # 两个竖笔画，高 14，模拟 "11"
        image[4:18, 25:28] = 0
        image[4:18, 33:36] = 0
        result = remove_small_fragments(image)
        self.assertEqual((result[4:18, 25:28] == 0).all(), True)
        self.assertEqual((result[4:18, 33:36] == 0).all(), True)

    def test_small_fragments_are_removed(self):
        """低碎块（h<6）应被置为背景。"""
        image = self._make_image()
        image[2:4, 3:8] = 0  # h=2 的碎块
        image[0:3, 13:17] = 0  # h=3 的碎块
        result = remove_small_fragments(image)
        self.assertEqual((result == 255).all(), True)

    def test_mixed_fragments_and_digits(self):
        """混合场景：碎块剔除、数字保留，其余为背景。"""
        image = self._make_image()
        image[2:5, 4:9] = 0  # 碎块
        image[4:18, 20:26] = 0  # 数字笔画
        result = remove_small_fragments(image)
        self.assertEqual((result[2:5, 4:9] == 255).all(), True)
        self.assertEqual((result[4:18, 20:26] == 0).all(), True)

    def test_empty_image(self):
        """空白图像不崩溃，返回全背景。"""
        image = self._make_image()
        result = remove_small_fragments(image)
        self.assertEqual((result == 255).all(), True)

    def test_all_fragments_image_becomes_blank(self):
        """全部组件都是碎块时，输出为全背景。"""
        image = self._make_image()
        image[0:2, 0:2] = 0
        image[5:7, 10:13] = 0
        result = remove_small_fragments(image)
        self.assertEqual((result == 255).all(), True)

    def test_input_is_not_mutated(self):
        """不应修改输入图像。"""
        image = self._make_image()
        image[2:5, 4:9] = 0
        snapshot = image.copy()
        remove_small_fragments(image)
        self.assertEqual((image == snapshot).all(), True)


if __name__ == "__main__":
    unittest.main()
