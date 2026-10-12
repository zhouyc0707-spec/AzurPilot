"""隔离验证明石商品复用和加载重拍，不读取用户配置或操作游戏。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from module.base.button import ButtonGrid
from module.os_shop.akashi_shop import AkashiShop
from module.os_shop.assets import PORT_SUPPLY_CHECK
from module.os_shop.item import OSShopItemGrid


class AkashiInventoryFixture(unittest.TestCase):
    def make_runner(self, session=True):
        runner = AkashiShop.__new__(AkashiShop)
        runner.config = SimpleNamespace(SHOP_EXTRACT_TEMPLATE=False)
        runner.device = SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8))
        runner.appear = Mock(return_value=True)
        runner._akashi_shop_session = {} if session else None
        grid = OSShopItemGrid(ButtonGrid((233, 224), (193, 228), (98, 98), (4, 2)), {})
        runner.__dict__['os_akashi_shop_items'] = grid
        grid.match_template = Mock(return_value='ActionPoint20')
        grid.match_cost_template = Mock(side_effect=lambda item: 'YellowCoins'
                                       if np.any(item.crop(grid.cost_area)) else None)
        grid.amount_ocr = SimpleNamespace(ocr_batch_with_validation=Mock(
            side_effect=lambda images, **kwargs: [int(image[0, 0, 0]) for image in images]))
        grid.price_ocr = SimpleNamespace(ocr=Mock(
            side_effect=lambda images, **kwargs: [int(image[0, 0, 0]) * 10 for image in images]))
        grid.predict = Mock(wraps=grid.predict)
        for button in grid.grids.buttons:
            x, y, right, bottom = button.area
            runner.device.image[y:bottom, x:right] = (150, 180, 200)
            self.paint(runner.device.image, button, grid.amount_area, 2)
            self.paint(runner.device.image, button, grid.cost_area, 100)
            self.paint(runner.device.image, button, grid.price_area, 100)
        return runner, grid

    @staticmethod
    def paint(image, button, relative_area, value):
        x, y = button.area[:2]
        left, top, right, bottom = relative_area
        image[y + top:y + bottom, x + left:x + right] = value

    @staticmethod
    def clear_recognition_calls(grid):
        grid.predict.reset_mock()
        grid.match_template.reset_mock()
        grid.match_cost_template.reset_mock()
        grid.amount_ocr.ocr_batch_with_validation.reset_mock()
        grid.price_ocr.ocr.reset_mock()

    def snapshot(self, runner):
        return [(item.area, item.name, item.amount, item.cost, item.price)
                for item in runner._akashi_predict_inventory()]


class AkashiInventoryTests(AkashiInventoryFixture):
    def test_unchanged_inventory_skips_all_recognition(self):
        runner, grid = self.make_runner()
        before = self.snapshot(runner)
        self.clear_recognition_calls(grid)
        self.assertEqual(self.snapshot(runner), before)
        grid.predict.assert_not_called()
        grid.amount_ocr.ocr_batch_with_validation.assert_not_called()
        grid.price_ocr.ocr.assert_not_called()

    def test_sold_out_slot_is_removed_and_other_seven_slots_are_reused(self):
        runner, grid = self.make_runner()
        self.snapshot(runner)
        self.clear_recognition_calls(grid)
        sold = grid.grids.buttons[3]
        self.paint(runner.device.image, sold, grid.cost_area, 0)
        items = runner._akashi_predict_inventory()
        self.assertEqual(len(items), 7)
        self.assertNotIn(sold.area, [item.area for item in items])
        self.assertEqual(grid.match_template.call_count, 1)
        self.assertEqual(len(grid.amount_ocr.ocr_batch_with_validation.call_args.args[0]), 1)
        grid.price_ocr.ocr.assert_not_called()

    def test_each_identification_region_change_forces_that_slot_to_refresh(self):
        for field in ('template_area', 'amount_area', 'cost_area', 'price_area',
                      'tag_area', 'counter_area'):
            with self.subTest(field=field):
                runner, grid = self.make_runner()
                self.snapshot(runner)
                self.clear_recognition_calls(grid)
                button = grid.grids.buttons[1]
                left, top, _, _ = getattr(grid, field)
                x, y = button.area[:2]
                runner.device.image[y + top, x + left, 0] ^= 1
                self.snapshot(runner)
                self.assertEqual(grid.match_template.call_count, 1)

    def test_amount_and_price_changes_return_new_actual_values(self):
        runner, grid = self.make_runner()
        self.snapshot(runner)
        button = grid.grids.buttons[2]
        self.paint(runner.device.image, button, grid.amount_area, 3)
        self.paint(runner.device.image, button, grid.price_area, 120)
        item = next(item for item in runner._akashi_predict_inventory() if item.area == button.area)
        self.assertEqual((item.amount, item.price), (3, 1200))

    def test_pixels_outside_item_regions_do_not_force_full_scan(self):
        runner, grid = self.make_runner()
        self.snapshot(runner)
        self.clear_recognition_calls(grid)
        runner.device.image[5, 5] = 255
        self.snapshot(runner)
        grid.predict.assert_not_called()

    def test_new_session_forces_full_inventory_scan(self):
        runner, grid = self.make_runner()
        self.snapshot(runner)
        self.clear_recognition_calls(grid)
        runner._akashi_shop_session = {}
        self.snapshot(runner)
        self.assertEqual(grid.match_template.call_count, 8)

    def test_without_session_every_call_retains_full_scan(self):
        runner, grid = self.make_runner(session=False)
        self.snapshot(runner)
        self.clear_recognition_calls(grid)
        self.snapshot(runner)
        self.assertEqual(grid.match_template.call_count, 8)

    def test_extraction_mode_discards_existing_cache(self):
        runner, grid = self.make_runner()
        self.snapshot(runner)
        grid.extract_template = Mock()
        runner.config.SHOP_EXTRACT_TEMPLATE = True
        self.clear_recognition_calls(grid)
        runner.os_shop_get_items_in_akashi()
        self.assertNotIn('inventory', runner._akashi_shop_session)
        self.assertEqual(grid.match_template.call_count, 8)
        grid.extract_template.assert_called_once()

    def test_unknown_item_is_reidentified_even_when_pixels_do_not_change(self):
        runner, grid = self.make_runner()
        grid.match_template.side_effect = ['123'] + ['ActionPoint20'] * 8
        self.snapshot(runner)
        self.clear_recognition_calls(grid)
        items = runner._akashi_predict_inventory()
        self.assertEqual(grid.match_template.call_count, 1)
        self.assertTrue(all(item.is_known_item() for item in items))

    def test_failed_price_is_not_cached_as_confirmed_empty_slot(self):
        runner, grid = self.make_runner()
        button = grid.grids.buttons[0]
        self.paint(runner.device.image, button, grid.price_area, 0)
        self.assertEqual(len(runner._akashi_predict_inventory()), 7)
        self.clear_recognition_calls(grid)
        runner._akashi_predict_inventory()
        self.assertEqual(grid.match_template.call_count, 1)

    def test_wrong_page_discards_cache_and_returns_no_stale_items(self):
        runner, grid = self.make_runner()
        self.snapshot(runner)
        runner.appear.return_value = False
        self.assertEqual(runner.os_shop_get_items_in_akashi(), [])
        self.assertNotIn('inventory', runner._akashi_shop_session)
        self.assertEqual(grid.items, [])

    def test_grid_layout_and_image_shape_changes_force_full_scan(self):
        for changed in ('layout', 'shape'):
            with self.subTest(changed=changed):
                runner, grid = self.make_runner()
                self.snapshot(runner)
                self.clear_recognition_calls(grid)
                if changed == 'layout':
                    grid.price_area = (53, 132, 132, 156)
                else:
                    runner.device.image = np.pad(runner.device.image, ((0, 1), (0, 0), (0, 0)))
                self.snapshot(runner)
                self.assertEqual(grid.match_template.call_count, 8)

    def test_partial_scan_failure_restores_original_grid(self):
        runner, grid = self.make_runner()
        self.snapshot(runner)
        original = grid.grids
        runner.device.image[224, 233, 0] ^= 1
        grid.predict.side_effect = RuntimeError('隔离的识别失败')
        with self.assertRaises(RuntimeError):
            self.snapshot(runner)
        self.assertIs(grid.grids, original)

    def test_three_purchases_match_full_scans_with_fewer_item_recognitions(self):
        runner, grid = self.make_runner()
        full_runner, full_grid = self.make_runner(session=False)
        for purchased in range(4):
            if purchased:
                self.paint(runner.device.image, grid.grids.buttons[purchased - 1], grid.cost_area, 0)
            full_runner.device.image = runner.device.image.copy()
            self.assertEqual(self.snapshot(runner), self.snapshot(full_runner))
        # 空/售罄格继续确认，未变化的有效商品无需再做名称、数量和价格识别。
        self.assertEqual(grid.match_template.call_count, 8 + 1 + 2 + 3)
        self.assertEqual(full_grid.match_template.call_count, 8 * 4)

    def test_cached_inventory_still_uses_new_balance_before_selection(self):
        runner, grid = self.make_runner()
        runner.config.OpsiGeneral_AkashiShopFilter = 'ActionPoint'
        runner._shop_purple_coins = 0
        runner._shop_yellow_coins = 1000
        runner.os_shop_get_coins = Mock()
        self.assertIsNotNone(runner.os_shop_get_item_to_buy_in_akashi())
        self.clear_recognition_calls(grid)
        runner._shop_yellow_coins = 999
        self.assertIsNone(runner.os_shop_get_item_to_buy_in_akashi())
        self.assertEqual(runner.os_shop_get_coins.call_count, 2)
        grid.predict.assert_not_called()

    def test_new_actual_price_cannot_reuse_old_cheaper_purchase(self):
        runner, grid = self.make_runner()
        runner.config.OpsiGeneral_AkashiShopFilter = 'ActionPoint'
        runner._shop_purple_coins = 0
        runner._shop_yellow_coins = 900
        runner.os_shop_get_coins = Mock()
        button = grid.grids.buttons[0]
        self.paint(runner.device.image, button, grid.price_area, 80)
        self.assertEqual(runner.os_shop_get_item_to_buy_in_akashi().price, 800)
        self.paint(runner.device.image, button, grid.price_area, 100)
        self.assertIsNone(runner.os_shop_get_item_to_buy_in_akashi())


class AkashiLoadingRetryTests(unittest.TestCase):
    def runner(self, results):
        runner = AkashiShop.__new__(AkashiShop)
        runner.device = SimpleNamespace(screenshot=Mock(), sleep=Mock())
        runner.appear = Mock(return_value=True)
        runner.os_shop_get_coins = Mock()
        runner.os_shop_get_items_in_akashi = Mock(side_effect=results)
        runner.items_filter_in_akashi_shop = Mock(side_effect=lambda items: list(items))
        return runner

    @staticmethod
    def item(known=True):
        return SimpleNamespace(is_known_item=lambda: known)

    def test_last_retry_success_is_selected_instead_of_discarded(self):
        item = self.item()
        runner = self.runner([[], [self.item(False)], [item]])
        self.assertIs(runner.os_shop_get_item_to_buy_in_akashi(), item)
        self.assertEqual(runner.device.screenshot.call_count, 2)
        runner.device.sleep.assert_not_called()

    def test_persistent_unknown_is_bounded_and_never_filtered_for_purchase(self):
        runner = self.runner([[self.item(False)]] * 3)
        self.assertIsNone(runner.os_shop_get_item_to_buy_in_akashi())
        self.assertEqual(runner.os_shop_get_items_in_akashi.call_count, 3)
        self.assertEqual(runner.device.screenshot.call_count, 2)
        runner.items_filter_in_akashi_shop.assert_not_called()

    def test_page_change_stops_retry_without_selecting_an_item(self):
        runner = self.runner([[]])
        runner.appear.side_effect = [True, False]
        self.assertIsNone(runner.os_shop_get_item_to_buy_in_akashi())
        runner.os_shop_get_items_in_akashi.assert_called_once()
        self.assertEqual(runner.appear.call_args.args[0], PORT_SUPPLY_CHECK)

    def test_first_success_keeps_filter_order_and_fresh_balance_read(self):
        first, last = self.item(), self.item()
        runner = self.runner([[first, last]])
        self.assertIs(runner.os_shop_get_item_to_buy_in_akashi(), last)
        runner.os_shop_get_coins.assert_called_once()
        runner.items_filter_in_akashi_shop.assert_called_once_with([first, last])
        runner.device.screenshot.assert_not_called()

    def test_empty_filtered_inventory_does_not_retry(self):
        runner = self.runner([[self.item()]])
        runner.items_filter_in_akashi_shop.return_value = []
        runner.items_filter_in_akashi_shop.side_effect = None
        self.assertIsNone(runner.os_shop_get_item_to_buy_in_akashi())
        runner.device.screenshot.assert_not_called()


if __name__ == '__main__':
    unittest.main()
