"""七种培养组合、可靠实读、逐船记录与中断恢复，不连接账号。"""

from copy import deepcopy
from datetime import timedelta
from itertools import product
from pathlib import Path
import json
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from module.api.protocol import ConfigChange, ApiError
from module.campaign.fleet_rotation import preflight_rotation
from module.campaign.fleet_training import after_battle_due, recover_reading
from module.combat.emotion import Emotion
from module.exception import (CampaignEnd, FleetManagementError, GameStuckError, GameTooManyClickError,
                              RequestHumanTakeover, ScriptEnd)
from module.os.ship_exp import ShipLevel
from module.awaken.assets import OCR_SHIP_LEVEL
from module.retire.fleet_training import AffinityReader, FleetTrainingReader, parse_affinity
from module.runtime.fleet_rotation import ROTATION_PATH
from module.runtime.fleet_training import (LEVEL_PATH, AFFINITY_PATH, TRAINING_PATH, completed,
                                           excluded, goals, due, observe, state)
from module.runtime.mind_calculator import catalog
from types import SimpleNamespace
from unittest.mock import Mock
import tests.test_fleet_rotation as rotation_cases


class TrainingRuleTests(unittest.TestCase):
    def test_affinity_navigation_waits_for_two_fresh_matching_values(self):
        reader = FleetTrainingReader.__new__(FleetTrainingReader)
        reader.device = SimpleNamespace(image=None, click=Mock())
        reader.loop = Mock(return_value=iter(range(3)))
        reader.appear = Mock(side_effect=[True, False])
        reader.ui_click = Mock()
        with patch('module.retire.fleet_training.AffinityReader') as factory:
            factory.return_value.read.side_effect = [None, 50, 50]
            self.assertEqual(50, reader._affinity())
        reader.device.click.assert_called_once()
        reader.ui_click.assert_called_once()

    def test_recover_known_affinity_and_detail_pages(self):
        reader = FleetTrainingReader.__new__(FleetTrainingReader)
        reader.device = SimpleNamespace(image=None, has_cached_image=True)
        reader.appear = Mock(side_effect=[False, True, True])
        reader.ui_click = Mock()
        reader._back_to_details = Mock()
        reader._back_to_formation = Mock()
        with patch('module.retire.fleet_training.AffinityReader') as factory:
            factory.return_value.read.return_value = 100
            reader.recover_page()
        reader.ui_click.assert_called_once()
        reader._back_to_details.assert_called_once()
        reader._back_to_formation.assert_called_once()

    def test_seven_modes_and_all_conditions_belong_to_one_ship(self):
        for mood, level, affinity in product((False, True), repeat=3):
            if not (mood or level or affinity):
                continue
            targets = dict(([('level', 120)] if level else []) + ([('affinity', 100)] if affinity else []))
            self.assertEqual(bool(targets), completed({'level': 120, 'affinity': 100}, targets))
            if level:
                self.assertFalse(completed({'level': 119, 'affinity': 100}, targets))
            if affinity:
                self.assertFalse(completed({'level': 125, 'affinity': 99}, targets))

    def test_unknown_or_old_affinity_cannot_combine_with_new_level(self):
        saved = {'records': {}}
        targets = {'level': 120, 'affinity': 100}
        self.assertFalse(observe(saved, '舰船A', {'level': 119, 'affinity': 100}, targets))
        self.assertFalse(observe(saved, '舰船A', {'level': 120}, targets))
        self.assertIsNone(saved['records']['舰船A']['affinity'])
        self.assertFalse(observe(saved, '舰船B', {'level': 1, 'affinity': 200}, targets))
        self.assertTrue(observe(saved, '舰船A', {'level': 120, 'affinity': 100}, targets))
        self.assertFalse(excluded(saved['records']['舰船B'], targets))
        self.assertTrue(excluded(saved['records']['舰船A'], dict(targets, level=125)))

    def test_affinity_parser_never_reads_the_fixed_100_as_current_value(self):
        for value in (0, 8, 37, 50, 99, 100, 200):
            self.assertEqual(value, parse_affinity(f'好感度达到100（当前{value}）'))
        for text in ('已达成', '好感度达到100', '当前100', '好感度达到100(当前201)', '好感度达到100(当前1'):
            self.assertIsNone(parse_affinity(text))

    def test_original_affinity_crops_include_white_gold_and_alternate_certificate(self):
        reader = AffinityReader()
        for value in (8, 37, 50, 100, 200):
            with self.subTest(value=value):
                pixels = np.asarray(Image.open(Path('tests/fixtures/fleet_training') / f'affinity_{value}.png'))
                self.assertEqual(value, reader.read(pixels))

    def test_original_level_crops_reuse_opsi_reader(self):
        reader = ShipLevel(OCR_SHIP_LEVEL)
        for value in (56, 99, 100, 105, 120, 125):
            with self.subTest(value=value):
                pixels = np.asarray(Image.open(Path('tests/fixtures/fleet_training') / f'level_{value}.png'))
                self.assertEqual(value, reader.ocr(pixels))


class TrainingIntegrationTests(unittest.TestCase):
    def setUp(self):
        rotation_cases.RotationIntegrationTests.setUp(self)
        self.stack.enter_context(patch('module.campaign.fleet_training.FleetManagement', return_value=self.manager))
        self.initial_names = {(fleet, slot): name for (fleet, slot), name in zip(
            ((f, s) for f in range(1, 7) for s in range(1, 7)), catalog()['ships'])}
        self.names = dict(self.initial_names)
        self.profile = {name: dict(level=80, affinity=50) for name in self.names.values()}
        self.manager.RESULT_PATH = 'FleetInfo.FleetInfo.Result'
        self.read_calls = []
        self.inventory_calls = []
        outer = self

        class Reader:
            def __init__(self, manager):
                self.manager = manager

            def inventory(self, fleets):
                outer.inventory_calls.append(set(fleets))
                outer.full_scan(fleets=fleets)
                return outer.config.cross_get(self.manager.RESULT_PATH)

            def read(self, fleet, slots, targets):
                outer.read_calls.append((fleet, list(slots), dict(targets)))
                return {slot: {metric: outer.profile[outer.names[fleet, slot]].get(metric) for metric in targets}
                        for slot in slots}
        self.reader_factory = self.stack.enter_context(patch('module.campaign.fleet_training.FleetTrainingReader', Reader))

    def full_scan(self, fleets=range(1, 7), **kwargs):
        result = deepcopy(self.config.cross_get(self.manager.RESULT_PATH, {}))
        for category, slots in (('main', (1, 2, 3)), ('vanguard', (4, 5, 6))):
            result.setdefault(category, {}).update({str(fleet): [dict(slot=slot, name=self.names[fleet, slot],
                   level=self.profile[self.names[fleet, slot]]['level'], occupied=True, review=False,
                   emotion=self.live[fleet][slot]) for slot in slots] for fleet in fleets})
        self.config.cross_set('FleetInfo.FleetInfo.Result', result)

    def snapshots(self):
        self.full_scan()
        return deepcopy(self.live)

    def rotate(self, source, slot, fleet):
        self.names[source, slot], self.names[fleet, slot] = self.names[fleet, slot], self.names[source, slot]
        self.live[source][slot], self.live[fleet][slot] = self.live[fleet][slot], self.live[source][slot]
        return self.snapshots()

    readings = rotation_cases.RotationIntegrationTests.readings

    def configure(self, mood=False, level=True, affinity=False, slots=(4,)):
        self.config.cross_set_many({ROTATION_PATH + '.Enabled': mood,
                                   ROTATION_PATH + '.Rules': json.dumps({'1': {'slots': list(slots), 'sources': [3]}}),
                                   ROTATION_PATH + '.State': {}, TRAINING_PATH: {},
                                   LEVEL_PATH + '.Enabled': level, LEVEL_PATH + '.Target': 120,
                                   AFFINITY_PATH + '.Enabled': affinity, AFFINITY_PATH + '.Target': 100})
        self.names = dict(self.initial_names)
        self.profile = {name: dict(level=80, affinity=50) for name in self.names.values()}
        self.live = {fleet: dict.fromkeys(range(1, 7), 150) for fleet in range(1, 7)}
        self.runner.__dict__.pop('_fleet_rotation_context', None)
        self.campaign.emotion = Emotion(self.config)
        self.manager.rotate_slot.reset_mock()
        self.read_calls.clear()
        self.inventory_calls.clear()
        self.full_scan()

    def test_seven_modes_execute_completion_or_temporary_swap_without_conflating_them(self):
        for mood, level, affinity in product((False, True), repeat=3):
            if not (mood or level or affinity):
                continue
            with self.subTest(mood=mood, level=level, affinity=affinity):
                self.configure(mood, level, affinity)
                name = self.names[1, 4]
                self.profile[name] = dict(level=120, affinity=100)
                if mood:
                    self.live[1][4] = 2
                preflight_rotation(self.runner, 5, 'normal')
                self.manager.rotate_slot.assert_called_once_with(3, 4, 1)
                saved = state(self.config)['records'].get(name, {})
                self.assertEqual(level or affinity, excluded(saved, goals(self.config)))
                if not level and not affinity:
                    self.assertNotIn('completed_for', saved)

    def test_only_one_of_two_goals_does_not_swap(self):
        for level, affinity in ((119, 100), (120, 99), (80, 50)):
            self.configure(level=True, affinity=True)
            self.profile[self.names[1, 4]] = dict(level=level, affinity=affinity)
            preflight_rotation(self.runner, 5, 'normal')
            self.manager.rotate_slot.assert_not_called()
            self.assertFalse(excluded(state(self.config)['records'][self.names[1, 4]], goals(self.config)))

    def test_multiple_ships_complete_independently_and_fixed_ships_are_not_marked(self):
        self.configure(level=True, affinity=True, slots=(4, 5))
        first, second, fixed = self.names[1, 4], self.names[1, 5], self.names[1, 1]
        self.profile[first] = self.profile[fixed] = dict(level=125, affinity=200)
        self.profile[second] = dict(level=120, affinity=99)
        preflight_rotation(self.runner, 5, 'normal')
        self.manager.rotate_slot.assert_called_once_with(3, 4, 1)
        records = state(self.config)['records']
        self.assertTrue(excluded(records[first], goals(self.config)))
        self.assertFalse(excluded(records[second], goals(self.config)))
        self.assertNotIn(fixed, records)

    def test_level_only_uses_detail_inventory_and_does_not_read_morale_until_a_swap_is_needed(self):
        self.configure()
        self.config.set_record(Emotion_Fleet1Value=150)
        preflight_rotation(self.runner, 5, 'normal')
        self.manager.run.assert_not_called()
        self.manager.prepare_rotation.assert_not_called()
        self.assertEqual([(1, [4], {'level': 120})], self.read_calls)
        self.campaign.emotion.check_reduce(5)

    def test_unknown_reading_delays_without_marking_completion(self):
        self.configure(affinity=True)
        name = self.names[1, 4]
        self.profile[name] = dict(level=120, affinity=None)
        with self.assertRaises(ScriptEnd):
            preflight_rotation(self.runner, 5, 'normal')
        self.manager.rotate_slot.assert_not_called()
        self.assertNotIn(name, state(self.config)['records'])

    def test_no_candidate_and_completed_candidate_delay_without_repeating_swaps(self):
        self.configure()
        self.profile[self.names[1, 4]]['level'] = 120
        self.profile[self.names[3, 4]]['level'] = 125
        with self.assertRaises(ScriptEnd):
            preflight_rotation(self.runner, 5, 'normal')
        self.manager.rotate_slot.assert_not_called()
        self.assertTrue(excluded(state(self.config)['records'][self.names[1, 4]], goals(self.config)))

    def test_temporary_morale_swap_preserves_progress_and_can_select_the_ship_again(self):
        self.configure(mood=True, level=True, affinity=True)
        original = self.names[1, 4]
        self.live[1][4] = 2
        preflight_rotation(self.runner, 5, 'normal')
        self.assertFalse(excluded(state(self.config)['records'][original], goals(self.config)))
        self.assertEqual(80, state(self.config)['records'][original]['level'])
        self.live[3][4], self.live[1][4] = 150, 2
        self.runner._fleet_rotation_context['scanned'] = False
        preflight_rotation(self.runner, 5, 'normal')
        self.assertEqual(original, self.names[1, 4])

    def test_restart_after_confirmed_swap_does_not_swap_again_and_keeps_completion_on_disk(self):
        self.configure(mood=True)
        original = self.names[1, 4]
        self.profile[original]['level'] = 120
        def interrupt(source, slot, fleet):
            self.rotate(source, slot, fleet)
            raise GameStuckError('模拟确认后进程中断')
        self.manager.rotate_slot.side_effect = interrupt
        with self.assertRaises(FleetManagementError):
            preflight_rotation(self.runner, 5, 'normal')
        saved = self.service.read_json(self.path)['FleetInfo']['FleetTraining']['State']
        self.assertTrue(saved['pending'])
        self.assertIn('completed_for', saved['records'][original])
        self.runner.__dict__.pop('_fleet_rotation_context', None)
        self.manager.rotate_slot.side_effect = self.rotate
        self.manager.rotate_slot.reset_mock()
        preflight_rotation(self.runner, 5, 'normal')
        self.manager.rotate_slot.assert_not_called()
        self.assertFalse(state(self.config)['pending'])

    def test_training_only_restart_rescans_both_sides_after_game_confirm_before_save(self):
        self.configure()
        original = self.names[1, 4]
        self.profile[original]['level'] = 120
        def interrupt(source, slot, fleet):
            self.names[source, slot], self.names[fleet, slot] = self.names[fleet, slot], self.names[source, slot]
            raise GameStuckError('模拟游戏确认后、结果保存前中断')
        self.manager.rotate_slot.side_effect = interrupt
        with self.assertRaises(FleetManagementError):
            preflight_rotation(self.runner, 5, 'normal')
        self.runner.__dict__.pop('_fleet_rotation_context', None)
        self.manager.rotate_slot.side_effect = self.rotate
        self.manager.rotate_slot.reset_mock()
        self.inventory_calls.clear()
        preflight_rotation(self.runner, 5, 'normal')
        self.assertEqual({1, 3}, self.inventory_calls[0])
        self.manager.rotate_slot.assert_not_called()
        self.assertFalse(state(self.config)['pending'])
        self.assertTrue(excluded(state(self.config)['records'][original], goals(self.config)))

    def test_training_only_swap_errors_require_human_review_and_keep_pending(self):
        for error in (GameStuckError, GameTooManyClickError, RequestHumanTakeover):
            with self.subTest(error=error.__name__):
                self.configure(mood=False, level=False, affinity=True)
                self.profile[self.names[1, 4]]['affinity'] = 100
                self.manager.rotate_slot.side_effect = error('模拟换船未确认')
                with self.assertRaises(FleetManagementError) as raised:
                    preflight_rotation(self.runner, 5, 'normal')
                self.assertIsInstance(raised.exception.__cause__, error)
                self.assertEqual({'1:4'}, set(state(self.config)['pending']))
                self.manager.rotate_slot.assert_called_once_with(3, 4, 1)
                self.manager._commit_formation.assert_not_called()
                self.campaign.ensure_campaign_ui.assert_not_called()

    def test_switching_training_off_mid_swap_preserves_unexecuted_job_for_restart(self):
        self.configure(slots=(4, 5))
        for slot in (4, 5):
            self.profile[self.names[1, slot]]['level'] = 120
        def disable(source, slot, fleet):
            readings = self.rotate(source, slot, fleet)
            self.config.cross_set(LEVEL_PATH + '.Enabled', False)
            return readings
        self.manager.rotate_slot.side_effect = disable
        preflight_rotation(self.runner, 5, 'normal')
        self.manager.rotate_slot.assert_called_once_with(3, 4, 1)
        self.assertEqual({'1:5'}, set(state(self.config)['pending']))
        self.config.cross_set(LEVEL_PATH + '.Enabled', True)
        self.runner.__dict__.pop('_fleet_rotation_context', None)
        self.manager.rotate_slot.side_effect = self.rotate
        self.manager.rotate_slot.reset_mock()
        preflight_rotation(self.runner, 5, 'normal')
        self.manager.rotate_slot.assert_called_once_with(3, 5, 1)
        self.assertFalse(state(self.config)['pending'])

    def test_completion_swap_uses_existing_optional_equipment_flow(self):
        self.configure(mood=True)
        self.profile[self.names[1, 4]]['level'] = 120
        self.config.cross_set('FleetInfo.FleetEquipment.Enabled', True)
        with patch('module.campaign.fleet_training.FleetRotationEquipment') as equipment:
            preflight_rotation(self.runner, 5, 'normal')
        equipment.return_value.clear_outgoing.assert_called_once_with(1, [4])
        equipment.return_value.apply_pending.assert_called_once()
        self.manager._commit_formation.assert_called_once()

    def test_interval_defers_reads_and_changed_goals_force_a_fresh_check(self):
        self.configure()
        preflight_rotation(self.runner, 5, 'normal')
        self.read_calls.clear()
        self.assertFalse(preflight_rotation(self.runner, 5, 'normal'))
        self.assertFalse(self.read_calls)
        self.assertFalse(due(self.config, self.runner._fleet_rotation_context))
        self.config.cross_set(LEVEL_PATH + '.Target', 125)
        self.assertTrue(due(self.config, self.runner._fleet_rotation_context))
        self.campaign._map_battle = 5
        self.assertTrue(after_battle_due(self.runner, 'normal'))
        self.runner._fleet_rotation_context['training_checked_at'] -= timedelta(hours=1)
        self.assertTrue(after_battle_due(self.runner, 'normal'))

    def test_config_flags_targets_and_intervals_are_independent_and_state_is_read_only(self):
        changes = [ConfigChange(path=LEVEL_PATH + '.Enabled', value=True),
                   ConfigChange(path=LEVEL_PATH + '.Target', value=125),
                   ConfigChange(path=LEVEL_PATH + '.CheckInterval', value=0),
                   ConfigChange(path=AFFINITY_PATH + '.Enabled', value=True),
                   ConfigChange(path=AFFINITY_PATH + '.Target', value=200)]
        values = self.service.patch('testpilot', None, changes)['values']['FleetInfo']
        self.assertEqual(125, values['FleetLevelTraining']['Target'])
        self.assertEqual(200, values['FleetAffinityTraining']['Target'])
        for path, value in ((LEVEL_PATH + '.Target', 126), (AFFINITY_PATH + '.Target', 201),
                            (LEVEL_PATH + '.CheckInterval', -1), (TRAINING_PATH, {'records': {}})):
            with self.assertRaises(ApiError):
                self.service.patch('testpilot', None, [ConfigChange(path=path, value=value)])

    def test_recorded_reading_is_recovered_before_stage_navigation_even_after_disable(self):
        self.configure(level=False)
        with patch('module.campaign.fleet_training.FleetTrainingReader') as reader:
            recover_reading(self.runner, 'normal')
            reader.assert_not_called()
            self.config.cross_set(TRAINING_PATH, {'reading': {'fleet': 1, 'slots': [4]}})
            recover_reading(self.runner, 'normal')
            reader.return_value.recover_page.assert_called_once()
            reader.reset_mock()
            recover_reading(self.runner, 'hard')
            reader.assert_not_called()

    def test_after_temporary_swap_uses_task_morale_and_selected_only_switch(self):
        for selected_only in (False, True):
            self.configure(mood=True, level=True, affinity=True)
            self.config.cross_set(ROTATION_PATH + '.SelectedOnly', selected_only)
            self.live[1][1], self.live[1][4] = 0, 2
            preflight_rotation(self.runner, 5, 'normal')
            self.manager.rotate_slot.assert_called_once()
            if selected_only:
                self.campaign.emotion.check_reduce(5)
            else:
                with self.assertRaises(ScriptEnd):
                    self.campaign.emotion.check_reduce(5)

    def test_level_or_affinity_only_low_morale_delays_without_temporary_swap(self):
        for level, affinity in ((True, False), (False, True), (True, True)):
            self.configure(level=level, affinity=affinity)
            self.config.set_record(Emotion_Fleet1Value=2)
            preflight_rotation(self.runner, 5, 'normal')
            self.manager.rotate_slot.assert_not_called()
            with self.assertRaises(ScriptEnd):
                self.campaign.emotion.check_reduce(5)

    def test_combined_training_keeps_fixed_red_confirmation_and_training_unknown_protection(self):
        for level, affinity in ((True, False), (False, True), (True, True)):
            self.configure(mood=True, level=level, affinity=affinity)
            self.config.cross_set(ROTATION_PATH + '.SelectedOnly', True)
            self.live[1][1] = 0
            preflight_rotation(self.runner, 5, 'normal')
            emotion = self.campaign.emotion
            handler = rotation_cases.RotationIntegrationTests.popup_handler(self, emotion)
            self.assertTrue(rotation_cases.InfoHandler.handle_combat_low_emotion(handler))
            handler.handle_popup_confirm.assert_called_once_with('IGNORE_LOW_EMOTION')
            self.assertEqual(150, emotion.training_lower(1))
            self.assertFalse(excluded(state(self.config)['records'][self.names[1, 4]], goals(self.config)))
            emotion.context['training'][1]['state'] = None
            handler = rotation_cases.RotationIntegrationTests.popup_handler(self, emotion)
            with self.assertRaises(ScriptEnd):
                rotation_cases.InfoHandler.handle_combat_low_emotion(handler)
            handler.handle_popup_confirm.assert_not_called()
            handler.handle_popup_cancel.assert_called_once()

    def test_oil_shortage_uses_existing_task_delay_without_disabling_scheduler(self):
        from module.campaign.run import CampaignRun
        self.configure()
        self.config.StopCondition_ReachLevel = 0
        enabled = self.config.Scheduler_Enable
        runner = SimpleNamespace(config=self.config, campaign=self.campaign, run_limit=False,
                                 status_get_gems=Mock(), get_coin=Mock(), get_oil=Mock(return_value=100))
        self.assertTrue(CampaignRun.triggered_stop_condition(runner))
        self.assertEqual(enabled, self.config.Scheduler_Enable)
        self.manager.rotate_slot.assert_not_called()

    def test_changed_goals_drop_unexecuted_pending_job_and_recheck_current_ship(self):
        self.configure()
        self.profile[self.names[1, 4]]['level'] = 120
        saved = state(self.config)
        saved['pending']['1:4'] = dict(fleet=1, slot=4, source=3, outgoing=self.names[1, 4],
                                       incoming=self.names[3, 4], targets={'level': 120})
        self.config.cross_set_many({TRAINING_PATH: saved, LEVEL_PATH + '.Target': 125})
        preflight_rotation(self.runner, 5, 'normal')
        self.manager.rotate_slot.assert_not_called()
        self.assertFalse(state(self.config)['pending'])

    def test_duplicate_names_and_corrupt_records_do_not_mark_completion(self):
        self.configure()
        self.names[3, 4] = self.names[1, 4]
        self.full_scan()
        self.profile[self.names[1, 4]]['level'] = 125
        with self.assertRaises(ScriptEnd):
            preflight_rotation(self.runner, 5, 'normal')
        self.assertFalse(state(self.config)['records'])
        corrupt = {'records': [], 'pending': {}}
        self.config.cross_set(TRAINING_PATH, corrupt)
        with self.assertRaises(FleetManagementError):
            preflight_rotation(self.runner, 5, 'normal')
        self.assertEqual(corrupt, self.config.cross_get(TRAINING_PATH))

    def test_due_check_finishes_current_map_without_withdraw_or_extra_battles(self):
        from module.campaign.campaign_base import CampaignBase
        campaign = CampaignBase.__new__(CampaignBase)
        campaign.__dict__.update(vars(self.campaign))
        campaign._map_battle = 5
        campaign.ENTRANCE = SimpleNamespace(area=None, button=(1, 2, 3, 4))
        campaign.map_is_auto_search = True
        campaign.map_get_info = Mock()
        campaign.enter_map = Mock()
        campaign.emotion = SimpleNamespace(check_reduce=Mock())
        campaign.MAP = object()
        campaign.lv_reset = campaign.lv_get = Mock()
        campaign.auto_search_execute_a_battle = Mock(side_effect=[None, None, CampaignEnd])
        campaign.auto_search_withdraw = Mock()
        campaign.AUTO_SEARCH_WITHDRAW = False
        campaign._fleet_training_after_battle = Mock(return_value=True)
        self.assertTrue(campaign.run())
        self.assertEqual(3, campaign.auto_search_execute_a_battle.call_count)
        campaign.auto_search_withdraw.assert_not_called()
        self.assertEqual(3, campaign._fleet_training_after_battle.call_count)
