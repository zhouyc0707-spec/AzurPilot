"""离线验证仓库批量读取、旧接口降级及不同截图隔离。"""

import unittest
from unittest.mock import Mock, patch

import numpy as np

from module.exception import GameStuckError
from module.island import warehouse as warehouse_module
from module.island.warehouse import WarehouseOCR, WarehouseQuantityDigit


class CellTemplate:
    """根据格子内测试标记匹配，避免依赖真实账号截图。"""

    def __init__(self, marker):
        self.marker = marker

    def match(self, image, similarity):
        return int(image[0, 0, 0]) == self.marker


class WarehouseBatchTests(unittest.TestCase):
    def setUp(self):
        self.warehouse = WarehouseOCR()
        self.image = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.cells = [button for _, _, button in self.warehouse.warehouse_grid.generate()]
        for index, button in enumerate(self.cells, start=1):
            self.image[button.area[1], button.area[0], 0] = index
        self.page_check = patch(
            'module.island.warehouse.ISLAND_WAREHOUSE_CHECK.match_template_color', return_value=True,
        )
        self.page_check.start()
        self.addCleanup(self.page_check.stop)

    def test_one_batch_ocr_reads_found_items_and_keeps_missing_zero(self):
        templates = {'apple': CellTemplate(1), 'tea': CellTemplate(8), 'missing': CellTemplate(99)}
        with patch('module.island.warehouse.WarehouseQuantityDigit') as digit, patch(
            'module.island.warehouse.crop', wraps=warehouse_module.crop,
        ) as crop:
            digit.return_value.ocr.return_value = [7, 12]
            digit.return_value.region_validity = [True, True]
            self.assertEqual(self.warehouse.ocr_item_quantities(self.image, templates),
                             {'apple': 7, 'tea': 12, 'missing': 0})
        digit.return_value.ocr.assert_called_once_with(self.image)
        self.assertEqual(crop.call_count, 12)
        self.assertEqual(len(digit.call_args.args[0]), 2)

    def test_shared_template_quantity_area_is_ocr_once(self):
        templates = {'tea': CellTemplate(2), 'alias': CellTemplate(2)}
        with patch('module.island.warehouse.WarehouseQuantityDigit') as digit:
            digit.return_value.ocr.return_value = 9
            digit.return_value.region_validity = [True]
            self.assertEqual(self.warehouse.ocr_item_quantities(self.image, templates),
                             {'tea': 9, 'alias': 9})
        self.assertEqual(len(digit.call_args.args[0]), 1)

    def test_no_items_or_matches_never_runs_ocr(self):
        with patch('module.island.warehouse.WarehouseQuantityDigit') as digit:
            self.assertEqual(self.warehouse.ocr_item_quantities(self.image, {}), {})
            self.assertEqual(self.warehouse.ocr_item_quantities(self.image, {'missing': CellTemplate(99)}),
                             {'missing': 0})
        digit.assert_not_called()

    def test_batch_failure_uses_existing_single_item_reader(self):
        templates = {'apple': CellTemplate(1), 'tea': CellTemplate(8), 'missing': CellTemplate(99)}
        self.warehouse.ocr_item_quantity = Mock(side_effect=[4, 11])
        with patch('module.island.warehouse.WarehouseQuantityDigit') as digit:
            digit.return_value.ocr.side_effect = RuntimeError('测试批量模型失败')
            self.assertEqual(self.warehouse.ocr_item_quantities(self.image, templates),
                             {'apple': 4, 'tea': 11, 'missing': 0})
        self.assertEqual(self.warehouse.ocr_item_quantity.call_count, 2)
        self.warehouse.ocr_item_quantity.assert_any_call(self.image, templates['apple'])

    def test_invalid_batch_results_do_not_create_inventory(self):
        for invalid in ([1], [1, -1], [True, 4], [1, '500'], [1, None]):
            with self.subTest(invalid=invalid):
                self.warehouse.ocr_item_quantity = Mock(side_effect=[3, 5])
                with patch('module.island.warehouse.WarehouseQuantityDigit') as digit:
                    digit.return_value.ocr.return_value = invalid
                    self.assertEqual(self.warehouse.ocr_item_quantities(
                        self.image, {'apple': CellTemplate(1), 'tea': CellTemplate(8)}),
                        {'apple': 3, 'tea': 5})

    def test_foreign_page_and_bad_resolution_are_rejected_before_ocr(self):
        with patch('module.island.warehouse.ISLAND_WAREHOUSE_CHECK.match_template_color', return_value=False), patch(
            'module.island.warehouse.WarehouseQuantityDigit',
        ) as digit:
            with self.assertRaises(GameStuckError):
                self.warehouse.ocr_item_quantities(self.image, {'apple': CellTemplate(1)})
        digit.assert_not_called()
        with patch('module.island.warehouse.WarehouseQuantityDigit') as digit:
            with self.assertRaises(GameStuckError):
                self.warehouse.ocr_item_quantities(self.image[:360], {'apple': CellTemplate(1)})
        digit.assert_not_called()

    def test_new_image_and_new_instance_have_no_cached_stock(self):
        templates = {'apple': CellTemplate(1)}
        with patch('module.island.warehouse.WarehouseQuantityDigit') as digit:
            digit.return_value.ocr.return_value = 8
            digit.return_value.region_validity = [True]
            first = self.warehouse.ocr_item_quantities(self.image, templates)
            digit.return_value.ocr.return_value = 2
            second = self.warehouse.ocr_item_quantities(self.image.copy(), templates)
            third = WarehouseOCR().ocr_item_quantities(self.image, templates)
        self.assertEqual(first, {'apple': 8})
        self.assertEqual(second, {'apple': 2})
        self.assertEqual(third, {'apple': 2})
        self.assertEqual(digit.return_value.ocr.call_count, 3)
        self.assertEqual(set(vars(self.warehouse)), {'warehouse_grid', 'number_area_relative'})

    def test_empty_region_falls_back_individually_but_valid_zero_does_not(self):
        templates = {'empty': CellTemplate(1), 'zero': CellTemplate(2), 'positive': CellTemplate(3)}
        self.warehouse.ocr_item_quantity = Mock(return_value=6)
        with patch('module.island.warehouse.WarehouseQuantityDigit') as digit:
            digit.return_value.ocr.return_value = [0, 0, 7]
            digit.return_value.region_validity = [False, True, True]
            self.assertEqual(self.warehouse.ocr_item_quantities(self.image, templates),
                             {'empty': 6, 'zero': 0, 'positive': 7})
        self.warehouse.ocr_item_quantity.assert_called_once_with(self.image, templates['empty'])

    def test_validity_is_recorded_per_region(self):
        digit = WarehouseQuantityDigit([], alphabet='0123456789')
        self.assertEqual([digit.after_process(text) for text in ('', '0', '15')], [0, 0, 15])
        self.assertEqual(digit.region_validity, [False, True, True])

    def test_legacy_single_reader_is_unchanged(self):
        with patch('module.island.warehouse.Digit') as digit:
            digit.return_value.ocr.return_value = 17
            self.assertEqual(self.warehouse.ocr_item_quantity(self.image, CellTemplate(8)), 17)
            self.assertEqual(self.warehouse.ocr_item_quantity(self.image, CellTemplate(99)), 0)
        digit.return_value.ocr.assert_called_once_with(self.image)


if __name__ == '__main__':
    unittest.main()
