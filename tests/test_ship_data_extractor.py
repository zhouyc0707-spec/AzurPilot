"""舰船资料提取使用临时 Lua，验证异常皮肤不会破坏合法改造别名。"""

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dev_tools.ship_data_extractor import extract_ship_data


class ShipDataExtractorTests(unittest.TestCase):
    def test_missing_or_empty_skin_group_preserves_valid_retrofits_and_ship_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'CN'
            (root / 'sharecfg').mkdir(parents=True)
            (root / 'sharecfgdata').mkdir()
            sources = {
                'sharecfg/ship_data_by_type.lua': '',
                'sharecfg/name_code.lua': '[1] = {\n name = "测试舰",\n}\n',
                'sharecfgdata/ship_data_statistics.lua': (
                    '[11] = {\n name = "测试舰",\n rarity = 5,\n}\n'
                    '[900001] = {\n name = "剧情舰",\n rarity = 6,\n}\n'
                ),
                'sharecfgdata/ship_data_template.lua': (
                    '[11] = {\n group_type = 1,\n}\n'
                    '[900001] = {\n group_type = 1,\n}\n'
                ),
                # 无法关联舰船的皮肤应在名称解析前跳过；合法皮肤仍须正常附加。
                'sharecfg/ship_skin_template.lua': (
                    '[17] = {\n name = "{namecode:999}.改",\n skin_type = 2,\n}\n'
                    '[18] = {\n name = "{namecode:999}.改",\n skin_type = 2,\n ship_group = 0,\n}\n'
                    '[19] = {\n name = "{namecode:999}.改",\n skin_type = 2,\n ship_group = "",\n}\n'
                    '[20] = {\n name = "{namecode:1}.改",\n skin_type = 2,\n ship_group = 1,\n}\n'
                ),
            }
            for relative_path, content in sources.items():
                (root / relative_path).write_text(content, encoding='utf-8')

            with patch('sys.stdout', io.StringIO()):
                data = extract_ship_data(directory)

            self.assertEqual(set(data), {'11', '900001'})
            self.assertEqual(data['11']['name']['cn'], '测试舰')
            self.assertEqual(data['11']['retrofit_names'], ['测试舰.改'])
            self.assertEqual(data['900001']['name']['cn'], '剧情舰')
            self.assertNotIn('retrofit_names', data['900001'])


if __name__ == '__main__':
    unittest.main()
