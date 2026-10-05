"""验证侵蚀一战后主舰队雷达预检及原有补查，不连接游戏或真实配置。"""

import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.config.config import TaskEnd
from module.exception import GameStuckError, GameTooManyClickError
from module.os.map import OSMap
from module.os.operation_siren import OperationSiren


class TestHazard1RadarPrecheck(unittest.TestCase):
    def setUp(self):
        self.primary_clear = self.enterContext(
            patch.object(OSMap, 'clear_question', autospec=True, side_effect=self.clear_question)
        )
        self.akashi_record = self.enterContext(
            patch('module.os.tasks.hazard_leveling.record_cl1_akashi_encounter')
        )
        self.enterContext(patch('module.base.debug_clip.cleanup_clips_if_due'))
        self.enterContext(patch('module.base.debug_clip.clip_recording', return_value=nullcontext()))
        self.make_runner()

    def make_runner(self):
        # 使用实际组合类，确保预检不会误调用侵蚀一的多舰队 clear_question。
        self.runner = OperationSiren.__new__(OperationSiren)
        self.runner.config = SimpleNamespace(
            OpsiFleet_Fleet=1,
            OpsiHazard1Leveling_DebugClip=False,
        )
        self.runner.zone = SimpleNamespace(is_port=False)
        self.actions = []
        self.fresh_image = object()
        self.runner.device = SimpleNamespace(
            image=object(), screenshot=Mock(side_effect=self.screenshot)
        )
        self.runner.radar = SimpleNamespace(
            predict_question=Mock(side_effect=self.predict_question)
        )
        self.runner.run_strategic_search = Mock(side_effect=self.strategic_search)
        self.runner.fleet_set = Mock(side_effect=lambda fleet: self.actions.append(('fleet', fleet)))
        self.runner.map_rescan = Mock(side_effect=self.map_rescan)
        self.runner._forced_move_enabled = Mock(return_value=True)
        self.runner._execute_fixed_patrol_scan = Mock(
            side_effect=lambda **kwargs: self.actions.append('forced_scan')
        )
        self.runner.handle_after_auto_search = Mock(
            side_effect=lambda: self.actions.append('after_search')
        )
        self.runner.clear_question = Mock(
            side_effect=AssertionError('主队预检不应调用多舰队清理')
        )
        self.question = None
        self.clear_event = None
        self.scan_event = None
        self.search_completed = True
        self.primary_clear.reset_mock()
        self.akashi_record.reset_mock()

    def strategic_search(self):
        self.actions.append('strategic_search')
        return self.search_completed

    def screenshot(self):
        self.actions.append('screenshot')
        self.runner.device.image = self.fresh_image

    def predict_question(self, image, in_port):
        self.assertIs(image, self.fresh_image)
        self.assertFalse(in_port)
        self.actions.append('radar')
        return self.question

    def clear_question(self, runner, drop=None):
        self.assertIs(runner, self.runner)
        self.actions.append('primary_clear')
        if self.clear_event:
            runner._solved_map_event.add(self.clear_event)
            return True
        return False

    def map_rescan(self):
        self.actions.append('map_rescan')
        if self.scan_event:
            self.runner._solved_map_event.add(self.scan_event)

    def run_battle(self):
        self.runner._cl1_run_battle()
        self.runner.clear_question.assert_not_called()
        self.runner.handle_after_auto_search.assert_called_once_with()

    def test_no_question_keeps_full_scan_and_existing_forced_scan(self):
        """雷达无问号时照常全图扫描，并由原开关决定后续补查。"""
        for enabled in (False, True):
            with self.subTest(forced_move=enabled):
                self.make_runner()
                self.runner._forced_move_enabled.return_value = enabled
                self.run_battle()
                self.primary_clear.assert_not_called()
                self.runner.map_rescan.assert_called_once_with()
                self.assertLess(self.actions.index('radar'), self.actions.index('map_rescan'))
                if enabled:
                    self.runner._execute_fixed_patrol_scan.assert_called_once_with(
                        ExecuteFixedPatrolScan=True
                    )
                else:
                    self.runner._execute_fixed_patrol_scan.assert_not_called()

    def test_successful_primary_event_skips_full_scan_and_forced_scan(self):
        """主队处理到目标即结束检索，强制移动关闭时也先清主队问号。"""
        for event in ('is_akashi', 'is_logging_tower', 'is_scanning_device'):
            for enabled in (False, True):
                with self.subTest(event=event, forced_move=enabled):
                    self.make_runner()
                    self.question = (0, -1)
                    self.clear_event = event
                    self.runner._forced_move_enabled.return_value = enabled
                    self.run_battle()
                    self.primary_clear.assert_called_once_with(self.runner)
                    self.runner.map_rescan.assert_not_called()
                    self.runner._execute_fixed_patrol_scan.assert_not_called()
                    self.assertEqual(self.runner._solved_map_event, {event})
                    if event == 'is_akashi':
                        self.akashi_record.assert_called_once_with(self.runner.config)
                    else:
                        self.akashi_record.assert_not_called()

    def test_failed_primary_clear_falls_back_to_full_scan(self):
        """主队看得到但清不掉时保留全图扫描，扫描成功后停止额外补查。"""
        self.question = (0, -1)
        self.scan_event = 'is_akashi'
        self.run_battle()
        self.primary_clear.assert_called_once_with(self.runner)
        self.runner.map_rescan.assert_called_once_with()
        self.runner._execute_fixed_patrol_scan.assert_not_called()
        self.assertLess(self.actions.index('primary_clear'), self.actions.index('map_rescan'))
        self.akashi_record.assert_called_once_with(self.runner.config)

    def test_unresolved_question_keeps_original_forced_scan(self):
        """主队清理与全图扫描都失败时，仍进入原有多舰队及挪队兜底。"""
        self.question = (0, -1)
        self.run_battle()
        self.runner.map_rescan.assert_called_once_with()
        self.runner._execute_fixed_patrol_scan.assert_called_once_with(ExecuteFixedPatrolScan=True)
        self.assertLess(self.actions.index('map_rescan'), self.actions.index('forced_scan'))

    def test_precheck_uses_fresh_frame_of_configured_primary_and_resets_events(self):
        """先恢复指定主队再截图，上一轮的已处理记录不能挡住本轮补查。"""
        self.runner.config.OpsiFleet_Fleet = 3
        self.runner._solved_map_event = {'is_akashi'}
        self.runner._solved_fleet_mechanism = True
        self.run_battle()
        self.assertEqual(
            self.actions[:4], ['strategic_search', ('fleet', 3), 'screenshot', 'radar']
        )
        self.runner.fleet_set.assert_called_once_with(3)
        self.runner.device.screenshot.assert_called_once_with()
        self.runner.radar.predict_question.assert_called_once_with(self.fresh_image, in_port=False)
        self.assertEqual(self.runner._solved_map_event, set())
        self.assertFalse(self.runner._solved_fleet_mechanism)
        self.runner.map_rescan.assert_called_once_with()
        self.akashi_record.assert_not_called()

    def test_recovery_and_task_switch_exceptions_propagate(self):
        """清理中的恢复异常与任务切换由上层处理，不吞异常继续点地图。"""
        self.question = (0, -1)
        for exception in (GameStuckError, GameTooManyClickError, TaskEnd):
            with self.subTest(exception=exception.__name__):
                self.primary_clear.side_effect = exception('测试中断')
                with self.assertRaises(exception):
                    self.runner._cl1_run_battle()
        self.runner.map_rescan.assert_not_called()
        self.runner._execute_fixed_patrol_scan.assert_not_called()
        self.runner.handle_after_auto_search.assert_not_called()

    def test_interrupted_search_keeps_postbattle_precheck_and_scan(self):
        """战略搜索返回 False 时沿用原来的战后补查行为。"""
        self.search_completed = False
        self.run_battle()
        self.runner.radar.predict_question.assert_called_once_with(self.fresh_image, in_port=False)
        self.runner.map_rescan.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
