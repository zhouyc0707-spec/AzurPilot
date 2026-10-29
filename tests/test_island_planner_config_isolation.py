"""独立规划工具不能改变已有自动规划的比例单位和持久化计划。"""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from module.api.config_service import ConfigService
from module.api.protocol import ConfigChange
from module.config.config_updater import ConfigUpdater
from tests.test_api import fixture


class PlannerConfigIsolationTests(unittest.TestCase):
    def test_old_auto_plan_survives_new_tool_defaults(self):
        data = json.loads(Path('config/template.json').read_text(encoding='utf-8'))
        data.pop('IslandProductionPlanner')
        old = data['IslandPlan']['IslandProductionPlanner']
        old.update(FieldsEfficiency=0.04, OrchardEfficiency=0.12,
                   DailyProfitLowerLimit=1234, TechnologyStatus='164: true',
                   PlannerTargets='{"2001": 300}', PlanFingerprint='saved-plan',
                   AutoManufactureActive=True, HardFloorItems='{"2001": 80}')
        expected = copy.deepcopy(old)
        updated = ConfigUpdater().config_update(data)
        self.assertEqual(updated['IslandPlan']['IslandProductionPlanner'], expected)
        tool = updated['IslandProductionPlanner']['IslandPlannerTool']
        self.assertEqual(tool['DailyProfitLowerLimit'], 50000)
        self.assertEqual(tool['FieldsEfficiency'], 0)
        self.assertFalse(tool['ApplySettings'])
        self.assertTrue(tool['RescanTechnology'])
        self.assertTrue(tool['ScanSeasonTasks'])

    def test_api_persists_separate_efficiency_units_and_settings(self):
        with tempfile.TemporaryDirectory(prefix='planner-isolation-') as directory:
            service = ConfigService(fixture(directory))
            changes = [
                ConfigChange(path='IslandPlan.IslandProductionPlanner.FieldsEfficiency', value=0.04),
                ConfigChange(path='IslandProductionPlanner.IslandPlannerTool.FieldsEfficiency', value=12),
                ConfigChange(path='IslandProductionPlanner.IslandPlannerTool.ExtraTargets', value='Apple: 100'),
            ]
            service.patch('testpilot', None, changes)
            values = service.get('testpilot')['values']
            old = values['IslandPlan']['IslandProductionPlanner']
            tool = values['IslandProductionPlanner']['IslandPlannerTool']
            self.assertEqual(old['FieldsEfficiency'], 0.04)
            self.assertEqual(tool['FieldsEfficiency'], 12)
            self.assertEqual(old['DailyProfitLowerLimit'], 0)
            self.assertEqual(tool['DailyProfitLowerLimit'], 50000)
            self.assertFalse(tool['ApplySettings'])
            self.assertEqual(tool['ExtraTargets'], 'Apple: 100')


if __name__ == '__main__':
    unittest.main()
