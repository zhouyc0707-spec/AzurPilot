"""评分工具失败现场保存回归，只使用临时目录和内存 RGB 图像。"""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from module.exception import GameStuckError, GameTooManyClickError, RequestHumanTakeover
from module.meowfficer.score_task import run_meowfficer_score


class ScoreFailureSceneTests(unittest.TestCase):
    """保存当前缓存，不产生任何设备操作，也不改写实际配置。"""

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        old_cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, old_cwd)
        self.config = object()
        self.task = SimpleNamespace(run=Mock())
        constructor = patch('module.meowfficer.score_task.MeowfficerScore', return_value=self.task)
        self.constructor = constructor.start()
        self.addCleanup(constructor.stop)
        logs = patch('module.meowfficer.score_task.logger')
        self.logger = logs.start()
        self.addCleanup(logs.stop)

    @staticmethod
    def _device(image=None):
        return SimpleNamespace(image=image, screenshot=Mock(), click=Mock(), swipe=Mock())

    def _scenes(self):
        return sorted((self.root / 'log/error/meowfficer_score').glob('*/screen.png'))

    def _assert_no_game_actions(self, device):
        device.screenshot.assert_not_called()
        device.click.assert_not_called()
        device.swipe.assert_not_called()

    def test_takeover_saves_real_rgb_cached_frame_and_specific_reason(self):
        image = np.zeros((8, 12, 3), dtype=np.uint8)
        image[:] = (240, 23, 7)
        device = self._device(image)
        reason = '未能确认目标猫卡片的位置与身份，避免重复读取或错配'
        self.task.run.side_effect = RequestHumanTakeover(reason)

        self.assertFalse(run_meowfficer_score(self.config, device))

        self.constructor.assert_called_once_with(self.config, device=device, task='MeowfficerScore')
        scenes = self._scenes()
        self.assertEqual(len(scenes), 1)
        with Image.open(scenes[0]) as saved:
            self.assertEqual(saved.mode, 'RGB')
            self.assertEqual(saved.getpixel((0, 0)), (240, 23, 7))
            np.testing.assert_array_equal(np.array(saved), image)
        self.assertEqual(scenes[0].with_name('reason.txt').read_text(encoding='utf-8'), reason + '\n')
        self.assertEqual(sorted(path.name for path in scenes[0].parent.iterdir()),
                         ['reason.txt', 'screen.png'])
        self.assertIn(reason, self.logger.critical.call_args.args[0])
        self._assert_no_game_actions(device)

    def test_failures_create_separate_scenes_instead_of_overwriting_previous(self):
        device = self._device(np.full((4, 5, 3), 50, dtype=np.uint8))
        self.task.run.side_effect = RequestHumanTakeover('第一页未知')
        self.assertFalse(run_meowfficer_score(self.config, device))
        first = self._scenes()[0]
        device.image[:] = 150
        self.task.run.side_effect = RequestHumanTakeover('第二页未知')
        self.assertFalse(run_meowfficer_score(self.config, device))

        self.assertEqual(len(self._scenes()), 2)
        with Image.open(first) as saved:
            self.assertEqual(saved.getpixel((0, 0)), (50, 50, 50))
        self.assertEqual(first.with_name('reason.txt').read_text(encoding='utf-8'), '第一页未知\n')
        self._assert_no_game_actions(device)

    def test_no_device_still_logs_specific_failure_without_creating_a_scene(self):
        reason = '本地截图目录没有可读取文件'
        self.task.run.side_effect = RequestHumanTakeover(reason)
        self.assertFalse(run_meowfficer_score(self.config))
        self.assertIn(reason, self.logger.critical.call_args.args[0])
        self.assertEqual(self._scenes(), [])
        self.assertFalse((self.root / 'log').exists())

    def test_missing_or_invalid_cached_images_do_not_trigger_new_screenshots(self):
        invalid = [None, '未知截图', np.empty((0, 0, 3), dtype=np.uint8),
                   np.zeros((4, 5), dtype=np.uint8), np.zeros((4, 5, 4), dtype=np.uint8),
                   np.zeros((4, 5, 3), dtype=np.float32)]
        for image in invalid:
            with self.subTest(image_type=type(image).__name__, shape=getattr(image, 'shape', None)):
                device = self._device(image)
                self.task.run.side_effect = RequestHumanTakeover('当前页面未知')
                self.assertFalse(run_meowfficer_score(self.config, device))
                self._assert_no_game_actions(device)
                self.assertEqual(self._scenes(), [])
        device = SimpleNamespace(screenshot=Mock())
        self.assertFalse(run_meowfficer_score(self.config, device))
        device.screenshot.assert_not_called()
        self.assertEqual(self._scenes(), [])

    def test_image_save_failure_warns_separately_and_keeps_primary_reason(self):
        device = self._device(np.full((4, 5, 3), 30, dtype=np.uint8))
        reason = '选中卡片与天赋页身份不符'
        self.task.run.side_effect = RequestHumanTakeover(reason)
        with patch('module.meowfficer.score_task.save_image', side_effect=OSError('磁盘只读')):
            self.assertFalse(run_meowfficer_score(self.config, device))
        self.assertIn(reason, self.logger.critical.call_args.args[0])
        warning = self.logger.warning.call_args.args[0]
        self.assertIn('现场保存失败', warning)
        self.assertIn('磁盘只读', warning)
        self.assertEqual(self._scenes(), [])
        self._assert_no_game_actions(device)

    def test_reason_file_failure_does_not_replace_task_failure(self):
        device = self._device(np.full((4, 5, 3), 30, dtype=np.uint8))
        self.task.run.side_effect = RequestHumanTakeover('无法确认返回猫窝')
        with patch('pathlib.Path.write_text', side_effect=OSError('原因文件无法写入')):
            self.assertFalse(run_meowfficer_score(self.config, device))
        self.assertIn('无法确认返回猫窝', self.logger.critical.call_args.args[0])
        self.assertIn('原因文件无法写入', self.logger.warning.call_args.args[0])
        self._assert_no_game_actions(device)

    def test_empty_exception_message_still_has_an_explicit_failure_label(self):
        self.task.run.side_effect = RequestHumanTakeover()
        self.assertFalse(run_meowfficer_score(self.config))
        self.assertIn('RequestHumanTakeover', self.logger.critical.call_args.args[0])

    def test_normal_success_returns_true_without_saving_or_logging_failure(self):
        device = self._device(np.full((4, 5, 3), 30, dtype=np.uint8))
        self.assertTrue(run_meowfficer_score(self.config, device))
        self.task.run.assert_called_once_with()
        self.assertEqual(self._scenes(), [])
        self.logger.critical.assert_not_called()
        self._assert_no_game_actions(device)

    def test_real_device_and_unexpected_errors_continue_to_propagate(self):
        for error in (GameStuckError('画面卡死'), GameTooManyClickError('真实连击'),
                      RuntimeError('未知程序错误')):
            with self.subTest(error_type=type(error).__name__):
                device = self._device(np.full((4, 5, 3), 30, dtype=np.uint8))
                self.task.run.side_effect = error
                with self.assertRaises(type(error)) as raised:
                    run_meowfficer_score(self.config, device)
                self.assertIs(raised.exception, error)
                self.assertEqual(self._scenes(), [])
                self._assert_no_game_actions(device)
        self.logger.critical.assert_not_called()


if __name__ == '__main__':
    unittest.main()
