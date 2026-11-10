"""生产进度只读投影、跨进程快照与失效边界，不连接游戏或真实配置。"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.island.round_inventory import IslandRoundInventory
from module.island.seasonal_progress import (
    POST_GROUPS, inspected_production, invalidate_progress, load_progress, publish_round_progress,
)
from module.island.item_ids import LOCAL_TO_ITEM_ID
from tests import test_island_production_round as round_tests


NOW = datetime(2026, 11, 10, 10)


def session_with_posts():
    posts = {f'ISLAND_{prefix}_POST{i}': {'state': 'idle', 'crop': None, 'runs': 0}
             for prefix, (_, count) in POST_GROUPS.items() for i in range(1, count + 1)}
    runner = SimpleNamespace(posts=posts)
    ranch = SimpleNamespace(_round_ranch_states={f'ISLAND_RANCH_POST{i}': 'idle' for i in range(1, 5)})
    return SimpleNamespace(runners={'IslandFarm': runner, 'IslandRancher': ranch},
                           inventory=IslandRoundInventory({'tea': 300, 'citrus': 0}))


class SeasonalProgressTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.directory)
        self.clock = self.enterContext(patch('module.island.seasonal_progress.current_time', return_value=NOW))
        self.config = SimpleNamespace(config_name='alas', SERVER='cn',
                                      cross_get=lambda path, default=None: 'winter')
        self.session = session_with_posts()
        self.posts = self.session.runners['IslandFarm'].posts

    def save(self):
        return publish_round_progress(self.config, self.session, root=self.root)

    def load(self, **kwargs):
        return load_progress('alas', 'winter', root=self.root, now=kwargs.pop('now', NOW), **kwargs)

    def test_confirmed_stock_zero_and_unknown_stay_distinct(self):
        self.assertTrue(self.save())
        items = self.load()['items']
        self.assertEqual(items['2014'], {'stock': 300, 'in_production': 0})
        self.assertEqual(items['2017']['stock'], 0)
        self.assertIsNone(items['2021']['stock'])
        self.assertEqual(self.session.inventory.stocks, {2014: 300, 2017: 0})

    def test_old_crop_and_new_dispatch_are_counted_once_without_becoming_stock(self):
        self.posts['ISLAND_NURSERY_POST1'].update(state='working', crop='tea', runs=2)
        self.assertTrue(self.save())
        before = self.load()['items']['2014']['in_production']
        self.assertGreater(before, 0)
        self.posts['ISLAND_NURSERY_POST2'].update(state='working', crop='tea', runs=3)
        self.session.inventory.production_started('tea', 30)
        self.assertTrue(self.save())
        item = self.load()['items']['2014']
        self.assertEqual(item, {'stock': 300, 'in_production': before + 30})

    def test_unknown_or_missing_post_never_fills_in_production_with_zero(self):
        for info in ({}, {'state': 'unknown'}, {'state': 'working', 'crop': None, 'runs': 5},
                     {'state': 'working', 'crop': 'tea', 'runs': 0}):
            with self.subTest(info=info):
                self.session = session_with_posts()
                self.session.runners['IslandFarm'].posts['ISLAND_NURSERY_POST2'] = info
                self.assertTrue(self.save())
                self.assertIsNone(self.load()['items']['2014']['in_production'])
        self.session = SimpleNamespace(runners={}, inventory=IslandRoundInventory({'tea': 0}))
        self.assertTrue(self.save())
        self.assertIsNone(self.load()['items']['2014']['in_production'])

    def test_new_food_dispatch_marker_excludes_stale_post_runs(self):
        post = self.posts['ISLAND_JUU_EATERY_POST1']
        post.update(state='working', crop='rice_mango', runs=4)
        self.assertTrue(self.save())
        self.assertEqual(self.load()['items']['3025']['in_production'], 4)
        post.update(state='idle', crop=None, runs=0)
        self.assertTrue(self.save())
        self.assertEqual(self.load()['items']['3025']['in_production'], 0)
        post.update(state='working', crop='rice_mango', dispatched_in_run=True)
        self.session.inventory.production_started('rice_mango', 7)
        self.assertTrue(self.save())
        self.assertEqual(self.load()['items']['3025']['in_production'], 7)

    def test_unknown_working_ranch_is_not_estimated_from_finish_time(self):
        self.session.runners['IslandRancher']._round_ranch_states['ISLAND_RANCH_POST3'] = 'working'
        self.assertTrue(self.save())
        self.assertIsNone(self.load()['items'][str(LOCAL_TO_ITEM_ID['milk'])]['in_production'])

    def test_fish_meat_zero_requires_inspected_fishery_and_uses_confirmed_exchange_stock(self):
        self.session.inventory.observe('freshwater_fish_meat', 20)
        self.assertTrue(self.save())
        self.assertEqual(self.load()['items']['2521'], {'stock': 20, 'in_production': 0})
        self.session.inventory.add({'freshwater_fish_meat': 5})
        self.assertTrue(self.save())
        self.assertEqual(self.load()['items']['2521']['stock'], 25)
        self.session = session_with_posts()
        self.session.runners['IslandFarm'].posts['ISLAND_FISHERY_POST1']['state'] = 'unknown'
        self.assertTrue(self.save())
        self.assertIsNone(self.load()['items']['2521']['in_production'])

    def test_confirmed_consumption_and_overdraft_are_reflected_without_reobservation(self):
        self.assertTrue(self.save())
        self.session.inventory.consume({'tea': 20})
        self.assertTrue(self.save())
        self.assertEqual(self.load()['items']['2014']['stock'], 280)
        self.session.inventory.consume({'tea': 500})
        self.assertTrue(self.save())
        self.assertIsNone(self.load()['items']['2014']['stock'])

    def test_no_change_keeps_real_update_time_and_invalidation_preserves_history(self):
        self.assertTrue(self.save())
        original = self.load()
        self.clock.return_value = NOW + timedelta(hours=2)
        self.assertTrue(self.save())
        self.assertEqual(self.load()['updated_at'], original['updated_at'])
        self.assertTrue(invalidate_progress('alas', root=self.root, reason='订单已执行'))
        result = self.load(now=NOW + timedelta(hours=2))
        self.assertEqual(result['status'], 'invalidated')
        self.assertEqual(result['updated_at'], original['updated_at'])
        self.assertEqual(result['items'], original['items'])
        self.assertEqual(result['age_seconds'], 7200)

    def test_snapshot_is_instance_server_season_and_cycle_isolated(self):
        self.assertTrue(self.save())
        self.assertIsNone(load_progress('other', 'winter', root=self.root, now=NOW))
        self.assertIsNone(load_progress('alas', 'summer', root=self.root, now=NOW))
        self.assertIsNone(self.load(server='en'))
        self.assertIsNone(self.load(now=NOW.replace(year=2027)))
        path = self.root / 'cache/island-progress/alas.json'
        data = json.loads(path.read_text())
        data['season_id'] = 4
        path.write_text(json.dumps(data))
        self.assertIsNone(self.load())

    def test_age_does_not_erase_old_but_same_cycle_quantities(self):
        self.assertTrue(self.save())
        data = self.load(now=NOW + timedelta(days=3))
        self.assertEqual(data['items']['2014']['stock'], 300)
        self.assertEqual(data['age_seconds'], 3 * 86400)

    def test_corrupt_or_invalid_quantity_is_unknown_and_path_cannot_escape(self):
        self.assertTrue(self.save())
        path = self.root / 'cache/island-progress/alas.json'
        for content in ('{', '[]', '{"version":1}',
                        json.dumps({**self.load(), 'items': {'2014': {'stock': True, 'in_production': 0}}})):
            path.write_text(content)
            self.assertIsNone(self.load())
        self.assertIsNone(load_progress('../alas', 'winter', root=self.root, now=NOW))
        self.assertFalse(invalidate_progress('../alas', root=self.root))

    def test_write_failure_does_not_change_inventory_or_raise_game_error(self):
        with patch('module.island.seasonal_progress.atomic_write', side_effect=PermissionError):
            self.assertFalse(self.save())
        self.assertEqual(self.session.inventory.get('tea'), 300)
        self.assertFalse((self.root / 'cache/island-progress/alas.json').exists())

    def test_all_quantities_use_local_ids_including_winter_translation(self):
        self.session.inventory.observe('sugar_tangerine', 5)
        self.assertTrue(self.save())
        items = self.load()['items']
        self.assertEqual(items[str(LOCAL_TO_ITEM_ID['sugar_tangerine'])]['stock'], 5)
        self.assertNotEqual(LOCAL_TO_ITEM_ID['sugar_tangerine'], 4047)


class ProductionRoundProgressIntegrationTests(unittest.TestCase):
    """复用原共享轮假设备，验证只观察原收获/盘点，不增加 UI 调用。"""

    setUp = round_tests.ProductionRoundTests.setUp

    def test_snapshot_hooks_follow_preparation_and_successful_task(self):
        from module.island.production_round import run_production_task
        with patch('module.island.production_round.publish_round_progress') as publish:
            run_production_task(self.owner, 'IslandFarm', self.runner_cls)
        self.assertEqual(publish.call_count, 2)
        self.warehouse_scan.assert_called_once()

    def test_failure_invalidates_previous_snapshot_without_replacing_game_exception(self):
        from module.island.production_round import run_production_task
        from module.exception import GameStuckError
        self.config.config_name = 'isolated'
        with patch('module.island.production_round.publish_round_progress'), \
                patch('module.island.production_round.invalidate_progress') as invalidate, \
                patch.object(self.runner_cls, 'run', side_effect=GameStuckError('原失败')):
            with self.assertRaisesRegex(GameStuckError, '原失败'):
                run_production_task(self.owner, 'IslandFarm', self.runner_cls)
        self.assertEqual(invalidate.call_count, 2)
        self.assertEqual(invalidate.call_args.args, ('isolated',))


if __name__ == '__main__':
    unittest.main()
