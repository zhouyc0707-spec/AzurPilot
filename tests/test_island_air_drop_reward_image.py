"""日常补给专属奖励页的真实局部图像回归，不连接设备或读取账号配置。"""

from pathlib import Path
import unittest

import numpy as np
import cv2

from module.base import utils
from module.island.assets import (
    GET_ITEMS_ISLAND,
    ISLAND_GET,
    TEMPLATE_AIR_DROP_REWARD_CLOSE,
    TEMPLATE_AIR_DROP_REWARD_TITLE,
)
from module.island.island_air_drop import is_daily_supply_reward
from module.ui.assets import ISLAND_CHECK


class IslandAirDropRewardImageTest(unittest.TestCase):
    FIXTURES = Path(__file__).parent / 'fixtures' / 'island_air_drop_reward'
    TITLE_AREA = (490, 160, 795, 240)
    CLOSE_AREA = (550, 545, 730, 595)
    OLD_REWARD_AREA = (550, 228, 725, 327)

    def setUp(self):
        previous_mode = utils.TEMPLATE_MATCH_NON_NATIVE_720P
        previous_resolution = utils.TEMPLATE_MATCH_NON_NATIVE_720P_RESOLUTION
        self.addCleanup(utils.set_template_match_non_native_720p,
                        previous_mode, previous_resolution)
        utils.set_template_match_non_native_720p(True, resolution=(2560, 1440))
        for button in (GET_ITEMS_ISLAND, ISLAND_GET, ISLAND_CHECK):
            self.addCleanup(button.clear_offset)

    def image(self, index=1):
        return utils.load_image(str(self.FIXTURES / f'reward_{index:02d}.png'))

    @staticmethod
    def erase_area(image, area):
        x1, y1, x2, y2 = area
        image[y1:y2, x1:x2] = 0

    def test_five_real_frames_match_both_strict_templates(self):
        for index in range(1, 6):
            with self.subTest(frame=index):
                image = self.image(index)
                self.assertTrue(TEMPLATE_AIR_DROP_REWARD_TITLE.match(
                    utils.crop(image, self.TITLE_AREA), similarity=0.9, direct_match=True))
                self.assertTrue(TEMPLATE_AIR_DROP_REWARD_CLOSE.match(
                    utils.crop(image, self.CLOSE_AREA), similarity=0.9, direct_match=True))
                self.assertTrue(is_daily_supply_reward(image))

    def test_original_reward_templates_do_not_recognize_actual_supply_reward(self):
        for index in range(1, 6):
            with self.subTest(frame=index):
                image = self.image(index)
                self.assertFalse(GET_ITEMS_ISLAND.match(image, offset=(20, 20)))
                self.assertFalse(ISLAND_GET.match(image, offset=(30, 30)))

    def test_title_without_close_prompt_is_not_a_complete_reward_page(self):
        image = self.image()
        self.erase_area(image, self.CLOSE_AREA)
        self.assertFalse(is_daily_supply_reward(image))

    def test_close_prompt_without_title_is_not_a_supply_reward(self):
        image = self.image()
        self.erase_area(image, self.TITLE_AREA)
        self.assertFalse(is_daily_supply_reward(image))

    def test_visible_island_background_without_reward_text_is_not_a_reward(self):
        image = utils.load_image(ISLAND_CHECK.file).copy()
        self.erase_area(image, self.TITLE_AREA)
        self.erase_area(image, self.CLOSE_AREA)
        self.assertTrue(ISLAND_CHECK.match(image))
        self.assertFalse(is_daily_supply_reward(image))

    def test_real_scene_before_reward_is_not_a_supply_reward(self):
        image = utils.load_image(str(self.FIXTURES / 'scene_before_reward.png'))
        self.assertFalse(is_daily_supply_reward(image))

    def test_high_resolution_mode_does_not_lower_title_threshold(self):
        """破损标题可越过兼容模式的通用阈值，仍不能作为专属奖励页。"""
        image = self.image(5)
        template = TEMPLATE_AIR_DROP_REWARD_TITLE.image
        y1, x1 = 172, 499
        title = image[y1:y1 + template.shape[0], x1:x1 + template.shape[1]]
        rng = np.random.default_rng(12)
        noise = rng.normal(0, 45, size=title.shape)
        title[:] = np.clip(title.astype(np.float64) + noise, 0, 255).astype(np.uint8)
        score = cv2.minMaxLoc(cv2.matchTemplate(
            utils.crop(image, self.TITLE_AREA), template, cv2.TM_CCOEFF_NORMED))[1]
        self.assertGreater(score, 0.75)
        self.assertLess(score, 0.9)
        self.assertTrue(TEMPLATE_AIR_DROP_REWARD_TITLE.match(
            utils.crop(image, self.TITLE_AREA), similarity=0.9))
        self.assertFalse(is_daily_supply_reward(image))

    def test_other_island_reward_titles_do_not_match_supply_reward(self):
        for button in (GET_ITEMS_ISLAND, ISLAND_GET):
            with self.subTest(button=button.name):
                self.assertFalse(is_daily_supply_reward(utils.load_image(button.file)))

    def test_unknown_blank_or_noise_page_is_not_a_supply_reward(self):
        for image in (np.zeros((720, 1280, 3), dtype=np.uint8),
                      np.full((720, 1280, 3), 255, dtype=np.uint8),
                      np.random.default_rng(123).integers(
                          0, 256, size=(720, 1280, 3), dtype=np.uint8)):
            with self.subTest(pixel=image[0, 0].tolist()):
                self.assertFalse(is_daily_supply_reward(image))

    def test_same_text_outside_expected_positions_is_not_a_supply_reward(self):
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        title = TEMPLATE_AIR_DROP_REWARD_TITLE.image
        close = TEMPLATE_AIR_DROP_REWARD_CLOSE.image
        image[40:40 + title.shape[0], 40:40 + title.shape[1]] = title
        image[640:640 + close.shape[0], 40:40 + close.shape[1]] = close
        self.assertTrue(TEMPLATE_AIR_DROP_REWARD_TITLE.match(
            image, similarity=0.9, direct_match=True))
        self.assertTrue(TEMPLATE_AIR_DROP_REWARD_CLOSE.match(
            image, similarity=0.9, direct_match=True))
        self.assertFalse(is_daily_supply_reward(image))

    def test_fixtures_contain_only_the_required_anonymous_regions(self):
        for index in range(1, 6):
            with self.subTest(frame=index):
                image = self.image(index)
                self.erase_area(image, self.TITLE_AREA)
                self.erase_area(image, self.CLOSE_AREA)
                self.erase_area(image, self.OLD_REWARD_AREA)
                self.assertEqual(int(np.count_nonzero(image)), 0)


if __name__ == '__main__':
    unittest.main()
