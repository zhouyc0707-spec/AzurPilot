"""旧统计配置不因合入云接口自动外传，大世界始终只做本地处理。"""
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import numpy as np

from module.config.config_updater import ConfigUpdater
from module.statistics.azurstats import AzurStats
from module.statistics.cloud_upload import enqueue_record, resolver_for, resume_pending, update_policy


class LocalStatisticsPolicyTest(unittest.TestCase):
    def test_direct_entry_points_cannot_bypass_local_policy(self):
        with patch('module.statistics.cloud_upload.queue_for') as queue:
            cases = [(False, 'research'), (True, 'opsi_daily'),
                     (True, 'opsi_hazard1_leveling'), (True, 'opsi_future'),
                     (True, 'campaign'), (True, 'research_refresh')]
            for enabled, scene in cases:
                with self.subTest(enabled=enabled, scene=scene):
                    config = SimpleNamespace(DropRecord_CloudUpload=enabled,
                                             DropRecord_ResearchRecord='upload')
                    self.assertIsNone(enqueue_record(config, scene, []))
                    self.assertIsNone(resolver_for(config, scene))
            missing = SimpleNamespace(DropRecord_ResearchRecord='upload')
            self.assertIsNone(enqueue_record(missing, 'research', []))
            self.assertIsNone(resolver_for(missing, 'research'))
            queue.assert_not_called()

    def test_disabled_or_missing_policy_does_not_start_resume_queue(self):
        with patch('module.statistics.cloud_upload.queue_for') as queue, \
                patch('module.statistics.cloud_upload.get_device_id') as device:
            for enabled in (False, True):
                resume_pending(SimpleNamespace(DropRecord_CloudUpload=enabled,
                                               DropRecord_ResearchRecord='save',
                                               DropRecord_CommissionRecord='do_not'))
            resume_pending(SimpleNamespace(DropRecord_ResearchRecord='upload'))
            queue.assert_not_called()
            device.assert_not_called()

    def test_disabling_existing_queue_keeps_durable_job_without_network(self):
        from module.cloud.service import CloudService
        with tempfile.TemporaryDirectory() as directory:
            api = Mock()
            queue = CloudService(Path(directory) / 'queue', 'a' * 32, api=api)
            queue.config_name = 'policy'
            config = SimpleNamespace(config_name='policy', DropRecord_CloudUpload=True,
                                     DropRecord_ResearchRecord='upload', DropRecord_CommissionRecord='do_not')
            with patch('module.statistics.cloud_upload._queues', {'isolated': queue}):
                update_policy(config)
                with patch.object(queue, 'start'):
                    ident = queue.enqueue({'scene': 'research', 'items': []})
                job = queue.root / 'jobs' / f'{ident}.json'
                original = job.read_bytes()
                config.DropRecord_CloudUpload = False
                update_policy(config)
                self.assertFalse(queue.drain_once())
                self.assertIsNone(queue.enqueue({'scene': 'research', 'items': []}))
                self.assertEqual(api.mock_calls, [])
                self.assertEqual(job.read_bytes(), original)

    def test_missing_cloud_flag_defaults_off_without_rewriting_record_modes(self):
        old = {'Alas': {'DropRecord': {'ResearchRecord': 'upload', 'OpsiDaily': 'upload'}}}
        new = ConfigUpdater().config_update(old)
        self.assertIs(new['Alas']['DropRecord']['CloudUpload'], False)
        self.assertEqual(new['Alas']['DropRecord']['ResearchRecord'], 'upload')
        self.assertEqual(new['Alas']['DropRecord']['OpsiDaily'], 'upload')

    def test_v3_resume_leaves_retired_screenshot_queue_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / 'log' / 'cloud_upload' / 'policy' / 'legacy.pending'
            old.mkdir(parents=True)
            (old / 'record.json').write_text('{"upload_version":2}', encoding='utf-8')
            (old / 'reward.png').write_bytes(b'legacy evidence')
            original = {path.name: path.read_bytes() for path in old.iterdir()}
            config = SimpleNamespace(config_name='policy', DropRecord_CloudUpload=True,
                                     DropRecord_ResearchRecord='upload')
            with patch('module.statistics.cloud_upload.get_database',
                       return_value=SimpleNamespace(directory=root / 'config')), \
                    patch('module.statistics.cloud_upload.get_device_id', return_value='a' * 32), \
                    patch('module.statistics.cloud_upload.queue_for') as queue:
                resume_pending(config)
            queue.assert_not_called()
            self.assertEqual({path.name: path.read_bytes() for path in old.iterdir()}, original)
            self.assertFalse((root / 'config' / 'cloud').exists())

    def test_explicit_cloud_enable_never_uploads_opsi_even_via_direct_commit(self):
        stat = AzurStats(SimpleNamespace(config_name='policy', DropRecord_CloudUpload=True))
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        for genre in ('opsi_daily', 'opsi_meowfficer_farming', 'opsi_hazard1_leveling', 'opsi_future'):
            with self.subTest(genre=genre), patch('module.statistics.azurstats.cleanup_drop_screenshots_if_due'), \
                    patch.object(stat, '_record_local') as local, \
                    patch('module.statistics.cloud_upload.enqueue_record') as cloud:
                record = stat.new(genre, method='upload')
                self.assertFalse(record.upload)
                stat.commit([image], genre, local=True, upload=True)
                local.assert_called_once()
                cloud.assert_not_called()

    def test_research_remains_local_until_explicitly_enabled(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled), patch('module.statistics.cloud_upload.resume_pending'):
                stat = AzurStats(SimpleNamespace(config_name='policy', DropRecord_CloudUpload=enabled,
                                                DropRecord_ResearchRecord='upload'))
                with patch('module.statistics.azurstats.cleanup_drop_screenshots_if_due'):
                    record = stat.new('research', method='upload')
                self.assertTrue(record.analyze)
                self.assertEqual(record.upload, enabled)

    def test_research_selection_save_mode_stays_local(self):
        from module.research.selector import ResearchSelector
        for mode in ('do_not', 'save', 'upload', 'save_and_upload'):
            with self.subTest(mode=mode):
                selector = object.__new__(ResearchSelector)
                selector.config = SimpleNamespace(DropRecord_ResearchRecord=mode)
                selector.device = SimpleNamespace(image=None)
                selector.stat = Mock()
                selector.stat.new.return_value = MagicMock()
                selector.research_list_archive()
                if mode in ('save', 'save_and_upload'):
                    selector.stat.new.assert_called_once_with('research_refresh', method=mode)
                else:
                    selector.stat.new.assert_not_called()
