"""离线验证后续地图帧恢复海域时保留掉落及清图前后的领奖语义。"""

import unittest
from types import SimpleNamespace

from module.azur_stats.image.opsi_zone import DataOpsiZone
from module.azur_stats.scene.operation_siren import SceneOperationSiren


class RecoveryScene(SceneOperationSiren):
    def __init__(self, frames):
        self.images = frames
        self.server = 'cn'
        self.__dict__['imgid'] = 'zone-recovery'
        self.zone_reads = []
        self.reward_groups = []

    def is_opsi_zone(self, frame):
        return frame.startswith('map-')

    def parse_opsi_zone(self, frame):
        self.zone_reads.append(frame)
        if frame != 'map-ready':
            raise ValueError('测试地图标题暂时不可读')
        return DataOpsiZone('Test zone', 'SAFE', 101, 3)

    def is_get_items(self, frame):
        return frame.startswith('popup-')

    def parse_get_items(self, frame):
        yield SimpleNamespace(name='GearDesignPlanGunT5', amount=1, tag='meow')

    def is_opsi_reward(self, frame):
        return frame.startswith('reward-')

    def parse_auto_search_reward_pages(self, frames):
        self.reward_groups.append(list(frames))
        # 同一组滚动页包含重叠的金板；场景只应将整组交给多页去重器一次。
        yield SimpleNamespace(name='PlateGeneralT4', amount=2, tag='meow')


class TestOpsiZoneRecovery(unittest.TestCase):
    def test_later_readable_map_recovers_zone_without_moving_reward_boundary(self):
        scene = RecoveryScene([
            'popup-before', 'reward-before', 'map-unreadable',
            'popup-between', 'reward-between', 'map-ready',
            'popup-after', 'reward-after', 'reward-after-overlap', 'map-unused',
        ])

        rows = list(scene.parse_scene())

        self.assertEqual(scene.zone_reads, ['map-unreadable', 'map-ready'])
        self.assertTrue(all((row.zone_id, row.hazard_level, row.zone_type) == (101, 3, 'SAFE') for row in rows))
        self.assertEqual([(row.item, row.amount, row.tag) for row in rows], [
            ('GearDesignPlanGunT5', 1, 'meow'),
            ('PlateGeneralT4', 2, 'meow'),
            ('GearDesignPlanGunT5', 1, 'log'),
            ('PlateGeneralT4', 2, 'scan'),
            ('GearDesignPlanGunT5', 1, 'log'),
        ])
        self.assertEqual(scene.reward_groups, [
            ['reward-before'],
            ['reward-between', 'reward-after', 'reward-after-overlap'],
        ])

    def test_all_map_reads_fail_preserves_unknown_rewards_and_existing_tags(self):
        scene = RecoveryScene([
            'popup-before', 'reward-before', 'map-first-unreadable',
            'popup-between', 'reward-between', 'map-second-unreadable',
            'popup-after', 'reward-after',
        ])

        rows = list(scene.parse_scene())

        self.assertEqual(scene.zone_reads, ['map-first-unreadable', 'map-second-unreadable'])
        self.assertTrue(all((row.zone_id, row.hazard_level, row.zone_type) == (0, 0, 'UNKNOWN') for row in rows))
        self.assertEqual([(row.item, row.amount, row.tag) for row in rows], [
            ('GearDesignPlanGunT5', 1, 'log'),
            ('PlateGeneralT4', 2, 'scan'),
            ('GearDesignPlanGunT5', 1, 'log'),
            ('GearDesignPlanGunT5', 1, 'log'),
        ])
        self.assertEqual(scene.reward_groups, [['reward-before', 'reward-between', 'reward-after']])

    def test_missing_map_preserves_unknown_rewards_without_guessing_zone(self):
        scene = RecoveryScene(['popup-reward', 'reward-first', 'reward-overlap'])

        rows = list(scene.parse_scene())

        self.assertEqual(scene.zone_reads, [])
        self.assertEqual([(row.item, row.amount, row.hazard_level, row.tag) for row in rows], [
            ('GearDesignPlanGunT5', 1, 0, 'log'),
            ('PlateGeneralT4', 2, 0, 'scan'),
        ])
        self.assertEqual(scene.reward_groups, [['reward-first', 'reward-overlap']])

    def test_first_readable_map_keeps_existing_boundary_and_ignores_later_maps(self):
        scene = RecoveryScene(['reward-before', 'map-ready', 'popup-after', 'map-unused'])

        rows = list(scene.parse_scene())

        self.assertEqual(scene.zone_reads, ['map-ready'])
        self.assertEqual([row.tag for row in rows], ['meow', 'log'])
        self.assertTrue(all(row.hazard_level == 3 for row in rows))


if __name__ == '__main__':
    unittest.main()
