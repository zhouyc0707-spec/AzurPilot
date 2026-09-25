"""耄耋相接结算截图按高价值物品归类的单元测试。

覆盖 ``AzurStats.classify_meow_loot`` / ``meow_loot_folders`` 的分类口径，
以及 ``classify_meow_screenshot`` 的落盘归类（含多分类与兜底分类）。
"""

import os
import tempfile
import unittest
from datetime import datetime

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


class TestMeowLootMonthFolder(unittest.TestCase):
    def test_millisecond_filename(self):
        # 2026-09-16 的结算截图
        self.assertEqual(AzurStats.meow_loot_month_folder('1789490773600.png'), '26年9月')

    def test_mumu_export_filename(self):
        self.assertEqual(
            AzurStats.meow_loot_month_folder('MuMu-20260910-202852-819.png'), '26年9月')

    def test_falls_back_to_mtime(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'unknown-name.png')
            with open(path, 'wb') as f:
                f.write(b'png')
            expected = datetime.fromtimestamp(os.path.getmtime(path))
            self.assertEqual(AzurStats.meow_loot_month_folder(path),
                             f'{expected.year % 100}年{expected.month}月')

    def test_missing_file_returns_none(self):
        self.assertIsNone(AzurStats.meow_loot_month_folder('no-such-name.png'))


class TestMeowLootNameSuffix(unittest.TestCase):
    def test_single_category(self):
        self.assertEqual(
            AzurStats.meow_loot_name_suffix([('PlateGunT4', 1), ('Coins', 64)]),
            '_金菜x1')

    def test_same_category_amounts_are_summed(self):
        self.assertEqual(
            AzurStats.meow_loot_name_suffix(
                [('PlateGunT4', 1), ('PlateAntiAirT4', 2), ('Oil', 50)]),
            '_金菜x3')

    def test_multiple_categories_keep_rule_order(self):
        self.assertEqual(
            AzurStats.meow_loot_name_suffix(
                [('CatT3', 1), ('PlateTorpedoT4', 2), ('GearDesignPlanGunT5', 1)]),
            '_彩图纸x1_金菜x2_金猫箱x1')

    def test_no_high_value_item_is_empty(self):
        self.assertEqual(AzurStats.meow_loot_name_suffix([('Coins', 10)]), '')
        self.assertEqual(AzurStats.meow_loot_name_suffix([]), '')


class TestClassifyMeowScreenshot(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.folder = self._dir.name
        # 2026-09-16 的结算截图名，归类后应落在 26年9月 子文件夹
        self.month = '26年9月'

    def tearDown(self):
        self._dir.cleanup()

    def write(self, name):
        path = os.path.join(self.folder, name)
        with open(path, 'wb') as f:
            f.write(b'png')
        return path

    def test_multi_category_links_and_removes_source(self):
        source = self.write('1789490773600.png')
        targets = AzurStats.classify_meow_screenshot(
            self.folder, '1789490773600.png',
            [('PlateGunT4', 1), ('CoordinateObscure', 2), ('Coins', 30)])
        expected = '1789490773600_金菜x1_隐秘x2.png'
        self.assertEqual(sorted(os.path.relpath(t, self.folder) for t in targets),
                         sorted([os.path.join('金菜', self.month, expected),
                                 os.path.join('隐秘', self.month, expected)]))
        for target in targets:
            self.assertTrue(os.path.exists(target), target)
        self.assertFalse(os.path.exists(source))

    def test_no_high_value_item_goes_to_fallback(self):
        self.write('1789490773601.png')
        targets = AzurStats.classify_meow_screenshot(
            self.folder, '1789490773601.png', [('Coins', 64), ('Oil', 50)])
        self.assertEqual([os.path.relpath(t, self.folder) for t in targets],
                         [os.path.join('无高价值物品', self.month, '1789490773601.png')])

    def test_missing_source_is_ignored(self):
        self.assertEqual(
            AzurStats.classify_meow_screenshot(
                self.folder, 'nope.png', [('PlateGunT4', 1)]), [])


if __name__ == '__main__':
    unittest.main()
