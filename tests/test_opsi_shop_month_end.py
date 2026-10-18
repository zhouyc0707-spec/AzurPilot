"""港口商店月末日期与任务入口回归，不读取账号配置或执行购买。"""

import calendar
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.campaign.os_run import OSCampaignRun
from module.config.config import TaskEnd
from module.os.tasks.shop import OpsiShop, defer_month_end_shop


def local_time(server_moment, offset):
    """将测试中的服务器时刻转换到测试机本地时间，不依赖测试机时区。"""
    return server_moment.replace(tzinfo=timezone(offset)).astimezone().replace(tzinfo=None)


@contextmanager
def server_clock(moment, hours=8):
    offset = timedelta(hours=hours)
    now = local_time(moment, offset)
    with (
        patch('module.os.tasks.shop.current_time', return_value=now),
        patch('module.config.utils.server_time_offset', return_value=now - moment),
        patch('module.config.utils.server_timezone', return_value=offset),
    ):
        yield offset


class TestOpsiShopMonthEnd(unittest.TestCase):
    def make_shop(self, enabled=True, limit=0):
        shop = object.__new__(OpsiShop)
        shop.config = SimpleNamespace(
            OpsiShop_MonthEndOnly=enabled,
            OpsiShop_DisableBeforeDate=limit,
            task_delay=Mock(),
            task_stop=Mock(side_effect=TaskEnd),
        )
        shop.perform_port_shop_purchase = Mock(return_value=True)
        shop._os_shop_delay = Mock(return_value=datetime(2030, 1, 1))
        return shop

    def test_all_month_lengths_delay_to_actual_last_day(self):
        # 覆盖大小月、普通二月、闰年及世纪闰年规则，不依赖固定的28/30/31。
        months = [(2026, month) for month in range(1, 13)] + [
            (2027, 2), (2028, 2), (2000, 2), (2100, 2),
        ]
        for year, month in months:
            with self.subTest(year=year, month=month):
                shop = self.make_shop()
                moment = datetime(year, month, 1, 12, 30)
                with server_clock(moment) as offset, self.assertRaises(TaskEnd):
                    shop.os_shop()
                last = datetime(year, month, calendar.monthrange(year, month)[1])
                shop.config.task_delay.assert_called_once_with(target=local_time(last, offset))
                shop.perform_port_shop_purchase.assert_not_called()
                shop._os_shop_delay.assert_not_called()

    def test_last_day_ignores_legacy_date_limit_and_preserves_purchase_flow(self):
        for moment in (datetime(2026, 10, 31), datetime(2027, 2, 28, 23, 59),
                       datetime(2028, 2, 29, 12), datetime(2026, 12, 31, 18)):
            with self.subTest(moment=moment):
                shop = self.make_shop(limit=31)
                with server_clock(moment), self.assertRaises(TaskEnd):
                    shop.os_shop()
                shop.perform_port_shop_purchase.assert_called_once_with()
                shop._os_shop_delay.assert_called_once_with(True)
                shop.config.task_delay.assert_called_once_with(target=datetime(2030, 1, 1))

    def test_penultimate_day_does_not_purchase_early(self):
        shop = self.make_shop()
        with server_clock(datetime(2026, 10, 30, 23, 59, 59)) as offset, self.assertRaises(TaskEnd):
            shop.os_shop()
        shop.config.task_delay.assert_called_once_with(target=local_time(datetime(2026, 10, 31), offset))
        shop.perform_port_shop_purchase.assert_not_called()

    def test_first_day_of_new_month_defers_to_new_month_end(self):
        for moment, expected in (
            (datetime(2026, 11, 1), datetime(2026, 11, 30)),
            (datetime(2027, 1, 1), datetime(2027, 1, 31)),
        ):
            with self.subTest(moment=moment):
                shop = self.make_shop()
                with server_clock(moment) as offset, self.assertRaises(TaskEnd):
                    shop.os_shop()
                shop.config.task_delay.assert_called_once_with(target=local_time(expected, offset))
                shop.perform_port_shop_purchase.assert_not_called()

    def test_server_date_instead_of_local_date_controls_purchase(self):
        # 服务器仍为倒数第二天时，本机可能已到最后一天，不能提前购买。
        for hours in (-7, 8, 9):
            with self.subTest(server_offset=hours):
                shop = self.make_shop()
                with server_clock(datetime(2026, 10, 30, 23, 30), hours) as offset, self.assertRaises(TaskEnd):
                    shop.os_shop()
                shop.config.task_delay.assert_called_once_with(target=local_time(datetime(2026, 10, 31), offset))
                shop.perform_port_shop_purchase.assert_not_called()

    def test_all_supported_server_offsets_allow_last_day(self):
        for hours in (-7, 8, 9):
            with self.subTest(server_offset=hours):
                shop = self.make_shop(limit=31)
                with server_clock(datetime(2026, 10, 31, 23, 30), hours), self.assertRaises(TaskEnd):
                    shop.os_shop()
                shop.perform_port_shop_purchase.assert_called_once_with()

    def test_disabled_option_preserves_original_numeric_date_rule(self):
        for today, limit, allowed in ((1, 0, True), (20, 20, False), (21, 20, True), (31, 31, False)):
            with self.subTest(today=today, limit=limit):
                shop = self.make_shop(enabled=False, limit=limit)
                with patch('module.os.tasks.shop.current_time', return_value=datetime(2026, 10, today)), \
                        patch('module.os.tasks.shop.get_os_next_reset_after') as reset, self.assertRaises(TaskEnd):
                    shop.os_shop()
                reset.assert_not_called()
                if allowed:
                    shop.perform_port_shop_purchase.assert_called_once_with()
                else:
                    shop.perform_port_shop_purchase.assert_not_called()
                    shop.config.task_delay.assert_called_once_with(server_update=True)

    def test_legacy_config_without_new_field_keeps_original_behavior(self):
        shop = self.make_shop(enabled=False, limit=0)
        del shop.config.OpsiShop_MonthEndOnly
        with patch('module.os.tasks.shop.current_time', return_value=datetime(2026, 10, 1)), \
                self.assertRaises(TaskEnd):
            shop.os_shop()
        shop.perform_port_shop_purchase.assert_called_once_with()

    def test_disabled_guard_does_not_read_clock_or_change_schedule(self):
        config = SimpleNamespace(OpsiShop_MonthEndOnly=False, task_delay=Mock(), task_stop=Mock())
        with patch('module.os.tasks.shop.current_time') as clock:
            defer_month_end_shop(config)
        clock.assert_not_called()
        config.task_delay.assert_not_called()
        config.task_stop.assert_not_called()

    def test_campaign_entry_defers_before_initializing_map(self):
        run = object.__new__(OSCampaignRun)
        run.config = self.make_shop().config
        run._run_opsi_task_with_ap_overflow_guard = Mock()
        with server_clock(datetime(2026, 10, 18, 12)), self.assertRaises(TaskEnd):
            run.opsi_shop()
        run._run_opsi_task_with_ap_overflow_guard.assert_not_called()
        run.config.task_delay.assert_called_once()

    def test_campaign_entry_keeps_existing_runner_on_purchase_day(self):
        run = object.__new__(OSCampaignRun)
        run.config = self.make_shop().config
        run._run_opsi_task_with_ap_overflow_guard = Mock()
        with server_clock(datetime(2026, 10, 31, 12)):
            run.opsi_shop()
        run._run_opsi_task_with_ap_overflow_guard.assert_called_once()
        campaign = SimpleNamespace(os_shop=Mock())
        run._run_opsi_task_with_ap_overflow_guard.call_args.args[0](campaign)
        campaign.os_shop.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
