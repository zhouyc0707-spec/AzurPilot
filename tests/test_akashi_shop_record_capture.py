"""隔离验证明石猫王仅记录购买前完整、独立确认的货架。"""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np
from PIL import Image

from module.base.button import ButtonGrid
from module.base.utils import crop
from module.os_shop.akashi_record import observation, observe_record
from module.os_shop.akashi_shop import AkashiShop
from module.os_shop.assets import PORT_SUPPLY_CHECK
from module.os_shop.item import OSShopItemGrid
from module.os_shop.shop import OSShop
from tests.test_opsi_akashi_inventory import AkashiInventoryFixture as _InventoryFixture


class AkashiCaptureFixture(unittest.TestCase):
    def setUp(self):
        self.store = Mock()
        self.store.records.return_value = {'monthly': None, 'historical': None}
        self.store_class = self.enterContext(patch(
            'module.os_shop.akashi_record.AkashiShopRecords', return_value=self.store
        ))

    def make_selector(self):
        runner, grid = _InventoryFixture().make_runner()
        runner.config.config_name = 'capture-test'
        runner.config.SERVER = 'cn'
        runner.config.OpsiGeneral_AkashiShopFilter = 'ActionPoint'
        runner._shop_yellow_coins = 100000
        runner._shop_purple_coins = 0
        runner.os_shop_get_coins = Mock()
        runner.device.screenshot = Mock()
        original = grid.amount_ocr.ocr_batch_with_validation.side_effect

        def amount(images, **kwargs):
            grid.amount_ocr.last_batch_validity = (True,) * len(images)
            return original(images, **kwargs)

        grid.amount_ocr.ocr_batch_with_validation.side_effect = amount
        return runner, grid

    def make_shelf(self):
        """用现有真实图标和空模板构造六个商品、两个空位的整店。"""
        runner = AkashiShop.__new__(AkashiShop)
        runner.config = SimpleNamespace(config_name='capture-test', SERVER='cn',
                                        SHOP_EXTRACT_TEMPLATE=False)
        runner._akashi_shop_session = {}
        runner.device = SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8))
        grid = OSShopItemGrid(ButtonGrid((233, 224), (193, 228), (98, 98), (4, 2)), {})
        runner.__dict__['os_akashi_shop_items'] = grid
        directory = Path(__file__).resolve().parents[1] / 'assets' / 'shop' / 'os'
        for file in directory.glob('Empty*.png'):
            image = np.array(Image.open(file).convert('RGB'))
            template = crop(image, grid.template_area)
            grid.templates[file.stem] = template
            grid.colors[file.stem] = cv2.mean(template)[:3]
        goods = [
            ('PurpleCoins', 5, 'YellowCoins', 400),
            ('ActionPoint20', 2, 'YellowCoins', 1600),
            ('ActionPoint50', 2, 'YellowCoins', 4000),
            ('ActionPoint100', 2, 'YellowCoins', 8000),
            ('RepairPack', 5, 'YellowCoins', 3000),
            ('TuningSampleSurvival', 1, 'EnergyStorage', 1),
        ]
        items = []
        for index, button in enumerate(grid.grids.buttons):
            name = goods[index][0] if index < len(goods) else 'EmptyT1'
            picture = np.array(Image.open(directory / f'{name}.png').convert('RGB'))
            left, top, right, bottom = button.area
            runner.device.image[top:bottom, left:right] = cv2.resize(
                picture, (right - left, bottom - top), interpolation=cv2.INTER_CUBIC
            )
            if index < len(goods):
                item = grid.item_class(runner.device.image, button)
                item.name, item.amount, item.cost, item.price = goods[index]
                item.amount_valid = True
                items.append(item)
        return runner, grid, items


class AkashiShelfObservationTests(AkashiCaptureFixture):
    def test_real_empty_templates_and_energy_storage_allow_complete_six_item_shelf(self):
        runner, _, items = self.make_shelf()
        total, boxes, signature = observation(runner, items)
        self.assertEqual((total, boxes), (340, {'20': 2, '50': 2, '100': 2}))
        self.assertEqual(len(signature), 8)
        self.assertEqual([item.count for item in items], [-1] * 6)

    def test_duplicate_action_point_goods_are_summed_by_slot(self):
        runner, _, items = self.make_shelf()
        items[0].name, items[0].amount = 'ActionPoint20', 3
        total, boxes, _ = observation(runner, items)
        self.assertEqual((total, boxes), (400, {'20': 5, '50': 2, '100': 2}))

    def test_missing_actual_goods_never_become_empty_slots(self):
        for index in (0, 1, 2, 3, 4, 5):
            with self.subTest(index=index):
                runner, _, items = self.make_shelf()
                items.pop(index)
                self.assertIsNone(observation(runner, items))

    def test_dark_missing_slot_is_not_positive_empty_evidence(self):
        runner, grid, items = self.make_shelf()
        left, top, right, bottom = grid.grids.buttons[-1].area
        runner.device.image[top:bottom, left:right] = 0
        self.assertIsNone(observation(runner, items))

    def test_unknown_or_unreliable_goods_block_record(self):
        cases = [('name', '123'), ('price', 0), ('amount', 0),
                 ('amount', -1), ('amount', True), ('amount_valid', False),
                 ('cost', 'DefaultCost')]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                runner, _, items = self.make_shelf()
                setattr(items[1], field, value)
                self.assertIsNone(observation(runner, items))

    def test_duplicate_areas_and_wrong_resolution_are_rejected(self):
        runner, _, items = self.make_shelf()
        self.assertIsNone(observation(runner, items + [items[0]]))
        runner.device.image = runner.device.image[:719]
        self.assertIsNone(observation(runner, items))


class AkashiRecordSelectorTests(AkashiCaptureFixture):
    def test_candidate_forces_independent_ocr_before_filter_and_saves_full_copy(self):
        runner, grid = self.make_selector()
        runner.items_filter_in_akashi_shop = Mock(side_effect=lambda items: list(items))
        selected = runner.os_shop_get_item_to_buy_in_akashi()
        self.assertIsNotNone(selected)
        self.assertEqual(grid.predict.call_count, 2)
        self.assertEqual(grid.amount_ocr.ocr_batch_with_validation.call_count, 2)
        self.assertEqual(grid.price_ocr.ocr.call_count, 2)
        runner.device.screenshot.assert_called_once()
        runner.items_filter_in_akashi_shop.assert_called_once()
        self.store.record.assert_called_once()
        instance, server, total, boxes, saved = self.store.record.call_args.args
        self.assertEqual((instance, server, total, boxes),
                         ('capture-test', 'cn', 320, {'20': 16, '50': 0, '100': 0}))
        self.assertEqual(saved.shape, (720, 1280, 3))
        np.testing.assert_array_equal(saved, runner.device.image)
        runner.device.image[0, 0] = 255
        self.assertFalse(np.array_equal(saved, runner.device.image))

    def test_shop_below_monthly_record_does_not_rescreenshot_or_reocr(self):
        runner, grid = self.make_selector()
        self.store.records.return_value = {'monthly': {'totalAp': 321}, 'historical': None}
        self.assertIsNotNone(runner.os_shop_get_item_to_buy_in_akashi())
        self.assertEqual(grid.predict.call_count, 1)
        self.assertEqual(grid.amount_ocr.ocr_batch_with_validation.call_count, 1)
        runner.device.screenshot.assert_not_called()
        self.store.record.assert_not_called()

    def test_no_action_point_goods_do_not_request_confirmation(self):
        runner, grid = self.make_selector()
        grid.match_template.return_value = 'RepairPack'
        self.assertIsNone(runner.os_shop_get_item_to_buy_in_akashi())
        self.assertEqual(grid.predict.call_count, 1)
        runner.device.screenshot.assert_not_called()
        self.store.record.assert_not_called()

    def test_amount_validity_stays_attached_to_its_slot_after_cost_and_price_filtering(self):
        runner, grid = self.make_selector()

        def amount(images, **kwargs):
            grid.amount_ocr.last_batch_validity = (True, False) + (True,) * (len(images) - 2)
            return [2] * len(images)

        grid.amount_ocr.ocr_batch_with_validation.side_effect = amount
        _InventoryFixture.paint(runner.device.image, grid.grids.buttons[0], grid.cost_area, 0)
        _InventoryFixture.paint(runner.device.image, grid.grids.buttons[-1], grid.price_area, 0)
        items = grid.predict(runner.device.image)
        self.assertEqual(len(items), 6)
        self.assertEqual(items[0].area, grid.grids.buttons[1].area)
        self.assertFalse(items[0].amount_valid)
        self.assertTrue(all(item.amount_valid for item in items[1:]))

    def test_equal_record_is_rechecked_and_latest_shop_is_saved(self):
        runner, _ = self.make_selector()
        self.store.records.return_value = {'monthly': {'totalAp': 320}, 'historical': {'totalAp': 320}}
        self.assertIsNotNone(runner.os_shop_get_item_to_buy_in_akashi())
        runner.device.screenshot.assert_called_once()
        self.store.record.assert_called_once()

    def test_zero_balance_and_filter_exclusion_do_not_hide_for_sale_total(self):
        for reason in ('balance', 'filter'):
            with self.subTest(reason=reason):
                self.store.reset_mock()
                runner, _ = self.make_selector()
                if reason == 'balance':
                    runner._shop_yellow_coins = 0
                else:
                    runner.config.OpsiGeneral_AkashiShopFilter = 'PurpleCoins'
                self.assertIsNone(runner.os_shop_get_item_to_buy_in_akashi())
                self.store.record.assert_called_once()
                self.assertEqual(self.store.record.call_args.args[2], 320)

    def test_identical_pixels_with_disagreeing_ocr_cannot_confirm_record(self):
        runner, grid = self.make_selector()
        readings = iter((2, 3, 4))

        def amount(images, **kwargs):
            grid.amount_ocr.last_batch_validity = (True,) * len(images)
            return [next(readings)] * len(images)

        grid.amount_ocr.ocr_batch_with_validation.side_effect = amount
        selected = runner.os_shop_get_item_to_buy_in_akashi()
        self.assertEqual(selected.amount, 4)
        self.assertEqual(grid.amount_ocr.ocr_batch_with_validation.call_count, 3)
        self.assertEqual(runner.device.screenshot.call_count, 2)
        self.store.record.assert_not_called()

    def test_next_two_stable_frames_can_confirm_changed_first_read(self):
        runner, grid = self.make_selector()

        def rescreenshot():
            _InventoryFixture.paint(runner.device.image, grid.grids.buttons[0], grid.amount_area, 3)

        runner.device.screenshot.side_effect = rescreenshot
        self.assertIsNotNone(runner.os_shop_get_item_to_buy_in_akashi())
        self.assertEqual(grid.predict.call_count, 3)
        self.store.record.assert_called_once()
        self.assertEqual(self.store.record.call_args.args[2], 340)

    def test_no_record_when_first_complete_read_is_final_retry(self):
        runner, grid = self.make_selector()
        items = runner._akashi_predict_inventory()
        grid.predict.reset_mock()
        runner.os_shop_get_items_in_akashi = Mock(side_effect=[[], [], items])
        self.assertIsNotNone(runner.os_shop_get_item_to_buy_in_akashi())
        self.assertEqual(runner.device.screenshot.call_count, 2)
        self.store.record.assert_not_called()

    def test_page_change_during_confirmation_prevents_record_and_purchase(self):
        runner, _ = self.make_selector()
        runner.appear.side_effect = lambda button, **kwargs: runner.device.screenshot.call_count == 0
        self.assertIsNone(runner.os_shop_get_item_to_buy_in_akashi())
        self.store.record.assert_not_called()
        self.assertEqual(runner.appear.call_args.args[0], PORT_SUPPLY_CHECK)

    def test_saving_failure_does_not_stop_purchase_or_repeat_record(self):
        runner, _ = self.make_selector()
        self.store.record.side_effect = OSError('隔离的截图保存失败')
        with patch('module.os_shop.akashi_record.logger.exception') as failure:
            self.assertIsNotNone(runner.os_shop_get_item_to_buy_in_akashi())
            self.assertIsNotNone(runner.os_shop_get_item_to_buy_in_akashi())
        self.store.record.assert_called_once()
        self.assertEqual(runner.device.screenshot.call_count, 1)
        failure.assert_called_once()

    def test_same_session_records_once_and_new_session_requires_new_independent_read(self):
        runner, grid = self.make_selector()
        runner.os_shop_get_item_to_buy_in_akashi()
        runner.os_shop_get_item_to_buy_in_akashi()
        self.store.record.assert_called_once()
        runner.device.screenshot.assert_called_once()
        runner._akashi_shop_session = {}
        runner.os_shop_get_item_to_buy_in_akashi()
        self.assertEqual(self.store.record.call_count, 2)
        self.assertEqual(runner.device.screenshot.call_count, 2)
        self.assertEqual(grid.predict.call_count, 4)

    def test_any_purchase_attempt_blocks_later_observation_even_when_purchase_fails(self):
        for success in (False, True):
            with self.subTest(success=success):
                self.store.reset_mock()
                runner, _ = self.make_selector()
                goods = runner._akashi_predict_inventory()
                item = goods[0]

                def execute(button):
                    self.assertTrue(runner._akashi_shop_session['purchase_attempted'])
                    self.assertFalse(observe_record(runner, goods, can_recheck=True))
                    return success

                runner.os_shop_buy_execute = Mock(side_effect=execute)
                count = OSShop.os_shop_buy(runner, Mock(side_effect=[item, None]))
                self.assertEqual(count, int(success))
                self.store_class.assert_not_called()

    def test_missing_config_bypasses_optional_record_hook(self):
        runner = SimpleNamespace(_akashi_shop_session={})
        self.assertFalse(observe_record(runner, [], can_recheck=True))
        self.store_class.assert_not_called()


class AkashiRecordSignatureTests(AkashiCaptureFixture):
    def test_each_recognized_attribute_change_requires_another_frame(self):
        for field, value in [('name', 'ActionPoint50'), ('amount', 3),
                             ('cost', 'PurpleCoins'), ('price', 1601)]:
            with self.subTest(field=field):
                self.store.reset_mock()
                runner, _, items = self.make_shelf()
                self.assertTrue(observe_record(runner, items, can_recheck=True))
                setattr(items[1], field, value)
                self.assertTrue(observe_record(runner, items, can_recheck=True))
                self.store.record.assert_not_called()
                self.assertFalse(observe_record(runner, items, can_recheck=False))
                self.store.record.assert_called_once()

    def test_incomplete_middle_frame_discards_earlier_candidate(self):
        runner, _, items = self.make_shelf()
        self.assertTrue(observe_record(runner, items, can_recheck=True))
        self.assertTrue(observe_record(runner, items[:-1], can_recheck=True))
        self.assertFalse(observe_record(runner, items, can_recheck=False))
        self.store.record.assert_not_called()


if __name__ == '__main__':
    unittest.main()
