"""白熊饮品的手工季节目标在初始化和库存需求阶段保持一致。"""

from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.island.island import Island
from module.island.island_season import SEASONAL_ITEMS, SeasonConfig
from module.island.island_teahouse import (
    FIXED_SELECT_CHRYSANTHEMUM_TEA,
    FIXED_SELECT_SPRING_FLOWER_TEA,
    SEASONAL_DRINK_CONFIG,
    IslandTeahouse,
)


def make_shop(season='winter', products=(), *, seasonal=False, away='None'):
    """只替换设备初始化，保留配置读取、目录注册和父类换季逻辑。"""
    values = {
        'ChefFilter': 'WorkerJuu', 'Seasonal': seasonal, 'PostNumber': 2,
    }
    for index in range(1, 9):
        product, target = products[index - 1] if index <= len(products) else ('None', 0)
        values[f'Meal{index}'] = product
        values[f'MealNumber{index}'] = target
    data = {
        'IslandPlan': {'IslandPlan': {'Season': season}},
        'IslandTeahouse': {
            'IslandTeahouse': deepcopy(values),
            'IslandTeahouseNextTask': {'AwayCook': away},
        },
    }
    config = SimpleNamespace(
        **{f'IslandTeahouse_{key}': value for key, value in values.items()},
        IslandTeahouseNextTask_AwayCook=away,
        cross_get=lambda path: season,
        data=deepcopy(data),
    )
    original = deepcopy(vars(config))

    def init_island(shop, config, device=None, task=None):
        shop.config = config
        shop.device = Mock()
        shop.unavailable_characters = set()

    with patch.object(Island, '__init__', init_island), patch(
        'module.island.island_shop_base.get_global_season_config', side_effect=SeasonConfig,
    ):
        shop = IslandTeahouse(config)
    return shop, original


class TeahouseSeasonTargetsTests(unittest.TestCase):
    def test_winter_saved_autumn_target_is_removed_before_stock_requests(self):
        products = [('floral_fruity', 14), ('lavender_tea', 14), ('chrysanthemum_tea', 14),
                    ('sunny_honey', 14), ('strawberry_lemon', 14)]
        for seasonal in (False, True):
            with self.subTest(seasonal=seasonal), patch('module.island.island_shop_base.logger') as log:
                shop, original = make_shop(products=products, seasonal=seasonal)
                self.assertEqual(shop.post_products, products[:2] + products[3:])
                self.assertNotIn('chrysanthemum_tea', shop.name_to_config)
                self.assertTrue({4001, 4014}.isdisjoint(shop.round_stock_items()))
                self.assertEqual(vars(shop.config), original)
                self.assertEqual(shop.get_priority_production(), {})
                self.assertTrue(any('菊花茶' in call.args[0] and '对应槽位' in call.args[0]
                                    and '跳过' in call.args[0] for call in log.info.call_args_list))
                shop.device.click.assert_not_called()

    def test_winter_removes_both_slots_from_every_old_season(self):
        for old_season in ('spring', 'summer', 'autumn'):
            with self.subTest(old_season=old_season):
                products = [(name, index + 3) for index, name
                            in enumerate(SEASONAL_ITEMS[old_season]['teahouse'])]
                shop, original = make_shop(products=products + [('apple_juice', 9)])
                self.assertEqual(shop.post_products, [('apple_juice', 9)])
                self.assertTrue(set(SEASONAL_DRINK_CONFIG).isdisjoint(shop.name_to_config))
                self.assertEqual(vars(shop.config), original)

    def test_manual_cross_season_targets_use_current_slots_with_priority_disabled(self):
        for source, season in (('autumn', 'summer'), ('summer', 'autumn'),
                               ('spring', 'summer'), ('spring', 'autumn')):
            with self.subTest(source=source, season=season):
                old = SEASONAL_ITEMS[source]['teahouse']
                current = SEASONAL_ITEMS[season]['teahouse']
                shop, original = make_shop(
                    season, [(old[0], 3), ('apple_juice', 11), (old[1], 4)])
                self.assertEqual(shop.post_products, [(current[0], 3), ('apple_juice', 11),
                                                      (current[1], 4)])
                self.assertIsNone(shop.seasonal_high_priority_drink)
                self.assertEqual(shop.get_priority_production(), {})
                for name in current:
                    self.assertIs(shop.name_to_config[name]['selection'],
                                  SEASONAL_DRINK_CONFIG[name]['selection'])
                self.assertTrue(set(old).isdisjoint(shop.name_to_config))
                self.assertEqual(vars(shop.config), original)

    def test_current_manual_drink_remains_without_enabling_priority(self):
        shop, original = make_shop('autumn', [('chrysanthemum_tea', 14)])
        self.assertEqual(shop.post_products, [('chrysanthemum_tea', 14)])
        self.assertIs(shop.name_to_config['chrysanthemum_tea']['selection'],
                      SEASONAL_DRINK_CONFIG['chrysanthemum_tea']['selection'])
        self.assertNotIn('carrot_pear_juice', shop.name_to_config)
        self.assertEqual(shop.get_priority_production(), {})
        self.assertEqual(vars(shop.config), original)

    def test_unused_seasonal_candidates_do_not_expand_inventory_requests(self):
        for season in ('spring', 'summer', 'autumn', 'winter'):
            with self.subTest(season=season):
                shop, _ = make_shop(season, [('apple_juice', 7)])
                self.assertEqual(shop.post_products, [('apple_juice', 7)])
                self.assertTrue(set(SEASONAL_DRINK_CONFIG).isdisjoint(shop.name_to_config))
                self.assertTrue({4001, 4013, 4014, 4037, 4038}.isdisjoint(shop.round_stock_items()))

    def test_priority_toggle_preserves_fixed_selection_resources(self):
        cases = (
            ('spring', 'spring_flower_tea', FIXED_SELECT_SPRING_FLOWER_TEA),
            ('summer', 'watermelon_juice', FIXED_SELECT_SPRING_FLOWER_TEA),
            ('autumn', 'chrysanthemum_tea', FIXED_SELECT_CHRYSANTHEMUM_TEA),
        )
        for season, product, button in cases:
            with self.subTest(season=season):
                shop, original = make_shop(season, [('apple_juice', 7)], seasonal=True)
                self.assertIs(shop.name_to_config[product]['selection'], button)
                self.assertIs(shop.name_to_config[product]['selection_check'], button)
                self.assertEqual(shop.get_priority_production(), {product: 5})
                self.assertEqual(vars(shop.config), original)

    def test_fixed_only_drink_does_not_gain_manual_selection_when_priority_disabled(self):
        shop, original = make_shop('spring', [('spring_flower_tea', 7)])
        self.assertEqual(shop.post_products, [])
        self.assertNotIn('spring_flower_tea', shop.name_to_config)
        self.assertEqual(shop.get_priority_production(), {})
        self.assertEqual(vars(shop.config), original)

    def test_winter_away_target_cannot_reintroduce_removed_autumn_drink(self):
        shop, original = make_shop(products=[('chrysanthemum_tea', 14)], away='chrysanthemum_tea')
        shop.process_away_cook()
        self.assertEqual(shop.to_post_products, {})
        self.assertTrue({4001, 4014}.isdisjoint(shop.round_stock_items()))
        self.assertEqual(vars(shop.config), original)
        shop.device.click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
