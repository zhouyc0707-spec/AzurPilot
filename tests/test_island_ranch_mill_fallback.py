"""牧场岗位全部工作时复用地图入口补料，不取消派遣或放宽加工前置条件。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from module.exception import GameBugError, GameStuckError
from module.island.island import ISLAND_MILL_CHECK, ISLAND_WORKING, POST_CLOSE
from module.island.island_rancher import IslandRancher
from module.island.round_inventory import IslandRoundInventory
from tests.test_island_reward_state import FrameFlow


class RanchMillFallbackTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.island.island_rancher.logger'))
        self.flow = FrameFlow([[ISLAND_WORKING]] * 4, task_class=IslandRancher)
        self.task = task = self.flow.task
        task.posts_ranch = {f'post{i}': f'post{i}' for i in range(1, 5)}
        task.check_mill_supplement_needs = Mock(return_value=[('wheat_flour', 7)])
        task._item_cn = lambda name: name
        for name in ('goto_postmanage', 'post_manage_mode', 'post_close', 'post_manage_swipe'):
            setattr(task, name, Mock())
        task.post_open = Mock(return_value=True)
        task.back_to_postmanage_after_mill_purchase = Mock(return_value=True)
        task.goto_mill = Mock(return_value=True)
        task.process_mill_item_with_inventory = Mock(return_value=True)

    def test_all_four_working_posts_use_existing_map_route_without_dispatch(self):
        result = self.flow.run('process_mill_supplements')
        self.assertEqual(result, ['wheat_flour'])
        self.assertEqual(self.task.post_open.call_args_list, [call(f'post{i}') for i in range(1, 5)])
        self.task.goto_mill.assert_called_once_with()
        self.task.process_mill_item_with_inventory.assert_called_once_with('wheat_flour', 7)
        self.assertEqual(self.flow.names(), [POST_CLOSE.name] * 4)
        self.task.post_manage_swipe.assert_not_called()
        self.task.select_character.assert_not_called()
        self.task.confirm_post_add_order.assert_not_called()

    def test_shortcut_success_does_not_walk_and_keeps_requested_quantity(self):
        self.task.goto_mill_from_any_ranch_post = Mock(return_value=True)
        self.assertEqual(self.task.process_mill_supplements(), ['wheat_flour'])
        self.task.goto_mill.assert_not_called()
        self.task.process_mill_item_with_inventory.assert_called_once_with('wheat_flour', 7)
        self.task.post_manage_swipe.assert_not_called()

    def test_sufficient_stock_needs_no_navigation(self):
        self.task.check_mill_supplement_needs.return_value = []
        self.assertEqual(self.task.process_mill_supplements(), [])
        self.task.goto_postmanage.assert_not_called()
        self.task.goto_mill.assert_not_called()

    def test_failed_map_entry_returns_to_posts_without_processing(self):
        self.task.goto_mill.return_value = False
        self.assertEqual(self.flow.run('process_mill_supplements'), [])
        self.task.process_mill_item_with_inventory.assert_not_called()
        self.assertEqual(self.task.goto_postmanage.call_count, 2)

    def test_failed_shortcut_recovery_cannot_start_walking(self):
        self.task.goto_mill_from_any_ranch_post = Mock(return_value=False)
        self.task.back_to_postmanage_after_mill_purchase.return_value = False
        with self.assertRaises(GameStuckError):
            self.task.process_mill_supplements()
        self.task.goto_mill.assert_not_called()
        self.task.process_mill_item_with_inventory.assert_not_called()

    def test_game_error_uses_existing_recovery_instead_of_map_fallback(self):
        self.task.goto_mill_from_any_ranch_post = Mock(return_value=False)
        self.task.island_error = True
        with self.assertRaises(GameBugError):
            self.task.process_mill_supplements()
        self.task.goto_mill.assert_not_called()

    def test_map_navigation_exception_is_not_swallowed(self):
        self.task.goto_mill.side_effect = GameStuckError('地图未就绪')
        with self.assertRaises(GameStuckError):
            self.flow.run('process_mill_supplements')
        self.task.process_mill_item_with_inventory.assert_not_called()


class SharedMillShortcutTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.island.island_rancher.logger'))
        self.task = task = IslandRancher.__new__(IslandRancher)
        task.device = SimpleNamespace(image=object())
        task.island_round = IslandRoundInventory()
        task.posts_ranch = {
            f'post{i}': SimpleNamespace(name=f'post{i}', button=(400, 300, 480, 380)) for i in range(1, 5)
        }
        task.ui_page_appear = Mock(return_value=True)
        task.is_post_detail_visible = Mock(return_value=False)
        task.goto_mill_from_ranch_post = Mock(return_value=False)
        task.back_to_postmanage_after_mill_purchase = Mock(return_value=True)
        self.match = self.enterContext(patch('module.island.post_list.match_post_title', return_value=False))

    def test_shared_working_and_locked_posts_are_not_opened(self):
        self.task._round_ranch_states = {
            'post1': 'working', 'post2': 'locked', 'post3': 'working', 'post4': 'locked',
        }
        self.assertFalse(self.task.goto_mill_from_any_ranch_post())
        self.task.goto_mill_from_ranch_post.assert_not_called()
        self.task.back_to_postmanage_after_mill_purchase.assert_not_called()

    def test_unknown_posts_still_use_original_bounded_shortcut(self):
        self.task._round_ranch_states = {'post1': 'working', 'post2': 'unknown', 'post3': 'locked'}
        self.assertFalse(self.task.goto_mill_from_any_ranch_post())
        self.assertEqual(self.task.goto_mill_from_ranch_post.call_args_list, [call('post2'), call('post4')])

    def test_current_visible_idle_post_precedes_offscreen_idle_and_unknown(self):
        self.task._round_ranch_states = {'post1': 'idle', 'post2': 'unknown', 'post3': 'idle', 'post4': 'working'}
        self.match.side_effect = lambda image, button, **kwargs: button.name == 'post3'
        self.task.goto_mill_from_ranch_post.return_value = True
        self.assertTrue(self.task.goto_mill_from_any_ranch_post())
        self.task.goto_mill_from_ranch_post.assert_called_once_with('post3')
        self.task.back_to_postmanage_after_mill_purchase.assert_not_called()

    def test_clipped_idle_post_is_not_prioritized_as_visible(self):
        self.task._round_ranch_states = {'post1': 'idle', 'post2': 'unknown', 'post3': 'idle', 'post4': 'working'}
        self.task.posts_ranch['post3'].button = (400, 610, 480, 690)
        self.match.side_effect = lambda image, button, **kwargs: button.name == 'post3'
        self.task.goto_mill_from_ranch_post.return_value = True
        self.assertTrue(self.task.goto_mill_from_any_ranch_post())
        self.task.goto_mill_from_ranch_post.assert_called_once_with('post1')

    def test_detail_overlay_cannot_supply_visible_post_priority(self):
        self.task._round_ranch_states = {'post1': 'idle', 'post2': 'idle', 'post3': 'locked', 'post4': 'working'}
        self.task.is_post_detail_visible.return_value = True
        self.assertFalse(self.task.goto_mill_from_any_ranch_post())
        self.match.assert_not_called()
        self.assertEqual(self.task.goto_mill_from_ranch_post.call_args_list, [call('post1'), call('post2')])

    def test_independent_task_does_not_reuse_stale_round_states(self):
        self.task.island_round = None
        self.task._round_ranch_states = dict.fromkeys(self.task.posts_ranch, 'working')
        self.assertFalse(self.task.goto_mill_from_any_ranch_post())
        self.assertEqual(self.task.goto_mill_from_ranch_post.call_args_list,
                         [call(post) for post in self.task.posts_ranch])
        self.match.assert_not_called()


class ExistingMillRouteTests(unittest.TestCase):
    """启用原有路线后，仍须传送成功和磨坊页面正向确认。"""

    def make_flow(self, frames):
        flow = FrameFlow(frames, task_class=IslandRancher)
        flow.task.island_map_goto = Mock(return_value=True)
        for method in ('island_up', 'island_left', 'island_down'):
            setattr(flow.task, method, Mock())
        return flow

    def test_failed_teleport_never_walks_and_retries_are_bounded(self):
        flow = self.make_flow([])
        flow.task.island_map_goto.return_value = False
        self.assertFalse(flow.run('goto_mill'))
        self.assertEqual(flow.task.island_map_goto.call_args_list, [call('farm')] * 3)
        for method in ('island_up', 'island_left', 'island_down'):
            getattr(flow.task, method).assert_not_called()

    def test_unknown_page_is_not_success(self):
        flow = self.make_flow([[]])
        self.assertFalse(flow.run('goto_mill', max_attempts=1))

    def test_positive_mill_page_confirms_success(self):
        flow = self.make_flow([[ISLAND_MILL_CHECK]])
        self.assertTrue(flow.run('goto_mill'))
        flow.task.island_map_goto.assert_called_once_with('farm')


if __name__ == '__main__':
    unittest.main()
