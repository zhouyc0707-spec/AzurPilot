"""规划详情仅记录可靠读数与确认下单；所有游戏交互均用离线夹具。"""

import json
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from module.exception import GameStuckError
from module.island.data import DIC_ISLAND_RECIPE
from module.island.fish_exchange import FishExchangeSession
from module.island.island_business import IslandBusiness
from module.island.island_manufacture import IslandManufacture
from module.island.island_shop_base import IslandShopBase
from module.island.manufacture_catalog import get_catalog
from module.island.manufacture_selector import read_selected_recipe_inventory
from module.island.order import IslandOrder
from module.island.planner_report import record_planner_stocks
from module.island.production_planner import CONFIG_PREFIX
from module.island.stock_probe import read_item_stocks
from tests.test_island_planned_dispatch import RawUI
from tests.test_island_production_planner import MemoryConfig


AT = datetime(2026, 10, 9, 12)
REPORT_PATH = f'{CONFIG_PREFIX}.PlannerReport'


def config_for(*items):
    report = {'version': 1, 'items': [{'id': item} for item in items],
              'observations': {}, 'dispatches': {}}
    return MemoryConfig(**{f'{CONFIG_PREFIX}.PlanFingerprint': 'valid', REPORT_PATH: json.dumps(report)})


def report_of(config):
    return json.loads(config.values[REPORT_PATH])


class PlannerReportHookTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.island.planner_report.now', return_value=AT))

    def test_complete_recipe_records_product_zero_and_every_actual_material(self):
        recipe = DIC_ISLAND_RECIPE[701022]
        product = next(iter(recipe['commission_product']))
        stocks = {item: 10 + index for index, item in enumerate(recipe['commission_cost'])}
        stocks[product] = 0
        main = SimpleNamespace(config=config_for(*stocks),
                               device=SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8)))
        counters = [(stocks[item], cost * 2) for item, cost in recipe['commission_cost'].items()]
        with patch('module.island.manufacture_selector._selected_recipe_row', return_value=object()), \
                patch('module.island.manufacture_selector._read_product_stock', return_value=0), \
                patch('module.island.manufacture_selector.ManufactureIngredientCounter') as counter:
            counter.return_value.ocr.return_value = counters
            result = read_selected_recipe_inventory(main, 701022)
        self.assertEqual({item: data['stock'] for item, data in result.items()}, stocks)
        observed = report_of(main.config)['observations']
        self.assertEqual({int(item): row['stock'] for item, row in observed.items()}, stocks)
        self.assertTrue(all(row['at'] == '2026-10-09 12:00:00' and not row['stale'] for row in observed.values()))
        self.assertEqual(observed[str(product)]['source'], '配方选品页')

    def test_partial_recipe_does_not_save_materials_or_invent_product_zero(self):
        main = SimpleNamespace(config=config_for(2700, 3056),
                               device=SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8)))
        with patch('module.island.manufacture_selector._selected_recipe_row', return_value=object()), \
                patch('module.island.manufacture_selector.ManufactureIngredientCounter') as counter:
            counter.return_value.ocr.return_value = [(10, 1), None, (10, 1)]
            self.assertIsNone(read_selected_recipe_inventory(main, 701022))
        self.assertEqual(report_of(main.config)['observations'], {})
        self.assertEqual(main.config.writes, [])

    def test_zero_material_recipe_requires_explicit_product_stock(self):
        main = SimpleNamespace(config=config_for(2700))
        with patch('module.island.manufacture_selector._selected_recipe_row', return_value=object()), \
                patch('module.island.manufacture_selector._read_product_stock', side_effect=[None, 0]):
            self.assertIsNone(read_selected_recipe_inventory(main, 401001))
            self.assertEqual(main.config.writes, [])
            self.assertEqual(read_selected_recipe_inventory(main, 401001)[2700]['stock'], 0)
        self.assertEqual(report_of(main.config)['observations']['2700']['stock'], 0)

    def test_stock_probe_records_only_successful_details_including_real_zero(self):
        main = SimpleNamespace(config=config_for(2603, 2604, 2605))
        with patch('module.island.stock_probe.StockProbeSession') as session:
            session.return_value.run.return_value = {2604: 0, 2605: 17}
            self.assertEqual(read_item_stocks(main, (2603, 2604, 2605)), {2604: 0, 2605: 17})
        self.assertEqual({item: row['stock'] for item, row in report_of(main.config)['observations'].items()},
                         {'2604': 0, '2605': 17})

    def test_disabled_planner_stock_hook_keeps_original_result_and_configuration(self):
        main = SimpleNamespace(config=config_for(2604))
        main.config.values[f'{CONFIG_PREFIX}.Enabled'] = False
        with patch('module.island.stock_probe.StockProbeSession') as session:
            session.return_value.run.return_value = {2604: 17}
            self.assertEqual(read_item_stocks(main, (2604,)), {2604: 17})
        self.assertEqual(main.config.writes, [])

    def raw_dispatch(self, ui, inventory, name='wheat', target=200, **kwargs):
        with patch('module.island.planned_dispatch.select_manufacture_recipe', return_value=True), \
                patch('module.island.planned_dispatch.set_manufacture_quantity', return_value=True), \
                patch('module.island.planned_dispatch.read_selected_recipe_quantity', return_value=1), \
                patch('module.island.planned_dispatch.read_selected_recipe_inventory', return_value=inventory):
            return ui._planned_dispatch_recipe('POST1', name, target, 'finished', **kwargs)

    def test_raw_dispatch_records_actual_confirmed_batches_without_guessing_after_stock(self):
        ui = RawUI()
        ui.config = config_for(2000, 1000)
        record_planner_stocks(ui.config, {2000: 0, 1000: 999}, '测试可靠读数')
        inventory = {2000: {'stock': 0, 'cost': 0, 'display_required': 0},
                     1000: {'stock': 999, 'cost': 9, 'display_required': 9}}
        self.assertEqual(self.raw_dispatch(ui, inventory), 324)
        report = report_of(ui.config)
        self.assertEqual(report['dispatches']['2000']['amount'], 324)
        self.assertEqual(report['dispatches']['2000']['source'], '原料派遣确认')
        self.assertEqual(report['observations']['1000']['stock'], 999)
        self.assertTrue(report['observations']['1000']['stale'])
        self.assertFalse(report['observations']['2000']['stale'])
        self.assertEqual(ui._planner_material_inventory[1000]['stock'], 981)

    def test_ranch_dispatch_records_runtime_technology_and_secondary_output(self):
        ui = RawUI()
        ui.config = config_for(3002, 2603, 2604)
        inventory = {3002: {'stock': 24, 'cost': 2, 'display_required': 8},
                     2603: {'stock': 0, 'cost': 0, 'display_required': 0}}
        ui._planner_protection = {'cattle_feed': 8}
        self.assertEqual(self.raw_dispatch(ui, inventory, 'milk', 100, extra_stocks={2604: 0}), 32)
        self.assertEqual({item: row['amount'] for item, row in report_of(ui.config)['dispatches'].items()},
                         {'2603': 32, '2604': 32})

    def test_raw_failed_confirm_retains_stock_and_does_not_record_dispatch(self):
        ui = RawUI()
        ui.config = config_for(2000, 1000)
        record_planner_stocks(ui.config, {1000: 999}, '测试可靠读数')
        ui.confirm_food_dispatch.side_effect = GameStuckError('确认超时')
        inventory = {2000: {'stock': 0, 'cost': 0, 'display_required': 0},
                     1000: {'stock': 999, 'cost': 9, 'display_required': 9}}
        with self.assertRaises(GameStuckError):
            self.raw_dispatch(ui, inventory)
        report = report_of(ui.config)
        self.assertEqual(report['dispatches'], {})
        self.assertFalse(report['observations']['1000']['stale'])
        self.assertEqual(ui._planner_dispatched, {})

    def test_report_write_failure_does_not_swallow_game_exception_or_change_dispatch_result(self):
        ui = RawUI()
        ui.config = config_for(2000, 1000)
        ui.config.cross_set = Mock(side_effect=OSError('离线展示写入失败'))
        inventory = {2000: {'stock': 0, 'cost': 0, 'display_required': 0},
                     1000: {'stock': 999, 'cost': 9, 'display_required': 9}}
        self.assertEqual(self.raw_dispatch(ui, inventory), 324)
        self.assertEqual(ui._planner_dispatched, {2000: 324})
        ui.confirm_food_dispatch.side_effect = GameStuckError('游戏确认仍交给上层恢复')
        ui._planner_dispatched = {}
        with self.assertRaises(GameStuckError):
            self.raw_dispatch(ui, inventory)

    def test_manufacture_uses_actual_returned_batches_and_only_invalidates_materials(self):
        ui = IslandManufacture.__new__(IslandManufacture)
        ui.config = config_for(3035, 2010)
        ui._planner_protection, ui._manufacture_scheduled = {}, {}
        ui._post_time_vars, ui.post_check_meal = {'POST1': 'finish'}, {}
        ui.warehouse_counts = {}
        ui._prepare_planned_recipe_page = Mock(return_value=True)
        ui.back_to_postmanage_from_dispatch = Mock()
        ui.confirm_food_dispatch = Mock(return_value=((2, object()), object()))
        ui.finish_food_dispatch = Mock(return_value=1)
        item = next(item for items in get_catalog().values() for item in items if item['name'] == 'cloth')
        inventory = {3035: {'stock': 0, 'cost': 0}, 2010: {'stock': 100, 'cost': item['ingredients'][2010]}}
        record_planner_stocks(ui.config, {3035: 0, 2010: 100}, '测试可靠读数')
        with patch('module.island.manufacture_selector.select_manufacture_recipe', return_value=True), \
                patch('module.island.manufacture_selector.set_manufacture_quantity', return_value=True), \
                patch('module.island.manufacture_selector.read_selected_recipe_inventory', return_value=inventory):
            self.assertEqual(ui._dispatch_planned_manufacture('POST1', item, 2), item['yield'])
        report = report_of(ui.config)
        self.assertEqual(report['dispatches']['3035']['amount'], item['yield'])
        self.assertEqual(report['observations']['2010']['stock'], 100)
        self.assertTrue(report['observations']['2010']['stale'])
        self.assertFalse(report['observations']['3035']['stale'])

    def food_ui(self):
        ui = IslandShopBase.__new__(IslandShopBase)
        ui.config = config_for(3007, 2603, 2009)
        ui.config.values[f'{CONFIG_PREFIX}.PlannerTargets'] = '{"3007":100}'
        ui.warehouse_counts, ui._planner_protection, ui._reserved_targets = {}, {}, {}
        ui.meal_compositions, ui.post_check_meal, ui._planned_food_dispatched = {}, {}, {}
        ui.chef_config = 'WorkerJuu'
        ui.chef_unavailable_products = set()
        ui._item_cn = lambda name: name
        ui.back_to_postmanage_from_dispatch = Mock()
        ui.confirm_food_dispatch = Mock(return_value=((1, timedelta(minutes=10)), AT))
        ui.finish_food_dispatch = Mock(side_effect=lambda post, product, time_var, number, preview, at:
                                       (ui._sync_planned_food_materials(product, 1), 1)[1])
        return ui

    def dispatch_food(self, ui):
        inventory = {3007: {'stock': 0, 'cost': 0}, 2603: {'stock': 10, 'cost': 2},
                     2009: {'stock': 10, 'cost': 1}}
        with patch('module.island.planned_dispatch.PlannedProductionMixin._planned_open_product_page', return_value=True), \
                patch('module.island.manufacture_selector.select_manufacture_recipe', return_value=True), \
                patch('module.island.manufacture_selector.set_manufacture_quantity', return_value=True), \
                patch('module.island.manufacture_selector.read_selected_recipe_inventory', return_value=inventory):
            return ui._post_produce_generic_food('POST1', 'latte', 2, 'finish')

    def test_food_dispatch_records_confirmed_output_and_preserves_pre_use_observation(self):
        ui = self.food_ui()
        record_planner_stocks(ui.config, {3007: 0, 2603: 10, 2009: 10}, '测试可靠读数')
        self.assertEqual(self.dispatch_food(ui), 1)
        report = report_of(ui.config)
        self.assertEqual(report['dispatches']['3007']['amount'], 1)
        self.assertEqual(report['observations']['2603']['stock'], 10)
        self.assertTrue(report['observations']['2603']['stale'])
        self.assertEqual(ui.warehouse_counts['milk'], 8)
        self.assertEqual(ui._planned_food_outputs, {})

    def test_food_confirm_failure_never_records_dispatch_or_modeled_stock(self):
        ui = self.food_ui()
        ui.confirm_food_dispatch.side_effect = GameStuckError('食品确认失败')
        with self.assertRaises(GameStuckError):
            self.dispatch_food(ui)
        ui.finish_food_dispatch.assert_not_called()
        self.assertEqual(report_of(ui.config)['dispatches'], {})

    def order_ui(self, result):
        order = IslandOrder.__new__(IslandOrder)
        order.config = config_for(2000)
        order.device = SimpleNamespace(screenshot=Mock(), save_screenshot=Mock())
        order.next_runtime, order.hard_floor, order.reserve = [], {}, {}
        order._click_order = Mock(return_value='detail')
        order._submit_order = Mock(return_value=result)
        order._reenter = Mock()
        order._read_time = Mock(return_value=timedelta(hours=1))
        order.scan_current_order_requirements = Mock(return_value={2000: (100, 10, 90)})
        order.appear = Mock(return_value=True)
        return order

    def test_only_confirmed_order_delivery_invalidates_observed_stock(self):
        for result in (True, False, None):
            with self.subTest(result=result):
                order = self.order_ui(result)
                order._process_order((200, 200), 'urgent')
                observed = report_of(order.config)['observations']['2000']
                self.assertEqual(observed['stock'], 100)
                self.assertEqual(observed['stale'], result is True)
                self.assertEqual(observed['source'], '订单需求页')
                self.assertEqual(report_of(order.config)['dispatches'], {})

    def test_unrecognized_order_requirements_never_overwrite_last_observation(self):
        order = self.order_ui(True)
        record_planner_stocks(order.config, {2000: 5}, '测试可靠读数')
        order.scan_current_order_requirements.return_value = None
        self.assertFalse(order._process_order((200, 200), 'urgent'))
        order._submit_order.assert_not_called()
        observed = report_of(order.config)['observations']['2000']
        self.assertEqual(observed['stock'], 5)
        self.assertFalse(observed['stale'])

    def test_business_confirmation_invalidates_only_possible_menu_products(self):
        ui = IslandBusiness.__new__(IslandBusiness)
        ui.config = config_for(3007, 3006)
        ui.active_products = {'啾咖啡': [{'name': 'latte'}]}
        ui.shop_products = {'啾咖啡': [{'name': 'cheese'}]}
        record_planner_stocks(ui.config, {3007: 7, 3006: 9}, '测试可靠读数')
        for method in ('_load_shop_characters', '_check_and_replace_boosted_product',
                       '_select_business_characters', '_select_business_product', 'post_manage_mode'):
            setattr(ui, method, Mock())
        ui.device = SimpleNamespace(sleep=Mock())
        ui._confirm_business_start = Mock(return_value=True)
        ui._process_shop_entry({'name': '啾咖啡'})
        report = report_of(ui.config)
        self.assertEqual(report['observations']['3007']['stock'], 7)
        self.assertTrue(report['observations']['3007']['stale'])
        self.assertFalse(report['observations']['3006']['stale'])

    def test_business_timeout_propagates_without_marking_stock_consumed(self):
        ui = IslandBusiness.__new__(IslandBusiness)
        ui.config = config_for(3007)
        ui.active_products = {'啾咖啡': [{'name': 'latte'}]}
        record_planner_stocks(ui.config, {3007: 7}, '测试可靠读数')
        for method in ('_load_shop_characters', '_check_and_replace_boosted_product',
                       '_select_business_characters', '_select_business_product'):
            setattr(ui, method, Mock())
        ui._confirm_business_start = Mock(side_effect=GameStuckError('经营确认超时'))
        with self.assertRaises(GameStuckError):
            ui._process_shop_entry({'name': '啾咖啡'})
        self.assertFalse(report_of(ui.config)['observations']['3007']['stale'])

    def fish_session(self):
        main = SimpleNamespace(config=config_for(2521, 5002), device=SimpleNamespace(screenshot=Mock()))
        session = FishExchangeSession(main)
        session._goto_group = Mock(return_value=20)
        session._cards = Mock(return_value={5002: {'stock': 10, 'button': (10, 10, 20, 20)}})
        session._panel = Mock(return_value=True)
        session._amounts = Mock(return_value=(20, 0))
        session._exchange_batch = Mock(return_value=24)
        record_planner_stocks(main.config, {2521: 20, 5002: 10}, '测试可靠读数')
        return session

    def test_fish_exchange_records_confirmed_arrival_and_keeps_consumed_fish_stale(self):
        session = self.fish_session()
        self.assertTrue(session.run({2521: 24}, {}))
        observed = report_of(session.main.config)['observations']
        self.assertEqual(observed['2521']['stock'], 24)
        self.assertFalse(observed['2521']['stale'])
        self.assertEqual(observed['2521']['source'], '鱼肉兑换到账页')
        self.assertEqual(observed['5002']['stock'], 10)
        self.assertTrue(observed['5002']['stale'])
        self.assertEqual(report_of(session.main.config)['dispatches'], {})

    def test_fish_exchange_unknown_does_not_guess_arrival_or_stock_consumption(self):
        session = self.fish_session()
        session._exchange_batch.side_effect = GameStuckError('兑换未确认到账')
        with self.assertRaises(GameStuckError):
            session.run({2521: 24}, {})
        observed = report_of(session.main.config)['observations']
        self.assertEqual(observed['2521']['stock'], 20)
        self.assertFalse(observed['2521']['stale'])
        self.assertFalse(observed['5002']['stale'])


if __name__ == '__main__':
    unittest.main()
