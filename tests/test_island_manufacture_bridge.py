"""制造链桥离线回归：真实现货、在制品、耗材保护与临时关闭顺序。"""

import unittest
from unittest.mock import Mock, patch

from module.exception import GameStuckError
from module.island.island_manufacture import IslandManufacture
from module.island.manufacture_catalog import get_catalog
from module.island.production_planner import CONFIG_PREFIX
from tests.test_island_production_planner import MemoryConfig


class ManufactureBridgeTests(unittest.TestCase):
    def setUp(self):
        self.ui = IslandManufacture.__new__(IslandManufacture)
        self.ui.config = MemoryConfig(**{f'{CONFIG_PREFIX}.PlanFingerprint': 'valid'})
        self.ui._planner_protection = {}
        self.ui._manufacture_scheduled = {}
        self.ui._post_time_vars = {'POST1': 'finish'}
        self.ui.post_check_meal = {}
        self.ui.warehouse_counts = {}
        self.ui.posts = {'POST1': {'button': object(), 'status': 'idle'}}
        self.ui._prepare_planned_recipe_page = Mock(return_value=True)
        self.ui.back_to_postmanage_from_dispatch = Mock()
        self.ui.confirm_food_dispatch = Mock(return_value=((2, object()), object()))
        self.ui.finish_food_dispatch = Mock(side_effect=lambda post, name, time_var, batches, preview, at: batches)
        self.item = next(item for items in get_catalog().values() for item in items if item['name'] == 'cloth')
        self.selection = patch('module.island.manufacture_selector.select_manufacture_recipe', return_value=True)
        self.quantity = patch('module.island.manufacture_selector.set_manufacture_quantity', return_value=True)
        self.select = self.selection.start()
        self.set_quantity = self.quantity.start()
        self.addCleanup(self.selection.stop)
        self.addCleanup(self.quantity.stop)

    def stocks(self, product=0, flax=1000):
        return {3035: {'stock': product, 'cost': 0, 'display_required': 0},
                2010: {'stock': flax, 'cost': self.item['ingredients'][2010], 'display_required': self.item['ingredients'][2010]}}

    def test_confirmed_stock_pending_outputs_and_recipe_limit_are_all_used(self):
        self.ui.post_check_meal['cloth'] = 3
        self.ui._manufacture_scheduled['cloth'] = 4
        with patch('module.island.manufacture_selector.read_selected_recipe_inventory', return_value=self.stocks(2)):
            quantity = self.ui._dispatch_planned_manufacture('POST1', self.item, 50)
        self.assertEqual(quantity, self.item['production_limit'])
        self.set_quantity.assert_called_once_with(self.ui, self.item['production_limit'])
        self.assertEqual(self.ui._manufacture_scheduled['cloth'], 4 + self.item['production_limit'])

    def test_material_protection_reduces_batches_and_deducts_confirmed_actual_cost(self):
        cost = self.item['ingredients'][2010]
        self.ui._planner_protection['flax'] = 10
        with patch('module.island.manufacture_selector.read_selected_recipe_inventory', return_value=self.stocks(flax=10 + 2 * cost)):
            self.assertEqual(self.ui._dispatch_planned_manufacture('POST1', self.item, 50), 2)
        self.set_quantity.assert_called_once_with(self.ui, 2)
        self.assertEqual(self.ui.warehouse_counts['flax'], 10)

    def test_unknown_stock_and_failed_quantity_never_commit_dispatch(self):
        with patch('module.island.manufacture_selector.read_selected_recipe_inventory', return_value=None):
            with self.assertRaises(GameStuckError):
                self.ui._dispatch_planned_manufacture('POST1', self.item, 5)
        self.ui.confirm_food_dispatch.assert_not_called()
        self.set_quantity.return_value = False
        with patch('module.island.manufacture_selector.read_selected_recipe_inventory', return_value=self.stocks()):
            with self.assertRaises(GameStuckError):
                self.ui._dispatch_planned_manufacture('POST1', self.item, 5)
        self.assertEqual(self.ui._manufacture_scheduled, {})
        self.ui.confirm_food_dispatch.assert_not_called()

    def test_fully_stocked_product_does_not_use_materials_or_add_a_job(self):
        with patch('module.island.manufacture_selector.read_selected_recipe_inventory', return_value=self.stocks(8)):
            self.assertEqual(self.ui._dispatch_planned_manufacture('POST1', self.item, 5), 0)
        self.ui.confirm_food_dispatch.assert_not_called()
        self.set_quantity.assert_not_called()

    def test_unknown_working_category_is_held_without_hiding_targets(self):
        self.ui.config.values[f'{CONFIG_PREFIX}.PlannerTargets'] = '{"3035": 10}'
        category = next(category for category, items in get_catalog().items() if self.item in items)
        self.ui.manufacture = {category: {'items': [self.item]}}
        self.ui._unknown_working_categories = {category}
        self.ui.get_idle_posts_by_category = Mock(return_value=['POST1'])
        self.ui._dispatch_planned_manufacture = Mock()
        self.ui.schedule_planned_manufacture()
        self.ui._dispatch_planned_manufacture.assert_not_called()
        self.assertEqual(self.ui.config.values[f'{CONFIG_PREFIX}.PlannerTargets'], '{"3035": 10}')

    def test_unknown_post_template_cannot_be_replaced_by_another_products_icon(self):
        self.ui.shop_items = [{'name': 'cloth', 'post_action': None}]
        self.ui.appear = Mock()
        self.assertIsNone(self.ui.post_product_check())
        self.ui.appear.assert_not_called()


if __name__ == '__main__':
    unittest.main()
