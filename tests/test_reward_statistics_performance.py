"""奖励统计去重与后台归档：只用隔离文件、模拟OCR和可控线程。"""
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from module.cloud.files import read_json
from module.cloud.service import CloudService
from module.commission import commission
from module.research.assets import QUEUE_CHECK
from module.statistics import research_drop
from module.statistics.azurstats import AzurStats
from module.statistics.item import ItemGrid


def stat_for(folder):
    stat = object.__new__(AzurStats)
    stat.config = SimpleNamespace(config_name='offline', DropRecord_SaveFolder=str(folder),
                                  DropRecord_CloudUpload=False)
    return stat


def queue_parser():
    codes = ['D-001-MI', 'Q-002-MI', 'G-003-MI', 'C-004-MI', 'H-005-MI']
    return SimpleNamespace(ocr=SimpleNamespace(ocr=Mock(return_value=codes)),
                           lookup=set(codes), _correct_code=Mock(return_value=''))


def test_research_batch_is_identical_to_individual_identities_with_one_ocr():
    frame = np.zeros((720, 1280, 3), np.uint8)
    parser = queue_parser()
    with patch.object(research_drop, 'get_parser', return_value=parser), \
            patch.object(QUEUE_CHECK, 'match', return_value=True), \
            patch('module.research.series.match_series', side_effect=[1, 4, 6, 8, 9] * 2):
        expected = [research_drop.read_research_identity(frame, index) for index in range(5)]
        parser.ocr.ocr.reset_mock()
        actual = list(research_drop.read_research_identities(frame, range(5)))
    assert actual == expected
    parser.ocr.ocr.assert_called_once_with(frame)


def test_research_batch_keeps_missing_names_invalid_indices_and_nonqueue_fallback():
    frame = np.zeros((720, 1280, 3), np.uint8)
    parser = queue_parser()
    parser.ocr.ocr.return_value = [' q-002-mi ', 'invalid']
    with patch.object(research_drop, 'get_parser', return_value=parser), \
            patch.object(QUEUE_CHECK, 'match', return_value=True), \
            patch('module.research.series.match_series', return_value=8):
        assert list(research_drop.read_research_identities(frame, [-1, 0, 1, 4, 5])) == [
            ('', 0), ('Q-002-MI', 8), ('', 8), ('', 8), ('', 0)]
    with patch.object(research_drop, 'get_parser', return_value=parser), \
            patch.object(QUEUE_CHECK, 'match', return_value=False), \
            patch('module.research.project.get_research_finished', return_value=0), \
            patch('module.research.project.research_detect',
                  return_value=[SimpleNamespace(name='Q-002-MI', series='S8')]) as detect:
        assert list(research_drop.read_research_identities(frame, range(3))) == [('Q-002-MI', 8)] * 3
        detect.assert_called_once()


def test_research_commit_preserves_groups_and_completed_records_before_later_failure(tmp_path):
    stat = stat_for(tmp_path)
    frames = [np.full((2, 2, 3), index, np.uint8) for index in range(7)]
    groups = [(1, 3), (3, 4), (4, 7)]
    for failed in (False, True):
        parser = queue_parser()
        series = [8, 9, RuntimeError('角标失败') if failed else 6]
        with patch.object(research_drop, 'get_parser', return_value=parser), \
                patch.object(QUEUE_CHECK, 'match', return_value=True), \
                patch('module.research.series.match_series', side_effect=series), \
                patch.object(research_drop, 'record_research_drop') as record:
            assert stat.commit(frames, 'research', analyze=True, research_groups=groups)
        assert record.call_count == (2 if failed else 3)
        expected = [('D-001-MI', 8), ('Q-002-MI', 9), ('G-003-MI', 6)]
        for index, call in enumerate(record.call_args_list):
            start, end = groups[index]
            assert call.args[0] == frames[start:end]
            assert call.kwargs['identity'] == expected[index]
            assert call.kwargs['imgid'].endswith(f':{index}')
        parser.ocr.ocr.assert_called_once()


def test_slow_save_does_not_block_commit_and_all_pages_are_classified(tmp_path):
    stat = stat_for(tmp_path)
    frames = [np.full((2, 2, 3), index, np.uint8) for index in (1, 2)]
    entered, release, finished, committed = [threading.Event() for _ in range(4)]
    errors = []
    original_save_pages = stat._save_pages

    def save(frame, path):
        entered.set()
        assert release.wait(5), '测试没有释放模拟写盘'
        Path(path).write_bytes(bytes([int(frame[0, 0, 0])]))

    def save_pages(*args):
        try:
            return original_save_pages(*args)
        finally:
            finished.set()

    def record(*args, classification_items):
        classification_items.extend([('PlateGunT4', 1), ('CoordinateObscure', 2)])
        return True

    def commit():
        try:
            stat.commit(frames, 'opsi_meowfficer_farming', save=True, local=True)
        except BaseException as error:
            errors.append(error)
        finally:
            committed.set()

    with patch.object(stat, '_drop_save_images', return_value=frames), \
            patch.object(stat, '_record_local', side_effect=record), \
            patch.object(stat, '_save_pages', side_effect=save_pages), \
            patch('module.statistics.azurstats.save_image', side_effect=save):
        caller = threading.Thread(target=commit)
        caller.start()
        try:
            assert entered.wait(2)
            assert committed.wait(2), 'commit仍在等待截图写盘'
            assert not finished.is_set()
        finally:
            release.set()
            caller.join(5)
            assert finished.wait(5)
    assert not errors
    root = tmp_path / 'opsi_meowfficer_farming'
    files = list(root.rglob('*.png'))
    assert len(files) == 4
    assert not list(root.glob('*.png'))
    for category in ('金菜', '隐秘'):
        archived = list((root / category).rglob('*.png'))
        assert sorted(path.read_bytes() for path in archived) == [b'\x01', b'\x02']
        assert all('_金菜x1_隐秘x2.png' in path.name for path in archived)


def test_partial_save_failure_classifies_only_successful_pages_without_polling(tmp_path):
    stat = stat_for(tmp_path)
    result = Future()
    result.set_result((('PlateGunT4', 1),))
    frames = [object(), object()]

    def save(frame, path):
        if frame is frames[1]:
            raise OSError('模拟第二页保存失败')
        Path(path).write_bytes(b'original')

    with patch('module.statistics.azurstats.save_image', side_effect=save), \
            patch('module.statistics.azurstats.time.sleep') as sleep:
        assert not stat._save_pages(frames, 'opsi_meowfficer_farming', '1789490773600.png', result)
        assert AzurStats.classify_meow_screenshot(str(tmp_path), 'missing.png', []) == []
    sleep.assert_not_called()
    assert [path.read_bytes() for path in tmp_path.rglob('*.png')] == [b'original']


def test_failed_statistics_keep_saved_original_without_classification(tmp_path):
    stat = stat_for(tmp_path)
    result = Future()
    result.set_result(None)
    with patch('module.statistics.azurstats.save_image',
               side_effect=lambda frame, path: Path(path).write_bytes(b'original')), \
            patch.object(stat, 'classify_meow_screenshot') as classify:
        assert stat._save_pages([object()], 'opsi_meowfficer_farming', '1789490773600.png', result)
    classify.assert_not_called()
    assert (tmp_path / 'opsi_meowfficer_farming/1789490773600.png').read_bytes() == b'original'


def test_unexpected_record_error_releases_save_worker_and_preserves_original(tmp_path):
    stat = stat_for(tmp_path)
    finished = threading.Event()
    future = Future()
    original_save_pages = stat._save_pages

    def save_pages(*args):
        try:
            return original_save_pages(*args)
        finally:
            finished.set()

    with patch('module.statistics.azurstats.Future', return_value=future), \
            patch.object(stat, '_record_local', side_effect=RuntimeError('模拟入库异常')), \
            patch.object(stat, '_save_pages', side_effect=save_pages), \
            patch.object(stat, 'classify_meow_screenshot') as classify, \
            patch('module.statistics.azurstats.save_image',
                  side_effect=lambda frame, path: Path(path).write_bytes(b'original')):
        try:
            try:
                stat.commit([np.zeros((2, 2, 3), np.uint8)],
                            'opsi_meowfficer_farming', save=True, local=True)
            except RuntimeError as error:
                assert str(error) == '模拟入库异常'
            else:
                raise AssertionError('入库异常不应被保存线程吞掉')
            assert finished.wait(2)
        finally:
            if not future.done():
                future.set_result(None)
            assert finished.wait(2)
    classify.assert_not_called()
    assert [path.read_bytes() for path in tmp_path.rglob('*.png')] == [b'original']


def test_commission_reuses_only_templates_and_keeps_recognition_state_separate():
    handler = SimpleNamespace(_commission_reward_images=[object()])
    states = []
    original_load = ItemGrid.load_template_folder

    def predict(grid, image, **kwargs):
        states.append((grid, set(grid.templates), dict(grid.templates_hit)))
        grid.templates['99999'] = np.zeros((1, 1, 3), np.uint8)
        grid.templates_hit['99999'] = 7
        grid.items = [SimpleNamespace(name='Oil', amount=57, is_known_item=lambda: True)]

    with patch.object(ItemGrid, 'load_template_folder', autospec=True, side_effect=original_load) as load, \
            patch.object(ItemGrid, 'predict', autospec=True, side_effect=predict), \
            patch('module.handler.assets.INFO_BAR_1.appear_on', return_value=False), \
            patch('module.combat.assets.GET_ITEMS_1.match_template_color', return_value=True), \
            patch('module.statistics.get_items.GetItemsStatistics._stats_get_items_is_odd', return_value=True):
        first = commission.RewardCommission._recognize_commission_income(handler)
        second = commission.RewardCommission._recognize_commission_income(handler)
    assert first == second
    assert first[0] == {'Oil': 57}
    load.assert_called_once()
    assert states[0][0] is not states[1][0]
    assert states[0][0].amount_ocr is not states[1][0].amount_ocr
    assert states[0][1:] == states[1][1:]
    assert '99999' not in handler._commission_income_template_cache[1]


def test_commission_notification_queues_final_message_and_skips_unused_summary():
    config = SimpleNamespace(config_name='offline', Commission_CommissionNotifyReward=True,
                             Commission_CommissionNotifyRewardStatistics=True,
                             Commission_GemStatistics=False, Error_OnePushConfig='isolated')
    handler = SimpleNamespace(config=config)
    stats = {period: {'Gem': 3} for period in ('today', 'week', 'month')}
    with patch('module.statistics.cl1_database.db') as db, \
            patch('module.base.async_executor.network_executor.submit') as submit, \
            patch.object(commission, 'handle_notify') as push, \
            patch.object(commission, 'notify_webui') as webui:
        db.get_commission_reward_stats.return_value = stats
        commission.RewardCommission._notify_commission_income(handler, {'Oil': 57})
        db.get_commission_reward_stats.assert_not_called()
        submit.assert_not_called()
        commission.RewardCommission._notify_commission_income(handler, {'Gem': 3})
        submit.assert_called_once()
        push.assert_not_called()
        webui.assert_not_called()
        function, *args = submit.call_args.args
        config.config_name, config.Error_OnePushConfig = 'changed', 'changed'
        stats['today']['Gem'] = 999
        function(*args)
        assert push.call_args.args == ('isolated',)
        assert '今日累计: 3' in push.call_args.kwargs['content']
        assert webui.call_args.args == ('offline',)


def test_notification_failure_does_not_suppress_other_channel():
    with patch.object(commission, 'handle_notify', side_effect=RuntimeError('离线')), \
            patch.object(commission, 'notify_webui') as webui:
        commission._send_commission_reward_notification('isolated', 'offline', '标题', '标题', '内容', '内容')
    webui.assert_called_once()


def test_cloud_capture_content_cache_and_unchanged_job_do_not_reencode_or_rewrite(tmp_path):
    queue = CloudService(tmp_path, 'offline', api=Mock())
    queue.start = Mock()
    frame = np.zeros((720, 1280, 3), np.uint8)
    first = {'source': 'U-ABCD', 'bounds': [10, 10, 106, 106]}
    second = {'source': 'U-EFGH', 'bounds': [110, 10, 206, 106]}
    from module.cloud.files import write_json
    with patch('module.cloud.service.cv2.imencode', wraps=cv2.imencode) as encode, \
            patch('module.cloud.service.write_json', wraps=write_json) as write, \
            patch.object(queue, '_capacity', wraps=queue._capacity) as capacity:
        queue.capture(frame, [first], scene='research')
        queue.capture(frame.copy(), [first], scene='research')
        assert encode.call_count == write.call_count == capacity.call_count == 1
        queue.capture(frame, [first, second], scene='research')
        assert encode.call_count == 1
        assert write.call_count == capacity.call_count == 2
        frame[0, 0] = 255
        queue.capture(frame, [first], scene='research')
        assert encode.call_count == 2
        assert write.call_count == capacity.call_count == 3
    jobs = [read_json(path) for path in (tmp_path / 'screenshots').glob('*.json')]
    assert sorted(len(job['observations']) for job in jobs) == [1, 2]
    import base64
    decoded = [cv2.imdecode(np.frombuffer(base64.b64decode(job['image_base64']), np.uint8), cv2.IMREAD_COLOR)
               for job in jobs]
    assert sorted(int(image[0, 0, 0]) for image in decoded) == [0, 255]


def test_concurrent_cloud_captures_keep_all_observations(tmp_path):
    queue = CloudService(tmp_path, 'offline', api=Mock())
    queue.start = Mock()
    frame = np.zeros((720, 1280, 3), np.uint8)
    observations = [{'source': source, 'bounds': [10, 10, 106, 106]} for source in ('U-ABCD', 'U-EFGH')]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(queue.capture, frame, [row], scene='research') for row in observations]
        for future in futures:
            future.result(timeout=5)
    jobs = list((tmp_path / 'screenshots').glob('*.json'))
    assert len(jobs) == 1
    assert sorted(read_json(jobs[0])['observations'], key=lambda row: row['source']) == observations
