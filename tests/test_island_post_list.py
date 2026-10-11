"""岗位列表空格识别与两帧巡检确认的离线回归。"""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from module.base.button import Button
from module.base.utils import load_image
from module.island.assets import ISLAND_POST_CHECK, TEMPLATE_POST_EMPTY
from module.island.island import Island
from module.island.post_list import is_empty_post_slot, is_post_title_confirmed
from module.island_farm.assets import ISLAND_FARM_POST2
from module.island_manufacture.assets import (
    ISLAND_ELECTRONIC_PROCESSING_POST1, ISLAND_WOOD_PROCESSING_POST1,
)
from module.ui.page import page_island_postmanage


FIXTURES = Path(__file__).parent / 'fixtures/island_post_list'
POSITION = (350, 260, 430, 340)
EMPTY_FIXTURES = (
    'empty_current_farm', 'empty_current_mine', 'empty_current_forest',
    'empty_current_orchard', 'empty_september_farm', 'empty_food_grill',
    'empty_food_industrial', 'empty_food_electronic',
)
OCCUPIED_FIXTURES = (
    'working_current_farm', 'working_current_fishery', 'complete_current_ranch',
    'working_september_orchard', 'working_food_wood', 'complete_food_juu',
    'working_scrolled_restaurant', 'complete_scrolled_restaurant',
    'locked_reconstructed',
)
UNCERTAIN_FIXTURES = ('empty_scrolled_nursery',)


def fixture_frame(name, position=POSITION):
    """将匿名岗位裁剪放回截图尺寸，不包含其他游戏或账号画面。"""
    image = np.full((720, 1280, 3), 255, dtype=np.uint8)
    cell = load_image(str(FIXTURES / f'{name}.png'))
    x1, y1, x2, y2 = position
    cx, cy = round((x1 + x2) / 2), round((y1 + y2) / 2)
    image[cy - 45:cy + 45, cx - 45:cx + 45] = cell
    return image


def shifted_position(dx=0, dy=0):
    x1, y1, x2, y2 = POSITION
    return x1 + dx, y1 + dy, x2 + dx, y2 + dy


def state(name='empty_current_farm', *, position=POSITION, page=True, detail=False, title=True):
    return {
        'image': fixture_frame(name, position), 'position': position,
        'page': page, 'detail': detail, 'title': title,
    }


class ListFrameDevice:
    def __init__(self, frames):
        self.frames = frames
        self.index = 0
        self.image = frames[0]['image']
        self.screenshot_count = 0
        self.click = Mock()

    def screenshot(self):
        self.screenshot_count += 1
        self.index = min(self.index + 1, len(self.frames) - 1)
        self.image = self.frames[self.index]['image']
        return self.image


class ListFrameIsland:
    post_list_is_empty = Island.post_list_is_empty

    def __init__(self, frames):
        self.device = ListFrameDevice(frames)
        self.appear_calls = []
        self.post = Button(area=(200, 100, 260, 125), color=(120, 190, 220),
                           button=POSITION, name='POST_LIST_TEST')

    @property
    def current(self):
        return self.device.frames[self.device.index]

    def ui_page_appear(self, page):
        return self.current['page']

    def is_post_detail_visible(self):
        return self.current['detail']

    def appear(self, post, offset=0):
        self.appear_calls.append((post, offset))
        if self.current['title']:
            post._button_offset = self.current['position']
            return True
        return False


class EmptyPostSlotVisionTests(unittest.TestCase):
    def test_real_empty_slots_across_lists_and_capture_dates(self):
        for name in EMPTY_FIXTURES:
            with self.subTest(fixture=name):
                self.assertTrue(is_empty_post_slot(fixture_frame(name), POSITION))

    def test_working_complete_and_locked_slots_are_not_empty(self):
        for name in OCCUPIED_FIXTURES:
            with self.subTest(fixture=name):
                self.assertFalse(is_empty_post_slot(fixture_frame(name), POSITION))

    def test_actual_template_scores_keep_positive_negative_margin(self):
        for name in EMPTY_FIXTURES + OCCUPIED_FIXTURES + UNCERTAIN_FIXTURES:
            with self.subTest(fixture=name):
                cell = load_image(str(FIXTURES / f'{name}.png'))
                score = float(cv2.matchTemplate(cell, TEMPLATE_POST_EMPTY.image,
                                               cv2.TM_CCOEFF_NORMED).max())
                if name in EMPTY_FIXTURES:
                    self.assertGreater(score, 0.95, f'{name}: {score:.5f}')
                else:
                    self.assertLessEqual(score, 0.95, f'{name}: {score:.5f}')

    def test_user_template_threshold_cannot_weaken_empty_confirmation(self):
        with patch('module.base.template.lower_template_match_similarity',
                   side_effect=AssertionError('空岗不得受全局降阈值影响')):
            self.assertTrue(is_empty_post_slot(fixture_frame('empty_current_farm'), POSITION))
            self.assertFalse(is_empty_post_slot(fixture_frame('complete_current_ranch'), POSITION))

    def test_non_native_global_threshold_does_not_accept_uncertain_empty_card(self):
        with patch('module.base.utils.TEMPLATE_MATCH_NON_NATIVE_720P', True), \
                patch('module.base.utils.TEMPLATE_MATCH_NON_NATIVE_720P_THRESHOLD', 0.75):
            self.assertTrue(is_empty_post_slot(fixture_frame('empty_current_farm'), POSITION))
            self.assertFalse(is_empty_post_slot(fixture_frame('empty_scrolled_nursery'), POSITION))

    def test_low_score_real_empty_card_keeps_normal_detail_fallback(self):
        self.assertFalse(is_empty_post_slot(fixture_frame('empty_scrolled_nursery'), POSITION))

    def test_popup_dimmed_card_cannot_be_confirmed_as_empty(self):
        for brightness in (0.35, 0.6, 0.8):
            with self.subTest(brightness=brightness):
                image = (fixture_frame('empty_current_farm').astype(np.float32)
                         * brightness).astype(np.uint8)
                self.assertFalse(is_empty_post_slot(image, POSITION))

    def test_colored_overlay_cannot_be_confirmed_as_gray_empty_card(self):
        for tint in ((15, 0, 0), (0, 20, 0), (0, 0, 20)):
            with self.subTest(tint=tint):
                image = np.clip(fixture_frame('empty_current_farm').astype(np.int16)
                                + np.array(tint, dtype=np.int16), 0, 255).astype(np.uint8)
                self.assertFalse(is_empty_post_slot(image, POSITION))

    def test_unknown_blank_area_does_not_mean_empty(self):
        self.assertFalse(is_empty_post_slot(np.full((720, 1280, 3), 224, dtype=np.uint8), POSITION))

    def test_partially_occluded_card_falls_back(self):
        image = fixture_frame('empty_current_farm')
        image[285:345, 387:435] = (60, 100, 140)
        self.assertFalse(is_empty_post_slot(image, POSITION))

    def test_full_card_must_stay_inside_list_and_visible_scroll_area(self):
        image = fixture_frame('empty_current_farm')
        for position in ((160, 260, 240, 340), (1200, 260, 1280, 340),
                         (350, 80, 430, 160), (350, 610, 430, 690)):
            with self.subTest(position=position):
                self.assertFalse(is_empty_post_slot(image, position))

    def test_non_game_dimensions_and_invalid_rectangles_fall_back(self):
        for image in (None, np.zeros((1440, 2560, 3), dtype=np.uint8),
                      np.zeros((720, 1280), dtype=np.uint8)):
            with self.subTest(shape=getattr(image, 'shape', None)):
                self.assertFalse(is_empty_post_slot(image, POSITION))
        for position in ((350, 260, 350, 340), (350, 340, 430, 260), (1, 2, 3)):
            with self.subTest(position=position):
                self.assertFalse(is_empty_post_slot(fixture_frame('empty_current_farm'), position))


class EmptyPostListConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.island.island.logger'))
        # 此组只验证状态编排；真实标题与偏移由下一组完整模板匹配测试覆盖。
        self.enterContext(patch('module.island.island.is_post_title_confirmed', return_value=True))

    def test_two_empty_frames_confirm_without_clicking(self):
        task = ListFrameIsland([state(), state()])
        self.assertTrue(task.post_list_is_empty(task.post))
        self.assertEqual(task.device.screenshot_count, 1)
        self.assertEqual([offset for _, offset in task.appear_calls], [300, 300])
        task.device.click.assert_not_called()

    def test_occupied_slot_does_not_add_a_screenshot(self):
        for fixture in OCCUPIED_FIXTURES + UNCERTAIN_FIXTURES:
            with self.subTest(fixture=fixture):
                task = ListFrameIsland([state(fixture)])
                self.assertFalse(task.post_list_is_empty(task.post))
                self.assertEqual(task.device.screenshot_count, 0)
                task.device.click.assert_not_called()

    def test_candidate_changes_to_working_and_uses_normal_detail_path(self):
        task = ListFrameIsland([state(), state('working_current_farm')])
        self.assertFalse(task.post_list_is_empty(task.post))
        self.assertEqual(task.device.screenshot_count, 1)
        task.device.click.assert_not_called()

    def test_candidate_moves_more_than_two_pixels_and_falls_back(self):
        task = ListFrameIsland([state(), state(position=shifted_position(dy=3))])
        self.assertFalse(task.post_list_is_empty(task.post))
        self.assertEqual(task.device.screenshot_count, 1)

    def test_small_position_jitter_uses_matched_current_coordinates(self):
        task = ListFrameIsland([state(), state(position=shifted_position(dx=2, dy=-2))])
        self.assertTrue(task.post_list_is_empty(task.post))
        self.assertEqual(task.device.screenshot_count, 1)

    def test_stale_position_after_failed_title_match_is_never_used(self):
        task = ListFrameIsland([state(title=False)])
        task.post._button_offset = POSITION
        self.assertFalse(task.post_list_is_empty(task.post))
        self.assertEqual(task.device.screenshot_count, 0)

    def test_initial_page_or_detail_precondition_rejects_without_screenshot(self):
        for overrides in ({'page': False}, {'detail': True}, {'title': False}):
            with self.subTest(overrides=overrides):
                task = ListFrameIsland([state(**overrides)])
                self.assertFalse(task.post_list_is_empty(task.post))
                self.assertEqual(task.device.screenshot_count, 0)

    def test_second_frame_page_detail_or_title_change_falls_back(self):
        for overrides in ({'page': False}, {'detail': True}, {'title': False}):
            with self.subTest(overrides=overrides):
                task = ListFrameIsland([state(), state(**overrides)])
                self.assertFalse(task.post_list_is_empty(task.post))
                self.assertEqual(task.device.screenshot_count, 1)

    def test_non_button_input_is_never_treated_as_empty(self):
        task = ListFrameIsland([state()])
        self.assertFalse(task.post_list_is_empty(SimpleNamespace(button=POSITION)))
        self.assertEqual(task.device.screenshot_count, 0)


class EmptyPostListRealLocationTests(unittest.TestCase):
    """真实页面模板和岗位标题定位，不模拟 appear 或 button 偏移结果。"""

    def setUp(self):
        self.enterContext(patch('module.island.island.logger'))
        # 当前实拍源为非原生 720p，页面／标题沿用运行时 .75 匹配；空卡仍单独严格 .95。
        self.enterContext(patch('module.base.utils.TEMPLATE_MATCH_NON_NATIVE_720P', True))
        self.enterContext(patch('module.base.utils.TEMPLATE_MATCH_NON_NATIVE_720P_THRESHOLD', 0.75))

    @staticmethod
    def make_frame(*, dy=0, header=True, title=True, fixture='empty_current_farm', detail=False):
        position = tuple(value + (dy if index % 2 else 0)
                         for index, value in enumerate(ISLAND_FARM_POST2._button))
        image = fixture_frame(fixture, position)
        if header:
            image[20:45, 122:219] = load_image(str(FIXTURES / 'page_header.png'))
        if title:
            image[79 + dy:105 + dy, 198:287] = load_image(str(FIXTURES / 'farm_title.png'))
        if detail:
            x1, y1, x2, y2 = ISLAND_POST_CHECK.area
            image[y1:y2, x1:x2] = load_image(ISLAND_POST_CHECK.file)[y1:y2, x1:x2]
        return image

    @staticmethod
    def make_task(image, source=ISLAND_FARM_POST2):
        task = Island.__new__(Island)
        task.config = SimpleNamespace(SERVER='cn', BUTTON_OFFSET=30)
        task.interval_timer = {}
        task.device = ListFrameDevice([{'image': image}, {'image': image.copy()}])
        task.device.stuck_record_add = Mock()
        post = Button(area=source.raw_area, color=source.raw_color,
                      button=source.raw_button, file=source.raw_file,
                      name=f'{source.name}_REAL_LOCATION_TEST')
        return task, post

    @staticmethod
    def make_wood_frame(*, electronic=False, weak=False):
        wood = ISLAND_WOOD_PROCESSING_POST1
        title_source = ISLAND_ELECTRONIC_PROCESSING_POST1 if electronic else wood
        dy = 182 if electronic else 0
        position = tuple(value + (dy if index % 2 else 0)
                         for index, value in enumerate(wood._button))
        image = fixture_frame('empty_current_farm', position)
        image[20:45, 122:219] = load_image(str(FIXTURES / 'page_header.png'))
        x1, y1, x2, y2 = title_source.area
        title = load_image(title_source.file)[y1:y2, x1:x2].copy()
        if weak:
            # 固定微量噪声让正确标题相关分约 .869，模拟放宽阈值能定位但不足以安全跳过。
            noise = np.random.default_rng(12012).normal(size=title.shape)
            title = np.clip(title.astype(np.float64) + noise * 30, 0, 255).astype(np.uint8)
        image[y1:y2, x1:x2] = title
        return image

    def test_real_header_and_farm_title_confirm_empty_without_clicking(self):
        task, post = self.make_task(self.make_frame())
        self.assertTrue(task.ui_page_appear(page_island_postmanage))
        self.assertFalse(task.is_post_detail_visible())
        self.assertTrue(task.post_list_is_empty(post))
        self.assertEqual(tuple(post.button), ISLAND_FARM_POST2._button)
        self.assertEqual(task.device.screenshot_count, 1)
        task.device.click.assert_not_called()

    def test_real_title_matching_tracks_scrolled_empty_cell(self):
        dy = 170
        task, post = self.make_task(self.make_frame(dy=dy))
        self.assertTrue(task.post_list_is_empty(post))
        x1, y1, x2, y2 = ISLAND_FARM_POST2._button
        self.assertEqual(tuple(post.button), (x1, y1 + dy, x2, y2 + dy))
        self.assertEqual(task.device.screenshot_count, 1)
        task.device.click.assert_not_called()

    def test_missing_real_page_header_cannot_skip_inspection(self):
        task, post = self.make_task(self.make_frame(header=False))
        self.assertFalse(task.ui_page_appear(page_island_postmanage))
        self.assertFalse(task.post_list_is_empty(post))
        self.assertEqual(task.device.screenshot_count, 0)

    def test_missing_real_title_cannot_use_unmatched_position(self):
        task, post = self.make_task(self.make_frame(title=False))
        self.assertTrue(task.ui_page_appear(page_island_postmanage))
        self.assertFalse(task.post_list_is_empty(post))
        self.assertEqual(task.device.screenshot_count, 0)

    def test_real_detail_template_rejects_background_empty_card(self):
        task, post = self.make_task(self.make_frame(detail=True))
        self.assertTrue(task.is_post_detail_visible())
        self.assertFalse(task.post_list_is_empty(post))
        self.assertEqual(task.device.screenshot_count, 0)

    def test_real_title_with_occupied_card_keeps_inspection_without_extra_frame(self):
        task, post = self.make_task(self.make_frame(fixture='working_current_farm'))
        self.assertTrue(task.ui_page_appear(page_island_postmanage))
        self.assertFalse(task.post_list_is_empty(post))
        self.assertEqual(task.device.screenshot_count, 0)

    def test_matching_wood_title_and_empty_card_can_skip_inspection(self):
        task, post = self.make_task(self.make_wood_frame(), ISLAND_WOOD_PROCESSING_POST1)
        self.assertTrue(task.appear(post, offset=300))
        self.assertTrue(is_post_title_confirmed(task.device.image, post))
        self.assertTrue(task.post_list_is_empty(post))
        self.assertEqual(task.device.screenshot_count, 1)
        task.device.click.assert_not_called()

    def test_electronic_title_cannot_skip_absent_wood_post(self):
        task, post = self.make_task(self.make_wood_frame(electronic=True),
                                    ISLAND_WOOD_PROCESSING_POST1)
        # 全局 .75 会让木料标题误匹配电子标题，严格标题复核必须阻止跳过木料巡检。
        self.assertTrue(task.appear(post, offset=300))
        self.assertFalse(is_post_title_confirmed(task.device.image, post))
        self.assertFalse(task.post_list_is_empty(post))
        self.assertEqual(task.device.screenshot_count, 0)
        task.device.click.assert_not_called()

    def test_weak_correct_wood_title_uses_normal_detail_inspection(self):
        task, post = self.make_task(self.make_wood_frame(weak=True), ISLAND_WOOD_PROCESSING_POST1)
        self.assertTrue(task.appear(post, offset=300))
        self.assertFalse(is_post_title_confirmed(task.device.image, post))
        self.assertFalse(task.post_list_is_empty(post))
        self.assertEqual(task.device.screenshot_count, 0)
        task.device.click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
