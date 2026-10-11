"""钻石须在新截图中稳定，空读数和过渡帧不得覆盖余额。"""

import copy
import itertools
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.exception import GameStuckError
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
        self.shop.device = SimpleNamespace(image=None, screenshot=Mock())
        self.clock = 100.0
        self.enterContext(patch('module.base.timer.time', side_effect=lambda: self.clock))
        self.ocr = Digit.__new__(Digit)
        self.ocr.SHOW_REVISE_WARNING = False
        self.enterContext(patch('module.shop.shop_status.OCR_SHOP_GEMS', self.ocr))
        self.enterContext(patch.object(LogRes, 'groups', {'Gem': {}}))
        self.observe = self.enterContext(patch.object(LogRes, '_observe'))
        self.snapshot = self.enterContext(patch.object(LogRes, '_record_all_resource_snapshot'))

    def read(self, *texts, repeat=False):
        frames = itertools.cycle(texts) if repeat else itertools.chain(texts, itertools.repeat(texts[-1]))

        def screenshot():
            self.clock += 0.25
            self.shop.device.image = next(frames)

        self.shop.device.screenshot.side_effect = screenshot
        with patch.object(self.ocr, 'ocr', side_effect=self.ocr.after_process):
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

    def test_partial_digits_during_transition_are_not_recorded(self):
        # 两帧都读到 7 仍可能处于过渡，不能直接判为稳定。
        self.assertEqual(7878, self.read('7', '7', '7878'))
        self.observe.assert_called_once_with('Gem', 7878)
        self.snapshot.assert_called_once_with({'Gem': 7878})

    def test_empty_frame_interrupts_confirmation(self):
        self.assertEqual(0, self.read('7', '7', '', repeat=True))
        self.assertEqual({'Other.Setting.Value': True}, self.config.modified)
        self.observe.assert_not_called()
        self.snapshot.assert_not_called()

    def test_changing_valid_values_time_out_without_writing(self):
        self.assertEqual(0, self.read('7', '7878', repeat=True))
        self.assertEqual(self.previous, self.config.data['Dashboard']['Gem'])
        self.observe.assert_not_called()
        self.snapshot.assert_not_called()
        self.assertLessEqual(self.clock, 103.5)

    def test_stale_arrival_frame_is_not_used(self):
        self.shop.device.image = '7'
        self.assertEqual(7878, self.read('7878'))
        self.assertGreaterEqual(self.shop.device.screenshot.call_count, 3)
        self.observe.assert_called_once_with('Gem', 7878)

    def test_device_failure_propagates_without_writing(self):
        self.shop.device.screenshot.side_effect = GameStuckError('截图失败')
        with self.assertRaises(GameStuckError):
            self.shop.status_get_gems()
        self.observe.assert_not_called()
        self.snapshot.assert_not_called()


if __name__ == '__main__':
    unittest.main()
