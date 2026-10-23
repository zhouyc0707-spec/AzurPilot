"""用脱敏库存截图和固定本地 OCR 验证五位库存及每日补喂确认，不连接游戏。"""

import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from module.dorm.dorm import RewardDorm
from module.ocr.al_ocr import AlOcr, OcrSettings


FIXTURES = Path(__file__).parent / 'fixtures' / 'dorm_food'


def inventory_frame(name):
    # 只保留六个库存数字所在行，夹具不含账号或其他游戏页面信息。
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    with Image.open(FIXTURES / name) as strip:
        image[451:476, 395:1141] = np.asarray(strip.convert('RGB'))
    return image


class DormFoodOcrTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        settings = OcrSettings(backend='onnx', device='cpu',
                               allow_vendor_execution_providers=False, model_version='alocr_en_v2_6')
        cls.model = AlOcr(name='azur_lane', settings=settings)

    def setUp(self):
        self.enterContext(patch('module.config.server.server', 'cn'))
        self.enterContext(patch('module.ocr.ocr.OCR_MODEL', SimpleNamespace(azur_lane=self.model)))
        self.enterContext(patch('module.dorm.dorm.current_time', return_value=datetime(
            2026, 10, 24, tzinfo=timezone.utc)))
        self.elapsed = 100.0
        self.enterContext(patch('module.base.timer.time', side_effect=lambda: self.elapsed))
        self.dorm = RewardDorm.__new__(RewardDorm)
        self.dorm.config = SimpleNamespace(Dorm_DailyFeedState=None)
        self.dorm.device = Mock(image=inventory_frame('before_feed.png'))
        self.dorm.appear = Mock(return_value=True)
        self.dorm.handle_popup_cancel = Mock(return_value=False)
        self.dorm.handle_info_bar = Mock(return_value=False)
        self.dorm.popup_interval_clear = Mock()
        self.dorm.device.screenshot.side_effect = self.screenshot
        self.dorm.device.click.side_effect = lambda button: setattr(
            self.dorm.device, 'image', inventory_frame('after_feed.png'))
        # 食量不属于本组局部截图；库存使用真实裁剪、预处理和本地模型。
        self.enterContext(patch('module.dorm.dorm.OCR_FILL.ocr', return_value=(1000, 59000, 60000)))

    def screenshot(self):
        self.elapsed += 0.2

    def test_five_digit_stock_and_other_five_slots_remain_complete(self):
        for name, cola in [('before_feed.png', 38601), ('after_feed.png', 38600)]:
            with self.subTest(frame=name):
                self.dorm.device.image = inventory_frame(name)
                food, _ = self.dorm.dorm_food_get()
                self.assertEqual([cola, 577, 1210, 40, 71, 64], [item.amount for item in food])

    def test_daily_feed_confirms_one_consumption_from_real_inventory_ocr(self):
        self.assertTrue(self.dorm.dorm_feed_daily())
        self.dorm.device.click.assert_called_once_with(self.dorm._dorm_food.buttons[0])
        self.assertEqual({'day': '2026-10-24', 'done': True}, self.dorm.config.Dorm_DailyFeedState)

    def test_restart_confirms_pending_consumption_without_feeding_again(self):
        self.dorm.device.image = inventory_frame('after_feed.png')
        self.dorm.config.Dorm_DailyFeedState = {
            'day': '2026-10-24', 'feed': 1000, 'amount': 38601, 'done': False,
        }
        self.assertTrue(self.dorm.dorm_feed_daily())
        self.dorm.device.click.assert_not_called()
        self.assertEqual({'day': '2026-10-24', 'done': True}, self.dorm.config.Dorm_DailyFeedState)


if __name__ == '__main__':
    unittest.main()
