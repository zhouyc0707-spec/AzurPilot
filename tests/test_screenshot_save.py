"""截图保存回归：隔离设备与配置，只把合成 RGB 图片写入临时目录。"""

import importlib.util
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

import module.base.utils as image_utils


def load_screenshot():
    """载入真实保存方法与图像写入工具，替换设备、日志和配置相关依赖。"""
    replacements = {}

    def module(name, **attrs):
        stub = ModuleType(name)
        stub.__dict__.update(attrs)
        replacements[name] = stub

    module('module.runtime.preview', publish=Mock())
    module('module.base.decorator', cached_property=property)
    module('module.base.timer', Timer=Mock())
    module('module.config.time_source', now=Mock())
    module('module.logger', logger=Mock())
    for name, cls in (
            ('azurpilot_android', 'AzurPilotAndroid'), ('adb', 'Adb'), ('ascreencap', 'AScreenCap'),
            ('droidcast', 'DroidCast'), ('ldopengl', 'LDOpenGL'), ('nemu_ipc', 'NemuIpc'),
            ('scrcpy', 'Scrcpy'), ('wsa', 'WSA')):
        module(f'module.device.method.{name}', **{cls: type(cls, (), {})})
    module('module.exception', RequestHumanTakeover=type('RequestHumanTakeover', (Exception,), {}),
           ScriptError=type('ScriptError', (Exception,), {}))
    path = Path(__file__).resolve().parents[1] / 'module/device/screenshot.py'
    spec = importlib.util.spec_from_file_location('_screenshot_save_test', path)
    loaded = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, replacements):
        spec.loader.exec_module(loaded)
    if loaded.save_image is not image_utils.save_image:
        raise AssertionError('截图保存必须使用真实图像写入工具')
    return loaded


class ScreenshotSaveTests(unittest.TestCase):
    def setUp(self):
        self.module = load_screenshot()
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / '用户截图目录' / '尚不存在'
        self.device = object.__new__(self.module.Screenshot)
        # 只提供现代目录字段：不加载实际 AzurLaneConfig，也不保留废弃属性。
        self.device.config = SimpleNamespace(DropRecord_SaveFolder=self.root)
        self.device._last_save_time = {}
        self.device.image = np.array([[(255, 0, 0), (0, 255, 0)],
                                      [(0, 0, 255), (17, 43, 91)]], dtype=np.uint8)
        self.device.screenshot = Mock(side_effect=AssertionError('保存现场不得另截一帧'))

    def save_at(self, now, **kwargs):
        with patch.object(self.module.time, 'time', return_value=now):
            return self.device.save_screenshot(**kwargs)

    def images(self, genre='items'):
        return sorted((self.root / genre).glob('*.png'))

    def assert_image(self, path, expected):
        with Image.open(path) as image:
            self.assertEqual(image.mode, 'RGB')
            np.testing.assert_array_equal(np.asarray(image), expected)

    def test_modern_custom_directory_saves_same_rgb_frame_and_creates_parents(self):
        expected = self.device.image.copy()
        self.assertFalse(self.root.exists())
        self.assertTrue(self.save_at(100, genre='island_order_unknown', interval=0))
        self.assertEqual(len(self.images('island_order_unknown')), 1)
        self.assert_image(self.images('island_order_unknown')[0], expected)
        np.testing.assert_array_equal(self.device.image, expected)
        self.device.screenshot.assert_not_called()

    def test_zero_interval_keeps_every_frame_even_with_identical_timestamp(self):
        expected = []
        for color in ((255, 0, 0), (0, 255, 0), (0, 0, 255)):
            self.device.image[:] = color
            expected.append(self.device.image.copy())
            self.assertTrue(self.save_at(100, interval=0))
        images = self.images()
        self.assertEqual(len(images), 3)
        for path, frame in zip(images, expected):
            self.assert_image(path, frame)
        self.device.screenshot.assert_not_called()

    def test_default_five_second_interval_preserves_skip_timestamp_semantics(self):
        self.assertTrue(self.save_at(100))
        self.assertFalse(self.save_at(104))
        self.assertEqual(self.device._last_save_time['items'], 104)
        # 旧行为由最近一次尝试计时，并要求严格大于间隔。
        self.assertFalse(self.save_at(109))
        self.assertEqual(self.device._last_save_time['items'], 109)
        self.assertTrue(self.save_at(114.1))
        self.assertEqual(len(self.images()), 2)

    def test_explicit_interval_controls_save_without_legacy_config(self):
        self.assertTrue(self.save_at(100, interval=10))
        self.assertFalse(self.save_at(105, interval=10))
        self.assertFalse(self.save_at(115, interval=10))
        self.assertTrue(self.save_at(125.1, interval=10))
        self.assertEqual(len(self.images()), 2)

    def test_compatibility_flag_uses_same_modern_directory(self):
        for flag in (False, True):
            with self.subTest(to_base_folder=flag):
                self.assertTrue(self.save_at(100, genre='diagnostic', interval=0, to_base_folder=flag))
        self.assertEqual(len(self.images('diagnostic')), 2)
        self.assertFalse((Path(self.temp.name) / 'screenshot').exists())

    def test_failed_write_propagates_without_timestamp_and_can_retry(self):
        for error in (PermissionError('禁止写入'), OSError('写入失败')):
            with self.subTest(error=type(error).__name__):
                self.device._last_save_time = {'items': 90}
                with patch.object(self.device, 'image_save', side_effect=error):
                    with self.assertRaises(type(error)):
                        self.save_at(100)
                self.assertEqual(self.device._last_save_time['items'], 90)
                self.assertTrue(self.save_at(100))
                self.assertEqual(self.device._last_save_time['items'], 100)
        self.assertEqual(len(self.images()), 2)

    def test_failed_directory_creation_propagates_and_retry_saves(self):
        blocker = Path(self.temp.name) / '不是目录'
        blocker.write_text('阻止父目录创建', encoding='utf-8')
        self.root = blocker / 'nested'
        self.device.config.DropRecord_SaveFolder = self.root
        self.device._last_save_time = {'items': 90}
        with self.assertRaises(OSError):
            self.save_at(100)
        self.assertEqual(self.device._last_save_time['items'], 90)
        blocker.unlink()
        self.assertTrue(self.save_at(100))
        self.assert_image(self.images()[0], self.device.image)

    def test_genres_have_independent_save_intervals(self):
        self.assertTrue(self.save_at(100, genre='island_order_unknown'))
        self.assertTrue(self.save_at(100, genre='island_stock_probe_unknown'))
        self.assertEqual(len(self.images('island_order_unknown')), 1)
        self.assertEqual(len(self.images('island_stock_probe_unknown')), 1)


if __name__ == '__main__':
    unittest.main()
