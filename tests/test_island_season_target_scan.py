"""季节目标单次导入的只读状态和完整性保护。"""

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import module.config.server as server
from module.exception import RequestHumanTakeover
from module.island_planner import season_tasks as tasks
from module.island_planner.season_target_scan import IslandSeasonTargetScan


class PagedScanner(tasks.IslandSeasonTaskScanner):
    def __init__(self, pages):
        self.pages = pages
        self.page = 0
        self.loop = lambda **kwargs: iter(range(1))

    @property
    def season_task_grid(self):
        rows = self.pages[self.page]
        self.season_task_rows_found = bool(rows)
        return SimpleNamespace(buttons=[SimpleNamespace(area=index) for index in range(len(rows))])

    @property
    def task_ids_on_page(self):
        return self.read_task_ids_on_page(None)

    def read_task_ids_on_page(self, season_ids, strict=False):
        return [task_id for task_id, _ in self.pages[self.page]]

    def image_crop(self, index, copy=True):
        return self.pages[self.page][index][1]


class SeasonStrictScanTest(unittest.TestCase):
    def setUp(self):
        self.previous_server = server.server
        server.server = 'cn'

    def tearDown(self):
        server.server = self.previous_server

    def scan(self, pages, expected, *, strict=True, bottom=True, ready=True, top=True):
        scanner = PagedScanner(pages)
        with patch.object(tasks, 'ISLAND_SEASON_TASK_SCROLL') as scroll, \
                patch.object(tasks, 'TEMPLATE_ISLAND_SEASON_TASK_OBTAINED') as obtained:
            scroll.appear.return_value = ready
            scroll.at_top.return_value = top
            scroll.at_bottom.side_effect = lambda **kwargs: bottom and scanner.page == len(pages) - 1
            scroll.next_page.side_effect = lambda **kwargs: setattr(
                scanner, 'page', min(scanner.page + 1, len(pages) - 1))
            obtained.match.side_effect = bool
            return scanner.scan_unfinished(strict=strict, task_ids=set(expected))

    def test_server_specific_official_season_and_no_calendar_guess(self):
        context = tasks.current_season_context(datetime(2026, 11, 8, 12))
        self.assertEqual(context[:2], (5, 'winter'))
        self.assertEqual(len(context[2]), 30)
        server.server = 'tw'
        self.assertEqual(tasks.current_season_context(datetime(2026, 11, 8, 12))[:2], (4, 'autumn'))
        self.assertIsNone(tasks.current_season_context(datetime(2026, 12, 1, 12)))
        server.server = 'cn'
        self.assertIsNone(tasks.current_season_context(datetime(2027, 3, 1, 12)))

    def test_unknown_or_overlapping_season_data_is_not_imported(self):
        data = dict(tasks.DIC_ISLAND_SEASON)
        data[6] = dict(data[5])
        with patch.object(tasks, 'DIC_ISLAND_SEASON', data):
            self.assertIsNone(tasks.current_season_context(datetime(2026, 11, 8, 12)))

    def test_ambiguous_names_are_rejected_only_in_strict_mode(self):
        data = {1: {'name': {'cn': '清茶'}}, 2: {'name': {'cn': '清香'}}}
        with patch.object(tasks, 'DIC_ISLAND_TASK', data):
            self.assertIsNone(tasks.match_task_name('清花', [1, 2], strict=True))
            self.assertEqual(tasks.match_task_name('清茶', [1, 2], strict=True), 1)
            self.assertIn(tasks.match_task_name('清花', [1, 2]), data)

    def test_submission_goal_uses_full_required_stock_without_progress_subtraction(self):
        self.assertEqual(tasks.task_item_targets([80001501, 80001517], strict=True), {2014: 500})
        self.assertIsNone(tasks.task_item_targets([50002029], strict=True))
        with patch.dict(tasks.DIC_ISLAND_TASK, {80001501: {'target': {2014: True}}}):
            self.assertIsNone(tasks.task_item_targets([80001501], strict=True))
        with patch.dict(tasks.DIC_ISLAND_TASK, {80001517: {'target': {2014: 1}}}):
            self.assertIsNone(tasks.task_item_targets([80001517], strict=True))

    def test_complete_scan_keeps_unsubmitted_and_excludes_claimed_with_overlap(self):
        pages = [[(1, False), (2, False)], [(2, False), (3, True)], [(3, True), (4, True)]]
        self.assertEqual(self.scan(pages, [1, 2, 3, 4]), [1, 2])
        self.assertEqual(self.scan([[(1, True), (2, True)]], [1, 2]), [])

    def test_all_thirty_claimed_cards_produce_confirmed_empty_result(self):
        expected = list(range(80001501, 80001531))
        pages = [[(task_id, True) for task_id in expected[start:start + 6]]
                 for start in range(0, len(expected), 3)]
        unfinished = self.scan(pages, expected)
        self.assertEqual(unfinished, [])
        self.assertEqual(tasks.task_item_targets(unfinished, strict=True), {})
        # 无卡片不是“全部已领取”，不能把识别失败写成空目标。
        self.assertIsNone(self.scan([[]], expected))

    def test_missing_unknown_duplicate_or_changed_cards_abort(self):
        cases = [
            ([[(1, False)]], [1, 2]),
            ([[(1, False), (None, False)]], [1, 2]),
            ([[(1, False), (1, False)]], [1, 2]),
            ([[(1, False)], []], [1, 2]),
            ([[(1, False)], [(1, True), (2, True)]], [1, 2]),
        ]
        for pages, expected in cases:
            with self.subTest(pages=pages):
                self.assertIsNone(self.scan(pages, expected))

    def test_missing_top_scroll_or_end_confirmation_aborts(self):
        for options in ({'ready': False}, {'top': False}, {'bottom': False}):
            with self.subTest(options=options), patch.object(tasks, 'MAX_PAGES', 2):
                self.assertIsNone(self.scan([[(1, False)]], [1], **options))

    def test_legacy_planner_still_stops_at_first_claimed_card(self):
        self.assertEqual(self.scan([[(1, False), (2, True)]], [1, 2, 3], strict=False), [1])

    def test_read_only_scan_never_calls_claim_and_cross_season_result_is_rejected(self):
        scanner = tasks.IslandSeasonTaskScanner.__new__(tasks.IslandSeasonTaskScanner)
        scanner.ui_ensure = Mock()
        scanner._season_bottom_navbar = Mock()
        scanner.receive_all_reward = Mock()
        scanner.scan_unfinished = Mock(return_value=[80001501])
        context = (5, 'winter', frozenset(range(80001501, 80001531)))
        with patch.object(tasks, 'current_season_context', return_value=context):
            self.assertEqual(scanner.scan_season_task_targets(claim_rewards=False, strict=True), {2014: 500})
        scanner.receive_all_reward.assert_not_called()
        scanner.scan_unfinished.assert_called_once_with(strict=True, task_ids=context[2])
        with patch.object(tasks, 'current_season_context', side_effect=[context, None]):
            self.assertIsNone(scanner.scan_season_task_targets(claim_rewards=False, strict=True))

    def test_legacy_scan_still_claims_by_default(self):
        scanner = tasks.IslandSeasonTaskScanner.__new__(tasks.IslandSeasonTaskScanner)
        scanner.ui_ensure = Mock()
        scanner._season_bottom_navbar = Mock()
        scanner.receive_all_reward = Mock()
        scanner.scan_unfinished = Mock(return_value=[])
        self.assertEqual(scanner.scan_season_task_targets(), {})
        scanner.receive_all_reward.assert_called_once_with()


class SeasonImportRunnerTest(unittest.TestCase):
    context = (5, 'winter', frozenset(range(80001501, 80001531)))

    def runner(self, season='winter', targets=None):
        runner = IslandSeasonTargetScan.__new__(IslandSeasonTargetScan)
        runner.config = SimpleNamespace(cross_get=lambda path, default=None: season)
        runner.scan_season_task_targets = Mock(return_value=targets)
        runner.ui_ensure = Mock()
        return runner

    def test_success_only_scans_returns_to_island_and_calls_merge_helper(self):
        runner = self.runner(targets={2014: 500})
        events = []
        runner.ui_ensure.side_effect = lambda page: events.append('return')
        with patch('module.island_planner.season_target_scan.current_season_context', return_value=self.context), \
                patch('module.island.seasonal_task_targets.merge_imported_targets') as merge:
            merge.side_effect = lambda *args: events.append('write') or {'path': 500}
            self.assertEqual(runner.run(), {'path': 500})
            runner.scan_season_task_targets.assert_called_once_with(claim_rewards=False, strict=True)
            merge.assert_called_once_with(runner.config, {2014: 500}, 'winter')
        self.assertEqual(events, ['return', 'write'])

    def test_wrong_config_or_missing_season_fails_before_game_navigation(self):
        for season, context in [('autumn', self.context), ('winter', None)]:
            runner = self.runner(season=season, targets={2014: 500})
            with self.subTest(season=season, context=context), \
                    patch('module.island_planner.season_target_scan.current_season_context', return_value=context), \
                    patch('module.island.seasonal_task_targets.merge_imported_targets') as merge:
                with self.assertRaises(RequestHumanTakeover):
                    runner.run()
                runner.scan_season_task_targets.assert_not_called()
                runner.ui_ensure.assert_not_called()
                merge.assert_not_called()

    def test_failed_scan_and_changed_season_never_write(self):
        for targets, contexts in [(None, [self.context]), ({2014: 500}, [self.context, None])]:
            runner = self.runner(targets=targets)
            with self.subTest(targets=targets), \
                    patch('module.island_planner.season_target_scan.current_season_context', side_effect=contexts), \
                    patch('module.island.seasonal_task_targets.merge_imported_targets') as merge:
                with self.assertRaises(RequestHumanTakeover):
                    runner.run()
                runner.ui_ensure.assert_called_once()
                merge.assert_not_called()

    def test_invalid_existing_targets_are_a_real_task_failure(self):
        runner = self.runner(targets={2014: 500})
        with patch('module.island_planner.season_target_scan.current_season_context', return_value=self.context), \
                patch('module.island.seasonal_task_targets.merge_imported_targets', side_effect=ValueError('目标格式错误')):
            with self.assertRaisesRegex(RequestHumanTakeover, '目标格式错误'):
                runner.run()


if __name__ == '__main__':
    unittest.main()
