"""季节设置的生成产物、服务端校验与调度隔离。"""

import json
from pathlib import Path
import unittest

from module.api.config_service import ConfigService
from module.api.protocol import ApiError
from module.island.seasonal_targets import SEASONAL_PRODUCTION_ITEMS


ROOT = Path(__file__).resolve().parents[1]


class SeasonalConfigTests(unittest.TestCase):
    def setUp(self):
        self.args = json.loads((ROOT / 'module/config/argument/args.json').read_text(encoding='utf-8'))

    def test_generated_groups_cover_all_seasonal_products_with_zero_defaults(self):
        groups = self.args['IslandSeasonalProduction']
        template = json.loads((ROOT / 'config/template.json').read_text(encoding='utf-8'))
        self.assertNotIn('Scheduler', groups)
        self.assertNotIn('Scheduler', template['IslandSeasonalProduction'])
        for season, categories in SEASONAL_PRODUCTION_ITEMS.items():
            group = f'IslandSeason{season.capitalize()}'
            names = {name for products in categories.values() for name in products}
            self.assertEqual(set(groups[group]), names)
            self.assertEqual(len(names), 8)
            self.assertEqual(template['IslandSeasonalProduction'][group], dict.fromkeys(names, 0))

    def test_backend_rejects_invalid_quantities_for_every_generated_field(self):
        # 只使用校验方法，不构造配置服务或连接用户数据库。
        service = object.__new__(ConfigService)
        service.args = self.args
        for group, fields in self.args['IslandSeasonalProduction'].items():
            if not group.startswith('IslandSeason'):
                continue
            for field in fields:
                path = f'IslandSeasonalProduction.{group}.{field}'
                for value in (0, 1, 250):
                    self.assertEqual(service.validate(path, value), path.split('.'))
                for value in (-1, 1.5, True, 'bad', 9007199254740992):
                    with self.subTest(path=path, value=value), self.assertRaises(ApiError):
                        service.validate(path, value)

    def test_menu_and_all_translations_expose_a_settings_page(self):
        menu = json.loads((ROOT / 'module/config/argument/menu.json').read_text(encoding='utf-8'))
        self.assertIn('IslandSeasonalProduction', menu['Island']['tasks'])
        self.assertNotEqual(menu['Island']['page'], 'tool')
        for language in ('zh-CN', 'zh-MIAO', 'zh-TW', 'en-US', 'ja-JP'):
            text = json.loads((ROOT / f'module/config/i18n/{language}.json').read_text(encoding='utf-8'))
            self.assertTrue(text['Task']['IslandSeasonalProduction']['name'])
            for group, fields in self.args['IslandSeasonalProduction'].items():
                self.assertTrue(text[group]['_info']['help'])
                for field in fields:
                    self.assertNotEqual(text[group][field]['name'], f'{group}.{field}.name')


if __name__ == '__main__':
    unittest.main()
