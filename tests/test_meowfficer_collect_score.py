"""领取新指挥喵时的评分与自动锁定回归，不连接设备或真实配置。"""

import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from module.meowfficer import collect as collect_module
from module.meowfficer.collect import MeowfficerCollect
from module.meowfficer.collect_score import MeowfficerCollectScore
from module.meowfficer.advice import VERDICT_FEED, VERDICT_KEEP, VERDICT_PENDING, VERDICT_REROLL, reset_advice
from module.meowfficer.score import Talent, evaluate


class CollectPageFixture:
    """一只待领取新猫到训练页的有限状态机，阻止意外无限点击。"""

    def __init__(self, rarity='purple', special=False, locked=False, **config):
        self.runner = MeowfficerCollect.__new__(MeowfficerCollect)
        defaults = dict(
            MeowfficerTrain_LockByAdvice=False,
            MeowfficerTrain_ScoreTalents=False,
            MeowfficerTrain_ScoreThreshold=0,
            MeowfficerTrain_RetainTalentedGold=True,
            MeowfficerTrain_RetainTalentedPurple=False,
            DropRecord_MeowfficerTalent='do_not',
        )
        defaults.update(config)
        self.runner.config = SimpleNamespace(**defaults)
        self.stage = 'get'
        self.rarity = rarity
        self.locked = locked
        self.steps = 0
        self.actions = []
        self.runner.device = SimpleNamespace(
            image=object(), screenshot=Mock(side_effect=self.screenshot),
            click=Mock(side_effect=self.click), sleep=Mock(), click_record=['领取占位'] * 20,
        )
        self.runner.appear = Mock(side_effect=self.appear)
        self.runner._meow_detect_shift = Mock(return_value=False)
        self.runner._meow_get_rarity = Mock(side_effect=lambda **kwargs: self.rarity)
        self.runner.handle_meow_popup_dismiss = Mock(return_value=False)
        self.runner._meow_is_special_talented = Mock(return_value=special)
        self.runner._meow_apply_lock = Mock(side_effect=self.apply_lock)
        self.runner._meow_skip_lock = Mock(side_effect=self.skip_lock)
        self.runner._meow_skip_popup_after_locking = Mock(side_effect=self.skip_locked_popup)
        self.runner.interval_reset = Mock()
        self.drop = SimpleNamespace(add=Mock())
        self.runner.stat = SimpleNamespace(new=Mock(side_effect=lambda **kwargs: nullcontext(self.drop)))

    def screenshot(self):
        self.steps += 1
        if self.steps > 8:
            raise AssertionError('离线领取流程未按有限状态退出')

    def appear(self, button, **kwargs):
        if button is collect_module.MEOWFFICER_TRAIN_START:
            return self.stage == 'train'
        if self.stage != 'get':
            return False
        if button is collect_module.MEOWFFICER_GET_CHECK:
            return True
        if button is collect_module.MEOWFFICER_APPLY_UNLOCK:
            return self.locked
        if button is collect_module.MEOWFFICER_APPLY_LOCK:
            return not self.locked
        if button is collect_module.MEOWFFICER_GOLD_CHECK:
            return self.rarity == 'gold'
        if button is collect_module.MEOWFFICER_PURPLE_CHECK:
            return self.rarity == 'purple'
        return False

    def click(self, button):
        self.actions.append(('click', button.name))
        if button is collect_module.MEOWFFICER_TRAIN_CLICK_SAFE_AREA:
            self.stage = 'train'

    def apply_lock(self, lock=True):
        self.actions.append(('lock', lock))
        self.locked = lock

    def skip_lock(self):
        self.actions.append(('skip_gold', None))
        self.stage = 'train'

    def skip_locked_popup(self, **kwargs):
        self.actions.append(('skip_locked', None))
        self.stage = 'train'

    def run(self):
        timer = Mock()
        timer.start.return_value = timer
        timer.reset.return_value = timer
        timer.reached.return_value = True
        with patch('module.meowfficer.collect.Timer', return_value=timer):
            self.runner.meow_get()
        if self.stage != 'train':
            raise AssertionError('领取结束没有返回训练页')
        return self.runner


class CollectQueueFixture(CollectPageFixture):
    """连续多猫领取：金猫确认弹窗与下一只预锁新猫是不同状态。"""

    def __init__(self, cats):
        self.cats = cats
        self.index = 0
        self.confirm_pending = False
        self.scored = []
        self.finished = []
        super().__init__(
            rarity=cats[0]['rarity'], locked=cats[0].get('locked', False),
            MeowfficerTrain_LockByAdvice=True,
            MeowfficerTrain_RetainTalentedGold=False,
            MeowfficerTrain_RetainTalentedPurple=False,
            MeowfficerTrain_ScoreThreshold=100,
        )
        self.runner._meow_score_ocr = Mock(return_value=object())
        self.runner._meow_is_special_talented.side_effect = self.read_details

    def screenshot(self):
        self.steps += 1
        if self.steps > len(self.cats) * 5:
            raise AssertionError('多猫领取未按有限状态退出')

    def appear(self, button, **kwargs):
        if self.confirm_pending:
            return button is collect_module.MEOWFFICER_CONFIRM
        return super().appear(button, **kwargs)

    def read_details(self, **kwargs):
        if self.confirm_pending:
            raise AssertionError('不能把已处理金猫的确认弹窗再次当作新猫评分')
        if self.rarity == 'blue':
            raise AssertionError('蓝猫必须跳过天赋详情与评分')
        self.scored.append(self.index)
        self.runner.meow_score_reset()
        spec = self.cats[self.index]
        cat = {'gold': '奥古喵', 'purple': '帕特喵'}[self.rarity]
        talents = evaluate(list(spec.get('names', ())), cat=cat).talents
        with patch('module.meowfficer.score_ocr.recognize', side_effect=[([talent], cat) for talent in talents]):
            for talent in talents:
                self.runner.meow_score_capture(object())
        self.runner.meow_score_finish(expected_talents=spec.get('expected', len(talents)))
        return False

    def advance(self):
        self.finished.append((self.index, self.locked))
        self.index += 1
        self.confirm_pending = False
        if self.index == len(self.cats):
            self.stage = 'train'
        else:
            self.rarity = self.cats[self.index]['rarity']
            self.locked = self.cats[self.index].get('locked', False)

    def click(self, button):
        self.actions.append(('click', button.name))
        if button is collect_module.MEOWFFICER_CONFIRM and self.confirm_pending:
            self.advance()
        elif button is collect_module.MEOWFFICER_TRAIN_CLICK_SAFE_AREA:
            if self.rarity == 'gold' and self.locked:
                self.confirm_pending = True
            else:
                self.advance()

    def skip_lock(self):
        self.actions.append(('skip_gold', None))
        self.advance()

    def skip_locked_popup(self, **kwargs):
        if not self.confirm_pending:
            raise AssertionError('下一只预锁新猫尚未评分，不能跳过它的领取页')
        self.actions.append(('skip_locked', None))
        self.advance()


class DelayedConfirmQueueFixture(CollectQueueFixture):
    """确认后关闭动画持续一帧，底层获取页可见但点击冷却还没结束。"""

    def __init__(self, cats):
        self.confirmation_clicked = False
        self.animation_frames = 0
        self.blocked_clicks = 0
        super().__init__(cats)

    def screenshot(self):
        super().screenshot()
        if self.confirm_pending and self.confirmation_clicked:
            self.animation_frames += 1
            if self.animation_frames == 2:
                self.advance()

    def appear(self, button, **kwargs):
        if self.confirm_pending:
            if button is collect_module.MEOWFFICER_GET_CHECK:
                return True
            if button is collect_module.MEOWFFICER_CANCEL:
                return True
            if button is collect_module.MEOWFFICER_CONFIRM:
                if kwargs.get('interval', 0) and self.confirmation_clicked:
                    self.blocked_clicks += 1
                    return False
                return True
            return False
        return super().appear(button, **kwargs)

    def click(self, button):
        if button is collect_module.MEOWFFICER_CONFIRM and self.confirm_pending:
            if self.confirmation_clicked:
                raise AssertionError('关闭动画期间不得重复确认点击')
            self.actions.append(('click', button.name))
            self.confirmation_clicked = True
            return
        super().click(button)


class TestExistingCollectBehavior(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.meowfficer.collect.logger'))
        self.enterContext(patch('module.meowfficer.collect_score.logger'))

    def test_default_gold_special_is_locked(self):
        runner = CollectPageFixture(rarity='gold', special=True).run()
        runner._meow_apply_lock.assert_called_once_with()
        runner._meow_skip_lock.assert_not_called()

    def test_default_gold_without_special_uses_original_cancel(self):
        runner = CollectPageFixture(rarity='gold', special=False).run()
        runner._meow_apply_lock.assert_not_called()
        runner._meow_skip_lock.assert_called_once_with()

    def test_gold_retain_switch_can_still_disable_original_lock(self):
        runner = CollectPageFixture(
            rarity='gold', special=True, MeowfficerTrain_RetainTalentedGold=False,
        ).run()
        runner._meow_apply_lock.assert_not_called()
        runner._meow_skip_lock.assert_called_once_with()

    def test_purple_special_requires_existing_retain_switch(self):
        for retain in (False, True):
            with self.subTest(retain=retain):
                runner = CollectPageFixture(
                    special=True, MeowfficerTrain_RetainTalentedPurple=retain,
                ).run()
                self.assertEqual(runner._meow_apply_lock.call_count, int(retain))
                runner._meow_skip_lock.assert_not_called()

    def test_blue_is_not_locked_by_legacy_special_rule(self):
        fixture = CollectPageFixture(rarity='blue', special=True)
        runner = fixture.run()
        self.assertFalse(fixture.locked)
        runner._meow_apply_lock.assert_not_called()
        runner._meow_skip_lock.assert_not_called()
        runner._meow_is_special_talented.assert_not_called()

    def test_original_score_threshold_remains_effective(self):
        fixture = CollectPageFixture(
            rarity='gold', special=True, MeowfficerTrain_ScoreTalents=True,
            MeowfficerTrain_ScoreThreshold=99,
        )
        fixture.runner._meow_score_result = evaluate(['侵略如火'], cat='奥古喵')
        runner = fixture.run()
        runner._meow_apply_lock.assert_not_called()
        runner._meow_skip_lock.assert_called_once_with()

    def test_disabled_scoring_never_creates_ocr_or_changes_cache(self):
        runner = MeowfficerCollectScore()
        runner.config = SimpleNamespace()
        runner._meow_score_ocr = Mock(side_effect=AssertionError('默认开关禁止加载 OCR'))
        runner.meow_score_capture(object())
        self.assertIsNone(runner.meow_score_finish())
        self.assertTrue(runner.meow_score_passes())
        runner._meow_score_ocr.assert_not_called()
        self.assertFalse(hasattr(runner, '_meow_score_result'))


class TestCollectRarityRecognition(unittest.TestCase):
    """用品质模板和代表性区域像素验证蓝色必须正向命中。"""

    @staticmethod
    def make_runner(image):
        runner = MeowfficerCollect.__new__(MeowfficerCollect)
        runner.device = SimpleNamespace(image=image, stuck_record_add=Mock())
        runner.interval_timer = {}
        return runner

    @staticmethod
    def blue_area(shifted=False):
        gold = collect_module.MEOWFFICER_GOLD_CHECK.area
        purple = collect_module.MEOWFFICER_PURPLE_CHECK.area
        dx, dy = (-40, -20) if shifted else (0, 0)
        return (
            min(gold[0], purple[0]) - 16 + dx,
            min(gold[1], purple[1]) - 4 + dy,
            max(gold[2], purple[2]) + 16 + dx,
            max(gold[3], purple[3]) + 4 + dy,
        )

    def test_real_gold_and_purple_resources_keep_their_rarity(self):
        root = Path(__file__).resolve().parents[1]
        for name, button in (
            ('gold', collect_module.MEOWFFICER_GOLD_CHECK),
            ('purple', collect_module.MEOWFFICER_PURPLE_CHECK),
        ):
            with self.subTest(rarity=name):
                with Image.open(root / button.file) as source:
                    image = np.array(source.convert('RGB'))
                self.assertEqual(self.make_runner(image)._meow_get_rarity(), name)

    def test_blue_marker_uses_real_color_count_without_ocr(self):
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        x, y, _, _ = self.blue_area()
        image[y + 2:y + 8, x + 2:x + 8] = (128, 190, 255)
        runner = self.make_runner(image)
        runner._meow_score_ocr = Mock(side_effect=AssertionError('品质识别不能初始化 OCR'))
        self.assertEqual(runner._meow_get_rarity(), 'blue')
        runner._meow_score_ocr.assert_not_called()

    def test_shifted_blue_marker_is_found_only_in_shifted_region(self):
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        x, y, _, _ = self.blue_area(shifted=True)
        image[y + 2:y + 8, x + 2:x + 8] = (128, 190, 255)
        runner = self.make_runner(image)
        self.assertIsNone(runner._meow_get_rarity(shifted=False))
        self.assertEqual(runner._meow_get_rarity(shifted=True), 'blue')

    def test_blank_gray_and_occluded_markers_are_unknown(self):
        for color in ((0, 0, 0), (140, 140, 140), (255, 255, 255), (20, 40, 60)):
            with self.subTest(color=color):
                image = np.full((720, 1280, 3), color, dtype=np.uint8)
                self.assertIsNone(self.make_runner(image)._meow_get_rarity())

    def test_a_few_blue_pixels_or_blue_outside_marker_do_not_prove_blue_cat(self):
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        x, y, _, _ = self.blue_area()
        image[y + 2:y + 4, x + 2:x + 7] = (128, 190, 255)
        image[100:150, 100:150] = (128, 190, 255)
        self.assertIsNone(self.make_runner(image)._meow_get_rarity())


class TestAdviceLockDecision(unittest.TestCase):
    """不锁只允许由完整的准确识别得出的明确 feed 建议触发。"""

    def setUp(self):
        self.enterContext(patch('module.meowfficer.collect_score.logger'))

    @staticmethod
    def make_runner(lock=True, score=False):
        runner = MeowfficerCollectScore()
        runner.config = SimpleNamespace(
            MeowfficerTrain_LockByAdvice=lock,
            MeowfficerTrain_ScoreTalents=score,
            MeowfficerTrain_ScoreThreshold=100,
        )
        runner.meow_score_reset()
        runner._meow_score_ocr = Mock(return_value=object())
        return runner

    @staticmethod
    def talents(*names):
        # 真实解析器提供标准天赋线与原文；不伪造分数、档位或成品标志。
        return evaluate(list(names), cat='奥古喵').talents

    def capture(self, runner, responses, expected=None, cat=None):
        with patch('module.meowfficer.score_ocr.recognize', side_effect=responses):
            for _ in responses:
                runner.meow_score_capture(object())
        return runner.meow_score_finish(
            cat=cat, expected_talents=len(responses) if expected is None else expected,
        )

    def test_advice_switch_enables_scoring_without_old_score_switch(self):
        for lock, score in ((False, False), (False, True), (True, False), (True, True)):
            with self.subTest(lock=lock, score=score):
                runner = self.make_runner(lock=lock, score=score)
                self.assertEqual(runner.meow_score_enabled(), lock or score)
                self.assertEqual(runner.meow_lock_by_advice_enabled(), lock)

    def test_complete_recognition_obeys_all_four_real_verdicts(self):
        cases = (
            (VERDICT_FEED, ('炮击新手·主力', '装填新手·战列'), False),
            (VERDICT_PENDING, ('侵略如火', '炮击新手·主力'), True),
            (VERDICT_REROLL, ('炮术长·主力', '无影手·战列'), True),
            (VERDICT_KEEP, ('侵略如火', '炮术长·主力'), True),
        )
        for verdict, names, locked in cases:
            with self.subTest(verdict=verdict):
                runner = self.make_runner()
                result = self.capture(
                    runner, [([talent], '奥古喵') for talent in self.talents(*names)],
                )
                self.assertEqual(reset_advice(result).verdict, verdict)
                self.assertEqual(runner.meow_should_lock_by_advice(), locked)

    def test_false_scoring_switch_does_not_disable_advice(self):
        runner = self.make_runner(score=False)
        self.capture(runner, [(self.talents('炮击新手·主力'), '奥古喵')])
        self.assertFalse(runner.meow_should_lock_by_advice())
        runner._meow_score_ocr.assert_called_once_with()

    def test_disabled_advice_does_not_claim_legacy_lock_decision(self):
        runner = self.make_runner(lock=False, score=True)
        self.assertFalse(runner.meow_should_lock_by_advice())

    def test_capture_errors_empty_and_multiple_results_are_protected(self):
        cases = (
            [RuntimeError('离线OCR失败')],
            [([], '奥古喵')],
            [(self.talents('炮击新手·主力', '装填新手·战列'), '奥古喵')],
        )
        for responses in cases:
            with self.subTest(responses=responses):
                runner = self.make_runner()
                self.capture(runner, responses)
                self.assertTrue(runner.meow_should_lock_by_advice())

    def test_ocr_unavailable_is_protected(self):
        runner = self.make_runner()
        runner._meow_score_ocr.return_value = None
        runner.meow_score_capture(object())
        runner.meow_score_finish(expected_talents=1)
        self.assertTrue(runner.meow_should_lock_by_advice())

    def test_unknown_inferred_and_fuzzy_original_are_protected(self):
        normal = self.talents('炮击新手·主力')[0]
        cases = (
            Talent(name='不存在的天赋', line='不存在的天赋', level=1, kind='unknown', raw='不存在的天赋'),
            Talent(name=normal.name, line=normal.line, level=1, kind='normal', raw=normal.name, inferred=True),
            Talent(name=normal.name, line=normal.line, level=1, kind='normal', raw='炮击新乎·主力'),
            Talent(name=normal.name, line=normal.line, level=1, kind='normal', raw=''),
        )
        for talent in cases:
            with self.subTest(raw=talent.raw, inferred=talent.inferred):
                runner = self.make_runner()
                self.capture(runner, [([talent], '奥古喵')])
                self.assertTrue(runner.meow_should_lock_by_advice())

    def test_spacing_and_punctuation_normalization_does_not_force_lock(self):
        runner = self.make_runner()
        talent = self.talents('炮击新手·主力')[0]
        talent.raw = '天赋：炮 击 新 手，主 力'
        self.capture(runner, [([talent], '奥古喵')])
        self.assertFalse(runner.meow_should_lock_by_advice())

    def test_missing_or_duplicate_details_are_protected(self):
        for expected in (0, 2, 3):
            with self.subTest(expected=expected):
                runner = self.make_runner()
                self.capture(runner, [(self.talents('炮击新手·主力'), '奥古喵')], expected=expected)
                self.assertTrue(runner.meow_should_lock_by_advice())
        runner = self.make_runner()
        self.capture(runner, [(self.talents('炮击新手·主力'), '奥古喵')] * 2)
        self.assertTrue(runner.meow_should_lock_by_advice())

    def test_missing_unknown_and_conflicting_cat_names_are_protected(self):
        for cats in ((None, None), ('未知测试猫', '未知测试猫'), ('奥古喵', '乔治喵')):
            with self.subTest(cats=cats):
                runner = self.make_runner()
                names = self.talents('炮击新手·主力', '装填新手·战列')
                self.capture(runner, [([talent], cat) for talent, cat in zip(names, cats)])
                self.assertTrue(runner.meow_should_lock_by_advice())

    def test_no_result_or_expected_count_cannot_unlock(self):
        runner = self.make_runner()
        self.assertTrue(runner.meow_should_lock_by_advice())
        with patch('module.meowfficer.score_ocr.recognize', return_value=(self.talents('炮击新手·主力'), '奥古喵')):
            runner.meow_score_capture(object())
        runner.meow_score_finish()
        self.assertTrue(runner.meow_should_lock_by_advice())

    def test_reset_clears_previous_failure_and_previous_cat(self):
        runner = self.make_runner()
        self.capture(runner, [RuntimeError('前一只识别失败')])
        self.assertTrue(runner.meow_should_lock_by_advice())
        runner.meow_score_reset()
        self.capture(runner, [(self.talents('炮击新手·主力'), '乔治喵')])
        self.assertFalse(runner.meow_should_lock_by_advice())

    def test_score_exception_is_protected_only_in_new_mode(self):
        for lock in (False, True):
            with self.subTest(lock=lock):
                runner = self.make_runner(lock=lock, score=True)
                with patch('module.meowfficer.score_ocr.recognize', return_value=(self.talents('炮击新手·主力'), '奥古喵')):
                    runner.meow_score_capture(object())
                with patch('module.meowfficer.collect_score.evaluate', side_effect=ValueError('离线评分失败')):
                    if lock:
                        runner.meow_score_finish(expected_talents=1)
                        self.assertTrue(runner.meow_should_lock_by_advice())
                    else:
                        with self.assertRaises(ValueError):
                            runner.meow_score_finish(expected_talents=1)


class TestAdviceCollectBehavior(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.meowfficer.collect.logger'))
        self.enterContext(patch('module.meowfficer.collect_score.logger'))

    def make_fixture(self, rarity, names, locked=False):
        fixture = CollectPageFixture(
            rarity=rarity, special=False, locked=locked,
            MeowfficerTrain_LockByAdvice=True,
            MeowfficerTrain_RetainTalentedGold=False,
            MeowfficerTrain_RetainTalentedPurple=False,
            MeowfficerTrain_ScoreThreshold=100,
        )
        runner = fixture.runner
        runner._meow_score_ocr = Mock(return_value=object())
        cat = {'gold': '奥古喵', 'purple': '帕特喵'}[rarity]

        def read_details(**kwargs):
            runner.meow_score_reset()
            talents = evaluate(list(names), cat=cat).talents
            with patch('module.meowfficer.score_ocr.recognize', side_effect=[([talent], cat) for talent in talents]):
                for talent in talents:
                    runner.meow_score_capture(object())
            runner.meow_score_finish(expected_talents=len(talents))
            return False

        runner._meow_is_special_talented.side_effect = read_details
        runner.meow_score_passes = Mock(side_effect=AssertionError('新规则不能被旧评分门槛覆盖'))
        return fixture

    def test_gold_and_purple_follow_real_advice_and_ignore_old_switches(self):
        cases = (
            (VERDICT_FEED, ('炮击新手·主力', '装填新手·战列')),
            (VERDICT_PENDING, ('侵略如火', '炮击新手·主力')),
            (VERDICT_REROLL, ('炮术长·主力', '无影手·战列')),
            (VERDICT_KEEP, ('侵略如火', '炮术长·主力')),
        )
        for rarity in ('gold', 'purple'):
            for verdict, names in cases:
                with self.subTest(rarity=rarity, verdict=verdict):
                    fixture = self.make_fixture(rarity, names)
                    runner = fixture.run()
                    self.assertEqual(reset_advice(runner._meow_score_result).verdict, verdict)
                    if verdict == VERDICT_FEED:
                        self.assertFalse(fixture.locked)
                        runner._meow_apply_lock.assert_called_once_with(lock=False)
                        self.assertEqual(runner._meow_skip_lock.call_count, int(rarity == 'gold'))
                    else:
                        self.assertTrue(fixture.locked)
                        runner._meow_apply_lock.assert_called_once_with(lock=True)
                        runner._meow_skip_lock.assert_not_called()
                    runner.meow_score_passes.assert_not_called()

    def test_prelocked_gold_feed_is_scored_then_unlocked_before_cancel(self):
        fixture = self.make_fixture('gold', ('炮击新手·主力', '装填新手·战列'), locked=True)
        runner = fixture.run()
        runner._meow_is_special_talented.assert_called_once()
        runner._meow_skip_popup_after_locking.assert_not_called()
        runner._meow_apply_lock.assert_called_once_with(lock=False)
        self.assertLess(fixture.actions.index(('lock', False)), fixture.actions.index(('skip_gold', None)))
        self.assertFalse(fixture.locked)

    def test_prelocked_gold_keep_is_not_skipped_before_scoring(self):
        fixture = self.make_fixture('gold', ('侵略如火', '炮术长·主力'), locked=True)
        runner = fixture.run()
        runner._meow_is_special_talented.assert_called_once()
        runner._meow_skip_popup_after_locking.assert_not_called()
        self.assertTrue(fixture.locked)
        runner._meow_skip_lock.assert_not_called()

    def test_blue_skips_details_ocr_and_lock_decision_for_all_score_switches(self):
        for advice in (False, True):
            for score in (False, True):
                with self.subTest(advice=advice, score=score):
                    fixture = CollectPageFixture(
                        rarity='blue', special=True,
                        MeowfficerTrain_LockByAdvice=advice,
                        MeowfficerTrain_ScoreTalents=score,
                        MeowfficerTrain_RetainTalentedGold=True,
                        MeowfficerTrain_RetainTalentedPurple=True,
                        MeowfficerTrain_ScoreThreshold=100,
                    )
                    runner = fixture.runner
                    runner._meow_score_result = evaluate(['侵略如火', '炮术长·主力'], cat='奥古喵')
                    runner._meow_score_ocr = Mock(side_effect=AssertionError('蓝猫不能初始化 OCR'))
                    runner.meow_score_passes = Mock(side_effect=AssertionError('蓝猫不应读取旧分数'))
                    runner.meow_should_lock_by_advice = Mock(side_effect=AssertionError('蓝猫不应读取旧建议'))
                    fixture.run()
                    self.assertFalse(fixture.locked)
                    runner._meow_apply_lock.assert_not_called()
                    runner._meow_skip_lock.assert_not_called()
                    runner._meow_is_special_talented.assert_not_called()
                    runner._meow_score_ocr.assert_not_called()
                    runner.meow_score_passes.assert_not_called()
                    runner.meow_should_lock_by_advice.assert_not_called()

    def test_prelocked_blue_is_unlocked_without_scoring_for_all_switches(self):
        for advice in (False, True):
            for score in (False, True):
                with self.subTest(advice=advice, score=score):
                    fixture = CollectPageFixture(
                        rarity='blue', locked=True,
                        MeowfficerTrain_LockByAdvice=advice,
                        MeowfficerTrain_ScoreTalents=score,
                    )
                    runner = fixture.run()
                    self.assertFalse(fixture.locked)
                    runner._meow_apply_lock.assert_called_once_with(lock=False)
                    runner._meow_is_special_talented.assert_not_called()
                    runner._meow_skip_popup_after_locking.assert_not_called()
                    runner._meow_skip_lock.assert_not_called()

    def test_unknown_rarity_protects_gold_and_purple_even_with_feed_advice(self):
        for rarity in ('gold', 'purple'):
            with self.subTest(rarity=rarity):
                fixture = self.make_fixture(rarity, ('炮击新手·主力', '装填新手·战列'))
                fixture.runner._meow_get_rarity.side_effect = None
                fixture.runner._meow_get_rarity.return_value = None
                fixture.runner.meow_should_lock_by_advice = Mock(
                    side_effect=AssertionError('品质未知不应按低分放行'),
                )
                runner = fixture.run()
                self.assertTrue(fixture.locked)
                runner._meow_apply_lock.assert_called_once_with(lock=True)
                runner._meow_skip_lock.assert_not_called()
                runner.meow_should_lock_by_advice.assert_not_called()

    def test_real_slot_scanning_counts_only_nonempty_slots(self):
        runner = MeowfficerCollect.__new__(MeowfficerCollect)
        runner.config = SimpleNamespace(
            MeowfficerTrain_LockByAdvice=True, MeowfficerTrain_ScoreTalents=False,
            DropRecord_MeowfficerTalent='do_not',
        )
        runner.device = SimpleNamespace(image=object())
        runner._meow_detect_shift = Mock(return_value=False)
        runner.meow_score_reset = Mock()
        runner.meow_score_finish = Mock()
        runner._meow_talent_cap_handle = Mock()
        buttons = collect_module.MEOWFFICER_TALENT_GRID_1.buttons

        def color_count(button, **kwargs):
            if button is buttons[2]:
                return True
            return kwargs['count'] == 25 and button is buttons[0]

        runner.image_color_count = Mock(side_effect=color_count)
        drop = SimpleNamespace(add=Mock())
        self.assertTrue(runner._meow_is_special_talented(drop=drop))
        self.assertEqual(runner._meow_talent_cap_handle.call_count, 2)
        runner.meow_score_reset.assert_called_once_with()
        runner.meow_score_finish.assert_called_once_with(expected_talents=2)
        drop.add.assert_not_called()

    def test_multiple_cats_with_next_prelocked_gold_are_each_scored(self):
        for first_rarity in ('blue', 'purple'):
            for next_keep in (False, True):
                with self.subTest(first=first_rarity, next_keep=next_keep):
                    fixture = CollectQueueFixture([
                        dict(rarity=first_rarity, names=('侵略如火', '炮击新手·主力')),
                        dict(rarity='gold', locked=True, names=(
                            ('侵略如火', '炮术长·主力') if next_keep
                            else ('炮击新手·主力', '装填新手·战列')
                        )),
                        dict(rarity='purple', names=('侵略如火', '炮术长·主力')),
                    ])
                    fixture.run()
                    self.assertEqual(fixture.scored, [1, 2] if first_rarity == 'blue' else [0, 1, 2])
                    self.assertEqual(fixture.finished, [(0, first_rarity != 'blue'), (1, next_keep), (2, True)])

    def test_gold_lock_confirmation_is_not_a_second_new_cat(self):
        fixture = CollectQueueFixture([
            dict(rarity='gold', names=('侵略如火', '炮术长·主力')),
            dict(rarity='purple', names=('炮击新手·主力', '装填新手·战列')),
        ])
        fixture.run()
        self.assertEqual(fixture.scored, [0, 1])
        self.assertEqual(fixture.finished, [(0, True), (1, False)])
        fixture.runner._meow_skip_popup_after_locking.assert_not_called()
        self.assertEqual(
            sum(action == ('click', collect_module.MEOWFFICER_CONFIRM.name) for action in fixture.actions),
            1,
        )

    def test_gold_confirmation_then_next_prelocked_gold_feed_is_scored(self):
        fixture = CollectQueueFixture([
            dict(rarity='gold', names=('侵略如火', '炮术长·主力')),
            dict(rarity='gold', locked=True, names=('炮击新手·主力', '装填新手·战列')),
        ])
        fixture.run()
        self.assertEqual(fixture.scored, [0, 1])
        self.assertEqual(fixture.finished, [(0, True), (1, False)])
        fixture.runner._meow_skip_popup_after_locking.assert_not_called()

    def test_confirmation_animation_with_both_buttons_and_cooldown_does_not_rescore_background(self):
        fixture = DelayedConfirmQueueFixture([
            dict(rarity='gold', names=('侵略如火', '炮术长·主力')),
            dict(rarity='gold', locked=True, names=('炮击新手·主力', '装填新手·战列')),
        ])
        fixture.run()
        self.assertEqual(fixture.scored, [0, 1])
        self.assertEqual(fixture.finished, [(0, True), (1, False)])
        self.assertEqual(fixture.animation_frames, 2)
        self.assertEqual(fixture.blocked_clicks, 1)
        self.assertEqual(
            sum(action == ('click', collect_module.MEOWFFICER_CONFIRM.name) for action in fixture.actions),
            1,
        )
        fixture.runner._meow_skip_popup_after_locking.assert_not_called()
        fixture.runner.device.sleep.assert_called_once_with(0.1)

    def test_previous_feed_result_does_not_cause_next_blue_to_be_scored(self):
        fixture = CollectQueueFixture([
            dict(rarity='purple', names=('炮击新手·主力', '装填新手·战列')),
            dict(rarity='blue', names=(), expected=1),
        ])
        fixture.run()
        self.assertEqual(fixture.scored, [0])
        self.assertEqual(fixture.finished, [(0, False), (1, False)])

    def test_previous_failed_purple_result_does_not_protect_lock_next_blue(self):
        fixture = CollectQueueFixture([
            dict(rarity='purple', names=(), expected=1),
            dict(rarity='blue', locked=True, names=(), expected=1),
            dict(rarity='gold', names=('侵略如火', '炮术长·主力')),
        ])
        fixture.run()
        self.assertEqual(fixture.scored, [0, 2])
        self.assertEqual(fixture.finished, [(0, True), (1, False), (2, True)])


if __name__ == '__main__':
    unittest.main()
