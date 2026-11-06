"""导航与空岗跳过使用各自证明门槛，兼容标题独立参与定位。"""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from module.base.button import Button
from module.base.utils import load_image
from module.island.assets import ISLAND_POST_VACANT_CHECK, TEMPLATE_POST_ORCHARD_TITLE_COMPAT
from module.island.island import Island
from module.island.post_list import is_post_title_confirmed, match_post_title
from module.island_farm.assets import (
    ISLAND_ORCHARD_POST1, ISLAND_ORCHARD_POST2, ISLAND_ORCHARD_POST3, ISLAND_ORCHARD_POST4,
)
from module.island_manufacture.assets import ISLAND_ELECTRONIC_PROCESSING_POST1, ISLAND_WOOD_PROCESSING_POST1


FIXTURES = Path(__file__).parent / 'fixtures/island_post_list'


def cn_button(source):
    """真实 CN 夹具使用独立 CN 对象，隔离前序跨服测试留下的资源缓存。"""
    return Button(area=source.raw_area['cn'], color=source.raw_color['cn'],
                  button=source.raw_button['cn'], file=source.raw_file['cn'], name=source.name)


def title_frame(source, *, weak=False):
    post = cn_button(source)
    post.ensure_template()
    image = np.full((720, 1280, 3), 255, dtype=np.uint8)
    image[20:45, 122:219] = load_image(str(FIXTURES / 'page_header.png'))
    cx, cy = round((post.button[0] + post.button[2]) / 2), round((post.button[1] + post.button[3]) / 2)
    image[cy-45:cy+45, cx-45:cx+45] = load_image(str(FIXTURES / 'empty_current_farm.png'))
    title = post.image.copy()
    if weak:
        noise = np.random.default_rng(12012).normal(size=title.shape)
        title = np.clip(title.astype(np.float64) + noise * 30, 0, 255).astype(np.uint8)
    x1, y1, x2, y2 = post.area
    image[y1:y2, x1:x2] = title
    return image, post


def frame_task(frames):
    task = Island.__new__(Island)
    task.config = SimpleNamespace(SERVER='cn', BUTTON_OFFSET=30)
    task.interval_timer = {}
    task.device = SimpleNamespace(image=frames[0], stuck_record_add=Mock(), click=Mock(), screenshot=Mock())

    def loop(**kwargs):
        for frame in frames:
            task.device.image = frame
            yield frame

    task.loop = loop
    task.post_manage_up_swipe = Mock(side_effect=AssertionError('已可见岗位不应上滑'))
    task.post_manage_down_swipe = Mock(side_effect=AssertionError('已可见岗位不应下滑'))
    task.post_close = Mock(side_effect=AssertionError('本场景不应关闭旧详情'))
    return task


class PostNavigationRegressionTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.base.utils.TEMPLATE_MATCH_NON_NATIVE_720P', True))
        self.enterContext(patch('module.base.utils.TEMPLATE_MATCH_NON_NATIVE_720P_THRESHOLD', 0.75))

    def test_weak_correct_wood_title_can_navigate_but_cannot_prove_empty(self):
        image, post = title_frame(ISLAND_WOOD_PROCESSING_POST1, weak=True)
        x1, y1, x2, y2 = post.area
        score = float(cv2.matchTemplate(image[y1:y2, x1:x2], post.image, cv2.TM_CCOEFF_NORMED).max())
        self.assertGreater(score, 0.85)
        self.assertLess(score, 0.90)
        self.assertTrue(match_post_title(image, post, strict=False))
        self.assertFalse(is_post_title_confirmed(image, post))
        self.assertFalse(match_post_title(image, post))
        self.assertIsNone(post._button_offset)

    def test_weak_correct_wood_zero_swipe_locate_does_not_skip_empty_inspection(self):
        image, post = title_frame(ISLAND_WOOD_PROCESSING_POST1, weak=True)
        task = frame_task([image, image.copy()])
        self.assertTrue(task.post_locate(post))
        self.assertFalse(task.post_list_is_empty(post))
        task.post_manage_up_swipe.assert_not_called()
        task.post_manage_down_swipe.assert_not_called()
        task.device.click.assert_not_called()

    def test_weak_correct_wood_opens_detail_with_one_click_and_no_swipe(self):
        image, post = title_frame(ISLAND_WOOD_PROCESSING_POST1, weak=True)
        detail = np.full((720, 1280, 3), 255, dtype=np.uint8)
        check = cn_button(ISLAND_POST_VACANT_CHECK)
        x1, y1, x2, y2 = check.area
        detail[y1:y2, x1:x2] = load_image(check.file)[y1:y2, x1:x2]
        task = frame_task([image, detail])
        self.assertTrue(task.post_open(post))
        task.device.click.assert_called_once_with(post)
        task.post_manage_up_swipe.assert_not_called()
        task.post_manage_down_swipe.assert_not_called()

    def test_wrong_workshop_class_is_rejected_even_under_global_point75(self):
        for source, target, fixture in (
                (ISLAND_ELECTRONIC_PROCESSING_POST1, ISLAND_WOOD_PROCESSING_POST1, None),
                (ISLAND_WOOD_PROCESSING_POST1, ISLAND_ELECTRONIC_PROCESSING_POST1, 'current_stable_shop_list.png')):
            with self.subTest(source=source.name, target=target.name):
                if fixture is None:
                    image, _ = title_frame(source)
                else:
                    # 保留实拍标题周围的真实背景，不能将较高模板上下多出的行置白。
                    frame = load_image(str(FIXTURES / fixture))
                    visible = cn_button(source)
                    self.assertTrue(match_post_title(frame, visible))
                    dx, dy = np.subtract(visible.button[:2], visible._button[:2])
                    x1, y1, x2, y2 = np.asarray(visible.area) + (dx, dy, dx, dy)
                    image = np.full_like(frame, 255)
                    image[y1-3:y2+3, x1-3:x2+3] = frame[y1-3:y2+3, x1-3:x2+3]
                post = cn_button(target)
                post.ensure_template()
                x1, _, x2, _ = post.area
                score = float(cv2.matchTemplate(image[69:655, x1-3:x2+3], post.image,
                                                cv2.TM_CCOEFF_NORMED).max())
                self.assertGreater(score, 0.75)
                self.assertLess(score, 0.85)
                self.assertFalse(match_post_title(image, post, strict=False))
                self.assertFalse(match_post_title(image, post))
                self.assertIsNone(post._button_offset)

    def test_real_ipc_orchard_all_four_slots_locate_without_swiping(self):
        image = load_image(str(FIXTURES / 'current_ipc_orchard_list.png'))
        for source in (ISLAND_ORCHARD_POST1, ISLAND_ORCHARD_POST2, ISLAND_ORCHARD_POST3, ISLAND_ORCHARD_POST4):
            with self.subTest(post=source.name):
                post = cn_button(source)
                self.assertTrue(match_post_title(image, post))
                self.assertTrue(is_post_title_confirmed(image, post))
                task = frame_task([image, image.copy()])
                self.assertTrue(task.post_locate(post))
                task.device.click.assert_not_called()
                task.post_manage_up_swipe.assert_not_called()
                task.post_manage_down_swipe.assert_not_called()

    def test_independent_compat_peak_wins_constructed_wrong_old_peak(self):
        # 这是构造的错峰回归；本次现场平移实验未复现旧模板最佳点偏移。
        image = load_image(str(FIXTURES / 'current_ipc_orchard_list.png'))
        post = cn_button(ISLAND_ORCHARD_POST1)
        post.ensure_template()
        original_match = cv2.matchTemplate
        calls = []

        def displaced_old_peak(region, template, method):
            calls.append(template)
            if template is post.image:
                result = np.zeros((region.shape[0] - template.shape[0] + 1,
                                   region.shape[1] - template.shape[1] + 1), dtype=np.float32)
                result[20, 0] = 0.93
                return result
            return original_match(region, template, method)

        with patch('module.island.post_list.cv2.matchTemplate', side_effect=displaced_old_peak):
            self.assertTrue(match_post_title(image, post))
        self.assertEqual(len(calls), 2)
        self.assertIs(calls[1], TEMPLATE_POST_ORCHARD_TITLE_COMPAT.image)
        dx, dy = np.subtract(post.button[:2], post._button[:2])
        self.assertEqual((post.area[0] + dx, post.area[1] + dy), (736, 444))
        self.assertTrue(is_post_title_confirmed(image, post))


if __name__ == '__main__':
    unittest.main()
