"""共享维护计划的逐日请求、持久恢复与只读界面契约。"""
import json
import multiprocessing
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from module import maintenance
from module.config.utils import SERVER_TO_TIMEZONE


TEMP_ROOT = Path('D:/Codex/tests')
TEMP_ROOT.mkdir(parents=True, exist_ok=True)
NOW = datetime(2026, 11, 13, 9, tzinfo=timezone(timedelta(hours=8)))
START = '2026-11-14T09:00:00+08:00'


def _concurrent_refresh(directory, attempts, output):
    """真实子进程共享磁盘锁，模拟HTTP入口只记录一次请求。"""
    def query(server, now):
        with Path(attempts).open('a', encoding='utf-8') as file:
            file.write(server + '\n')
        time.sleep(0.1)
        return START

    with (patch.object(maintenance, '_clock', return_value=NOW),
          patch.object(maintenance, '_query', side_effect=query),
          patch('module.maintenance_schedule.capture_baselines', return_value={}),
          patch('module.maintenance_schedule.reconcile_plan', return_value=True)):
        output.put(maintenance.get_maintenance_snapshot('cn', directory, refresh=True))


class TestMaintenanceCache(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEMP_ROOT, prefix='maintenance-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'config'
        self.now = NOW
        self.clock = patch.object(maintenance, '_clock', side_effect=lambda server: self.now.astimezone(
            timezone(SERVER_TO_TIMEZONE[server]))).start()
        self.addCleanup(patch.stopall)
        self.capture = patch('module.maintenance_schedule.capture_baselines', return_value={'a.json': {
            'Exercise': '2026-11-20 12:00:00'}}).start()
        self.reconcile = patch('module.maintenance_schedule.reconcile_plan', return_value=True).start()
        self.get = patch.object(maintenance.http, 'get').start()
        self.device = patch.object(maintenance, 'get_device_id', return_value='test-device').start()
        self.reply(None)

    def reply(self, start, server='cn'):
        response = Mock()
        response.json.return_value = {'schema_version': 2, 'server': server,
                                      'maintenance': {'starts_at': start} if start else None}
        self.get.return_value = response
        self.get.side_effect = None
        return response

    def refresh(self, server='cn'):
        return maintenance.get_maintenance_snapshot(server, self.directory, refresh=True)

    def path(self, server='cn'):
        return self.directory / 'maintenance' / f'{server}.json'

    def seed(self, **updates):
        data = {'server': 'cn', 'attemptDay': '2026-11-12', 'state': 'announced',
                'startsAt': START, 'checkedAt': '2026-11-12T10:00:00+08:00', 'error': None,
                'hasApplied': True, 'appliedStart': START}
        data.update(updates)
        self.path().parent.mkdir(parents=True, exist_ok=True)
        self.path().write_text(json.dumps(data), encoding='utf-8')
        return data

    def test_pure_read_does_not_create_directories_or_touch_network_or_schedules(self):
        self.assertEqual(maintenance.get_maintenance_snapshot('cn', self.directory), {
            'state': 'pending', 'startsAt': None, 'checkedAt': None, 'error': None})
        self.assertFalse(self.directory.exists())
        self.get.assert_not_called()
        self.capture.assert_not_called()
        self.reconcile.assert_not_called()

    def test_instance_settlement_without_pending_is_pure_read(self):
        self.assertTrue(maintenance.settle_maintenance_instance('cn', 'a', self.directory))
        self.assertFalse(self.directory.exists())
        self.get.assert_not_called()
        self.capture.assert_not_called()
        self.reconcile.assert_not_called()

    def test_instance_settlement_only_reconciles_its_own_pending_baselines(self):
        baselines = {'a.json': {'Exercise': '2026-11-20'}, 'broken.json': {'Hard': '2026-11-20'}}
        self.seed(reconciliation={'id': 'pending', 'snapshot': {'startsAt': START}, 'baselines': baselines})
        before = self.path().read_bytes()
        self.assertTrue(maintenance.settle_maintenance_instance('cn', 'a', self.directory))
        self.reconcile.assert_called_once_with(self.directory.resolve(), 'cn', {'a.json': baselines['a.json']}, run_at=None)
        self.assertEqual(self.path().read_bytes(), before)
        self.get.assert_not_called()
        self.capture.assert_not_called()
        self.reconcile.return_value = False
        self.assertFalse(maintenance.settle_maintenance_instance('cn', 'a', self.directory))

    def test_instance_settlement_reports_corrupt_cache_without_writing(self):
        self.path().parent.mkdir(parents=True)
        self.path().write_text('{bad', encoding='utf-8')
        self.assertFalse(maintenance.settle_maintenance_instance('cn', 'a', self.directory))
        self.assertEqual(self.path().read_text(), '{bad')
        self.get.assert_not_called()
        self.reconcile.assert_not_called()

    def test_success_without_announcement_is_persisted_for_today(self):
        first = self.refresh()
        self.assertEqual(first, self.refresh())
        self.assertEqual(first['state'], 'not_published')
        self.assertIsNone(first['startsAt'])
        self.get.assert_called_once_with(maintenance.MAINTENANCE_API_URL, params={'server': 'cn'},
                                        headers={'X-AzurPilot-ID': 'test-device'}, timeout=10)
        stored = json.loads(self.path().read_text(encoding='utf-8'))
        self.assertEqual(stored['attemptDay'], '2026-11-13')
        self.assertTrue(stored['hasApplied'])
        self.assertIsNone(stored['appliedStart'])
        self.assertNotIn('reconciliation', stored)
        self.reconcile.assert_called_once_with(self.directory.resolve(), 'cn', self.capture.return_value,
                                               run_at=stored['lastReconciliation']['wakeAt'])

    def test_reschedule_receipt_proves_exact_baseline_and_fixed_wake_time(self):
        self.reply(START)
        self.refresh()
        before_bytes = self.path().read_bytes()
        receipt = json.loads(before_bytes)['lastReconciliation']
        before = datetime.fromisoformat(receipt['baselines']['a.json']['Exercise'])
        after = datetime.fromisoformat(receipt['wakeAt'])
        self.assertTrue(maintenance.was_maintenance_rescheduled('cn', 'a', 'Exercise', before, after, self.directory))
        self.assertTrue(maintenance.was_maintenance_rescheduled(
            'cn', 'a', 'Exercise', before.astimezone(timezone.utc), after.astimezone(timezone.utc), self.directory))
        for name, task, original, current in (
                ('a', 'Exercise', before, after + timedelta(seconds=1)),
                ('a', 'Exercise', before + timedelta(seconds=1), after),
                ('a', 'Hard', before, after), ('other', 'Exercise', before, after),
                ('a', 'Exercise', after, after)):
            self.assertFalse(maintenance.was_maintenance_rescheduled(
                'cn', name, task, original, current, self.directory))
        self.assertEqual(self.path().read_bytes(), before_bytes)
        self.get.assert_called_once()

    def test_pending_receipt_keeps_fixed_wake_time_during_retry(self):
        self.reply(START)
        self.reconcile.return_value = False
        self.refresh()
        pending = json.loads(self.path().read_text())['reconciliation']
        before = pending['baselines']['a.json']['Exercise']
        self.now += timedelta(hours=1)
        self.refresh()
        self.assertEqual(json.loads(self.path().read_text())['reconciliation']['wakeAt'], pending['wakeAt'])
        self.assertTrue(maintenance.was_maintenance_rescheduled(
            'cn', 'a', 'Exercise', before, pending['wakeAt'], self.directory))
        self.get.assert_called_once()

    def test_legacy_or_invalid_receipt_cannot_claim_manual_change(self):
        self.seed(reconciliation={'id': 'legacy', 'snapshot': {'startsAt': START},
                                  'baselines': {'a.json': {'Exercise': '2026-11-20'}}})
        self.assertFalse(maintenance.was_maintenance_rescheduled(
            'cn', 'a', 'Exercise', '2026-11-20', NOW, self.directory))
        self.assertFalse(maintenance.was_maintenance_rescheduled('cn', 'a', 'Exercise', None, NOW, self.directory))
        self.seed(lastReconciliation={'wakeAt': '2026-11-13 09:00:00', 'baselines': []})
        self.assertFalse(maintenance.was_maintenance_rescheduled(
            'cn', 'a', 'Exercise', '2026-11-20', NOW, self.directory))

    def test_restart_reads_same_day_claim_without_http(self):
        self.refresh()
        self.get.reset_mock()
        with patch.object(maintenance, '_directory', side_effect=lambda value: Path(str(value)).resolve()):
            self.assertEqual(self.refresh()['state'], 'not_published')
        self.get.assert_not_called()

    def test_next_natural_day_requests_again_but_same_plan_does_not_wake_again(self):
        self.reply(START)
        self.refresh()
        self.now += timedelta(days=1)
        self.refresh()
        self.assertEqual(self.get.call_count, 2)
        self.assertEqual(self.reconcile.call_count, 1)

    def test_server_date_dedupe_is_independent_of_host_date(self):
        self.reply(None, server='en')
        # 国服11月13日09点对应美服11月12日18点。
        self.refresh('en')
        self.assertEqual(json.loads(self.path('en').read_text())['attemptDay'], '2026-11-12')
        self.now += timedelta(hours=5)
        self.refresh('en')
        self.assertEqual(self.get.call_count, 1)
        self.now += timedelta(hours=1)
        self.refresh('en')
        self.assertEqual(self.get.call_count, 2)

    def test_query_failure_is_persisted_until_next_day(self):
        self.get.side_effect = TimeoutError('test timeout')
        first = self.refresh()
        self.assertEqual(first['state'], 'error')
        self.assertIn('TimeoutError', first['error'])
        self.assertIsNone(first['startsAt'])
        self.assertEqual(self.refresh(), first)
        self.get.assert_called_once()
        self.reconcile.assert_called_once()
        self.now += timedelta(days=1)
        self.refresh()
        self.assertEqual(self.get.call_count, 2)
        self.assertEqual(self.reconcile.call_count, 1)

    def test_failure_retains_announced_today_even_after_start(self):
        today = '2026-11-13T08:00:00+08:00'
        self.seed(startsAt=today, appliedStart=today)
        self.get.side_effect = OSError('offline')
        result = self.refresh()
        self.assertEqual(result['state'], 'error')
        self.assertEqual(result['startsAt'], today)
        self.assertEqual(maintenance.get_maintenance_start('cn', self.directory, refresh=False),
                         datetime.fromisoformat(today))

    def test_failure_retains_future_but_removes_old_date(self):
        self.seed()
        self.get.side_effect = OSError('offline')
        self.assertEqual(self.refresh()['startsAt'], START)
        self.now += timedelta(days=2)
        self.assertIsNone(self.refresh()['startsAt'])
        self.assertEqual(self.get.call_count, 2)
        self.assertIsNone(json.loads(self.path().read_text())['startsAt'])

    def test_successful_null_clears_previous_plan(self):
        self.seed()
        result = self.refresh()
        self.assertEqual(result['state'], 'not_published')
        self.assertIsNone(result['startsAt'])
        self.reconcile.assert_called_once()

    def test_rfc3339_normalizes_server_offset(self):
        self.reply('2026-11-14T00:00:00Z')
        self.assertEqual(self.refresh()['startsAt'], '2026-11-14T08:00:00+08:00')

    def test_other_server_or_naive_time_are_not_accepted(self):
        self.reply(START, server='jp')
        self.assertEqual(self.refresh()['state'], 'error')
        self.assertIsNone(self.refresh()['startsAt'])
        self.get.assert_called_once()
        self.now += timedelta(days=1)
        self.reply('2026-11-15T10:00:00')
        result = self.refresh()
        self.assertEqual(result['state'], 'error')
        self.assertIn('维护时间缺少时区', result['error'])

    def test_existing_cache_is_pure_read_and_expires_by_server_day(self):
        self.seed()
        before = self.path().read_bytes()
        self.assertEqual(maintenance.get_maintenance_snapshot('cn', self.directory)['state'], 'pending')
        self.now += timedelta(days=2)
        self.assertIsNone(maintenance.get_maintenance_snapshot('cn', self.directory)['startsAt'])
        self.assertEqual(self.path().read_bytes(), before)
        self.get.assert_not_called()
        self.reconcile.assert_not_called()

    def test_corrupt_cache_reports_error_without_mutating(self):
        self.path().parent.mkdir(parents=True)
        self.path().write_text('{bad json', encoding='utf-8')
        self.assertEqual(self.refresh()['state'], 'error')
        self.assertEqual(self.path().read_text(), '{bad json')
        self.get.assert_not_called()

    def test_interrupted_http_claim_recovers_without_second_attempt(self):
        self.get.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.refresh()
        stored = json.loads(self.path().read_text())
        self.assertEqual(stored['state'], 'pending')
        self.assertEqual(stored['baselines'], self.capture.return_value)
        self.get.side_effect = AssertionError('当天不应再次发起HTTP')
        result = self.refresh()
        self.assertEqual(result['state'], 'error')
        self.assertIn('上次维护查询未完成', result['error'])
        self.get.assert_called_once()
        self.reconcile.assert_called_once()

    def test_unfinished_reconciliation_replays_persisted_baselines_without_http(self):
        self.reply(START)
        self.reconcile.return_value = False
        first = self.refresh()
        pending = json.loads(self.path().read_text())['reconciliation']
        self.assertEqual(pending['snapshot'], first)
        self.assertEqual(pending['baselines'], self.capture.return_value)
        self.capture.return_value = {'changed.json': {'Hard': '2026-11-21'}}
        self.reconcile.return_value = True
        self.refresh()
        self.get.assert_called_once()
        self.reconcile.assert_called_with(self.directory.resolve(), 'cn', pending['baselines'], run_at=pending['wakeAt'])
        self.assertNotIn('reconciliation', json.loads(self.path().read_text()))

    def test_concurrent_refresh_waits_for_reconciliation_before_returning(self):
        self.reply(START)
        captures = threading.Barrier(2)
        first_reconcile = threading.Event()
        second_reconcile = threading.Event()
        release_first = threading.Event()
        guard = threading.Lock()
        calls = [0]

        def capture(directory, server):
            captures.wait(timeout=3)
            return {}

        def reconcile(directory, server, baselines, run_at=None):
            with guard:
                calls[0] += 1
                first = calls[0] == 1
            if first:
                first_reconcile.set()
                self.assertTrue(release_first.wait(3))
            else:
                second_reconcile.set()
            return True

        self.capture.side_effect = capture
        self.reconcile.side_effect = reconcile
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(self.refresh) for _ in range(2)]
            try:
                self.assertTrue(first_reconcile.wait(3))
                self.assertTrue(second_reconcile.wait(3), '并发刷新必须自行完成重排后再返回')
            finally:
                release_first.set()
            for future in futures:
                self.assertEqual(future.result(timeout=3)['startsAt'], START)
        self.get.assert_called_once()
        self.assertNotIn('reconciliation', json.loads(self.path().read_text()))

    def test_same_day_fast_path_applies_journal_published_after_initial_check(self):
        self.seed(attemptDay=NOW.date().isoformat())
        read = maintenance._read
        reads = [0]

        def concurrent_publication(path, server):
            data = read(path, server)
            reads[0] += 1
            if reads[0] == 2:
                data['reconciliation'] = {'id': 'new', 'snapshot': {'startsAt': START},
                                           'baselines': {'a.json': {'Exercise': '2026-11-20'}}}
                maintenance._write(path, data)
            return data

        with patch.object(maintenance, '_read', side_effect=concurrent_publication):
            self.assertEqual(self.refresh()['startsAt'], START)
        self.reconcile.assert_called_once()
        self.get.assert_not_called()
        self.assertNotIn('reconciliation', json.loads(self.path().read_text()))

    def test_reconciliation_checks_new_journal_id_before_returning(self):
        next_start = '2026-11-15T09:00:00+08:00'
        self.seed(attemptDay=NOW.date().isoformat(), reconciliation={
            'id': 'old', 'snapshot': {'startsAt': START}, 'baselines': {'old.json': {}}})
        calls = [0]

        def reconcile(directory, server, baselines, run_at=None):
            calls[0] += 1
            if calls[0] == 1:
                data = json.loads(self.path().read_text())
                data['startsAt'] = next_start
                data['reconciliation'] = {'id': 'new', 'snapshot': {'startsAt': next_start},
                                           'baselines': {'new.json': {}}}
                maintenance._write(self.path(), data)
            return True

        self.reconcile.side_effect = reconcile
        self.assertEqual(self.refresh()['startsAt'], next_start)
        self.assertEqual(self.reconcile.call_count, 2)
        self.reconcile.assert_called_with(self.directory.resolve(), 'cn', {'new.json': {}}, run_at=None)
        data = json.loads(self.path().read_text())
        self.assertEqual(data['appliedStart'], next_start)
        self.assertNotIn('reconciliation', data)
        self.get.assert_not_called()

    def test_unfinished_reconciliation_does_not_block_next_day_query_or_rewake_same_plan(self):
        self.reply(START)
        self.reconcile.return_value = False
        self.refresh()
        old_baselines = self.capture.return_value
        self.capture.return_value = {'a.json': {'Exercise': '2026-11-21 12:00:00'},
                                     'new.json': {'Hard': '2026-11-21 00:00:00'}}
        self.now += timedelta(days=1)
        self.refresh()
        self.assertEqual(self.get.call_count, 2)
        pending = json.loads(self.path().read_text())['reconciliation']
        self.assertEqual(pending['baselines']['a.json'], old_baselines['a.json'])
        self.assertEqual(pending['baselines']['new.json'], self.capture.return_value['new.json'])

    def test_changed_plan_uses_new_baselines_and_preserves_pending_unreadable_instances(self):
        self.reply(START)
        self.capture.return_value = {'a.json': {'Exercise': '2026-11-20 12:00:00'},
                                     'broken.json': {'Hard': '2026-11-20 18:00:00'}}
        self.reconcile.return_value = False
        self.refresh()
        self.capture.return_value = {'a.json': {'Exercise': '2026-11-21 12:00:00'}}
        self.reply('2026-11-15T09:00:00+08:00')
        self.now += timedelta(days=1)
        self.refresh()
        pending = json.loads(self.path().read_text())['reconciliation']
        self.assertEqual(pending['baselines']['a.json'], self.capture.return_value['a.json'])
        self.assertEqual(pending['baselines']['broken.json'], {'Hard': '2026-11-20 18:00:00'})
        self.assertEqual(pending['snapshot']['startsAt'], '2026-11-15T09:00:00+08:00')

    def test_monitor_discovers_configs_and_queries_on_start_and_next_day(self):
        self.directory.mkdir()
        (self.directory / 'template.json').write_text(json.dumps({'Alas': {'Emulator': {'PackageName': 'jp'}}}))
        (self.directory / 'broken.json').write_text('invalid')
        (self.directory / 'account.json').write_text('\ufeff' + json.dumps({
            'Alas': {'Emulator': {'PackageName': 'cn'}}}), encoding='utf-8')
        (self.directory / 'other.json').write_text('{}')
        reached = threading.Event()
        count = [0]

        def response(*args, **kwargs):
            count[0] += 1
            reached.set()
            return Mock(**{'json.return_value': {'schema_version': 2, 'server': 'cn', 'maintenance': None}})

        self.get.side_effect = response
        monitor = maintenance.start_maintenance_monitor(directory=self.directory, interval=0.03)
        try:
            self.assertTrue(reached.wait(3))
            reached.clear()
            self.now += timedelta(days=1)
            self.assertTrue(reached.wait(3))
        finally:
            self.assertTrue(monitor.stop())
        self.assertEqual(count[0], 2)
        self.assertFalse(self.path('jp').exists())
        self.assertFalse(monitor.thread.is_alive())

    def test_server_mapping_follows_existing_package_rules(self):
        for package, expected in [('jp', 'jp'), ('com.YoStarEN.AzurLane', 'en'), ('unknown', 'cn'), (None, 'cn')]:
            self.assertEqual(maintenance.server_for_config({'Alas': {'Emulator': {'PackageName': package}}}), expected)
        self.assertEqual(maintenance.server_for_config({}), 'cn')

    def test_auto_package_uses_detected_fallback_but_explicit_package_wins(self):
        self.assertEqual(maintenance.server_for_config({
            'Alas': {'Emulator': {'PackageName': 'auto'}}}, fallback='en'), 'en')
        self.assertEqual(maintenance.server_for_config({
            'Alas': {'Emulator': {'PackageName': 'com.YoStarJP.AzurLane'}}}, fallback='cn'), 'jp')
        self.assertEqual(maintenance.server_for_config({
            'Alas': {'Emulator': {'PackageName': 'unknown'}}}, fallback='jp'), 'cn')

    def test_server_cannot_escape_cache_directory(self):
        with self.assertRaises(ValueError):
            maintenance.get_maintenance_snapshot('../elsewhere', self.directory, refresh=True)
        self.assertFalse(self.directory.exists())


class TestMaintenanceProcesses(unittest.TestCase):
    def test_real_processes_share_one_daily_attempt(self):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT, prefix='maintenance-process-') as directory:
            context = multiprocessing.get_context('spawn')
            output = context.Queue()
            attempts = str(Path(directory) / 'attempts.txt')
            processes = [context.Process(target=_concurrent_refresh, args=(directory, attempts, output))
                         for _ in range(2)]
            try:
                for process in processes:
                    process.start()
                for process in processes:
                    process.join(timeout=20)
                    self.assertEqual(process.exitcode, 0)
                results = [output.get(timeout=3) for _ in processes]
                self.assertEqual([result['startsAt'] for result in results], [START, START])
                self.assertEqual(Path(attempts).read_text().splitlines(), ['cn'])
            finally:
                for process in processes:
                    if process.is_alive():
                        process.terminate()
                        process.join(timeout=3)
                output.close()
                output.join_thread()


class TestMaintenanceScheduleRecovery(unittest.TestCase):
    def test_instance_settlement_inside_own_transaction_ignores_other_broken_profile(self):
        from module.config.transaction import config_transaction

        with tempfile.TemporaryDirectory(dir=TEMP_ROOT, prefix='maintenance-settle-') as directory:
            directory = Path(directory)
            profile = {'Alas': {'Emulator': {'PackageName': 'cn'}}, 'Exercise': {
                'Scheduler': {'Enable': True, 'NextRun': '2026-11-20 12:00:00'},
                'Exercise': {'MaintenanceAdvance': True}}}
            healthy = directory / 'healthy.json'
            healthy.write_text(json.dumps(profile), encoding='utf-8')
            (directory / 'broken.json').write_text('{invalid', encoding='utf-8')
            maintenance._write(directory / 'maintenance/cn.json', {
                'server': 'cn', 'attemptDay': NOW.date().isoformat(), 'state': 'announced', 'startsAt': START,
                'reconciliation': {'id': 'pending', 'snapshot': {'startsAt': START}, 'baselines': {
                    name: {'Exercise': '2026-11-20 12:00:00'} for name in ('healthy.json', 'broken.json')}}})
            with (patch('module.maintenance_schedule.current_time', return_value=NOW),
                  patch.object(maintenance.http, 'get') as request):
                with config_transaction(healthy):
                    self.assertTrue(maintenance.settle_maintenance_instance('cn', 'healthy', directory))
                    data = json.loads(healthy.read_text())
                    self.assertEqual(datetime.fromisoformat(data['Exercise']['Scheduler']['NextRun']), NOW)
                self.assertIn('reconciliation', json.loads((directory / 'maintenance/cn.json').read_text()))
                request.assert_not_called()

    def test_cross_day_pending_cas_preserves_finished_instance_and_recovers_broken_instance(self):
        for changed_plan in (False, True):
            with self.subTest(changed_plan=changed_plan), tempfile.TemporaryDirectory(
                    dir=TEMP_ROOT, prefix='maintenance-cas-') as directory:
                directory = Path(directory)
                now = [NOW]
                profile = {'Alas': {'Emulator': {'PackageName': 'cn'}}, 'Exercise': {
                    'Scheduler': {'Enable': True, 'NextRun': '2026-11-20 12:00:00'},
                    'Exercise': {'MaintenanceAdvance': True}}}
                healthy = directory / 'healthy.json'
                broken = directory / 'broken.json'
                for path in (healthy, broken):
                    path.write_text(json.dumps(profile), encoding='utf-8')

                def query(server, at):
                    if at.date() == NOW.date():
                        # 请求期间另一实例配置写坏，发布后健康实例仍能独立完成重排。
                        broken.write_text('{invalid', encoding='utf-8')
                        return START
                    broken.write_text(json.dumps(profile), encoding='utf-8')
                    return '2026-11-15T09:00:00+08:00' if changed_plan else START

                with (patch.object(maintenance, '_clock', side_effect=lambda server: now[0]),
                      patch.object(maintenance, '_query', side_effect=query) as request,
                      patch('module.maintenance_schedule.current_time', side_effect=lambda: now[0])):
                    maintenance.get_maintenance_snapshot('cn', directory, refresh=True)
                    first = json.loads(healthy.read_text())['Exercise']['Scheduler']['NextRun']
                    self.assertEqual(datetime.fromisoformat(first), NOW.astimezone().replace(tzinfo=None))
                    self.assertIn('reconciliation', json.loads((directory / 'maintenance/cn.json').read_text()))
                    completed = json.loads(healthy.read_text())
                    completed['Exercise']['Scheduler']['NextRun'] = '2026-11-21 12:00:00'
                    healthy.write_text(json.dumps(completed), encoding='utf-8')
                    now[0] += timedelta(days=1)
                    maintenance.get_maintenance_snapshot('cn', directory, refresh=True)
                    self.assertEqual(request.call_count, 2)
                    cache = json.loads((directory / 'maintenance/cn.json').read_text())
                    wake_at = cache['lastReconciliation']['wakeAt']
                    recovered = json.loads(broken.read_text())['Exercise']['Scheduler']['NextRun']
                    self.assertEqual(recovered, wake_at)
                    self.assertTrue(maintenance.was_maintenance_rescheduled(
                        'cn', 'broken', 'Exercise', '2026-11-20 12:00:00', recovered, directory))
                    latest = json.loads(healthy.read_text())['Exercise']['Scheduler']['NextRun']
                    if changed_plan:
                        self.assertEqual(latest, wake_at)
                        self.assertNotEqual(wake_at, first)
                    else:
                        self.assertEqual(latest, '2026-11-21 12:00:00')
                        self.assertEqual(wake_at, first)
                    self.assertNotIn('reconciliation', json.loads((directory / 'maintenance/cn.json').read_text()))

    def test_manual_next_run_during_http_is_not_attributed_to_receipt(self):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT, prefix='maintenance-manual-') as directory:
            directory = Path(directory)
            before = '2026-11-20 12:00:00'
            manual = '2026-11-22 14:30:00'
            profile = {'Alas': {'Emulator': {'PackageName': 'cn'}}, 'Exercise': {
                'Scheduler': {'Enable': True, 'NextRun': before}, 'Exercise': {'MaintenanceAdvance': True}}}
            path = directory / 'a.json'
            path.write_text(json.dumps(profile), encoding='utf-8')

            def query(server, at):
                profile['Exercise']['Scheduler']['NextRun'] = manual
                path.write_text(json.dumps(profile), encoding='utf-8')
                return START

            with (patch.object(maintenance, '_clock', return_value=NOW),
                  patch.object(maintenance, '_query', side_effect=query),
                  patch('module.maintenance_schedule.current_time', return_value=NOW)):
                maintenance.get_maintenance_snapshot('cn', directory, refresh=True)
            self.assertEqual(json.loads(path.read_text())['Exercise']['Scheduler']['NextRun'], manual)
            self.assertFalse(maintenance.was_maintenance_rescheduled('cn', 'a', 'Exercise', before, manual, directory))


if __name__ == '__main__':
    unittest.main()
