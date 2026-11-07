"""货运装载、出发与待补货状态必须由新截图确认。"""

from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.island import island_cargo_preparation as cargo_module
from module.island.island_cargo_preparation import CargoPreparationTransport, IslandCargoPreparation
from module.map.map_grids import SelectedGrids


class CargoStartStateTests(unittest.TestCase):
    def make(self, frames):
        cargo = IslandCargoPreparation.__new__(IslandCargoPreparation)
        cargo.interval_clear = Mock()
        frame = {}
        clock = {'now': 100.0}
        calls = []
        self.enterContext(patch('module.base.timer.time', side_effect=lambda: clock['now']))
        self.enterContext(patch.object(cargo_module, 'logger'))

        def loop(**kwargs):
            self.assertEqual(kwargs, {'timeout': 30, 'skip_first': False})
            for value in frames:
                clock['now'] += 0.4
                frame.clear()
                frame.update(value)
                yield None

        def click(button, **kwargs):
            self.assertIs(button, cargo_module.TRANSPORT_START)
            self.assertEqual(kwargs, {'offset': (0, 176), 'interval': 2})
            if frame.get('clickable', True):
                calls.append('load')
                return True
            return False

        cargo.loop = loop
        cargo.appear = lambda button, **kwargs: button is cargo_module.GET_ITEMS_ISLAND and frame.get('reward', False)
        cargo.handle_get_items = Mock(side_effect=lambda: frame.get('dismissed', True))
        cargo.handle_popup_cancel = Mock(return_value=False)
        cargo.island_in_transport = lambda: frame.get('page', True)
        cargo.match_template_color = lambda *args, **kwargs: frame.get('active', False)
        cargo.appear_then_click = Mock(side_effect=click)
        comm = SimpleNamespace(index=1, offset=(0, 176), get_transport_status=lambda main: frame.get('state', 'unknown'))
        return cargo, comm, calls

    def test_partial_loading_reward_does_not_count_as_departure(self):
        frames = [{'state': 'pending', 'active': True}, {'reward': True}]
        frames += [{'state': 'pending'}] * 5
        cargo, comm, clicks = self.make(frames)
        self.assertFalse(cargo.transport_start(comm))
        self.assertEqual(clicks, ['load'])

    def test_active_loading_button_is_processed_again_after_reward(self):
        frames = [{'state': 'pending', 'active': True}, {'reward': True}]
        frames += [{'state': 'pending', 'active': True, 'clickable': False}] * 4
        frames += [{'state': 'pending', 'active': True}]
        frames += [{'state': 'running'}] * 5
        cargo, comm, clicks = self.make(frames)
        self.assertTrue(cargo.transport_start(comm))
        self.assertEqual(clicks, ['load', 'load'])

    def test_reward_cooldown_cannot_confirm_background_state(self):
        frames = [{'state': 'running', 'reward': True, 'dismissed': False}] * 6
        cargo, comm, clicks = self.make(frames)
        self.assertFalse(cargo.transport_start(comm))
        self.assertEqual(clicks, [])

    def test_confirmed_running_or_finished_needs_no_loading_click(self):
        for state in ('running', 'finished'):
            with self.subTest(state=state):
                cargo, comm, clicks = self.make([{'state': state}] * 5)
                self.assertTrue(cargo.transport_start(comm))
                self.assertEqual(clicks, [])

    def test_transient_running_frame_does_not_hide_pending_state(self):
        frames = [{'state': 'running'}] * 2 + [{'state': 'pending'}] * 5
        cargo, comm, clicks = self.make(frames)
        self.assertFalse(cargo.transport_start(comm))
        self.assertEqual(clicks, [])

    def test_unknown_page_after_click_does_not_report_success(self):
        frames = [{'state': 'pending', 'active': True}] + [{'page': False}] * 6
        cargo, comm, clicks = self.make(frames)
        self.assertFalse(cargo.transport_start(comm))
        self.assertEqual(clicks, ['load'])


def commission(status, start=False, index=0):
    comm = CargoPreparationTransport.__new__(CargoPreparationTransport)
    comm.index = index
    comm.status = status
    comm.start = start
    comm.valid = True
    comm.locked = False
    comm.refresh = False
    comm.replace_unfillable = False
    comm.short = status == 'pending'
    comm.delivered = status == 'pending'
    comm.duration = timedelta(hours=1)
    comm.create_time = datetime(2026, 11, 7, 10)
    return comm


class CargoResultStateTests(unittest.TestCase):
    def make(self, snapshots):
        cargo = IslandCargoPreparation.__new__(IslandCargoPreparation)
        cargo.transport_receive = Mock()
        cargo.transport_detect = Mock(side_effect=snapshots)
        cargo.transport_start = Mock(return_value=True)
        self.enterContext(patch.object(cargo_module, 'logger'))
        return cargo

    def test_round_uses_actual_pending_after_successful_loading(self):
        before = commission('pending', start=True)
        after = commission('pending')
        cargo = self.make([SelectedGrids([before]), SelectedGrids([after])])
        result = cargo._run_cargo_preparation()
        self.assertIs(result[0], after)
        self.assertEqual(result[0].status, 'pending')
        self.assertIsNone(result[0].finish_time)
        self.assertEqual(before.status, 'pending')
        cargo.transport_detect.assert_called_with(trial=5, skip_first_screenshot=False)
        cargo.transport_start.assert_called_once_with(before)

    def test_round_uses_fresh_remaining_time_after_departure(self):
        before = commission('pending', start=True)
        after = commission('running')
        after.duration = timedelta(minutes=58)
        cargo = self.make([SelectedGrids([before]), SelectedGrids([after])])
        result = cargo._run_cargo_preparation()
        self.assertIs(result[0], after)
        self.assertEqual(result[0].finish_time, datetime(2026, 11, 7, 10, 58))

    def test_timeout_does_not_skip_final_state_detection(self):
        before = commission('pending', start=True)
        cargo = self.make([SelectedGrids([before]), SelectedGrids([])])
        cargo.transport_start.return_value = False
        self.assertEqual(cargo._run_cargo_preparation().count, 0)
        self.assertEqual(cargo.transport_detect.call_count, 2)

    def test_finished_result_is_received_and_detected_again(self):
        before = commission('pending', start=True)
        finished = commission('finished')
        after = commission('empty')
        cargo = self.make([SelectedGrids([before]), SelectedGrids([finished]), SelectedGrids([after])])
        result = cargo._run_cargo_preparation()
        self.assertIs(result[0], after)
        self.assertEqual(cargo.transport_receive.call_count, 2)
        self.assertEqual(cargo.transport_detect.call_count, 3)

    def test_only_running_and_refreshing_have_completion_deadlines(self):
        for state in ('pending', 'empty', 'finished', 'locked', 'unknown', 'running', 'refreshing'):
            with self.subTest(state=state):
                value = commission(state).finish_time
                expected = datetime(2026, 11, 7, 11) if state in ('running', 'refreshing') else None
                self.assertEqual(value, expected)

    def test_pending_duration_does_not_postpone_running_deadline(self):
        pending = commission('pending')
        pending.duration = timedelta(hours=6)
        running = commission('running')
        cargo = self.make([])
        cargo.config = SimpleNamespace(task_delay=Mock())
        with patch.object(cargo_module, 'current_time', return_value=datetime(2026, 11, 7, 10)):
            cargo._schedule_next_run(SelectedGrids([pending, running]))
        cargo.config.task_delay.assert_called_once_with(target=datetime(2026, 11, 7, 11))


if __name__ == '__main__':
    unittest.main()
