"""用连续模拟截图验证明石凭证快读与会话隔离，不连接游戏或用户配置。"""

import sys
import unittest
from collections import deque
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from module.combat.assets import GET_ITEMS_1, GET_ITEMS_2, GET_SHIP
from module.exception import GameStuckError
from module.os_handler.os_status import OSStatus
from module.os_shop.assets import OS_SHOP_CHECK, PORT_SUPPLY_CHECK
from module.os_shop.shop import OSShop


@dataclass
class CoinFrame:
    value: int = 100000
    popup: object = None
    timeout: bool = False


class CoinReadFixture(unittest.TestCase):
    def setUp(self):
        self.reader = OSStatus.__new__(OSStatus)
        self.reader.config = SimpleNamespace(
            config_name='coin_read_fixture',
            task=SimpleNamespace(command='OpsiObscure'),
            save=Mock(),
        )
        self.reader.device = Mock()
        self.reader._last_yellow_coins = 54321
        self.reader.appear = Mock(
            side_effect=lambda button, **kwargs: self.reader.device.image.popup is button,
        )
        self.reader.appear_then_click = Mock(
            side_effect=lambda button, **kwargs: self.reader.device.image.popup is button,
        )
        self.timer = Mock()
        self.timer.start.return_value = self.timer
        self.timer.reached.side_effect = lambda: self.reader.device.image.timeout
        self.yellow_ocr = Mock()
        self.yellow_ocr.ocr.side_effect = lambda image: image.value
        self.purple_ocr = Mock(last_valid=True)
        self.purple_ocr.ocr.return_value = 300
        self.port_purple_ocr = Mock(last_valid=True)
        self.port_purple_ocr.ocr.return_value = 600
        self.recorder = Mock()
        self.db = Mock()
        for target, value in (
            ('module.os_handler.os_status.Timer', self.timer),
            ('module.os_handler.os_status.OCR_SHOP_YELLOW_COINS', self.yellow_ocr),
            ('module.os_handler.os_status.OCR_SHOP_PURPLE_COINS', self.purple_ocr),
            ('module.os_handler.os_status.OCR_OS_SHOP_PURPLE_COINS', self.port_purple_ocr),
            ('module.os_handler.os_status.LogRes', self.recorder),
            ('module.os_handler.os_status.logger', Mock()),
        ):
            replacement = patch(target, return_value=value) if target.endswith(('Timer', 'LogRes')) else patch(target, value)
            replacement.start()
            self.addCleanup(replacement.stop)
        database_patch = patch.dict(sys.modules, {
            'module.statistics.cl1_database': SimpleNamespace(db=self.db),
        })
        database_patch.start()
        self.addCleanup(database_patch.stop)

    def frames(self, *frames):
        """首帧为调用前已有截图，后续帧只由实际截图循环取得。"""
        self.reader.device.image = frames[0]
        pending = deque(frames[1:])

        def screenshot():
            if not pending:
                raise GameStuckError('测试截图耗尽，读取未结束')
            self.reader.device.image = pending.popleft()

        self.reader.device.screenshot.side_effect = screenshot

    def read(self, *frames, fast=False):
        self.frames(*frames)
        return self.reader.get_yellow_coins(skip_verification_delay=fast)


class TestYellowCoinRead(CoinReadFixture):
    def test_default_read_preserves_verification_delay(self):
        self.frames(CoinFrame(), CoinFrame())
        self.assertEqual(self.reader.get_yellow_coins(), 100000)
        self.reader.device.sleep.assert_called_once_with(0.2)
        self.reader.device.screenshot.assert_called_once()

    def test_fast_read_requires_two_distinct_frames_without_explicit_sleep(self):
        first, second = CoinFrame(), CoinFrame()
        self.assertEqual(self.read(first, second, fast=True), 100000)
        self.reader.device.sleep.assert_not_called()
        self.reader.device.screenshot.assert_called_once()
        self.assertEqual(self.yellow_ocr.ocr.call_args_list, [call(first), call(second)])
        self.assertIs(self.yellow_ocr.ocr.call_args_list[0].args[0], first)
        self.assertIs(self.yellow_ocr.ocr.call_args_list[1].args[0], second)
        self.recorder.record.assert_called_once_with('YellowCoin', 100000, observed=True)

    def test_changed_value_requires_another_actual_frame(self):
        self.assertEqual(self.read(CoinFrame(100000), CoinFrame(99000), CoinFrame(99000), fast=True), 99000)
        self.assertEqual(self.reader.device.screenshot.call_count, 2)
        self.assertEqual(self.yellow_ocr.ocr.call_count, 3)
        self.reader.device.sleep.assert_not_called()

    def test_default_changed_value_keeps_each_delay(self):
        self.assertEqual(self.read(CoinFrame(100000), CoinFrame(99000), CoinFrame(99000)), 99000)
        self.assertEqual(self.reader.device.sleep.call_args_list, [call(0.2), call(0.2)])

    def test_zero_between_equal_values_breaks_stability(self):
        self.assertEqual(self.read(CoinFrame(), CoinFrame(0), CoinFrame(), CoinFrame(), fast=True), 100000)
        self.assertEqual(self.yellow_ocr.ocr.call_count, 4)
        self.assertEqual(self.reader.device.screenshot.call_count, 3)

    def test_each_reward_popup_breaks_stability_and_resets_timeout(self):
        for button in (GET_ITEMS_1, GET_ITEMS_2, GET_SHIP):
            with self.subTest(button=button.name):
                self.yellow_ocr.reset_mock()
                self.timer.reset.reset_mock()
                self.reader.device.screenshot.reset_mock()
                self.assertEqual(self.read(
                    CoinFrame(), CoinFrame(popup=button), CoinFrame(), CoinFrame(), fast=True,
                ), 100000)
                self.assertEqual(self.yellow_ocr.ocr.call_count, 3)
                self.assertEqual(self.reader.device.screenshot.call_count, 3)
                self.timer.reset.assert_called_once()
        self.reader.appear_then_click.assert_any_call(GET_SHIP, offset=(20, 20), interval=1)

    def test_each_visible_reward_during_click_cooldown_skips_background_ocr(self):
        self.reader.appear_then_click.side_effect = None
        self.reader.appear_then_click.return_value = False
        for button in (GET_ITEMS_1, GET_ITEMS_2, GET_SHIP):
            with self.subTest(button=button.name):
                self.yellow_ocr.reset_mock()
                self.timer.reset.reset_mock()
                self.reader.device.screenshot.reset_mock()
                popup = CoinFrame(12345, popup=button)
                first, second = CoinFrame(95000), CoinFrame(95000)
                self.assertEqual(self.read(popup, first, second, fast=True), 95000)
                self.assertEqual(self.yellow_ocr.ocr.call_args_list, [call(first), call(second)])
                self.assertEqual(self.reader.device.screenshot.call_count, 2)
                self.timer.reset.assert_not_called()
                self.assertNotIn(popup, [args.args[0] for args in self.yellow_ocr.ocr.call_args_list])

    def test_popup_click_cooldown_breaks_nonzero_frame_stability(self):
        self.reader.appear_then_click.side_effect = None
        self.reader.appear_then_click.return_value = False
        for button in (GET_ITEMS_1, GET_ITEMS_2, GET_SHIP):
            with self.subTest(button=button.name):
                self.yellow_ocr.reset_mock()
                self.timer.reset.reset_mock()
                self.reader.device.screenshot.reset_mock()
                before = CoinFrame(95000)
                popup = CoinFrame(95000, popup=button)
                after, confirmed = CoinFrame(95000), CoinFrame(95000)
                self.assertEqual(self.read(before, popup, after, confirmed, fast=True), 95000)
                self.assertEqual(self.yellow_ocr.ocr.call_count, 3)
                self.assertIs(self.yellow_ocr.ocr.call_args_list[-1].args[0], confirmed)
                self.assertEqual(self.reader.device.screenshot.call_count, 3)
                self.timer.reset.assert_not_called()

    def test_persistent_popup_cooldown_times_out_without_observing_background(self):
        self.reader.appear_then_click.side_effect = None
        self.reader.appear_then_click.return_value = False
        for button in (GET_ITEMS_1, GET_ITEMS_2, GET_SHIP):
            with self.subTest(button=button.name):
                self.yellow_ocr.reset_mock()
                self.recorder.reset_mock()
                self.timer.reset.reset_mock()
                self.reader.device.screenshot.reset_mock()
                self.assertEqual(self.read(
                    CoinFrame(99999, popup=button),
                    CoinFrame(99999, popup=button, timeout=True),
                    fast=True,
                ), 54321)
                self.yellow_ocr.ocr.assert_not_called()
                self.timer.reset.assert_not_called()
                self.reader.device.screenshot.assert_called_once()
                self.recorder.record.assert_called_once_with('YellowCoin', 54321, observed=False)

    def test_timeout_uses_cache_without_recording_a_successful_observation(self):
        self.assertEqual(self.read(CoinFrame(0), CoinFrame(0, timeout=True), fast=True), 54321)
        self.recorder.record.assert_called_once_with('YellowCoin', 54321, observed=False)
        self.assertEqual(self.reader._last_yellow_coins, 54321)

    def test_single_positive_value_before_timeout_is_not_accepted(self):
        self.assertEqual(self.read(CoinFrame(99000), CoinFrame(99000, timeout=True), fast=True), 54321)
        self.recorder.record.assert_called_once_with('YellowCoin', 54321, observed=False)

    def test_timeout_with_no_cache_does_not_claim_zero_was_observed(self):
        self.reader._last_yellow_coins = 0
        self.assertEqual(self.read(CoinFrame(0, timeout=True), fast=True), 0)
        self.recorder.record.assert_called_once_with('YellowCoin', 0, observed=False)

    def test_success_updates_cache_using_actual_ocr_value(self):
        self.assertEqual(self.read(CoinFrame(123456), CoinFrame(123456), fast=True), 123456)
        self.assertEqual(self.reader._last_yellow_coins, 123456)

    def test_timeout_settings_remain_five_seconds_and_ten_checks(self):
        with patch('module.os_handler.os_status.Timer', return_value=self.timer) as create_timer:
            self.read(CoinFrame(), CoinFrame(), fast=True)
        create_timer.assert_called_once_with(5, count=10)
        self.timer.start.assert_called_once()

    def test_ocr_error_propagates_without_overwriting_cache(self):
        self.frames(CoinFrame())
        self.yellow_ocr.ocr.side_effect = GameStuckError('模拟 OCR 失败')
        with self.assertRaises(GameStuckError):
            self.reader.get_yellow_coins(skip_verification_delay=True)
        self.assertEqual(self.reader._last_yellow_coins, 54321)
        self.recorder.record.assert_not_called()

    def test_screenshot_error_propagates_before_stable_confirmation(self):
        self.frames(CoinFrame())
        with self.assertRaises(GameStuckError):
            self.reader.get_yellow_coins(skip_verification_delay=True)
        self.assertEqual(self.reader._last_yellow_coins, 54321)
        self.recorder.record.assert_not_called()


class TestShopCoinRead(CoinReadFixture):
    def test_empty_active_session_enables_fast_read(self):
        self.reader._akashi_shop_session = {}
        self.reader.get_yellow_coins = Mock(return_value=100000)
        self.reader.get_purple_coins = Mock(return_value=300)
        self.reader.os_shop_get_coins()
        self.reader.get_yellow_coins.assert_called_once_with(skip_verification_delay=True)

    def test_non_session_paths_keep_compatible_argument_free_call(self):
        for session in (None, False, [], '旧状态'):
            with self.subTest(session=session):
                self.reader._akashi_shop_session = session
                self.reader.get_yellow_coins = Mock(return_value=100000)
                self.reader.get_purple_coins = Mock(return_value=300)
                self.reader.os_shop_get_coins()
                self.reader.get_yellow_coins.assert_called_once_with()

    def test_missing_session_keeps_compatible_argument_free_call(self):
        self.reader.get_yellow_coins = Mock(return_value=100000)
        self.reader.get_purple_coins = Mock(return_value=300)
        self.reader.os_shop_get_coins()
        self.reader.get_yellow_coins.assert_called_once_with()

    def test_fast_read_preserves_both_observations_snapshot_and_config_save(self):
        self.reader._akashi_shop_session = {}
        self.frames(CoinFrame(), CoinFrame())
        self.reader.os_shop_get_coins()
        self.assertEqual(self.recorder.record.call_args_list, [
            call('YellowCoin', 100000, observed=True),
            call('PurpleCoin', 300, observed=True),
        ])
        self.db.add_coins_snapshot.assert_called_once_with('coin_read_fixture', 100000, 300, source='other')
        self.reader.config.save.assert_called_once_with()
        self.assertEqual((self.reader._shop_yellow_coins, self.reader._shop_purple_coins), (100000, 300))

    def test_snapshot_sources_preserve_cl1_and_meow_context(self):
        for command, source in (('OpsiHazard1Leveling', 'cl1'), ('OpsiMeowfficerFarming', 'meow')):
            with self.subTest(command=command):
                self.reader.config.task.command = command
                self.reader.get_yellow_coins = Mock(return_value=100000)
                self.reader.get_purple_coins = Mock(return_value=300)
                self.reader._paired_purple_read = (300, True)
                self.db.async_add_coins_snapshot.reset_mock()
                self.reader.os_shop_get_coins()
                self.db.async_add_coins_snapshot.assert_called_once_with('coin_read_fixture', 100000, 300, source=source)

    def test_port_purple_read_keeps_separate_ocr_and_observation(self):
        self.reader.appear.side_effect = lambda button: button is OS_SHOP_CHECK
        self.assertEqual(self.reader.get_purple_coins(), 600)
        self.port_purple_ocr.ocr.assert_called_once()
        self.purple_ocr.ocr.assert_not_called()
        self.recorder.record.assert_called_once_with('PurpleCoin', 600, observed=True)

    def test_invalid_purple_read_is_not_recorded_as_successful_observation(self):
        self.purple_ocr.last_valid = False
        self.assertEqual(self.reader.get_purple_coins(), 300)
        self.recorder.record.assert_called_once_with('PurpleCoin', 300, observed=False)

    def test_coin_read_error_propagates_without_snapshot_or_save(self):
        self.reader.get_yellow_coins = Mock(side_effect=GameStuckError('模拟读取失败'))
        self.reader.get_purple_coins = Mock()
        with self.assertRaises(GameStuckError):
            self.reader.os_shop_get_coins()
        self.reader.get_purple_coins.assert_not_called()
        self.db.add_coins_snapshot.assert_not_called()
        self.reader.config.save.assert_not_called()


class TestAkashiShopSession(unittest.TestCase):
    def runner(self):
        runner = OSShop.__new__(OSShop)
        runner._akashi_handled = False
        runner.is_in_map = Mock()
        runner.handle_story_skip = Mock()
        runner.appear = Mock(return_value=False)
        runner.ui_click = Mock()
        runner.ui_back = Mock()
        runner.os_shop_buy = Mock()
        runner.os_shop_get_item_to_buy_in_akashi = Mock()
        return runner

    def test_successful_visit_has_session_only_after_entry_and_until_exit(self):
        runner = self.runner()
        states = []
        runner.ui_click.side_effect = lambda *args, **kwargs: states.append(('enter', getattr(runner, '_akashi_shop_session', None)))
        runner.os_shop_buy.side_effect = lambda **kwargs: states.append(('buy', runner._akashi_shop_session))
        runner.ui_back.side_effect = lambda **kwargs: states.append(('exit', runner._akashi_shop_session))
        runner.handle_akashi_supply_buy(object())
        self.assertEqual(states, [('enter', None), ('buy', {}), ('exit', {})])
        self.assertIsNone(runner._akashi_shop_session)
        self.assertTrue(runner._akashi_handled)
        runner.os_shop_buy.assert_called_once_with(select_func=runner.os_shop_get_item_to_buy_in_akashi)

    def test_entry_failure_does_not_leave_session_or_mark_handled(self):
        runner = self.runner()
        runner.ui_click.side_effect = GameStuckError('模拟进店失败')
        with self.assertRaises(GameStuckError):
            runner.handle_akashi_supply_buy(object())
        self.assertIsNone(runner._akashi_shop_session)
        self.assertFalse(runner._akashi_handled)
        runner.os_shop_buy.assert_not_called()
        runner.ui_back.assert_not_called()

    def test_purchase_failure_discards_cached_session_and_propagates(self):
        runner = self.runner()
        session = []

        def buy(**kwargs):
            runner._akashi_shop_session['items'] = ['测试商品']
            session.append(runner._akashi_shop_session)
            raise GameStuckError('模拟购买失败')

        runner.os_shop_buy.side_effect = buy
        with self.assertRaises(GameStuckError):
            runner.handle_akashi_supply_buy(object())
        self.assertEqual(session, [{'items': ['测试商品']}])
        self.assertIsNone(runner._akashi_shop_session)
        self.assertFalse(runner._akashi_handled)
        runner.ui_back.assert_not_called()

    def test_exit_failure_discards_session_without_marking_visit_finished(self):
        runner = self.runner()
        runner.ui_back.side_effect = GameStuckError('模拟出店失败')
        with self.assertRaises(GameStuckError):
            runner.handle_akashi_supply_buy(object())
        self.assertIsNone(runner._akashi_shop_session)
        self.assertFalse(runner._akashi_handled)

    def test_already_handled_map_does_not_create_session(self):
        runner = self.runner()
        runner._akashi_handled = True
        runner.handle_akashi_supply_buy(object())
        self.assertNotIn('_akashi_shop_session', runner.__dict__)
        runner.ui_click.assert_not_called()
        runner.os_shop_buy.assert_not_called()
        runner.ui_back.assert_not_called()

    def test_already_handled_open_shop_closes_without_new_session(self):
        runner = self.runner()
        runner._akashi_handled = True
        runner.appear.side_effect = lambda button, **kwargs: button is PORT_SUPPLY_CHECK
        runner.handle_akashi_supply_buy(object())
        self.assertNotIn('_akashi_shop_session', runner.__dict__)
        runner.ui_back.assert_called_once_with(
            appear_button=PORT_SUPPLY_CHECK, check_button=runner.is_in_map,
            skip_first_screenshot=True,
        )
        runner.ui_click.assert_not_called()
        runner.os_shop_buy.assert_not_called()


if __name__ == '__main__':
    unittest.main()
