"""开发计划页面检查不确定时保留失败，并验证每日周任务的离线调度。"""

import unittest
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import module.base.timer as timer_module
from module.exception import GameStuckError
from module.island.island_daily_interact import IslandDailyInteract
from module.island_daily_interact.assets import (
    DEVELOPMENT_PLAN_DAILY_TAB,
    DEVELOPMENT_PLAN_DAILY_TAB_CHECK,
    ISLAND_DEVELOPMENT_PLAN_CHECK,
    TEMPLATE_JUU_EXPRESS_TASK_ICON,
    WEEKLY_PHOTO_TASK_CHECK,
)


class DevelopmentPlanResultTests(unittest.TestCase):
    def setUp(self):
        self.runner = IslandDailyInteract.__new__(IslandDailyInteract)
        self.runner.device = SimpleNamespace(screenshot=Mock(), click=Mock())
        self.runner._enter_development_plan = Mock(return_value=True)
        self.runner._switch_development_plan_tab = Mock(return_value=True)
        self.button = SimpleNamespace(area=(190, 200, 220, 230))
        self.runner._read_development_plan_task_template = Mock(return_value=(True, self.button))
        self.runner.loop = Mock(return_value=iter((None, None)))
        self.runner._island_reward_visible = Mock(return_value=False)
        self.runner._handle_island_reward_once = Mock()
        self.runner.appear_then_click = Mock(return_value=True)

    def detect(self):
        return self.runner._detect_development_plan_template_task(
            task_template=TEMPLATE_JUU_EXPRESS_TASK_ICON,
            tab_button=DEVELOPMENT_PLAN_DAILY_TAB,
            tab_check=DEVELOPMENT_PLAN_DAILY_TAB_CHECK,
            tab_label='每日计划', label='JUU速运',
        )

    def start(self):
        return self.runner._start_development_plan_template_task(
            task_template=TEMPLATE_JUU_EXPRESS_TASK_ICON,
            task_check=WEEKLY_PHOTO_TASK_CHECK,
            tab_button=DEVELOPMENT_PLAN_DAILY_TAB,
            tab_check=DEVELOPMENT_PLAN_DAILY_TAB_CHECK,
            tab_label='每日计划', label='每周照相任务',
        )

    def test_entry_failure_is_unknown_instead_of_absent(self):
        self.runner._enter_development_plan.return_value = False
        self.assertIsNone(self.detect())
        self.assertIsNone(self.start())
        self.runner._switch_development_plan_tab.assert_not_called()
        self.runner._read_development_plan_task_template.assert_not_called()
        self.runner.device.click.assert_not_called()

    def test_tab_failure_is_unknown_instead_of_absent(self):
        self.runner._switch_development_plan_tab.return_value = False
        self.assertIsNone(self.detect())
        self.assertIsNone(self.start())
        self.runner._read_development_plan_task_template.assert_not_called()
        self.runner.device.click.assert_not_called()

    def test_confirmed_absence_is_false_without_click(self):
        self.runner._read_development_plan_task_template.return_value = (False, None)
        self.assertIs(self.detect(), False)
        self.assertIs(self.start(), False)
        self.runner.device.click.assert_not_called()
        self.runner.appear_then_click.assert_not_called()

    def test_unstable_check_is_unknown_without_click(self):
        self.runner._read_development_plan_task_template.return_value = (None, None)
        self.assertIsNone(self.detect())
        self.assertIsNone(self.start())
        self.runner.device.click.assert_not_called()
        self.runner.appear_then_click.assert_not_called()

    def test_detect_presence_does_not_start_task(self):
        self.assertIs(self.detect(), True)
        self.runner.device.click.assert_not_called()

    def test_start_clicks_stable_target_and_requires_confirmation(self):
        self.assertIs(self.start(), True)
        self.runner.device.click.assert_called_once_with(self.button)
        self.runner.appear_then_click.assert_called_once_with(
            WEEKLY_PHOTO_TASK_CHECK, offset=(20, 20), interval=2,
        )

    def test_start_confirmation_timeout_is_unknown(self):
        self.runner.appear_then_click.return_value = False
        self.assertIsNone(self.start())
        self.runner.device.click.assert_called_once_with(self.button)

    def test_start_handles_reward_before_confirming_task(self):
        self.runner._island_reward_visible.side_effect = (True, False)
        self.assertIs(self.start(), True)
        self.runner._handle_island_reward_once.assert_called_once_with()
        self.runner.appear_then_click.assert_called_once()

    def test_start_does_not_reuse_stale_confirmation_frame_after_icon_click(self):
        """旧帧已有确认图案，点击任务后须观察新帧上的实际确认状态。"""
        del self.runner.loop
        clock = SimpleNamespace(now=1000.0)
        events = []
        self.runner.device.image = True

        def screenshot():
            clock.now += .2
            count = self.runner.device.screenshot.call_count
            self.runner.device.image = count >= 3
            events.append(('screenshot', count, self.runner.device.image))
            return self.runner.device.image

        def confirm(button, **kwargs):
            events.append(('confirm', self.runner.device.screenshot.call_count, self.runner.device.image))
            return self.runner.device.image

        self.runner.device.screenshot.side_effect = screenshot
        self.runner.device.click.side_effect = lambda button: events.append(('task_click', button))
        self.runner.appear_then_click.side_effect = confirm
        with patch.object(timer_module, 'time', lambda: clock.now):
            self.assertIs(self.start(), True)

        self.assertEqual(events[0], ('task_click', self.button))
        self.assertEqual(events[1], ('screenshot', 1, False))
        self.assertEqual(
            [event for event in events if event[0] == 'confirm'],
            [('confirm', 1, False), ('confirm', 2, False), ('confirm', 3, True)],
        )
        self.runner.device.click.assert_called_once_with(self.button)


@dataclass(frozen=True)
class PlanFrame:
    header: bool = True
    tab: bool = True
    area: tuple | None = None
    reward: bool = False


class DevelopmentPlanStableReadTests(unittest.TestCase):
    def setUp(self):
        self.clock = 1000.0
        self.t0 = self.clock
        self.timeline = lambda seconds: PlanFrame()
        self.runner = IslandDailyInteract.__new__(IslandDailyInteract)
        self.runner.device = SimpleNamespace(
            image=PlanFrame(), screenshot=Mock(side_effect=self.screenshot),
            click=Mock(side_effect=AssertionError('检查任务列表不应点击游戏')),
            sleep=Mock(side_effect=AssertionError('不能固定休眠猜测页面状态')),
        )
        self.runner.appear = Mock(side_effect=self.appear)
        self.runner._island_reward_visible = Mock(side_effect=lambda: self.runner.device.image.reward)
        # 冷却中的处理函数也返回 False；奖励可见仍必须阻止扫描背景列表。
        self.runner._handle_island_reward_once = Mock(return_value=False)
        self.runner._match_development_plan_task_template = Mock(side_effect=self.match)
        self.enterContext(patch.object(timer_module, 'time', lambda: self.clock))

    def screenshot(self):
        self.clock += .2
        self.runner.device.image = self.timeline(round(self.clock - self.t0, 2))
        return self.runner.device.image

    def appear(self, button, **kwargs):
        frame = self.runner.device.image
        if button is ISLAND_DEVELOPMENT_PLAN_CHECK:
            return frame.header
        if button is DEVELOPMENT_PLAN_DAILY_TAB_CHECK:
            return frame.tab
        return False

    def match(self, template):
        frame = self.runner.device.image
        self.assertTrue(frame.header and frame.tab and not frame.reward)
        return SimpleNamespace(area=frame.area) if frame.area is not None else None

    def read(self, timeline):
        self.timeline = timeline
        self.runner.device.image = timeline(0)
        return self.runner._read_development_plan_task_template(
            TEMPLATE_JUU_EXPRESS_TASK_ICON, DEVELOPMENT_PLAN_DAILY_TAB_CHECK, 'JUU速运',
        )

    def test_stable_absence_requires_confirmed_page_and_new_frames(self):
        state, button = self.read(lambda seconds: PlanFrame())
        self.assertIs(state, False)
        self.assertIsNone(button)
        self.assertGreaterEqual(self.runner.device.screenshot.call_count, 3)
        self.assertGreater(self.clock - self.t0, .5)

    def test_stable_presence_returns_target_button(self):
        area = (190, 200, 220, 230)
        state, button = self.read(lambda seconds: PlanFrame(area=area))
        self.assertIs(state, True)
        self.assertEqual(button.area, area)
        self.assertGreater(self.clock - self.t0, .5)

    def test_missing_header_is_unknown_not_absent(self):
        self.assertEqual(self.read(lambda seconds: PlanFrame(header=False)), (None, None))
        self.runner._match_development_plan_task_template.assert_not_called()

    def test_missing_active_tab_is_unknown_not_absent(self):
        self.assertEqual(self.read(lambda seconds: PlanFrame(tab=False)), (None, None))
        self.runner._match_development_plan_task_template.assert_not_called()

    def test_loading_frame_resets_absent_stability(self):
        area = (190, 200, 220, 230)

        def timeline(seconds):
            if seconds < .4:
                return PlanFrame()
            if seconds < .8:
                return PlanFrame(header=False, tab=False)
            return PlanFrame(area=area)

        state, button = self.read(timeline)
        self.assertIs(state, True)
        self.assertEqual(button.area, area)
        self.assertGreaterEqual(self.clock - self.t0, 1.3)

    def test_icon_loaded_after_initial_empty_frame_is_not_skipped(self):
        area = (190, 200, 220, 230)
        state, button = self.read(lambda seconds: PlanFrame(area=area if seconds >= .4 else None))
        self.assertIs(state, True)
        self.assertEqual(button.area, area)
        self.assertGreaterEqual(self.clock - self.t0, .9)

    def test_reward_during_cooldown_blocks_background_absence(self):
        area = (190, 200, 220, 230)
        state, button = self.read(lambda seconds: PlanFrame(
            reward=seconds < 1, area=area if seconds >= 1 else None,
        ))
        self.assertIs(state, True)
        self.assertEqual(button.area, area)
        self.runner._handle_island_reward_once.assert_called()
        self.assertGreaterEqual(self.clock - self.t0, 1.5)

    def test_reward_between_absent_frames_resets_stability(self):
        state, button = self.read(lambda seconds: PlanFrame(reward=.4 <= seconds < 1))
        self.assertIs(state, False)
        self.assertIsNone(button)
        self.assertGreaterEqual(self.clock - self.t0, 1.5)

    def test_target_position_change_resets_stability(self):
        first_area = (190, 200, 220, 230)
        final_area = (190, 240, 220, 270)
        state, button = self.read(lambda seconds: PlanFrame(
            area=first_area if seconds < .4 else final_area,
        ))
        self.assertIs(state, True)
        self.assertEqual(button.area, final_area)
        self.assertGreaterEqual(self.clock - self.t0, .9)

    def test_continuously_moving_list_is_unknown_not_present(self):
        self.assertEqual(self.read(lambda seconds: PlanFrame(
            area=(190, int(seconds * 10) + 200, 220, int(seconds * 10) + 230),
        )), (None, None))


class DailyInteractFailurePropagationTests(unittest.TestCase):
    def setUp(self):
        self.runner = IslandDailyInteract.__new__(IslandDailyInteract)
        self.runner.device = SimpleNamespace(screenshot=Mock(), click=Mock())
        self.runner.ui_goto = Mock()
        self.runner._detect_development_plan_template_task = Mock(return_value=True)
        self.runner._start_development_plan_template_task = Mock(return_value=False)
        self.runner._back_to_island_phone_from_development_plan = Mock(return_value=True)
        self.runner._back_to_island_phone = Mock(return_value=True)
        self.runner._handle_island_reward_optional = Mock()
        self.runner._juu_express_steps = Mock(return_value=[])
        self.runner._business_delivery_steps = Mock(return_value=[])
        self.runner.juu_express_location_flow = Mock(return_value=True)
        self.runner.delivery_location_flow = Mock(return_value=True)
        self.runner._run_weekly_photo_once = Mock(return_value=True)

    def test_unknown_daily_check_fails_without_running_locations(self):
        for method in ('juu_express', 'business_delivery'):
            with self.subTest(method=method):
                self.runner._detect_development_plan_template_task.return_value = None
                self.assertIs(getattr(self.runner, method)(), False)
        self.runner._juu_express_steps.assert_not_called()
        self.runner._business_delivery_steps.assert_not_called()
        self.runner.juu_express_location_flow.assert_not_called()
        self.runner.delivery_location_flow.assert_not_called()

    def test_absent_daily_task_requires_successful_return_to_phone(self):
        self.runner._detect_development_plan_template_task.return_value = False
        for returned in (False, True):
            for method in ('juu_express', 'business_delivery'):
                with self.subTest(method=method, returned=returned):
                    self.runner._back_to_island_phone_from_development_plan.return_value = returned
                    self.assertIs(getattr(self.runner, method)(), returned)

    def test_present_daily_task_requires_return_before_walking(self):
        self.runner._back_to_island_phone_from_development_plan.return_value = False
        self.assertIs(self.runner.juu_express(), False)
        self.assertIs(self.runner.business_delivery(), False)
        self.runner._juu_express_steps.assert_not_called()
        self.runner._business_delivery_steps.assert_not_called()

    def test_unknown_weekly_start_fails_without_photo_actions(self):
        self.runner._start_development_plan_template_task.return_value = None
        self.assertIs(self.runner.weekly_photo(), False)
        self.runner._run_weekly_photo_once.assert_not_called()

    def test_absent_weekly_task_requires_successful_return_to_phone(self):
        for returned in (False, True):
            with self.subTest(returned=returned):
                self.runner._back_to_island_phone_from_development_plan.return_value = returned
                self.assertIs(self.runner.weekly_photo(), returned)
        self.runner._run_weekly_photo_once.assert_not_called()

    def test_unknown_weekly_recheck_after_one_round_remains_failure(self):
        self.runner._start_development_plan_template_task.side_effect = (True, None)
        self.assertIs(self.runner.weekly_photo(), False)
        self.runner._run_weekly_photo_once.assert_called_once_with()
        self.runner._back_to_island_phone.assert_called_once_with()

    def test_confirmed_absence_after_one_weekly_round_completes(self):
        self.runner._start_development_plan_template_task.side_effect = (True, False)
        self.assertIs(self.runner.weekly_photo(), True)
        self.runner._run_weekly_photo_once.assert_called_once_with()


class DailyInteractRunSchedulingTests(unittest.TestCase):
    def setUp(self):
        self.runner = IslandDailyInteract.__new__(IslandDailyInteract)
        self.runner.config = SimpleNamespace(IslandDailyInteract_WeeklyPhoto=True, task_delay=Mock())
        self.runner.ui_ensure = Mock()
        self.runner.pet_cat = Mock()
        self.runner.juu_express = Mock(return_value=True)
        self.runner.business_delivery = Mock(return_value=True)
        self.runner.weekly_photo = Mock(return_value=True)
        self.target = datetime(2026, 10, 15, 3)
        self.next_update = self.enterContext(patch(
            'module.island.island_daily_interact.get_server_next_update', return_value=self.target,
        ))

    def test_success_delays_until_server_three_oclock(self):
        self.runner.run()
        self.next_update.assert_called_once_with('03:00')
        self.runner.config.task_delay.assert_called_once_with(target=self.target)

    def test_ordinary_failure_retries_in_hour_without_short_circuiting_later_groups(self):
        for failed in ('juu_express', 'business_delivery', 'weekly_photo'):
            with self.subTest(failed=failed):
                for name in ('juu_express', 'business_delivery', 'weekly_photo'):
                    getattr(self.runner, name).reset_mock()
                    getattr(self.runner, name).return_value = name != failed
                self.runner.config.task_delay.reset_mock()
                self.runner.run()
                for name in ('juu_express', 'business_delivery', 'weekly_photo'):
                    getattr(self.runner, name).assert_called_once_with()
                self.runner.config.task_delay.assert_called_once_with(minute=60)
        self.next_update.assert_not_called()

    def test_disabled_weekly_photo_does_not_run(self):
        self.runner.config.IslandDailyInteract_WeeklyPhoto = False
        self.runner.run()
        self.runner.weekly_photo.assert_not_called()
        self.runner.config.task_delay.assert_called_once_with(target=self.target)

    def test_route_exception_interrupts_remaining_tasks_and_defers_to_recovery(self):
        self.runner.business_delivery.side_effect = GameStuckError('切换啾咖啡餐厅超时')
        with self.assertRaisesRegex(GameStuckError, '切换啾咖啡餐厅超时'):
            self.runner.run()
        self.runner.juu_express.assert_called_once_with()
        self.runner.weekly_photo.assert_not_called()
        self.runner.config.task_delay.assert_not_called()


if __name__ == '__main__':
    unittest.main()
