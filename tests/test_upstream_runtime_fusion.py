"""上游运行参数与本地调度、岛屿保护、委托截图清理的隔离融合回归。"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, PropertyMock, call, patch

from alas import AzurLaneAutoScript, DAILY_SUMMARY_CHECK_INTERVAL, WATCHDOG_CHECK_INTERVAL
from module.commission.commission import RewardCommission
from module.island.island import (
    ISLAND_CHECK, ISLAND_MAP_FARM, ISLAND_MAP_FARM_CHECK, Island,
)
from module.os_shop.shop import OSShop, PORT_SUPPLY_CHECK


class TestGuardIntervals(unittest.TestCase):
    def test_missing_config_does_not_trigger_lazy_loading(self):
        script = AzurLaneAutoScript.__new__(AzurLaneAutoScript)
        with patch.object(AzurLaneAutoScript, 'config', new_callable=PropertyMock,
                          side_effect=AssertionError('守护线程不得懒加载配置')) as config:
            self.assertEqual(script._watchdog_interval(), WATCHDOG_CHECK_INTERVAL)
            self.assertEqual(script._daily_summary_interval(), DAILY_SUMMARY_CHECK_INTERVAL)
        config.assert_not_called()

    def test_loaded_config_controls_both_guard_intervals(self):
        script = AzurLaneAutoScript.__new__(AzurLaneAutoScript)
        script.config = SimpleNamespace(Watchdog_CheckInterval=12,
                                        Watchdog_DailySummaryCheckInterval=4)
        self.assertEqual(script._watchdog_interval(), 12)
        self.assertEqual(script._daily_summary_interval(), 4)


class TestIslandMapFusion(unittest.TestCase):
    def test_question_mark_fallback_and_extended_wait_share_stuck_guard(self):
        device = MagicMock()
        runner = SimpleNamespace(
            config=SimpleNamespace(UiWait_IslandMapDestinationWait=600,
                                   UiWait_IslandMapConfirmWait=6,
                                   UiWait_IslandMapConfirmRetryWait=12),
            device=device,
            goto_island_map=Mock(return_value=True),
            loop=Mock(side_effect=[iter(range(2)), iter(range(1))]),
            appear_then_click=Mock(return_value=False),
            appear=Mock(side_effect=lambda button, **kwargs:
                        button in (ISLAND_MAP_FARM_CHECK, ISLAND_CHECK)),
            ui_additional=Mock(return_value=False),
            ui_page_appear=Mock(return_value=False),
        )
        with patch('module.island.island.Timer') as timer_class:
            timer = timer_class.return_value
            timer.start.return_value = timer
            timer.reached.return_value = True
            self.assertTrue(Island.island_map_goto(runner, 'farm'))
        device.stuck_timeout_override.assert_called_once_with(image_stuck=630)
        self.assertEqual(runner.loop.call_args_list,
                         [call(timeout=20, skip_first=False), call(timeout=600, skip_first=False)])
        self.assertEqual(device.click.call_args_list[0], call(ISLAND_MAP_FARM))
        self.assertIn(call(6), timer_class.call_args_list)
        self.assertIn(call(12), timer_class.call_args_list)


class TestCommissionScreenshotLimitFusion(unittest.TestCase):
    def setUp(self):
        self.base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.backup = self.base / 'bak' / 'old.png'
        self.backup.parent.mkdir()
        self.backup.write_bytes(b'backup')
        for index in range(61):
            (self.base / f'{index:03d}.png').write_bytes(b'fixture')
        self.enterContext(patch('module.commission.commission.logger'))

    def runner(self, keep):
        runner = RewardCommission.__new__(RewardCommission)
        runner.config = SimpleNamespace(DropRecord_CommissionIncomeScreenshot='save',
                                        DropRecord_RetentionDays=0,
                                        UiWait_CommissionRewardScreenshotKeep=keep)
        runner._prune_commission_reward_screenshots = lambda instance, max_keep: (
            RewardCommission._prune_commission_reward_screenshots(
                runner, instance, max_keep=max_keep, base=str(self.base)))
        return runner

    def save_frame(self, image, path):
        (self.base / Path(path).name).write_bytes(b'new frame')

    def assert_kept(self, expected):
        self.assertEqual(len(list(self.base.glob('*.png'))), expected)
        self.assertEqual(self.backup.read_bytes(), b'backup')

    def test_missing_count_setting_uses_default_and_keeps_backups(self):
        runner = RewardCommission.__new__(RewardCommission)
        runner.config = SimpleNamespace()
        runner._prune_commission_reward_screenshots('probe', base=str(self.base))
        self.assert_kept(50)

    def test_bound_cleanup_reads_configured_limit_and_keeps_backups(self):
        runner = RewardCommission.__new__(RewardCommission)
        runner.config = SimpleNamespace(UiWait_CommissionRewardScreenshotKeep=8)
        runner._prune_commission_reward_screenshots('probe', base=str(self.base))
        self.assert_kept(8)

    def test_local_single_harvest_save_reads_configured_limit(self):
        with patch('os.makedirs'), patch('module.commission.commission.save_image',
                                         side_effect=self.save_frame):
            saved = self.runner(5)._save_commission_reward_screenshot(object(), 'probe')
        self.assertIsNotNone(saved)
        self.assert_kept(5)

    def test_compatible_batch_save_reads_configured_limit(self):
        with patch('os.makedirs'), patch('module.commission.commission.save_image',
                                         side_effect=self.save_frame):
            saved = self.runner(8)._save_commission_reward_screenshots([object(), object()], 'probe')
        self.assertEqual(len(saved), 2)
        self.assert_kept(8)


class TestOSShopFusion(unittest.TestCase):
    def test_already_handled_akashi_does_not_buy_again(self):
        shop = SimpleNamespace(_akashi_handled=True, appear=Mock(return_value=False),
                               ui_click=Mock(), ui_back=Mock(), os_shop_buy=Mock())
        OSShop.handle_akashi_supply_buy(shop, object())
        shop.ui_click.assert_not_called()
        shop.os_shop_buy.assert_not_called()

    def test_failed_purchase_is_bounded_and_does_not_record_resource_spending(self):
        shop = SimpleNamespace(
            config=SimpleNamespace(config_name='probe'), device=Mock(), interval_clear=Mock(),
            handle_map_get_items=Mock(return_value=False),
            appear_then_click=Mock(return_value=False), handle_popup_confirm=Mock(return_value=False),
            appear=Mock(side_effect=lambda button, **kwargs: button == PORT_SUPPLY_CHECK),
        )
        shop.device.screenshot.side_effect = [None, None, None, AssertionError('购买重试必须有界')]
        item = SimpleNamespace(name='ActionPoint20')
        with patch('module.statistics.resource_tracking.record_purchase') as record:
            self.assertFalse(OSShop.os_shop_buy_execute(shop, item))
        self.assertEqual(shop.device.click.call_count, 3)
        record.assert_not_called()


if __name__ == '__main__':
    unittest.main()
