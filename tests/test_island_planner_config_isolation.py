"""独立工具迁移保留参数，旧自动规划仅恢复其临时启用的工坊。"""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from yaml import safe_load

from module.api.config_service import ConfigService
from module.api.protocol import ApiError, ConfigChange
from module.config.config_updater import ConfigUpdater
from tests.test_api import fixture


class PlannerConfigMigrationTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(Path('config/template.json').read_text('utf-8'))

    def legacy_tool(self, **values):
        self.data['IslandProductionPlanner'].pop('IslandProductionPlanner', None)
        self.data['IslandProductionPlanner']['IslandPlannerTool'] = values

    def legacy_auto(self, **values):
        self.data['IslandPlan']['IslandProductionPlanner'] = values

    def migrated_tool(self):
        result = ConfigUpdater().config_update(self.data)
        self.assertNotIn('IslandProductionPlanner', result['IslandPlan'])
        self.assertNotIn('IslandPlannerTool', result['IslandProductionPlanner'])
        return result['IslandProductionPlanner']['IslandProductionPlanner']

    def test_old_auto_parameters_do_not_become_independent_tool_settings(self):
        self.legacy_auto(Enabled=True, FieldsEfficiency=0.12, DailyProfitLowerLimit=1234,
                         TechnologyStatus='164: true', PlannerTargets='{"2001": 300}',
                         PlanFingerprint='saved-plan', HardFloorItems='{"2001": 80}')
        self.data['IslandPlan']['IslandPlan'].update(Season='winter', TaskAlignment='hour')
        original = copy.deepcopy(self.data)
        tool = self.migrated_tool()
        self.assertEqual(self.data, original)
        self.assertEqual(tool['DailyProfitLowerLimit'], 50000)
        self.assertEqual(tool['FieldsEfficiency'], 0)
        self.assertFalse(tool['ApplySettings'])
        self.assertEqual(tool['Season'], 'auto')
        self.assertEqual(tool['Priority'], 'pt')
        self.assertTrue(tool['FillSparePosts'])
        self.assertTrue(tool['AutoPositions'])
        self.assertEqual(ConfigUpdater().config_update(self.data)['IslandPlan']['IslandPlan'],
                         original['IslandPlan']['IslandPlan'])

    def test_tool_percentage_units_and_user_parameters_are_preserved(self):
        self.legacy_tool(FieldsEfficiency=12, OrchardEfficiency=4, RescanTechnology=False,
                         ApplySettings=True, DailyProfitLowerLimit=76543,
                         ExtraTargets='Apple: 100', CargoTargetDays=3)
        tool = self.migrated_tool()
        for key, value in self.data['IslandProductionPlanner']['IslandPlannerTool'].items():
            self.assertEqual(tool[key], value)
        self.assertIn('yellowfin_tuna', tool['UnlockedFish'])

    def test_explicit_new_fields_win_and_are_not_remapped(self):
        self.legacy_tool(FieldsEfficiency=12, ExtraTargets='4049: 5', CargoTargetDays=3)
        self.data['IslandProductionPlanner']['IslandProductionPlanner'] = {
            'FieldsEfficiency': 4, 'ExtraTargets': '4049: 22', 'Season': 'season_5'}
        tool = self.migrated_tool()
        self.assertEqual(tool['FieldsEfficiency'], 4)
        self.assertEqual(tool['ExtraTargets'], '4049: 22')
        self.assertEqual(tool['CargoTargetDays'], 3)
        self.assertEqual(tool['Season'], 'season_5')

    def test_legacy_winter_ids_migrate_once_without_changing_quantities(self):
        self.legacy_tool(ExtraTargets='2023: 30\n4049: 4\n2000: 12',
                         SeasonTaskTargets='"4051": 8', CargoTargets='冬季花束 (4048): 5\n4046: 7')
        tool = self.migrated_tool()
        self.assertEqual(safe_load(tool['ExtraTargets']), {4047: 30, 4051: 4, 2000: 12})
        self.assertEqual(safe_load(tool['SeasonTaskTargets']), {4053: 8})
        self.assertEqual(safe_load(tool['CargoTargets']), {4056: 5, 4044: 7})
        migrated = ConfigUpdater().config_update(self.data)
        self.assertEqual(ConfigUpdater().config_update(migrated)['IslandProductionPlanner']['IslandProductionPlanner'], tool)

    def test_legacy_winter_flower_names_migrate_without_changing_target_periods(self):
        self.legacy_tool(ExtraTargets='Winter Flower: {count: 12, period: 3}',
                         SeasonTaskTargets='冬季花卉: 18', CargoTargets='Winter Flower (4046): 7')
        tool = self.migrated_tool()
        self.assertEqual(safe_load(tool['ExtraTargets']), {'Daffodils': {'count': 12, 'period': 3}})
        self.assertEqual(safe_load(tool['SeasonTaskTargets']), {'水仙花': 18})
        self.assertEqual(safe_load(tool['CargoTargets']), {4044: 7})
        migrated = ConfigUpdater().config_update(self.data)
        self.assertEqual(ConfigUpdater().config_update(migrated)['IslandProductionPlanner']['IslandProductionPlanner'], tool)

    def test_invalid_target_text_is_preserved_for_tool_validation(self):
        self.legacy_tool(ExtraTargets='[4049: invalid', SeasonTaskTargets='- 4049', CargoTargets=None)
        tool = self.migrated_tool()
        self.assertEqual(tool['ExtraTargets'], '[4049: invalid')
        self.assertEqual(tool['SeasonTaskTargets'], '- 4049')
        self.assertIsNone(tool['CargoTargets'])

    def test_only_auto_owned_factory_is_disabled_and_original_time_restored(self):
        for active, enabled in ((True, True), (True, False), (False, True), (False, False), ('true', True)):
            with self.subTest(active=active, enabled=enabled):
                self.legacy_auto(AutoManufactureActive=active, AutoManufactureNextRun='2026-10-01 03:00:00')
                self.data['IslandManufacture']['Scheduler'].update(Enable=enabled, NextRun='2026-11-07 04:00:00')
                updated = ConfigUpdater().config_update(self.data)
                scheduler = updated['IslandManufacture']['Scheduler']
                self.assertEqual(scheduler['Enable'], False if active is True else enabled)
                expected = '2026-10-01 03:00:00' if active is True else '2026-11-07 04:00:00'
                self.assertEqual(str(scheduler['NextRun']), expected)
                self.assertEqual(ConfigUpdater().config_update(updated)['IslandManufacture']['Scheduler'], scheduler)

    def test_invalid_saved_factory_time_keeps_current_schedule(self):
        for original in ('not-a-date', '', None, 123):
            with self.subTest(original=original):
                self.legacy_auto(AutoManufactureActive=True, AutoManufactureNextRun=original)
                self.data['IslandManufacture']['Scheduler'].update(Enable=True, NextRun='2026-11-07 04:00:00')
                scheduler = ConfigUpdater().config_update(self.data)['IslandManufacture']['Scheduler']
                self.assertFalse(scheduler['Enable'])
                self.assertEqual(str(scheduler['NextRun']), '2026-11-07 04:00:00')

    def test_api_migrates_read_only_and_patch_saves_new_contract(self):
        self.legacy_tool(FieldsEfficiency=12, ExtraTargets='4049: 5')
        self.legacy_auto(AutoManufactureActive=True, AutoManufactureNextRun='2026-10-01 03:00:00',
                         PlanFingerprint='old-report')
        self.data['IslandManufacture']['Scheduler']['Enable'] = True
        for index in range(1, 6):
            self.data['IslandBusiness'][f'IslandBusinessShop{index}'].update(
                PlannedMenu='{"apple_pie": 10}', Grade='gold', Char1='mingshi')
        with tempfile.TemporaryDirectory(prefix='planner-migration-') as directory:
            service = ConfigService(fixture(directory))
            path = service.path('testpilot')
            path.write_text(json.dumps(self.data), encoding='utf-8')
            before = path.read_bytes()
            values = service.get('testpilot')['values']
            self.assertEqual(path.read_bytes(), before)
            self.assertNotIn('IslandProductionPlanner', values['IslandPlan'])
            self.assertNotIn('IslandPlannerTool', values['IslandProductionPlanner'])
            self.assertFalse(values['IslandManufacture']['Scheduler']['Enable'])
            for index in range(1, 6):
                expected = self.data['IslandBusiness'][f'IslandBusinessShop{index}'].copy()
                expected.pop('PlannedMenu')
                self.assertEqual(values['IslandBusiness'][f'IslandBusinessShop{index}'], expected)
            self.assertEqual(safe_load(values['IslandProductionPlanner']['IslandProductionPlanner']['ExtraTargets']), {4051: 5})
            service.patch('testpilot', None, [ConfigChange(
                path=f'IslandProductionPlanner.IslandProductionPlanner.{key}', value=value)
                for key, value in [('Priority', 'coins'), ('Season', 'season_5'), ('UnlockedFish', ['shell'])]])
            saved = json.loads(path.read_text('utf-8'))
            tool = saved['IslandProductionPlanner']['IslandProductionPlanner']
            self.assertEqual(tool['FieldsEfficiency'], 12)
            self.assertEqual(tool['Priority'], 'coins')
            self.assertEqual(tool['Season'], 'season_5')
            self.assertEqual(tool['UnlockedFish'], ['shell'])
            self.assertNotIn('IslandProductionPlanner', saved['IslandPlan'])
            with self.assertRaises(ApiError):
                service.patch('testpilot', None, [ConfigChange(
                    path='IslandPlan.IslandProductionPlanner.Enabled', value=True)])

    def test_all_languages_have_tool_options_and_no_retired_report_group(self):
        for language in ('zh-CN', 'zh-MIAO', 'en-US', 'ja-JP', 'zh-TW'):
            with self.subTest(language=language):
                data = json.loads(Path(f'module/config/i18n/{language}.json').read_text('utf-8'))
                self.assertNotIn('IslandPlannerTool', data)
                self.assertNotIn('IslandPlannerReport', data['Gui'])
                planner = data['IslandProductionPlanner']
                for field in ('Season', 'Priority', 'FillSparePosts', 'AutoPositions', 'UnlockedFish'):
                    self.assertTrue(planner[field]['name'])
                    self.assertNotEqual(planner[field]['name'], f'IslandProductionPlanner.{field}.name')
                for choice in ('auto', 'season_1', 'season_2', 'season_3', 'season_4', 'season_5'):
                    self.assertTrue(planner['Season'][choice])
                self.assertTrue(planner['UnlockedFish']['sea_cucumber'])


if __name__ == '__main__':
    unittest.main()
