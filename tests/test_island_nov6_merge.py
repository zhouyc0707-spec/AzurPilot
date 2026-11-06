"""冬季数据桥接、订单弹窗及空岗/锁岗融合的离线回归。"""

import unittest
from unittest.mock import Mock

from module.island.data import DIC_ISLAND_ITEM, DIC_ISLAND_RECIPE
from module.island.item_ids import LOCAL_TO_ITEM_ID
from module.island.order import IslandOrder, ISLAND_LEVEL_UP
from module.island.order_stock import get_business_menu_items
from module.island.production_recipe import recipe_for_local_name
from module.island.recipe_groups import SEASONAL_RECIPE_GROUPS
from tests.test_island_farm_locked_post import make_farm


class WinterBridgeTest(unittest.TestCase):
    def test_winter_products_resolve_through_local_dispatch_and_menu_reservations(self):
        groups = {
            'sugar_tangerine': 'orchard', 'green_jujube': 'orchard',
            'shiitake_chicken_soup': 'koi', 'green_jujube_porridge': 'koi',
            'water_chestnut_cake': 'eatery', 'sugar_tangerine_daifuku': 'eatery',
            'plum_blossom_sachet': 'manufacturing_crafts', 'winter_bouquet': 'manufacturing_crafts',
        }
        for name, group in groups.items():
            with self.subTest(name=name):
                item = LOCAL_TO_ITEM_ID[name]
                self.assertIn(item, DIC_ISLAND_ITEM)
                recipe = recipe_for_local_name(name)
                self.assertIn(item, DIC_ISLAND_RECIPE[recipe]['commission_product'])
                self.assertEqual(SEASONAL_RECIPE_GROUPS[recipe], group)
        self.assertIn('shiitake_chicken_soup', get_business_menu_items(601, 'winter'))
        self.assertIn('water_chestnut_cake', get_business_menu_items(603, 'winter'))
        self.assertNotIn('water_chestnut_cake', get_business_menu_items(603, 'spring'))

    def test_island_level_popup_stays_visible_during_click_cooldown(self):
        order = object.__new__(IslandOrder)
        order.appear = Mock(side_effect=lambda button, **kwargs: button is ISLAND_LEVEL_UP)
        order.appear_then_click = Mock(return_value=False)
        self.assertFalse(order._handle_popups())
        self.assertTrue(order._order_popup_visible())
        order.appear_then_click = Mock(side_effect=lambda button, **kwargs: button is ISLAND_LEVEL_UP)
        self.assertTrue(order._handle_popups())

    def test_confirmed_empty_post_remains_schedulable_without_opening_it(self):
        farm = make_farm()
        farm.post_list_is_empty.return_value = True
        farm.post_open = Mock(return_value=False)
        self.assertTrue(farm.decided_lists(Mock(), 'ISLAND_NURSERY_POST2', 'nursery', 'nursery_time_1'))
        self.assertEqual(farm.posts['ISLAND_NURSERY_POST2']['state'], 'idle')
        self.assertIsNone(farm.time_vars['nursery'][1])
        farm.post_open.assert_not_called()


if __name__ == '__main__':
    unittest.main()
