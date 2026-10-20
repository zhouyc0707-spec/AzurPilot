"""用隔离画面验证明石去重范围，不读取用户配置或操作游戏。"""

import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.base.timer import Timer
from module.exception import GameStuckError
from module.os.assets import MAP_EXIT, ZONE_ENTRANCE, ZONE_LOCKED
from module.os.globe_operation import GlobeOperation, OSExploreError
from module.os.map import OSMap
from module.os.map_operation import OSMapOperation
from module.os_shop.assets import PORT_SUPPLY_CHECK
from module.os_shop.selector import Selector
from module.os_shop.shop import OSShop
from tests.test_opsi_cl1_speed import VirtualClock, WalkStateStub


class AkashiScopeFixture(unittest.TestCase):
    def entry_runner(self, frames=('pinned', 'loading', 'map'), pinned='OBSCURE'):
        runner = SimpleNamespace(
            _akashi_handled=True,
            frame=None,
            device=Mock(),
            get_zone_pinned_name=Mock(return_value=pinned),
            handle_action_point=Mock(return_value=False),
            handle_map_event=Mock(return_value=False),
            handle_popup_confirm=Mock(return_value=False),
            appear_then_click=Mock(return_value=False),
        )
        runner.is_in_map = lambda: runner.frame == 'map'
        runner.is_zone_pinned = lambda: runner.frame in ('pinned', 'locked')
        runner.appear = Mock(side_effect=lambda button, **kwargs: button is ZONE_LOCKED and runner.frame == 'locked')
        self.set_frames(runner, frames)
        return runner

    @staticmethod
    def set_frames(runner, frames):
        def loop():
            for frame in frames:
                runner.frame = frame
                runner.device.image = frame
                yield frame
            raise GameStuckError('测试画面耗尽，导航尚未完成')

        runner.loop = loop

    @staticmethod
    def enter(runner):
        with patch('module.os.globe_operation.time.sleep'), patch(
            'module.os.globe_operation.Timer', return_value=Mock(reached=Mock(return_value=True))
        ):
            GlobeOperation.globe_enter(runner, SimpleNamespace(zone_id=72))

    def shop_runner(self, runner=None):
        if runner is None:
            runner = self.entry_runner(frames=('map',))
        runner.ui_click = Mock()
        runner.ui_back = Mock()
        runner.os_shop_buy = Mock()
        runner.os_shop_get_item_to_buy_in_akashi = Mock(name='原明石选品器')
        runner.handle_story_skip = Mock(return_value=False)
        runner.appear = Mock(side_effect=lambda button, **kwargs: button is PORT_SUPPLY_CHECK and runner.frame == 'shop')
        return runner

    @staticmethod
    def visit_shop(runner, grid=None):
        # 入口与退出仍由各自导航夹具验证；商店使用连续的地图、商店新画面。
        origin = runner.frame if runner.is_in_map() else 'map'
        runner.ui_click.side_effect = lambda *args, **kwargs: setattr(runner, 'frame', 'shop')
        runner.ui_back.side_effect = lambda **kwargs: setattr(runner, 'frame', origin)
        return OSShop.handle_akashi_supply_buy(runner, grid or object())

    def exit_runner(self, frames):
        runner = SimpleNamespace(
            _akashi_handled=True,
            frame=None,
            device=Mock(),
            interval_reset=Mock(),
            zone_init=Mock(),
            handle_popup_confirm=Mock(return_value=False),
            appear_then_click=Mock(return_value=False),
        )
        runner.is_in_map = lambda: runner.frame in ('special', 'source')
        runner.appear = Mock(side_effect=lambda button, **kwargs: button is MAP_EXIT and runner.frame == 'special')
        runner.handle_map_event = Mock(side_effect=lambda **kwargs: runner.frame == 'event')
        self.set_frames(runner, frames)
        return runner

    @staticmethod
    def exit(runner):
        with patch('module.os.map_operation.Timer', return_value=Mock(reached=Mock(return_value=True))):
            OSMapOperation.map_exit(runner)

    def search_runner(self, strategic=False):
        runner = OSMap.__new__(OSMap)
        runner._akashi_handled = True
        runner.config = SimpleNamespace(
            task=SimpleNamespace(command='OpsiMeowfficerFarming' if strategic else 'OpsiObscure'),
            DropRecord_OpsiMeowfficerFarming='save', DropRecord_OpsiObscure='save',
        )
        runner.device = Mock(image='当前海域')
        runner.stat = Mock()
        runner.drop = Mock(count=0)
        runner.stat.new.side_effect = lambda **kwargs: nullcontext(runner.drop)
        runner._auto_search_battle_count = 0
        runner.handle_ash_beacon_attack = Mock()
        runner.hp_reset = Mock()
        runner.hp_get = Mock()
        runner.map_rescan = Mock()
        runner.frame = 'map'
        runner.is_in_map = Mock(return_value=True)
        runner.appear = Mock(side_effect=lambda button, **kwargs:
                             button is PORT_SUPPLY_CHECK and runner.frame == 'shop')
        runner.ui_click = Mock()
        runner.ui_back = Mock()
        runner.os_shop_buy = Mock()
        runner.os_shop_get_item_to_buy_in_akashi = Mock(name='原明石选品器')
        runner.handle_story_skip = Mock(return_value=False)
        runner.fleet_selector = SimpleNamespace(bar_opened=lambda: False)

        def search(drop, **kwargs):
            self.visit_shop(runner)
            return 0

        runner.os_auto_search_run = Mock(side_effect=search)
        return runner


class TestAkashiEntryScope(AkashiScopeFixture):
    def test_confirmed_new_entry_clears_old_handled_flag(self):
        runner = self.entry_runner()
        previous_states = []
        runner.device.click.side_effect = lambda button: previous_states.append(runner._akashi_handled)
        self.enter(runner)
        self.assertEqual(previous_states, [True], '入口点击前应保留旧海域状态')
        self.assertFalse(runner._akashi_handled)
        runner.device.click.assert_called_once_with(ZONE_ENTRANCE)

    def test_already_in_map_without_entry_click_keeps_deduplication(self):
        runner = self.entry_runner(frames=('map',))
        self.enter(runner)
        self.assertTrue(runner._akashi_handled)
        runner.device.click.assert_not_called()

    def test_locked_entry_keeps_old_handled_flag(self):
        runner = self.entry_runner(frames=('locked',))
        with self.assertRaises(OSExploreError):
            self.enter(runner)
        self.assertTrue(runner._akashi_handled)
        runner.device.click.assert_not_called()

    def test_clicked_but_unconfirmed_entry_keeps_old_handled_flag(self):
        runner = self.entry_runner(frames=('pinned', 'loading'))
        with self.assertRaises(GameStuckError):
            self.enter(runner)
        runner.device.click.assert_called_once_with(ZONE_ENTRANCE)
        self.assertTrue(runner._akashi_handled)

    def test_new_map_types_all_allow_a_new_purchase(self):
        for zone_type in ('SAFE', 'DANGEROUS', 'OBSCURE', 'ABYSSAL', 'STRONGHOLD'):
            with self.subTest(zone_type=zone_type):
                runner = self.shop_runner(self.entry_runner(pinned=zone_type))
                self.enter(runner)
                self.visit_shop(runner)
                runner.os_shop_buy.assert_called_once_with(select_func=runner.os_shop_get_item_to_buy_in_akashi)
                self.assertTrue(runner._akashi_handled)

    def test_same_zone_id_new_coordinate_allows_each_new_shop(self):
        runner = self.shop_runner()
        for _ in range(2):
            self.set_frames(runner, ('pinned', 'loading', 'map'))
            self.enter(runner)
            self.visit_shop(runner)
            self.assertTrue(runner._akashi_handled)
        self.assertEqual(runner.os_shop_buy.call_count, 2)

    def test_repeated_shop_in_one_map_buys_once_and_closes_open_shop(self):
        runner = self.shop_runner()
        self.set_frames(runner, ('pinned', 'map'))
        self.enter(runner)
        grid = object()
        self.visit_shop(runner, grid)
        runner.frame = 'shop'
        self.visit_shop(runner, grid)
        runner.ui_click.assert_called_once_with(
            grid, appear_button=runner.is_in_map, check_button=PORT_SUPPLY_CHECK,
            additional=runner.handle_story_skip, skip_first_screenshot=True)
        runner.os_shop_buy.assert_called_once()
        self.assertEqual(runner.ui_back.call_count, 2, '已弹出的重复商店仍需关闭')

    def test_shop_failure_does_not_mark_unfinished_purchase_as_handled(self):
        runner = self.shop_runner()
        self.set_frames(runner, ('pinned', 'map'))
        self.enter(runner)
        runner.os_shop_buy.side_effect = GameStuckError('测试购买中断')
        with self.assertRaises(GameStuckError):
            self.visit_shop(runner)
        self.assertFalse(runner._akashi_handled)


class TestAkashiExitScope(AkashiScopeFixture):
    def test_confirmed_special_map_exit_clears_handled_flag_before_zone_init(self):
        runner = self.exit_runner(('special', 'event', 'source'))
        runner.zone_init.side_effect = lambda: self.assertFalse(runner._akashi_handled)
        self.exit(runner)
        self.assertFalse(runner._akashi_handled)
        runner.zone_init.assert_called_once()

    def test_exit_button_still_visible_does_not_end_even_after_event(self):
        runner = self.exit_runner(('event', 'special'))
        with self.assertRaises(GameStuckError):
            self.exit(runner)
        self.assertTrue(runner._akashi_handled)
        runner.zone_init.assert_not_called()

    def test_exit_without_observed_change_keeps_original_state(self):
        runner = self.exit_runner(('source', 'source'))
        with self.assertRaises(GameStuckError):
            self.exit(runner)
        self.assertTrue(runner._akashi_handled)
        runner.zone_init.assert_not_called()

    def test_event_then_transition_failure_keeps_original_state(self):
        runner = self.exit_runner(('event', 'loading'))
        with self.assertRaises(GameStuckError):
            self.exit(runner)
        self.assertTrue(runner._akashi_handled)
        runner.zone_init.assert_not_called()

    def test_special_shop_flag_does_not_suppress_source_map_shop(self):
        runner = self.shop_runner(self.exit_runner(('special', 'event', 'source')))
        self.exit(runner)
        self.visit_shop(runner)
        runner.os_shop_buy.assert_called_once()


class TestAkashiSearchAndFilterScope(AkashiScopeFixture):
    def test_normal_search_retry_and_rescan_keep_same_map_deduplication(self):
        runner = self.search_runner()
        for _ in range(2):
            OSMap.run_auto_search(runner, question=False, rescan='current', after_auto_search=False)
        self.assertTrue(runner._akashi_handled)
        runner.os_shop_buy.assert_not_called()
        self.assertEqual(runner.map_rescan.call_count, 2)

    def test_new_strategic_round_still_resets_and_can_buy_again(self):
        runner = self.search_runner(strategic=True)
        for _ in range(2):
            self.assertTrue(OSMap.run_strategic_search(runner))
        self.assertEqual(runner.os_shop_buy.call_count, 2)
        self.assertTrue(runner._akashi_handled)

    def test_general_and_cl1_item_filters_and_coins_remain_effective(self):
        runner = SimpleNamespace(
            config=SimpleNamespace(OpsiGeneral_AkashiShopFilter='ActionPoint', OpsiHazard1Leveling_Cl1Filter='RepairPack'),
            _shop_yellow_coins=2000,
            _shop_purple_coins=0,
        )
        runner.pretreatment = lambda items: Selector.pretreatment(runner, items)
        runner.enough_coins_in_akashi = lambda item: Selector.enough_coins_in_akashi(runner, item)
        points = SimpleNamespace(name='ActionPoint20', cost='YellowCoins', price=1000)
        expensive = SimpleNamespace(name='ActionPoint50', cost='YellowCoins', price=2500)
        repair = SimpleNamespace(name='RepairPack', cost='YellowCoins', price=1000)
        items = [points, expensive, repair]
        self.assertEqual(Selector.items_filter_in_akashi_shop(runner, items), [points])
        runner.is_running_cl1_leveling = True
        self.assertEqual(Selector.items_filter_in_akashi_shop(runner, items), [repair])
        self.assertEqual(runner.config.OpsiGeneral_AkashiShopFilter, 'ActionPoint')
        self.assertEqual(runner.config.OpsiHazard1Leveling_Cl1Filter, 'RepairPack')


class TestAkashiVisitLifecycle(unittest.TestCase):
    """普通入口回退后，本地缓存、去重和正向回图确认仍须完整存在。"""

    def runner(self, *, handled=False, page='map'):
        runner = SimpleNamespace(
            _akashi_handled=handled, page=page,
            os_shop_get_item_to_buy_in_akashi=Mock(),
            handle_story_skip=Mock(return_value=False),
        )
        runner.appear = Mock(side_effect=lambda button, **kwargs:
                             button is PORT_SUPPLY_CHECK and runner.page == 'shop')
        runner.is_in_map = Mock(side_effect=lambda: runner.page == 'map')
        runner.ui_click = Mock(side_effect=lambda *args, **kwargs: setattr(runner, 'page', 'shop'))
        runner.os_shop_buy = Mock()
        runner.ui_back = Mock(side_effect=lambda **kwargs: setattr(runner, 'page', 'map'))
        return runner

    def test_session_survives_purchase_until_confirmed_return_then_clears(self):
        runner = self.runner()
        sessions = []

        def enter(*args, **kwargs):
            self.assertFalse(hasattr(runner, '_akashi_shop_session'))
            runner.page = 'shop'

        def buy(**kwargs):
            self.assertEqual(runner.page, 'shop')
            runner._akashi_shop_session['inventory'] = ['行动力商品']
            sessions.append(runner._akashi_shop_session)

        def leave(**kwargs):
            self.assertIs(runner._akashi_shop_session, sessions[0])
            self.assertFalse(runner._akashi_handled)
            runner.page = 'map'

        runner.ui_click.side_effect = enter
        runner.os_shop_buy.side_effect = buy
        runner.ui_back.side_effect = leave
        self.assertTrue(OSShop.handle_akashi_supply_buy(runner, object()))
        self.assertTrue(runner._akashi_handled)
        self.assertIsNone(runner._akashi_shop_session)
        runner.os_shop_buy.assert_called_once_with(select_func=runner.os_shop_get_item_to_buy_in_akashi)

    def test_purchase_error_clears_session_without_marking_handled(self):
        runner = self.runner()
        runner.os_shop_buy.side_effect = GameStuckError('隔离购买失败')
        with self.assertRaises(GameStuckError):
            OSShop.handle_akashi_supply_buy(runner, object())
        self.assertFalse(runner._akashi_handled)
        self.assertIsNone(runner._akashi_shop_session)
        runner.ui_back.assert_not_called()

    def test_failed_entry_clears_cache_without_purchasing(self):
        runner = self.runner()
        runner.ui_click.side_effect = GameStuckError('隔离入口失败')
        with self.assertRaises(GameStuckError):
            OSShop.handle_akashi_supply_buy(runner, object())
        self.assertFalse(runner._akashi_handled)
        self.assertIsNone(runner._akashi_shop_session)
        runner.os_shop_buy.assert_not_called()
        runner.ui_back.assert_not_called()

    def test_unconfirmed_map_return_does_not_mark_handled(self):
        runner = self.runner()
        runner.ui_back.side_effect = None
        self.assertFalse(OSShop.handle_akashi_supply_buy(runner, object()))
        self.assertFalse(runner._akashi_handled)
        self.assertIsNone(runner._akashi_shop_session)
        runner.is_in_map.assert_called_once()

    def test_repeated_shop_only_closes_and_requires_positive_map_state(self):
        runner = self.runner(handled=True, page='shop')
        self.assertTrue(OSShop.handle_akashi_supply_buy(runner, object()))
        runner.ui_click.assert_not_called()
        runner.os_shop_buy.assert_not_called()
        runner.ui_back.assert_called_once()
        self.assertFalse(hasattr(runner, '_akashi_shop_session'))
        runner.page = 'unknown'
        self.assertFalse(OSShop.handle_akashi_supply_buy(runner, object()))
        self.assertEqual(runner.ui_back.call_count, 1)
        runner.os_shop_buy.assert_not_called()

    def test_failed_shop_is_not_reported_as_a_completed_walk_event(self):
        from module.os.fleet import OSFleet

        clock = VirtualClock()
        runner = WalkStateStub(clock, command='OpsiHazard1Leveling', shop=True)
        runner.handle_akashi_supply_buy.return_value = False
        with patch('module.base.timer.time', clock):
            result = OSFleet.wait_until_walk_stable(runner, confirm_timer=Timer(1), allow_event_finish=True)
        self.assertNotIn('akashi', result)


class TestAkashiMapCompletion(unittest.TestCase):
    """三处直接进店和行军回调都必须传播未确认结果。"""

    @staticmethod
    def runner(distances):
        grid = SimpleNamespace(is_akashi=True, location=(1, 7), distance_to=lambda other: 3)
        runner = SimpleNamespace(
            _solved_map_event=set(),
            config=SimpleNamespace(temporary=lambda **kwargs: nullcontext()),
            device=SimpleNamespace(click=Mock(), screenshot=Mock()),
            view=SimpleNamespace(select=lambda **kwargs: [grid] if kwargs.get('is_akashi') else [],
                                 predict=Mock()),
            update_os=Mock(), fleet_set=Mock(),
            fleet_selector=SimpleNamespace(get=lambda: 1),
            wait_until_walk_stable=Mock(return_value=''),
            _open_akashi_store=Mock(return_value=False),
            _save_pending_map_event=Mock(),
            _finish_pending_map_event=Mock(),
            _close_fleet_bar_if_open=Mock(return_value=False),
            _recover_unreachable_akashi=Mock(return_value=False),
        )
        runner.convert_radar_to_local = Mock(side_effect=[
            SimpleNamespace(distance_to=lambda other, distance=distance: distance)
            for distance in distances
        ])
        return runner, grid

    def test_adjacent_unconfirmed_shop_does_not_mark_event_solved(self):
        runner, grid = self.runner([1])
        self.assertFalse(OSMap.map_rescan_current(runner))
        runner._open_akashi_store.assert_called_once_with(grid, drop=None)
        runner.device.click.assert_not_called()
        self.assertEqual(runner._solved_map_event, set())

    def test_walk_then_unconfirmed_shop_does_not_mark_event_solved(self):
        runner, grid = self.runner([3, 1])
        self.assertFalse(OSMap.map_rescan_current(runner))
        runner.device.click.assert_called_once_with(grid)
        runner.wait_until_walk_stable.assert_called_once()
        runner._open_akashi_store.assert_called_once_with(grid, drop=None)
        self.assertEqual(runner._solved_map_event, set())

    def test_other_fleets_unconfirmed_shops_restore_fleet_without_marking_solved(self):
        runner, grid = self.runner([1, 1, 1])
        self.assertFalse(OSMap._goto_akashi_with_other_fleets(runner))
        self.assertEqual(runner._open_akashi_store.call_args_list,
                         [unittest.mock.call(grid, drop=None)] * 3)
        self.assertEqual(runner.fleet_set.call_args_list,
                         [unittest.mock.call(fleet) for fleet in (2, 3, 4, 1)])
        runner.device.click.assert_not_called()
        self.assertEqual(runner._solved_map_event, set())


    def test_confirmed_bounded_shop_finishes_pending_and_marks_solved(self):
        runner, grid = self.runner([1])
        runner._open_akashi_store.return_value = True
        self.assertTrue(OSMap.map_rescan_current(runner))
        runner._finish_pending_map_event.assert_called_once_with('akashi', node='B8')
        runner._recover_unreachable_akashi.assert_not_called()
        self.assertEqual(runner._solved_map_event, {'is_akashi'})


if __name__ == '__main__':
    unittest.main()
