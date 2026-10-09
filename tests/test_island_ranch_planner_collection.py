"""牧场阶段收取与继续生产的离线规划巡检回归。"""

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

import module.config.server as server
from module.base.utils import load_image
from module.exception import GameStuckError
from module.island.assets import (
    ISLAND_GET, ISLAND_POST_SAFE_AREA, ISLAND_POST_SELECT, ISLAND_POST_VACANT_CHECK,
    ISLAND_WORKING, ISLAND_WORKING_TIME, POST_GET,
)
from module.island.island_rancher import IslandRancher
from module.island.production_planner import CONFIG_PREFIX
from module.ocr.al_ocr import AlOcr, OcrSettings
from module.ocr.ocr import Duration
from tests.test_island_production_planner import MemoryConfig


FIXTURE = Path(__file__).parent / 'fixtures/island_ranch_planner/working_sheep_with_collection.png'
NOW = datetime(2026, 10, 9, 18, 21, 35)
REMAINING = timedelta(hours=1, minutes=47, seconds=7)


def frame_with(*buttons):
    """仅重组成页面状态所需资源，不包含角色或账号。"""
    image = np.full((720, 1280, 3), 255, dtype=np.uint8)
    for button in buttons:
        x1, y1, x2, y2 = button.area
        image[y1:y2, x1:x2] = load_image(button.file)[y1:y2, x1:x2]
    return image


class RanchPlannerCollectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        settings = OcrSettings(backend='onnx', device='cpu',
                               allow_vendor_execution_providers=False, model_version='alocr_cn_v3')
        cls.ocr_model = AlOcr(name='cn', settings=settings)

    def setUp(self):
        self.enterContext(patch.object(server, 'server', 'cn'))
        self.enterContext(patch('module.island.island_rancher.logger'))
        self.enterContext(patch('module.island.island_rancher.current_time', return_value=NOW))
        self.enterContext(patch('module.config.utils.current_time',
                                return_value=NOW.replace(tzinfo=timezone.utc)))
        self.enterContext(patch('module.ocr.ocr.OCR_MODEL', SimpleNamespace(azur_lane=self.ocr_model)))
        self.partial = load_image(str(FIXTURE))
        self.working = self.partial.copy()
        self.working[530:588, 570:765] = 255
        self.frames = {
            'partial': self.partial,
            'working': self.working,
            'reward': frame_with(ISLAND_GET),
            'idle': frame_with(ISLAND_POST_SELECT, ISLAND_POST_VACANT_CHECK),
            'unknown': frame_with(),
        }
        self.phase = 'working'
        self.after_collection = 'working'
        self.active_post = None
        self.history = []
        self.configs = [(f'ISLAND_RANCH_POST{i}', f'time_ranch{i}') for i in range(1, 5)]
        self.task = IslandRancher.__new__(IslandRancher)
        self.task.config = MemoryConfig(**{f'{CONFIG_PREFIX}.PlanFingerprint': 'valid',
                                          f'{CONFIG_PREFIX}.PlannerTargets': '{"2605":1000}'})
        for field in ('ChickenFilter', 'PigFilter', 'RancherFilter', 'WoolWorkerFilter'):
            setattr(self.task.config, f'IslandRancher_{field}', 'WorkerJuu')
        self.task.posts_ranch = {pid: pid for pid, _ in self.configs}
        self.task.device = SimpleNamespace(image=self.working, screenshot=Mock(side_effect=self.screenshot),
                                           click=Mock(side_effect=self.click))
        for method in ('goto_postmanage', 'post_manage_mode', 'post_manage_swipe_to_top', 'post_close'):
            setattr(self.task, method, Mock())
        self.task.post_open = Mock(side_effect=self.open_post)
        self.task.post_get_and_close = Mock(return_value=True)
        self.task._planned_dispatch_recipe = Mock(return_value=4)
        self.task.loop = self.loop
        self.task.appear = lambda button, offset=0: button.match_template_color(
            self.task.device.image, offset=offset)
        self.task.appear_then_click = Mock(side_effect=self.appear_then_click)

    def open_post(self, post_id):
        self.active_post = post_id
        self.phase = 'partial' if post_id == 'ISLAND_RANCH_POST4' else 'working'
        return True

    def screenshot(self):
        self.task.device.image = self.frames[self.phase]
        self.history.append(('screenshot', self.active_post, self.phase))
        return self.task.device.image

    def click(self, button):
        self.history.append(('click', self.active_post, button.name))
        if button is POST_GET:
            self.phase = 'reward'
        elif button is ISLAND_POST_SAFE_AREA:
            self.phase = self.after_collection

    def appear_then_click(self, button, *, offset=0, interval=0):
        if self.task.appear(button, offset=offset):
            self.task.device.click(button)
            return True
        return False

    def loop(self, **kwargs):
        self.assertFalse(kwargs['skip_first'])
        for _ in range(5):
            yield self.screenshot()

    def test_real_frame_has_collectable_produce_and_a_positive_running_timer(self):
        self.assertTrue(ISLAND_WORKING.match_template_color(self.partial))
        self.assertTrue(POST_GET.match_template_color(self.partial, offset=(50, 0)))
        self.assertEqual(Duration(ISLAND_WORKING_TIME).ocr(self.partial), REMAINING)
        self.task.device.image = self.partial
        self.assertIsNone(self.task.ranch_ocr_finish_time('ISLAND_RANCH_POST4'))

    def test_partial_collection_refreshes_actual_time_and_keeps_existing_dispatch(self):
        self.task.run_planned_ranch(self.configs)
        self.assertEqual(self.task.time_ranch4, NOW + REMAINING)
        self.assertEqual(self.task.posts['ISLAND_RANCH_POST4']['state'], 'working')
        self.task._planned_dispatch_recipe.assert_not_called()
        self.assertEqual([entry for entry in self.history if entry[1] == 'ISLAND_RANCH_POST4'], [
            ('screenshot', 'ISLAND_RANCH_POST4', 'partial'),
            ('click', 'ISLAND_RANCH_POST4', 'POST_GET'),
            ('screenshot', 'ISLAND_RANCH_POST4', 'reward'),
            ('click', 'ISLAND_RANCH_POST4', 'ISLAND_POST_SAFE_AREA'),
            ('screenshot', 'ISLAND_RANCH_POST4', 'working'),
        ])
        self.task.appear_then_click.assert_any_call(POST_GET, offset=(50, 0), interval=2)
        self.assertEqual(self.task.post_get_and_close.call_count, 4)

    def test_complete_collection_requires_a_new_idle_frame_before_dispatch(self):
        self.after_collection = 'idle'
        self.task.run_planned_ranch(self.configs)
        self.assertEqual(self.task.posts['ISLAND_RANCH_POST4']['state'], 'idle')
        self.assertFalse(hasattr(self.task, 'time_ranch4'))
        self.task._planned_dispatch_recipe.assert_called_once_with(
            'ISLAND_RANCH_POST4', 'wool', 1000, 'time_ranch4', 'WorkerJuu', extra_stocks={})
        self.assertEqual(self.history[-1], ('screenshot', 'ISLAND_RANCH_POST4', 'idle'))

    def test_unknown_after_collection_keeps_the_existing_recovery_error(self):
        self.after_collection = 'unknown'
        with self.assertRaisesRegex(GameStuckError, 'ISLAND_RANCH_POST4 计划巡检状态无法确认'):
            self.task.run_planned_ranch(self.configs)
        self.assertEqual(self.task.posts['ISLAND_RANCH_POST4']['state'], 'unknown')
        self.assertFalse(hasattr(self.task, 'time_ranch4'))
        self.task._planned_dispatch_recipe.assert_not_called()


if __name__ == '__main__':
    unittest.main()
