"""维护周期与公告变化重排，使用临时实例和固定时间。"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from module.maintenance_schedule import capture_baselines, exercise_start_time, reconcile_plan

NOW = datetime(2026, 11, 13, 8)
CN = timezone(timedelta(hours=8))


def profile(directory, name='pilot', package='com.bilibili.azurlane'):
    data = {'Alas': {'Emulator': {'PackageName': package}}}
    for task, gate in (('Exercise', 'MaintenanceAdvance'), ('Hard', 'RunAt18')):
        data[task] = {'Scheduler': {'Enable': True, 'NextRun': '2026-11-13 18:00:00'}, task: {gate: True}}
    path = directory / f'{name}.json'
    path.write_text(json.dumps(data), encoding='utf8')
    return path, data


def load(path):
    return json.loads(path.read_text(encoding='utf8'))


def test_lead_time_only_changes_its_refill_period_for_afternoon_and_midnight():
    for start, end, expected in (
        (datetime(2026, 11, 13, 16, tzinfo=CN), datetime(2026, 11, 13, 18, tzinfo=CN), datetime(2026, 11, 13, 15, tzinfo=CN)),
        (datetime(2026, 11, 14, 0, 30, tzinfo=CN), datetime(2026, 11, 14, 0, tzinfo=CN), datetime(2026, 11, 13, 23, 30, tzinfo=CN)),
        (datetime(2026, 11, 13, 13, tzinfo=CN), datetime(2026, 11, 13, 18, tzinfo=CN), datetime(2026, 11, 13, 12, tzinfo=CN)),
    ):
        assert exercise_start_time(end, start, '00:00,12:00,18:00', 1, timedelta(hours=8)) == expected
        later = end + timedelta(days=1)
        assert exercise_start_time(later, start, '00:00,12:00,18:00', 1, timedelta(hours=8)) == later - timedelta(hours=1)


def test_no_announcement_and_other_cycle_keep_regular_delay():
    end = datetime(2026, 11, 13, 12, tzinfo=CN)
    afternoon = datetime(2026, 11, 13, 16, tzinfo=CN)
    assert exercise_start_time(end, None, '00:00,12:00,18:00', 2, timedelta(hours=8)) == end - timedelta(hours=2)
    assert exercise_start_time(end, afternoon, '00:00,12:00,18:00', 2, timedelta(hours=8)) == end - timedelta(hours=2)


def test_capture_uses_server_and_skips_templates_and_broken_files(tmp_path):
    profile(tmp_path)
    profile(tmp_path, 'japan', 'com.YoStarJP.AzurLane')
    profile(tmp_path, 'template')
    profile(tmp_path, 'template2')
    (tmp_path / 'broken.json').write_text('{', encoding='utf8')
    assert set(capture_baselines(tmp_path, 'cn')) == {'pilot.json', 'template2.json'}


def test_malformed_task_groups_do_not_block_other_tasks_or_profiles(tmp_path):
    path, data = profile(tmp_path)
    data['Exercise'] = None
    data['Alas']['Emulator']['PackageName'] = []
    path.write_text(json.dumps(data), encoding='utf8')
    profile(tmp_path, 'valid')
    (tmp_path / 'invalid.json').write_text(json.dumps({'Alas': {'Emulator': None}}), encoding='utf8')
    baseline = capture_baselines(tmp_path, 'cn')
    assert set(baseline) == {'pilot.json', 'valid.json'}
    assert set(baseline['pilot.json']) == {'Hard'}
    with patch('module.maintenance_schedule.current_time', return_value=NOW):
        assert reconcile_plan(tmp_path, 'cn', baseline)
    saved = load(path)
    assert saved['Exercise'] is None
    assert saved['Hard']['Scheduler']['NextRun'] == NOW.isoformat(sep=' ')
    assert load(tmp_path / 'valid.json')['Exercise']['Scheduler']['NextRun'] == NOW.isoformat(sep=' ')


def test_missing_legacy_switches_use_existing_task_defaults(tmp_path):
    path, data = profile(tmp_path)
    data['Exercise']['Exercise'] = {}
    data['Hard']['Hard'] = {}
    path.write_text(json.dumps(data), encoding='utf8')
    baseline = capture_baselines(tmp_path, 'cn')
    with patch('module.maintenance_schedule.current_time', return_value=NOW):
        assert reconcile_plan(tmp_path, 'cn', baseline)
    saved = load(path)
    assert saved['Exercise']['Scheduler']['NextRun'] == NOW.isoformat(sep=' ')
    assert saved['Hard']['Scheduler']['NextRun'] == data['Hard']['Scheduler']['NextRun']


def test_same_raw_next_run_compare_preserves_user_edit_and_other_task_wakes(tmp_path):
    path, data = profile(tmp_path)
    baselines = capture_baselines(tmp_path, 'cn')
    data['Exercise']['Scheduler']['NextRun'] = '2026-11-15 12:00:00'
    path.write_text(json.dumps(data), encoding='utf8')
    with patch('module.maintenance_schedule.current_time', return_value=NOW):
        assert reconcile_plan(tmp_path, 'cn', baselines)
    saved = load(path)
    assert saved['Exercise']['Scheduler']['NextRun'] == '2026-11-15 12:00:00'
    assert saved['Hard']['Scheduler']['NextRun'] == NOW.isoformat(sep=' ')


def test_disabled_tasks_switches_and_already_due_time_are_preserved(tmp_path):
    path, data = profile(tmp_path)
    data['Exercise']['Scheduler']['Enable'] = False
    data['Hard']['Hard']['RunAt18'] = False
    path.write_text(json.dumps(data), encoding='utf8')
    baseline = capture_baselines(tmp_path, 'cn')
    with patch('module.maintenance_schedule.current_time', return_value=NOW):
        assert reconcile_plan(tmp_path, 'cn', baseline)
    assert load(path) == data
    data['Exercise']['Scheduler'].update(Enable=True, NextRun='2026-11-13 00:00:00')
    path.write_text(json.dumps(data), encoding='utf8')
    baseline = capture_baselines(tmp_path, 'cn')
    with patch('module.maintenance_schedule.current_time', return_value=NOW):
        assert reconcile_plan(tmp_path, 'cn', baseline)
    assert load(path) == data


def test_partial_reconcile_can_retry_without_waking_completed_round_again(tmp_path):
    first, _ = profile(tmp_path, 'first')
    second, _ = profile(tmp_path, 'second')
    baseline = capture_baselines(tmp_path, 'cn')
    from deploy.atomic import atomic_write

    def fail_second(path, text):
        if path == str(second):
            raise OSError('模拟写入中断')
        atomic_write(path, text)

    with patch('module.maintenance_schedule.current_time', return_value=NOW), \
            patch('module.maintenance_schedule.atomic_write', side_effect=fail_second):
        assert not reconcile_plan(tmp_path, 'cn', baseline)
    completed = load(first)
    for task in ('Exercise', 'Hard'):
        completed[task]['Scheduler']['NextRun'] = '2026-11-14 00:00:00'
    first.write_text(json.dumps(completed), encoding='utf8')
    with patch('module.maintenance_schedule.current_time', return_value=NOW):
        assert reconcile_plan(tmp_path, 'cn', baseline)
    assert load(first) == completed
    assert load(second)['Hard']['Scheduler']['NextRun'] == NOW.isoformat(sep=' ')


def test_region_changed_after_fetch_is_not_rescheduled(tmp_path):
    path, data = profile(tmp_path)
    baseline = capture_baselines(tmp_path, 'cn')
    data['Alas']['Emulator']['PackageName'] = 'com.YoStarJP.AzurLane'
    path.write_text(json.dumps(data), encoding='utf8')
    assert reconcile_plan(tmp_path, 'cn', baseline)
    assert load(path) == data


def test_config_lock_timeout_keeps_reconcile_pending_without_editing_file(tmp_path):
    path, original = profile(tmp_path)
    baseline = capture_baselines(tmp_path, 'cn')
    with patch('module.maintenance_schedule.config_transaction', side_effect=TimeoutError('配置锁忙')):
        assert not reconcile_plan(tmp_path, 'cn', baseline)
    assert load(path) == original


def test_retry_uses_fixed_receipt_wake_time_and_current_clock_for_due_check(tmp_path):
    path, data = profile(tmp_path)
    baseline = capture_baselines(tmp_path, 'cn')
    earlier = '2026-11-13 07:00:00'
    with patch('module.maintenance_schedule.current_time', return_value=NOW):
        assert reconcile_plan(tmp_path, 'cn', baseline, run_at=earlier)
    assert load(path)['Hard']['Scheduler']['NextRun'] == earlier
    # 已到期排程不因旧回执时间比它更早而被重新写入。
    data['Hard']['Scheduler']['NextRun'] = '2026-11-13 07:30:00'
    path.write_text(json.dumps(data), encoding='utf8')
    baseline = capture_baselines(tmp_path, 'cn')
    with patch('module.maintenance_schedule.current_time', return_value=NOW):
        assert reconcile_plan(tmp_path, 'cn', baseline, run_at=earlier)
    assert load(path)['Hard']['Scheduler']['NextRun'] == '2026-11-13 07:30:00'
