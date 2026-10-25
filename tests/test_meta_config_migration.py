"""合并 META 任务时不得丢弃用户支援设置或静默改变任务范围。"""

from copy import deepcopy
from datetime import datetime
import json
import tempfile
import unittest

from module.config.config_updater import ConfigUpdater


class MetaConfigMigrationTests(unittest.TestCase):
    def config(self, assist=True, beacon=True):
        return {
            'OpsiAshAssist': {'Scheduler': {'Enable': assist, 'NextRun': '2026-10-26 01:00:00'},
                              'OpsiAshAssist': {'Tier': 1}, 'Equipment': {'Enable': False, 'Config': 'old'}},
            'OpsiAshBeacon': {'Scheduler': {'Enable': beacon, 'NextRun': '2026-10-26 03:00:00'},
                              'Equipment': {'Enable': False, 'Config': 'old'}},
        }

    def test_enable_tier_and_earlier_schedule_preserved_without_mutation(self):
        for enabled in (True, False):
            old = self.config(assist=enabled)
            snapshot = deepcopy(old)
            new = ConfigUpdater().config_update(old)
            self.assertEqual(old, snapshot)
            self.assertNotIn('OpsiAshAssist', new)
            self.assertEqual(new['_legacyMetaAssist'], old['OpsiAshAssist'])
            self.assertEqual(new['OpsiAshBeacon']['OpsiAshAssist'], {'Enable': enabled, 'Tier': 1})
            self.assertEqual(new['OpsiAshBeacon']['Scheduler']['NextRun'],
                             datetime(2026, 10, 26, 1 if enabled else 3))

    def test_reloading_preserves_archive_and_user_changes(self):
        updater = ConfigUpdater()
        new = updater.config_update(self.config())
        archive = deepcopy(new['_legacyMetaAssist'])
        new['OpsiAshBeacon']['OpsiAshAssist'] = {'Enable': False, 'Tier': 15}
        again = updater.config_update(new)
        self.assertEqual(again['_legacyMetaAssist'], archive)
        self.assertEqual(again['OpsiAshBeacon']['OpsiAshAssist'], {'Enable': False, 'Tier': 15})

    def test_explicit_new_settings_win_over_legacy(self):
        old = self.config()
        old['OpsiAshBeacon']['OpsiAshAssist'] = {'Enable': False, 'Tier': 15}
        new = ConfigUpdater().config_update(old)
        self.assertEqual(new['OpsiAshBeacon']['OpsiAshAssist'], {'Enable': False, 'Tier': 15})
        self.assertEqual(new['_legacyMetaAssist'], old['OpsiAshAssist'])

    def test_assist_only_requires_scope_decision(self):
        old = self.config(beacon=False)
        before = deepcopy(old)
        with self.assertRaisesRegex(ValueError, '单独启用'):
            ConfigUpdater().config_update(old)
        self.assertEqual(old, before)

    def test_partial_new_group_cannot_enable_previously_disabled_assist(self):
        for group in ({}, {'Tier': 10}, {'Enable': False}):
            with self.subTest(group=group):
                old = self.config(assist=False)
                old['OpsiAshBeacon']['OpsiAshAssist'] = group
                new = ConfigUpdater().config_update(old)
                self.assertEqual(new['OpsiAshBeacon']['OpsiAshAssist'],
                                 {'Enable': False, 'Tier': group.get('Tier', 1)})

    def test_different_active_equipment_requires_decision(self):
        old = self.config()
        old['OpsiAshAssist']['Equipment']['Enable'] = True
        with self.assertRaisesRegex(ValueError, '装备方案不同'):
            ConfigUpdater().config_update(old)

    def test_disabled_assist_does_not_apply_its_equipment(self):
        old = self.config(assist=False)
        old['OpsiAshAssist']['Equipment'] = {'Enable': True, 'Config': 'different'}
        new = ConfigUpdater().config_update(old)
        self.assertFalse(new['OpsiAshBeacon']['Equipment']['Enable'])
        self.assertEqual(new['_legacyMetaAssist'], old['OpsiAshAssist'])

    def test_template_does_not_copy_private_legacy(self):
        new = ConfigUpdater().config_update(self.config(), is_template=True)
        self.assertNotIn('_legacyMetaAssist', new)

    def test_webui_read_and_save_preserve_disabled_legacy_settings(self):
        from module.api.config_service import ConfigService
        from module.api.protocol import ConfigChange
        from tests.test_api import fixture
        with tempfile.TemporaryDirectory() as directory:
            service = ConfigService(fixture(directory))
            path = service.path('testpilot')
            old = service.read_json(path)
            old.update(self.config(assist=False))
            path.write_text(json.dumps(old), encoding='utf-8')
            original = path.read_bytes()
            values = service.get('testpilot')['values']
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(values['OpsiAshBeacon']['OpsiAshAssist'], {'Enable': False, 'Tier': 1})
            self.assertNotIn('OpsiAshAssist', values)
            service.patch('testpilot', None, [ConfigChange(
                path='OpsiAshBeacon.OpsiAshBeacon.DossierSkipAssist', value=True)])
            saved = service.read_json(path)
            self.assertEqual(saved['_legacyMetaAssist'], old['OpsiAshAssist'])
            self.assertFalse(saved['OpsiAshBeacon']['OpsiAshAssist']['Enable'])

    def test_webui_conflict_cannot_overwrite_original(self):
        from module.api.config_service import ConfigService
        from module.api.protocol import ApiError
        from tests.test_api import fixture
        with tempfile.TemporaryDirectory() as directory:
            service = ConfigService(fixture(directory))
            path = service.path('testpilot')
            old = service.read_json(path)
            old.update(self.config(beacon=False))
            path.write_text(json.dumps(old), encoding='utf-8')
            original = path.read_bytes()
            with self.assertRaisesRegex(ApiError, '单独启用'):
                service.get('testpilot')
            self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
