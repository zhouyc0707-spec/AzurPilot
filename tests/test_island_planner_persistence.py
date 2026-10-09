"""用临时实例验证规划输出经过真实配置读写、任务唤醒和重启后仍然有效。"""

import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from yaml import safe_dump

from module.config.config import AzurLaneConfig
from module.config.config_updater import ConfigUpdater
from module.config.deep import deep_get, deep_set
from module.island.data import DIC_ISLAND_TECHNOLOGY
from module.island.production_planner import (
    CONFIG_PREFIX, FOOD_GROUPS, GROUP_CONFIG, IslandProductionPlanner,
    finish_auto_manufacture, load_planner_targets, refresh_production_plan,
)


RUNTIME_PATHS = tuple(f'{CONFIG_PREFIX}.{name}' for name in (
    'DailyBufferItems', 'IdleAccumulatingItems', 'PlannerTargets', 'PlannerStatus',
    'PlanFingerprint', 'OrderManufactureTargets', 'OrderManufactureFinalTargets',
    'AutoManufactureActive', 'AutoManufactureNextRun', 'CompletedManufactureOrderId',
)) + tuple(f'IslandBusiness.IslandBusinessShop{shop}.PlannedMenu' for shop in range(1, 6)) + (
    'IslandDailyOrder.IslandDailyOrder.StuckSeasonOrderId',
)


class PlannerPersistenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='island-planner-persistence-')
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'fixture.json'
        self.time = datetime(2026, 10, 8, 12)
        self.original_factory_run = datetime(2026, 10, 19, 11)
        # 只替换实例路径和时间；配置迁移、事务锁、保存和加载使用真实实现。
        for target in (
            'module.config.config.filepath_config',
            'module.config.config_updater.filepath_config',
        ):
            context = patch(target, return_value=str(self.path))
            context.start()
            self.addCleanup(context.stop)
        for target, value in (
            ('module.config.config.current_time', self.time),
            ('module.island.production_planner.now', self.time),
            ('module.island.production_planner.server_time_offset', timedelta()),
            ('module.api.island_suspend.read_state', {'suspended': []}),
        ):
            context = patch(target, return_value=value)
            context.start()
            self.addCleanup(context.stop)
        # 本测试聚焦持久化，不调用运行时的设备环境检查及其他任务排程恢复。
        context = patch.object(AzurLaneConfig, 'config_override')
        context.start()
        self.addCleanup(context.stop)

        fixture = json.loads(Path('config/template.json').read_text(encoding='utf-8'))
        values = {
            f'{CONFIG_PREFIX}.Enabled': True,
            f'{CONFIG_PREFIX}.TechnologyStatus': safe_dump({key: True for key in DIC_ISLAND_TECHNOLOGY}),
            'IslandPlan.IslandPlan.Season': 'autumn',
            'IslandDailyGather.Scheduler.Enable': True,
            'IslandBusiness.Scheduler.Enable': True,
            'IslandDailyOrder.Scheduler.Enable': True,
            'IslandManufacture.Scheduler.NextRun': str(self.original_factory_run),
        }
        for group, (task, arguments, maximum) in GROUP_CONFIG.items():
            values[f'{task}.Scheduler.Enable'] = task != 'IslandManufacture'
            if arguments:
                key = 'PostNumber' if group in FOOD_GROUPS else 'Positions'
                values[f'{task}.{arguments}.{key}'] = maximum
        for shop in range(1, 6):
            values[f'IslandBusiness.IslandBusinessShop{shop}.Grade'] = 'diamond'
        for path, value in values.items():
            deep_set(fixture, path, value)
        self.path.write_text(json.dumps(fixture, ensure_ascii=False), encoding='utf-8')

    def config(self, task='IslandFarm'):
        return AzurLaneConfig('planner_persistence_fixture', task=task)

    def disk(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def snapshot(self, config):
        return {path: copy.deepcopy(config.cross_get(path)) for path in RUNTIME_PATHS}

    def generate(self, config):
        calculator = IslandProductionPlanner(config).run(current_time=self.time)
        self.assertTrue(calculator.lp_success)
        self.assertTrue(config.cross_get(f'{CONFIG_PREFIX}.PlanFingerprint'))
        self.assertEqual(json.loads(config.cross_get(f'{CONFIG_PREFIX}.PlannerTargets')),
                         calculator.local_targets)
        self.assertTrue(config.cross_get(f'{CONFIG_PREFIX}.PlannerStatus').startswith('已生成'))
        for shop in range(1, 6):
            # 已生成的空菜单也必须为 {}，不能变回表示尚未规划的空值。
            menu = config.cross_get(f'IslandBusiness.IslandBusinessShop{shop}.PlannedMenu')
            self.assertIsInstance(json.loads(menu), dict)
        return calculator

    def test_plan_survives_producer_wakes_other_task_save_and_same_day_reload(self):
        config = self.config()
        with patch.object(config, 'task_delay', wraps=config.task_delay) as delayed:
            self.generate(config)
        self.assertGreater(delayed.call_count, 0)
        self.assertTrue(all(call.kwargs['minute'] == 0 for call in delayed.call_args_list))
        expected = self.snapshot(config)
        expected_targets = load_planner_targets(config)
        self.assertTrue(expected_targets)
        self.assertEqual(deep_get(self.disk(), f'{CONFIG_PREFIX}.PlanFingerprint'),
                         expected[f'{CONFIG_PREFIX}.PlanFingerprint'])

        config.task_delay(minute=15, task='IslandFarm')
        another = self.config('Research')
        another.cross_set('Research.Scheduler.Enable', True)
        another.task_delay(minute=30)
        reloaded = self.config('IslandRestaurant')
        self.assertEqual(self.snapshot(reloaded), expected)
        self.assertEqual(load_planner_targets(reloaded), expected_targets)
        with patch.object(IslandProductionPlanner, 'run') as solve:
            self.assertTrue(refresh_production_plan(reloaded))
        solve.assert_not_called()

    def test_temporary_factory_lifecycle_survives_restart_and_next_day(self):
        config = self.config('IslandDailyOrder')
        config.cross_set('IslandDailyOrder.IslandDailyOrder.StuckSeasonOrderId', 100060)
        self.generate(config)
        factory = self.config('IslandManufacture')
        self.assertTrue(factory.cross_get('IslandManufacture.Scheduler.Enable'))
        self.assertTrue(factory.cross_get(f'{CONFIG_PREFIX}.AutoManufactureActive'))
        self.assertEqual(factory.cross_get(f'{CONFIG_PREFIX}.AutoManufactureNextRun'),
                         self.original_factory_run)
        finals = {int(key): value for key, value in json.loads(
            factory.cross_get(f'{CONFIG_PREFIX}.OrderManufactureFinalTargets')).items()}
        self.assertEqual(finals, {4011: 5})
        self.assertFalse(finish_auto_manufacture(factory, finals, working=True))
        self.assertFalse(finish_auto_manufacture(factory, {}, working=False))
        self.assertTrue(factory.cross_get('IslandManufacture.Scheduler.Enable'))
        self.assertTrue(finish_auto_manufacture(factory, finals, working=False))

        finished = self.config('IslandDailyOrder')
        self.assertFalse(finished.cross_get('IslandManufacture.Scheduler.Enable'))
        self.assertFalse(finished.cross_get(f'{CONFIG_PREFIX}.AutoManufactureActive'))
        self.assertEqual(finished.cross_get('IslandManufacture.Scheduler.NextRun'),
                         self.original_factory_run)
        self.assertEqual(finished.cross_get(f'{CONFIG_PREFIX}.CompletedManufactureOrderId'), 100060)
        self.assertEqual(finished.cross_get('IslandDailyOrder.IslandDailyOrder.StuckSeasonOrderId'), 100060)
        self.assertEqual(json.loads(finished.cross_get(f'{CONFIG_PREFIX}.OrderManufactureTargets')), {})
        self.assertEqual(json.loads(finished.cross_get(f'{CONFIG_PREFIX}.OrderManufactureFinalTargets')), {})
        self.assertEqual(finished.cross_get('IslandDailyOrder.Scheduler.NextRun'), self.time)
        with patch('module.island.production_planner.now', return_value=self.time + timedelta(days=1)):
            with patch.object(IslandProductionPlanner, 'run') as solve:
                self.assertTrue(refresh_production_plan(finished))
            solve.assert_not_called()
        self.assertFalse(finished.cross_get('IslandManufacture.Scheduler.Enable'))

        # 订单确认交付后清掉卡单，正常规划可继续，工坊不会再被该单重开。
        finished.cross_set('IslandDailyOrder.IslandDailyOrder.StuckSeasonOrderId', 0)
        self.assertTrue(refresh_production_plan(finished))
        delivered = self.config()
        self.assertEqual(delivered.cross_get(f'{CONFIG_PREFIX}.CompletedManufactureOrderId'), 0)
        self.assertFalse(delivered.cross_get('IslandManufacture.Scheduler.Enable'))

    def test_template_clears_all_runtime_outputs_even_when_persisted(self):
        config = self.config()
        with config.multi_set():
            for path in RUNTIME_PATHS:
                definition = deep_get(config.args, path)
                default = definition['value']
                value = (True if isinstance(default, bool) else
                         100060 if isinstance(default, int) else '{"4011": 5}')
                config.cross_set(path, value)
        updater = ConfigUpdater()
        template = updater.config_update(config.data, is_template=True)
        defaults = updater.config_update({}, is_template=True)
        for path in RUNTIME_PATHS:
            with self.subTest(path=path):
                self.assertEqual(deep_get(template, path), deep_get(defaults, path))
                self.assertNotEqual(deep_get(config.data, path), deep_get(template, path))


class RuntimePersistenceMetadataTests(unittest.TestCase):
    def test_persisted_runtime_fields_stay_read_only_in_api(self):
        from module.api.config_service import ConfigService
        from module.api.protocol import ApiError, ConfigChange
        from tests.test_api import fixture

        with tempfile.TemporaryDirectory(prefix='island-planner-api-') as directory:
            service = ConfigService(fixture(directory))
            path = service.path('testpilot')
            before = path.read_bytes()
            for argument in RUNTIME_PATHS:
                with self.subTest(path=argument):
                    self.assertIs(deep_get(service.args, argument)['persist'], True)
                    with self.assertRaises(ApiError) as context:
                        service.patch('testpilot', None, [ConfigChange(path=argument, value='手工伪造结果')])
                    self.assertEqual(context.exception.code, 'READ_ONLY')
                    self.assertEqual(path.read_bytes(), before)

    def test_only_marked_state_and_hidden_fields_persist_and_lock_still_resets(self):
        updater = ConfigUpdater()
        updater.args = {'Fixture': {'Runtime': {
            'PersistentState': {'type': 'state', 'value': '默认状态', 'persist': True},
            'PersistentHidden': {'type': 'textarea', 'display': 'hide', 'value': '{}', 'persist': True},
            'TransientState': {'type': 'state', 'value': '默认状态'},
            'TransientHidden': {'type': 'textarea', 'display': 'hide', 'value': '{}'},
            'Locked': {'type': 'lock', 'value': 3, 'persist': True},
            'HiddenStored': {'type': 'stored', 'display': 'hide', 'value': {}},
        }}}
        old = {'Fixture': {'Runtime': {
            'PersistentState': '规划已生成', 'PersistentHidden': '{"4011": 5}',
            'TransientState': '运行时状态', 'TransientHidden': '{"4011": 5}',
            'Locked': 99, 'HiddenStored': {'counter': 2},
        }}}
        result = updater.config_update(old)['Fixture']['Runtime']
        self.assertEqual(result, {
            'PersistentState': '规划已生成', 'PersistentHidden': '{"4011": 5}',
            'TransientState': '默认状态', 'TransientHidden': '{}',
            'Locked': 3, 'HiddenStored': {'counter': 2},
        })
        template = updater.config_update(old, is_template=True)['Fixture']['Runtime']
        self.assertEqual(template, {key: value['value'] for key, value in updater.args['Fixture']['Runtime'].items()})


if __name__ == '__main__':
    unittest.main()
