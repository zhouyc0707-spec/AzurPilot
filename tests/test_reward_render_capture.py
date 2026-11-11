"""奖励渲染后取新帧，同时保留本地委托分批记账与科研事件分组。"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

from module.base import timer
from module.commission import commission
from module.research import ui as research


class TestCommissionRenderCapture(unittest.TestCase):
    def run_frames(self, buttons):
        handler = MagicMock()
        handler.config = SimpleNamespace(
            config_name="render-test", DropRecord_CommissionRecord="save", SERVER="en",
            Commission_DetectShipDrop=False,
        )
        drop = handler.stat.new.return_value.__enter__.return_value
        handler.ui_main_appear_then_click.return_value = False
        handler.appear_then_click.return_value = False
        handler.ui_additional.return_value = False
        handler._record_commission_income.return_value = True
        state = {"frame": 0}

        def screenshot():
            state["frame"] += 1
            if state["frame"] > len(buttons):
                raise AssertionError("奖励领取没有在预期帧内退出")
            handler.device.image = np.full((2, 2, 3), state["frame"], dtype=np.uint8)

        handler.device.image = np.zeros((2, 2, 3), dtype=np.uint8)
        handler.device.screenshot.side_effect = screenshot
        handler.ui_page_appear.side_effect = lambda *a, **kw: state["frame"] == len(buttons)
        handler.appear.side_effect = lambda button, **kw: button is buttons[state["frame"]]

        def additional_popup():
            if buttons[state["frame"]] in (commission.GET_ITEMS_1, commission.GET_ITEMS_2):
                raise AssertionError("通用弹窗处理器会直接点掉未记录的奖励")
            return False

        handler.ui_additional.side_effect = additional_popup
        saved = []
        drop.add.side_effect = lambda image: saved.append(int(image[0, 0, 0]))
        clicks = []
        handler.device.click.side_effect = lambda button: clicks.append(state["frame"])
        with (patch.object(timer, "time", side_effect=lambda: 10 + state["frame"] * .26),
              patch("module.statistics.cl1_database.db") as db,
              patch("module.statistics.commission_drop.classify_frame", return_value='other'),
              patch("module.statistics.commission_drop.read_commission_name", side_effect=ValueError('无名称夹具'))):
            result = commission.RewardCommission._commission_receive(handler)
        handler.device.sleep.assert_not_called()
        return handler, db, result, saved, clicks

    def test_capture_fresh_frame_and_keep_separate_reward_batches(self):
        first, second, experience = commission.GET_ITEMS_1, commission.GET_ITEMS_2, commission.EXP_INFO_S_REWARD
        handler, db, result, saved, clicks = self.run_frames(
            [first, first, first, second, second, second, second, experience, experience, None]
        )
        self.assertTrue(result)
        # 首次标题帧 0/4 不保存；点击后的下一帧 3/7 不穿透。
        self.assertEqual(saved, [2, 6, 8])
        self.assertEqual(clicks, [2, 6, 8])
        handler._handle_research_genre_t_update.assert_called_once_with(1)
        self.assertEqual(handler._record_commission_income.call_count, 2)
        db.settle_expired_gem_commissions.assert_called_once()

    def test_changed_popup_is_not_saved_as_pending_reward(self):
        first, second = commission.GET_ITEMS_1, commission.GET_ITEMS_2
        _, _, result, saved, clicks = self.run_frames([first, second, second, second, second, None])
        self.assertTrue(result)
        self.assertEqual(saved, [4])
        self.assertEqual(clicks, [4])


class TestResearchRenderCapture(unittest.TestCase):
    def test_single_page_rewards_use_new_frame_and_keep_event_group(self):
        for button in (research.GET_ITEMS_1, research.GET_ITEMS_2):
            with self.subTest(button=button.name):
                handler = MagicMock()
                handler.get_items.return_value = button
                handler.device.image = "old"
                handler.device.screenshot.side_effect = lambda: setattr(handler.device, "image", "rendered")
                drop = MagicMock(count=7)
                research.ResearchUI.drop_record(handler, drop)
                handler.device.sleep.assert_called_once_with(.5)
                handler.device.screenshot.assert_called_once()
                drop.add.assert_called_once_with("rendered")
                drop.finish_research_reward.assert_called_once_with(7)


if __name__ == "__main__":
    unittest.main()
