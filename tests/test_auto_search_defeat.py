"""离线回放自律寻敌结算，验证沉船撤退、弹窗与关闭任务策略。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import module.config.server as server

server.server = 'cn'

from module.base.timer import Timer
from module.campaign.gems_farming import GemsCampaignOverride
from module.campaign.run import CampaignRun
from module.combat.assets import (
    BATTLE_STATUS_D, EXP_INFO_A, EXP_INFO_B, EXP_INFO_C, EXP_INFO_D, EXP_INFO_S, OPTS_INFO_D,
)
from module.combat.auto_search_combat import AutoSearchCombat
from module.exception import CampaignEnd, GameStuckError, ScriptEnd
from module.handler.assets import (
    AUTO_SEARCH_MAP_OPTION_OFF, AUTO_SEARCH_MAP_OPTION_ON, AUTO_SEARCH_MENU_EXIT, GET_MISSION,
    POPUP_CANCEL, POPUP_CONFIRM,
)
from module.map.assets import FLEET_SWITCH_CONFIRM, FLEET_WITHDRAW, FLEET_WITHDRAW_BOSS, SWITCH_OVER, WITHDRAW


def screen(*buttons):
    return frozenset(button if isinstance(button, str) else button.name for button in buttons)


class ScreenReplay:
    """只提供识别结果和虚拟时钟，点击、截图及等待均不接触模拟器。"""

    def __init__(self, frames):
        self.time = 100.0
        self.clicks = []
        self.click_frames = []
        self.device = Mock(
            has_cached_image=True, app_is_running=Mock(return_value=True),
            screenshot=Mock(side_effect=self.screenshot), click=Mock(side_effect=self.click))
        self.load(frames)

    def load(self, frames):
        self.frames = list(frames)
        self.index = 0
        self.device.image = self.frames[0]

    def screenshot(self):
        self.time += 1
        self.index += 1
        if self.index >= len(self.frames):
            raise AssertionError(f'结算未在夹具内结束，已点击：{self.clicks}')
        self.device.image = self.frames[self.index]

    def click(self, button):
        self.clicks.append(button.name)
        self.click_frames.append((self.index, button.name))

    def appear(self, button, offset=0, interval=0, **kwargs):
        """保留生产计时器的点击冷却，避免回放掩盖连击或冷却漏判。"""
        detected = button.name in self.device.image
        if interval:
            timer = self.campaign.interval_timer.setdefault(button.name, Timer(interval))
            if not timer.reached():
                return False
            if detected:
                timer.reset()
        return detected

    def make_campaign(self, cls=GemsCampaignOverride, policy='withdraw_stop'):
        campaign = object.__new__(cls)
        campaign.config = SimpleNamespace(
            Campaign_DefeatWithdraw=policy, Campaign_Mode='normal',
            Submarine_Fleet=0, Fleet_Fleet1Mode='combat_auto', Fleet_Fleet2Mode='combat_auto',
            BUTTON_OFFSET=(30, 30), MAP_IS_ONE_TIME_STAGE=False, modified={})
        campaign.device = self.device
        campaign.emotion = Mock(is_calculate=True)
        campaign.map_is_auto_search = True
        campaign.fleet_alive_multiple = True
        campaign.interval_timer = {}
        campaign._defeat_count = 0
        campaign._hot_fix_check_wait = Timer(6)
        campaign._auto_search_in_stage_timer = Timer(3, count=6)
        campaign.appear = Mock(side_effect=self.appear)
        campaign.is_combat_executing = Mock(side_effect=lambda: 'COMBAT' in self.device.image)
        campaign.is_in_stage = Mock(side_effect=lambda: 'STAGE' in self.device.image)
        campaign.is_in_auto_search_menu = Mock(
            side_effect=lambda: AUTO_SEARCH_MENU_EXIT.name in self.device.image)

        def handle_in_stage():
            if campaign.is_in_stage():
                raise CampaignEnd('Withdraw')
            return False

        campaign.handle_in_stage = Mock(side_effect=handle_in_stage)
        # 无关的战斗控制与游戏弹窗不参与本组回放；结算、委托与自律处理使用真实代码。
        for name in (
            'handle_combat_automation_confirm', 'handle_combat_low_emotion', 'handle_retirement',
            'handle_submarine_call', 'handle_combat_auto', 'handle_combat_manual',
            'handle_popup_confirm', 'handle_story_skip', 'handle_guild_popup_cancel',
            'handle_vote_popup', 'handle_mission_popup_ack', 'handle_get_ship', 'handle_get_items',
        ):
            setattr(campaign, name, Mock(return_value=False))
        self.campaign = campaign
        return campaign

    def run(self, **kwargs):
        with patch('module.base.timer.time', side_effect=lambda: self.time):
            return self.campaign.auto_search_combat(**kwargs)


def retreat_frames(*prefix):
    """地图按钮连续稳定后点击撤退，最后用选关页正向确认退出。"""
    return [*prefix, *[screen(WITHDRAW, AUTO_SEARCH_MAP_OPTION_OFF)] * 5, screen('STAGE')]


class AutoSearchDefeatTests(unittest.TestCase):
    def test_logged_defeat_closes_commission_and_retires_instead_of_restarting(self):
        for cls in (AutoSearchCombat, GemsCampaignOverride):
            for policy in ('withdraw_continue', 'withdraw_stop'):
                with self.subTest(cls=cls.__name__, policy=policy):
                    replay = ScreenReplay(retreat_frames(
                        screen('COMBAT'), screen(EXP_INFO_D), screen(OPTS_INFO_D), screen(GET_MISSION)))
                    campaign = replay.make_campaign(cls, policy)
                    with self.assertRaises(CampaignEnd):
                        replay.run(fleet_index=2)
                    self.assertIn(GET_MISSION.name, replay.clicks)
                    self.assertIn(WITHDRAW.name, replay.clicks)
                    self.assertNotIn(AUTO_SEARCH_MAP_OPTION_OFF.name, replay.clicks)
                    self.assertEqual(campaign._defeat_count, int(policy == 'withdraw_stop'))
                    self.assertEqual(campaign.emotion.reduce.call_args_list, [call(2), call(2, shipwreck=True)])
                    self.assertTrue(campaign.is_in_stage())

    def test_execute_detects_shipwreck_before_experience_page(self):
        replay = ScreenReplay(retreat_frames(
            screen('COMBAT'), screen(OPTS_INFO_D), screen(EXP_INFO_D), screen(OPTS_INFO_D)))
        campaign = replay.make_campaign()
        with self.assertRaises(CampaignEnd):
            replay.run()
        self.assertEqual(campaign._defeat_count, 1)
        campaign.emotion.reduce.assert_has_calls([call(1), call(1, shipwreck=True)])
        self.assertEqual(campaign.emotion.reduce.call_count, 2)

    def test_repeated_shipwreck_popup_counts_and_reduces_emotion_once(self):
        replay = ScreenReplay(retreat_frames(
            screen('COMBAT'), screen(EXP_INFO_D), *[screen(OPTS_INFO_D)] * 5))
        campaign = replay.make_campaign()
        with self.assertRaises(CampaignEnd):
            replay.run()
        self.assertEqual(campaign._defeat_count, 1)
        self.assertEqual(campaign.emotion.reduce.call_args_list, [call(1), call(1, shipwreck=True)])
        self.assertEqual(replay.clicks.count(OPTS_INFO_D.name), 2)

    def test_gems_experience_override_records_defeat_before_dismissing_popup(self):
        for auto in (True, False):
            with self.subTest(auto=auto):
                replay = ScreenReplay([screen(OPTS_INFO_D)])
                campaign = replay.make_campaign()
                campaign.map_is_auto_search = auto
                campaign._auto_search_emotion_reduce = True
                with patch('module.base.timer.time', side_effect=lambda: replay.time):
                    self.assertTrue(campaign.handle_exp_info())
                self.assertEqual(replay.clicks, [OPTS_INFO_D.name])
                self.assertEqual(campaign._defeat_count, int(auto))
                self.assertEqual(campaign._withdraw, auto)
                self.assertEqual(campaign.emotion.reduce.call_count, int(auto))

    def test_shipwreck_is_recorded_even_while_click_is_on_cooldown(self):
        replay = ScreenReplay([screen(OPTS_INFO_D)])
        campaign = replay.make_campaign()
        with patch('module.base.timer.time', side_effect=lambda: replay.time):
            campaign.interval_reset(OPTS_INFO_D, interval=2)
            self.assertFalse(campaign.handle_auto_search_shipwreck())
        self.assertTrue(campaign._withdraw)
        self.assertTrue(campaign._auto_search_defeated)
        self.assertEqual(campaign._defeat_count, 1)
        self.assertEqual(replay.clicks, [])

    def test_defeat_with_ignored_emotion_still_retires(self):
        replay = ScreenReplay(retreat_frames(screen('COMBAT'), screen(EXP_INFO_D), screen(OPTS_INFO_D)))
        campaign = replay.make_campaign()
        with self.assertRaises(CampaignEnd):
            replay.run(emotion_reduce=False)
        campaign.emotion.reduce.assert_not_called()
        self.assertEqual(campaign._defeat_count, 1)

    def test_non_s_emotion_is_not_reduced_again_when_shipwreck_popup_appears(self):
        for grade in (EXP_INFO_A, EXP_INFO_B, EXP_INFO_C):
            with self.subTest(grade=grade.name):
                replay = ScreenReplay(retreat_frames(screen('COMBAT'), screen(grade), screen(OPTS_INFO_D)))
                campaign = replay.make_campaign()
                with self.assertRaises(CampaignEnd):
                    replay.run()
                self.assertEqual(campaign.emotion.reduce.call_args_list, [call(1), call(1, shipwreck=True)])
                self.assertEqual(campaign._defeat_count, 1)

    def test_transient_d_grade_does_not_trigger_retreat_without_shipwreck_popup(self):
        for grade in (BATTLE_STATUS_D, EXP_INFO_D):
            with self.subTest(grade=grade.name):
                replay = ScreenReplay([
                    screen('COMBAT'), screen(grade), screen(EXP_INFO_S), screen(AUTO_SEARCH_MAP_OPTION_ON)])
                campaign = replay.make_campaign()
                campaign._defeat_count = 2
                replay.run()
                self.assertFalse(campaign._withdraw)
                self.assertEqual(campaign._defeat_count, 0)
                campaign.emotion.reduce.assert_called_once_with(1)
                self.assertNotIn(WITHDRAW.name, replay.clicks)

    def test_settlement_timeout_does_not_invent_defeat(self):
        replay = ScreenReplay([
            screen('COMBAT'), *[screen()] * 12, screen(EXP_INFO_S), screen(AUTO_SEARCH_MAP_OPTION_ON)])
        campaign = replay.make_campaign()
        replay.run()
        self.assertFalse(campaign._withdraw)
        self.assertEqual(campaign._defeat_count, 0)
        campaign.emotion.reduce.assert_called_once_with(1)
        self.assertNotIn(WITHDRAW.name, replay.clicks)

    def test_running_auto_search_cannot_cancel_pending_retreat(self):
        for policy in ('withdraw_continue', 'withdraw_stop'):
            with self.subTest(policy=policy):
                replay = ScreenReplay(retreat_frames(
                    screen('COMBAT'), screen(EXP_INFO_D), screen(OPTS_INFO_D),
                    screen(AUTO_SEARCH_MAP_OPTION_ON), screen(GET_MISSION)))
                campaign = replay.make_campaign(policy=policy)
                campaign._defeat_count = 2
                expected = ScriptEnd if policy == 'withdraw_stop' else CampaignEnd
                with self.assertRaises(expected):
                    replay.run()
                self.assertIn(AUTO_SEARCH_MAP_OPTION_ON.name, replay.clicks)
                self.assertIn(WITHDRAW.name, replay.clicks)
                self.assertNotIn(AUTO_SEARCH_MAP_OPTION_OFF.name, replay.clicks)
                self.assertEqual(campaign._defeat_count, 3 if policy == 'withdraw_stop' else 2)

    def test_commission_after_withdraw_click_is_still_handled(self):
        replay = ScreenReplay([
            screen('COMBAT'), screen(EXP_INFO_D), screen(OPTS_INFO_D),
            *[screen(WITHDRAW)] * 4, screen(POPUP_CANCEL, POPUP_CONFIRM),
            screen(GET_MISSION), screen('STAGE')])
        campaign = replay.make_campaign()
        del campaign.handle_popup_confirm
        with self.assertRaises(CampaignEnd):
            replay.run()
        self.assertIn('POPUP_CONFIRM_WITHDRAW', replay.clicks)
        self.assertLess(replay.clicks.index(WITHDRAW.name), replay.clicks.index(GET_MISSION.name))
        self.assertTrue(campaign.is_in_stage())

    def test_withdraw_button_flicker_restarts_stability_wait(self):
        replay = ScreenReplay([
            screen('COMBAT'), screen(EXP_INFO_D), screen(OPTS_INFO_D),
            *[screen(WITHDRAW)] * 2, screen(), *[screen(WITHDRAW)] * 4, screen('STAGE')])
        replay.make_campaign()
        with self.assertRaises(CampaignEnd):
            replay.run()
        self.assertEqual([(index, name) for index, name in replay.click_frames if name == WITHDRAW.name],
                         [(9, WITHDRAW.name)])

    def test_third_defeat_stops_only_after_confirmed_exit(self):
        for exit_screen in ('withdraw', 'menu', 'stage'):
            with self.subTest(exit=exit_screen):
                prefix = [screen('COMBAT'), screen(EXP_INFO_D), screen(OPTS_INFO_D)]
                frames = retreat_frames(*prefix) if exit_screen == 'withdraw' else [
                    *prefix, *([screen(AUTO_SEARCH_MENU_EXIT)] if exit_screen == 'menu' else []), screen('STAGE')]
                replay = ScreenReplay(frames)
                campaign = replay.make_campaign()
                campaign._defeat_count = 2
                with self.assertRaisesRegex(ScriptEnd, 'DefeatWithdraw=withdraw_stop'):
                    replay.run()
                self.assertTrue(campaign.is_in_stage())
                self.assertEqual(campaign._defeat_count, 3)
                self.assertFalse(campaign._withdraw)

    def test_switch_policy_resumes_only_when_requested(self):
        for handoff in ('confirm', 'automatic'):
            with self.subTest(handoff=handoff):
                frames = [screen('COMBAT'), screen(EXP_INFO_D), screen(OPTS_INFO_D), screen(GET_MISSION)]
                if handoff == 'confirm':
                    frames.extend([screen(FLEET_SWITCH_CONFIRM, POPUP_CANCEL, POPUP_CONFIRM),
                                   screen(AUTO_SEARCH_MAP_OPTION_OFF)])
                replay = ScreenReplay([*frames, screen(AUTO_SEARCH_MAP_OPTION_ON)])
                campaign = replay.make_campaign(policy='switch_fleet')
                campaign._defeat_count = 2
                del campaign.handle_popup_confirm
                replay.run()
                if handoff == 'confirm':
                    self.assertIn(FLEET_SWITCH_CONFIRM.name, replay.clicks)
                    self.assertIn(AUTO_SEARCH_MAP_OPTION_OFF.name, replay.clicks)
                self.assertNotIn(WITHDRAW.name, replay.clicks)
                self.assertFalse(campaign.fleet_alive_multiple)
                self.assertFalse(campaign._withdraw)
                self.assertEqual(campaign._defeat_count, 2)

    def test_switch_policy_retires_when_only_one_fleet_survives(self):
        replay = ScreenReplay(retreat_frames(screen('COMBAT'), screen(EXP_INFO_D), screen(OPTS_INFO_D)))
        campaign = replay.make_campaign(policy='switch_fleet')
        campaign.fleet_alive_multiple = False
        with self.assertRaises(CampaignEnd):
            replay.run()
        self.assertIn(WITHDRAW.name, replay.clicks)
        self.assertNotIn(AUTO_SEARCH_MAP_OPTION_OFF.name, replay.clicks)

    def test_alternate_switch_flow_clicks_switch_once_and_handles_commission(self):
        replay = ScreenReplay([
            screen('COMBAT'), screen(EXP_INFO_D), screen(OPTS_INFO_D), *[screen(WITHDRAW)] * 4,
            screen(SWITCH_OVER), screen(SWITCH_OVER), screen(GET_MISSION), screen(FLEET_WITHDRAW),
            screen(AUTO_SEARCH_MAP_OPTION_OFF), screen(AUTO_SEARCH_MAP_OPTION_ON)])
        campaign = replay.make_campaign(policy='switch_fleet')
        replay.run()
        self.assertEqual(replay.clicks.count(SWITCH_OVER.name), 1)
        self.assertIn(GET_MISSION.name, replay.clicks)
        self.assertIn(FLEET_WITHDRAW.name, replay.clicks)
        self.assertIn(AUTO_SEARCH_MAP_OPTION_OFF.name, replay.clicks)
        self.assertNotIn(WITHDRAW.name, replay.clicks)
        self.assertFalse(campaign._withdraw)

    def test_failed_switch_falls_back_to_retreat_without_restarting(self):
        for failure in ('boss', 'timeout'):
            with self.subTest(failure=failure):
                prefix = [screen('COMBAT'), screen(EXP_INFO_D), screen(OPTS_INFO_D)]
                if failure == 'boss':
                    prefix.append(screen(FLEET_WITHDRAW_BOSS, POPUP_CANCEL, POPUP_CONFIRM))
                else:
                    prefix.extend([*[screen(WITHDRAW)] * 4, *[screen()] * 22])
                replay = ScreenReplay(retreat_frames(*prefix, screen(AUTO_SEARCH_MAP_OPTION_ON)))
                campaign = replay.make_campaign(policy='switch_fleet')
                del campaign.handle_popup_confirm
                with self.assertRaises(CampaignEnd):
                    replay.run()
                self.assertIn(WITHDRAW.name, replay.clicks)
                self.assertIn(AUTO_SEARCH_MAP_OPTION_ON.name, replay.clicks)
                self.assertNotIn(AUTO_SEARCH_MAP_OPTION_OFF.name, replay.clicks)
                self.assertNotIn('POPUP_CONFIRM_combat_status', replay.clicks)
                self.assertFalse(campaign.fleet_alive_multiple)

    def test_victory_between_defeats_resets_consecutive_count(self):
        loss = retreat_frames(screen('COMBAT'), screen(EXP_INFO_D), screen(OPTS_INFO_D))
        replay = ScreenReplay(loss)
        campaign = replay.make_campaign()
        for expected in (1, 2):
            replay.load(loss)
            with self.assertRaises(CampaignEnd):
                replay.run()
            self.assertEqual(campaign._defeat_count, expected)
        replay.load([screen('COMBAT'), screen(EXP_INFO_S), screen(AUTO_SEARCH_MAP_OPTION_ON)])
        replay.run()
        self.assertEqual(campaign._defeat_count, 0)
        replay.load(loss)
        with self.assertRaises(CampaignEnd):
            replay.run()
        self.assertEqual(campaign._defeat_count, 1)

    def test_retreat_errors_reach_existing_recovery(self):
        replay = ScreenReplay(retreat_frames(screen('COMBAT'), screen(EXP_INFO_D), screen(OPTS_INFO_D)))
        campaign = replay.make_campaign()
        def click(button):
            if button == WITHDRAW:
                raise GameStuckError('测试撤退异常')
            replay.click(button)

        campaign.device.click.side_effect = click
        with self.assertRaises(GameStuckError):
            replay.run()
        self.assertTrue(campaign._withdraw)

    def test_runner_disables_task_after_three_real_defeats(self):
        frames = retreat_frames(screen('COMBAT'), screen(EXP_INFO_D), screen(OPTS_INFO_D), screen(GET_MISSION))
        replay = ScreenReplay(frames)
        campaign = replay.make_campaign()

        def battle():
            replay.load(frames)
            try:
                campaign.auto_search_combat()
            except CampaignEnd:
                return True

        campaign.run = Mock(side_effect=battle)
        campaign.is_in_map = Mock(return_value=False)
        campaign.ensure_campaign_ui = Mock()
        campaign.event_time_limit_triggered = Mock(return_value=False)
        campaign.get_event_pt_limit = Mock(return_value=0)
        campaign.ensure_auto_search_exit = Mock()
        runner = object.__new__(CampaignRun)
        runner.config = SimpleNamespace(
            Scheduler_Enable=True, StopCondition_RunCount=0, StopCondition_MapAchievement='non_stop',
            modified={}, override=Mock(), task_switched=Mock(return_value=False))
        runner.device = replay.device
        runner.campaign = campaign
        runner.stage = '2-4'
        runner.handle_stage_name = Mock(return_value=('2-4', 'campaign_main'))
        runner.load_campaign = Mock()
        runner.ui_page_appear = Mock(return_value=False)
        runner.disable_raid_on_event = Mock()
        runner.handle_commission_notice = Mock()
        runner.triggered_stop_condition = Mock(return_value=False)
        runner.after_campaign_run = Mock()
        with patch('module.base.timer.time', side_effect=lambda: replay.time):
            runner._run('2-4', total=5)
        self.assertEqual(campaign.run.call_count, 3)
        self.assertFalse(runner.config.Scheduler_Enable)
        self.assertEqual(campaign._defeat_count, 3)
        self.assertTrue(campaign.is_in_stage())


if __name__ == '__main__':
    unittest.main()
