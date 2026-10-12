"""验证地球仪导航的详情恢复、坐标进展和有界失败，不连接游戏。"""

import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from module.exception import GameStuckError, GameTooManyClickError
from module.os.globe_camera import GlobeCamera


@dataclass(frozen=True)
class GlobeFrame:
    """截图对应的已识别摄像机坐标和详情状态。"""

    name: str
    camera: tuple[float, float] = (1000, 1000)
    pinned: bool = False
    handled: bool = True
    elapsed: float = 1


class GlobeNavigationTests(unittest.TestCase):
    def setUp(self):
        self.monotonic = 1000.0
        self.frames = iter(())
        self.frame = GlobeFrame('入口')
        self.events = []
        self.zone = SimpleNamespace(zone_id=153, location=(2200, 1000))
        self.camera = GlobeCamera.__new__(GlobeCamera)
        self.camera.config = SimpleNamespace(OS_GLOBE_SWIPE_MULTIPLY=(1, 1))
        self.camera.globe = SimpleNamespace(
            homo_center=np.array((500, 420)),
            globe2screen=lambda points: np.array(points),
        )
        self.camera.globe_camera = np.array(self.frame.camera, dtype=float)
        self.camera.device = SimpleNamespace(save_screenshot=Mock())
        self.camera.name_to_zone = Mock(return_value=self.zone)
        self.camera.is_zone_pinned = Mock(side_effect=lambda: self.frame.pinned)
        self.camera.handle_zone_pinned = Mock(side_effect=self.handle_pinned)
        self.camera.globe_update = Mock(side_effect=self.update)
        self.camera.globe_swipe = Mock(side_effect=self.swipe)
        self.enterContext(patch('module.base.timer.time', side_effect=lambda: self.monotonic))
        self.enterContext(patch('module.os.globe_camera.logger'))

    def update(self):
        try:
            self.frame = next(self.frames)
        except StopIteration:
            self.fail('导航没有在提供的模拟画面内结束')
        self.monotonic += self.frame.elapsed
        self.camera.globe_camera = np.array(self.frame.camera, dtype=float)
        self.events.append(('update', self.frame.name))

    def swipe(self, vector):
        self.assertFalse(self.frame.pinned, '详情展开或关闭冷却中不能普通滑动')
        self.events.append(('swipe', self.frame.name, tuple(vector)))
        self.update()

    def handle_pinned(self):
        self.events.append(('handle', self.frame.name, self.frame.handled))
        return self.frame.handled

    def run_frames(self, frames, *, initial=None, **kwargs):
        if initial is not None:
            self.frame = initial
            self.camera.globe_camera = np.array(initial.camera, dtype=float)
        self.frames = iter(frames)
        return self.camera.globe_in_sight('纽约港', **kwargs)

    def assert_saved(self):
        self.camera.device.save_screenshot.assert_called_once_with(
            genre='opsi_globe_navigation', interval=0,
        )

    def test_visible_target_returns_without_swipe(self):
        self.zone.location = (1400, 1000)
        self.assertIsNone(self.run_frames([]))
        self.camera.name_to_zone.assert_called_once_with('纽约港')
        self.camera.globe_swipe.assert_not_called()
        self.camera.handle_zone_pinned.assert_not_called()
        self.camera.device.save_screenshot.assert_not_called()

    def test_initial_visible_target_must_first_close_detail(self):
        self.zone.location = (1400, 1000)
        self.assertIsNone(self.run_frames(
            [GlobeFrame('详情关闭')], initial=GlobeFrame('入口详情', pinned=True),
        ))
        self.camera.handle_zone_pinned.assert_called_once_with()
        self.camera.globe_update.assert_called_once_with()
        self.camera.globe_swipe.assert_not_called()

    def test_detail_appearing_after_swipe_is_closed_before_next_swipe(self):
        self.assertIsNone(self.run_frames([
            GlobeFrame('滑动后详情', pinned=True),
            GlobeFrame('关闭后地图', camera=(1300, 1000)),
            GlobeFrame('目标可见', camera=(1800, 1000)),
        ]))
        self.assertEqual([event[0] for event in self.events], [
            'swipe', 'update', 'handle', 'update', 'swipe', 'update',
        ])
        self.camera.handle_zone_pinned.assert_called_once_with()

    def test_detail_cooldown_keeps_refreshing_without_swipe(self):
        self.assertIsNone(self.run_frames([
            GlobeFrame('关闭冷却二', pinned=True, handled=False),
            GlobeFrame('可以关闭', pinned=True),
            GlobeFrame('目标可见', camera=(1800, 1000)),
        ], initial=GlobeFrame('关闭冷却一', pinned=True, handled=False)))
        self.assertEqual(self.camera.handle_zone_pinned.call_count, 3)
        self.assertEqual(self.camera.globe_update.call_count, 3)
        self.camera.globe_swipe.assert_not_called()

    def test_closed_detail_recalculates_vector_from_new_coordinates(self):
        self.assertIsNone(self.run_frames([
            GlobeFrame('详情关闭定位', camera=(1600, 1000)),
            GlobeFrame('目标可见', camera=(1800, 1000)),
        ], initial=GlobeFrame('入口详情', pinned=True)))
        self.camera.globe_swipe.assert_called_once_with((570.0, 0.0))

    def test_three_stationary_swipes_save_and_raise(self):
        with self.assertRaisesRegex(GameStuckError, '连续 3 次'):
            self.run_frames([GlobeFrame(f'没有移动{i}') for i in range(3)])
        self.assertEqual(self.camera.globe_swipe.call_count, 3)
        self.assert_saved()

    def test_coordinate_jitter_below_threshold_is_not_progress(self):
        with self.assertRaises(GameStuckError):
            self.run_frames([
                GlobeFrame('抖动一', camera=(1003, 1000)),
                GlobeFrame('抖动二', camera=(1000, 1000)),
                GlobeFrame('抖动三', camera=(1004, 1000)),
            ])
        self.assertEqual(self.camera.globe_swipe.call_count, 3)
        self.assert_saved()

    def test_detail_after_third_stationary_swipe_can_still_recover(self):
        self.assertIsNone(self.run_frames([
            GlobeFrame('无进展一'), GlobeFrame('无进展二'),
            GlobeFrame('第三次出现详情', pinned=True),
            GlobeFrame('关闭详情'),
            GlobeFrame('目标可见', camera=(1800, 1000)),
        ]))
        self.assertEqual(self.camera.globe_swipe.call_count, 4)
        self.camera.handle_zone_pinned.assert_called_once_with()
        self.camera.device.save_screenshot.assert_not_called()

    def test_target_seen_after_third_small_swipe_succeeds_before_stall_check(self):
        self.zone.location = (1490, 1000)
        self.assertIsNone(self.run_frames([
            GlobeFrame('微调一', camera=(1001, 1000)),
            GlobeFrame('微调二', camera=(1002, 1000)),
            GlobeFrame('目标刚可见', camera=(1008, 1000)),
        ]))
        self.assertEqual(self.camera.globe_swipe.call_count, 3)
        self.camera.device.save_screenshot.assert_not_called()

    def test_real_progress_resets_stationary_counter(self):
        self.assertIsNone(self.run_frames([
            GlobeFrame('无进展一'), GlobeFrame('无进展二'),
            GlobeFrame('已有进展', camera=(1020, 1000)),
            GlobeFrame('再无进展一', camera=(1020, 1000)),
            GlobeFrame('再无进展二', camera=(1020, 1000)),
            GlobeFrame('目标可见', camera=(1800, 1000)),
        ]))
        self.assertEqual(self.camera.globe_swipe.call_count, 6)
        self.camera.device.save_screenshot.assert_not_called()

    def test_small_progress_accumulates_against_latest_progress_anchor(self):
        self.assertIsNone(self.run_frames([
            *(GlobeFrame(f'有效微调{i}', camera=(1000 + i * 4, 1000)) for i in range(1, 7)),
            GlobeFrame('目标可见', camera=(1800, 1000)),
        ]))
        self.assertEqual(self.camera.globe_swipe.call_count, 7)
        self.camera.device.save_screenshot.assert_not_called()

    def test_exact_threshold_uses_two_dimensional_distance(self):
        self.assertIsNone(self.run_frames([
            GlobeFrame('无进展一'), GlobeFrame('无进展二'),
            GlobeFrame('十像素进展', camera=(1006, 1008)),
            GlobeFrame('进展后等待一', camera=(1006, 1008)),
            GlobeFrame('进展后等待二', camera=(1006, 1008)),
            GlobeFrame('目标可见', camera=(1800, 1000)),
        ]))
        self.assertEqual(self.camera.globe_swipe.call_count, 6)
        self.camera.device.save_screenshot.assert_not_called()

    def test_repeated_detail_recovery_does_not_extend_total_timeout(self):
        with self.assertRaisesRegex(GameStuckError, '导航超过 30 秒'):
            self.run_frames(
                [GlobeFrame(f'再次详情{i}', pinned=True, elapsed=8) for i in range(4)],
                initial=GlobeFrame('入口详情', pinned=True),
            )
        self.assertEqual(self.camera.handle_zone_pinned.call_count, 4)
        self.camera.globe_swipe.assert_not_called()
        self.assert_saved()

    def test_detail_cooldown_also_has_total_timeout(self):
        with self.assertRaisesRegex(GameStuckError, '导航超过 30 秒'):
            self.run_frames(
                [GlobeFrame(f'冷却{i}', pinned=True, handled=False, elapsed=8) for i in range(4)],
                initial=GlobeFrame('入口冷却', pinned=True, handled=False),
            )
        self.assertEqual(self.camera.handle_zone_pinned.call_count, 4)
        self.camera.globe_swipe.assert_not_called()
        self.assert_saved()

    def test_navigation_with_progress_is_still_bounded_by_total_timeout(self):
        with self.assertRaisesRegex(GameStuckError, '导航超过 30 秒'):
            self.run_frames([
                GlobeFrame(f'缓慢移动{i}', camera=(1000 + i * 20, 1000), elapsed=11)
                for i in range(1, 4)
            ])
        self.assertEqual(self.camera.globe_swipe.call_count, 3)
        self.assert_saved()

    def test_slow_final_frame_already_at_target_finishes_without_extra_operation(self):
        self.assertIsNone(self.run_frames([
            GlobeFrame('慢速到达', camera=(1800, 1000), elapsed=31),
        ]))
        self.camera.globe_swipe.assert_called_once()
        self.camera.handle_zone_pinned.assert_not_called()
        self.camera.device.save_screenshot.assert_not_called()

    def test_original_vector_clipping_multiplier_and_swipe_limits_are_preserved(self):
        self.zone.location = (-200, 3000)
        self.camera.config.OS_GLOBE_SWIPE_MULTIPLY = (2, 4)
        self.assertIsNone(self.run_frames([
            GlobeFrame('自定义目标可见', camera=(-200, 3000)),
        ], swipe_limit=(300, 80)))
        self.camera.globe_swipe.assert_called_once_with((-300.0, 80.0))

    def test_custom_sight_area_controls_arrival(self):
        self.zone.location = (1500, 1000)
        self.assertIsNone(self.run_frames([
            GlobeFrame('自定义视野内', camera=(1500, 1000)),
        ], sight=(200, 200, 500, 500)))
        self.camera.globe_swipe.assert_called_once_with((500.0, 0.0))

    def test_existing_swipe_failure_is_propagated_without_masking(self):
        error = GameTooManyClickError('既有操作保护')
        self.camera.globe_swipe.side_effect = error
        with self.assertRaises(GameTooManyClickError) as raised:
            self.run_frames([])
        self.assertIs(raised.exception, error)
        self.camera.device.save_screenshot.assert_not_called()

    def test_existing_update_failure_is_propagated_without_masking(self):
        error = GameStuckError('全球地图识别失败')
        self.camera.globe_update.side_effect = error
        with self.assertRaises(GameStuckError) as raised:
            self.run_frames([], initial=GlobeFrame('入口详情', pinned=True))
        self.assertIs(raised.exception, error)
        self.camera.device.save_screenshot.assert_not_called()

    def test_unexpected_handler_failure_is_propagated(self):
        error = ValueError('状态识别异常')
        self.camera.handle_zone_pinned.side_effect = error
        with self.assertRaises(ValueError) as raised:
            self.run_frames([], initial=GlobeFrame('入口详情', pinned=True))
        self.assertIs(raised.exception, error)
        self.camera.device.save_screenshot.assert_not_called()


if __name__ == '__main__':
    unittest.main()
