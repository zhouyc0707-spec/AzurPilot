"""单次排程任务的互斥、取消与排程提交回归，全部使用假 worker 和临时配置。"""

import copy
import json
import queue
import tempfile
import threading
import unittest
from contextlib import ExitStack, nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch, mock_open

from pydantic import ValidationError

from module.api.config_service import ConfigService
from module.api.protocol import ApiError
from module.api.router import Router
from module.api.runtime_service import RuntimeService
from module.config.config import AzurLaneConfig, Function, TaskEnd
from module.config.deep import deep_get
from module.runtime.process_manager import ProcessManager
from module.runtime.single_task import (
    execute_once, is_run_once_allowed, run_once, scheduled_tasks,
    single_task_state, stop_once,
)
from module.runtime.worker_events import WorkerResult
from tests.test_api import fixture


class FakeTaskManager:
    """模拟实例工作进程，不创建进程、截图或连接设备。"""

    def __init__(self):
        self.alive = False
        self.started_func = None
        self.run_id = None
        self.current_task = None
        self.stopping = False
        self.start = Mock(side_effect=self._start)
        self.stop = Mock(side_effect=self._stop)
        self.stop_by_user = Mock(return_value=True)

    @property
    def state(self):
        return 1 if self.alive else 2

    def _start(self, func, ev=None):
        self.started_func = func
        self.run_id = 'round-1'
        self.alive = True

    def _stop(self):
        self.alive = False
        return True


class SingleTaskControlTests(unittest.TestCase):
    def setUp(self):
        self.manager = FakeTaskManager()
        self.data = {'Main': {'Scheduler': {'Command': 'Main', 'Enable': True,
                                            'NextRun': '2099-01-01 00:00:00'}},
                     'General': {}}
        self.configs = SimpleNamespace(
            path=Mock(), read=lambda _: (self.data, 'revision'), template=copy.deepcopy(self.data)
        )
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(ProcessManager, 'get_manager', return_value=self.manager))
        self.stack.enter_context(patch.object(ProcessManager, '_processes', {'testpilot': self.manager}))
        self.event = threading.Event()
        self.stack.enter_context(patch('module.runtime.updater.updater', SimpleNamespace(event=self.event)))

    def test_whitelist_excludes_hidden_functions_and_disabled_tasks(self):
        self.assertEqual({'Main'}, scheduled_tasks(self.configs.template))
        self.assertTrue(is_run_once_allowed(self.data, self.configs.template, 'Main'))
        for name in ('Alas', 'General', '__dict__', 'task:Main'):
            with self.subTest(task=name), self.assertRaises(ApiError):
                run_once('testpilot', name, configs=self.configs)
        self.data['Main']['Scheduler']['Enable'] = False
        with self.assertRaises(ApiError):
            run_once('testpilot', 'Main', configs=self.configs)
        self.manager.start.assert_not_called()

    def test_future_task_runs_without_changing_its_schedule_or_starting_scheduler(self):
        original = copy.deepcopy(self.data)
        run_once('testpilot', 'Main', configs=self.configs)
        self.manager.start.assert_called_once_with('task:Main', ev=self.event)
        self.assertEqual(original, self.data)
        self.assertEqual({'name': 'Main', 'runId': 'round-1'}, single_task_state(self.manager))

    def test_running_instance_cannot_start_another_single_task(self):
        for func in ('alas', 'task:Main', 'MeowfficerScore'):
            with self.subTest(func=func):
                self.manager.started_func = func
                self.manager.alive = True
                with self.assertRaises(ApiError) as error:
                    run_once('testpilot', 'Main', configs=self.configs)
                self.assertEqual('INSTANCE_RUNNING', error.exception.code)
        self.manager.start.assert_not_called()

    def test_concurrent_start_requests_spawn_exactly_one_worker(self):
        results = []

        def start():
            try:
                run_once('testpilot', 'Main', configs=self.configs)
                results.append('started')
            except ApiError as error:
                results.append(error.code)

        threads = [threading.Thread(target=start) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
        self.assertCountEqual(['started', 'INSTANCE_RUNNING'], results)
        self.manager.start.assert_called_once()

    def test_stop_is_immediate_and_never_runs_stop_after_actions(self):
        run_once('testpilot', 'Main', configs=self.configs)
        stop_once('testpilot', 'Main', 'round-1', configs=self.configs)
        self.manager.stop.assert_called_once()
        self.manager.stop_by_user.assert_not_called()
        self.assertIsNone(single_task_state(self.manager))

    def test_stale_or_wrong_target_stop_does_not_kill_new_worker(self):
        run_once('testpilot', 'Main', configs=self.configs)
        for task, run_id in (('Main', 'old-round'), ('Research', 'round-1')):
            with self.subTest(task=task, run_id=run_id), self.assertRaises(ApiError) as error:
                stop_once('testpilot', task, run_id, configs=self.configs)
            self.assertEqual('TASK_RUN_CHANGED', error.exception.code)
        self.manager.started_func = 'alas'
        with self.assertRaises(ApiError):
            stop_once('testpilot', 'Main', 'round-1', configs=self.configs)
        self.manager.stop.assert_not_called()

    def test_stop_after_same_run_naturally_finishes_is_idempotent(self):
        run_once('testpilot', 'Main', configs=self.configs)
        self.manager.alive = False
        stop_once('testpilot', 'Main', 'round-1', configs=self.configs)
        self.manager.stop.assert_called_once()

    def test_failed_process_stop_is_reported_without_claiming_stopped(self):
        run_once('testpilot', 'Main', configs=self.configs)
        self.manager.stop.side_effect = None
        self.manager.stop.return_value = False
        with self.assertRaises(ApiError) as error:
            stop_once('testpilot', 'Main', 'round-1', configs=self.configs)
        self.assertEqual('STOP_FAILED', error.exception.code)
        self.assertIsNotNone(single_task_state(self.manager))

    def test_overview_shows_target_before_first_worker_task_event(self):
        runtime = RuntimeService(self.configs)
        run_once('testpilot', 'Main', configs=self.configs)
        with patch('module.api.runtime_service.monthly_statistics_resources', return_value=[]):
            overview = runtime.overview('testpilot')
        self.assertEqual('running', overview['status'])
        self.assertFalse(overview['schedulerRunning'])
        self.assertEqual({'name': 'Main', 'runId': 'round-1'}, overview['singleTask'])
        self.assertEqual('running', overview['tasks'][0]['state'])
        self.assertTrue(overview['tasks'][0]['runOnceAllowed'])
        self.assertFalse(overview['tasks'][0]['pending'])

    def test_overview_recognizes_bridge_schedulers_and_keeps_tools_separate(self):
        runtime = RuntimeService(self.configs)
        self.manager.alive = True
        with patch('module.api.runtime_service.monthly_statistics_resources', return_value=[]):
            for func, scheduler in (('alas', True), ('maa', True), ('fpy', True), ('StorageStatistics', False)):
                with self.subTest(func=func):
                    self.manager.started_func = func
                    overview = runtime.overview('testpilot')
                    self.assertEqual(scheduler, overview['schedulerRunning'])
                    self.assertIsNone(overview['singleTask'])

    def test_global_stop_uses_direct_stop_for_single_task_and_keeps_scheduler_behavior(self):
        runtime = RuntimeService(self.configs)
        with patch.object(runtime, '_record_running_now'), patch.object(runtime, 'overview'):
            self.manager.started_func = 'task:Main'
            runtime.stop('testpilot')
            self.manager.stop.assert_called_once()
            self.manager.stop_by_user.assert_not_called()
            self.manager.started_func = 'alas'
            runtime.stop('testpilot')
            self.manager.stop_by_user.assert_called_once_with(soft=True)

    def test_api_separates_tool_run_and_validates_stop_run_id(self):
        runtime = SimpleNamespace(run_once=Mock(), stop_once=Mock(), start=Mock())
        router = Router(self.configs, runtime)
        router.dispatch('tasks.runOnce', {'instance': 'testpilot', 'task': 'Main'})
        runtime.run_once.assert_called_once_with('testpilot', 'Main')
        router.dispatch('tasks.stop', {'instance': 'testpilot', 'task': 'Main', 'runId': 'round-1'})
        runtime.stop_once.assert_called_once_with('testpilot', 'Main', 'round-1')
        for run_id in ('', None, 1):
            with self.subTest(run_id=run_id), self.assertRaises(ValidationError):
                router.dispatch('tasks.stop', {'instance': 'testpilot', 'task': 'Main', 'runId': run_id})
        with self.assertRaises(ValidationError):
            router.dispatch('tasks.stop', {'instance': 'testpilot', 'task': 'Main'})
        router.dispatch('tasks.run', {'instance': 'testpilot', 'task': 'StorageStatistics'})
        runtime.start.assert_called_once_with('testpilot', 'StorageStatistics')

    def test_startup_memory_excludes_single_task_but_keeps_other_running_instances(self):
        runtime = RuntimeService(self.configs)
        once = SimpleNamespace(config_name='once', started_func='task:Main')
        scheduler = SimpleNamespace(config_name='scheduler', started_func='alas')
        tool = SimpleNamespace(config_name='tool', started_func='StorageStatistics')
        with patch.object(ProcessManager, 'running_instances', return_value=[once, scheduler, tool]), \
                patch('module.runtime.startup_memory.record_running') as record:
            runtime._record_running_now()
        self.assertEqual(['scheduler', 'tool'], list(record.call_args.args[0]))


class SingleTaskScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.configs = ConfigService(fixture(self.temp.name))
        self.path = self.configs.path('testpilot')
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('module.config.config.filepath_config', return_value=str(self.path)))
        self.stack.enter_context(patch('module.config.config_updater.filepath_config', return_value=str(self.path)))
        self.worker = AzurLaneConfig.__new__(AzurLaneConfig)
        self.worker.config_name = 'testpilot'
        self.worker.bound = {}
        self.worker.modified = {}
        self.worker.auto_update = True
        self.worker.overridden = {}
        self.worker.data = self.worker.read_file('testpilot')
        self.worker.data['Main']['Scheduler']['Enable'] = True
        self.worker.write_file('testpilot', data=self.worker.data)
        self.worker._loaded_data = copy.deepcopy(self.worker.data)
        self.worker.task = Function(self.worker.data['Main'])
        self.worker.bind('Main')
        self.worker.begin_single_run()
        self.original = self.worker.cross_get('Main.Scheduler.NextRun')
        self.delayed = datetime(2026, 10, 10, 12)

    def saved(self):
        return self.worker.read_file('testpilot')

    def test_task_delay_is_visible_in_memory_and_only_success_commits_it(self):
        self.worker.task_delay(target=self.delayed)
        self.assertEqual(self.original, deep_get(self.saved(), 'Main.Scheduler.NextRun'))
        self.assertEqual(self.delayed, self.worker.cross_get('Main.Scheduler.NextRun'))
        self.assertEqual(self.delayed, self.worker.Scheduler_NextRun)
        self.worker.load()
        self.assertEqual(self.delayed, self.worker.cross_get('Main.Scheduler.NextRun'))
        self.worker.finish_single_run(True)
        self.assertEqual(self.delayed, deep_get(self.saved(), 'Main.Scheduler.NextRun'))

    def test_cancel_after_delay_preserves_original_schedule_and_other_task_changes(self):
        with self.worker.multi_set():
            self.worker.task_delay(target=self.delayed)
            self.worker.cross_set('Main.Campaign.Name', '1-2')
            self.worker.task_call('Reward')
        self.worker.finish_single_run(False)
        data = self.saved()
        self.assertEqual(self.original, deep_get(data, 'Main.Scheduler.NextRun'))
        self.assertEqual('1-2', deep_get(data, 'Main.Campaign.Name'))
        self.assertTrue(deep_get(data, 'Reward.Scheduler.Enable'))
        self.assertNotEqual(self.original, deep_get(data, 'Reward.Scheduler.NextRun'))

    def test_multi_set_keeps_deferred_schedule_in_memory_after_other_fields_save(self):
        with self.worker.multi_set():
            self.worker.Scheduler_NextRun = self.delayed
            self.worker.cross_set('Main.Campaign.Name', '1-2')
        self.assertEqual(self.delayed, self.worker.Scheduler_NextRun)
        self.assertEqual(self.delayed, self.worker.cross_get('Main.Scheduler.NextRun'))
        self.assertEqual(self.original, deep_get(self.saved(), 'Main.Scheduler.NextRun'))
        self.worker.finish_single_run(True)
        self.assertEqual(self.delayed, deep_get(self.saved(), 'Main.Scheduler.NextRun'))

    def test_success_does_not_override_user_schedule_edit_during_run(self):
        self.worker.task_delay(target=self.delayed)
        data = json.loads(self.path.read_text(encoding='utf-8'))
        user_schedule = '2026-10-11 08:00:00'
        data['Main']['Scheduler']['NextRun'] = user_schedule
        self.path.write_text(json.dumps(data), encoding='utf-8')
        # 即使任务后来保存了别的字段或重读配置，原排程的并发校验仍然有效。
        self.worker.cross_set('Main.Campaign.Name', '1-2')
        self.worker.load()
        self.worker.finish_single_run(True)
        self.assertEqual(datetime.fromisoformat(user_schedule), deep_get(self.saved(), 'Main.Scheduler.NextRun'))
        self.assertEqual('1-2', deep_get(self.saved(), 'Main.Campaign.Name'))

    def test_single_task_does_not_yield_to_queue_but_still_respects_stop_event(self):
        self.worker.stop_event = threading.Event()
        with patch.object(self.worker, 'get_next', side_effect=AssertionError('不能选择另一任务')):
            self.assertFalse(self.worker.task_switched())
            self.worker.stop_event.set()
            self.assertTrue(self.worker.task_switched())
            self.worker._disable_task_switch = True
            with self.assertRaises(TaskEnd):
                self.worker.check_task_switch()

    def test_task_self_disable_is_deferred_and_cancel_keeps_task_in_original_queue(self):
        with self.worker.multi_set():
            self.worker.Scheduler_Enable = False
            self.worker.task_delay(target=self.delayed)
            self.worker.cross_set('Main.Campaign.Name', '1-2')
        self.assertTrue(deep_get(self.saved(), 'Main.Scheduler.Enable'))
        self.assertFalse(self.worker.Scheduler_Enable)
        self.assertFalse(self.worker.cross_get('Main.Scheduler.Enable'))
        self.worker.load()
        self.assertFalse(self.worker.cross_get('Main.Scheduler.Enable'))
        self.worker.finish_single_run(False)
        self.assertTrue(deep_get(self.saved(), 'Main.Scheduler.Enable'))
        self.assertEqual(self.original, deep_get(self.saved(), 'Main.Scheduler.NextRun'))
        self.assertEqual('1-2', deep_get(self.saved(), 'Main.Campaign.Name'))

    def test_success_commits_task_self_disable_and_schedule_in_same_transaction(self):
        with self.worker.multi_set():
            self.worker.Scheduler_Enable = False
            self.worker.task_delay(target=self.delayed)
        self.worker.finish_single_run(True)
        self.assertFalse(deep_get(self.saved(), 'Main.Scheduler.Enable'))
        self.assertEqual(self.delayed, deep_get(self.saved(), 'Main.Scheduler.NextRun'))

    def test_user_disable_is_preserved_while_successful_next_run_still_commits(self):
        with self.worker.multi_set():
            self.worker.Scheduler_Enable = True
            self.worker.task_delay(target=self.delayed)
        data = json.loads(self.path.read_text(encoding='utf-8'))
        data['Main']['Scheduler']['Enable'] = False
        self.path.write_text(json.dumps(data), encoding='utf-8')
        self.worker.finish_single_run(True)
        self.assertFalse(deep_get(self.saved(), 'Main.Scheduler.Enable'))
        self.assertEqual(self.delayed, deep_get(self.saved(), 'Main.Scheduler.NextRun'))

    def test_user_next_run_is_preserved_while_successful_disable_still_commits(self):
        with self.worker.multi_set():
            self.worker.Scheduler_Enable = False
            self.worker.task_delay(target=self.delayed)
        data = json.loads(self.path.read_text(encoding='utf-8'))
        data['Main']['Scheduler']['NextRun'] = '2026-10-11 08:00:00'
        self.path.write_text(json.dumps(data), encoding='utf-8')
        self.worker.finish_single_run(True)
        self.assertFalse(deep_get(self.saved(), 'Main.Scheduler.Enable'))
        self.assertEqual(datetime(2026, 10, 11, 8), deep_get(self.saved(), 'Main.Scheduler.NextRun'))

    def test_real_run_boundary_accepts_task_end_and_commits_successful_delay(self):
        from alas import AzurLaneAutoScript
        script = AzurLaneAutoScript.__new__(AzurLaneAutoScript)
        script.config_name = 'testpilot'
        script.device = Mock()
        script._channel_float_done = True
        self.worker.data['Main']['Scheduler']['Enable'] = True

        def main():
            script.config.task_delay(target=self.delayed)
            script.config.task_stop()

        script.main = main
        with patch('module.config.config.AzurLaneConfig', return_value=self.worker), \
                patch('alas.AzurLaneAutoScript', return_value=script), \
                patch('module.api.config_service.ConfigService', return_value=self.configs), \
                patch('module.runtime.preview.set_task'), \
                patch('module.statistics.resource_flow.task_session', return_value=nullcontext()):
            self.assertTrue(execute_once('testpilot', 'Main'))
        self.assertEqual(self.delayed, deep_get(self.saved(), 'Main.Scheduler.NextRun'))

    def test_maintenance_reconcile_precedes_single_baseline_and_manual_edits_still_win(self):
        from module import maintenance
        from deploy.atomic import atomic_write

        self.worker.finish_single_run(False)
        now = datetime(2026, 11, 13, 8)
        target = datetime(2026, 11, 14)
        manual = '2026-11-16 07:30:00'
        for task in ('Exercise', 'Hard'):
            for success, edit in ((True, 'none'), (False, 'none'), (True, 'query'), (False, 'query'),
                                  (True, 'run'), (False, 'run')):
                with self.subTest(task=task, success=success, manual_edit=edit), ExitStack() as stack:
                    directory = Path(self.path).parent
                    cache = directory / 'maintenance' / 'jp.json'
                    if cache.exists():
                        cache.unlink()
                    data = json.loads(self.path.read_text(encoding='utf-8'))
                    data['Alas']['Emulator']['PackageName'] = 'com.YoStarJP.AzurLane'
                    data[task]['Scheduler'].update(Enable=True, NextRun='2026-11-13 18:00:00')
                    data['Exercise']['Exercise']['MaintenanceAdvance'] = True
                    data['Hard']['Hard']['RunAt18'] = True
                    self.path.write_text(json.dumps(data), encoding='utf-8')
                    for name in ('module.config.config.current_time', 'module.config.utils.current_time',
                                 'module.maintenance_schedule.current_time'):
                        stack.enter_context(patch(name, return_value=now))
                    stack.enter_context(patch('module.config.server.server', 'cn'))
                    stack.enter_context(patch('module.config.utils.filepath_config', return_value=str(self.path)))
                    stack.enter_context(patch.object(maintenance, '_clock', return_value=now.replace(
                        tzinfo=timezone(timedelta(hours=8))).astimezone(timezone(timedelta(hours=9)))))
                    def edit_next_run():
                        current = json.loads(self.path.read_text(encoding='utf-8'))
                        current[task]['Scheduler']['NextRun'] = manual
                        self.path.write_text(json.dumps(current), encoding='utf-8')

                    def query_maintenance(*args):
                        if edit == 'query':
                            edit_next_run()
                        return '2026-11-13T10:00:00+09:00'

                    query = stack.enter_context(patch.object(maintenance, '_query', side_effect=query_maintenance))
                    worker = AzurLaneConfig('testpilot', task=task)
                    script = Mock()
                    writes = []

                    def fail_first_reconcile_write(path, text):
                        writes.append(path)
                        if len(writes) == 1:
                            raise OSError('模拟首次重排写入暂时失败')
                        atomic_write(path, text)

                    stack.enter_context(patch('module.maintenance_schedule.atomic_write',
                                              side_effect=fail_first_reconcile_write))

                    def complete():
                        baseline = datetime(2026, 11, 13, 18) if edit == 'query' else now
                        self.assertEqual(worker._single_run_schedule_baseline[f'{task}.Scheduler.NextRun'], baseline)
                        # 消费者入口再次刷新仍复用同日结果，完成后延后至下一轮。
                        maintenance.get_maintenance_snapshot('jp', directory, refresh=True)
                        worker.update()
                        if edit == 'run':
                            edit_next_run()
                        worker.task_delay(target=target)
                        return True

                    script.run.side_effect = lambda function: complete()
                    event = threading.Event()
                    if not success:
                        event.set()
                    stack.enter_context(patch('module.config.config.AzurLaneConfig', return_value=worker))
                    stack.enter_context(patch('alas.AzurLaneAutoScript', return_value=script))
                    stack.enter_context(patch('module.api.config_service.ConfigService', return_value=self.configs))
                    self.assertTrue(execute_once('testpilot', task, event))
                    if edit != 'query':
                        self.assertGreaterEqual(len(writes), 2)
                    query.assert_called_once()
                    self.assertEqual(query.call_args.args[0], 'jp')
                    expected = manual if edit != 'none' else (target if success else now).isoformat(sep=' ')
                    saved = json.loads(self.path.read_text(encoding='utf-8'))
                    self.assertEqual(saved[task]['Scheduler']['NextRun'], expected)

    def test_consumer_preserves_manual_next_run_during_query_and_accepts_automatic_receipt(self):
        from module import maintenance
        from module.exercise.exercise import Exercise
        from module.hard.hard import CampaignHard

        self.worker.finish_single_run(False)
        now = datetime(2026, 11, 13, 8)
        manual = '2026-11-16 07:30:00'
        for task, consumer_type in (('Exercise', Exercise), ('Hard', CampaignHard)):
            for edited, single in ((False, False), (True, False), (True, True)):
                with self.subTest(task=task, manual_edit=edited, single=single), ExitStack() as stack:
                    directory = Path(self.path).parent
                    cache = directory / 'maintenance' / 'jp.json'
                    if cache.exists():
                        cache.unlink()
                    data = json.loads(self.path.read_text(encoding='utf-8'))
                    data['Alas']['Emulator']['PackageName'] = 'com.YoStarJP.AzurLane'
                    data[task]['Scheduler'].update(
                        Enable=True, NextRun='2026-11-13 00:00:00' if edited else '2026-11-13 18:00:00')
                    data['Exercise']['Exercise']['MaintenanceAdvance'] = True
                    data['Hard']['Hard']['RunAt18'] = True
                    self.path.write_text(json.dumps(data), encoding='utf-8')
                    for name in ('module.config.config.current_time', 'module.config.utils.current_time',
                                 'module.maintenance_schedule.current_time', 'module.exercise.exercise.current_time',
                                 'module.hard.hard.current_time'):
                        stack.enter_context(patch(name, return_value=now))
                    for name in ('module.exercise.exercise.filepath_config', 'module.hard.hard.filepath_config'):
                        stack.enter_context(patch(name, return_value=str(self.path)))
                    stack.enter_context(patch('module.config.server.server', 'cn'))
                    stack.enter_context(patch('module.hard.hard.server_time_offset', return_value=timedelta()))
                    stack.enter_context(patch.object(maintenance, '_clock', return_value=now.replace(
                        tzinfo=timezone(timedelta(hours=8))).astimezone(timezone(timedelta(hours=9)))))

                    def query(*args):
                        if edited:
                            current = json.loads(self.path.read_text(encoding='utf-8'))
                            current[task]['Scheduler']['NextRun'] = manual
                            self.path.write_text(json.dumps(current), encoding='utf-8')
                        return '2026-11-13T10:00:00+09:00'

                    stack.enter_context(patch.object(maintenance, '_query', side_effect=query))
                    worker = AzurLaneConfig('testpilot', task=task)
                    if single:
                        worker.begin_single_run()
                    consumer = object.__new__(consumer_type)
                    consumer.config = worker
                    consumer.device = Mock()
                    consumer.ui_ensure = Mock(side_effect=RuntimeError('已通过门禁进入页面'))
                    consumer.load_campaign = Mock()
                    continues = not edited or single
                    if task == 'Exercise' and continues:
                        with self.assertRaisesRegex(RuntimeError, '已通过门禁进入页面'):
                            consumer.run()
                        consumer.ui_ensure.assert_called_once()
                    else:
                        with self.assertRaises(TaskEnd):
                            consumer.run()
                        consumer.ui_ensure.assert_not_called()
                    if task == 'Hard' and continues:
                        self.assertTrue(hasattr(consumer, '_hard_next_run'))
                    consumer.device.screenshot.assert_not_called()
                    consumer.load_campaign.assert_not_called()
                    saved = json.loads(self.path.read_text(encoding='utf-8'))
                    expected = manual if edited else ('2026-11-13 17:00:00' if task == 'Hard' else '2026-11-13 08:00:00')
                    self.assertEqual(saved[task]['Scheduler']['NextRun'], expected)
                    if single:
                        worker.finish_single_run(False)


class SingleTaskWorkerTests(unittest.TestCase):
    def test_maintenance_tasks_refresh_and_reload_before_single_run_baseline(self):
        for task in ('Exercise', 'Hard'):
            with self.subTest(task=task):
                data = {'Alas': {'Emulator': {'PackageName': 'com.YoStarJP.AzurLane'}},
                        task: {'Scheduler': {'Enable': True, 'Command': task}}}
                config = Mock(data=data, SERVER='cn')
                script = Mock()
                script.run.return_value = True
                calls = Mock()
                calls.attach_mock(config.update, 'reload')
                calls.attach_mock(config.begin_single_run, 'begin')
                calls.attach_mock(script.run, 'run')
                with patch('module.config.config.AzurLaneConfig', return_value=config), \
                        patch('alas.AzurLaneAutoScript', return_value=script), \
                        patch('module.api.config_service.ConfigService', return_value=SimpleNamespace(template=data)), \
                        patch('module.maintenance.settle_maintenance_instance', return_value=True) as settle, \
                        patch('module.maintenance.get_maintenance_snapshot', return_value={}) as refresh:
                    calls.attach_mock(refresh, 'refresh')
                    calls.attach_mock(settle, 'settle')
                    self.assertTrue(execute_once('testpilot', task))
                self.assertEqual([item[0] for item in calls.mock_calls], ['refresh', 'settle', 'reload', 'begin', 'run'])
                self.assertEqual(refresh.call_args.args[0], 'jp')
                self.assertTrue(refresh.call_args.kwargs['refresh'])

    def test_current_maintenance_reconcile_failure_does_not_begin_single_task(self):
        data = {'Hard': {'Scheduler': {'Enable': True, 'Command': 'Hard'}}}
        config = Mock(data=data, SERVER='cn')
        with patch('module.config.config.AzurLaneConfig', return_value=config), \
                patch('alas.AzurLaneAutoScript') as script, \
                patch('module.api.config_service.ConfigService', return_value=SimpleNamespace(template=data)), \
                patch('module.maintenance.get_maintenance_snapshot', return_value={}), \
                patch('module.maintenance.settle_maintenance_instance', return_value=False), \
                self.assertRaisesRegex(RuntimeError, '当前实例的维护排程尚未同步'):
            execute_once('testpilot', 'Hard')
        config.begin_single_run.assert_not_called()
        script.assert_not_called()

    def test_disabling_maintenance_task_during_preheat_prevents_single_task_start(self):
        data = {'Hard': {'Scheduler': {'Enable': True, 'Command': 'Hard'}}}
        config = Mock(data=data, SERVER='cn')
        config.update.side_effect = lambda: data['Hard']['Scheduler'].update(Enable=False)
        with patch('module.config.config.AzurLaneConfig', return_value=config), \
                patch('alas.AzurLaneAutoScript') as script, \
                patch('module.api.config_service.ConfigService', return_value=SimpleNamespace(template=data)), \
                patch('module.maintenance.get_maintenance_snapshot', return_value={}), \
                patch('module.maintenance.settle_maintenance_instance', return_value=True), \
                self.assertRaises(ApiError):
            execute_once('testpilot', 'Hard')
        config.begin_single_run.assert_not_called()
        script.assert_not_called()

    def test_worker_binds_target_runs_once_and_commits_only_success(self):
        for result, stopped, success in ((True, False, True), (False, False, False),
                                         ('recoverable', False, False), (True, True, False)):
            with self.subTest(result=result, stopped=stopped):
                config = Mock(data={'Main': {'Scheduler': {'Enable': True, 'Command': 'Main'}}})
                script = Mock()
                script.run.return_value = result
                event = threading.Event()
                if stopped:
                    event.set()
                with patch('module.config.config.AzurLaneConfig', return_value=config) as config_class, \
                        patch('alas.AzurLaneAutoScript', return_value=script), \
                        patch('module.api.config_service.ConfigService', return_value=SimpleNamespace(template=config.data)):
                    self.assertEqual(result, execute_once('testpilot', 'Main', event))
                config_class.assert_called_once_with(config_name='testpilot', task='Main')
                self.assertIs(config, script.config)
                script.run.assert_called_once_with('main')
                script.loop.assert_not_called()
                config.begin_single_run.assert_called_once()
                config.finish_single_run.assert_called_once_with(success)

    def test_worker_exception_discards_target_delay(self):
        config = Mock(data={'Main': {'Scheduler': {'Enable': True, 'Command': 'Main'}}})
        script = Mock()
        script.run.side_effect = RuntimeError('合成任务失败')
        with patch('module.config.config.AzurLaneConfig', return_value=config), \
                patch('alas.AzurLaneAutoScript', return_value=script), \
                patch('module.api.config_service.ConfigService', return_value=SimpleNamespace(template=config.data)), \
                self.assertRaisesRegex(RuntimeError, '合成任务失败'):
            execute_once('testpilot', 'Main')
        config.finish_single_run.assert_called_once_with(False)

    def test_process_entry_routes_single_task_without_entering_scheduler_or_tool_path(self):
        with patch('module.runtime.process_manager.set_file_logger'), \
                patch('module.runtime.process_manager.set_func_logger'), \
                patch('module.runtime.process_manager.prepare_statistics'), \
                patch('module.runtime.single_task.execute_once', return_value=True) as execute, \
                patch('module.runtime.process_manager.get_available_func', side_effect=AssertionError('不是工具路径')):
            result = ProcessManager._run_process('testpilot', 'task:Main', queue.Queue(), None, None, 'round-1')
        self.assertEqual(WorkerResult.FINISHED, result)
        execute.assert_called_once_with('testpilot', 'Main', None)

    def test_update_waits_for_single_task_but_does_not_put_it_in_reload_list(self):
        from module.runtime.updater import Updater
        once = SimpleNamespace(config_name='once', started_func='task:Main')
        scheduler = SimpleNamespace(config_name='scheduler', started_func='alas')
        updater = object.__new__(Updater)
        with patch.object(ProcessManager, 'running_instances', return_value=[once, scheduler]), \
                patch.object(updater, '_wait_update', return_value=True) as wait:
            self.assertTrue(updater._start_update())
        wait.assert_called_once_with([once, scheduler], ['scheduler\n'])

    def test_update_cancel_never_restores_single_task_as_scheduler(self):
        once = FakeTaskManager()
        once.config_name = 'once'
        once.started_func = 'task:Main'
        scheduler = FakeTaskManager()
        scheduler.config_name = 'scheduler'
        scheduler.started_func = 'alas'
        with patch('module.runtime.process_manager.list_mod_instance'), \
                patch('module.runtime.process_manager.open', mock_open(read_data=''), create=True), \
                patch('module.runtime.process_manager.os.remove'), \
                patch('module.runtime.process_manager.get_config_mod', return_value='alas'), \
                patch.object(ProcessManager, 'get_manager', side_effect=lambda name: {'once': once, 'scheduler': scheduler}[name]):
            # 实际恢复入口接受 ProcessManager 或名字；名字从同一manager缓存取回。
            ProcessManager.restart_processes(['once', 'scheduler'])
        once.start.assert_not_called()
        scheduler.start.assert_called_once()


if __name__ == '__main__':
    unittest.main()
