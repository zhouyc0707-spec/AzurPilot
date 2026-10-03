"""用临时配置和虚拟面板验证周次数记忆、月末禁购及 OCR 保护。"""

import calendar
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import module.config.server as server
from module.config.config import AzurLaneConfig
from module.config.config_updater import ConfigUpdater
from module.os_handler.action_point import (
    ACTION_POINT_BOX,
    OIL_PURCHASE_EXHAUSTED_WEEK_PATH,
    ActionPointHandler,
)


class TestActionPointOilPurchase(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 3, 23)
        self.offset = timedelta()
        self.enterContext(patch('module.os_handler.action_point.current_time', side_effect=lambda: self.now))
        self.enterContext(patch('module.os_handler.action_point.server_time_offset', side_effect=lambda: self.offset))
        self.enterContext(patch.object(server, 'server', 'cn'))
        self.enterContext(patch('module.os_handler.action_point.logger'))
        self.config = self.make_config()
        self.runner = self.make_runner(self.config)

    def make_config(self, name='probe', limit=5):
        config = AzurLaneConfig.__new__(AzurLaneConfig)
        config.config_name = name
        config.bound = {}
        config.modified = {}
        config.overridden = {}
        config.auto_update = False
        config.data = {'OpsiGeneral': {'Storage': {'Storage': {}}}}
        config.OpsiGeneral_BuyActionPointLimit = limit
        config.OpsiGeneral_OilLimit = 1000
        config.OS_ACTION_POINT_PRESERVE = 0
        return config

    def make_runner(self, config):
        runner = ActionPointHandler.__new__(ActionPointHandler)
        runner.config = config
        runner.device = Mock()
        runner._action_point_box = [20000, 0, 0, 3]
        runner.action_point_set_button = Mock(return_value=True)
        runner.action_point_get_buy_remain = Mock(return_value=0)
        runner.action_point_use = Mock()
        return runner

    def read_counts(self, readings):
        self.runner.loop = lambda **kwargs: iter(range(len(readings)))
        with patch('module.os_handler.action_point.OCR_ACTION_POINT_BUY_REMAIN.ocr', side_effect=readings):
            return ActionPointHandler.action_point_get_buy_remain(self.runner)

    def record_exhausted(self, value=None):
        if value is None:
            value = self.runner._get_oil_purchase_week()
        self.config.data['OpsiGeneral']['Storage']['Storage']['OilPurchaseExhaustedWeek'] = value

    def test_confirmed_zero_is_shared_between_tasks_without_another_oil_click(self):
        self.assertFalse(self.runner.action_point_buy())
        self.assertEqual(self.config.modified[OIL_PURCHASE_EXHAUSTED_WEEK_PATH], 'cn:2026-09-28')
        other_task = self.make_runner(self.config)
        self.assertFalse(other_task.action_point_buy())
        other_task.action_point_set_button.assert_not_called()
        other_task.action_point_get_buy_remain.assert_not_called()
        other_task.action_point_use.assert_not_called()

    def test_exhausted_instances_do_not_block_other_instances(self):
        self.runner.action_point_buy()
        other_instance = self.make_runner(self.make_config('another'))
        other_instance.action_point_get_buy_remain.return_value = 5
        self.assertTrue(other_instance.action_point_buy())
        other_instance.action_point_get_buy_remain.assert_called_once()
        other_instance.action_point_use.assert_called_once()

    def test_week_reset_expires_record_at_monday_midnight(self):
        self.record_exhausted()
        self.now = datetime(2026, 10, 4, 23, 59, 59)
        self.assertFalse(self.runner.action_point_buy())
        self.runner.action_point_get_buy_remain.assert_not_called()
        self.now = datetime(2026, 10, 5)
        self.runner.action_point_get_buy_remain.return_value = 5
        self.assertTrue(self.runner.action_point_buy())
        self.runner.action_point_get_buy_remain.assert_called_once()

    def test_week_reset_uses_server_time_for_jp_and_en(self):
        cases = (
            ('jp', timedelta(hours=-1), datetime(2026, 10, 11, 22, 59, 59), datetime(2026, 10, 11, 23)),
            ('en', timedelta(hours=15), datetime(2026, 10, 12, 14, 59, 59), datetime(2026, 10, 12, 15)),
        )
        for region, offset, before, after in cases:
            with self.subTest(region=region), patch.object(server, 'server', region):
                self.offset = offset
                self.now = before
                self.record_exhausted()
                self.runner.action_point_get_buy_remain.reset_mock()
                self.assertFalse(self.runner.action_point_buy())
                self.runner.action_point_get_buy_remain.assert_not_called()
                self.now = after
                self.runner.action_point_get_buy_remain.return_value = 5
                self.assertTrue(self.runner.action_point_buy())

    def test_server_change_does_not_reuse_other_server_record(self):
        self.record_exhausted()
        with patch.object(server, 'server', 'jp'):
            self.runner.action_point_get_buy_remain.return_value = 5
            self.assertTrue(self.runner.action_point_buy())

    def test_new_month_in_same_week_keeps_exhausted_record(self):
        self.record_exhausted('cn:2026-10-26')
        self.now = datetime(2026, 11, 1)
        self.assertFalse(self.runner.action_point_buy())
        self.runner.action_point_get_buy_remain.assert_not_called()
        self.now = datetime(2026, 11, 2)
        self.runner.action_point_get_buy_remain.return_value = 5
        self.assertTrue(self.runner.action_point_buy())

    def test_reading_across_week_reset_does_not_poison_new_week(self):
        self.now = datetime(2026, 10, 11, 23, 59, 59)

        def old_frame():
            self.now = datetime(2026, 10, 12)
            return 0

        self.runner.action_point_get_buy_remain.side_effect = old_frame
        self.assertFalse(self.runner.action_point_buy())
        self.assertNotIn(OIL_PURCHASE_EXHAUSTED_WEEK_PATH, self.config.modified)
        self.runner.action_point_use.assert_not_called()
        self.runner.action_point_get_buy_remain.side_effect = None
        self.runner.action_point_get_buy_remain.return_value = 5
        self.assertTrue(self.runner.action_point_buy())

    def test_invalid_stored_record_does_not_block_purchases(self):
        for invalid in (None, {}, 0, 'cn:broken-date', 'cn:2099-01-01'):
            with self.subTest(record=invalid):
                self.config.data['OpsiGeneral']['Storage']['Storage']['OilPurchaseExhaustedWeek'] = invalid
                self.runner.action_point_get_buy_remain.return_value = 5
                self.assertTrue(self.runner.action_point_buy())

    def test_disabled_purchases_do_not_click_or_read_oil(self):
        self.config.OpsiGeneral_BuyActionPointLimit = 0
        self.assertFalse(self.runner.action_point_buy())
        self.runner.action_point_set_button.assert_not_called()
        self.runner.action_point_get_buy_remain.assert_not_called()

    def test_oil_selection_failure_does_not_read_or_cache(self):
        self.runner.action_point_set_button.return_value = False
        self.assertFalse(self.runner.action_point_buy())
        self.runner.action_point_get_buy_remain.assert_not_called()
        self.assertEqual(self.config.modified, {})

    def test_unknown_reading_does_not_cache_and_can_be_retried(self):
        self.runner.action_point_get_buy_remain.return_value = None
        self.assertFalse(self.runner.action_point_buy())
        self.assertEqual(self.config.modified, {})
        self.runner.action_point_get_buy_remain.return_value = 5
        self.assertTrue(self.runner.action_point_buy())

    def test_oil_reserve_is_respected_without_marking_exhausted(self):
        self.runner._action_point_box[0] = 1999
        self.runner.action_point_get_buy_remain.return_value = 5
        self.assertFalse(self.runner.action_point_buy(preserve=1000))
        self.runner.action_point_use.assert_not_called()
        self.assertEqual(self.config.modified, {})

    def test_configured_lower_limit_does_not_mark_all_five_used(self):
        self.config.OpsiGeneral_BuyActionPointLimit = 2
        self.runner.action_point_get_buy_remain.return_value = 3
        self.assertFalse(self.runner.action_point_buy())
        self.assertEqual(self.config.modified, {})
        self.config.OpsiGeneral_BuyActionPointLimit = 5
        self.assertTrue(self.runner.action_point_buy())

    def test_purchase_costs_are_unchanged(self):
        for remain, cost in ((5, 1000), (4, 1000), (3, 2000), (2, 2000), (1, 4000)):
            with self.subTest(remain=remain):
                self.runner.action_point_get_buy_remain.return_value = remain
                self.runner._action_point_box[0] = cost + 1000
                self.assertTrue(self.runner.action_point_buy(preserve=1000))
                self.runner._action_point_box[0] -= 1
                self.assertFalse(self.runner.action_point_buy(preserve=1000))

    def test_zero_requires_two_consecutive_valid_reads(self):
        self.assertEqual(self.read_counts([(0, 5, 5), (0, 5, 5)]), 0)
        self.assertIsNone(self.read_counts([(0, 5, 5)]))

    def test_invalid_read_breaks_zero_confirmation(self):
        self.assertIsNone(self.read_counts([(0, 5, 5), (0, 0, 0), (0, 5, 5)]))
        self.assertEqual(self.read_counts([(0, 0, 0), (0, 5, 5), (0, 5, 5)]), 0)

    def test_nonzero_read_after_zero_is_not_exhausted(self):
        self.assertEqual(self.read_counts([(0, 5, 5), (4, 1, 5)]), 4)

    def test_invalid_total_and_out_of_range_are_unknown(self):
        self.assertIsNone(self.read_counts([(0, 0, 0), (0, 3, 3), (6, -1, 5), (-1, 6, 5)]))

    def test_zero_counter_shorthand_remains_supported(self):
        from module.os_handler.action_point import OCR_ACTION_POINT_BUY_REMAIN
        self.assertEqual(OCR_ACTION_POINT_BUY_REMAIN.after_process('05'), '0/5')

    def test_month_end_boundaries_cover_every_month_and_weekday(self):
        for year in (2026, 2027, 2028):
            for month in range(1, 13):
                last = datetime(year, month, calendar.monthrange(year, month)[1])
                start = last - timedelta(days=last.weekday())
                with self.subTest(year=year, month=month, weekday=last.weekday()):
                    self.now = start - timedelta(microseconds=1)
                    self.assertFalse(self.runner._is_in_month_end_purchase_block_week())
                    self.now = start
                    self.assertTrue(self.runner._is_in_month_end_purchase_block_week())
                    self.now = last.replace(hour=23, minute=59, second=59)
                    self.assertTrue(self.runner._is_in_month_end_purchase_block_week())
                    self.now = last + timedelta(days=1)
                    self.assertFalse(self.runner._is_in_month_end_purchase_block_week())

    def test_last_week_is_blocked_before_oil_selection_and_ocr(self):
        # 下月 1 日为周一曾导致漏禁购；月末为周一时只禁购当月最后一天。
        for day in (datetime(2027, 2, 22), datetime(2027, 2, 28), datetime(2026, 11, 30)):
            with self.subTest(day=day):
                self.now = day
                self.assertFalse(self.runner.action_point_buy())
        self.runner.action_point_set_button.assert_not_called()
        self.runner.action_point_get_buy_remain.assert_not_called()
        self.runner.action_point_use.assert_not_called()
        self.assertEqual(self.config.modified, {})

    def test_month_end_boundary_uses_server_date(self):
        self.offset = timedelta(hours=-1)
        self.now = datetime(2026, 10, 25, 22, 59, 59)
        self.assertFalse(self.runner._is_in_month_end_purchase_block_week())
        self.now = datetime(2026, 10, 25, 23)
        self.assertTrue(self.runner._is_in_month_end_purchase_block_week())
        self.now = datetime(2026, 10, 31, 23)
        self.assertFalse(self.runner._is_in_month_end_purchase_block_week())

    def test_cached_exhaustion_and_month_end_still_use_ap_boxes(self):
        for cached in (True, False):
            with self.subTest(cached=cached):
                runner = self.make_runner(self.make_config())
                if cached:
                    runner.config.cross_set(OIL_PURCHASE_EXHAUSTED_WEEK_PATH, runner._get_oil_purchase_week())
                else:
                    self.now = datetime(2026, 10, 26)
                runner._action_point_current = 25
                runner._action_point_total = 325
                runner._is_in_action_point = Mock(return_value=True)
                runner.action_point_safe_get = Mock()
                runner.action_point_quit = Mock()
                selected = []
                runner.action_point_set_button.side_effect = lambda index: selected.append(index) or True

                def use_box():
                    index = selected[-1]
                    self.assertNotEqual(index, 0)
                    runner._action_point_current += ACTION_POINT_BOX[index]
                    runner._action_point_box[index] -= 1

                runner.action_point_use.side_effect = use_box
                self.assertTrue(runner.handle_action_point(None, None, cost=80))
                self.assertEqual(selected, [3])
                runner.action_point_get_buy_remain.assert_not_called()

    def test_record_survives_restart_and_config_migration_without_touching_other_storage(self):
        from tests.test_api import fixture

        with tempfile.TemporaryDirectory() as directory:
            root = fixture(directory)
            path = root / 'config/testpilot.json'
            shutil.copyfile(path, root / 'config/another.json')
            data = json.loads(path.read_text(encoding='utf-8'))
            data['OpsiGeneral']['OpsiGeneral']['BuyActionPointLimit'] = 5
            data['OpsiGeneral']['Storage']['Storage']['Unrelated'] = {'keep': 17}
            path.write_text(json.dumps(data), encoding='utf-8')
            local_path = lambda name, mod_name='alas': str(root / 'config' / f'{name}.json')
            with (
                patch('module.config.config.filepath_config', side_effect=local_path),
                patch('module.config.config_updater.filepath_config', side_effect=local_path),
                patch.object(AzurLaneConfig, 'config_override'),
            ):
                first = self.make_runner(AzurLaneConfig('testpilot', task='OpsiHazard1Leveling'))
                self.assertFalse(first.action_point_buy())
                saved = json.loads(path.read_text(encoding='utf-8'))
                storage = saved['OpsiGeneral']['Storage']['Storage']
                self.assertEqual(storage, {'Unrelated': {'keep': 17}, 'OilPurchaseExhaustedWeek': 'cn:2026-09-28'})
                migrated = ConfigUpdater().config_update(saved)
                self.assertEqual(migrated['OpsiGeneral']['Storage']['Storage'], storage)
                restarted = self.make_runner(AzurLaneConfig('testpilot', task='OpsiMeowfficerFarming'))
                self.assertFalse(restarted.action_point_buy())
                restarted.action_point_set_button.assert_not_called()
                restarted.action_point_get_buy_remain.assert_not_called()
                other = self.make_runner(AzurLaneConfig('another', task='OpsiHazard1Leveling'))
                self.assertFalse(other._is_oil_purchase_exhausted(other._get_oil_purchase_week()))


if __name__ == '__main__':
    unittest.main()
