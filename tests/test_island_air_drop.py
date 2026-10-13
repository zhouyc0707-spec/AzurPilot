"""验证岛屿每日补给只领取、不误点分享，以及领取结果与补滑调度。"""

import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from module.exception import GameStuckError
from module.island.island_air_drop import AIR_DROP_RECEIVE, IslandAirDrop


@dataclass(frozen=True)
class AirDropFrame:
    """一帧可识别状态；模拟时间随截图推进，不连接模拟器。"""

    name: str
    scene: bool = False
    friend: bool = False
    share: bool = False
    action: str | None = ''
    reward: str = ''
    skip: bool = False
    phone: bool = False
    season: bool = False
    elapsed: float = 0.3


class IslandAirDropStateTests(unittest.TestCase):
    def setUp(self):
        self.monotonic = 1000.0
        self.frames = iter(())
        self.frame = None
        self.clicks = []
        self.island = IslandAirDrop.__new__(IslandAirDrop)
        self.island.interval_timer = {}
        self.island.device = SimpleNamespace(
            image=None,
            screenshot=Mock(side_effect=self.screenshot),
            click=Mock(side_effect=self.click),
            sleep=Mock(side_effect=self.sleep),
            save_screenshot=Mock(),
        )
        self.island.appear = Mock(side_effect=self.appear)
        self.island.is_in_friend_island = Mock(side_effect=lambda: self.frame.friend)
        self.island.air_drop_action_ocr = SimpleNamespace(
            ocr=Mock(side_effect=lambda image: image.action),
        )
        self.enterContext(patch('module.base.timer.time', side_effect=lambda: self.monotonic))
        self.enterContext(patch('module.island.island_air_drop.logger'))

    def screenshot(self):
        try:
            self.frame = next(self.frames)
        except StopIteration:
            self.fail('补给状态循环没有在已提供的画面内结束')
        self.monotonic += self.frame.elapsed
        self.island.device.image = self.frame
        return self.frame

    def click(self, button):
        self.clicks.append((button.name, self.frame.name, self.monotonic))

    def sleep(self, seconds):
        self.monotonic += seconds

    def appear(self, button, interval=0, **_):
        visible = {
            'GET_ITEMS_ISLAND': self.frame.reward == 'GET_ITEMS_ISLAND',
            'ISLAND_GET': self.frame.reward == 'ISLAND_GET',
            'AIR_DROP_SKIP': self.frame.skip,
            'ISLAND_PHONE_CHECK': self.frame.phone,
            'ISLAND_SEASON_CHECK': self.frame.season,
            'ISLAND_PHONE_GOTO_ISLAND': self.frame.phone,
            'ISLAND_SEASON_GOTO_ISLAND': self.frame.season,
            'ISLAND_CHECK': self.frame.scene,
            'ISLAND_AIR_DROP_A': self.frame.share,
        }.get(button.name, False)
        if visible and interval:
            timer = self.island.get_interval_timer(button, interval=interval, renew=True)
            if not timer.reached():
                return False
            timer.reset()
        return visible

    def run_frames(self, frames):
        self.frames = iter(frames)
        return self.island.island_air_drop()

    def assert_no_share_click(self):
        self.assertFalse(any(name.startswith('ISLAND_AIR_DROP_') for name, _, _ in self.clicks))

    def test_share_template_at_entry_finishes_without_any_click(self):
        result = self.run_frames([AirDropFrame('分享入口', scene=True, share=True)])
        self.assertTrue(result)
        self.island.device.click.assert_not_called()
        self.island.air_drop_action_ocr.ocr.assert_not_called()

    def test_share_text_finishes_without_click_when_template_does_not_match(self):
        result = self.run_frames([
            AirDropFrame('文字分享入口', scene=True, action='分享剩余补给'),
        ])
        self.assertTrue(result)
        self.island.device.click.assert_not_called()

    def test_receive_once_then_share_finishes_without_clicking_share(self):
        result = self.run_frames([
            AirDropFrame('接收入口', scene=True, action='接收补给'),
            AirDropFrame('领取过渡'),
            AirDropFrame('分享入口', scene=True, share=True),
        ])
        self.assertTrue(result)
        self.assertEqual([name for name, _, _ in self.clicks], [AIR_DROP_RECEIVE.name])
        self.assert_no_share_click()

    def test_daily_supply_entry_is_clicked_before_reward_and_share(self):
        result = self.run_frames([
            AirDropFrame('日常补给入口', scene=True, action='日常补给'),
            AirDropFrame('入口点击后转场一', scene=True, action='日常补给'),
            AirDropFrame('入口点击后转场二', scene=True, action='日常补给'),
            AirDropFrame('领取奖励', scene=True, reward='ISLAND_GET', elapsed=2.1),
            AirDropFrame('领取后分享入口', scene=True, action='分享剩余补给'),
        ])
        self.assertTrue(result)
        self.assertEqual([name for name, _, _ in self.clicks],
                         ['AIR_DROP_RECEIVE', 'ISLAND_CLICK_SAFE_AREA'])
        self.assert_no_share_click()

    def test_daily_supply_entry_on_friend_island_uses_same_guarded_click(self):
        result = self.run_frames([
            AirDropFrame('好友日常补给入口', friend=True, action='日常补给'),
            AirDropFrame('好友领取后分享入口', friend=True, share=True),
        ])
        self.assertTrue(result)
        self.assertEqual([name for name, _, _ in self.clicks], ['AIR_DROP_RECEIVE'])
        self.assert_no_share_click()

    def test_daily_supply_text_ignores_whitespace_and_keeps_click_cooldown(self):
        result = self.run_frames([
            *(AirDropFrame(f'日常补给仍可见{index}', scene=True, action=' 日 常\n补 给 ')
              for index in range(5)),
            AirDropFrame('分享状态', scene=True, share=True),
        ])
        self.assertTrue(result)
        self.assertEqual([name for name, _, _ in self.clicks], ['AIR_DROP_RECEIVE'])

    def test_daily_supply_text_outside_confirmed_scene_is_not_clicked(self):
        result = self.run_frames([
            AirDropFrame('未知页面上的相同文字', action='日常补给'),
            *(AirDropFrame(f'恢复无入口场景{index}', scene=True, elapsed=0.5)
              for index in range(3)),
        ])
        self.assertFalse(result)
        self.island.device.click.assert_not_called()

    def test_receive_text_ignores_ocr_whitespace(self):
        result = self.run_frames([
            AirDropFrame('接收入口', scene=True, action=' 接 收\n补 给 '),
            AirDropFrame('分享入口', scene=True, action='分享剩余补给'),
        ])
        self.assertTrue(result)
        self.assertEqual([name for name, _, _ in self.clicks], ['AIR_DROP_RECEIVE'])

    def test_similar_or_incomplete_action_text_never_triggers_receive(self):
        for text in ('接受补给', '接收补', '接收补给分享', '日常补', '每日补给',
                     '日常补给分享', '分享剩余补', '', None):
            with self.subTest(text=text):
                self.island.device.click.reset_mock()
                result = self.run_frames([
                    AirDropFrame(f'未知文字{index}', scene=True, action=text, elapsed=0.5)
                    for index in range(3)
                ])
                self.assertFalse(result)
                self.island.device.click.assert_not_called()

    def test_receive_button_during_cooldown_is_not_clicked_twice(self):
        result = self.run_frames([
            AirDropFrame('首次接收', scene=True, action='接收补给'),
            AirDropFrame('接收动画尚未更新一', scene=True, action='接收补给'),
            AirDropFrame('接收动画尚未更新二', scene=True, action='接收补给'),
            AirDropFrame('接收动画尚未更新三', scene=True, action='接收补给'),
            AirDropFrame('分享入口', scene=True, share=True),
        ])
        self.assertTrue(result)
        self.assertEqual([name for name, _, _ in self.clicks], ['AIR_DROP_RECEIVE'])

    def test_rewards_covering_share_button_are_closed_before_finish(self):
        for reward in ('GET_ITEMS_ISLAND', 'ISLAND_GET'):
            with self.subTest(reward=reward):
                self.clicks.clear()
                result = self.run_frames([
                    AirDropFrame('奖励遮住分享', scene=True, share=True, reward=reward),
                    AirDropFrame('奖励关闭后的分享', scene=True, share=True),
                ])
                self.assertTrue(result)
                self.assertEqual([name for name, _, _ in self.clicks], ['ISLAND_CLICK_SAFE_AREA'])
                self.assertEqual(self.clicks[0][1], '奖励遮住分享')
                self.assert_no_share_click()

    def test_reward_click_cooldown_does_not_finish_on_background_share(self):
        result = self.run_frames([
            AirDropFrame('首次奖励', scene=True, share=True, reward='GET_ITEMS_ISLAND'),
            AirDropFrame('冷却中仍有奖励一', scene=True, share=True, reward='GET_ITEMS_ISLAND'),
            AirDropFrame('冷却中仍有奖励二', scene=True, share=True, reward='GET_ITEMS_ISLAND'),
            AirDropFrame('真正恢复分享', scene=True, share=True),
        ])
        self.assertTrue(result)
        self.assertEqual([name for name, _, _ in self.clicks], ['ISLAND_CLICK_SAFE_AREA'])
        self.assertEqual(self.island.device.screenshot.call_count, 4)
        self.island.air_drop_action_ocr.ocr.assert_not_called()

    def test_receive_to_reward_obeys_click_interval_then_confirms_normal_scene(self):
        result = self.run_frames([
            AirDropFrame('首次接收', scene=True, action='接收补给'),
            *(AirDropFrame(f'奖励等待{index}', reward='ISLAND_GET', elapsed=0.4)
              for index in range(6)),
            *(AirDropFrame(f'恢复场景{index}', scene=True, elapsed=0.5)
              for index in range(3)),
        ])
        self.assertTrue(result)
        self.assertEqual([name for name, _, _ in self.clicks],
                         ['AIR_DROP_RECEIVE', 'ISLAND_CLICK_SAFE_AREA'])
        self.assertGreaterEqual(self.clicks[1][2] - self.clicks[0][2], 2)
        self.assert_no_share_click()

    def test_stable_normal_scene_without_entry_returns_false_for_outer_retry(self):
        result = self.run_frames([
            AirDropFrame(f'正常场景{index}', scene=True, elapsed=0.5)
            for index in range(3)
        ])
        self.assertFalse(result)
        self.assertEqual(self.island.device.screenshot.call_count, 3)
        self.island.device.click.assert_not_called()

    def test_unknown_frame_interrupts_stable_scene_confirmation(self):
        result = self.run_frames([
            AirDropFrame('第一次正常场景', scene=True, elapsed=0.5),
            AirDropFrame('第二次正常场景', scene=True, elapsed=0.5),
            AirDropFrame('场景中断', elapsed=0.5),
            *(AirDropFrame(f'重新确认场景{index}', scene=True, elapsed=0.5)
              for index in range(3)),
        ])
        self.assertFalse(result)
        self.assertEqual(self.island.device.screenshot.call_count, 6)

    def test_skip_animation_does_not_consider_background_action_ready(self):
        result = self.run_frames([
            AirDropFrame('过场跳过', scene=True, skip=True, share=True),
            AirDropFrame('跳过冷却一', scene=True, skip=True, share=True),
            AirDropFrame('跳过冷却二', scene=True, skip=True, share=True),
            AirDropFrame('过场后接收', scene=True, action='接收补给'),
            AirDropFrame('领取后分享', scene=True, share=True),
        ])
        self.assertTrue(result)
        self.assertEqual([name for name, _, _ in self.clicks], ['AIR_DROP_SKIP', 'AIR_DROP_RECEIVE'])
        self.assertEqual([args.args[0].name for args in self.island.air_drop_action_ocr.ocr.call_args_list],
                         ['过场后接收'])

    def test_phone_and_season_pages_return_to_island_before_action_ocr(self):
        for page in ('phone', 'season'):
            with self.subTest(page=page):
                self.clicks.clear()
                self.island.interval_timer.clear()
                self.island.air_drop_action_ocr.ocr.reset_mock()
                result = self.run_frames([
                    AirDropFrame('手机或季节页', scene=True, action='接收补给', **{page: True}),
                    AirDropFrame('返回后的分享入口', scene=True, share=True),
                ])
                self.assertTrue(result)
                expected = 'ISLAND_PHONE_GOTO_ISLAND' if page == 'phone' else 'ISLAND_SEASON_GOTO_ISLAND'
                self.assertEqual([name for name, _, _ in self.clicks], [expected])
                self.island.air_drop_action_ocr.ocr.assert_not_called()

    def test_friend_scene_can_receive_without_own_island_marker(self):
        result = self.run_frames([
            AirDropFrame('好友接收入口', friend=True, action='接收补给'),
            AirDropFrame('好友分享入口', friend=True, share=True),
        ])
        self.assertTrue(result)
        self.assertEqual([name for name, _, _ in self.clicks], ['AIR_DROP_RECEIVE'])

    def test_unconfirmed_page_does_not_ocr_or_click_apparent_action(self):
        self.frames = iter(
            AirDropFrame('持续未知页', action='接收补给', elapsed=1)
            for _ in range(20)
        )
        with self.assertRaisesRegex(GameStuckError, '已保存现场'):
            self.island.island_air_drop()
        self.island.device.save_screenshot.assert_called_once_with(
            genre='island_air_drop_unknown', interval=0)
        self.island.device.click.assert_not_called()
        self.island.air_drop_action_ocr.ocr.assert_not_called()
        self.assertGreaterEqual(self.monotonic - 1000, 15)
        self.assertLessEqual(self.monotonic - 1000, 16)

    def test_persistent_reward_is_bounded_and_never_clicks_background_share(self):
        self.frames = iter(
            AirDropFrame('奖励始终未关闭', scene=True, share=True, reward='ISLAND_GET', elapsed=1)
            for _ in range(20)
        )
        with self.assertRaisesRegex(GameStuckError, '已保存现场'):
            self.island.island_air_drop()
        self.assertTrue(self.clicks)
        self.assertEqual({name for name, _, _ in self.clicks}, {'ISLAND_CLICK_SAFE_AREA'})
        self.island.device.save_screenshot.assert_called_once_with(
            genre='island_air_drop_unknown', interval=0)
        self.island.air_drop_action_ocr.ocr.assert_not_called()
        self.assertLessEqual(self.monotonic - 1000, 16)

    def test_persistent_receive_action_cannot_extend_timeout_indefinitely(self):
        self.frames = iter(
            AirDropFrame('领取未响应', scene=True, action='接收补给', elapsed=1)
            for _ in range(20)
        )
        with self.assertRaisesRegex(GameStuckError, '已保存现场'):
            self.island.island_air_drop()
        self.assertTrue(self.clicks)
        self.assertEqual({name for name, _, _ in self.clicks}, {'AIR_DROP_RECEIVE'})
        self.island.device.save_screenshot.assert_called_once_with(
            genre='island_air_drop_unknown', interval=0)
        self.assertLessEqual(self.monotonic - 1000, 16)


class IslandAirDropRouteTests(unittest.TestCase):
    def setUp(self):
        self.island = IslandAirDrop.__new__(IslandAirDrop)
        self.island.island_walk_route = Mock()
        self.island.island_walk_composite_durations = Mock(return_value=[600, 700, 800])
        self.island.island_air_drop = Mock()
        self.island.island_up = Mock()
        self.island.island_right = Mock()
        self.island.island_down = Mock()
        self.island.exit_friend_island = Mock()
        self.island.appear = Mock(return_value=True)

    def test_confirmed_friend_collection_skips_all_retry_movements(self):
        self.island.island_air_drop.return_value = True
        self.assertTrue(self.island.run_and_get())
        self.island.island_walk_route.assert_called_once_with('AirDrop')
        self.island.island_air_drop.assert_called_once_with()
        for move in (self.island.island_up, self.island.island_right, self.island.island_down):
            move.assert_not_called()
        self.island.exit_friend_island.assert_called_once_with()
        self.island.appear.assert_not_called()

    def test_friend_retry_stops_immediately_after_confirmed_collection(self):
        self.island.island_air_drop.side_effect = [False, False, True]
        self.assertTrue(self.island.run_and_get())
        self.island.island_up.assert_called_once_with(600)
        self.island.island_right.assert_called_once_with(700)
        self.island.island_down.assert_not_called()
        self.assertEqual(self.island.island_air_drop.call_count, 3)
        self.island.exit_friend_island.assert_called_once_with()
        self.island.appear.assert_not_called()

    def test_friend_information_icon_cannot_replace_collection_confirmation(self):
        self.island.island_air_drop.return_value = False
        self.assertFalse(self.island.run_and_get())
        self.island.island_up.assert_called_once_with(600)
        self.island.island_right.assert_called_once_with(700)
        self.island.island_down.assert_called_once_with(800)
        self.assertEqual(self.island.island_air_drop.call_count, 4)
        self.island.appear.assert_not_called()
        self.island.exit_friend_island.assert_called_once_with()


class IslandAirDropScheduleTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 12, 12, 30)
        self.today = self.now.replace(hour=0, minute=0)
        self.old_last_steal = self.today - timedelta(days=1)
        self.island = IslandAirDrop.__new__(IslandAirDrop)
        self.island.config = SimpleNamespace(
            IslandAirDrop_LastSteal=self.old_last_steal,
            IslandAirDrop_VisitOtherIsland=False,
            task_delay=Mock(),
        )
        self.island.device = SimpleNamespace(sleep=Mock(), save_screenshot=Mock())
        self.island.goto_postmanage = Mock()
        self.island.goto_management = Mock()
        self.island.ui_goto = Mock()
        self.island.appear_then_click = Mock(return_value=True)
        self.island.island_walk_route = Mock()
        self.island.island_air_drop = Mock()
        self.enterContext(patch('module.island.island_air_drop.current_time', return_value=self.now))
        self.enterContext(patch('module.island.island_air_drop.get_server_last_update', return_value=self.today))
        self.enterContext(patch('module.island.island_air_drop.logger'))

    def assert_successful_daily_schedule(self):
        self.assertEqual(self.island.config.IslandAirDrop_LastSteal, self.now)
        self.island.config.task_delay.assert_called_once_with(
            target=self.today + timedelta(days=1, hours=1))
        self.island.device.save_screenshot.assert_not_called()

    def test_own_collection_confirmed_first_time_keeps_route_without_second_collection(self):
        self.island.island_air_drop.return_value = True
        self.island.run()
        self.island.island_air_drop.assert_called_once_with()
        self.island.island_walk_route.assert_called_once_with('AirDropSelf')
        self.assert_successful_daily_schedule()

    def test_own_first_attempt_missing_entry_retries_after_configured_walk(self):
        self.island.island_air_drop.side_effect = [False, True]
        events = Mock()
        events.attach_mock(self.island.island_air_drop, 'collect')
        events.attach_mock(self.island.island_walk_route, 'walk')
        self.island.run()
        self.assertEqual(events.mock_calls, [call.collect(), call.walk('AirDropSelf'), call.collect()])
        self.assert_successful_daily_schedule()

    def test_unconfirmed_own_collection_keeps_date_and_rechecks_in_five_minutes(self):
        self.island.island_air_drop.return_value = False
        self.island.run()
        self.assertEqual(self.island.config.IslandAirDrop_LastSteal, self.old_last_steal)
        self.assertEqual(self.island.island_air_drop.call_count, 2)
        self.island.island_walk_route.assert_called_once_with('AirDropSelf')
        self.island.device.save_screenshot.assert_called_once_with(
            genre='island_air_drop_unknown', interval=0)
        self.island.config.task_delay.assert_called_once_with(target=self.now + timedelta(minutes=5))
        self.island.goto_management.assert_not_called()
        self.island.device.sleep.assert_not_called()

    def test_already_checked_today_does_not_restart_own_collection(self):
        self.island.config.IslandAirDrop_LastSteal = self.today + timedelta(hours=1)
        self.island.run()
        self.island.goto_postmanage.assert_not_called()
        self.island.island_air_drop.assert_not_called()
        self.island.island_walk_route.assert_not_called()
        self.assert_successful_daily_schedule()


if __name__ == '__main__':
    unittest.main()
