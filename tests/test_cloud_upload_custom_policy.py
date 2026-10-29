"""旧统计配置不因合入云接口自动外传，大世界始终只做本地处理。"""
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from module.config.config_updater import ConfigUpdater
from module.statistics.azurstats import AzurStats


class LocalStatisticsPolicyTest(unittest.TestCase):
    def test_direct_enqueue_cannot_bypass_local_policy(self):
        from module.statistics.cloud_upload import enqueue
        with patch('module.statistics.cloud_upload.UPLOAD_ENABLED', True), \
                patch('module.statistics.cloud_upload.queue_for') as queue:
            for enabled, genre in ((False, 'research'), (True, 'opsi_daily')):
                self.assertIsNone(enqueue(SimpleNamespace(DropRecord_CloudUpload=enabled), [], genre, 0, None))
            queue.assert_not_called()

    def test_upstream_pause_blocks_explicitly_enabled_upload_before_queue_creation(self):
        from module.statistics.cloud_upload import enqueue
        with patch('module.statistics.cloud_upload.UPLOAD_ENABLED', False), \
                patch('module.statistics.cloud_upload.queue_for') as queue:
            self.assertIsNone(enqueue(SimpleNamespace(DropRecord_CloudUpload=True), [], 'research', 0, None))
            queue.assert_not_called()

    def test_upstream_pause_keeps_existing_queue_without_background_send(self):
        from module.statistics.cloud_upload import CloudUploadQueue
        with tempfile.TemporaryDirectory() as directory:
            queue = CloudUploadQueue(Path(directory) / 'queue', enabled=lambda: True)
            upload_id = queue.enqueue([np.zeros((720, 1280, 3), dtype=np.uint8)], {})
            job = queue.root / f'{upload_id}.pending'
            original = {p.name: p.read_bytes() for p in job.iterdir()}
            with patch('module.statistics.cloud_upload.UPLOAD_ENABLED', False), \
                    patch('module.statistics.cloud_upload.requests.Session'), \
                    patch.object(queue, 'drain_once') as send, \
                    patch.object(queue.wake, 'wait', side_effect=lambda _: queue.stop.set()):
                queue._run()
            send.assert_not_called()
            self.assertEqual({p.name: p.read_bytes() for p in job.iterdir()}, original)

    def test_missing_cloud_flag_defaults_off_without_rewriting_record_modes(self):
        old = {'Alas': {'DropRecord': {'ResearchRecord': 'upload', 'OpsiDaily': 'upload'}}}
        new = ConfigUpdater().config_update(old)
        self.assertIs(new['Alas']['DropRecord']['CloudUpload'], False)
        self.assertEqual(new['Alas']['DropRecord']['ResearchRecord'], 'upload')
        self.assertEqual(new['Alas']['DropRecord']['OpsiDaily'], 'upload')

    def test_explicit_cloud_enable_never_uploads_opsi_even_via_direct_commit(self):
        stat = AzurStats(SimpleNamespace(config_name='policy', DropRecord_CloudUpload=True))
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        for genre in ('opsi_daily', 'opsi_meowfficer_farming', 'opsi_hazard1_leveling', 'opsi_future'):
            with self.subTest(genre=genre), patch('module.statistics.azurstats.cleanup_drop_screenshots_if_due'), \
                    patch.object(stat, '_record_local') as local, patch('module.statistics.cloud_upload.enqueue') as cloud:
                record = stat.new(genre, method='upload')
                self.assertFalse(record.upload)
                stat.commit([image], genre, local=True, upload=True)
                local.assert_called_once()
                cloud.assert_not_called()

    def test_research_remains_local_until_explicitly_enabled(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                stat = AzurStats(SimpleNamespace(config_name='policy', DropRecord_CloudUpload=enabled))
                with patch('module.statistics.azurstats.cleanup_drop_screenshots_if_due'):
                    record = stat.new('research', method='upload')
                self.assertTrue(record.analyze)
                self.assertEqual(record.upload, enabled)
