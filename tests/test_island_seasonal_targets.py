"""季节追加目标的作用范围与无效配置保护。"""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

from module.island.seasonal_targets import SEASONAL_PRODUCTION_ITEMS, seasonal_targets


class SeasonalTargetsTest(unittest.TestCase):
    def make_config(self, season='winter', targets=None):
        values = {'IslandPlan.IslandPlan.Season': season}
        for name, value in (targets or {}).items():
            values[f'IslandSeasonalProduction.IslandSeasonWinter.{name}'] = value
        return SimpleNamespace(cross_get=lambda path, default=None: values.get(path, default)), values

    def test_only_current_season_and_requested_category_are_read(self):
        config, values = self.make_config(targets={
            'shiitake_chicken_soup': 12,
            'green_jujube_porridge': 20,
            'water_chestnut_cake': 30,
        })
        values['IslandSeasonalProduction.IslandSeasonSpring.double_bamboo_shoots'] = 99
        self.assertEqual(seasonal_targets(config, 'restaurant'), {
            'shiitake_chicken_soup': 12, 'green_jujube_porridge': 20,
        })
        self.assertEqual(seasonal_targets(config, 'juu_eatery'), {'water_chestnut_cake': 30})
        self.assertEqual(seasonal_targets(config, 'teahouse'), {})
        self.assertEqual(seasonal_targets(config, 'unknown'), {})

    def test_config_order_does_not_change_production_order(self):
        config, _ = self.make_config(targets={
            'green_jujube_porridge': 20, 'shiitake_chicken_soup': 12,
        })
        self.assertEqual(list(seasonal_targets(config, 'restaurant')), [
            'shiitake_chicken_soup', 'green_jujube_porridge',
        ])

    def test_zero_and_missing_targets_do_not_override_manual_settings(self):
        config, values = self.make_config(targets={'shiitake_chicken_soup': 0})
        values['IslandRestaurant.IslandRestaurant.Meal1'] = 'shiitake_chicken_soup'
        values['IslandRestaurant.IslandRestaurant.MealNumber1'] = 40
        previous = dict(values)
        self.assertEqual(seasonal_targets(config, 'restaurant'), {})
        self.assertEqual(values, previous)

    def test_invalid_target_never_becomes_positive_demand(self):
        for value in (None, '', '  ', True, False, -1, 1.5, 1.0, float('nan'),
                      float('inf'), '1.5', '-1', '+1', 'True', '1e3', '１２', [], {}, '9' * 5000):
            with self.subTest(value_type=type(value).__name__):
                config, _ = self.make_config(targets={'shiitake_chicken_soup': value})
                self.assertEqual(seasonal_targets(config, 'restaurant'), {})

    def test_positive_integer_text_is_accepted(self):
        config, _ = self.make_config(targets={
            'shiitake_chicken_soup': ' 12 ', 'green_jujube_porridge': '0020',
        })
        self.assertEqual(seasonal_targets(config, 'restaurant'), {
            'shiitake_chicken_soup': 12, 'green_jujube_porridge': 20,
        })

    def test_unknown_season_does_not_guess_spring(self):
        for season in (None, '', 'none', 'auto', 'Winter', [], {}):
            with self.subTest(season=season):
                config, _ = self.make_config(season=season, targets={'shiitake_chicken_soup': 12})
                self.assertEqual(seasonal_targets(config, 'restaurant'), {})

    def test_missing_season_uses_schema_default(self):
        config, values = self.make_config()
        values.pop('IslandPlan.IslandPlan.Season')
        values['IslandSeasonalProduction.IslandSeasonSpring.double_bamboo_shoots'] = 25
        self.assertEqual(seasonal_targets(config, 'restaurant'), {'double_bamboo_shoots': 25})

    def test_season_change_reads_the_other_saved_group(self):
        config, values = self.make_config(targets={'shiitake_chicken_soup': 12})
        values['IslandSeasonalProduction.IslandSeasonSpring.double_bamboo_shoots'] = 25
        values['IslandPlan.IslandPlan.Season'] = 'spring'
        self.assertEqual(seasonal_targets(config, 'restaurant'), {'double_bamboo_shoots': 25})
        self.assertEqual(values['IslandSeasonalProduction.IslandSeasonWinter.shiitake_chicken_soup'], 12)

    def test_every_season_has_eight_unique_production_items(self):
        for season, groups in SEASONAL_PRODUCTION_ITEMS.items():
            with self.subTest(season=season):
                names = [name for items in groups.values() for name in items]
                self.assertEqual(len(names), 8)
                self.assertEqual(len(set(names)), 8)

    def test_catalog_matches_existing_execution_seasons(self):
        # 不导入游戏及日志模块；同时防止配置目录与执行季节归属漂移。
        source = Path(__file__).resolve().parents[1] / 'module/island/island_season.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        node = next(node for node in tree.body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == 'SEASONAL_ITEMS'
                            for target in node.targets))
        existing = ast.literal_eval(node.value)
        expected = {season: {category: tuple(names) for category, names in groups.items() if names}
                    for season, groups in existing.items()}
        self.assertEqual(SEASONAL_PRODUCTION_ITEMS, expected)


if __name__ == '__main__':
    unittest.main()
