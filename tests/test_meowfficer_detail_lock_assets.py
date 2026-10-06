"""天赋页标记及锁状态实拍裁剪资源的离线验证，不连接设备或读取用户配置。"""

from pathlib import Path
import unittest

import cv2
import numpy as np
from PIL import Image

from module.base.template import Template
from module.meowfficer import assets


ROOT = Path(__file__).resolve().parents[1]
LOCK_AREA = (33, 454, 75, 511)
MATCH_AREA = (16, 440, 92, 526)
TALENT_AREA = (795, 100, 920, 132)
TALENT_MATCH_AREA = (770, 85, 955, 150)


class TestMeowfficerDetailLockAssets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.images = {}
        cls.templates = {}
        for state in ('LOCKED', 'UNLOCKED'):
            path = ROOT / 'assets' / 'cn' / 'meowfficer' / f'TEMPLATE_MEOWFFICER_DETAIL_{state}.png'
            with Image.open(path) as source:
                cls.images[state] = np.array(source.convert('RGB'))
            cls.templates[state] = Template(file=str(path))
        path = ROOT / 'assets' / 'cn' / 'meowfficer' / 'TEMPLATE_MEOWFFICER_DETAIL_TALENT_CHECK.png'
        with Image.open(path) as source:
            cls.talent_image = np.array(source.convert('RGB'))
        cls.talent_template = Template(file=str(path))

    @staticmethod
    def canvas(image, dx=0, dy=0):
        """仅把按钮局部放回原坐标，不保存账号、猫名或资源数量。"""
        result = np.zeros((720, 1280, 3), dtype=np.uint8)
        x1, y1, x2, y2 = LOCK_AREA
        result[y1 + dy:y2 + dy, x1 + dx:x2 + dx] = image
        return result

    def assert_state(self, image, expected):
        x1, y1, x2, y2 = MATCH_AREA
        region = image[y1:y2, x1:x2]
        matches = {
            state: template.match(region, similarity=0.9)
            for state, template in self.templates.items()
        }
        self.assertEqual(matches, {state: state == expected for state in matches})

    def talent_appears(self, image):
        x1, y1, x2, y2 = TALENT_MATCH_AREA
        return self.talent_template.match(image[y1:y2, x1:x2], similarity=0.9)

    def add_talent_header(self, image, dx=0, dy=0):
        x1, y1, x2, y2 = TALENT_AREA
        image[y1 + dy:y2 + dy, x1 + dx:x2 + dx] = self.talent_image
        return image

    def test_real_cropped_locked_and_unlocked_are_exclusive(self):
        for state, image in self.images.items():
            with self.subTest(state=state):
                self.assert_state(self.canvas(image), state)

    def test_opposite_state_similarity_is_below_detection_threshold(self):
        locked = self.images['LOCKED']
        unlocked = self.images['UNLOCKED']
        similarity = float(cv2.matchTemplate(locked, unlocked, cv2.TM_CCOEFF_NORMED)[0, 0])
        self.assertLess(similarity, 0.75)

    def test_small_position_changes_keep_state_exclusive(self):
        for state, image in self.images.items():
            for dx, dy in ((-5, -4), (5, 4)):
                with self.subTest(state=state, dx=dx, dy=dy):
                    self.assert_state(self.canvas(image, dx, dy), state)

    def test_blank_gray_and_unknown_pattern_are_not_lock_states(self):
        for color in ((0, 0, 0), (140, 140, 140), (255, 255, 255)):
            with self.subTest(color=color):
                self.assert_state(np.full((720, 1280, 3), color, dtype=np.uint8), None)
        rng = np.random.default_rng(7107)
        self.assert_state(rng.integers(0, 256, size=(720, 1280, 3), dtype=np.uint8), None)

    def test_talent_header_and_lock_state_are_both_positive(self):
        for state, image in self.images.items():
            with self.subTest(state=state):
                canvas = self.canvas(image)
                self.assertFalse(self.talent_appears(canvas))
                self.assert_state(canvas, state)
                self.add_talent_header(canvas)
                self.assertTrue(self.talent_appears(canvas))
                self.assert_state(canvas, state)
        header_only = self.add_talent_header(np.zeros((720, 1280, 3), dtype=np.uint8))
        self.assertTrue(self.talent_appears(header_only))
        self.assert_state(header_only, None)

    def test_talent_header_small_position_changes_remain_recognized(self):
        for dx, dy in ((-5, -4), (5, 4)):
            with self.subTest(dx=dx, dy=dy):
                canvas = np.zeros((720, 1280, 3), dtype=np.uint8)
                self.assertTrue(self.talent_appears(self.add_talent_header(canvas, dx, dy)))

    def test_blank_and_unknown_pattern_are_not_talent_page(self):
        for color in ((0, 0, 0), (140, 140, 140), (255, 255, 255)):
            with self.subTest(color=color):
                self.assertFalse(self.talent_appears(np.full((720, 1280, 3), color, dtype=np.uint8)))
        rng = np.random.default_rng(7108)
        self.assertFalse(self.talent_appears(rng.integers(0, 256, size=(720, 1280, 3), dtype=np.uint8)))

    def test_only_cn_files_are_added_and_contain_only_button_crop(self):
        for state, size in (('LOCKED', (42, 57)), ('UNLOCKED', (42, 57)), ('TALENT_CHECK', (125, 32))):
            with self.subTest(state=state):
                filename = f'TEMPLATE_MEOWFFICER_DETAIL_{state}.png'
                resource = getattr(assets, f'TEMPLATE_MEOWFFICER_DETAIL_{state}')
                self.assertEqual(resource.raw_file['cn'], f'./assets/cn/meowfficer/{filename}')
                for server in ('en', 'jp', 'tw'):
                    self.assertFalse((ROOT / 'assets' / server / 'meowfficer' / filename).exists())
                    # 生成器默认回退 CN；自动操作必须由调用方显式限制国服。
                    self.assertEqual(resource.raw_file[server], resource.raw_file['cn'])
                with Image.open(ROOT / resource.raw_file['cn']) as source:
                    self.assertEqual(source.size, size)
                    self.assertEqual(source.info, {})


if __name__ == '__main__':
    unittest.main()
