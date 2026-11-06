"""按产地读取当前页的真实列表、零库存和未知保护回归。"""

import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

import numpy as np

from module.base.utils import load_image
from module.island.island import Island
from module.island.island_rancher import IslandRancher
from module.island.round_warehouse import (
    UNKNOWN, RoundWarehouseReader, WarehousePage,
    read_round_warehouse_stocks, read_warehouse_page, stocks_from_rows, warehouse_catalog,
)
from module.island_farm import assets as farm_assets
from module.island.item_ids import LOCAL_TO_ITEM_ID


FIXTURES = Path(__file__).parent / 'fixtures/island_round_warehouse'
FARM_NAMES = ('wheat', 'corn', 'rice', 'chinese_cabbage', 'potato', 'soybean', 'pasture', 'coffee_bean')
FARM_STOCKS = (50, 1482, 628, 899, 229, 2409, 914, 324)
MILL_NAMES = ('chicken_feed', 'pig_feed', 'cattle_feed', 'sheep_feed', 'wheat_flour')


def row(*cells):
    return tuple(cells) + (None,) * (6 - len(cells))


class RoundWarehouseTests(unittest.TestCase):
    def make_ranch(self):
        config = SimpleNamespace(IslandRancher_MinChicken=20, IslandRancher_MinPork=0)

        def init(task, config):
            task.config, task.device = config, Mock()

        with patch.object(Island, '__init__', init):
            return IslandRancher(config)

    def make_main(self, image=None):
        main = SimpleNamespace(device=SimpleNamespace(image=image, click=Mock(), swipe_vector=Mock(),
                                                       click_record_remove=Mock()),
                               goto_postmanage=Mock(), post_close=Mock(return_value=True),
                               warehouse_filter=Mock())
        main.match_template_color = lambda button, **kwargs: button.match_template_color(
            main.device.image, **kwargs)
        return main

    def test_real_farm_list_reads_eight_cards_in_one_quantity_batch(self):
        image = load_image(str(FIXTURES / 'farm.png'))
        detections = json.loads((FIXTURES / 'farm_ocr.json').read_text(encoding='utf-8'))
        templates = {LOCAL_TO_ITEM_ID[name]: [getattr(farm_assets, f'TEMPLATE_{name.upper()}')]
                     for name in FARM_NAMES}
        with patch('module.island.round_warehouse.Ocr') as ocr, \
                patch('module.island.round_warehouse.WarehouseQuantityDigit') as digit:
            ocr.return_value.cnocr.det.return_value = detections
            digit.return_value.ocr.return_value = list(FARM_STOCKS)
            digit.return_value.region_validity = [True] * 8
            main = self.make_main(image)
            page = read_warehouse_page(main, templates)
        self.assertTrue(page.complete)
        self.assertFalse(page.bottom_partial)
        self.assertEqual(page.rows[1][2:], (None,) * 4)
        self.assertEqual(stocks_from_rows(page.rows, templates, True),
                         dict(zip(templates, FARM_STOCKS)))
        digit.return_value.ocr.assert_called_once()
        main.device.click.assert_not_called()

    def test_real_empty_season_list_confirms_zero_even_when_product_has_no_old_icon(self):
        image = load_image(str(FIXTURES / 'coffee_empty.png'))
        detections = json.loads((FIXTURES / 'coffee_empty_ocr.json').read_text(encoding='utf-8'))
        from module.island_juu_coffee.assets import TEMPLATE_ICED_COFFEE
        templates = {3005: [TEMPLATE_ICED_COFFEE], 3006: []}
        with patch('module.island.round_warehouse.Ocr') as ocr, \
                patch('module.island.round_warehouse.WarehouseQuantityDigit') as digit:
            ocr.return_value.cnocr.det.return_value = detections
            main = self.make_main(image)
            page = read_warehouse_page(main, templates)
        self.assertTrue(page.complete)
        self.assertFalse(page.bottom_partial)
        self.assertEqual(stocks_from_rows(page.rows, templates, True), {3005: 0, 3006: 0})
        digit.assert_not_called()
        main.device.click.assert_not_called()

    def test_missing_ocr_does_not_make_an_occupied_card_empty(self):
        main = self.make_main(load_image(str(FIXTURES / 'farm.png')))
        with patch('module.island.round_warehouse.Ocr') as ocr:
            ocr.return_value.cnocr.det.return_value = []
            page = read_warehouse_page(main, {})
        self.assertFalse(page.complete)
        self.assertIn(UNKNOWN, page.rows[0])
        self.assertEqual(stocks_from_rows(page.rows, {2000}, page.complete), {})

    def test_dimmed_empty_page_cannot_confirm_zero(self):
        image = (load_image(str(FIXTURES / 'coffee_empty.png')).astype(float) * 0.8).astype(np.uint8)
        main = self.make_main(image)
        main.match_template_color = Mock(return_value=True)
        with patch('module.island.round_warehouse.Ocr') as ocr:
            ocr.return_value.cnocr.det.return_value = []
            page = read_warehouse_page(main, {})
        self.assertFalse(page.complete)

    def test_current_complete_rows_sum_separate_stacks_but_unknown_never_means_zero(self):
        rows = [row((2000, 3), (2000, 7), (2001, 5))]
        self.assertEqual(stocks_from_rows(rows, {2000, 2001, 2002}, True),
                         {2000: 10, 2001: 5, 2002: 0})
        self.assertEqual(stocks_from_rows(rows, {2000, 2001, 2002}, False), {2001: 5})
        rows = [row((2000, 3), (2000, None), (2001, 5), UNKNOWN)]
        self.assertEqual(stocks_from_rows(rows, {2000, 2001, 2002}, True), {2001: 5})

    def test_reader_filters_once_and_reads_only_current_page_without_any_swipe_or_click(self):
        main = self.make_main()
        page = WarehousePage((row((2000, 10)), row((2001, 5))), (261, 428), True)
        reader = RoundWarehouseReader(main)
        reader.stable_page = Mock(return_value=page)
        self.assertEqual(reader.read_group('farm', {2000: [object()], 2001: [object()]}, {2000, 2001}),
                         {2000: 10, 2001: 5})
        main.warehouse_filter.assert_called_once_with('farm')
        reader.stable_page.assert_called_once()
        main.device.click.assert_not_called()
        main.device.swipe_vector.assert_not_called()

    def test_bottom_partial_card_keeps_visible_value_but_cannot_confirm_missing_zero(self):
        main = self.make_main()
        page = WarehousePage((row((2000, 10)), row((2001, 5))), (261, 428), True, True)
        reader = RoundWarehouseReader(main)
        reader.stable_page = Mock(return_value=page)
        self.assertEqual(reader.read_group('farm', {2000: [object()], 2002: []}, {2000, 2002}), {2000: 10})
        main.device.swipe_vector.assert_not_called()

    def test_catalog_merges_material_origins_and_missing_icons_do_not_become_aliases(self):
        wheat = farm_assets.TEMPLATE_WHEAT
        runners = [SimpleNamespace(INVENTORY_CONFIG={'farm': {'filter': 'farm', 'items': [
            {'name': 'wheat', 'template': wheat}]}}, filter_asset='factory', shop_items=[
                {'name': 'filter_element', 'template': wheat}])]
        catalog = warehouse_catalog(runners)
        self.assertEqual(catalog['farm'][2000], [wheat])
        self.assertEqual(catalog['factory'][resolve('filter_element')], [])
        self.assertIn(2601, catalog['ranch'])
        self.assertIn(2605, catalog['ranch'])
        self.assertIn(2700, catalog['mine'])
        self.assertIn(2521, catalog['other_from'])
        self.assertIn(2522, catalog['other_from'])
        self.assertIn(2606, catalog['other_from'])

    def test_real_ranch_catalog_merges_five_mill_items_with_fish_meat_and_honey(self):
        ranch = self.make_ranch()
        catalog = warehouse_catalog([ranch])
        self.assertEqual(ranch.INVENTORY_CONFIG['mill']['filter'], 'other_from')
        self.assertEqual(tuple(item['name'] for item in ranch.INVENTORY_CONFIG['mill']['items']),
                         MILL_NAMES)
        self.assertEqual(set(catalog['other_from']), {resolve(name) for name in MILL_NAMES}
                         | {2521, 2522, 2606})
        self.assertEqual(len(catalog['other_from']), 8)
        self.assertNotIn('processed', catalog)

    def test_eight_other_origin_items_share_one_filter_and_page_read(self):
        ranch = self.make_ranch()
        main = self.make_main()
        requested = [resolve(name) for name in MILL_NAMES] + [2521, 2522, 2606]
        stocks = {item: quantity for quantity, item in enumerate(requested)}
        cells = list(stocks.items())
        page = WarehousePage((row(*cells[:6]), row(*cells[6:])), (261, 428), True)
        with patch.object(RoundWarehouseReader, 'stable_page', return_value=page) as read:
            self.assertEqual(read_round_warehouse_stocks(main, [ranch], requested), stocks)
        main.warehouse_filter.assert_called_once_with('other_from')
        read.assert_called_once_with(warehouse_catalog([ranch])['other_from'])
        self.assertEqual(main.goto_postmanage.call_count, 2)
        main.device.swipe_vector.assert_not_called()
        main.device.click.assert_not_called()

    def test_standalone_ranch_reads_original_five_mill_items_from_catalog_origin(self):
        ranch = self.make_ranch()
        images = [object(), object(), object()]
        ranch.device.screenshot.side_effect = images
        ranch.warehouse_filter = Mock()
        ranch.ocr_item_quantity = Mock(side_effect=range(10))
        ranch.warehouse_mill_ranch()
        self.assertEqual(ranch.warehouse_filter.call_args_list,
                         [call(ranch.INVENTORY_CONFIG['mill']['filter']), call('ranch'), call('farm')])
        self.assertEqual(ranch.warehouse_filter.call_args_list[0], call('other_from'))
        self.assertEqual(ranch.inventory_counts['mill'], dict(zip(MILL_NAMES, range(5))))
        expected_reads = [call(images[index], item['template'])
                          for index, category in enumerate(('mill', 'ranch', 'farm'))
                          for item in ranch.INVENTORY_CONFIG[category]['items']]
        self.assertEqual(ranch.ocr_item_quantity.call_args_list, expected_reads)
        self.assertEqual(ranch.device.screenshot.call_count, 3)

    def test_only_needed_origins_are_read_once_then_returns_to_management(self):
        main = self.make_main()
        catalog = {'farm': {2000: [object()], 2001: [object()]}, 'ranch': {2603: [object()]}}
        with patch('module.island.round_warehouse.warehouse_catalog', return_value=catalog), \
                patch.object(RoundWarehouseReader, 'read_group', return_value={2000: 5, 2001: 7}) as read:
            self.assertEqual(read_round_warehouse_stocks(main, [], ['wheat', 'corn']), {2000: 5, 2001: 7})
        read.assert_called_once_with('farm', catalog['farm'], {2000, 2001})
        self.assertEqual(main.goto_postmanage.call_count, 2)
        main.device.click.assert_not_called()

    def test_unmapped_materials_stay_unknown_without_all_source_scan(self):
        main = self.make_main()
        with patch('module.island.round_warehouse.warehouse_catalog', return_value={}), \
                patch.object(RoundWarehouseReader, 'read_group') as read:
            self.assertEqual(read_round_warehouse_stocks(main, [], [2521, 2522, 2700, 4003, 4015, 4016]), {})
        read.assert_not_called()
        main.device.swipe_vector.assert_not_called()

    def test_stability_needs_two_equal_frames_and_stop_does_not_scan(self):
        from module.config.config import TaskEnd
        main = self.make_main()
        main.loop = lambda **kwargs: iter(range(4))
        a = WarehousePage((row((2000, 3)),), (261,), True)
        b = WarehousePage((row((2000, 3)),), (264,), True)
        with patch('module.island.round_warehouse.read_warehouse_page', side_effect=[None, a, b, b]) as read:
            self.assertEqual(RoundWarehouseReader(main).stable_page({}), b)
            self.assertEqual(read.call_count, 4)
        main.config = SimpleNamespace(stop_event=SimpleNamespace(is_set=lambda: True))
        with patch('module.island.round_warehouse.read_warehouse_page') as read:
            with self.assertRaises(TaskEnd):
                RoundWarehouseReader(main).stable_page({})
            read.assert_not_called()


def resolve(name):
    return LOCAL_TO_ITEM_ID[name]


if __name__ == '__main__':
    unittest.main()
