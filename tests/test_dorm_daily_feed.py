"""使用真实截图循环验证关闭常规喂食后的每日最小食物补喂。"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.config.config import AzurLaneConfig
from module.dorm.dorm import FOOD_FEED_AMOUNT, Food, RewardDorm
from module.exception import GameStuckError


class DormDailyFeedTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 20, 4, tzinfo=timezone.utc)
        self.elapsed = 100.0
        self.frame = 0
        self.amounts = [4, 3, 2, 1, 1, 1]
        self.fill = 50000
        self.consume_delay = 1
        self.consume_delta = 1
        self.pending_consumption = []
        self.ocr_override = None
        self.advance_server_time = False
        self.enterContext(patch.dict(os.environ, {'AZURPILOT_NTP_DISABLE': '1'}))
        self.enterContext(patch('module.config.server.server', 'cn'))
        self.enterContext(patch('module.base.timer.time', side_effect=lambda: self.elapsed))
        self.enterContext(patch('module.dorm.dorm.current_time', side_effect=lambda tz: self.now.astimezone(tz)))
        self.config = SimpleNamespace(
            Dorm_Feed=False, Dorm_Collect=False, BuyFurniture_Enable=False,
            Dorm_DailyFeedState=None, Dorm_ReserveFood=True,
            Dorm_FeedFilter='20000', Scheduler_Enable=True,
            Scheduler_SuccessInterval=60, task_delay=Mock(),
        )
        self.dorm = self.make_dorm(self.config)

    def make_dorm(self, config):
        dorm = RewardDorm.__new__(RewardDorm)
        dorm.config = config
        dorm._dorm_food = SimpleNamespace(buttons=[object() for _ in FOOD_FEED_AMOUNT])
        dorm.device = Mock(image=object())
        dorm.device.screenshot.side_effect = self.screenshot
        dorm.device.click.side_effect = lambda button: self.click(dorm, button)
        dorm.appear = Mock(return_value=True)
        dorm.handle_popup_cancel = Mock(return_value=False)
        dorm.handle_info_bar = Mock(return_value=False)
        dorm.popup_interval_clear = Mock()
        dorm.dorm_food_get = Mock(side_effect=self.read_food)
        return dorm

    def screenshot(self):
        self.frame += 1
        self.elapsed += 0.5
        if self.advance_server_time:
            self.now += timedelta(seconds=0.5)
        for due, index in self.pending_consumption.copy():
            if self.frame >= due:
                self.amounts[index] -= self.consume_delta
                self.fill -= FOOD_FEED_AMOUNT[index] * self.consume_delta
                self.pending_consumption.remove((due, index))

    def click(self, dorm, button):
        index = dorm._dorm_food.buttons.index(button)
        self.pending_consumption.append((self.frame + self.consume_delay, index))

    def read_food(self):
        if self.ocr_override is not None:
            return self.ocr_override()
        return [Food(feed, amount) for feed, amount in zip(FOOD_FEED_AMOUNT, self.amounts)], self.fill

    def prepare_navigation(self):
        for name in ('ui_ensure', 'ui_goto', 'dorm_feed_enter', 'dorm_feed_quit', 'dorm_collect'):
            setattr(self.dorm, name, Mock())
        self.dorm.get_dorm_ship_amount = Mock(return_value=6)

    def test_uses_one_lowest_owned_food_ignoring_regular_filter_and_reserve(self):
        self.amounts = [1, 3, 2, 1, 1, 1]
        self.assertTrue(self.dorm.dorm_feed_daily())
        self.assertEqual([0, 3, 2, 1, 1, 1], self.amounts)
        self.dorm.device.click.assert_called_once_with(self.dorm._dorm_food.buttons[0])
        self.assertEqual({'day': '2026-10-20', 'done': True}, self.config.Dorm_DailyFeedState)

    def test_falls_back_to_next_owned_food_value(self):
        self.amounts = [0, 0, 2, 3, 0, 1]
        self.assertTrue(self.dorm.dorm_feed_daily())
        self.assertEqual([0, 0, 1, 3, 0, 1], self.amounts)
        self.dorm.device.click.assert_called_once_with(self.dorm._dorm_food.buttons[2])

    def test_empty_inventory_does_not_click_or_complete(self):
        self.amounts = [0] * 6
        self.assertFalse(self.dorm.dorm_feed_daily())
        self.dorm.device.click.assert_not_called()
        self.assertIsNone(self.config.Dorm_DailyFeedState)

    def test_insufficient_capacity_waits_without_consuming(self):
        self.amounts = [0, 2, 0, 0, 0, 0]
        self.fill = 1999
        self.assertFalse(self.dorm.dorm_feed_daily())
        self.dorm.device.click.assert_not_called()
        self.assertFalse(self.dorm._dorm_daily_feed_done())

    def test_exact_one_food_capacity_is_sufficient(self):
        self.fill = 1000
        self.assertTrue(self.dorm.dorm_feed_daily())
        self.assertEqual(3, self.amounts[0])

    def test_delayed_inventory_update_does_not_repeat_click(self):
        self.consume_delay = 20
        self.assertTrue(self.dorm.dorm_feed_daily())
        self.assertGreaterEqual(self.dorm.device.screenshot.call_count, 21)
        self.dorm.device.click.assert_called_once()
        self.assertEqual(3, self.amounts[0])

    def test_missing_consumption_times_out_and_retains_pending_baseline(self):
        self.consume_delay = 100
        with self.assertRaises(GameStuckError):
            self.dorm.dorm_feed_daily()
        self.dorm.device.click.assert_called_once()
        self.assertEqual(
            {'day': '2026-10-20', 'feed': 1000, 'amount': 4, 'done': False},
            self.config.Dorm_DailyFeedState,
        )
        self.assertFalse(self.dorm._dorm_daily_feed_done())

    def test_invalid_ocr_never_clicks_or_completes(self):
        self.fill = -1
        with self.assertRaises(GameStuckError):
            self.dorm.dorm_feed_daily()
        self.dorm.device.click.assert_not_called()
        self.assertIsNone(self.config.Dorm_DailyFeedState)

    def test_transient_ocr_drop_is_not_taken_as_verified_consumption(self):
        self.consume_delay = 8

        def transient_read():
            food = [Food(feed, amount) for feed, amount in zip(FOOD_FEED_AMOUNT, self.amounts)]
            if self.frame == 2:
                food[0].amount -= 1
            return food, self.fill

        self.dorm.dorm_food_get.side_effect = transient_read
        self.assertTrue(self.dorm.dorm_feed_daily())
        self.assertGreaterEqual(self.frame, 10)
        self.assertEqual(3, self.amounts[0])
        self.dorm.device.click.assert_called_once()

    def test_abnormal_consumption_or_stock_increase_never_marks_done(self):
        for delta in (2, -1):
            with self.subTest(delta=delta):
                self.config.Dorm_DailyFeedState = None
                self.amounts[0] = 4
                self.pending_consumption.clear()
                self.consume_delta = delta
                self.dorm.device.click.reset_mock()
                with self.assertRaises(GameStuckError):
                    self.dorm.dorm_feed_daily()
                self.dorm.device.click.assert_called_once()
                self.assertFalse(self.dorm._dorm_daily_feed_done())

    def test_resume_after_interruption_confirms_already_consumed_without_click(self):
        self.config.Dorm_DailyFeedState = {'day': '2026-10-20', 'feed': 1000, 'amount': 5, 'done': False}
        self.assertTrue(self.dorm.dorm_feed_daily())
        self.dorm.device.click.assert_not_called()
        self.assertTrue(self.dorm._dorm_daily_feed_done())

    def test_resume_before_consumption_retries_one_click(self):
        self.config.Dorm_DailyFeedState = {'day': '2026-10-20', 'feed': 1000, 'amount': 4, 'done': False}
        self.assertTrue(self.dorm.dorm_feed_daily())
        self.dorm.device.click.assert_called_once()
        self.assertEqual(3, self.amounts[0])

    def test_same_day_completed_state_survives_new_task_instance(self):
        self.assertTrue(self.dorm.dorm_feed_daily())
        restarted = self.make_dorm(self.config)
        self.assertTrue(restarted.dorm_feed_daily())
        restarted.device.click.assert_not_called()
        restarted.device.screenshot.assert_not_called()
        self.assertEqual(3, self.amounts[0])
        self.now += timedelta(days=1)
        self.assertTrue(restarted.dorm_feed_daily())
        restarted.device.click.assert_called_once()
        self.assertEqual(2, self.amounts[0])

    def test_server_midnight_resets_daily_completion_in_all_regions(self):
        for server, hour, day in [('cn', 16, '2026-10-20'), ('tw', 16, '2026-10-20'),
                                  ('jp', 15, '2026-10-20'), ('en', 7, '2026-10-19')]:
            with self.subTest(server=server), patch('module.config.server.server', server):
                self.now = datetime(2026, 10, 20, hour, tzinfo=timezone.utc) - timedelta(seconds=1)
                self.config.Dorm_DailyFeedState = {'day': day, 'done': True}
                self.assertTrue(self.dorm._dorm_daily_feed_done())
                self.now += timedelta(seconds=2)
                self.assertFalse(self.dorm._dorm_daily_feed_done())

    def test_click_after_midnight_is_recorded_for_actual_click_day(self):
        self.now = datetime(2026, 10, 20, 15, 59, 59, 750000, tzinfo=timezone.utc)
        self.advance_server_time = True
        self.assertTrue(self.dorm.dorm_feed_daily())
        self.assertEqual({'day': '2026-10-21', 'done': True}, self.config.Dorm_DailyFeedState)
        self.dorm.device.click.assert_called_once()
        restarted = self.make_dorm(self.config)
        self.assertTrue(restarted.dorm_feed_daily())
        restarted.device.click.assert_not_called()

    def test_disabled_subtasks_still_run_daily_feed_without_disabling_task(self):
        self.prepare_navigation()
        self.dorm.run()
        self.assertTrue(self.config.Scheduler_Enable)
        self.assertTrue(self.dorm._dorm_daily_feed_done())
        self.dorm.dorm_feed_enter.assert_called_once()
        self.dorm.dorm_feed_quit.assert_called_once()
        self.dorm.dorm_collect.assert_not_called()
        self.dorm.get_dorm_ship_amount.assert_not_called()
        self.config.task_delay.assert_called_once_with(minute=None, server_update='00:00')

    def test_all_subtasks_disabled_and_done_skip_dorm_navigation(self):
        self.prepare_navigation()
        self.config.Dorm_DailyFeedState = {'day': '2026-10-20', 'done': True}
        self.dorm.run()
        self.dorm.ui_ensure.assert_not_called()
        self.dorm.ui_goto.assert_not_called()
        self.dorm.device.click.assert_not_called()
        self.assertTrue(self.config.Scheduler_Enable)
        self.config.task_delay.assert_called_once_with(server_update='00:00')

    def test_empty_inventory_rechecks_in_half_hour(self):
        self.prepare_navigation()
        self.amounts = [0] * 6
        self.dorm.run()
        self.config.task_delay.assert_called_once_with(minute=30, server_update='00:00')
        self.assertTrue(self.config.Scheduler_Enable)

    def test_collect_continues_after_daily_feed_done_and_caps_schedule_at_reset(self):
        self.prepare_navigation()
        self.config.Dorm_Collect = True
        self.config.Dorm_DailyFeedState = {'day': '2026-10-20', 'done': True}
        self.dorm.run()
        self.dorm.dorm_feed_enter.assert_not_called()
        self.dorm.dorm_collect.assert_called_once()
        self.config.task_delay.assert_called_once_with(minute=278, server_update='00:00')

    def test_regular_feed_preserves_existing_feed_and_ship_based_schedule(self):
        self.prepare_navigation()
        self.config.Dorm_Feed = True
        self.dorm.dorm_feed = Mock(return_value=1)
        self.dorm.dorm_feed_daily = Mock()
        self.dorm.run()
        self.dorm.dorm_feed.assert_called_once()
        self.dorm.dorm_feed_daily.assert_not_called()
        self.config.task_delay.assert_called_once_with(minute=278)
        self.assertIsNone(self.config.Dorm_DailyFeedState)

    def test_completed_record_is_persisted_in_config_and_loaded_after_restart(self):
        with tempfile.TemporaryDirectory(dir='D:/Codex/tmp') as directory:
            path = Path(directory) / 'dorm_daily_test.json'
            template = Path(__file__).resolve().parents[1] / 'config/template.json'
            data = json.loads(template.read_text(encoding='utf-8'))
            data['Dorm']['Dorm'].update(Feed=False, DailyFeedState=None)
            path.write_text(json.dumps(data), encoding='utf-8')
            with patch('module.config.config.filepath_config', return_value=str(path)), patch(
                'module.config.config_updater.filepath_config', return_value=str(path)
            ):
                config = AzurLaneConfig('dorm_daily_test', task='Dorm')
                dorm = self.make_dorm(config)
                self.assertTrue(dorm.dorm_feed_daily())
                saved = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual({'day': '2026-10-20', 'done': True}, saved['Dorm']['Dorm']['DailyFeedState'])
                restarted = self.make_dorm(AzurLaneConfig('dorm_daily_test', task='Dorm'))
                self.assertTrue(restarted.dorm_feed_daily())
                restarted.device.click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
