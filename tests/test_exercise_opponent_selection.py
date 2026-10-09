"""使用内存战斗结果核对演习选敌顺序，不连接设备或加载用户配置。"""

import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from module.exercise.exercise import Exercise
from module.exercise.opponent import Opponent, OpponentChoose


class ExerciseFixture(Exercise):
    """保留真实选敌、尝试和刷新流程，只替换游戏界面操作。"""

    def __init__(self, *, refreshed=0, mode='leftmost', trial=2, outcomes=None):
        self.config = SimpleNamespace(
            Exercise_OpponentChooseMode=mode,
            Exercise_OpponentTrial=trial,
            set_record=Mock(),
        )
        self.opponent_change_count = refreshed
        self.events = []
        self.attempts = []
        self.selected = None
        self.outcomes = {key: deque(values) for key, values in (outcomes or {}).items()}
        self.opponents = []
        for index, (level, power) in enumerate(((100, 32000), (60, 12000), (120, 40000), (80, 18000))):
            opponent = object.__new__(Opponent)
            opponent.index = index
            opponent.level = [level] * 6
            opponent.power = [power // 2, power // 2]
            self.opponents.append(opponent)

    def _choose_opponent(self, index, skip_first_screenshot=True):
        self.selected = int(index)
        self.events.append(('choose', self.opponent_change_count, self.selected))

    def _combat_preparation(self, skip_first_screenshot=True):
        self.events.append(('start', self.opponent_change_count, self.selected))

    def _combat_execute(self):
        key = (self.opponent_change_count, self.selected)
        self.attempts.append(key)
        results = self.outcomes.get(key)
        result = results.popleft() if results else False
        self.events.append(('complete' if result else 'retreat', *key))
        return result

    def _preparation_quit(self):
        self.events.append(('leave_preparation', self.opponent_change_count, self.selected))

    def appear_then_click(self, button, **kwargs):
        self.events.append(('refresh', self.opponent_change_count + 1))
        return True

    def ensure_no_info_bar(self, timeout):
        pass


class ExerciseOpponentSelectionTests(unittest.TestCase):
    def setUp(self):
        # 仅用合成对手数据，禁止走舰队截图与 OCR 的真实入口。
        self.fleet_check = self.enterContext(
            patch.object(OpponentChoose, '_opponent_fleet_check_all', autospec=True)
        )

    def test_second_attempt_is_same_opponent_and_success_does_not_refresh(self):
        exercise = ExerciseFixture(outcomes={(0, 0): [False, True]})

        self.assertTrue(exercise._exercise_once())

        self.assertEqual(exercise.events, [
            ('choose', 0, 0),
            ('start', 0, 0), ('retreat', 0, 0),
            ('start', 0, 0), ('complete', 0, 0),
        ])
        exercise.config.set_record.assert_not_called()
        self.fleet_check.assert_not_called()

    def test_two_retreats_return_to_home_and_refresh_before_selecting_right(self):
        exercise = ExerciseFixture(outcomes={(1, 0): [True]})

        self.assertTrue(exercise._exercise_once())

        self.assertEqual(exercise.events, [
            ('choose', 0, 0),
            ('start', 0, 0), ('retreat', 0, 0),
            ('start', 0, 0), ('retreat', 0, 0),
            ('leave_preparation', 0, 0), ('refresh', 1),
            ('choose', 1, 0), ('start', 1, 0), ('complete', 1, 0),
        ])
        self.assertEqual(exercise.opponent_change_count, 1)
        exercise.config.set_record.assert_called_once_with(Exercise_OpponentRefreshValue=1)

    def test_zero_refreshes_try_only_left_until_five_then_all_last_group(self):
        exercise = ExerciseFixture()

        self.assertFalse(exercise._exercise_once())

        self.assertEqual(exercise.attempts, [
            (0, 0), (0, 0), (1, 0), (1, 0), (2, 0), (2, 0),
            (3, 0), (3, 0), (4, 0), (4, 0), (5, 0), (5, 0),
            (5, 1), (5, 1), (5, 2), (5, 2), (5, 3), (5, 3),
        ])
        self.assertEqual(exercise.opponent_change_count, 5)
        self.assertEqual(exercise.config.set_record.call_args_list, [
            call(Exercise_OpponentRefreshValue=count) for count in (1, 2, 3, 4, 5)
        ])
        self.fleet_check.assert_not_called()

    def test_four_refreshes_use_last_refresh_before_trying_right(self):
        exercise = ExerciseFixture(refreshed=4)

        self.assertFalse(exercise._exercise_once())

        self.assertEqual(exercise.attempts, [
            (4, 0), (4, 0), (5, 0), (5, 0),
            (5, 1), (5, 1), (5, 2), (5, 2), (5, 3), (5, 3),
        ])
        self.assertEqual(exercise.opponent_change_count, 5)
        exercise.config.set_record.assert_called_once_with(Exercise_OpponentRefreshValue=5)

    def test_five_refreshes_immediately_fall_back_to_right_without_refresh(self):
        exercise = ExerciseFixture(refreshed=5, outcomes={(5, 1): [True]})

        self.assertTrue(exercise._exercise_once())

        self.assertEqual(exercise.attempts, [(5, 0), (5, 0), (5, 1)])
        self.assertEqual(exercise.opponent_change_count, 5)
        exercise.config.set_record.assert_not_called()

    def test_exhausted_refresh_count_above_five_also_does_not_refresh(self):
        exercise = ExerciseFixture(refreshed=6)

        self.assertFalse(exercise._exercise_once())

        self.assertEqual(exercise.attempts, [
            (6, 0), (6, 0), (6, 1), (6, 1), (6, 2), (6, 2), (6, 3), (6, 3),
        ])
        exercise.config.set_record.assert_not_called()

    def test_right_opponent_exhausts_two_attempts_before_moving_further_right(self):
        exercise = ExerciseFixture(refreshed=5, outcomes={(5, 2): [False, True]})

        self.assertTrue(exercise._exercise_once())

        self.assertEqual(exercise.attempts, [(5, 0), (5, 0), (5, 1), (5, 1), (5, 2), (5, 2)])
        self.assertEqual([event for event in exercise.events if event[0] == 'leave_preparation'], [
            ('leave_preparation', 5, 0), ('leave_preparation', 5, 1),
        ])
        exercise.config.set_record.assert_not_called()

    def test_natural_completion_true_does_not_retry_or_refresh(self):
        exercise = ExerciseFixture(outcomes={(0, 0): [True]})

        self.assertTrue(exercise._exercise_once())

        # 胜败自然结算均沿用战斗执行器的 True 合同，不能当作低血量退出重试。
        self.assertEqual(exercise.attempts, [(0, 0)])
        self.assertEqual(exercise.events, [('choose', 0, 0), ('start', 0, 0), ('complete', 0, 0)])
        exercise.config.set_record.assert_not_called()

    def test_refresh_record_accumulates_across_completed_exercises(self):
        exercise = ExerciseFixture(outcomes={(1, 0): [True, False, False], (2, 0): [True]})

        self.assertTrue(exercise._exercise_once())
        self.assertEqual(exercise.opponent_change_count, 1)
        self.assertTrue(exercise._exercise_once())

        self.assertEqual(exercise.attempts, [(0, 0), (0, 0), (1, 0), (1, 0), (1, 0), (2, 0)])
        self.assertEqual(exercise.opponent_change_count, 2)
        self.assertEqual(exercise.config.set_record.call_args_list, [
            call(Exercise_OpponentRefreshValue=1), call(Exercise_OpponentRefreshValue=2),
        ])

    def test_configured_trial_count_is_respected_before_refresh(self):
        for trial in (1, 3):
            with self.subTest(trial=trial):
                exercise = ExerciseFixture(trial=trial, outcomes={(1, 0): [True]})

                self.assertTrue(exercise._exercise_once())

                self.assertEqual(exercise.attempts, [(0, 0)] * trial + [(1, 0)])
                exercise.config.set_record.assert_called_once_with(Exercise_OpponentRefreshValue=1)

    def test_other_modes_keep_their_ranked_order_before_refresh(self):
        for mode, order in (('max_exp', (2, 0, 3, 1)), ('easiest', (1, 3, 0, 2))):
            with self.subTest(mode=mode):
                exercise = ExerciseFixture(mode=mode, outcomes={(1, order[0]): [True]})
                self.fleet_check.reset_mock()

                self.assertTrue(exercise._exercise_once())

                self.assertEqual(exercise.attempts, [
                    (0, opponent) for opponent in order for _ in range(2)
                ] + [(1, order[0])])
                exercise.config.set_record.assert_called_once_with(Exercise_OpponentRefreshValue=1)
                self.assertEqual(self.fleet_check.call_count, 2)


if __name__ == '__main__':
    unittest.main()
