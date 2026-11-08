"""岛屿入口异常截图的懒写入、原帧保留及数量限制。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from module.ui import island_entry_diagnostic as diagnostic


class IslandEntryDiagnosticTest(unittest.TestCase):
    def setUp(self):
        # 使用测试工作树内的日志目录，避免临时文件默认落到系统盘。
        folder = Path(__file__).resolve().parents[1] / 'log'
        folder.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='island-entry-test-', dir=folder)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'diagnostic'
        self.patch_root = patch.object(diagnostic, 'DIAGNOSTIC_ROOT', self.root)
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)
        self.image = np.full((720, 1280, 3), 17, dtype=np.uint8)

    def test_normal_entry_only_keeps_first_frame_in_memory(self):
        recorder = diagnostic.IslandEntryDiagnostic()
        self.assertTrue(recorder.start(self.image))
        self.assertFalse(recorder.start(np.zeros_like(self.image)))
        self.assertFalse(self.root.exists())
        self.assertFalse(diagnostic.IslandEntryDiagnostic().capture(self.image, '尚未点击'))
        self.assertFalse(self.root.exists())

    def test_first_anomaly_keeps_original_frame_and_current_frame(self):
        recorder = diagnostic.IslandEntryDiagnostic()
        with patch('module.base.timer.time', return_value=100):
            recorder.start(self.image)
        self.image[:] = 33
        with patch('module.base.timer.time', return_value=104.25), patch.object(diagnostic, 'logger') as log:
            self.assertTrue(recorder.capture(self.image, '返回主界面'))
        folders = list(self.root.iterdir())
        self.assertEqual(len(folders), 1)
        self.assertRegex(folders[0].name, r'^\d{8}-\d{6}-\d{6}-\d+$')
        files = sorted(folders[0].glob('*.png'))
        self.assertEqual([file.name for file in files], ['01-first-click.png', '02-observed.png'])
        with Image.open(files[0]) as first, Image.open(files[1]) as current:
            self.assertEqual(first.getpixel((800, 450)), (17, 17, 17))
            self.assertEqual(current.getpixel((800, 450)), (33, 33, 33))
            self.assertEqual(first.getpixel((0, 0)), (0, 0, 0))
            self.assertEqual(current.getpixel((0, 0)), (0, 0, 0))
        self.assertTrue(np.all(self.image == 33))
        self.assertIn('4.25 秒', log.info.call_args.args[0])
        self.assertIn('返回主界面', log.info.call_args.args[0])

    def test_mask_preserves_current_page_and_link_evidence_only(self):
        from module.ui.page import page_dormmenu, page_island, page_main, page_main_white

        masked = diagnostic._navigation_image(self.image)
        self.assertEqual(masked.shape, self.image.shape)
        for page in (page_main, page_main_white, page_dormmenu, page_island):
            x1, y1, x2, y2 = page.check_button.area
            self.assertTrue(np.all(masked[y1:y2, x1:x2] == 17), page.name)
        for page, target in ((page_main, page_dormmenu), (page_main_white, page_dormmenu),
                             (page_dormmenu, page_island)):
            for area in (page.links[target].area, page.links[target].button):
                x1, y1, x2, y2 = area
                self.assertTrue(np.all(masked[y1:y2, x1:x2] == 17))
        self.assertTrue(np.all(masked[:180, :600] == 0))
        self.assertTrue(np.all(masked[630:, :400] == 0))
        self.assertTrue(np.all(self.image == 17))

    def test_repeated_anomalies_are_limited_to_three_images(self):
        recorder = diagnostic.IslandEntryDiagnostic()
        recorder.start(self.image)
        self.assertTrue(recorder.capture(self.image, '再次点击岛屿入口'))
        self.assertTrue(recorder.capture(self.image, '返回主界面'))
        self.assertFalse(recorder.capture(self.image, '再次点击岛屿入口'))
        self.assertFalse(recorder.start(self.image))
        self.assertEqual(len(list(self.root.rglob('*.png'))), 3)

    def test_directory_error_is_logged_once_without_fallback_or_retry(self):
        recorder = diagnostic.IslandEntryDiagnostic()
        recorder.start(self.image)
        with patch.object(Path, 'mkdir', side_effect=OSError('磁盘不可写')) as mkdir, \
                patch.object(diagnostic, 'save_image') as save, patch.object(diagnostic, 'logger') as log:
            self.assertFalse(recorder.capture(self.image, '返回主界面'))
            self.assertFalse(recorder.capture(self.image, '再次点击岛屿入口'))
        mkdir.assert_called_once()
        save.assert_not_called()
        log.warning.assert_called_once()
        self.assertFalse(self.root.exists())

    def test_partial_write_error_disables_remaining_images(self):
        recorder = diagnostic.IslandEntryDiagnostic()
        recorder.start(self.image)
        with patch.object(diagnostic, 'save_image', side_effect=[None, OSError('磁盘已满')]) as save, \
                patch.object(diagnostic, 'logger') as log:
            self.assertFalse(recorder.capture(self.image, '返回主界面'))
            self.assertFalse(recorder.capture(self.image, '再次点击岛屿入口'))
        self.assertEqual(save.call_count, 2)
        log.warning.assert_called_once()
        self.assertIsNone(recorder._first_frame)

    def test_non_filesystem_errors_are_not_swallowed(self):
        recorder = diagnostic.IslandEntryDiagnostic()
        recorder.start(self.image)
        with patch.object(diagnostic, 'save_image', side_effect=ValueError('图像格式错误')):
            with self.assertRaisesRegex(ValueError, '图像格式错误'):
                recorder.capture(self.image, '返回主界面')


if __name__ == '__main__':
    unittest.main()
