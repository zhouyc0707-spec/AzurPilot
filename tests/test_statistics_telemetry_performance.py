"""遥测节流与网络隔离不能阻塞本地统计，也不能变更计数。"""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.base.api_client import ApiClient
from module.base.async_executor import async_executor, network_executor
from module.statistics.cl1_data_submitter import Cl1DataSubmitter


class TelemetryPerformanceTests(unittest.TestCase):
    def test_collect_only_reads_three_counters(self):
        submitter = object.__new__(Cl1DataSubmitter)
        submitter._instance_name = 'isolated'
        with patch('module.statistics.cl1_data_submitter.cl1_db') as database:
            database.get_cl1_totals.return_value = {'battle_count': 4, 'akashi_encounters': 2, 'akashi_ap': 300}
            self.assertEqual(
                {'month': '2026-11', 'battle_count': 4, 'akashi_encounters': 2, 'akashi_ap': 300},
                submitter.collect_data(2026, 11),
            )
            database.get_cl1_totals.assert_called_once_with('isolated', '2026-11')
            database.get_stats.assert_not_called()

    def test_auto_submit_throttles_before_collecting_and_retries_next_interval(self):
        submitter = object.__new__(Cl1DataSubmitter)
        submitter._last_submit_time = 0
        submitter._submit_interval = 600
        with patch.object(submitter, 'collect_data', return_value={'battle_count': 2}) as collect, \
                patch.object(submitter, 'calculate_metrics', return_value={'battle_count': 2}), \
                patch.object(submitter, 'submit_data', return_value=True) as submit, \
                patch('module.statistics.cl1_data_submitter.time.time', return_value=1000) as clock:
            self.assertTrue(submitter.auto_submit())
            clock.return_value = 1599
            self.assertFalse(submitter.auto_submit())
            self.assertEqual(1, collect.call_count)
            clock.return_value = 1600
            self.assertTrue(submitter.auto_submit())
            self.assertEqual(2, submit.call_count)

    def test_empty_month_does_not_submit(self):
        submitter = object.__new__(Cl1DataSubmitter)
        submitter._last_submit_time = 0
        submitter._submit_interval = 600
        with patch.object(submitter, 'collect_data', return_value={'battle_count': 0}), \
                patch.object(submitter, 'submit_data') as submit:
            self.assertFalse(submitter.auto_submit())
            submit.assert_not_called()

    def test_leveling_round_uses_existing_submission_gate(self):
        from module.os.tasks.hazard_leveling import OpsiHazard1Leveling
        runner = SimpleNamespace(config=SimpleNamespace(DropRecord_TelemetryReport=True, config_name='isolated'))
        submitter = Mock()
        submitter.auto_submit.return_value = False
        with patch('module.statistics.cl1_data_submitter.get_cl1_submitter', return_value=submitter), \
                patch.object(async_executor, 'submit', side_effect=lambda func: func()):
            OpsiHazard1Leveling._cl1_handle_telemetry(runner)
        submitter.auto_submit.assert_called_once_with()
        submitter.collect_data.assert_not_called()
        submitter.submit_data.assert_not_called()

    def test_slow_network_does_not_hold_local_statistics_queue(self):
        entered, release = threading.Event(), threading.Event()
        payload = {'battle_count': 2}

        def slow_request(data, timeout):
            entered.set()
            if not release.wait(3):
                raise AssertionError('测试网络阻塞未释放')
            return data['battle_count']

        with patch.object(ApiClient, '_submit_cl1_data', side_effect=slow_request):
            future = ApiClient.submit_cl1_data(payload)
            try:
                self.assertTrue(entered.wait(2))
                payload['battle_count'] = 999
                self.assertIsNot(async_executor, network_executor)
                self.assertEqual('stored', async_executor.submit(lambda: 'stored').result(timeout=1))
            finally:
                release.set()
            self.assertEqual(2, future.result(timeout=2))


if __name__ == '__main__':
    unittest.main()
