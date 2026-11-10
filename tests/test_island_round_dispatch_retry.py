"""渔场共享轮补点的离线状态回放，保留真实确认循环与计时器，不连接游戏。"""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.exception import GameStuckError
from module.island.island import (
    GET_ITEMS_ISLAND, ISLAND_GET, ISLAND_POSTMANAGE_CHECK, ISLAND_SELECT_PRODUCT_CHECK,
    ISLAND_WORKING, POST_ADD_ORDER, Island,
)
from module.island.round_inventory import IslandRoundInventory
from module.island_fishery.assets import SELECT_CRAB_CHECK


MATERIAL = 1000
AMOUNTS = (5, 5, 5)


class DispatchReplay:
    """每次假截图推进单调时钟；真实 Base.loop 决定超时，门禁不整体打桩。"""

    def __init__(self, *, success_after=2, change=None, receipt=True, step=0.2):
        self.time, self.frame, self.step = 100.0, 0, step
        self.success_after, self.change = success_after, change
        self.clicks, self.reads = [], []
        self.screen = {}
        self.main = Island.__new__(Island)
        self.main.island_round = IslandRoundInventory({MATERIAL: 5})
        self.main.island_round.consume = Mock(wraps=self.main.island_round.consume)
        self.main._round_material_receipt = {'item_id': MATERIAL} if receipt else None
        self.main.device = SimpleNamespace(image=None, screenshot=self.screenshot, click=Mock(side_effect=self.click))
        self.main.appear = Mock(side_effect=self.appear)
        self.main.match_template_color = Mock(side_effect=self.match_color)
        self.main.is_post_detail_visible = Mock(return_value=False)
        self.main._read_round_dispatch_amounts = Mock(side_effect=self.read_amounts)

    def screenshot(self):
        self.frame += 1
        self.time = 100.0 + self.frame * self.step
        if self.frame > 200:
            raise AssertionError('真实状态循环未按时退出')
        success = self.success_after is not None and len(self.clicks) >= self.success_after
        self.screen = dict(select=not success, product=not success, start=not success,
                           working=success, management=False, reward=False, get_items=False,
                           amounts=AMOUNTS, select_color=True, product_color=True, start_color=True)
        if self.change:
            self.screen.update(self.change(self) or {})
        self.main.device.image = self.screen

    def appear(self, button, **kwargs):
        if button is POST_ADD_ORDER and kwargs.get('threshold', 30) <= 10:
            # 按钮文字随预计完成时间变化，补点只要求严格蓝色，不要求整块文字模板。
            return self.screen['start'] and self.screen['start_color']
        key = {
            id(ISLAND_SELECT_PRODUCT_CHECK): 'select', id(SELECT_CRAB_CHECK): 'product',
            id(POST_ADD_ORDER): 'start', id(ISLAND_WORKING): 'working',
            id(ISLAND_POSTMANAGE_CHECK): 'management', id(ISLAND_GET): 'reward',
            id(GET_ITEMS_ISLAND): 'get_items',
        }.get(id(button))
        return bool(key and self.screen[key])

    def match_color(self, button, **kwargs):
        key = {id(ISLAND_SELECT_PRODUCT_CHECK): 'select_color', id(SELECT_CRAB_CHECK): 'product_color',
               id(POST_ADD_ORDER): 'start_color'}.get(id(button))
        return self.appear(button) and (self.screen[key] if key else True)

    def read_amounts(self):
        result = self.screen['amounts']
        self.reads.append((self.frame, self.time, result))
        return result

    def click(self, button):
        if button is not POST_ADD_ORDER:
            raise AssertionError(f'派遣确认不应点击其他按钮：{button}')
        self.clicks.append((self.frame, self.time))

    def run(self, *, opt_in=True):
        kwargs = {'retry_product_check': SELECT_CRAB_CHECK} if opt_in else {}
        with patch('module.base.timer.time', side_effect=lambda: self.time):
            return self.main.confirm_post_add_order('螃蟹离线回放', preserve_quantity=True, **kwargs)


class RoundDispatchRetryTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.island.island.logger'))

    def assert_success_debited_once(self, replay, clicks):
        self.assertEqual(len(replay.clicks), clicks)
        self.assertEqual(replay.main._round_dispatch_batches, 5)
        self.assertEqual(replay.main._round_dispatch_materials, {MATERIAL: 5})
        self.assertEqual(replay.main.island_round.get(MATERIAL), 0)
        self.assertIsNone(replay.main._round_material_receipt)
        replay.main.island_round.consume.assert_called_once_with({MATERIAL: 5})

    def assert_uncertain_not_debited(self, replay, clicks=1, *, opt_in=True):
        with self.assertRaises(GameStuckError):
            replay.run(opt_in=opt_in)
        self.assertEqual(len(replay.clicks), clicks)
        self.assertEqual(replay.main.island_round.get(MATERIAL), 5)
        self.assertIsNone(replay.main._round_dispatch_batches)
        self.assertEqual(replay.main._round_dispatch_materials, {})
        replay.main.island_round.consume.assert_not_called()

    def test_first_click_not_effective_retries_once_then_commits_one_dispatch(self):
        replay = DispatchReplay()
        self.assertTrue(replay.run())
        self.assert_success_debited_once(replay, 2)
        self.assertGreaterEqual(replay.clicks[1][1] - replay.clicks[0][1], 2)

    def test_first_click_success_never_sends_retry(self):
        replay = DispatchReplay(success_after=1)
        self.assertTrue(replay.run())
        self.assert_success_debited_once(replay, 1)

    def test_retry_needs_two_new_matching_frames_even_if_first_new_frame_is_after_two_seconds(self):
        replay = DispatchReplay(step=2.5)
        self.assertTrue(replay.run())
        self.assert_success_debited_once(replay, 2)
        first_frame, second_frame = replay.clicks[0][0], replay.clicks[1][0]
        self.assertGreaterEqual(second_frame - first_frame, 2)
        following = [row for row in replay.reads if first_frame < row[0] <= second_frame]
        self.assertGreaterEqual(len(following), 2)
        self.assertTrue(all(row[2] == AMOUNTS for row in following))

    def test_read_change_or_unknown_before_two_seconds_permanently_cancels_retry(self):
        for candidate in (None, (4, 5, 4), (5, 0, 5), (5, 6, 5), (5, 5, 4)):
            with self.subTest(candidate=candidate):
                def change(replay):
                    if replay.clicks and replay.frame == replay.clicks[0][0] + 1:
                        return {'amounts': candidate}
                replay = DispatchReplay(success_after=None, change=change)
                self.assert_uncertain_not_debited(replay)

    def test_single_unsafe_frame_permanently_cancels_even_after_screen_recovers(self):
        for key, value in (
            ('select', False), ('product', False), ('start', False),
            ('select_color', False), ('product_color', False), ('start_color', False),
            ('reward', True), ('get_items', True), ('working', True),
        ):
            with self.subTest(condition=key):
                def change(replay):
                    if replay.clicks and replay.frame == replay.clicks[0][0] + 1:
                        return {key: value}
                replay = DispatchReplay(success_after=None, change=change)
                self.assert_uncertain_not_debited(replay)

    def test_first_click_frame_must_also_have_confirmed_product_and_blue_button(self):
        for key in ('product', 'product_color', 'start_color'):
            with self.subTest(condition=key):
                replay = DispatchReplay(success_after=None,
                                        change=lambda replay: {key: False} if not replay.clicks else {})
                self.assert_uncertain_not_debited(replay)

    def test_missing_material_receipt_never_retries(self):
        replay = DispatchReplay(success_after=None, receipt=False)
        self.assert_uncertain_not_debited(replay)

    def test_default_without_product_opt_in_keeps_single_submission(self):
        replay = DispatchReplay(success_after=None)
        self.assert_uncertain_not_debited(replay, opt_in=False)

    def test_retry_failure_has_no_third_click_and_no_estimated_debit(self):
        replay = DispatchReplay(success_after=None)
        self.assert_uncertain_not_debited(replay, clicks=2)
        self.assertGreaterEqual(replay.time - replay.clicks[1][1], 2)

    def test_unknown_initial_amounts_never_submit(self):
        replay = DispatchReplay(change=lambda _: {'amounts': None})
        self.assertFalse(replay.run())
        self.assertEqual(replay.clicks, [])
        replay.main.island_round.consume.assert_not_called()

    def test_cancelled_retry_can_still_accept_later_positive_success(self):
        def change(replay):
            if replay.clicks:
                if replay.frame == replay.clicks[0][0] + 1:
                    return {'amounts': None}
                if replay.time - replay.clicks[0][1] >= 1:
                    return {'select': False, 'working': True}
        replay = DispatchReplay(success_after=None, change=change)
        self.assertTrue(replay.run())
        self.assert_success_debited_once(replay, 1)


if __name__ == '__main__':
    unittest.main()
