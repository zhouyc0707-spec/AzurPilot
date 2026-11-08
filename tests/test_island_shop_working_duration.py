"""工作时长重读及未知时长调度草稿：只使用假截图，不连接游戏或真实配置。"""

import unittest
from collections import Counter
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.base.base import ModuleBase
from module.config.config import AzurLaneConfig, name_to_function
from module.island.island import ISLAND_POST_SELECT, ISLAND_WORK_COMPLETE, ISLAND_WORKING
from module.island.island_manufacture import IslandManufacture
from module.island.island_shop_base import IslandShopBase
from module.island.island_teahouse import IslandTeahouse
from module.island.round_inventory import IslandRoundInventory
from tests.test_island_shop_production import ShopUI


NOW = datetime(2026, 11, 8, 5, 1, 29)


class DurationUI:
    """使用真实截图循环和计时器，仅替换设备帧、按钮识别与 OCR 输出。"""

    def __init__(self, fresh_frames, initial=ISLAND_WORKING):
        self.frames = [(initial, '初始帧不作为时长确认')] + list(fresh_frames)
        self.now = 1000.0
        self.reads = 0
        self.frame = (initial, '')
        self.task = IslandShopBase.__new__(IslandShopBase)
        task = self.task
        task.device = SimpleNamespace(image=None, screenshot=self.screenshot, sleep=Mock())
        task.posts = {'POST1': {'button': Mock()}}
        task.post_check_meal = Counter()
        task._round_collect_all = True
        task.post_close = Mock()
        task.post_open = Mock(return_value=True)
        task.post_list_is_empty = Mock(return_value=False)
        task.post_product_check = Mock(return_value='lavender_tea')
        task.post_get_stay = Mock(return_value=True)
        task.appear = lambda button, **kwargs: button is self.frame[0]
        task.loop = Mock(wraps=ModuleBase.loop.__get__(task, IslandShopBase))

    def screenshot(self):
        self.frame = self.frames[min(self.reads, len(self.frames) - 1)]
        self.reads += 1
        self.now += 1
        self.task.device.image = self.frame
        return self.frame


class WorkingDurationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.island.island_shop_base.logger'))
        self.enterContext(patch('module.island.island_shop_base.current_time', return_value=NOW))
        self.digit = self.enterContext(patch('module.island.island_shop_base.Digit'))
        self.digit.return_value.ocr.return_value = 4
        # 旧实现验红时不加载模型，复现日志中无效时长返回零的语义。
        self.duration = self.enterContext(patch('module.island.island_shop_base.Duration'))
        self.duration.return_value.ocr.return_value = timedelta(0)
        self.raw = self.enterContext(patch('module.island.island_shop_base.Ocr'))

    def run_flow(self, flow):
        self.raw.return_value.ocr.side_effect = lambda image: image[1]
        with patch('module.base.timer.time', lambda: flow.now):
            flow.task.post_check('POST1', 'time_1')
        return flow.task

    def test_truncated_first_fresh_frame_recovers_on_next_frame(self):
        flow = DurationUI([(ISLAND_WORKING, '01:01:1'), (ISLAND_WORKING, '01:01:17')])
        task = self.run_flow(flow)
        self.assertEqual(flow.reads, 3)
        self.assertEqual(task.time_1, NOW + timedelta(hours=1, minutes=1, seconds=17))
        self.assertEqual(task.posts['POST1']['status'], 'working')
        self.assertEqual(task.posts['POST1']['crop'], 'lavender_tea')
        self.assertEqual(task.posts['POST1']['runs'], 4)
        self.assertEqual(task.post_check_meal, {'lavender_tea': 4})
        self.assertEqual(self.raw.return_value.ocr.call_count, 2)
        self.assertIsNone(self.raw.call_args.kwargs['alphabet'])
        self.assertIs(task.loop.call_args.kwargs['skip_first'], False)
        task.post_get_stay.assert_called_once()

    def test_invalid_duration_times_out_without_finishing_or_losing_in_production(self):
        for raw in ('01:01:1', '010117', '前01:01:17', '01:01:17后', '01:01:X17',
                    '01:60:00', '01:00:60', '00:00:00', '', '1:1:17'):
            with self.subTest(raw=raw):
                self.raw.reset_mock()
                flow = DurationUI([(ISLAND_WORKING, raw)])
                task = self.run_flow(flow)
                # Timer.reached 使用严格大于；第五秒边界允许再读一帧，之后结束。
                self.assertEqual(flow.reads, 7)
                self.assertEqual(task.loop.call_args.kwargs['timeout'].limit, 5)
                self.assertIsNone(task.time_1)
                self.assertEqual(task.posts['POST1']['status'], 'working')
                self.assertEqual(task.posts['POST1']['runs'], 4)
                self.assertEqual(task.post_check_meal, {'lavender_tea': 4})
                self.assertEqual(self.raw.return_value.ocr.call_count, 6)
                task.post_get_stay.assert_called_once()

    def test_unknown_transition_does_not_read_ocr_until_working_is_confirmed(self):
        flow = DurationUI([(None, '00:01:00'), (ISLAND_WORKING, '00:02:00')])
        task = self.run_flow(flow)
        self.assertEqual(task.time_1, NOW + timedelta(minutes=2))
        self.raw.return_value.ocr.assert_called_once_with((ISLAND_WORKING, '00:02:00'))

    def test_positive_idle_during_retry_clears_working_only_once(self):
        for state in (ISLAND_POST_SELECT,):
            with self.subTest(state=state):
                self.raw.reset_mock()
                flow = DurationUI([(ISLAND_WORKING, '01:01:1'), (state, '')])
                task = self.run_flow(flow)
                self.assertEqual(task.posts['POST1']['status'], 'idle')
                self.assertIsNone(task.posts['POST1']['crop'])
                self.assertEqual(task.posts['POST1']['runs'], 0)
                self.assertIsNone(task.time_1)
                self.assertEqual(task.post_check_meal, {})
                self.assertEqual(self.raw.return_value.ocr.call_count, 1)
                task.post_get_stay.assert_called_once()

    def test_initial_complete_or_idle_is_rechecked_after_collection(self):
        for state in (ISLAND_WORK_COMPLETE, ISLAND_POST_SELECT):
            with self.subTest(state=state):
                flow = DurationUI([(ISLAND_POST_SELECT, '')], initial=state)
                task = self.run_flow(flow)
                self.assertEqual(task.posts['POST1']['status'], 'idle')
                task.loop.assert_called_once()
                task.post_get_stay.assert_called_once()
                self.assertEqual(flow.reads, 2)

    def test_unknown_working_product_keeps_working_without_inventing_product(self):
        flow = DurationUI([(ISLAND_WORKING, '01:01:1')])
        flow.task.post_product_check.return_value = None
        task = self.run_flow(flow)
        self.assertEqual(task.posts['POST1']['status'], 'working')
        self.assertEqual(task.posts['POST1']['runs'], 4)
        self.assertIsNone(task.time_1)
        self.assertEqual(task.post_check_meal, {})

    def test_duration_at_supported_boundaries_is_preserved(self):
        for text, duration in (('0:00:01', timedelta(seconds=1)),
                               ('99:59:59', timedelta(hours=99, minutes=59, seconds=59))):
            with self.subTest(text=text):
                task = self.run_flow(DurationUI([(ISLAND_WORKING, text)]))
                self.assertEqual(task.time_1, NOW + duration)


class UnknownWorkingScheduleTests(unittest.TestCase):
    def setUp(self):
        for module in ('island_shop_base', 'island_manufacture'):
            self.enterContext(patch(f'module.island.{module}.logger'))
            self.enterContext(patch(f'module.island.{module}.current_time', return_value=NOW))

    def shop(self, finishes, *, states=('working', 'working')):
        task = ShopUI(IslandTeahouse).shop
        task.island_round = IslandRoundInventory()
        task._round_prepared = True
        task._round_time_vars = []
        for index, (post_id, finish, state) in enumerate(zip(task.posts, finishes, states), 1):
            variable = f'{task.time_prefix}{index}'
            task._round_time_vars.append(variable)
            setattr(task, variable, finish)
            task.posts[post_id].update(status=state, round_enabled=True)
        task.post_check = Mock(side_effect=AssertionError('不得重复巡检'))
        task.get_warehouse_counts = Mock(side_effect=AssertionError('不得重复读仓库'))
        return task

    def test_unknown_working_uses_five_minute_instead_of_six_hour_default(self):
        task = self.shop((None, None))
        task.run()
        self.assertEqual(task.config.task_delay.call_args.kwargs['target'],
                         [NOW + timedelta(minutes=5), NOW + timedelta(hours=6)])
        task.get_warehouse_counts.assert_not_called()
        task.post_check.assert_not_called()

    def test_disabled_unknown_working_and_known_earlier_completion_keep_earliest(self):
        task = self.shop((None, NOW + timedelta(minutes=2)))
        task.posts[next(iter(task.posts))]['round_enabled'] = False
        task.run()
        self.assertEqual(task.config.task_delay.call_args.kwargs['target'],
                         [NOW + timedelta(minutes=2), NOW + timedelta(minutes=5),
                          NOW + timedelta(hours=6)])

    def test_all_known_working_posts_do_not_add_retry(self):
        task = self.shop((NOW + timedelta(hours=1), NOW + timedelta(hours=2)))
        task.run()
        self.assertEqual(task.config.task_delay.call_args.kwargs['target'],
                         [NOW + timedelta(hours=1), NOW + timedelta(hours=2),
                          NOW + timedelta(hours=6)])

    def test_non_working_unknown_posts_do_not_add_retry(self):
        task = self.shop((None, None), states=('none', 'none'))
        task.run()
        self.assertEqual(task.config.task_delay.call_args.kwargs['target'],
                         [NOW + timedelta(hours=6)])

    def test_real_task_delay_still_uses_global_hour_alignment(self):
        for alignment, expected in (('hour', datetime(2026, 11, 8, 6)),
                                    ('none', NOW + timedelta(minutes=5))):
            with self.subTest(alignment=alignment):
                task = self.shop((None, None))
                config = AzurLaneConfig.__new__(AzurLaneConfig)
                config.bound, config.modified, config.auto_update = {}, {}, False
                config.data = {'IslandPlan': {'IslandPlan': {'TaskAlignment': alignment}}}
                config.task = name_to_function('IslandTeahouse')
                task.config = config
                with patch('module.config.config.current_time', return_value=NOW):
                    task.run()
                self.assertEqual(config.modified['IslandTeahouse.Scheduler.NextRun'], expected)

    def test_manufacture_uses_all_inspected_posts_not_only_enabled_button_order(self):
        task = IslandManufacture.__new__(IslandManufacture)
        task.config = SimpleNamespace(task_delay=Mock())
        task.island_round = IslandRoundInventory()
        task._round_prepared = True
        task.post_buttons = {'ISLAND_INDUSTRIAL_POST1': Mock()}
        task.posts = {
            'ISLAND_WOOD_PROCESSING_POST1': {'status': 'working', 'round_enabled': False},
            'ISLAND_INDUSTRIAL_POST1': {'status': 'working', 'round_enabled': True},
        }
        task._post_time_vars = {
            'ISLAND_WOOD_PROCESSING_POST1': 'time_manufacture1',
            'ISLAND_INDUSTRIAL_POST1': 'time_manufacture5',
        }
        task.time_manufacture1 = None
        task.time_manufacture5 = NOW + timedelta(hours=2)
        for name in ('goto_postmanage', 'post_manage_mode', 'post_close'):
            setattr(task, name, Mock())
        task.get_warehouse_counts = Mock(side_effect=AssertionError('不得重复读仓库'))
        task.post_check = Mock(side_effect=AssertionError('不得重复巡检'))
        task.run()
        self.assertEqual(task.config.task_delay.call_args.kwargs['target'],
                         [NOW + timedelta(minutes=5), NOW + timedelta(hours=2),
                          NOW + timedelta(hours=6)])
        task.get_warehouse_counts.assert_not_called()
        task.post_check.assert_not_called()


if __name__ == '__main__':
    unittest.main()
