"""国服统一收获按列表八行巡检，调度资格和库存续接保持独立。"""

import json
import unittest
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.config.config import TaskEnd
from module.island.island_fishery import IslandFishery
from module.island.production_round import (
    PRODUCTION_TASKS, IslandProductionRound, collect_cn_posts, invalidates_round, run_production_task,
)
from tests.test_island_production_round import FakeConfig
from tests import test_island_round_template as template_helpers


# 独立写出已经实屏确认的行序与全部岗位数，防止路由遗漏、同类重复或左右列颠倒。
EXPECTED_CATEGORIES = (
    ('FARM', 4), ('RANCH', 4), ('FISHERY', 3), ('MINE', 4),
    ('FOREST', 4), ('ORCHARD', 4), ('NURSERY', 2), ('RESTAURANT', 2),
    ('TEAHOUSE', 2), ('JUU_EATERY', 2), ('GRILL', 2), ('WOOD_PROCESSING', 2),
    ('INDUSTRIAL', 2), ('ELECTRONIC_PROCESSING', 2), ('HANDMADE', 2), ('JUU_COFFEE', 2),
)
EXPECTED_POSTS = [f'ISLAND_{category}_POST{i}'
                  for category, count in EXPECTED_CATEGORIES for i in range(1, count + 1)]


class RealRunnerHarvestRouteTests(unittest.TestCase):
    def make_runners(self):
        data = json.loads(Path('config/template.json').read_text(encoding='utf-8'))
        for task in PRODUCTION_TASKS:
            data[task]['Scheduler']['Enable'] = False
            for group in data[task].values():
                for field in ('Positions', 'PostNumber'):
                    if field in group:
                        group[field] = 0
        data['IslandRancher']['IslandRancher'].update(Milk=False, Wool=False)
        runners = template_helpers.RoundTemplateSmokeTests().build_runners(data)
        self.events = []
        for runner in runners.values():
            for method in ('goto_postmanage', 'post_manage_mode', 'post_close'):
                setattr(runner, method, Mock())
            if hasattr(runner, 'post_check_meal'):
                def food(post_id, time_var, runner=runner):
                    self.events.append(post_id)
                    runner.posts[post_id]['status'] = 'idle'
                runner.post_check = food
        farm = runners['IslandFarm']
        def farm_post(button, post_id, category, time_var):
            self.events.append(post_id)
            farm.posts[post_id]['state'] = 'idle'
            return True
        farm.decided_lists = farm_post
        mine = runners['IslandMineForest']
        def mine_post(button, post_id, category, time_var):
            self.events.append(post_id)
            mine.posts[post_id]['state'] = 'idle'
            return True
        mine.collect_and_detect_post = mine_post
        ranch = runners['IslandRancher']
        def empty_ranch(button):
            self.events.append(button.name)
            return True
        ranch.post_list_is_empty = empty_ranch
        def fish_post(fishery, button, post_id, index):
            self.events.append(post_id)
            fishery.posts[post_id]['state'] = 'idle'
            return True
        self.enterContext(patch.object(IslandFishery, 'decided_lists', fish_post))
        return runners

    def test_all_real_runner_posts_collected_once_by_row_even_when_disabled(self):
        runners = self.make_runners()
        stop = Mock()
        collect_cn_posts(runners, stop)
        self.assertEqual(self.events, EXPECTED_POSTS)
        self.assertEqual(len(self.events), 43)
        self.assertTrue(all(count == 1 for count in Counter(self.events).values()))
        self.assertEqual(sum(r.goto_postmanage.call_count for r in runners.values()), 1)
        for name, runner in runners.items():
            with self.subTest(task=name):
                self.assertTrue(all(not post['round_enabled'] for post in getattr(runner, 'posts', {}).values()))
                if hasattr(runner, 'post_check_meal'):
                    self.assertTrue(runner._round_prepared)
                    self.assertEqual(runner.post_check_meal, {})
        ranch = runners['IslandRancher']
        self.assertEqual(ranch._round_ranch_states,
                         {f'ISLAND_RANCH_POST{i}': 'idle' for i in range(1, 5)})
        self.assertEqual(ranch._round_fishery._round_idle_posts, [])
        self.assertEqual(runners['IslandFarm']._round_idle_posts,
                         {'farm': [], 'orchard': [], 'nursery': []})
        self.assertEqual(set(runners['IslandManufacture']._post_time_vars),
                         {post for post in EXPECTED_POSTS if any(
                             post.startswith(f'ISLAND_{category}_') for category in (
                                 'WOOD_PROCESSING', 'ELECTRONIC_PROCESSING', 'INDUSTRIAL', 'HANDMADE'))})
        self.assertEqual(stop.call_count, len(runners) * 2 + len(EXPECTED_POSTS))

    def test_stop_between_posts_does_not_visit_next_post_or_finalize(self):
        runners = self.make_runners()
        def stop():
            if len(self.events) >= 5:
                raise TaskEnd('用户停止')
        with self.assertRaises(TaskEnd):
            collect_cn_posts(runners, stop)
        self.assertEqual(self.events, EXPECTED_POSTS[:5])
        self.assertTrue(all(not r._round_prepared for r in runners.values()
                            if hasattr(r, 'post_check_meal')))


class CoordinatorHarvestRouteTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 8, 20)
        self.config = FakeConfig(self.now)
        self.config.SERVER = 'cn'
        self.config.data['IslandManufacture']['Scheduler']['Enable'] = True
        self.events = []
        events = self.events
        categories = {
            'IslandFarm': ('FARM', 'ORCHARD', 'NURSERY'),
            'IslandRancher': ('RANCH', 'FISHERY'),
            'IslandMineForest': ('MINE', 'FOREST'),
            'IslandRestaurant': ('RESTAURANT',), 'IslandTeahouse': ('TEAHOUSE',),
            'IslandGrill': ('GRILL',), 'IslandJuuEatery': ('JUU_EATERY',),
            'IslandJuuCoffee': ('JUU_COFFEE',),
            'IslandManufacture': ('WOOD_PROCESSING', 'ELECTRONIC_PROCESSING', 'INDUSTRIAL', 'HANDMADE'),
        }
        class Runner:
            def __init__(self, config, device):
                self.config, self.device = config, device
                self.task = config.task.command
                self.posts = {}
                for category in categories[self.task]:
                    for i in range(1, dict(EXPECTED_CATEGORIES)[category] + 1):
                        self.posts[f'ISLAND_{category}_POST{i}'] = {'state': 'unknown'}
                self.goto_postmanage = Mock()
                self.post_manage_mode = Mock()
                self.post_close = Mock()
            def prepare_round(self):
                events.append(('legacy', self.task))
                self.prepare_round_start()
                for post in self.posts:
                    self.prepare_round_post(post)
                self.prepare_round_finish()
            def prepare_round_start(self):
                return list(self.posts)
            def prepare_round_post(self, post):
                events.append(('post', post))
                self.posts[post]['state'] = 'working' if self.task == 'IslandManufacture' else 'idle'
            def prepare_round_finish(self):
                events.append(('finish', self.task))
            def round_stock_items(self):
                return {'coffee_bean'}
            def run(self):
                events.append(('run', self.task))
                self.island_round.consume({'coffee_bean': 2})
        self.runner_cls = Runner
        self.enterContext(patch('module.island.production_round.current_time', return_value=self.now))
        self.enterContext(patch('module.island.production_round.import_module',
                                return_value=SimpleNamespace(**{name: Runner for _, name in PRODUCTION_TASKS.values()})))
        def scan(*args):
            events.append(('warehouse', None))
            return {2009: 10}
        self.scan = self.enterContext(patch('module.island.round_warehouse.read_round_warehouse_stocks', side_effect=scan))
        self.owner = SimpleNamespace(config=self.config, device=object())

    def test_row_order_preserves_pending_qualification_and_inventory_after_interruptions(self):
        run_production_task(self.owner, 'IslandFarm', self.runner_cls)
        session = self.owner._island_production_round
        self.assertEqual([value for kind, value in self.events if kind == 'post'], EXPECTED_POSTS)
        self.assertEqual(session.pending, {'IslandJuuCoffee'})
        self.assertEqual(self.config.cross_get('IslandManufacture.Scheduler.NextRun'), self.now + timedelta(hours=6))
        self.assertFalse(self.config.cross_get('IslandRestaurant.Scheduler.Enable'))
        self.assertEqual(self.config.cross_get('IslandRestaurant.Scheduler.NextRun'), self.now + timedelta(hours=6))
        for task in ('Commission', 'Research'):
            self.assertFalse(invalidates_round(task))
        with patch('module.island.production_round.current_time', return_value=self.now + timedelta(hours=2)):
            run_production_task(self.owner, 'IslandJuuCoffee', self.runner_cls)
        self.scan.assert_called_once()
        self.assertEqual(session.inventory.get('coffee_bean'), 6)
        self.assertEqual(sum(kind == 'post' for kind, _ in self.events), 43)
        self.assertLess(self.events.index(('finish', 'IslandManufacture')), self.events.index(('warehouse', None)))
        self.assertLess(self.events.index(('warehouse', None)), self.events.index(('run', 'IslandFarm')))

    def test_disabled_explicit_entry_remains_pending(self):
        session = IslandProductionRound(self.config, object(), 'IslandTeahouse')
        self.assertEqual(session.pending, {'IslandFarm', 'IslandJuuCoffee', 'IslandTeahouse'})

    def test_collection_failure_does_not_scan_or_publish_ready_session(self):
        self.config.stop_event = Event()
        original = self.runner_cls.prepare_round_post
        def stop_after_first(runner, post):
            original(runner, post)
            self.config.stop_event.set()
        with patch.object(self.runner_cls, 'prepare_round_post', stop_after_first):
            with self.assertRaises(TaskEnd):
                run_production_task(self.owner, 'IslandFarm', self.runner_cls)
        self.scan.assert_not_called()
        self.assertIsNone(self.owner._island_production_round)
        self.assertEqual(self.events, [('post', EXPECTED_POSTS[0])])

    def test_other_servers_keep_original_task_collection_order(self):
        for server in ('en', 'jp', 'tw'):
            with self.subTest(server=server):
                self.events.clear()
                self.config.SERVER = server
                IslandProductionRound(self.config, object(), 'IslandFarm')
                self.assertEqual([value for kind, value in self.events if kind == 'legacy'], list(PRODUCTION_TASKS))


if __name__ == '__main__':
    unittest.main()
