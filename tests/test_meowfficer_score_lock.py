"""已有猫建议锁定的离线回归：真实局部模板、模拟设备和临时报告，不连接游戏。"""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, Mock, patch

import numpy as np
from PIL import Image

from module.exception import RequestHumanTakeover
from module.meowfficer.advice import reset_advice
from module.meowfficer.scan_capture import ScanCapture, _exact_breed
from module.meowfficer.score import evaluate
from module.meowfficer.score_lock import IDENTITY_AREA, LOCK_BUTTON, lock_target, set_lock_state
from module.meowfficer.score_task import MeowfficerScore


ROOT = Path(__file__).resolve().parents[1]
FEED = ('炮击新手·主力', '装填新手·战列')
PENDING = ('侵略如火', '炮击新手·主力', '装填新手·战列')
REROLL = ('炮术长·主力', '装填新手·战列')
KEEP = ('侵略如火', '炮术长·主力')


def capture(names=FEED, **overrides):
    """用真实评分库生成已确认标题，避免把模糊 OCR 文本当完整天赋。"""
    defaults = dict(display_name='奥古喵', talents=evaluate(list(names), cat='奥古喵').talents,
                    level=10, breed='奥古喵', rarity='SSR', complete=True,
                    identity_confirmed=True)
    defaults.update(overrides)
    return ScanCapture(**defaults)


def runner(**overrides):
    """绕开真实配置初始化与可能发生的自动落盘。"""
    task = MeowfficerScore.__new__(MeowfficerScore)
    defaults = dict(MeowfficerScore_Source='scan', MeowfficerScore_LockByAdvice=True,
                    MeowfficerScore_ScanLimit=0, MeowfficerScore_ScanPasses=12,
                    MeowfficerScore_ReportPath='')
    defaults.update(overrides)
    task.config = SimpleNamespace(**defaults)
    task.device = Mock()
    task.results = []
    task.lock_actions = []
    return task


def fast_timer():
    """未知状态立即到限；已确认目标仍由正向识别退出，不等待墙钟时间。"""
    timer = Mock()
    timer.start.return_value = timer
    timer.reached.return_value = True
    return patch('module.meowfficer.score_lock.Timer', return_value=timer)


class DetailPage:
    """把局部资源放回空白画布，模拟锁状态响应，不包含任何账号画面。"""

    def __init__(self, locked=True, responds=True):
        self.locked = locked
        self.responds = responds
        self.page_visible = True
        self.identity_changed = False
        self.after_click = None
        self.steps = 0
        self.templates = {}
        for name in ('LOCKED', 'UNLOCKED', 'TALENT_CHECK'):
            path = ROOT / 'assets/cn/meowfficer' / f'TEMPLATE_MEOWFFICER_DETAIL_{name}.png'
            with Image.open(path) as source:
                self.templates[name] = np.array(source.convert('RGB'))
        self.device = SimpleNamespace(image=self.frame(), screenshot=Mock(side_effect=self.screenshot),
                                      click=Mock(side_effect=self.click))

    def frame(self):
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        if self.page_visible:
            image[100:132, 795:920] = self.templates['TALENT_CHECK']
        if self.locked is not None:
            image[454:511, 33:75] = self.templates['LOCKED' if self.locked else 'UNLOCKED']
        if self.identity_changed:
            x1, y1, x2, y2 = IDENTITY_AREA
            image[y1:y2, x1:x2] = 40
        return image

    def screenshot(self):
        self.steps += 1
        if self.steps > 16:
            raise AssertionError('模拟锁状态循环未有限结束')
        self.device.image = self.frame()

    def click(self, button):
        if button is not LOCK_BUTTON:
            raise AssertionError('离线夹具仅允许锁按钮操作')
        if self.responds:
            self.locked = not self.locked
        if self.after_click:
            self.after_click()

    def snapshot(self, **kwargs):
        x1, y1, x2, y2 = IDENTITY_AREA
        return capture(identity_image=self.device.image[y1:y2, x1:x2].copy(), **kwargs)


class LockTargetTests(unittest.TestCase):
    def test_final_advice_feed_unlocks_and_all_other_verdicts_lock(self):
        for rarity, breed in (('SSR', '奥古喵'), ('SR', '帕特喵')):
            for verdict, names in (('feed', FEED), ('pending', PENDING), ('reroll', REROLL), ('keep', KEEP)):
                with self.subTest(rarity=rarity, verdict=verdict):
                    current = capture(names, rarity=rarity, breed=breed)
                    result = evaluate(current.talents, cat=current.breed, level=current.level)
                    self.assertEqual(reset_advice(result).verdict, verdict)
                    target, reason = lock_target(current, result)
                    self.assertIs(target, verdict != 'feed')
                    self.assertEqual(reason, reset_advice(result).headline)

    def test_bad_initial_tier_with_invested_talents_uses_reroll_instead_of_feed(self):
        current = capture(REROLL)
        result = evaluate(current.talents, cat=current.breed)
        self.assertIn('建议喂掉', result.rubrics[result.primary[0]].tier)
        self.assertEqual(reset_advice(result).verdict, 'reroll')
        self.assertIs(lock_target(current, result)[0], True)

    def test_confirmed_blue_does_not_need_talents_or_score_to_unlock(self):
        current = capture((), display_name='蓝猫', breed=None, rarity='R', complete=False)
        self.assertIs(lock_target(current, None)[0], False)

    def test_incomplete_unknown_breed_and_unknown_rarity_protect(self):
        base = capture()
        result = evaluate(base.talents, cat=base.breed)
        for changed in (dict(complete=False, reasons=['末行被遮挡']), dict(breed=None),
                        dict(rarity=None)):
            with self.subTest(changed=changed):
                target, reason = lock_target(replace(base, **changed), result)
                self.assertIs(target, True)
                self.assertIn('保护锁定', reason)

    def test_unknown_current_identity_never_produces_a_lock_target(self):
        for rarity in ('R', 'SR', 'SSR', None):
            with self.subTest(rarity=rarity):
                current = capture(rarity=rarity, identity_confirmed=False)
                self.assertIsNone(lock_target(current, evaluate(current.talents, cat=current.breed))[0])

    def test_missing_mismatched_or_untrustworthy_score_never_unlocks(self):
        current = capture()
        original = evaluate(current.talents, cat=current.breed)
        scores = [None, replace(original, cat='帕特喵'), replace(original, primary=[]),
                  replace(original, talents=original.talents[:1])]
        for talent in (replace(original.talents[0], inferred=True),
                       replace(original.talents[0], raw=''),
                       replace(original.talents[0], raw='另一个标题'),
                       replace(original.talents[0], kind='unknown')):
            scores.append(replace(original, talents=[talent, original.talents[1]]))
        for index, result in enumerate(scores):
            with self.subTest(index=index):
                self.assertIs(lock_target(current, result)[0], True)

    def test_advice_failure_is_protective(self):
        current = capture()
        result = evaluate(current.talents, cat=current.breed)
        for response in (dict(side_effect=ValueError('建议失败')), dict(return_value=None)):
            with self.subTest(response=response), patch('module.meowfficer.score_lock.reset_advice', **response):
                self.assertIs(lock_target(current, result)[0], True)

    def test_custom_name_never_fuzzily_resolves_to_another_breed(self):
        self.assertEqual(_exact_breed('奥古喵'), '奥古喵')
        self.assertEqual(_exact_breed('限定奥古喵'), '奥古喵')
        for name in ('奥古喵喵', '我的奥古喵', '奥古猫', '不动如山'):
            with self.subTest(name=name):
                self.assertIsNone(_exact_breed(name))


class LockStateTests(unittest.TestCase):
    def setUp(self):
        self.server = patch('module.config.server.server', 'cn')
        self.server.start()
        self.addCleanup(self.server.stop)

    def test_opposite_states_click_once_and_confirm_with_fresh_screenshot(self):
        for target in (False, True):
            with self.subTest(target=target):
                page = DetailPage(locked=not target)
                action = set_lock_state(page, page.snapshot(), target, '培养建议')
                self.assertEqual(action['status'], 'changed')
                self.assertIs(action['before'], not target)
                self.assertIs(action['after'], target)
                page.device.click.assert_called_once_with(LOCK_BUTTON)
                self.assertGreaterEqual(page.device.screenshot.call_count, 2)

    def test_already_matching_state_does_not_click(self):
        for target in (False, True):
            with self.subTest(target=target):
                page = DetailPage(locked=target)
                action = set_lock_state(page, page.snapshot(), target, '培养建议')
                self.assertEqual(action['status'], 'unchanged')
                page.device.click.assert_not_called()

    def test_unknown_lock_state_does_not_guess_or_click(self):
        page = DetailPage(locked=None)
        with fast_timer():
            action = set_lock_state(page, page.snapshot(), False, '培养建议')
        self.assertEqual(action['status'], 'skipped')
        self.assertIsNone(action['before'])
        page.device.click.assert_not_called()

    def test_unconfirmed_page_or_changed_identity_never_click(self):
        for change in ('page', 'identity'):
            with self.subTest(change=change):
                page = DetailPage()
                current = page.snapshot()
                if change == 'page':
                    page.page_visible = False
                else:
                    page.identity_changed = True
                action = set_lock_state(page, current, False, '培养建议')
                self.assertEqual(action['status'], 'skipped')
                page.device.click.assert_not_called()

    def test_missing_snapshot_identity_and_unsupported_server_never_click(self):
        for change in ('snapshot', 'identity', 'server', 'target'):
            with self.subTest(change=change):
                page = DetailPage()
                current = page.snapshot()
                target = False
                if change == 'snapshot':
                    current.identity_image = None
                elif change == 'identity':
                    current.identity_confirmed = False
                elif change == 'target':
                    target = None
                with patch('module.config.server.server', 'jp' if change == 'server' else 'cn'):
                    action = set_lock_state(page, current, target, '培养建议')
                self.assertEqual(action['status'], 'skipped')
                page.device.click.assert_not_called()
                page.device.screenshot.assert_not_called()

    def test_failed_switch_is_unconfirmed_without_second_toggle(self):
        page = DetailPage(locked=True, responds=False)
        with fast_timer():
            action = set_lock_state(page, page.snapshot(), False, '培养建议')
        self.assertEqual(action['status'], 'unconfirmed')
        self.assertIs(action['after'], True)
        page.device.click.assert_called_once_with(LOCK_BUTTON)

    def test_page_or_identity_loss_after_click_is_unconfirmed(self):
        for change in ('page', 'identity'):
            with self.subTest(change=change):
                page = DetailPage()
                page.after_click = (lambda: setattr(page, 'page_visible', False)) if change == 'page' \
                    else lambda: setattr(page, 'identity_changed', True)
                action = set_lock_state(page, page.snapshot(), False, '培养建议')
                self.assertEqual(action['status'], 'unconfirmed')
                page.device.click.assert_called_once_with(LOCK_BUTTON)


class ScanLockTaskTests(unittest.TestCase):
    def setUp(self):
        self.server = patch('module.config.server.server', 'cn')
        self.server.start()
        self.addCleanup(self.server.stop)

    def test_blue_is_not_evaluated_and_existing_lock_is_removed(self):
        task = runner()
        page = DetailPage(locked=True)
        current = page.snapshot(names=(), display_name='蓝猫', breed=None, rarity='R')
        with patch('module.meowfficer.score_task.evaluate') as score:
            action = task._score_and_apply_lock(page, current)
        score.assert_not_called()
        self.assertEqual(task.results, [])
        self.assertEqual(task.lock_actions, [action])
        self.assertEqual(action['status'], 'changed')
        self.assertIs(action['after'], False)

    def test_same_name_cats_get_independent_scores_and_lock_decisions(self):
        task = runner()
        with patch('module.meowfficer.score_task.evaluate', wraps=evaluate) as score:
            first = DetailPage(locked=True)
            task._score_and_apply_lock(first, first.snapshot(names=FEED))
            second = DetailPage(locked=False)
            task._score_and_apply_lock(second, second.snapshot(names=KEEP))
        self.assertEqual(score.call_count, 2)
        self.assertEqual([name for name, _ in task.results], ['奥古喵', '奥古喵'])
        self.assertEqual([action['target'] for action in task.lock_actions], [False, True])
        self.assertEqual([action['after'] for action in task.lock_actions], [False, True])
        first.device.click.assert_called_once()
        second.device.click.assert_called_once()

    def test_custom_name_is_scored_without_fuzzy_breed_and_remains_protected(self):
        task = runner()
        page = DetailPage(locked=False)
        current = page.snapshot(display_name='奥古喵喵', breed=None, complete=False)
        with patch('module.meowfficer.score.match_cat', side_effect=AssertionError('不允许自定义名模糊匹配')), \
                patch('module.meowfficer.score_task.evaluate', wraps=evaluate) as score:
            action = task._score_and_apply_lock(page, current)
        score.assert_called_once_with(current.talents, cat=None, level=current.level)
        self.assertIsNone(task.results[0][1].cat)
        self.assertIs(action['after'], True)

    def test_score_failure_protects_and_records_no_fabricated_score(self):
        task = runner()
        page = DetailPage(locked=False)
        current = page.snapshot()
        with patch('module.meowfficer.score_task.evaluate', side_effect=ValueError('离线评分失败')):
            action = task._score_and_apply_lock(page, current)
        self.assertFalse(current.complete)
        self.assertEqual(task.results, [])
        self.assertIs(action['after'], True)
        self.assertIn('离线评分失败', action['reason'])

    def test_unconfirmed_identity_has_no_lock_operation(self):
        task = runner()
        page = DetailPage(locked=True)
        action = task._score_and_apply_lock(page, page.snapshot(identity_confirmed=False))
        self.assertEqual(action['status'], 'skipped')
        self.assertIsNone(action['target'])
        page.device.click.assert_not_called()
        page.device.screenshot.assert_not_called()

    def test_disabled_switch_uses_original_readonly_scan(self):
        task = runner(MeowfficerScore_LockByAdvice=False)
        scanner = Mock()
        current = capture()
        scanner.scan_all.return_value = [(current.display_name, current.talents, current.level)]
        with patch('module.meowfficer.scan.MeowfficerScanner', return_value=scanner), \
                patch.object(task, '_score_and_apply_lock') as action:
            task._run_scan()
        scanner.scan_all.assert_called_once_with(limit=0, passes=12, on_result=ANY)
        action.assert_not_called()
        self.assertEqual(len(task.results), 1)
        self.assertEqual(task.lock_actions, [])

    def test_unsupported_server_uses_readonly_progress_without_lock_callback(self):
        for server in ('en', 'jp', 'tw'):
            with self.subTest(server=server):
                task = runner()
                scanner = Mock()
                current = capture()
                scanner.scan_all.return_value = [(current.display_name, current.talents, current.level)]
                with patch('module.config.server.server', server), \
                        patch('module.meowfficer.scan.MeowfficerScanner', return_value=scanner), \
                        patch.object(task, '_score_and_apply_lock') as action:
                    task._run_scan()
                scanner.scan_all.assert_called_once_with(limit=0, passes=12, on_result=ANY)
                action.assert_not_called()
                self.assertEqual(len(task.results), 1)
                self.assertEqual(task.lock_actions, [])

    def test_cn_enabled_scan_scores_during_callback_without_second_pass(self):
        task = runner()
        page = DetailPage(locked=True)
        current = page.snapshot()
        def scan_all(**kwargs):
            kwargs['on_cat'](page, current)
            return [(current.display_name, current.talents, current.level)]
        page.scan_all = Mock(side_effect=scan_all)
        with patch('module.meowfficer.scan.MeowfficerScanner', return_value=page), \
                patch('module.meowfficer.score_task.evaluate', wraps=evaluate) as score:
            task._run_scan()
        self.assertIn('on_cat', page.scan_all.call_args.kwargs)
        score.assert_called_once()
        self.assertEqual(len(task.results), 1)
        self.assertEqual(len(task.lock_actions), 1)

    def test_screenshot_and_device_sources_ignore_lock_setting(self):
        for source in ('screenshot', 'device'):
            with self.subTest(source=source):
                task = runner(MeowfficerScore_Source=source)
                task._run_screenshots = Mock()
                task._run_device = Mock()
                task._run_scan = Mock()
                task._log_summary = Mock()
                task._save_report = Mock()
                with patch('module.meowfficer.score_lock.set_lock_state') as lock:
                    task.run()
                getattr(task, '_run_screenshots' if source == 'screenshot' else '_run_device').assert_called_once()
                task._run_scan.assert_not_called()
                lock.assert_not_called()

    def test_actions_without_scores_still_save_blue_and_failure_to_all_reports(self):
        with tempfile.TemporaryDirectory(prefix='meow-lock-report-') as directory:
            path = Path(directory) / 'report.md'
            task = runner(MeowfficerScore_ReportPath=str(path))
            task.lock_actions = [
                dict(name='蓝猫', before=True, after=False, target=False, status='changed',
                     reason='蓝猫不评分，保持未锁定'),
                dict(name='未知猫', before=False, after=True, target=True, status='changed',
                     reason='天赋识别失败，保护锁定'),
                dict(name='未确认猫', before=None, after=None, target=None, status='skipped',
                     reason='当前猫身份未确认，不操作'),
            ]
            task._save_report()
            for suffix in ('.md', '.html'):
                text = path.with_suffix(suffix).read_text(encoding='utf-8')
                for action in task.lock_actions:
                    self.assertIn(action['name'], text)
                    self.assertIn(action['reason'], text)
            payload = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
            self.assertEqual(payload['count'], 0)
            self.assertEqual(payload['cats'], [])
            self.assertEqual(payload['lockActions'], task.lock_actions)

    def test_unconfirmed_switch_stops_before_next_cat_and_saves_partial_report(self):
        with tempfile.TemporaryDirectory(prefix='meow-lock-partial-') as directory:
            path = Path(directory) / 'report.md'
            task = runner(MeowfficerScore_ReportPath=str(path))
            page = DetailPage(locked=True, responds=False)
            first, second = page.snapshot(), page.snapshot(names=KEEP)
            visited = []
            def scan_all(**kwargs):
                for current in (first, second):
                    visited.append(current)
                    kwargs['on_cat'](page, current)
                return []
            page.scan_all = Mock(side_effect=scan_all)
            with patch('module.meowfficer.scan.MeowfficerScanner', return_value=page), fast_timer(), \
                    self.assertRaises(RequestHumanTakeover):
                task._run_scan()
            self.assertEqual(visited, [first])
            page.device.click.assert_called_once_with(LOCK_BUTTON)
            self.assertEqual(task.lock_actions[0]['status'], 'unconfirmed')
            for suffix in ('.md', '.html', '.json'):
                self.assertTrue(path.with_suffix(suffix).is_file())
            payload = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
            self.assertEqual(len(payload['lockActions']), 1)
            self.assertEqual(payload['lockActions'][0]['status'], 'unconfirmed')

    def test_device_failure_after_click_preserves_pending_action_before_propagating(self):
        for failure_stage in ('click', 'screenshot'):
            with self.subTest(stage=failure_stage), tempfile.TemporaryDirectory(prefix='meow-lock-device-') as directory:
                path = Path(directory) / 'report.md'
                task = runner(MeowfficerScore_ReportPath=str(path))
                page = DetailPage(locked=True)
                current = page.snapshot()
                failure = ConnectionError('离线设备断开')
                if failure_stage == 'click':
                    def fail_click():
                        raise failure
                    page.after_click = fail_click
                else:
                    def fail_screenshot():
                        if page.device.click.called:
                            raise failure
                        page.screenshot()
                    page.device.screenshot.side_effect = fail_screenshot
                def scan_all(**kwargs):
                    kwargs['on_cat'](page, current)
                    return []
                page.scan_all = Mock(side_effect=scan_all)
                with patch('module.meowfficer.scan.MeowfficerScanner', return_value=page), \
                        self.assertRaises(ConnectionError) as caught:
                    task._run_scan()
                self.assertIs(caught.exception, failure)
                page.device.click.assert_called_once_with(LOCK_BUTTON)
                self.assertEqual(len(task.lock_actions), 1)
                action = task.lock_actions[0]
                self.assertEqual(action['status'], 'unconfirmed')
                self.assertIs(action['before'], True)
                self.assertIsNone(action['after'])
                payload = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
                self.assertEqual(payload['lockActions'], task.lock_actions)
                self.assertIn('切换后未确认', path.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
