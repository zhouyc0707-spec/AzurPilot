"""活动商店数量兼容与上游余额记账的离线回归。"""

import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from module.config.redirect_utils.shop import migrate_shop_options
from module.shop_event.clerk import EventShopClerk, ItemNotFoundError
from module.shop_event.selector import parse_filter_amount, parse_filter_tokens, strip_filter_amount
from module.shop_event.shop_event import EventShop
from module.shop_event.ui import EventShopUI


def make_item(name, count=10, sub_genre=None, tier=None):
    group = name.lower() if sub_genre is None else 'plate'
    return SimpleNamespace(name=name, group=group, sub_genre=sub_genre, tier=tier,
                           count=count, price=100, cost='pt')


def make_shop(custom_filter, items, *, ended=False):
    shop = object.__new__(EventShop)
    shop.config = SimpleNamespace(EventShop_BuyURShip=0, EventShop_UnlockSSRShip=False,
                                  EventShop_PresetFilter='custom', EventShop_CustomFilter=custom_filter,
                                  Scheduler_Enable=True, task_stop=Mock())
    shop.pt, shop.urpt, shop.pt_preserved = 10000, 0, 0
    shop.__dict__['is_event_ended'] = ended
    shop.event_shop_load_ensure = Mock()
    shop.scan_all = Mock(return_value=items)
    shop.get_current_pts = Mock()
    shop.get_oil = Mock(return_value=20000)
    shop.handle_items_related_with_urpt = Mock(side_effect=lambda value, *_: (value, []))
    shop.handle_unobtained_items = Mock(side_effect=lambda value, *_: (value, []))
    shop.event_shop_buy_item = Mock()
    return shop


class QuantityMigrationTests(unittest.TestCase):
    def test_migration_preserves_quantity_text_order_and_enabled_state(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                old = {'EventShop': {'Scheduler': {'Enable': enabled}, 'EventShop': {
                    'PresetFilter': 'custom', 'CustomFilter': 'Cube:5 > Oil : 2 > EquipSSR > Coin:0'}}}
                original = copy.deepcopy(old)
                migrated, warnings = migrate_shop_options(old, old)
                self.assertEqual(old, original)
                self.assertEqual(migrated, original)
                self.assertEqual(warnings, [])
                self.assertEqual(migrate_shop_options(migrated, migrated), (migrated, []))

    def test_script_removal_does_not_remove_quantity_limits(self):
        old = {'EventShop': {'Scheduler': {'Enable': True}, 'ShopAdvanced': {
            'Mode': 'advanced', 'Script': 'old'}, 'EventShop': {'CustomFilter': 'Cube:5 > Oil:2'}}}
        migrated, warnings = migrate_shop_options(old, old)
        self.assertNotIn('ShopAdvanced', migrated['EventShop'])
        self.assertFalse(migrated['EventShop']['Scheduler']['Enable'])
        self.assertEqual(migrated['EventShop']['EventShop']['CustomFilter'], 'Cube:5 > Oil:2')
        self.assertEqual(warnings, [('EventShop', '商店脚本策略已移除')])

    def test_quantity_parsing_does_not_turn_zero_into_unlimited_purchase(self):
        self.assertEqual(parse_filter_amount('Cube:5 > Oil:0 > Coin:-1 > EquipSSR'),
                         {'cube': 5, 'oil': 0, 'coin': 0})
        self.assertEqual(strip_filter_amount('Cube : 5 > Oil:0 > EquipSSR'), 'Cube > Oil > EquipSSR')
        self.assertEqual(parse_filter_tokens('Cube:wrong')[0]['name'], 'Cube:wrong')


class QuantityPurchaseTests(unittest.TestCase):
    def test_quantity_cap_does_not_stop_purchase_of_next_priority(self):
        cube, coin = make_item('Cube'), make_item('Coin')
        shop = make_shop('Cube:2 > Coin', [cube, coin])
        shop._run()
        self.assertEqual(shop.event_shop_buy_item.call_args_list, [call(cube, amount=2), call(coin)])
        self.assertEqual(shop.config.EventShop_CustomFilter, 'Coin')
        self.assertTrue(shop.config.Scheduler_Enable)
        shop.config.task_stop.assert_not_called()
        shop.event_shop_buy_item.reset_mock()
        shop._run()
        shop.event_shop_buy_item.assert_called_once_with(coin)

    def test_generic_limit_is_shared_across_matching_shelves(self):
        first = make_item('PlateGeneralT3', count=3, sub_genre='general', tier='t3')
        second = make_item('PlateGunT3', sub_genre='gun', tier='t3')
        coin = make_item('Coin')
        shop = make_shop('Plate:5 > Coin', [first, second, coin])
        shop._run()
        self.assertEqual(shop.event_shop_buy_item.call_args_list,
                         [call(first), call(second, amount=2), call(coin)])
        self.assertEqual(shop.config.EventShop_CustomFilter, 'Coin')

    def test_specific_limit_precedes_generic_limit(self):
        item = make_item('PlateGeneralT3', sub_genre='general', tier='t3')
        self.assertEqual(EventShop.item_filter_amount_key(item, {'plategeneralt3': 2, 'plate': 5}),
                         'plategeneralt3')

    def test_insufficient_balance_preserves_remaining_limit_and_priority(self):
        cube, coin = make_item('Cube'), make_item('Coin')
        shop = make_shop('Cube:5 > Coin', [cube, coin])
        shop.pt = 200
        shop._run()
        shop.event_shop_buy_item.assert_called_once_with(cube, amount=2)
        self.assertEqual(shop.config.EventShop_CustomFilter, 'Cube:3 > Coin')

    def test_zero_limit_skips_item_without_affecting_next_priority(self):
        cube, coin = make_item('Cube'), make_item('Coin')
        shop = make_shop('Cube:0 > Coin', [cube, coin])
        shop._run()
        shop.event_shop_buy_item.assert_called_once_with(coin)

    def test_successful_quantity_is_saved_before_later_rescan_failure(self):
        cube, oil = make_item('Cube', count=2), make_item('Oil')
        shop = make_shop('Cube:5 > Oil:2', [cube, oil])
        shop.event_shop_buy_item.side_effect = [None, ItemNotFoundError('需重扫')]
        with self.assertRaises(ItemNotFoundError):
            shop._run()
        self.assertEqual(shop.config.EventShop_CustomFilter, 'Cube:3 > Oil:2')
        self.assertTrue(shop.config.Scheduler_Enable)

    def test_failed_purchase_does_not_consume_limit(self):
        shop = make_shop('Cube:5', [make_item('Cube')])
        shop.event_shop_buy_item.side_effect = ItemNotFoundError('目标未确认')
        with self.assertRaises(ItemNotFoundError):
            shop._run()
        self.assertEqual(shop.config.EventShop_CustomFilter, 'Cube:5')
        self.assertTrue(shop.config.Scheduler_Enable)

    def test_all_limits_exhausted_disable_only_after_final_balance_read(self):
        cube = make_item('Cube')
        shop = make_shop('Cube:2', [cube])
        history = Mock()
        history.attach_mock(shop.event_shop_buy_item, 'buy')
        history.attach_mock(shop.get_current_pts, 'balance')
        history.attach_mock(shop.config.task_stop, 'stop')
        shop._run()
        self.assertEqual(shop.config.EventShop_CustomFilter, '')
        self.assertFalse(shop.config.Scheduler_Enable)
        self.assertEqual(history.mock_calls[-3:], [call.buy(cube, amount=2), call.balance(), call.stop()])


class UpstreamResourceTrackingTests(unittest.TestCase):
    def test_reliable_pt_balance_still_records_observed_resource(self):
        shop = object.__new__(EventShopUI)
        shop.config = SimpleNamespace()
        shop.device = SimpleNamespace(image=object())
        shop.__dict__['is_pt_reversed'] = False
        with patch('module.shop_event.ui.OCR_EVENT_SHOP_PT') as ocr, \
                patch('module.log_res.LogRes') as recorder:
            ocr.ocr.return_value, ocr.last_valid = 900, True
            self.assertEqual(shop.event_shop_get_pt(), 900)
        recorder.return_value.record.assert_called_once_with('Pt', 900, observed=True)

    def test_reliable_urpt_balance_still_records_resource_flow(self):
        shop = object.__new__(EventShopUI)
        shop.config = SimpleNamespace()
        shop.device = SimpleNamespace(image=object())
        shop.__dict__['is_pt_reversed'] = False
        with patch('module.shop_event.ui.OCR_EVENT_SHOP_URPT') as ocr, \
                patch('module.statistics.resource_flow.observe') as observer:
            ocr.ocr.return_value, ocr.last_valid = 200, True
            self.assertEqual(shop.event_shop_get_urpt(), 200)
        observer.assert_called_once_with(shop.config, 'URPt', 200)


class QuantityExecutionTests(unittest.TestCase):
    def test_relocated_item_keeps_requested_quantity(self):
        item = make_item('Cube')
        item.scroll_pos, item.is_ship = 0.5, False
        clerk = object.__new__(EventShopClerk)
        clerk.event_shop_get_items = Mock(return_value=[item])
        clerk.event_shop_buy_item_execute = Mock()
        with patch('module.shop_event.clerk.EVENT_SHOP_SCROLL') as scroll:
            clerk.event_shop_buy_item(item, amount=2)
        scroll.set.assert_called_once_with(0.5, main=clerk)
        clerk.event_shop_buy_item_execute.assert_called_once_with(item, amount=2)

    def test_ship_purchase_repeats_only_requested_number(self):
        item = make_item('ShipSSR', count=5)
        item.scroll_pos, item.is_ship = 0.5, True
        clerk = object.__new__(EventShopClerk)
        clerk.event_shop_get_items = Mock(return_value=[item])
        clerk.event_shop_buy_item_execute = Mock()
        with patch('module.shop_event.clerk.EVENT_SHOP_SCROLL'):
            clerk.event_shop_buy_item(item, amount=2)
        self.assertEqual(clerk.event_shop_buy_item_execute.call_args_list,
                         [call(item, amount=1), call(item, amount=1)])

    def test_quantity_dialog_sets_cap_before_confirmation(self):
        from module.shop_event.clerk import AMOUNT_MAX, BACK_ARROW_WHITE, SHOP_BUY_CONFIRM_AMOUNT

        clerk = object.__new__(EventShopClerk)
        clerk.device = Mock()
        clerk.event_shop_handle_obstruct = Mock(return_value=False)
        clerk.handle_popup_confirm = Mock(return_value=False)
        clerk.ui_ensure_index = Mock()

        def frames():
            for frame in ('amount', 'amount', 'returned'):
                clerk.frame = frame
                yield None

        clerk.loop = frames
        clerk.appear = lambda button, **_: ((button == AMOUNT_MAX and clerk.frame == 'amount')
                                           or (button == BACK_ARROW_WHITE and clerk.frame == 'returned'))
        with patch('module.shop_event.clerk.Timer') as timer:
            timer.return_value.start.return_value.reached.return_value = True
            clerk.event_shop_buy_item_execute(make_item('Cube'), amount=2)
        self.assertEqual(clerk.ui_ensure_index.call_args.args, (2,))
        self.assertEqual(clerk.device.click.call_args_list, [call(AMOUNT_MAX), call(SHOP_BUY_CONFIRM_AMOUNT)])


if __name__ == '__main__':
    unittest.main()
