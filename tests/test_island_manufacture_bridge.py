"""工坊手工顺序接入上游配置，并保留共享库存、安全扣料与未知在制边界。"""

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.island.data import DIC_ISLAND_RECIPE
from module.island.island import Island
from module.island.island_manufacture import IslandManufacture, PRODUCT_LINES, parse_product_order
from module.island.island_season import SeasonConfig
from module.island.item_ids import LOCAL_TO_ITEM_ID
from module.island.production_recipe import recipe_for_local_name
from module.island.round_inventory import IslandRoundInventory


NEW_PRODUCTS = {
    'wood_processing': ['paper', 'notebook', 'chair_desk'],
    'electronic_processing': ['ink_cartridge'],
    'industrial_production': ['charcoal_brush', 'cable'],
    'handmade': ['cloth', 'rope'],
}
POSTS = {
    'wood_processing': 'ISLAND_WOOD_PROCESSING_POST1',
    'electronic_processing': 'ISLAND_ELECTRONIC_PROCESSING_POST1',
    'industrial_production': 'ISLAND_INDUSTRIAL_POST1',
    'handmade': 'ISLAND_HANDMADE_POST1',
}


class ManufactureBridgeTests(unittest.TestCase):
    def factory(self, season='winter'):
        config = SimpleNamespace(**{f'{line}_Positions': 2 for line in PRODUCT_LINES},
                                 cross_get=Mock(side_effect=lambda path, default=None: season
                                                if path == 'IslandPlan.IslandPlan.Season' else default))

        def initialize(instance, config, device=None, **kwargs):
            instance.config = config
            instance.device = device
            instance.unavailable_characters = set()

        with patch.object(Island, '__init__', initialize), patch(
                'module.island.island_shop_base.get_global_season_config', side_effect=SeasonConfig):
            ui = IslandManufacture(config)
        ui._post_time_vars = {post: f'finish_{index}' for index, post in enumerate(ui.posts)}
        ui._prepare_recipe_page = Mock(return_value=True)
        ui.back_to_postmanage_from_dispatch = Mock(return_value=True)
        ui.select_product = Mock(return_value=True)
        ui.produce_check = Mock(return_value=False)
        ui.post_add_one = Mock()
        ui.confirm_food_dispatch = Mock(return_value=((2, timedelta(hours=1)), datetime(2026, 11, 7)))
        ui.island_round = IslandRoundInventory()
        return ui

    def test_products_order_accepts_upstream_separators_and_deduplicates(self):
        self.assertEqual(parse_product_order(' Paper > notebook, paper\nCHAIR_DESK '),
                         ['paper', 'notebook', 'chair_desk'])
        ui = self.factory()
        ui.config.WoodProcessing_Products = 'paper > chair_desk > notebook'
        self.assertEqual([item['name'] for item in ui.configured_product_list('wood_processing')],
                         ['paper', 'chair_desk', 'notebook'])

    def test_all_eight_new_products_enter_round_inventory_and_fast_dispatch(self):
        for category, products in NEW_PRODUCTS.items():
            for name in products:
                with self.subTest(product=name):
                    ui = self.factory()
                    recipe = DIC_ISLAND_RECIPE[recipe_for_local_name(name)]
                    item_id = LOCAL_TO_ITEM_ID[name]
                    ui.island_round = IslandRoundInventory({
                        **{item: cost * 4 for item, cost in recipe['commission_cost'].items()},
                        item_id: 0,
                    })
                    needed = ui.round_stock_items()
                    self.assertIn(item_id, needed)
                    self.assertTrue(set(recipe['commission_cost']) <= needed)
                    with patch('module.island.manufacture_selector.read_selected_recipe_inventory',
                               side_effect=AssertionError('不得生产页盘点')), patch(
                            'module.island.manufacture_selector.set_manufacture_quantity',
                            side_effect=AssertionError('有模板产品不得慢调量')):
                        item = ui.name_to_config[name]
                        result = ui.select_product_with_material_check(POSTS[category], [item])
                    self.assertIs(result, item)
                    ui.select_product.assert_called_once_with(item['selection'], item['selection_check'])
                    ui.post_add_one.assert_called_once_with(3)
                    for material, cost in recipe['commission_cost'].items():
                        self.assertEqual(ui.island_round.get(material), cost * 2)
                    self.assertEqual(ui.island_round.get(name), 0)
                    self.assertEqual(ui.island_round.in_production[item_id],
                                     2 * recipe['commission_product'][item_id])
                    self.assertEqual(ui.posts[POSTS[category]]['status'], 'working')

    def test_each_line_passes_explicit_order_to_actual_scheduler(self):
        methods = {'wood_processing': 'schedule_wood_processing',
                   'electronic_processing': 'schedule_electronic_processing',
                   'industrial_production': 'schedule_industrial_production',
                   'handmade': 'schedule_handmade'}
        for line, category in PRODUCT_LINES.items():
            with self.subTest(line=line):
                ui = self.factory()
                order = list(reversed(NEW_PRODUCTS[category]))
                setattr(ui.config, f'{line}_Products', ' > '.join(order))
                ui.get_idle_posts_by_category = Mock(return_value=[POSTS[category]])
                ui.select_product_with_material_check = Mock()
                getattr(ui, methods[category])()
                actual = ui.select_product_with_material_check.call_args.args[1]
                self.assertEqual([item['name'] for item in actual], order)

    def test_empty_products_preserves_file_cabinet_and_filter_defaults(self):
        ui = self.factory()
        ui.get_idle_posts_by_category = Mock(side_effect=lambda category: [POSTS[category]])
        ui.select_product_with_material_check = Mock()
        ui.schedule_wood_processing()
        ui.schedule_electronic_processing()
        self.assertEqual([[item['name'] for item in call.args[1]]
                          for call in ui.select_product_with_material_check.call_args_list],
                         [['file_cabinet'], ['filter_element']])

    def test_unknown_configured_product_does_not_silently_fall_back(self):
        ui = self.factory()
        ui.config.WoodProcessing_Products = 'unregistered_product'
        self.assertEqual(ui.configured_product_list('wood_processing'), [])

    def test_products_excludes_other_seasons_but_keeps_current_handmade(self):
        for season, current in (('spring', 'shepherd_purse'), ('summer', 'summer_bouquet'),
                                ('winter', 'winter_bouquet')):
            with self.subTest(season=season):
                ui = self.factory(season)
                ui.config.Handmade_Products = 'winter_bouquet > summer_bouquet > shepherd_purse > cloth'
                actual = ui.configured_product_list('handmade')
                self.assertEqual([item['name'] for item in actual], [current, 'cloth'])

    def test_material_shortage_skips_first_product_and_uses_next_configured_candidate(self):
        ui = self.factory()
        ui.config.WoodProcessing_Products = 'paper > notebook'
        # 纸张需要原木；手头已有纸张可继续制作记事本，不能把首项缺料当作整线不可用。
        ui.island_round = IslandRoundInventory({'paper': 8, 'notebook': 0, 'raw_timber': 0, 2801: 2})
        result = ui.select_product_with_material_check(
            POSTS['wood_processing'], ui.configured_product_list('wood_processing'))
        self.assertEqual(result['name'], 'notebook')
        self.assertTrue(ui.island_round.is_blocked('paper'))
        ui.select_product.assert_called_once_with(ui.name_to_config['notebook']['selection'],
                                                 ui.name_to_config['notebook']['selection_check'])
        self.assertEqual(ui.island_round.get('paper'), 0)
        self.assertEqual(ui.island_round.get(2801), 0)
        self.assertEqual(ui.island_round.get('notebook'), 0)

    def test_explicit_cutlery_uses_recipe_cost_not_default_iron_switch_threshold(self):
        ui = self.factory()
        ui.config.Industrial_Products = 'cutlery'
        recipe = DIC_ISLAND_RECIPE[recipe_for_local_name('cutlery')]
        stocks = {item: cost * 2 for item, cost in recipe['commission_cost'].items()}
        stocks[LOCAL_TO_ITEM_ID['cutlery']] = 0
        stocks[LOCAL_TO_ITEM_ID['iron_nail']] = 1
        ui.island_round = IslandRoundInventory(stocks)
        self.assertLess(ui.island_round.get('iron_nail'), 20)
        item = ui.name_to_config['cutlery']
        self.assertIs(ui.select_product_with_material_check(POSTS['industrial_production'], [item]), item)
        ui.post_add_one.assert_called_once_with(1)

    def test_zero_and_unknown_material_prevent_unsafe_dispatch(self):
        for unknown in (False, True):
            with self.subTest(unknown=unknown):
                ui = self.factory()
                ui.island_round = IslandRoundInventory({'cloth': 0, 'flax': 20})
                if unknown:
                    ui.island_round.observe('flax', None)
                else:
                    ui.island_round.observe('flax', 0)
                self.assertIsNone(ui.select_product_with_material_check(POSTS['handmade'],
                                                                       [ui.name_to_config['cloth']]))
                ui._prepare_recipe_page.assert_not_called()
                ui.confirm_food_dispatch.assert_not_called()

    def test_unknown_working_product_only_blocks_its_own_category(self):
        ui = self.factory()
        ui._unknown_working_categories = {'wood_processing'}
        ui.island_round = IslandRoundInventory({'cloth': 0, 'flax': 100})
        self.assertIsNone(ui.select_product_with_material_check(POSTS['wood_processing'],
                                                               [ui.name_to_config['paper']]))
        ui._prepare_recipe_page.assert_not_called()
        item = ui.name_to_config['cloth']
        self.assertIs(ui.select_product_with_material_check(POSTS['handmade'], [item]), item)

    def test_missing_post_template_never_uses_another_products_icon(self):
        ui = self.factory()
        ui.shop_items = [ui.name_to_config['cloth']]
        ui.appear = Mock()
        self.assertIsNone(ui.post_product_check())
        ui.appear.assert_not_called()


if __name__ == '__main__':
    unittest.main()
