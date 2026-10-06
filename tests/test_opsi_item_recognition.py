"""用原始掉落截图的图标裁图验证新增统计物品的识别与数量。"""

import json
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from module.azur_stats.scene.operation_siren import SceneOperationSiren
from module.base.button import ButtonGrid


FIXTURES = Path(__file__).parent / 'fixtures' / 'opsi_requested_items'
ASSETS = Path(__file__).resolve().parents[1] / 'assets'


class TestRequestedItemRecognition(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads((FIXTURES / 'cases.json').read_text(encoding='utf-8'))
        cls.scene = SceneOperationSiren()
        cls.scene.server = 'cn'

    def test_native_items_and_amounts(self):
        """材料、金计划和彩突破部件使用真实数量，紫计划和金突破部件保留原稀有度。"""
        for case in self.cases:
            with self.subTest(file=case['file']):
                image = np.array(Image.open(FIXTURES / case['file']).convert('RGB'))
                grid = self.scene.item_grid if case['layout'] == 'popup' else self.scene.auto_search_item_group
                size = 96 if case['layout'] == 'popup' else 64
                grid.grids = ButtonGrid(origin=(0, 0), delta=(size, size),
                                        button_shape=(size, size), grid_shape=(1, 1))
                grid.predict(image, tag=False)
                self.assertEqual([(item.name, item.amount) for item in grid.items],
                                 [(case['item'], case['amount'])])

    def test_third_popup_layout_keeps_boss_upgrade_part(self):
        image = np.array(Image.open(FIXTURES / 'boss_get_items_3.png').convert('RGB'))
        scene = SceneOperationSiren()
        scene.server = 'cn'
        self.assertTrue(scene.is_get_items(image))
        scene.load_file([image])
        rows = list(scene.parse_scene())
        parts = [row for row in rows if row.item == 'PrototypeGearPartsT5']
        self.assertEqual([(row.zone_type, row.amount) for row in parts], [('UNKNOWN', 1)])

    def test_upgrade_parts_keep_strict_similarity(self):
        """按底色筛选突破部件时，不沿用纸类动画模板的宽松阈值。"""
        grid = SceneOperationSiren().item_grid
        grid._matching_tier = 'T5'
        self.assertEqual(grid.template_similarity_for('PrototypeGearPartsT5', 0.92), 0.92)
        self.assertEqual(grid.template_similarity_for('GearDesignPlanPlaneT5', 0.92), 0.6)
        self.assertEqual(grid.template_similarity_for('OrdnanceTestingReportT5', 0.92), 0.6)


class TestRepairPackRecognition(unittest.TestCase):
    """用真实模板集区分单舰、三舰维修包和全舰维修套装，不读取数量或标签。"""

    def setUp(self):
        scene = SceneOperationSiren()
        scene.server = 'cn'
        self.grid = scene.auto_search_item_group
        self.grid.grids = ButtonGrid(
            origin=(0, 0), delta=(96, 96), button_shape=(96, 96), grid_shape=(1, 1)
        )

    def assert_item_name(self, relative_path, expected):
        with Image.open(ASSETS / relative_path) as source:
            # 输入用已有无标注原图，96 像素保持与生产模板的裁剪坐标一致。
            self.assertEqual(source.size, (96, 96))
            image = np.array(source.convert('RGB'))
        self.grid.predict(image, name=True, amount=False, tag=False)
        self.assertEqual([item.name for item in self.grid.items], [expected])

    def test_full_repair_pack_variants_keep_distinct_names(self):
        """交替识别普通与豪华套装，命中频率变化也不能把两种套装混为同名。"""
        for suffix in ('', '_0', '_2'):
            for name in ('RepairPackFull', 'RepairPackFull2'):
                with self.subTest(item=name, suffix=suffix):
                    self.assert_item_name(f'shop/os/{name}{suffix}.png', name)

    def test_existing_single_and_triple_repair_packs_keep_original_names(self):
        """新增全舰维修套装不得抢走既有四种单舰、三舰维修包的匹配。"""
        for name in (
            'EmergencyRepairPack', 'Complete RepairPack',
            'TripleEmergencyRepairPack', 'TripleComplete RepairPack',
        ):
            with self.subTest(item=name):
                self.assert_item_name(f'stats/opsi_reward_items/{name}.png', name)

    def test_unrelated_rewards_are_not_matched_as_repair_packs(self):
        """先提高套装命中频率，再确认其他奖励仍由原物品模板识别。"""
        self.assert_item_name('shop/os/RepairPackFull_0.png', 'RepairPackFull')
        self.assert_item_name('shop/os/RepairPackFull2_0.png', 'RepairPackFull2')
        for name in ('Coins', 'ActionPoint20', 'CoordinateObscure'):
            with self.subTest(item=name):
                self.assert_item_name(f'stats/opsi_reward_items/{name}.png', name)

    def test_repair_packs_keep_existing_similarity_threshold(self):
        """维修套装无需白纸类动画的阈值放宽。"""
        self.assertEqual(self.grid.similarity, 0.85)
        self.grid._matching_tier = 'T3'
        for name in ('RepairPackFull', 'RepairPackFull2'):
            with self.subTest(item=name):
                self.assertEqual(self.grid.template_similarity_for(name, 0.85), 0.85)


if __name__ == '__main__':
    unittest.main()
