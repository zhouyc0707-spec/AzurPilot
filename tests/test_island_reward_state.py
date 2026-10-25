"""岗位奖励和补货弹窗使用当前帧确认，不固定双点或穿透冷却中的遮罩。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.base.timer import Timer
from module.island.island import (
    Island, ERROR1, ISLAND_GET, ISLAND_POST_SAFE_AREA, ISLAND_POST_CHECK,
    ISLAND_POST_VACANT_CHECK, ISLAND_POST_SELECT, ISLAND_WORKING,
    ISLAND_POSTMANAGE_CHECK,
    ISLAND_SELECT_PRODUCT_CHECK, ISLAND_SELECT_CHARACTER_CHECK,
    ISLAND_SHOP_GET, ISLAND_SHOP_CONFIRM, ISLAND_SHOPPING_CHECK, ISLAND_SHOPPING_CLOSE,
    ISLAND_SELECT_GOTO_BUY_SEED, ISLAND_SELECT_SEED,
    POST_GET, POST_CLOSE, POST_ADD, page_island_postmanage,
)
from module.island.island_rancher import IslandRancher, ISLAND_MILL_CHECK
from module.island_fishery.assets import (
    ISLAND_FRY_SHOP_CHECK, ISLAND_FISH_FRY_SHOP_OTHER,
    ISLAND_FISH_FRY_SHOP_OTHER_CHECK,
)


class FrameFlow:
    """逐帧脚本只定义可见状态，真实流程方法决定点击和结束时机。"""

    def __init__(self, frames, task_class=Island, step=0.2):
        self.frames = frames
        self.step = step
        self.now = 1000.0
        self.frame = set()
        self.reads = 0
        self.clicks = []
        self.task = task_class.__new__(task_class)
        task = self.task
        task.interval_timer = {}
        task.island_error = False
        task.device = SimpleNamespace(image=None, click=self.click, sleep=Mock())
        task.loop = self.loop
        task.appear = self.appear
        task.appear_then_click = self.appear_then_click
        task.ui_page_appear = lambda page: page in self.frame
        task.is_character_filter_visible = lambda: 'filter' in self.frame
        task.close_character_filter = Mock()
        task.select_character = Mock(return_value=True)
        task.confirm_selected_character = Mock(return_value=True)
        task.select_product = Mock(return_value=True)
        task.confirm_post_add_order = Mock(return_value=True)
        task.back_to_postmanage_from_dispatch = Mock()
        task.ranch_ocr_finish_time = Mock(return_value='finish')
        self.time_patch = patch('module.base.timer.time', lambda: self.now)

    def loop(self, **kwargs):
        assert kwargs['skip_first'] is False
        while self.reads < len(self.frames):
            frame = self.frames[self.reads]
            self.frame = set(frame)
            self.now += self.step
            self.reads += 1
            yield None

    def appear(self, button, offset=0, interval=0, **kwargs):
        if button not in self.frame:
            return False
        if interval:
            timer = self.task.get_interval_timer(button, interval=interval, renew=True)
            if not timer.reached():
                return False
            timer.reset()
        return True

    def appear_then_click(self, button, **kwargs):
        if self.appear(button, **kwargs):
            self.click(button)
            return True
        return False

    def click(self, button, **kwargs):
        self.clicks.append((button, self.reads, self.now))

    def names(self):
        return [button.name for button, _, _ in self.clicks]

    def run(self, method, *args, **kwargs):
        with self.time_patch:
            return getattr(self.task, method)(*args, **kwargs)


class IslandRewardStateTests(unittest.TestCase):
    def test_collection_waits_for_delayed_reward_before_stay(self):
        flow = FrameFlow([
            [POST_GET], [ISLAND_POST_VACANT_CHECK],
            [ISLAND_GET, ISLAND_POST_VACANT_CHECK],
            *[[ISLAND_POST_VACANT_CHECK]] * 5,
        ])
        self.assertTrue(flow.run('post_get_stay'))
        self.assertEqual(flow.names(), ['POST_GET', 'ISLAND_POST_SAFE_AREA'])
        self.assertGreaterEqual(flow.reads, 7)
        flow.task.device.sleep.assert_not_called()

    def test_reward_cooldown_cannot_finish_on_background_list(self):
        flow = FrameFlow([
            [POST_GET], *[[ISLAND_GET, page_island_postmanage]] * 4,
            *[[page_island_postmanage]] * 5,
        ])
        self.assertTrue(flow.run('post_get_and_close'))
        self.assertGreaterEqual(flow.reads, 9)
        self.assertEqual(flow.names(), ['POST_GET', 'ISLAND_POST_SAFE_AREA'])

    def test_persistent_multi_layer_reward_retries_only_after_cooldown(self):
        flow = FrameFlow([
            [POST_GET], *[[ISLAND_GET]] * 7, *[[page_island_postmanage]] * 5,
        ], step=0.4)
        self.assertTrue(flow.run('post_get_and_close'))
        safe = [click for click in flow.clicks if click[0] is ISLAND_POST_SAFE_AREA]
        self.assertEqual(len(safe), 2)
        self.assertGreaterEqual(safe[1][2] - safe[0][2], 2)
        self.assertGreaterEqual(flow.reads, 11)

    def test_already_working_post_is_inspected_without_safety_clicks(self):
        flow = FrameFlow([[ISLAND_WORKING]] * 5)
        self.assertTrue(flow.run('post_get_stay'))
        self.assertEqual(flow.clicks, [])

    def test_empty_post_closes_after_positive_detail_confirmation(self):
        flow = FrameFlow([
            *[[ISLAND_POST_VACANT_CHECK, POST_CLOSE]] * 5,
            *[[page_island_postmanage]] * 5,
        ])
        self.assertTrue(flow.run('post_get_and_close'))
        self.assertEqual(flow.names(), ['POST_CLOSE'])
        self.assertGreaterEqual(flow.reads, 6)

    def test_error_and_filter_keep_existing_recovery_semantics(self):
        flow = FrameFlow([['filter'], [ERROR1], *[[page_island_postmanage]] * 5])
        self.assertTrue(flow.run('post_get_and_close'))
        self.assertTrue(flow.task.island_error)
        flow.task.close_character_filter.assert_called_once()
        self.assertEqual(flow.names(), ['POST_CLOSE'])

    def test_unknown_page_does_not_count_as_collection_complete(self):
        flow = FrameFlow([[]] * 5)
        self.assertFalse(flow.run('post_get_stay'))
        self.assertEqual(flow.clicks, [])

    def test_reward_finishes_before_append_dispatch(self):
        flow = FrameFlow([
            [POST_GET], [POST_ADD], [ISLAND_GET, POST_ADD],
            *[[POST_ADD]] * 5, [ISLAND_SELECT_CHARACTER_CHECK],
            [ISLAND_SELECT_PRODUCT_CHECK], [ISLAND_POST_CHECK, ISLAND_WORKING, POST_CLOSE],
            [page_island_postmanage],
        ])
        self.assertTrue(flow.run('post_get_and_add', 'product', 'selected'))
        self.assertEqual(flow.names()[:3], ['POST_GET', 'ISLAND_POST_SAFE_AREA', 'POST_ADD'])
        self.assertGreaterEqual(next(frame for button, frame, _ in flow.clicks if button is POST_ADD), 7)
        flow.task.confirm_selected_character.assert_called_once()
        flow.task.confirm_post_add_order.assert_called_once()

    def test_ranch_collect_append_uses_same_current_frame_reward_flow(self):
        flow = FrameFlow([
            [POST_GET], [ISLAND_GET, POST_ADD], *[[POST_ADD]] * 5,
            [ISLAND_SELECT_CHARACTER_CHECK], [ISLAND_SELECT_PRODUCT_CHECK],
            [ISLAND_POST_CHECK, ISLAND_WORKING, POST_CLOSE],
            [ISLAND_POSTMANAGE_CHECK, page_island_postmanage],
        ], task_class=IslandRancher)
        flow.task.ranch_feed_map = {}
        self.assertTrue(flow.run('ranch_post_get_and_add', 'post1'))
        self.assertEqual(flow.names(), ['POST_GET', 'ISLAND_POST_SAFE_AREA', 'POST_ADD', 'POST_CLOSE'])
        self.assertEqual(flow.task.ranch_last_finish_time, 'finish')
        flow.task.confirm_post_add_order.assert_called_once()

    def test_success_popup_cooldown_does_not_click_or_return_on_background_shop(self):
        shop = ISLAND_FRY_SHOP_CHECK
        flow = FrameFlow([
            [ISLAND_SHOPPING_CHECK, ISLAND_SHOP_CONFIRM],
            *[[ISLAND_SHOP_GET, ISLAND_SHOP_CONFIRM, shop]] * 4,
            *[[shop]] * 5,
        ])
        self.assertTrue(flow.run('confirm_island_shop_purchase', shop, '鱼苗'))
        self.assertEqual(flow.names(), ['ISLAND_SHOP_CONFIRM', 'ISLAND_SHOP_CONFIRM'])
        self.assertEqual([frame for _, frame, _ in flow.clicks], [1, 2])
        self.assertGreaterEqual(flow.reads, 8)
        flow.task.device.sleep.assert_not_called()

    def test_paid_purchase_can_confirm_direct_stable_shop_return(self):
        shop = ISLAND_FRY_SHOP_CHECK
        flow = FrameFlow([
            [ISLAND_SHOPPING_CHECK, ISLAND_SHOP_CONFIRM], *[[shop]] * 6,
        ])
        self.assertTrue(flow.run('confirm_island_shop_purchase', shop, '鱼苗'))
        self.assertEqual(flow.names(), ['ISLAND_SHOP_CONFIRM'])

    def test_unaffordable_material_purchase_waits_then_closes_without_repaying(self):
        shop = ISLAND_FRY_SHOP_CHECK
        flow = FrameFlow([
            *[[ISLAND_SHOPPING_CHECK, ISLAND_SHOP_CONFIRM, shop]] * 18,
            *[[shop]] * 5,
        ])
        self.assertFalse(flow.run('confirm_island_shop_purchase', shop, '鱼苗', cancel_unaffordable=True))
        self.assertEqual(flow.names(), ['ISLAND_SHOP_CONFIRM', 'ISLAND_SHOPPING_CLOSE'])
        self.assertGreaterEqual(flow.clicks[1][2] - flow.clicks[0][2], 3)
        self.assertGreaterEqual(flow.reads, 21)

    def test_slow_success_before_material_cancel_keeps_reward_priority(self):
        shop = ISLAND_FRY_SHOP_CHECK
        flow = FrameFlow([
            *[[ISLAND_SHOPPING_CHECK, ISLAND_SHOP_CONFIRM]] * 12,
            *[[ISLAND_SHOP_GET, ISLAND_SHOP_CONFIRM, shop]] * 3,
            *[[shop]] * 5,
        ])
        self.assertTrue(flow.run('confirm_island_shop_purchase', shop, '鱼苗', cancel_unaffordable=True))
        self.assertEqual(flow.names(), ['ISLAND_SHOP_CONFIRM', 'ISLAND_SHOP_CONFIRM'])

    def test_shop_without_payment_or_success_does_not_report_purchase_success(self):
        flow = FrameFlow([[ISLAND_FRY_SHOP_CHECK]] * 7)
        self.assertFalse(flow.run('confirm_island_shop_purchase', ISLAND_FRY_SHOP_CHECK, '鱼苗'))
        self.assertEqual(flow.clicks, [])

    def test_payment_then_unknown_page_does_not_report_purchase_success(self):
        flow = FrameFlow([[ISLAND_SHOPPING_CHECK, ISLAND_SHOP_CONFIRM], *[[]] * 6])
        self.assertFalse(flow.run('confirm_island_shop_purchase', ISLAND_FRY_SHOP_CHECK, '鱼苗'))
        self.assertEqual(flow.names(), ['ISLAND_SHOP_CONFIRM'])

    def test_transient_list_does_not_finish_before_late_reward(self):
        flow = FrameFlow([
            [POST_GET], [page_island_postmanage], [page_island_postmanage],
            [ISLAND_GET, page_island_postmanage], *[[page_island_postmanage]] * 5,
        ])
        self.assertTrue(flow.run('post_get_and_close'))
        self.assertEqual(flow.names(), ['POST_GET', 'ISLAND_POST_SAFE_AREA'])
        self.assertGreaterEqual(flow.reads, 8)

    def test_transient_shop_does_not_finish_before_late_success(self):
        shop = ISLAND_FRY_SHOP_CHECK
        flow = FrameFlow([
            [ISLAND_SHOPPING_CHECK, ISLAND_SHOP_CONFIRM], [shop], [shop],
            [ISLAND_SHOP_GET, shop], *[[shop]] * 5,
        ])
        self.assertTrue(flow.run('confirm_island_shop_purchase', shop, '鱼苗'))
        self.assertEqual(flow.names(), ['ISLAND_SHOP_CONFIRM', 'ISLAND_SHOP_CONFIRM'])
        self.assertGreaterEqual(flow.reads, 8)

    def test_success_then_repainted_quantity_page_never_pays_again(self):
        flow = FrameFlow([
            [ISLAND_SHOP_GET], *[[ISLAND_SHOPPING_CHECK, ISLAND_SHOP_CONFIRM]] * 15,
            *[[ISLAND_FRY_SHOP_CHECK]] * 5,
        ])
        self.assertTrue(flow.run('confirm_island_shop_purchase', ISLAND_FRY_SHOP_CHECK, '鱼苗'))
        self.assertEqual(flow.names(), ['ISLAND_SHOP_CONFIRM'])

    def test_next_confirmed_purchase_can_close_new_popup_without_previous_cooldown(self):
        shop = ISLAND_FRY_SHOP_CHECK
        flow = FrameFlow([
            [ISLAND_SHOPPING_CHECK, ISLAND_SHOP_CONFIRM], [ISLAND_SHOP_GET],
            *[[shop]] * 5,
            [ISLAND_SHOPPING_CHECK, ISLAND_SHOP_CONFIRM], [ISLAND_SHOP_GET],
            *[[shop]] * 5,
        ])
        self.assertTrue(flow.run('confirm_island_shop_purchase', shop, '鱼苗一'))
        # 新交易的支付已由调用方打开数量页；只清支付间隔模拟商店开下一商品。
        flow.task.interval_clear(ISLAND_SHOP_CONFIRM)
        self.assertTrue(flow.run('confirm_island_shop_purchase', shop, '鱼苗二'))
        self.assertEqual(len(flow.clicks), 4)
        success_closes = [flow.clicks[1], flow.clicks[3]]
        self.assertLess(success_closes[1][2] - success_closes[0][2], 2)

    def test_purchase_and_return_keep_quantity_and_share_late_popup_cooldown(self):
        shop = ISLAND_FRY_SHOP_CHECK
        flow = FrameFlow([
            [ISLAND_SHOPPING_CHECK], [ISLAND_SHOPPING_CHECK, ISLAND_SHOP_CONFIRM],
            [ISLAND_SHOP_GET], *[[shop]] * 5,
            [ISLAND_SHOP_GET, ISLAND_SELECT_PRODUCT_CHECK],
            [ISLAND_SELECT_PRODUCT_CHECK],
        ])
        flow.task.set_buy_number = Mock()
        self.assertTrue(flow.run('buy_shop_item', ISLAND_SELECT_SEED, 7, shop))
        self.assertTrue(flow.run('back_to_select_product_after_shop'))
        flow.task.set_buy_number.assert_called_once_with(7)
        self.assertEqual(flow.names(), ['ISLAND_SHOP_CONFIRM', 'ISLAND_SHOP_CONFIRM'])
        self.assertEqual(flow.reads, 10)

    def test_mill_entry_collects_before_opening_dispatch_and_keeps_character(self):
        flow = FrameFlow([
            [POST_GET], [ISLAND_GET, POST_ADD], *[[POST_ADD]] * 5,
            [ISLAND_SELECT_CHARACTER_CHECK], [ISLAND_SELECT_PRODUCT_CHECK],
        ], task_class=IslandRancher)
        flow.task.posts_ranch = {'post1': 'post1'}
        flow.task.post_close = Mock(return_value=True)
        flow.task.post_open = Mock(return_value=True)
        flow.task.goto_shop_from_select_product = Mock(return_value=True)
        self.assertTrue(flow.run('goto_mill_from_ranch_post', 'post1'))
        self.assertEqual(flow.names(), ['POST_GET', 'ISLAND_POST_SAFE_AREA', 'POST_ADD'])
        flow.task.select_character.assert_called_once_with('WorkerJuu')
        flow.task.goto_shop_from_select_product.assert_called_once_with(shop_check=ISLAND_MILL_CHECK)

    def test_return_from_shop_reuses_popup_close_cooldown(self):
        flow = FrameFlow([
            *[[ISLAND_SHOP_GET, ISLAND_SHOP_CONFIRM, ISLAND_SELECT_PRODUCT_CHECK]] * 4,
            [ISLAND_SELECT_PRODUCT_CHECK],
        ])
        self.assertTrue(flow.run('back_to_select_product_after_shop'))
        self.assertEqual(flow.reads, 5)
        self.assertEqual(flow.names(), ['ISLAND_SHOP_CONFIRM'])

    def test_return_closes_purchase_before_accepting_visible_background(self):
        flow = FrameFlow([
            [ISLAND_SHOPPING_CHECK, ISLAND_SELECT_PRODUCT_CHECK],
            [ISLAND_SELECT_PRODUCT_CHECK],
        ])
        self.assertTrue(flow.run('back_to_select_product_after_shop'))
        self.assertEqual(flow.reads, 2)
        self.assertEqual(flow.names(), ['ISLAND_SHOPPING_CLOSE'])

    def test_mill_back_ignores_background_management_until_success_closed(self):
        flow = FrameFlow([
            *[[ISLAND_SHOP_GET, ISLAND_SHOP_CONFIRM, page_island_postmanage]] * 3,
            [page_island_postmanage],
        ], task_class=IslandRancher)
        self.assertTrue(flow.run('back_to_postmanage_after_mill_purchase'))
        self.assertEqual(flow.reads, 4)
        self.assertEqual(flow.names(), ['ISLAND_SHOP_CONFIRM'])

    def test_switch_shop_tab_waits_for_ready_and_does_not_spend_cooldown_in_transition(self):
        shop = ISLAND_FRY_SHOP_CHECK
        tab = ISLAND_FISH_FRY_SHOP_OTHER
        selected = ISLAND_FISH_FRY_SHOP_OTHER_CHECK
        flow = FrameFlow([
            [tab], *[[shop, tab]] * 4, [shop, selected],
        ])
        self.assertTrue(flow.run('switch_shop_tab', selected, tab, shop_check=shop))
        self.assertEqual(flow.names(), [tab.name])
        self.assertGreaterEqual(flow.clicks[0][1], 4)
        flow.task.device.sleep.assert_not_called()

    def test_tab_retries_are_limited_and_stop_immediately_when_selected(self):
        tab = ISLAND_FISH_FRY_SHOP_OTHER
        selected = ISLAND_FISH_FRY_SHOP_OTHER_CHECK
        shop = ISLAND_FRY_SHOP_CHECK
        flow = FrameFlow([*[[shop, tab]] * 16, [shop, selected]], step=0.2)
        self.assertTrue(flow.run('switch_shop_tab', selected, tab, shop_check=shop))
        self.assertEqual(len(flow.clicks), 2)
        self.assertGreaterEqual(flow.clicks[1][2] - flow.clicks[0][2], 2)
        self.assertEqual(flow.reads, 17)

    def test_goto_material_shop_does_not_click_missing_purchase_entry(self):
        shop = ISLAND_FRY_SHOP_CHECK
        tab = ISLAND_FISH_FRY_SHOP_OTHER
        selected = ISLAND_FISH_FRY_SHOP_OTHER_CHECK
        flow = FrameFlow([
            [ISLAND_SELECT_PRODUCT_CHECK], [ISLAND_SELECT_PRODUCT_CHECK],
            [ISLAND_SELECT_GOTO_BUY_SEED], [tab],
            *[[shop, tab]] * 4, [shop, selected],
        ])
        self.assertTrue(flow.run('goto_shop_from_select_product', shop, selected, tab))
        self.assertEqual(flow.names(), [ISLAND_SELECT_SEED.name, ISLAND_SELECT_GOTO_BUY_SEED.name, tab.name])
        flow.task.device.sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()
