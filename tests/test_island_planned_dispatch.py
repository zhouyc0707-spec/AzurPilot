"""离线验证新配方派遣的真实库存、次数、保护线与确认顺序，不连接账号。"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from module.exception import GameStuckError
from module.island.data import DIC_ISLAND_RECIPE
from module.island.planned_dispatch import PlannedProductionMixin, recipe_runtime_terms
from module.island.production_planner import CONFIG_PREFIX
from tests.test_island_production_planner import MemoryConfig


class RawUI(PlannedProductionMixin):
    def __init__(self):
        self.config = MemoryConfig(**{f'{CONFIG_PREFIX}.PlanFingerprint': 'valid'})
        self.posts = {'POST1': {'button': object(), 'state': 'idle'}}
        self._planner_actual_stocks = {}
        self._planner_dispatched = {}
        self._planner_protection = {}
        self._planned_open_product_page = Mock(return_value=True)
        self.back_to_postmanage_from_dispatch = Mock()
        self.confirm_food_dispatch = Mock(side_effect=lambda number, context: (
            (number, timedelta(minutes=10)), datetime(2026, 10, 8, 12)))
        self.finish_food_dispatch = Mock(side_effect=self._finish)

    def _finish(self, post, product, time_var, count, preview, confirmed_at):
        self.deduct_materials(product, count)
        setattr(self, time_var, confirmed_at + preview[1])
        return count


class PlannedDispatchTests(unittest.TestCase):
    def setUp(self):
        self.ui = RawUI()
        self.select = patch('module.island.planned_dispatch.select_manufacture_recipe', return_value=True)
        self.quantity = patch('module.island.planned_dispatch.set_manufacture_quantity', return_value=True)
        self.selector = self.select.start()
        self.quantity_setter = self.quantity.start()
        observed = patch('module.island.planned_dispatch.read_selected_recipe_quantity', return_value=1)
        self.observed_quantity = observed.start()
        self.addCleanup(observed.stop)
        self.addCleanup(self.select.stop)
        self.addCleanup(self.quantity.stop)

    def read_stock(self, product=0, seed=999):
        return {2000: {'stock': product, 'cost': 0, 'display_required': 0},
                1000: {'stock': seed, 'cost': 9, 'display_required': 9}}

    def test_dispatch_rounds_by_actual_recipe_yield_and_commits_after_confirmation(self):
        with patch('module.island.planned_dispatch.read_selected_recipe_inventory', return_value=self.read_stock()):
            units = self.ui._planned_dispatch_recipe('POST1', 'wheat', 200, 'finished')
        self.assertEqual(units, 324)
        self.quantity_setter.assert_called_once_with(self.ui, 2)
        self.assertEqual(self.ui._planner_dispatched[2000], 324)
        self.assertEqual(self.ui.posts['POST1']['runs'], 2)
        self.assertEqual(self.ui._planner_material_inventory[1000]['stock'], 981)
        self.assertEqual(self.ui.posts['POST1']['state'], 'working')

    def test_real_stock_and_existing_jobs_prevent_duplicate_production(self):
        with patch('module.island.planned_dispatch.read_selected_recipe_inventory', return_value=self.read_stock(200)):
            result = self.ui._planned_dispatch_recipe('POST1', 'wheat', 300, 'finished', in_production=162)
        self.assertEqual(result, 0)
        self.quantity_setter.assert_not_called()
        self.ui.confirm_food_dispatch.assert_not_called()
        self.ui.back_to_postmanage_from_dispatch.assert_called_once()

    def test_second_post_does_not_reproduce_first_posts_pending_output(self):
        self.ui.posts['POST2'] = {'button': object(), 'state': 'idle'}
        with patch('module.island.planned_dispatch.read_selected_recipe_inventory', side_effect=lambda *args: self.read_stock()):
            self.assertEqual(self.ui._planned_dispatch_recipe('POST1', 'wheat', 200, 'finished'), 324)
            self.assertEqual(self.ui._planned_dispatch_recipe('POST2', 'wheat', 200, 'other'), 0)
        self.assertEqual(self.ui.confirm_food_dispatch.call_count, 1)

    def test_unknown_inventory_never_becomes_zero_or_confirmed_dispatch(self):
        with patch('module.island.planned_dispatch.read_selected_recipe_inventory', return_value=None):
            with self.assertRaises(GameStuckError):
                self.ui._planned_dispatch_recipe('POST1', 'wheat', 200, 'finished')
        self.quantity_setter.assert_not_called()
        self.ui.confirm_food_dispatch.assert_not_called()
        self.assertEqual(self.ui._planner_dispatched, {})

    def test_failed_quantity_confirmation_does_not_commit_or_deduct(self):
        self.quantity_setter.return_value = False
        with patch('module.island.planned_dispatch.read_selected_recipe_inventory', return_value=self.read_stock()):
            with self.assertRaises(GameStuckError):
                self.ui._planned_dispatch_recipe('POST1', 'wheat', 200, 'finished')
        self.ui.confirm_food_dispatch.assert_not_called()
        self.assertEqual(self.ui._planner_dispatched, {})

    def test_zero_material_recipe_uses_its_real_limit_and_yield(self):
        inventory = {2700: {'stock': 0, 'cost': 0, 'display_required': 0}}
        with patch('module.island.planned_dispatch.read_selected_recipe_inventory', return_value=inventory):
            units = self.ui._planned_dispatch_recipe('POST1', 'coal', 500, 'finished')
        self.quantity_setter.assert_called_once_with(self.ui, 12)
        self.assertEqual(units, 96)

    def test_confirm_timeout_does_not_commit_any_future_stock(self):
        self.ui.confirm_food_dispatch.side_effect = GameStuckError('确认未返回')
        with patch('module.island.planned_dispatch.read_selected_recipe_inventory', return_value=self.read_stock()):
            with self.assertRaises(GameStuckError):
                self.ui._planned_dispatch_recipe('POST1', 'wheat', 200, 'finished')
        self.assertEqual(self.ui._planner_dispatched, {})
        self.ui.finish_food_dispatch.assert_not_called()

    def test_technology_scales_ranch_feed_and_both_outputs_consistently(self):
        costs, outputs = recipe_runtime_terms(self.ui.config, 101016)
        self.assertEqual(costs, {3002: 8})
        self.assertEqual(outputs, {2603: 16, 2604: 16})

    def test_ranch_current_feed_protects_hard_floor_in_actual_scaled_units(self):
        inventory = {3002: {'stock': 24, 'cost': 2, 'display_required': 8},
                     2603: {'stock': 0, 'cost': 0, 'display_required': 0}}
        self.ui._planner_protection = {'cattle_feed': 8}
        with patch('module.island.planned_dispatch.read_selected_recipe_inventory', return_value=inventory):
            units = self.ui._planned_dispatch_recipe('POST1', 'milk', 100, 'finished', extra_stocks={2604: 0})
        self.assertEqual(units, 32)
        self.quantity_setter.assert_called_once_with(self.ui, 2)
        self.assertEqual(self.ui._planner_material_inventory[3002]['stock'], 8)

    def test_secondary_demand_can_dispatch_when_primary_already_full(self):
        self.ui.config.values[f'{CONFIG_PREFIX}.PlannerTargets'] = '{"2603": 20, "2604": 30}'
        inventory = {3002: {'stock': 100, 'cost': 2, 'display_required': 8},
                     2603: {'stock': 1000, 'cost': 0, 'display_required': 0}}
        with patch('module.island.planned_dispatch.read_selected_recipe_inventory', return_value=inventory):
            self.ui._planned_dispatch_recipe('POST1', 'milk', 20, 'finished', extra_stocks={2604: 0})
        self.quantity_setter.assert_called_once_with(self.ui, 2)
        self.assertEqual(self.ui._planner_dispatched[2604], 32)

    def test_changed_technology_is_detected_by_real_material_counter(self):
        inventory = {3002: {'stock': 100, 'cost': 2, 'display_required': 16},
                     2603: {'stock': 0, 'cost': 0, 'display_required': 0}}
        with patch('module.island.planned_dispatch.read_selected_recipe_inventory', return_value=inventory):
            with self.assertRaises(GameStuckError):
                self.ui._planned_dispatch_recipe('POST1', 'milk', 100, 'finished', extra_stocks={2604: 0})
        self.ui.confirm_food_dispatch.assert_not_called()

    def test_all_registered_raw_and_factory_outputs_have_exact_keys(self):
        from module.island.item_ids import ITEM_ID_TO_LOCAL
        from module.island.manufacture_catalog import get_catalog
        from module.island.production_plan_calculator import ProductionPlanCalculator
        from module.island.data import DIC_ISLAND_TECHNOLOGY
        calculator = ProductionPlanCalculator(technology_status={key: True for key in DIC_ISLAND_TECHNOLOGY})
        products = {item for recipe in calculator.recipe_group for item in DIC_ISLAND_RECIPE[recipe]['commission_product']}
        self.assertFalse(products - set(ITEM_ID_TO_LOCAL))
        self.assertEqual(sum(len(items) for items in get_catalog().values()), 29)

    def test_unknown_secondary_with_busy_downstream_does_not_restart_the_task(self):
        from module.island.island_rancher import IslandRancher
        from module.island.assets import ISLAND_POST_SELECT
        ui = IslandRancher.__new__(IslandRancher)
        ui.config = MemoryConfig(**{f'{CONFIG_PREFIX}.PlanFingerprint': 'valid',
                                   f'{CONFIG_PREFIX}.PlannerTargets': '{"2604":5}'})
        for field in ('ChickenFilter', 'PigFilter', 'RancherFilter', 'WoolWorkerFilter'):
            setattr(ui.config, f'IslandRancher_{field}', 'WorkerJuu')
        ui.posts_ranch = {f'ISLAND_RANCH_POST{i}': object() for i in range(1, 5)}
        for method in ('goto_postmanage', 'post_manage_mode', 'post_manage_swipe_to_top', 'post_close'):
            setattr(ui, method, Mock())
        ui.post_open = Mock(return_value=True)
        ui.post_get_and_close = Mock(return_value=True)
        ui.loop = Mock(return_value=iter((object(),)))
        ui.loop.side_effect = lambda **kwargs: iter((object(),))
        ui.appear = Mock(side_effect=lambda button, **kwargs: button is ISLAND_POST_SELECT)
        ui._probe_ranch_secondary_stock = Mock(return_value=None)
        ui._planned_dispatch_recipe = Mock()
        configs = [(f'ISLAND_RANCH_POST{i}', f'time_ranch{i}') for i in range(1, 5)]
        with patch('module.island.island_rancher.current_time', return_value=datetime(2026, 10, 8, 12)):
            ui.run_planned_ranch(configs)
        self.assertEqual(ui.time_ranch3, datetime(2026, 10, 8, 12, 15))
        ui._planned_dispatch_recipe.assert_not_called()
        self.assertIn({'minute': 0, 'task': 'IslandManufacture'}, ui.config.delays)


if __name__ == '__main__':
    unittest.main()
