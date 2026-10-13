"""战斗执行与结算衔接时必须先取得新截图，保留多页掉落记录。"""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from module.combat.assets import GET_ITEMS_1, GET_ITEMS_2
from module.combat.combat import Combat


class CombatStatusFreshFrameTest(unittest.TestCase):
    @staticmethod
    def make_combat(frames):
        combat = Combat.__new__(Combat)
        index = 0
        events = []

        def screenshot():
            nonlocal index
            index += 1
            if index >= len(frames):
                raise AssertionError('结算未在预期页面结束')
            combat.device.image = frames[index]
            events.append(('screenshot', frames[index]))

        combat.device = SimpleNamespace(
            image=frames[0], screenshot=Mock(side_effect=screenshot),
            click=Mock(side_effect=lambda button: events.append(('click', frames[index]))),
            screenshot_interval_set=Mock(), stuck_record_clear=Mock(),
            click_record_clear=Mock(),
        )
        combat.config = SimpleNamespace()
        combat.battle_status_click_interval = 2
        combat.auto_mode_checked = False
        combat.appear = Mock(side_effect=lambda button, **kwargs:
                             (button is GET_ITEMS_1 and frames[index] == 'reward_1')
                             or (button is GET_ITEMS_2 and frames[index] == 'reward_2'))
        combat.interval_reset = Mock()
        for name in ('submarine_call_reset', 'combat_auto_reset', 'combat_manual_reset',
                     'handle_combat_automation_confirm', 'handle_story_skip',
                     'handle_combat_auto', 'handle_combat_manual', 'handle_submarine_call',
                     'handle_popup_confirm', 'handle_urgent_commission',
                     'handle_guild_popup_cancel', 'handle_vote_popup', 'handle_mission_popup_ack',
                     'handle_battle_status', 'handle_get_ship', 'handle_exp_info',
                     'handle_auto_search_exit', 'handle_combat_mis_click', 'handle_in_stage'):
            setattr(combat, name, Mock(return_value=False))
        drop = SimpleNamespace(handle_add=Mock(
            side_effect=lambda _: events.append(('record', frames[index]))))
        return combat, drop, events

    def test_execute_clicked_reward_is_not_processed_again_by_status(self):
        combat, drop, events = self.make_combat(['reward_1', 'stage'])

        combat.combat_execute(drop=drop)
        combat.combat_status(drop=drop, expected_end=lambda: combat.device.image == 'stage')

        self.assertEqual(events, [
            ('record', 'reward_1'), ('click', 'reward_1'), ('screenshot', 'stage')])
        drop.handle_add.assert_called_once_with(combat)

    def test_normal_multiple_reward_pages_record_each_new_page(self):
        combat, drop, events = self.make_combat(['reward_1', 'reward_2', 'stage'])

        combat.combat_execute(drop=drop)
        combat.combat_status(drop=drop, expected_end=lambda: combat.device.image == 'stage')

        self.assertEqual(events, [
            ('record', 'reward_1'), ('click', 'reward_1'), ('screenshot', 'reward_2'),
            ('record', 'reward_2'), ('click', 'reward_2'), ('screenshot', 'stage')])
        self.assertEqual(drop.handle_add.call_count, 2)

    def test_status_without_execute_also_refreshes_before_handlers(self):
        combat, drop, events = self.make_combat(['stale_reward', 'reward_1', 'stage'])

        combat.combat_status(drop=drop, expected_end=lambda: combat.device.image == 'stage')

        self.assertEqual(events[0], ('screenshot', 'reward_1'))
        drop.handle_add.assert_called_once_with(combat)
        combat.device.click.assert_called_once_with(GET_ITEMS_1)


if __name__ == '__main__':
    unittest.main()
