"""任务切换时的 OCR 缓存释放回归；使用假模型，不读取配置或模型文件。"""

import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.base import resource
from module.ocr import al_ocr
from module.ocr.al_ocr import AlOcr, OcrSettings
from module.ocr.models import OcrModel


class FakeModel:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def stub_module(name, **attributes):
    module = ModuleType(name)
    module.__dict__.update(attributes)
    return module


class TestResourceOcrRelease(unittest.TestCase):
    # 属性名与底层模型名不同的 cnocr 也必须覆盖，避免只清掉一层缓存。
    MODEL_NAMES = ('azur_lane', 'azur_lane_jp', 'ppocr_v6', 'cnocr', 'jp', 'tw')

    def setUp(self):
        al_ocr.reset_ocr_model()
        self.enterContext(patch.object(al_ocr, 'logger'))
        self.enterContext(patch.object(AlOcr, '_get_settings', return_value=OcrSettings(
            backend='onnx', device='cpu', allow_vendor_execution_providers=False,
            model_version='standard',
        )))
        self.rec_factory = self.enterContext(patch.object(
            al_ocr, '_create_ocr', side_effect=lambda *_: FakeModel(),
        ))
        self.det_factory = self.enterContext(patch.object(
            al_ocr, '_create_det_ocr_for_onnx', side_effect=lambda *_: FakeModel(),
        ))
        self.ncnn_det_factory = self.enterContext(patch.object(
            al_ocr, '_create_det_ocr_for_ncnn', side_effect=lambda: FakeModel(),
        ))
        self.models = OcrModel()
        self.deploy = SimpleNamespace(UseOcrServer=False)
        self.assets = SimpleNamespace(ui_mask=object(), tile_center_image=object())
        self.enterContext(patch.dict(sys.modules, {
            'module.runtime.setting': stub_module('module.runtime.setting', State=SimpleNamespace(
                deploy_config=self.deploy,
            )),
            'module.ocr.ocr': stub_module('module.ocr.ocr', OCR_MODEL=self.models),
            'module.map_detection.utils_assets': stub_module(
                'module.map_detection.utils_assets', ASSETS=self.assets,
            ),
        }))
        self.enterContext(patch.object(resource.Resource, 'instances', {}))
        self.collect = self.enterContext(patch.object(resource.gc, 'collect'))
        self.addCleanup(al_ocr.reset_ocr_model)

    def load_models(self):
        for name in self.MODEL_NAMES:
            recognizer = getattr(self.models, name)
            recognizer.init()
            al_ocr._run_ocr_queued(recognizer._ensure_det_loaded)

    def test_idle_releases_all_model_properties_and_both_engine_caches(self):
        self.load_models()
        engines = list(al_ocr._model_cache.values()) + list(al_ocr._det_model_cache.values())

        resource.release_resources()

        self.assertEqual(vars(self.models), {})
        self.assertEqual(al_ocr._model_cache, {})
        self.assertEqual(al_ocr._det_model_cache, {})
        self.assertTrue(all(model.closed for model in engines))
        self.assertEqual(vars(self.assets), {})
        self.collect.assert_called_once_with(2)

    def test_next_normal_task_keeps_only_default_recognizer_and_its_detector(self):
        self.load_models()
        default = self.models.azur_lane
        detector = default._det_model
        released_engines = [recognizer.model for name, recognizer in vars(self.models).items()
                            if name != 'azur_lane']

        resource.release_resources(next_task='Campaign')

        self.assertEqual(set(vars(self.models)), {'azur_lane'})
        self.assertIs(self.models.azur_lane, default)
        self.assertEqual({key[0] for key in al_ocr._model_cache}, {'azur_lane'})
        self.assertEqual(list(al_ocr._det_model_cache.values()), [detector])
        self.assertFalse(default.model.closed)
        self.assertFalse(detector.closed)
        self.assertTrue(all(model.closed for model in released_engines))
        self.collect.assert_called_once_with(2)

    def test_ncnn_shared_detector_is_preserved_between_tasks_and_released_when_idle(self):
        with patch.object(AlOcr, '_get_settings', return_value=OcrSettings(
                backend='ncnn', device='cpu', allow_vendor_execution_providers=False,
                model_version='standard')):
            self.load_models()
        detector = self.models.azur_lane._det_model
        self.ncnn_det_factory.assert_called_once_with()

        resource.release_resources(next_task='Campaign')

        self.assertEqual(set(vars(self.models)), {'azur_lane'})
        self.assertEqual(list(al_ocr._det_model_cache.values()), [detector])
        self.assertFalse(detector.closed)

        resource.release_resources()

        self.assertEqual(al_ocr._model_cache, {})
        self.assertEqual(al_ocr._det_model_cache, {})
        self.assertTrue(detector.closed)

    def test_continuous_opsi_and_commission_tasks_preserve_loaded_models(self):
        self.load_models()
        before_properties = dict(vars(self.models))
        before_recognition = dict(al_ocr._model_cache)
        before_detection = dict(al_ocr._det_model_cache)

        for next_task in ('OpsiCl1Leveling', 'OpsiDaily', 'commission'):
            with self.subTest(next_task=next_task):
                resource.release_resources(next_task=next_task)
                self.assertEqual(vars(self.models), before_properties)
                self.assertEqual(al_ocr._model_cache, before_recognition)
                self.assertEqual(al_ocr._det_model_cache, before_detection)
                self.assertFalse(any(model.closed for model in before_recognition.values()))
        self.collect.assert_not_called()

    def test_released_v6_and_japanese_models_load_fresh_on_next_access(self):
        self.load_models()
        previous = {name: getattr(self.models, name) for name in ('ppocr_v6', 'azur_lane_jp')}

        resource.release_resources()
        self.rec_factory.reset_mock()
        self.det_factory.reset_mock()
        for name, old in previous.items():
            with self.subTest(name=name):
                fresh = getattr(self.models, name)
                self.assertIsNot(fresh, old)
                self.assertIsNone(fresh.model)
                self.assertIsNone(fresh._det_model)
                fresh.init()
                al_ocr._run_ocr_queued(fresh._ensure_det_loaded)
                self.assertIsNot(fresh.model, old.model)
                self.assertIsNot(fresh._det_model, old._det_model)
                self.assertFalse(fresh.model.closed)
                self.assertFalse(fresh._det_model.closed)
        self.assertEqual(self.rec_factory.call_count, 2)
        # ONNX 检测模型按语言分别缓存，两个入口都必须重新加载。
        self.assertEqual(self.det_factory.call_count, 2)

    def test_releasing_unloaded_models_does_not_load_models_or_collect(self):
        resource.release_resources()
        resource.release_resources(next_task='Campaign')

        self.assertEqual(vars(self.models), {})
        self.rec_factory.assert_not_called()
        self.det_factory.assert_not_called()
        self.collect.assert_not_called()

    def test_second_idle_release_is_safe_and_does_not_repeat_collection(self):
        self.load_models()
        resource.release_resources()
        self.collect.reset_mock()

        resource.release_resources()

        self.assertEqual(vars(self.models), {})
        self.assertEqual(al_ocr._model_cache, {})
        self.assertEqual(al_ocr._det_model_cache, {})
        self.collect.assert_not_called()

    def test_remote_ocr_only_disconnects_when_idle(self):
        self.deploy.UseOcrServer = True
        self.models.close = Mock()
        self.load_models()
        before_recognition = dict(al_ocr._model_cache)
        before_detection = dict(al_ocr._det_model_cache)
        resource.release_resources(next_task='Campaign')
        self.models.close.assert_not_called()

        resource.release_resources()

        self.models.close.assert_called_once_with()
        self.assertEqual(al_ocr._model_cache, before_recognition)
        self.assertEqual(al_ocr._det_model_cache, before_detection)
        self.collect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
