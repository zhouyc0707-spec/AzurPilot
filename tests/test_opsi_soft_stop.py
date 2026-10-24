"""用伪帧与假时钟验证侵蚀一温柔停止停在地图，不连接游戏或真实配置。"""

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.config.config import TaskEnd
from module.os.map import OSMap
from module.os_handler.assets import (
    AUTO_SEARCH_OS_MAP_OPTION_OFF,
    AUTO_SEARCH_OS_MAP_OPTION_OFF_DISABLED,
    AUTO_SEARCH_OS_MAP_OPTION_ON,
    AUTO_SEARCH_REWARD,
)
from module.ui.assets import GOTO_MAIN


class MapStopFrames:
    """真实退出状态机使用的有限画面序列，截图推进时钟，点击受真实间隔约束。"""

    def __init__(self, frames):
        self.frames = list(frames)
        self.index = 0
        self.now = 100.0
        self.actions = []
        self.intervals = {}
        self.stopped_at = None
        handler = self.handler = OSMap.__new__(OSMap)
        handler.config = SimpleNamespace(task_stop=Mock(side_effect=self.stop))
        handler.device = SimpleNamespace(
            image=(0, self.frames[0]), screenshot=self.screenshot, click=self.click,
        )
        handler.is_in_main = lambda: self.frame == 'main'
        handler.is_in_map = lambda: self.frame not in ('combat', 'quit', 'reconfirm', 'loading', 'main')
        handler.appear = Mock(side_effect=self.appear)
        handler.match_template_color = Mock(side_effect=self.appear)
        handler.appear_then_click = Mock(side_effect=self.appear_then_click)
        handler.interval_clear = Mock(side_effect=lambda button: self.intervals.pop(button, None))
        handler.interval_reset = Mock(side_effect=lambda button: self.intervals.update({button: self.now}))
        handler.is_combat_executing = lambda: 'pause' if self.frame == 'combat' else False
        handler.handle_combat_quit = lambda: self.handle('quit')
        handler.handle_combat_quit_reconfirm = lambda: self.handle('reconfirm')
        handler.ui_additional = lambda: self.handle('ui')
        handler.handle_map_event = lambda **kwargs: self.handle('popup')
        handler.is_combat_loading = lambda: self.frame == 'loading'
        handler.handle_battle_status = Mock(return_value=False)
        handler.handle_exp_info = Mock(return_value=False)

    @property
    def frame(self):
        return self.frames[self.index]

    def screenshot(self):
        self.index += 1
        self.now += .25
        if self.index >= len(self.frames):
            raise AssertionError('退出状态机耗尽伪帧，未在确认自律关闭后结束')
        self.handler.device.image = (self.index, self.frame)

    def click(self, button):
        self.actions.append((self.index, button))

    def handle(self, kind):
        if self.frame != kind:
            return False
        self.actions.append((self.index, kind))
        return True

    def appear(self, button, *, interval=0, **kwargs):
        matches = {
            AUTO_SEARCH_REWARD: self.frame == 'reward',
            AUTO_SEARCH_OS_MAP_OPTION_ON: self.frame in ('on', 'reward', 'ambiguous'),
            AUTO_SEARCH_OS_MAP_OPTION_OFF: self.frame in ('off', 'reward', 'popup', 'ui', 'ambiguous'),
            AUTO_SEARCH_OS_MAP_OPTION_OFF_DISABLED: self.frame == 'disabled',
            GOTO_MAIN: self.frame in ('off', 'on', 'disabled'),
        }
        if not matches.get(button, False):
            return False
        if interval:
            if self.now - self.intervals.get(button, float('-inf')) < interval:
                return False
            self.intervals[button] = self.now
        return True

    def appear_then_click(self, button, **kwargs):
        if self.appear(button, **kwargs):
            self.click(button)
            return True
        return False

    def stop(self):
        self.stopped_at = self.index
        raise TaskEnd


class OpsiSoftStopMapTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.os.map.logger'))

    def run_frames(self, frames, *, end_task=True):
        state = MapStopFrames(frames)
        drop = Mock()
        with patch('module.base.timer.time', side_effect=lambda: state.now):
            if end_task:
                with self.assertRaises(TaskEnd):
                    state.handler.interrupt_auto_search(goto_main=False, drop=drop)
            else:
                state.handler.interrupt_auto_search(goto_main=False, end_task=False, drop=drop)
        self.assertFalse(any(button is GOTO_MAIN for _, button in state.actions))
        return state, drop

    def test_already_off_still_requires_stable_positive_map_confirmation(self):
        for off in ('off', 'disabled'):
            with self.subTest(off=off):
                state, _ = self.run_frames([off] * 9)
                self.assertGreaterEqual(state.stopped_at, 6)
                self.assertEqual(state.actions, [])

    def test_pause_quit_reconfirm_reward_and_search_off_before_stopping(self):
        frames = ['combat', 'quit', 'reconfirm'] + ['reward'] * 8 + ['on'] * 3 + ['off'] * 9
        state, drop = self.run_frames(frames)
        self.assertEqual([button for _, button in state.actions], [
            'pause', 'quit', 'reconfirm', AUTO_SEARCH_REWARD, AUTO_SEARCH_OS_MAP_OPTION_ON,
        ])
        drop.add.assert_called_once_with((3, 'reward'))
        self.assertGreaterEqual(state.stopped_at, frames.index('off') + 6)

    def test_reward_overlay_blocks_map_completion_and_click_through_during_cooldown(self):
        frames = ['off'] * 4 + ['reward'] * 8 + ['off'] * 9
        state, drop = self.run_frames(frames)
        self.assertEqual(state.actions, [(4, AUTO_SEARCH_REWARD)])
        drop.add.assert_called_once_with((4, 'reward'))
        self.assertGreaterEqual(state.stopped_at, 12 + 6)

    def test_uncertain_or_handled_pages_reset_stable_off_confirmation(self):
        for obstacle in ('unknown', 'ambiguous', 'popup', 'ui', 'loading'):
            with self.subTest(obstacle=obstacle):
                state, _ = self.run_frames(['off'] * 4 + [obstacle] + ['off'] * 9)
                self.assertGreaterEqual(state.stopped_at, 5 + 6)
                self.assertFalse(any(button is AUTO_SEARCH_OS_MAP_OPTION_ON
                                     for _, button in state.actions))

    def test_search_on_cooldown_does_not_allow_early_completion(self):
        state, _ = self.run_frames(['on'] * 8 + ['off'] * 9)
        self.assertEqual(state.actions, [(0, AUTO_SEARCH_OS_MAP_OPTION_ON)])
        self.assertGreaterEqual(state.stopped_at, 8 + 6)

    def test_end_task_false_returns_on_confirmed_map_without_task_stop(self):
        state, _ = self.run_frames(['reward', 'on'] + ['disabled'] * 9, end_task=False)
        state.handler.config.task_stop.assert_not_called()
        self.assertGreaterEqual(state.index, 2 + 6)

    def test_default_interruption_retains_go_to_main(self):
        state = MapStopFrames(['off', 'main'])
        with patch('module.base.timer.time', side_effect=lambda: state.now):
            with self.assertRaises(TaskEnd):
                state.handler.interrupt_auto_search()
        self.assertEqual(state.actions, [(0, GOTO_MAIN)])

    def test_default_interruption_with_existing_cl1_stop_changes_destination(self):
        state = MapStopFrames(['off'] * 9)
        stop_event = threading.Event()
        stop_event.set()
        state.handler.config.task = SimpleNamespace(command='OpsiHazard1Leveling')
        state.handler.config.stop_event = stop_event
        with patch('module.base.timer.time', side_effect=lambda: state.now):
            with self.assertRaises(TaskEnd):
                state.handler.interrupt_auto_search()
        self.assertEqual(state.actions, [])
        self.assertGreaterEqual(state.stopped_at, 6)

    def test_stop_arriving_during_default_interruption_changes_destination(self):
        frames = ['combat', 'quit', 'reconfirm', 'reward', 'on'] + ['off'] * 9
        state = MapStopFrames(frames)
        stop_event = threading.Event()
        state.handler.config.task = SimpleNamespace(command='OpsiScheduling')
        state.handler.config._bind_task_override = 'OpsiHazard1Leveling'
        state.handler.config.stop_event = stop_event

        def screenshot():
            state.screenshot()
            if state.index == 1:
                stop_event.set()

        state.handler.device.screenshot = screenshot
        with patch('module.base.timer.time', side_effect=lambda: state.now):
            with self.assertRaises(TaskEnd):
                state.handler.interrupt_auto_search()
        self.assertEqual([button for _, button in state.actions], [
            'pause', 'quit', 'reconfirm', AUTO_SEARCH_REWARD, AUTO_SEARCH_OS_MAP_OPTION_ON,
        ])
        self.assertGreaterEqual(state.stopped_at, frames.index('off') + 6)


class OpsiSoftStopDispatchTests(unittest.TestCase):
    def make_handler(self, task, *, bound=None, stopped=True, combat=False, switched=False):
        handler = OSMap.__new__(OSMap)
        stop_event = threading.Event()
        if stopped:
            stop_event.set()
        handler.config = SimpleNamespace(
            task=SimpleNamespace(command=task), stop_event=stop_event,
            OpsiGeneral_AutoSearchTimeLimit=10,
            task_switched=Mock(return_value=switched),
        )
        if bound:
            handler.config._bind_task_override = bound
        handler.device = Mock()
        handler.loop = Mock(return_value=range(1))
        handler.on_auto_search_battle_count_reset = Mock()
        handler.on_auto_search_battle_count_add = Mock()
        handler.hp_reset = Mock()
        handler.handle_background_coin_interrupt = Mock()
        handler.is_in_map = Mock(return_value=not combat)
        handler.appear = Mock(return_value=True)
        handler.match_template_color = Mock(return_value=False)
        handler.handle_os_auto_search_map_option = Mock(return_value=False)
        handler.handle_retirement = Mock(return_value=False)
        handler.combat_appear = Mock(return_value=combat)
        handler.handle_map_event = Mock(return_value=False)
        handler.interrupt_auto_search = Mock(side_effect=TaskEnd)
        return handler

    def test_direct_and_proxy_cl1_take_over_before_combat_on_soft_stop(self):
        for task, bound in (
            ('OpsiHazard1Leveling', None),
            ('OpsiScheduling', 'OpsiHazard1Leveling'),
            ('OpsiPreventActionPointOverflow', 'OpsiHazard1Leveling'),
        ):
            for combat in (False, True):
                with self.subTest(task=task, combat=combat):
                    handler = self.make_handler(task, bound=bound, combat=combat)
                    drop = Mock()
                    with self.assertRaises(TaskEnd):
                        handler.os_auto_search_daemon(drop=drop)
                    handler.interrupt_auto_search.assert_called_once_with(goto_main=False, drop=drop)
                    handler.handle_os_auto_search_map_option.assert_not_called()
                    handler.combat_appear.assert_not_called()

    def test_non_cl1_soft_stop_keeps_original_main_destination(self):
        for task in ('OpsiObscure', 'OpsiMeowfficerFarming'):
            with self.subTest(task=task):
                handler = self.make_handler(task, combat=True)
                drop = Mock()
                with self.assertRaises(TaskEnd):
                    handler.os_auto_search_daemon(drop=drop)
                handler.interrupt_auto_search.assert_called_once_with(drop=drop)

    def test_normal_strategic_preemption_still_returns_main(self):
        for task in ('OpsiHazard1Leveling', 'OpsiObscure'):
            with self.subTest(task=task):
                handler = self.make_handler(task, stopped=False, combat=True, switched=True)
                drop = Mock()
                with self.assertRaises(TaskEnd):
                    handler.os_auto_search_daemon(drop=drop, strategic=True)
                handler.interrupt_auto_search.assert_called_once_with(drop=drop)


if __name__ == '__main__':
    unittest.main()
