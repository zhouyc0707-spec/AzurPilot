"""钻石读取失败不得覆盖余额；真实零值仍是有效读数。"""

import copy
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.log_res.log_res import LogRes
from module.ocr.ocr import Digit
from module.shop.shop_status import ShopStatus


class ShopGemObservationTests(unittest.TestCase):
    def setUp(self):
        self.previous = {'Value': 1234, 'Record': datetime(2026, 1, 1, 8), 'Color': '^FF3333'}
        self.config = SimpleNamespace(
            data={'Dashboard': {'Gem': copy.deepcopy(self.previous)}},
            modified={'Other.Setting.Value': True}, update=Mock(),
        )
        self.shop = object.__new__(ShopStatus)
        self.shop.config = self.config
        self.shop.device = SimpleNamespace(image=None)
        self.ocr = Digit.__new__(Digit)
        self.ocr.SHOW_REVISE_WARNING = False
        self.enterContext(patch('module.shop.shop_status.OCR_SHOP_GEMS', self.ocr))
        self.enterContext(patch.object(LogRes, 'groups', {'Gem': {}}))
        self.observe = self.enterContext(patch.object(LogRes, '_observe'))
        self.snapshot = self.enterContext(patch.object(LogRes, '_record_all_resource_snapshot'))

    def read(self, text):
        with patch.object(self.ocr, 'ocr', side_effect=lambda image: self.ocr.after_process(text)):
            return self.shop.status_get_gems()

    def test_empty_ocr_preserves_dashboard_timestamp_and_history(self):
        self.assertEqual(0, self.read(''))
        self.assertFalse(self.ocr.last_valid)
        self.assertEqual({'Other.Setting.Value': True}, self.config.modified)
        self.assertEqual(self.previous, self.config.data['Dashboard']['Gem'])
        self.observe.assert_not_called()
        self.snapshot.assert_not_called()
        self.config.update.assert_called_once()

    def test_recognized_zero_updates_dashboard_and_observation(self):
        self.assertEqual(0, self.read('0'))
        self.assertTrue(self.ocr.last_valid)
        self.assertEqual(0, self.config.modified['Dashboard.Gem.Value'])
        self.assertIsInstance(self.config.modified['Dashboard.Gem.Record'], datetime)
        self.observe.assert_called_once_with('Gem', 0)
        self.snapshot.assert_called_once_with({'Gem': 0})

    def test_empty_after_success_keeps_pending_valid_read(self):
        self.assertEqual(1251, self.read('1251'))
        valid_changes = dict(self.config.modified)
        self.assertEqual(0, self.read(''))
        self.assertEqual(valid_changes, self.config.modified)
        self.observe.assert_called_once_with('Gem', 1251)
        self.snapshot.assert_called_once_with({'Gem': 1251})
        self.assertEqual(2, self.config.update.call_count)

    def test_next_valid_read_recovers_after_failure(self):
        self.read('')
        self.assertEqual(1251, self.read('1251'))
        self.assertEqual(1251, self.config.modified['Dashboard.Gem.Value'])
        self.observe.assert_called_once_with('Gem', 1251)
        self.snapshot.assert_called_once_with({'Gem': 1251})


if __name__ == '__main__':
    unittest.main()
