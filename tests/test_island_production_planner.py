"""使用明确科技夹具验证纯求解、原子导出及本地目标桥；不连接设备。"""

import json
import unittest
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import patch

from yaml import safe_dump

from module.exception import GameStuckError
from module.island.data import DIC_ISLAND_TECHNOLOGY
from module.island.item_ids import LOCAL_TO_ITEM_ID, resolve_item_id
from module.island.production_planner import (
    CONFIG_PREFIX, FOOD_GROUPS, GROUP_CONFIG, IslandPlanningError,
    IslandProductionPlanner, get_configured_slots, load_planner_targets,
    merge_food_targets, planner_target, refresh_production_plan,
    finish_auto_manufacture, manufacture_order_targets,
    get_planned_recipe_items,
)


class MemoryConfig:
    def __init__(self, **overrides):
        self.values = {
            f'{CONFIG_PREFIX}.Enabled': True,
            f'{CONFIG_PREFIX}.TechnologyStatus': safe_dump({i: True for i in DIC_ISLAND_TECHNOLOGY}),
            'IslandPlan.IslandPlan.Season': 'autumn',
            'IslandBusiness.Scheduler.Enable': True,
            'IslandDailyGather.Scheduler.Enable': True,
        }
        for group, (task, arguments, maximum) in GROUP_CONFIG.items():
            self.values[f'{task}.Scheduler.Enable'] = True
            if arguments:
                key = 'PostNumber' if group in FOOD_GROUPS else 'Positions'
                self.values[f'{task}.{arguments}.{key}'] = maximum
        for shop in range(1, 6):
            self.values[f'IslandBusiness.IslandBusinessShop{shop}.Grade'] = 'diamond'
        self.values.update(overrides)
        self.writes = []
        self.delays = []

    def cross_get(self, key, default=None):
        return self.values.get(key, default)

    def cross_set(self, key, value):
        self.values[key] = value
        self.writes.append((key, value))

    @contextmanager
    def multi_set(self):
        yield

    def task_delay(self, **kwargs):
        self.delays.append(kwargs)


class BufferedConfig(MemoryConfig):
    """模拟真实 cross_get 在 multi_set 退出前读不到 queued cross_set 的语义。"""
    def __init__(self, **overrides):
        super().__init__(**overrides)
        self.pending = {}
        self.depth = 0

    def cross_set(self, key, value):
        self.writes.append((key, value))
        if self.depth:
            self.pending[key] = value
        else:
            self.values[key] = value

    @contextmanager
    def multi_set(self):
        self.depth += 1
        try:
            yield
        finally:
            self.depth -= 1
            if not self.depth:
                self.values.update(self.pending)
                self.pending.clear()


class TemporaryFactoryLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.time = datetime(2026, 10, 8, 12)
        self.config = BufferedConfig(**{
            'IslandManufacture.Scheduler.Enable': False,
            'IslandManufacture.Scheduler.NextRun': '2026-10-19 11:00:00',
            'IslandDailyOrder.Scheduler.Enable': True,
            'IslandDailyOrder.IslandDailyOrder.StuckSeasonOrderId': 100060,
        })

    def generate(self):
        with patch('module.island.production_planner.now', return_value=self.time):
            return IslandProductionPlanner(self.config).run(current_time=self.time)

    def test_enable_close_next_day_refresh_delivery_and_new_shortage_cycle(self):
        calculator = self.generate()
        self.assertTrue(calculator.lp_success)
        self.assertTrue(self.config.cross_get('IslandManufacture.Scheduler.Enable'))
        self.assertTrue(self.config.cross_get(f'{CONFIG_PREFIX}.AutoManufactureActive'))
        self.assertEqual(json.loads(self.config.cross_get(f'{CONFIG_PREFIX}.OrderManufactureFinalTargets')), {'4011': 5})
        with patch('module.island.production_planner.now', return_value=self.time):
            with patch.object(IslandProductionPlanner, 'run') as solve:
                self.assertTrue(refresh_production_plan(self.config))
                solve.assert_not_called()
            self.assertFalse(finish_auto_manufacture(self.config, {4011: 5}, working=True))
            self.assertFalse(finish_auto_manufacture(self.config, {}, working=False))
            self.assertTrue(finish_auto_manufacture(self.config, {4011: 5}))
        self.assertFalse(self.config.cross_get('IslandManufacture.Scheduler.Enable'))
        self.assertEqual(self.config.cross_get('IslandManufacture.Scheduler.NextRun'), '2026-10-19 11:00:00')
        self.assertEqual(self.config.cross_get(f'{CONFIG_PREFIX}.CompletedManufactureOrderId'), 100060)
        self.assertIn({'minute': 0, 'task': 'IslandDailyOrder'}, self.config.delays)
        # 跨日也等待订单实物复核，不将同一需求再次开启。
        with patch('module.island.production_planner.now', return_value=datetime(2026, 10, 9, 12)):
            with patch.object(IslandProductionPlanner, 'run') as solve:
                self.assertTrue(refresh_production_plan(self.config))
                solve.assert_not_called()
        self.assertFalse(self.config.cross_get('IslandManufacture.Scheduler.Enable'))
        # 只有订单实读再次缺货、清标记后才允许重新开启同单。
        self.config.cross_set(f'{CONFIG_PREFIX}.CompletedManufactureOrderId', 0)
        self.generate()
        self.assertTrue(self.config.cross_get('IslandManufacture.Scheduler.Enable'))

    def test_order_delivery_clears_stuck_id_and_allows_normal_replan_without_factory(self):
        self.generate()
        with patch('module.island.production_planner.now', return_value=self.time):
            finish_auto_manufacture(self.config, {4011: 5})
        self.config.cross_set('IslandDailyOrder.IslandDailyOrder.StuckSeasonOrderId', 0)
        with patch('module.island.production_planner.now', return_value=self.time):
            self.assertTrue(refresh_production_plan(self.config))
        self.assertFalse(self.config.cross_get('IslandManufacture.Scheduler.Enable'))
        self.assertFalse(self.config.cross_get(f'{CONFIG_PREFIX}.AutoManufactureActive'))
        self.assertEqual(self.config.cross_get(f'{CONFIG_PREFIX}.CompletedManufactureOrderId'), 0)

    def test_paused_services_do_not_get_factory_auto_enabled(self):
        self.config.config_name = 'offline_fixture'
        with patch('module.api.island_suspend.read_state', return_value={'suspended': ['IslandDailyOrder']}):
            with self.assertRaises(IslandPlanningError):
                self.generate()
        self.assertFalse(self.config.cross_get('IslandManufacture.Scheduler.Enable'))
        self.assertFalse(self.config.cross_get(f'{CONFIG_PREFIX}.AutoManufactureActive', False))

    def test_independent_factory_goal_cannot_borrow_seasonal_temporary_switch(self):
        self.config.values[f'{CONFIG_PREFIX}.TaskTarget'] = '{"cloth": 100}'
        with self.assertRaisesRegex(IslandPlanningError, '需要用户主动开启制造任务'):
            self.generate()
        self.assertFalse(self.config.cross_get('IslandManufacture.Scheduler.Enable'))
        self.config.values['IslandManufacture.Scheduler.Enable'] = True
        self.assertTrue(self.generate().lp_success)

    def test_spring_and_summer_seasonal_orders_include_the_full_manufacture_chain(self):
        for season, day, order, item in (
                ('spring', datetime(2026, 3, 8), 100030, 4028),
                ('summer', datetime(2026, 6, 8), 100045, 4041)):
            with self.subTest(season=season):
                config = BufferedConfig(**{
                    'IslandPlan.IslandPlan.Season': season,
                    'IslandManufacture.Scheduler.Enable': False,
                    'IslandDailyOrder.IslandDailyOrder.StuckSeasonOrderId': order,
                })
                calculator = IslandProductionPlanner(config).run(current_time=day)
                self.assertTrue(calculator.lp_success)
                self.assertTrue(config.cross_get('IslandManufacture.Scheduler.Enable'))
                self.assertEqual(json.loads(config.cross_get(f'{CONFIG_PREFIX}.OrderManufactureFinalTargets')),
                                 {str(item): 5})
                self.assertIn(str(item), calculator.local_targets)

    def test_summer_recipe_gating_does_not_enable_autumn_or_closed_nursery(self):
        from module.island.data import DIC_ISLAND_TECHNOLOGY
        from module.island.planner_utils import get_current_activity_list
        from module.island.production_plan_calculator import ProductionPlanCalculator
        summer = get_current_activity_list(datetime(2026, 6, 8))
        calculator = ProductionPlanCalculator(technology_status={key: True for key in DIC_ISLAND_TECHNOLOGY},
                                             activity_list=summer)
        self.assertTrue(calculator.recipe_available[9900017])
        self.assertFalse(calculator.recipe_available[9900001])
        calculator = ProductionPlanCalculator(technology_status={key: True for key in DIC_ISLAND_TECHNOLOGY},
                                             activity_list=summer, execution_limits={'slots': {9207}})
        self.assertNotIn(9900017, calculator.recipe_group)
        self.assertIn(9900023, calculator.recipe_group)


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.config = MemoryConfig()
        self.time = datetime(2026, 10, 8, 12, 0, 0)

    def test_solve_outputs_are_positive_and_supported_menus(self):
        calculator = IslandProductionPlanner(self.config).run(current_time=self.time)
        self.assertTrue(calculator.lp_success)
        self.assertTrue(calculator.local_targets)
        self.assertTrue(all(value > 0 for value in calculator.local_targets.values()))
        self.assertTrue(all(len(menu) <= 5 for menu in calculator.local_menus.values()))
        self.assertEqual(set(calculator.local_menus), {601, 602, 603, 604, 901})
        self.assertTrue(self.config.values[f'{CONFIG_PREFIX}.PlanFingerprint'])
        self.assertFalse(any('Meal' in key for key, _ in self.config.writes))
        self.assertTrue(all(delay['minute'] == 0 for delay in self.config.delays))

    def test_all_unlocked_recipe_groups_remain_executable_with_old_season_disabled(self):
        from module.island.data import DIC_ISLAND_ACTIVITY, DIC_ISLAND_RECIPE
        from module.island.production_plan_calculator import ProductionPlanCalculator
        from module.island.item_ids import ITEM_ID_TO_LOCAL
        self.config.values['IslandPlan.IslandPlan.Season'] = 'none'
        activities = list(DIC_ISLAND_ACTIVITY)
        calculator = ProductionPlanCalculator(technology_status={key: True for key in DIC_ISLAND_TECHNOLOGY},
                                             activity_list=activities)
        actual = set()
        with patch('module.island.production_planner.get_current_activity_list', return_value=activities):
            for group in set(calculator.recipe_group.values()):
                items = get_planned_recipe_items(self.config, group)
                for item in items:
                    actual.add(item['recipe_id'])
                    product = next(iter(DIC_ISLAND_RECIPE[item['recipe_id']]['commission_product']))
                    self.assertEqual(item['name'], ITEM_ID_TO_LOCAL[product])
        expected = {recipe for recipe, group in calculator.recipe_group.items()
                    if calculator.recipe_available.get(recipe, False)}
        self.assertEqual(actual, expected)
        self.assertIn(401001, actual)
        self.assertIn(402001, actual)
        self.assertIn(201002, actual)
        self.assertIn(9900017, actual)

    def test_disabled_task_and_positions_limit_actual_capacity(self):
        self.config.values['IslandManufacture.Scheduler.Enable'] = False
        self.config.values['IslandFarm.IslandFarm.Positions'] = 1
        slots = get_configured_slots(self.config)
        self.assertIn(9001, slots)
        self.assertNotIn(9002, slots)
        self.assertFalse(any(9201 <= slot <= 9208 for slot in slots))
        calculator = IslandProductionPlanner(self.config).run(current_time=self.time, export=False)
        self.assertFalse(any(calculator.recipe_group[recipe].startswith('manufacturing_')
                             for recipe in calculator.production_plan))

    def test_failed_demand_preserves_old_targets_and_menus(self):
        old_target = '{"3011": 20}'
        self.config.values[f'{CONFIG_PREFIX}.PlannerTargets'] = old_target
        self.config.values['IslandBusiness.IslandBusinessShop1.PlannedMenu'] = '{"tofu_meat": 6}'
        self.config.values[f'{CONFIG_PREFIX}.TaskTarget'] = 'chair_and_desk: {count: 10, period: 1}'
        self.config.values['IslandManufacture.Scheduler.Enable'] = False
        with self.assertRaises(IslandPlanningError):
            IslandProductionPlanner(self.config).run(current_time=self.time)
        self.assertEqual(self.config.values[f'{CONFIG_PREFIX}.PlannerTargets'], old_target)
        self.assertEqual(self.config.values['IslandBusiness.IslandBusinessShop1.PlannedMenu'], '{"tofu_meat": 6}')
        self.assertEqual(len(self.config.writes), 1)
        self.assertIn('需要用户主动开启制造任务', self.config.writes[0][1])

    def test_blank_technology_invokes_scanner_and_caches_only_real_result(self):
        self.config.values[f'{CONFIG_PREFIX}.TechnologyStatus'] = ''
        class Scanner:
            calls = 0
            def __init__(self, config, device):
                pass
            def get_technology_status(self):
                Scanner.calls += 1
                return {i: True for i in DIC_ISLAND_TECHNOLOGY}
        planner = IslandProductionPlanner(self.config, scanner_factory=Scanner)
        planner.run(current_time=self.time)
        planner.run(current_time=self.time)
        self.assertEqual(Scanner.calls, 1)
        self.assertTrue(self.config.values[f'{CONFIG_PREFIX}.TechnologyStatus'])

    def test_scan_failure_propagates_without_writing_false_technology(self):
        self.config.values[f'{CONFIG_PREFIX}.TechnologyStatus'] = ''
        class Scanner:
            def __init__(self, config, device):
                pass
            def get_technology_status(self):
                raise GameStuckError('科技页不可识别')
        with self.assertRaises(GameStuckError):
            IslandProductionPlanner(self.config, scanner_factory=Scanner).run(current_time=self.time)
        self.assertEqual(self.config.writes, [])

    def test_invalid_technology_string_is_not_truthy_unlocked(self):
        self.config.values[f'{CONFIG_PREFIX}.TechnologyStatus'] = '310101: "false"'
        with self.assertRaises(IslandPlanningError):
            IslandProductionPlanner(self.config).run(current_time=self.time)
        self.assertNotIn(f'{CONFIG_PREFIX}.PlanFingerprint', self.config.values)

    def test_same_day_refresh_does_not_wake_self_or_other_tasks(self):
        with patch('module.island.production_planner.now', return_value=self.time), \
                patch('module.island.production_planner.server_time_offset', return_value=self.time - self.time):
            self.assertTrue(refresh_production_plan(self.config))
            delays = list(self.config.delays)
            with patch.object(IslandProductionPlanner, 'run', side_effect=AssertionError('重复求解')):
                self.assertTrue(refresh_production_plan(self.config))
            self.assertEqual(self.config.delays, delays)

    def test_explicit_ignore_avocado_cannot_satisfy_avocado_order(self):
        self.config.values['IslandFarm.IslandOrchard.IgnoreAvocado'] = True
        self.config.values[f'{CONFIG_PREFIX}.TaskTarget'] = 'avocado: {count: 10, period: 1}'
        with self.assertRaises(IslandPlanningError):
            IslandProductionPlanner(self.config).run(current_time=self.time)

    def test_hard_floor_adds_target_and_remains_user_input(self):
        self.config.values[f'{CONFIG_PREFIX}.HardFloorItems'] = 'cheese: 17'
        calculator = IslandProductionPlanner(self.config).run(current_time=self.time)
        self.assertGreaterEqual(calculator.local_targets['3006'], 17)
        self.assertEqual(self.config.values[f'{CONFIG_PREFIX}.HardFloorItems'], 'cheese: 17')

    def test_planner_disabled_does_not_scan_or_modify(self):
        self.config.values[f'{CONFIG_PREFIX}.Enabled'] = False
        self.assertIsNone(IslandProductionPlanner(self.config).run(current_time=self.time))
        self.assertFalse(refresh_production_plan(self.config))
        self.assertEqual(self.config.writes, [])


class TargetBridgeTests(unittest.TestCase):
    def setUp(self):
        self.config = MemoryConfig()
        self.config.values[f'{CONFIG_PREFIX}.PlanFingerprint'] = 'verified-offline-fixture'
        for shop in range(1, 6):
            self.config.values[f'IslandBusiness.IslandBusinessShop{shop}.PlannedMenu'] = '{}'

    def test_runtime_food_goals_take_over_and_disabled_restores_manual(self):
        self.config.values[f'{CONFIG_PREFIX}.PlannerTargets'] = '{"3006": 20,"3010": 3}'
        manual = [('latte', 8), ('cheese', 30), ('iced_coffee', 5)]
        combined = merge_food_targets(self.config, manual, ['cheese', 'strawberry_milkshake', 'latte'])
        self.assertEqual(combined, [('cheese', 20), ('strawberry_milkshake', 3)])
        self.assertEqual(manual, [('latte', 8), ('cheese', 30), ('iced_coffee', 5)])
        self.config.values[f'{CONFIG_PREFIX}.Enabled'] = False
        self.assertEqual(merge_food_targets(self.config, manual, ['cheese']), manual)

    def test_raw_target_takes_over_and_disabled_restores_custom_minimum(self):
        self.config.values[f'{CONFIG_PREFIX}.PlannerTargets'] = '{"2000": 700}'
        self.assertEqual(planner_target(self.config, 'wheat', 660), 700)
        self.assertEqual(planner_target(self.config, 'wheat', 900), 700)
        self.assertEqual(planner_target(self.config, 'cotton', 90), 0)
        self.config.values[f'{CONFIG_PREFIX}.Enabled'] = False
        self.assertEqual(planner_target(self.config, 'wheat', 900), 900)

    def test_menu_change_immediately_adds_new_shelf_lower_bound(self):
        self.config.values[f'{CONFIG_PREFIX}.PlannerTargets'] = '{}'
        self.config.values['IslandBusiness.IslandBusinessShop5.PlannedMenu'] = '{"cheese": 0.1}'
        targets = load_planner_targets(self.config)
        self.assertEqual(targets[3006], 6)

    def test_unknown_stock_target_raises_instead_of_being_discarded(self):
        self.config.values[f'{CONFIG_PREFIX}.PlannerTargets'] = '{"999999": 10}'
        with self.assertRaises(IslandPlanningError):
            load_planner_targets(self.config)

    def test_numeric_localized_and_local_keys_are_exact(self):
        self.assertEqual(resolve_item_id('芝士'), 3006)
        self.assertEqual(resolve_item_id('cheese'), 3006)
        self.assertEqual(resolve_item_id('3006'), 3006)
        with self.assertRaises(ValueError):
            resolve_item_id('cheez')
        self.assertEqual(LOCAL_TO_ITEM_ID['raw_timber'], 2800)

    def test_nonfinite_or_negative_stored_target_cannot_be_dispatched(self):
        for value in [-1, float('nan'), True]:
            self.config.values[f'{CONFIG_PREFIX}.PlannerTargets'] = json.dumps({'3006': value})
            with self.assertRaises(IslandPlanningError):
                load_planner_targets(self.config)

    def test_manufacture_dependency_counts_include_final_raw_claims(self):
        with patch('module.island.production_planner.get_stuck_season_order_requirements',
                   return_value={3035: 3, 3038: 5}):
            needed, final = manufacture_order_targets(123)
        self.assertEqual(final, {3035: 3, 3038: 5})
        self.assertEqual(needed[3035], 8)
        self.assertEqual(needed[3038], 5)

    def test_only_owned_factory_closes_after_goods_collected(self):
        self.config.values[f'{CONFIG_PREFIX}.AutoManufactureActive'] = True
        self.config.values[f'{CONFIG_PREFIX}.OrderManufactureFinalTargets'] = '{"3038": 5}'
        self.config.values[f'{CONFIG_PREFIX}.AutoManufactureNextRun'] = '2026-10-09 08:00:00'
        self.assertFalse(finish_auto_manufacture(self.config, {3038: 4}))
        self.assertFalse(finish_auto_manufacture(self.config, {3038: 5}, working=True))
        self.assertFalse(finish_auto_manufacture(self.config, {}))
        self.assertTrue(finish_auto_manufacture(self.config, {3038: 5}))
        self.assertFalse(self.config.values['IslandManufacture.Scheduler.Enable'])
        self.assertFalse(self.config.values[f'{CONFIG_PREFIX}.AutoManufactureActive'])
        self.assertEqual(self.config.values['IslandManufacture.Scheduler.NextRun'], '2026-10-09 08:00:00')

    def test_manually_enabled_factory_is_never_auto_closed(self):
        self.assertFalse(finish_auto_manufacture(self.config, {}, working=False))
        self.assertTrue(self.config.values['IslandManufacture.Scheduler.Enable'])


if __name__ == '__main__':
    unittest.main()
