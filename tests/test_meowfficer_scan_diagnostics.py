"""保护锁定诊断只保存裁剪证据，不连接设备或改变识别结果。"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from module.exception import GameStuckError, RequestHumanTakeover
from module.meowfficer.scan_capture import ScanCapture, TalentRow
from module.meowfficer.scan_diagnostics import TalentReadFrame, save_incomplete_capture
from module.meowfficer.score import Talent


def _capture(complete=False):
    return ScanCapture('匿名猫', [], 30, None, 'SSR', complete, True,
                       ['第 5 行天赋未能完整确认，不能排除漏读'], talents_complete=False)


def _frame(stage='top', value=123, offset=0):
    image = np.full((720, 1280, 3), value, dtype=np.uint8)
    # 面板外模拟账号私有信息，仅用匿名颜色，验证不会被保存。
    image[:152] = (255, 0, 0)
    image[588:] = (255, 0, 0)
    row = TalentRow(153, 240, Talent(name='不动如山', line='不动如山', level=1,
                                    kind='special', raw='不动如山'), complete=True)
    partial = TalentRow(561, 588)
    return TalentReadFrame(stage, image, [row, partial], ['天赋行被裁切或行框不完整'], offset)


class TalentReadDiagnosticTests(unittest.TestCase):
    """文件内容及失败语义覆盖，不把同一实现输出当作完整性结论。"""

    def test_complete_capture_does_not_create_directory(self):
        with TemporaryDirectory() as temp:
            directory = Path(temp) / 'unused'
            self.assertIsNone(save_incomplete_capture(_capture(True), [_frame()], directory=directory))
            self.assertFalse(directory.exists())

    def test_missing_frames_do_not_create_directory(self):
        with TemporaryDirectory() as temp:
            directory = Path(temp) / 'unused'
            self.assertIsNone(save_incomplete_capture(_capture(), [], directory=directory))
            self.assertFalse(directory.exists())

    def test_cropped_images_metadata_and_all_row_evidence(self):
        capture = _capture()
        frames = [_frame(), _frame('bottom', 147, 75), _frame('reread', 156, 75)]
        with TemporaryDirectory() as temp, patch('module.meowfficer.scan_diagnostics.logger'):
            target = save_incomplete_capture(capture, frames, directory=temp)
            self.assertIsNotNone(target)
            self.assertEqual({file.name for file in target.iterdir()},
                             {'top.png', 'bottom.png', 'top_rows.png', 'bottom_rows.png', 'evidence.json'})
            for name, value in (('top.png', 123), ('bottom.png', 156)):
                with Image.open(target / name) as image:
                    self.assertEqual(image.size, (500, 436))
                    self.assertEqual(image.info, {})
                    self.assertTrue((np.asarray(image) == value).all())
            with Image.open(target / 'top_rows.png') as marked:
                pixels = np.asarray(marked)
                self.assertEqual(tuple(pixels[1, 0]), (0, 160, 80))
                self.assertEqual(tuple(pixels[409, 0]), (240, 140, 0))
            payload = json.loads((target / 'evidence.json').read_text(encoding='utf-8'))
            self.assertEqual(payload['capture']['reasons'], capture.reasons)
            self.assertEqual(len(payload['frames']), 3)
            self.assertEqual(payload['imageFrames'], {'top': 0, 'bottom': 2})
            self.assertEqual(payload['frames'][2]['offset'], 75)
            row = payload['frames'][0]['rows'][0]
            self.assertEqual((row['top'], row['bottom'], row['height']), (153, 240, 87))
            self.assertEqual(row['talent']['name'], '不动如山')
            self.assertTrue(row['complete'])
            self.assertIsNone(payload['frames'][0]['rows'][1]['talent'])
            self.assertFalse(payload['frames'][0]['rows'][1]['complete'])
            self.assertEqual(payload['frames'][0]['issues'], frames[0].issues)
            self.assertFalse(capture.complete)
            self.assertFalse(capture.talents_complete)
            self.assertEqual(capture.talents, [])
            self.assertTrue((frames[0].image[153:588, 744:1244] == 123).all())

    def test_first_top_frame_is_used_instead_of_previous_stage(self):
        frames = [_frame('initial', 91), _frame('top', 123), _frame('top', 134)]
        with TemporaryDirectory() as temp, patch('module.meowfficer.scan_diagnostics.logger'):
            target = save_incomplete_capture(_capture(), frames, directory=temp)
            with Image.open(target / 'top.png') as image:
                self.assertTrue((np.asarray(image) == 123).all())
            payload = json.loads((target / 'evidence.json').read_text(encoding='utf-8'))
            self.assertEqual(payload['imageFrames'], {'top': 1, 'bottom': 2})

    def test_two_protected_cats_do_not_overwrite_each_other(self):
        with TemporaryDirectory() as temp, patch('module.meowfficer.scan_diagnostics.logger'):
            first = save_incomplete_capture(_capture(), [_frame()], directory=temp)
            second = save_incomplete_capture(_capture(), [_frame()], directory=temp)
            self.assertNotEqual(first, second)
            self.assertTrue((first / 'evidence.json').is_file())
            self.assertTrue((second / 'evidence.json').is_file())

    def test_unverified_shift_keeps_failed_frame_without_inventing_offset(self):
        frame = _frame('unverified', 199, None)
        frame.rows = [TalentRow(153, 240), TalentRow(255, 342)]
        frame.issues = ['天赋滚动前后重叠位移未能确认，不能排除漏行']
        with TemporaryDirectory() as temp, patch('module.meowfficer.scan_diagnostics.logger'):
            target = save_incomplete_capture(_capture(), [_frame(), frame], directory=temp)
            payload = json.loads((target / 'evidence.json').read_text(encoding='utf-8'))
            self.assertEqual(payload['frames'][-1]['stage'], 'unverified')
            self.assertIsNone(payload['frames'][-1]['offset'])
            self.assertEqual(payload['frames'][-1]['issues'], frame.issues)
            self.assertEqual(payload['imageFrames']['bottom'], 1)
            self.assertEqual(payload['frames'][-1]['rows'][0]['ocrReadings'], [])
            self.assertIsNone(payload['frames'][-1]['rows'][0]['talent'])
            with Image.open(target / 'bottom.png') as image:
                self.assertTrue((np.asarray(image) == 199).all())

    def test_unknown_low_confidence_and_failed_ocr_readings_remain_raw_evidence(self):
        frame = _frame()
        row = TalentRow(153, 240)
        row.readings = [
            {'variant': 'plain', 'results': [{'text': '不动如由', 'confidence': 0.99}]},
            {'variant': 'clahe', 'results': [{'text': '不动如山', 'confidence': 0.63}]},
            {'variant': 'failed', 'results': None},
            {'variant': 'invalid', 'results': [{'text': '不动如山', 'confidence': 'nan'}]},
        ]
        frame.rows = [row]
        with TemporaryDirectory() as temp, patch('module.meowfficer.scan_diagnostics.logger'):
            target = save_incomplete_capture(_capture(), [frame], directory=temp)
            payload = json.loads((target / 'evidence.json').read_text(encoding='utf-8'))
            evidence = payload['frames'][0]['rows'][0]
            self.assertEqual(evidence['ocrReadings'], row.readings)
            self.assertIsNone(evidence['talent'])
            self.assertFalse(evidence['complete'])
            self.assertIsNone(row.talent)
            self.assertFalse(row.complete)

    def test_malformed_frame_warns_without_affecting_capture_or_writing(self):
        capture = _capture()
        frame = _frame()
        frame.image = np.zeros((720, 1280, 4), dtype=np.uint8)
        with TemporaryDirectory() as temp, patch('module.meowfficer.scan_diagnostics.logger') as log:
            self.assertIsNone(save_incomplete_capture(capture, [frame], directory=temp))
            self.assertEqual(list(Path(temp).iterdir()), [])
            log.warning.assert_called_once()
            self.assertFalse(capture.complete)
            self.assertEqual(capture.reasons, ['第 5 行天赋未能完整确认，不能排除漏读'])

    def test_disk_write_failure_warns_and_does_not_publish_evidence(self):
        capture = _capture()
        with TemporaryDirectory() as temp, patch('module.meowfficer.scan_diagnostics.logger') as log, \
                patch('module.meowfficer.scan_diagnostics.atomic_write', side_effect=OSError('磁盘只读')):
            self.assertIsNone(save_incomplete_capture(capture, [_frame()], directory=temp))
            self.assertEqual(list(Path(temp).rglob('evidence.json')), [])
            log.warning.assert_called_once()
            self.assertFalse(capture.complete)

    def test_control_errors_are_never_swallowed_by_save_failure_handler(self):
        for error in (GameStuckError('游戏控制异常'), RequestHumanTakeover('人工接管')):
            with self.subTest(error=type(error).__name__), TemporaryDirectory() as temp, \
                    patch('module.meowfficer.scan_diagnostics.atomic_write', side_effect=error), \
                    patch('module.meowfficer.scan_diagnostics.logger') as log:
                with self.assertRaises(type(error)):
                    save_incomplete_capture(_capture(), [_frame()], directory=temp)
                log.warning.assert_not_called()


if __name__ == '__main__':
    unittest.main()
