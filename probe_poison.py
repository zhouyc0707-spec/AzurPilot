"""临时脚本：抓谁把 sys.modules 的键置成 None（用完即删）。"""
import sys
import traceback
import unittest

MODULES = [
    'tests.test_campaign_pt_continue', 'tests.test_ci_import', 'tests.test_cl1_read_cache',
    'tests.test_commission_income_record', 'tests.test_commission_planner',
    'tests.test_commission_settlement', 'tests.test_config_batch',
]
TARGET = 'tests.test_island_character_filter'


class TracingModules(dict):
    """拦截把模块标记成 None 的赋值。"""

    def __setitem__(self, key, value):
        if value is None:
            print(f'[poison] sys.modules[{key!r}] = None')
            for line in traceback.format_stack(limit=10)[:-1]:
                print('    ' + line.strip().replace('\n', ' | '))
        super().__setitem__(key, value)


# 保留原字典里的所有条目（模块对象本身不变），只换外层映射
sys.modules = TracingModules(sys.modules)

loader = unittest.defaultTestLoader
suite = unittest.TestSuite([loader.loadTestsFromName(name) for name in MODULES + [TARGET]])
result = unittest.TextTestRunner(verbosity=0).run(suite)
print(f'=== 用例 {result.testsRun} 失败 {len(result.failures)} 错误 {len(result.errors)} 跳过 {len(result.skipped)}')
