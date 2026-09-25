"""耄耋相接结算截图按高价值物品归类的单元测试。

覆盖 ``AzurStats.classify_meow_loot`` / ``meow_loot_folders`` 的分类口径，
以及 ``classify_meow_screenshot`` 的落盘归类（含多分类与兜底分类）。
"""

import os
import tempfile
import unittest

from module.statistics.azurstats import AzurStats


class TestClassifyMeowLoot(unittest.TestCase):
    def test_high_value_items(self):
        cases = {
            'PlateGunT4': 'Plate',
            'PlateAntiAirT4': 'Plate',
            'GearDesignPlanGunT5': 'GearDesignPlanT5',
            'OrdnanceTestingReportT4': 'OrdnanceTestingReportT4',
            'CoordinateObscure': 'CoordinateObscure',
            'CoordinateAbyssal': 'CoordinateAbyssal',
            'CatT3': 'CatT3',
        }
        for name, expected in cases.items():
            self.assertEqual(AzurStats.classify_meow_loot(name), expected, name)

    def test_lower_tiers_are_not_high_value(self):
        # 金图纸 T4、报告 T3 等低一级物品不算高价值
        for name in ('GearDesignPlanGunT4', 'OrdnanceTestingReportT3', 'CatT2',
                     'Coins', 'Oil', 'OperationCoin', '71', ''):
            self.assertIsNone(AzurStats.classify_meow_loot(name), name)


class TestMeowLootFolders(unittest.TestCase):
    def test_single_category(self):
        self.assertEqual(
            AzurStats.meow_loot_folders(['Coins', 'PlateGunT4', 'Oil']), ['金菜'])

    def test_multiple_categories_keep_rule_order(self):
        self.assertEqual(
            AzurStats.meow_loot_folders(
                ['CatT3', 'PlateTorpedoT4', 'GearDesignPlanGunT5']),
            ['彩图纸', '金菜', '金猫箱'])

    def test_no_high_value_item_falls_back(self):
        self.assertEqual(AzurStats.meow_loot_folders(['Coins', 'Oil']),
                         ['无高价值物品'])
        self.assertEqual(AzurStats.meow_loot_folders([]), ['无高价值物品'])


class TestClassifyMeowScreenshot(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.folder = self._dir.name

    def tearDown(self):
        self._dir.cleanup()

    def write(self, name):
        path = os.path.join(self.folder, name)
        with open(path, 'wb') as f:
            f.write(b'png')
        return path

    def test_multi_category_links_and_removes_source(self):
        source = self.write('1789.png')
        targets = AzurStats.classify_meow_screenshot(
            self.folder, '1789.png', ['PlateGunT4', 'CoordinateObscure'])
        self.assertEqual(sorted(os.path.relpath(t, self.folder) for t in targets),
                         sorted([os.path.join('金菜', '1789.png'),
                                 os.path.join('隐秘', '1789.png')]))
        for target in targets:
            self.assertTrue(os.path.exists(target), target)
        self.assertFalse(os.path.exists(source))

    def test_no_high_value_item_goes_to_fallback(self):
        self.write('1790.png')
        targets = AzurStats.classify_meow_screenshot(
            self.folder, '1790.png', ['Coins', 'Oil'])
        self.assertEqual([os.path.relpath(t, self.folder) for t in targets],
                         [os.path.join('无高价值物品', '1790.png')])

    def test_missing_source_is_ignored(self):
        self.assertEqual(
            AzurStats.classify_meow_screenshot(self.folder, 'nope.png', ['PlateGunT4']), [])


if __name__ == '__main__':
    unittest.main()
