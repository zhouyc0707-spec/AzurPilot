"""关闭制造业时的有限季节补产调度；不创建配置或设备，不操作游戏。"""

import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.api.island_suspend import IslandSuspendService, read_state
from module.config.config import AzurLaneConfig, Function
from module.island.production_round import delay_failed_preparation, run_production_task
from module.island.seasonal_scheduler import effective_task_enabled, seasonal_manufacture_enabled
from module.scheduler.context import simulation_plan, snapshot
from tests import test_island_production_round as round_tests
from tests.test_island_suspend import FakeConfigs


NOW = datetime(2026, 11, 10, 10)


def config_data(target=10):
    data = {name: {'Scheduler': {'Enable': name != 'IslandManufacture', 'Command': name,
                                'NextRun': NOW - timedelta(minutes=1)}}
            for name in ('Commission', 'Research', 'IslandFarm', 'IslandManufacture')}
    data['IslandPlan'] = {'IslandPlan': {'Season': 'winter'}}
    data['IslandSeasonalProduction'] = {'IslandSeasonWinter': {'winter_bouquet': target, 'ImportedTargets': '{}'}}
    return data


class SeasonalSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch('module.api.island_suspend.filepath_config',
                                side_effect=lambda name: str(self.root / f'{name}.json')))
        self.enterContext(patch('module.config.config.current_time', return_value=NOW))
        self.enterContext(patch.object(AzurLaneConfig, 'is_hoarding_task', False))
        self.data = config_data()

    def queue(self):
        config = SimpleNamespace(data=self.data, config_name='isolated', hoarding=timedelta(0),
                                 SCHEDULER_PRIORITY='Commission > Research > IslandFarm > IslandManufacture')
        AzurLaneConfig.get_next_task(config)
        return config

    def test_positive_target_projects_original_priority_without_modifying_saved_switch(self):
        before = copy.deepcopy(self.data)
        config = self.queue()
        self.assertEqual([t.command for t in config.pending_task],
                         ['Commission', 'Research', 'IslandFarm', 'IslandManufacture'])
        self.assertTrue(config.pending_task[-1].enable)
        self.assertEqual(self.data, before)

    def test_original_deadline_is_preserved_for_virtual_manufacture(self):
        self.data['IslandManufacture']['Scheduler']['NextRun'] = NOW + timedelta(hours=2)
        config = self.queue()
        self.assertNotIn('IslandManufacture', [t.command for t in config.pending_task])
        self.assertEqual(config.waiting_task[0].command, 'IslandManufacture')
        self.assertEqual(config.waiting_task[0].next_run, NOW + timedelta(hours=2))

    def test_zero_invalid_or_another_season_never_wakes_disabled_manufacture(self):
        for target in (0, -1, False, 1.5, 'bad'):
            with self.subTest(target=target):
                self.data = config_data(target)
                self.assertFalse(seasonal_manufacture_enabled(self.data))
        self.data = config_data()
        for season in ('spring', 'unknown'):
            self.data['IslandPlan']['IslandPlan']['Season'] = season
            self.assertFalse(seasonal_manufacture_enabled(self.data))

    def test_imported_ordinary_manufacture_target_also_qualifies(self):
        from module.island.seasonal_task_targets import format_imported_targets, imported_item_catalog
        self.data = config_data(0)
        item = next(item for item, (name, category) in imported_item_catalog().items() if name == 'ink_cartridge')
        self.data['IslandSeasonalProduction']['IslandSeasonWinter']['ImportedTargets'] = format_imported_targets({item: 30})
        self.assertTrue(seasonal_manufacture_enabled(self.data))

    def test_malformed_imported_targets_fail_closed_with_explanation(self):
        self.data['IslandSeasonalProduction']['IslandSeasonWinter']['ImportedTargets'] = '{'
        with patch('module.island.seasonal_scheduler.logger.warning') as warning:
            self.assertFalse(seasonal_manufacture_enabled(self.data))
        self.assertIn('暂不加入补产调度', warning.call_args.args[0])

    def test_virtual_only_suspend_and_restore_never_enable_the_saved_task(self):
        self.data = config_data()
        for name in ('Commission', 'Research', 'IslandFarm'):
            self.data[name]['Scheduler']['Enable'] = False
        configs = FakeConfigs(self.data)
        service = IslandSuspendService(configs)
        self.assertEqual(service.state('isolated')['enabledCount'], 1)
        state = service.toggle('isolated')
        self.assertEqual(state['suspended'], ['IslandManufacture'])
        self.assertEqual(state['enabledCount'], 0)
        self.assertFalse(seasonal_manufacture_enabled(self.data, 'isolated'))
        self.assertEqual(read_state('isolated')['seasonal_only'], ['IslandManufacture'])
        restored = service.toggle('isolated')
        self.assertEqual(restored['enabledCount'], 1)
        self.assertFalse(self.data['IslandManufacture']['Scheduler']['Enable'])
        self.assertEqual(configs.patched, [])

    def test_manual_enable_after_virtual_pause_is_preserved_on_restore(self):
        configs = FakeConfigs(self.data)
        service = IslandSuspendService(configs)
        service.toggle('isolated')
        self.data['IslandManufacture']['Scheduler']['Enable'] = True
        configs.patched.clear()
        service.toggle('isolated')
        self.assertTrue(self.data['IslandManufacture']['Scheduler']['Enable'])
        self.assertNotIn(('IslandManufacture.Scheduler.Enable', True), configs.patched)
        self.assertNotIn(('IslandManufacture.Scheduler.Enable', False), configs.patched)

    def test_legacy_pause_record_blocks_new_virtual_work_and_restores_only_recorded_switches(self):
        self.data['IslandFarm']['Scheduler']['Enable'] = False
        (self.root / 'island_suspend_isolated.json').write_text(
            json.dumps({'suspended': ['IslandFarm'], 'at': '2026-11-10'}), encoding='utf-8')
        self.assertFalse(effective_task_enabled(self.data, 'IslandManufacture', 'isolated'))
        service = IslandSuspendService(FakeConfigs(self.data))
        service.toggle('isolated')
        self.assertTrue(self.data['IslandFarm']['Scheduler']['Enable'])
        self.assertFalse(self.data['IslandManufacture']['Scheduler']['Enable'])
        self.assertTrue(effective_task_enabled(self.data, 'IslandManufacture', 'isolated'))

    def test_pause_record_remains_complete_until_atomic_replacement(self):
        from deploy.atomic import replace_tmp
        from module.api.island_suspend import write_state

        write_state('isolated', ['IslandFarm'], 'before')

        def inspect_then_replace(temporary, destination):
            self.assertEqual(read_state('isolated')['suspended'], ['IslandFarm'])
            self.assertEqual(read_state('isolated')['at'], 'before')
            replace_tmp(temporary, destination)

        with patch('deploy.atomic.replace_tmp', side_effect=inspect_then_replace):
            write_state('isolated', ['IslandManufacture'], 'after', ['IslandManufacture'])
        self.assertEqual(read_state('isolated')['suspended'], ['IslandManufacture'])
        self.assertEqual(read_state('isolated')['seasonal_only'], ['IslandManufacture'])

    def test_task_graph_snapshot_and_simulation_use_same_virtual_eligibility(self):
        for name in ('Commission', 'Research', 'IslandFarm'):
            self.data[name]['Scheduler']['Enable'] = False
        context = snapshot(self.data, {}, NOW, 'isolated')
        task = next(t for t in context['tasks'] if t['name'] == 'IslandManufacture')
        self.assertTrue(task['enabled'])
        self.assertEqual(simulation_plan(self.data, context['tasks'], NOW)['nativeTask']['name'], 'IslandManufacture')
        IslandSuspendService(FakeConfigs(self.data)).toggle('isolated')
        paused = snapshot(self.data, {}, NOW, 'isolated')
        self.assertIsNone(simulation_plan(self.data, paused['tasks'], NOW)['nativeTask'])

    def test_overview_includes_virtual_task_but_keeps_single_run_switch_requirement(self):
        from module.api.runtime_service import RuntimeService

        service = RuntimeService.__new__(RuntimeService)
        service.configs = SimpleNamespace(read=lambda _: (self.data, 'revision'), template=self.data)
        with patch('module.runtime.process_manager.ProcessManager._processes', {}), \
                patch('module.api.runtime_service.monthly_statistics_resources', return_value=[]):
            overview = RuntimeService.overview.__wrapped__(service, 'isolated')
            task = next(t for t in overview['tasks'] if t['name'] == 'IslandManufacture')
            self.assertFalse(task['runOnceAllowed'])
            IslandSuspendService(FakeConfigs(self.data)).toggle('isolated')
            paused = RuntimeService.overview.__wrapped__(service, 'isolated')
            self.assertNotIn('IslandManufacture', [t['name'] for t in paused['tasks']])

    def test_card_runtime_consumes_same_queue_and_marks_virtual_candidate_enabled(self):
        from module.scheduler.runtime import SchedulerRuntime

        for name in ('Commission', 'Research', 'IslandFarm'):
            self.data[name]['Scheduler']['Enable'] = False
        config = self.queue()
        config.get_next_task = lambda: AzurLaneConfig.get_next_task(config)
        runtime = SchedulerRuntime.__new__(SchedulerRuntime)
        runtime.script = SimpleNamespace(config=config, config_name='isolated')
        runtime.store = SimpleNamespace(observations=Mock(return_value={}))
        runtime.mode, runtime.requests = 'enhance', []
        with patch('module.scheduler.runtime.now', return_value=NOW):
            context = runtime.context()
        self.assertEqual(context['nativeTask']['name'], 'IslandManufacture')
        self.assertTrue(context['nativeTask']['enabled'])
        self.assertEqual([t['name'] for t in context['tasks']], ['IslandManufacture'])

    def test_real_task_switched_continues_virtual_task_but_yields_for_priority_zero_or_pause(self):
        self.data['Research']['Scheduler']['Enable'] = False
        self.data['IslandFarm']['Scheduler']['Enable'] = False
        self.data['Commission']['Scheduler']['NextRun'] = NOW + timedelta(hours=1)
        config = self.queue()
        config.get_next_task = lambda: AzurLaneConfig.get_next_task(config)
        config.get_next = lambda: AzurLaneConfig.get_next(config)
        config.stop_event, config.load = None, Mock()
        config.task = Function(self.data['IslandManufacture'])
        self.assertFalse(config.task.enable)
        self.assertFalse(AzurLaneConfig.task_switched(config))
        self.data['Commission']['Scheduler']['NextRun'] = NOW - timedelta(minutes=1)
        self.assertTrue(AzurLaneConfig.task_switched(config))
        self.data['Commission']['Scheduler']['NextRun'] = NOW + timedelta(hours=1)
        self.data['IslandSeasonalProduction']['IslandSeasonWinter']['winter_bouquet'] = 0
        self.assertTrue(AzurLaneConfig.task_switched(config))
        self.data['IslandSeasonalProduction']['IslandSeasonWinter']['winter_bouquet'] = 10
        IslandSuspendService(FakeConfigs(self.data)).toggle('isolated')
        self.assertTrue(AzurLaneConfig.task_switched(config))

    def waiting_script(self):
        from alas import AzurLaneAutoScript

        config = AzurLaneConfig.__new__(AzurLaneConfig)
        object.__setattr__(config, 'bound', {})
        config.config_name, config.data = 'isolated', self.data
        config.get_mtime = Mock(return_value=NOW)
        config.get_next_task()
        script = AzurLaneAutoScript.__new__(AzurLaneAutoScript)
        script.config, script.config_name, script.stop_event = config, 'isolated', None
        return script

    def test_real_wait_detects_virtual_only_pause_and_restore_at_next_existing_check(self):
        from alas import AzurLaneAutoScript

        self.data['IslandFarm']['Scheduler']['Enable'] = False
        service = IslandSuspendService(FakeConfigs(self.data))
        script = self.waiting_script()
        with patch('alas.current_time', return_value=NOW), \
                patch('alas.time.sleep', side_effect=lambda _: service.toggle('isolated')) as sleep:
            self.assertFalse(AzurLaneAutoScript.wait_until(script, NOW + timedelta(hours=1)))
            sleep.assert_called_once_with(5)
        self.assertFalse(self.data['IslandManufacture']['Scheduler']['Enable'])
        script.config.get_next_task()
        with patch('alas.current_time', return_value=NOW), \
                patch('alas.time.sleep', side_effect=lambda _: service.toggle('isolated')) as sleep:
            self.assertFalse(AzurLaneAutoScript.wait_until(script, NOW + timedelta(hours=1)))
            sleep.assert_called_once_with(5)
        self.assertFalse(self.data['IslandManufacture']['Scheduler']['Enable'])

    def test_pause_after_selection_before_wait_rejects_cached_task_without_mtime_change(self):
        from alas import AzurLaneAutoScript

        script = self.waiting_script()
        IslandSuspendService(FakeConfigs(self.data)).toggle('isolated')
        with patch('alas.current_time', return_value=NOW), patch('alas.time.sleep') as sleep:
            self.assertFalse(AzurLaneAutoScript.wait_until(script, NOW + timedelta(hours=1)))
        sleep.assert_called_once_with(5)

    def test_unchanged_virtual_eligibility_does_not_interrupt_normal_wait(self):
        from alas import AzurLaneAutoScript

        script = self.waiting_script()
        future = NOW + timedelta(hours=1)
        with patch('alas.current_time', side_effect=[NOW, future + timedelta(seconds=2)]), \
                patch('alas.time.sleep') as sleep:
            self.assertTrue(AzurLaneAutoScript.wait_until(script, future))
        sleep.assert_called_once_with(5)


class SeasonalRoundTests(unittest.TestCase):
    setUp = round_tests.ProductionRoundTests.setUp

    def seasonal(self):
        self.config.data['IslandPlan']['IslandPlan']['Season'] = 'winter'
        self.config.data['IslandSeasonalProduction'] = config_data()['IslandSeasonalProduction']

    def test_virtual_manufacture_joins_shared_round_and_reuses_stock_after_priority_interruptions(self):
        self.seasonal()
        run_production_task(self.owner, 'IslandFarm', self.runner_cls)
        session = self.owner._island_production_round
        self.assertIn('IslandManufacture', session.pending)
        self.assertEqual(self.config.cross_get('IslandManufacture.Scheduler.NextRun'), self.now)
        self.assertFalse(self.config.cross_get('IslandManufacture.Scheduler.Enable'))
        # 委托科研离开再回来只沿用原轮，不再收获或读取仓库。
        for task in ('Commission', 'Research'):
            from module.island.production_round import invalidates_round
            self.assertFalse(invalidates_round(task))
        run_production_task(self.owner, 'IslandManufacture', self.runner_cls)
        self.assertIs(self.owner._island_production_round, session)
        self.warehouse_scan.assert_called_once()

    def test_group_failure_cooldown_includes_disabled_but_effective_manufacture(self):
        self.seasonal()
        self.config.data['IslandManufacture']['Scheduler']['NextRun'] = self.now
        with patch('module.config.config.current_time', return_value=self.now):
            delay_failed_preparation(self.owner, 'IslandFarm')
        self.assertEqual(self.config.cross_get('IslandManufacture.Scheduler.NextRun'),
                         self.now + timedelta(hours=2))
        self.assertFalse(self.config.cross_get('IslandManufacture.Scheduler.Enable'))

    def test_pause_change_invalidates_existing_virtual_pending_round(self):
        self.seasonal()
        self.config.config_name = 'isolated'
        with patch('module.api.island_suspend.read_state', return_value={'suspended': []}) as state, \
                patch('module.island.production_round.invalidate_progress'), \
                patch('module.island.production_round.publish_round_progress'):
            run_production_task(self.owner, 'IslandFarm', self.runner_cls)
            session = self.owner._island_production_round
            self.assertTrue(session.valid(self.config))
            state.return_value = {'suspended': ['IslandManufacture']}
            self.assertFalse(session.valid(self.config))


if __name__ == '__main__':
    unittest.main()
