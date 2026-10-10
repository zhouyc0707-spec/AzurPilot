"""战术学院提示处理后的流程回归，不读取真实配置或操作游戏。"""

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.tactical.assets import TACTICAL_CLASS_START
from module.tactical.tactical_class import RewardTacticalClass
from module.ui.assets import REWARD_CHECK


class TacticalScenario:
    """用页面状态序列重放提示、教材、课程与奖励页的衔接。"""

    def __init__(self, frames):
        self.frames = frames
        self.index = 0
        self.events = []
        self.finish_time = datetime(2026, 10, 11, 11, 24, 56)
        self.instance = object.__new__(RewardTacticalClass)
        self.instance.config = SimpleNamespace(
            AddNewStudent_Enable=True,
            Scheduler_ServerUpdate='00:00',
            task_delay=Mock(),
        )
        self.instance.device = SimpleNamespace(screenshot=Mock(side_effect=self.screenshot))
        self.instance.tactical_finish = []
        self.instance.pending_ship_confirm = True
        self.instance.dock_select_index = 2
        self.instance.ui_ensure = Mock()
        self.instance.appear = Mock(side_effect=self.appear)
        self.instance._handle_tactical_add_new_student = Mock(return_value=False)
        self.instance.handle_rapid_training = Mock(return_value=False)
        self.instance._handle_tactical_finish = Mock(side_effect=self.handle_finish)
        self.instance._handle_tactical_popups = Mock(return_value=False)
        self.instance._handle_tactical_books_start = Mock(side_effect=self.handle_books)
        self.instance.handle_game_tips = Mock(side_effect=self.handle_tips)
        self.instance._handle_tactical_dock = Mock(return_value=(False, False))
        self.instance._handle_tactical_skill_confirm = Mock(return_value=(False, False))
        self.instance._handle_tactical_meta = Mock(return_value=False)
        self.instance._handle_tactical_book_empty = Mock(side_effect=self.handle_book_empty)

    @property
    def frame(self):
        return self.frames[self.index]

    def screenshot(self):
        self.index += 1
        if self.index >= len(self.frames):
            raise AssertionError('未在奖励页正常结束流程')
        self.events.append(('screenshot', self.frame))

    def appear(self, button, **kwargs):
        return button is REWARD_CHECK and self.frame == 'reward'

    def handle_tips(self):
        if self.frame != 'tips':
            return False
        self.events.append(('close_tips', self.frame))
        return True

    def handle_books(self):
        if self.frame != 'books':
            return False, False
        self.events.append(('choose_book', TACTICAL_CLASS_START.name))
        return True, False

    def handle_finish(self, book_empty, empty_confirm):
        if self.frame != 'courses':
            return False, False
        if book_empty:
            return True, False
        self.instance.tactical_finish = [self.finish_time]
        self.events.append(('read_finish', self.frame))
        return True, True

    def handle_book_empty(self):
        return self.frame == 'empty'


class TestTacticalGameTips(unittest.TestCase):
    def test_tips_resume_books_and_schedule_actual_course_time(self):
        scenario = TacticalScenario(['tips', 'books', 'courses', 'reward'])

        scenario.instance.run()

        self.assertEqual(scenario.frame, 'reward')
        self.assertEqual(scenario.events, [
            ('close_tips', 'tips'),
            ('screenshot', 'books'),
            ('choose_book', TACTICAL_CLASS_START.name),
            ('screenshot', 'courses'),
            ('read_finish', 'courses'),
            ('screenshot', 'reward'),
        ])
        scenario.instance.config.task_delay.assert_called_once_with(target=[scenario.finish_time])
        self.assertTrue(scenario.instance.pending_ship_confirm)
        self.assertEqual(scenario.instance.dock_select_index, 2)

    def test_repeated_tips_require_fresh_screenshots_before_continuing(self):
        scenario = TacticalScenario(['tips', 'tips', 'books', 'courses', 'reward'])
        timer = Mock()

        with patch('module.tactical.tactical_class.Timer', return_value=timer):
            timer.start.return_value = timer
            scenario.instance.tactical_class_receive()

        self.assertEqual(scenario.frame, 'reward')
        self.assertEqual(scenario.events[:4], [
            ('close_tips', 'tips'),
            ('screenshot', 'tips'),
            ('close_tips', 'tips'),
            ('screenshot', 'books'),
        ])
        self.assertEqual(timer.reset.call_count, 2)
        scenario.instance._handle_tactical_dock.assert_not_called()
        scenario.instance._handle_tactical_skill_confirm.assert_not_called()

    def test_normal_course_flow_without_tips_still_returns_to_reward(self):
        scenario = TacticalScenario(['books', 'courses', 'reward'])

        scenario.instance.run()

        self.assertEqual(scenario.frame, 'reward')
        scenario.instance.config.task_delay.assert_called_once_with(target=[scenario.finish_time])
        scenario.instance.handle_game_tips.assert_not_called()

    def test_tips_then_empty_books_keep_next_server_update_schedule(self):
        scenario = TacticalScenario(['tips', 'empty', 'courses', 'reward'])
        server_update = datetime(2026, 10, 12)

        with patch('module.tactical.tactical_class.get_server_next_update', return_value=server_update) as update:
            scenario.instance.run()

        self.assertEqual(scenario.frame, 'reward')
        update.assert_called_once_with('00:00')
        scenario.instance.config.task_delay.assert_called_once_with(target=server_update)

    def test_explicit_first_screenshot_does_not_reuse_previous_page(self):
        scenario = TacticalScenario(['stale', 'tips', 'books', 'courses', 'reward'])

        scenario.instance.tactical_class_receive(skip_first_screenshot=False)

        self.assertEqual(scenario.frame, 'reward')
        self.assertEqual(scenario.events[0], ('screenshot', 'tips'))


if __name__ == '__main__':
    unittest.main()
