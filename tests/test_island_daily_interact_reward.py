"""岛屿互动只在确认奖励遮罩时关闭的离线状态回归。"""

import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import Mock, patch

import module.base.timer as timer_module
from module.island.island_daily_interact import IslandDailyInteract


@dataclass(frozen=True)
class InteractFrame:
    reward: str = ''
    phone: bool = False
    back: bool = False
    scene: bool = False
    story: bool = False


class IslandDailyInteractRewardTests(unittest.TestCase):
    def setUp(self):
        self.clock = 1000.0
        self.t0 = self.clock
        self.clicks = []
        self.runner = IslandDailyInteract.__new__(IslandDailyInteract)
        self.runner.interval_timer = {}
        self.timeline = lambda seconds: InteractFrame()
        self.runner.device = SimpleNamespace(
            image=InteractFrame(), screenshot=Mock(side_effect=self.screenshot),
            click=Mock(side_effect=self.click), sleep=Mock(side_effect=AssertionError('不能固定休眠猜测奖励状态')),
        )
        self.runner.appear = Mock(side_effect=self.appear)
        self.runner.ui_page_appear = Mock(side_effect=lambda page: self.runner.device.image.scene)
        self.enterContext(patch.object(timer_module, 'time', lambda: self.clock))

    def screenshot(self):
        self.clock += .2
        self.runner.device.image = self.timeline(self.clock - self.t0)
        return self.runner.device.image

    def click(self, button, **kwargs):
        self.clicks.append((button.name, round(self.clock - self.t0, 2)))

    def appear(self, button, **kwargs):
        frame = self.runner.device.image
        return {
            'ISLAND_GET': frame.reward == 'ISLAND_GET',
            'GET_ITEMS_ISLAND': frame.reward == 'GET_ITEMS_ISLAND',
            'ISLAND_PHONE_CHECK': frame.phone,
            'ISLAND_BACK': frame.back,
        }.get(button.name, False)

    def set_timeline(self, timeline):
        self.timeline = timeline
        self.runner.device.image = timeline(0)

    def test_no_reward_has_no_click_and_keeps_observation_budget(self):
        self.set_timeline(lambda seconds: InteractFrame(scene=True))
        self.assertFalse(self.runner._handle_island_reward_optional())
        self.assertEqual(self.clicks, [])
        self.assertGreaterEqual(self.clock - self.t0, 6)

    def test_unknown_frame_is_not_clicked_or_forced_into_recovery(self):
        self.assertFalse(self.runner._handle_island_reward_optional())
        self.assertEqual(self.clicks, [])

    def test_delayed_reward_is_observed_and_closed_once(self):
        self.set_timeline(lambda seconds: InteractFrame(reward='ISLAND_GET' if 3 <= seconds < 3.8 else '', scene=True))
        self.assertTrue(self.runner._handle_island_reward_optional())
        self.assertEqual(len(self.clicks), 1)
        self.assertEqual(self.clicks[0][0], 'ISLAND_CLICK_SAFE_AREA')
        self.assertGreaterEqual(self.clicks[0][1], 3)

    def test_reward_close_never_has_a_blind_followup_click(self):
        for reward in ('ISLAND_GET', 'GET_ITEMS_ISLAND'):
            with self.subTest(reward=reward):
                self.clock = self.t0
                self.clicks.clear()
                self.runner.interval_timer = {}
                self.set_timeline(lambda seconds: InteractFrame(reward=reward if seconds < .8 else '', scene=True))
                self.assertTrue(self.runner._handle_island_reward_optional())
                self.assertEqual(self.clicks, [('ISLAND_CLICK_SAFE_AREA', .2)])

    def test_reward_type_changes_still_share_the_safe_area_cooldown(self):
        self.set_timeline(lambda seconds: InteractFrame(reward='ISLAND_GET' if int(seconds * 5) % 2 else 'GET_ITEMS_ISLAND'))
        self.assertTrue(self.runner._handle_island_reward_optional(timeout=5))
        self.assertEqual(len(self.clicks), 3)
        times = [seconds for _, seconds in self.clicks]
        self.assertTrue(all(later - earlier >= 2 for earlier, later in zip(times, times[1:])))

    def test_handler_returns_false_during_cooldown(self):
        self.set_timeline(lambda seconds: InteractFrame(reward='ISLAND_GET'))
        self.assertTrue(self.runner._handle_island_reward_once())
        self.clock += .2
        self.assertFalse(self.runner._handle_island_reward_once())
        self.assertEqual(len(self.clicks), 1)

    def test_weekly_photo_does_not_click_camera_behind_reward_cooldown(self):
        self.runner.loop = Mock(return_value=iter(range(5)))
        self.runner._island_reward_visible = Mock(return_value=True)
        self.runner._handle_island_reward_once = Mock(return_value=False)
        self.runner.appear = Mock(return_value=True)
        self.assertFalse(self.runner._run_weekly_photo_once())
        self.runner.device.click.assert_not_called()
        self.assertEqual(self.runner._handle_island_reward_once.call_count, 5)

    def test_reward_covering_phone_does_not_complete_navigation_early(self):
        self.set_timeline(lambda seconds: InteractFrame(reward='ISLAND_GET' if seconds < 1 else '', phone=True, back=True))
        self.runner.appear_then_click = Mock(side_effect=AssertionError('奖励仍在不应点击背景返回'))
        self.assertTrue(self.runner._back_to_island_phone_from_development_plan())
        self.assertEqual(self.clicks, [('ISLAND_CLICK_SAFE_AREA', 0.0)])
        self.assertGreaterEqual(self.clock - self.t0, 1)

    def test_story_still_visible_during_cooldown_does_not_end_on_background(self):
        self.set_timeline(lambda seconds: InteractFrame(scene=True, story=seconds < 1))

        def story_appear(interval=0):
            if not self.runner.device.image.story:
                return False
            if interval:
                timer = self.runner.get_interval_timer('STORY_SKIP_3', interval=interval, renew=True)
                if not timer.reached():
                    return False
                timer.reset()
            return True

        self.runner._appear_story_skip_luma = Mock(side_effect=story_appear)
        self.assertTrue(self.runner.handle_island_story_skip_safely())
        self.assertEqual(self.clicks, [('AIR_DROP_SKIP', .2)])
        self.assertGreaterEqual(self.clock - self.t0, 1)

    def test_missing_pet_cat_interaction_does_not_click_safe_area(self):
        self.runner.island_map_goto = Mock(return_value=True)
        self.runner.move_for_morningdew_farm = Mock()
        self.runner._click_optional_interact = Mock(return_value=False)
        self.runner._handle_island_reward_optional = Mock()
        self.runner.pet_cat()
        self.runner._handle_island_reward_optional.assert_not_called()
        self.assertEqual(self.clicks, [])

    def test_delivery_summary_without_reward_does_not_click_safe_area(self):
        for method, steps in (('juu_express', '_juu_express_steps'), ('business_delivery', '_business_delivery_steps')):
            with self.subTest(method=method):
                self.runner._detect_development_plan_template_task = Mock(return_value=True)
                self.runner._back_to_island_phone_from_development_plan = Mock(return_value=True)
                setattr(self.runner, steps, Mock(return_value=[]))
                self.runner._handle_island_reward_optional = Mock(return_value=False)
                self.runner.ui_goto = Mock()
                self.assertTrue(getattr(self.runner, method)())
                self.assertEqual(self.clicks, [])
                self.runner._handle_island_reward_optional.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
