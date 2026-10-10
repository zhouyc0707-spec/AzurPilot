"""岛屿计划「一键暂停 / 恢复」的回归测试。

需求：岛屿计划全局配置里一键关闭该组下所有**已启用**的任务，并记住是哪几个被这次
操作关掉的；下次再点一下恢复。恢复只处理**仍处于关闭状态**的那些 —— 中途被手动打开
过的任务保持不动（用户 2026-10-01 确认的语义）。

`IslandPlan` 现在包含可调度的走位校验；启用时同样参与一键开关。兼容早期只有
季节与时间对齐、尚无 Scheduler 的配置，暂停或恢复不改写这些全局参数。
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import module.api.island_suspend as island_suspend
from module.api.island_suspend import IslandSuspendService, read_state

SCHEDULABLE = ['IslandPlan', 'IslandBusiness', 'IslandFarm', 'IslandRancher', 'IslandRestaurant']


def island_config(enabled=()):
    """构造包含全局参数、走位校验和日常任务的岛屿配置。"""
    data = {
        name: {'Scheduler': {'Enable': name in enabled, 'NextRun': '2026-10-01 08:00:00'}}
        for name in SCHEDULABLE
    }
    data['IslandPlan'].update({
        'IslandPlan': {'Season': 'spring', 'TaskAlignment': 'hour'},
        'IslandProductionPlanner': {'Enabled': True, 'ProductionTargets': '{"Rice": 10}'},
        'IslandWalk': {'DailyLisha': 'up 3000', 'DailyLishaEnable': True},
        'Storage': {'Keep': {}},
    })
    data['Alas'] = {'Scheduler': {'Enable': True}}
    return data


class FakeConfigs:
    """只实现服务用到的 read / patch，记录每次修改。"""

    def __init__(self, data):
        self.data = data
        self.patched = []

    def read(self, name):
        return copy.deepcopy(self.data), 'rev'

    def patch(self, name, revision, changes):
        for change in changes:
            task, group, arg = change.path.split('.')
            self.data[task][group][arg] = change.value
            self.patched.append((change.path, change.value))
        return self.data


class IslandSuspendTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'config').mkdir()
        patcher = patch.object(island_suspend, 'filepath_config', lambda name: str(root / 'config' / f'{name}.json'))
        patcher.start()
        self.addCleanup(patcher.stop)

    def build(self, **kwargs):
        configs = FakeConfigs(island_config(**kwargs))
        return IslandSuspendService(configs), configs

    def test_suspend_records_and_disables_enabled_tasks(self):
        service, configs = self.build(enabled=['IslandFarm', 'IslandBusiness'])

        state = service.toggle('alas')

        self.assertEqual(sorted(state['suspended']), ['IslandBusiness', 'IslandFarm'])
        self.assertEqual(state['enabledCount'], 0)
        self.assertEqual(
            sorted(configs.patched),
            [('IslandBusiness.Scheduler.Enable', False), ('IslandFarm.Scheduler.Enable', False)],
        )
        self.assertEqual(sorted(read_state('alas')['suspended']), ['IslandBusiness', 'IslandFarm'])
        # 未启用的任务与非岛屿任务不受影响；默认关闭的走位校验不参与
        self.assertFalse(configs.data['IslandRancher']['Scheduler']['Enable'])
        self.assertTrue(configs.data['Alas']['Scheduler']['Enable'])
        self.assertNotIn('IslandPlan', state['suspended'])
        self.assertEqual(configs.data['IslandPlan']['IslandPlan']['Season'], 'spring')
        self.assertEqual(configs.data['IslandPlan']['IslandPlan']['TaskAlignment'], 'hour')

    def test_restore_only_reenables_still_disabled(self):
        service, configs = self.build(enabled=['IslandFarm', 'IslandBusiness'])
        service.toggle('alas')
        # 用户中途手动打开了 IslandBusiness
        configs.data['IslandBusiness']['Scheduler']['Enable'] = True
        configs.patched.clear()

        state = service.toggle('alas')

        self.assertEqual(configs.patched, [('IslandFarm.Scheduler.Enable', True)])
        self.assertEqual(state['suspendedCount'], 0)
        self.assertEqual(state['enabledCount'], 2)
        self.assertEqual(read_state('alas')['suspended'], [])

    def test_suspend_with_nothing_enabled_keeps_record_empty(self):
        service, configs = self.build(enabled=[])

        state = service.toggle('alas')

        self.assertEqual(state['suspendedCount'], 0)
        self.assertEqual(configs.patched, [])
        self.assertEqual(state['enabledCount'], 0)

    def test_state_reports_counts_without_mutating(self):
        service, configs = self.build(enabled=['IslandBusiness'])

        state = service.state('alas')

        # 走位校验也属于可调度任务，但默认关闭
        self.assertEqual(state['total'], len(SCHEDULABLE))
        self.assertEqual(state['enabledCount'], 1)
        self.assertEqual(state['suspendedCount'], 0)
        self.assertEqual(configs.patched, [])

    def test_next_run_is_left_untouched(self):
        """只改 Enable，调度时间保持原样，恢复后按原计划继续。"""
        service, configs = self.build(enabled=['IslandFarm'])
        service.toggle('alas')
        self.assertEqual(configs.data['IslandFarm']['Scheduler']['NextRun'], '2026-10-01 08:00:00')
        service.toggle('alas')
        self.assertEqual(configs.data['IslandFarm']['Scheduler']['NextRun'], '2026-10-01 08:00:00')
        self.assertTrue(configs.data['IslandFarm']['Scheduler']['Enable'])

    def test_enabled_walk_verification_is_suspended_and_restored(self):
        """只切换调度开关，不改变路线、生产规划、季节和时间对齐。"""
        service, configs = self.build(enabled=['IslandPlan', 'IslandFarm'])
        original = copy.deepcopy(configs.data['IslandPlan'])

        state = service.toggle('alas')

        self.assertEqual(state['suspended'], ['IslandFarm', 'IslandPlan'])
        paused = copy.deepcopy(configs.data['IslandPlan'])
        paused['Scheduler']['Enable'] = True
        self.assertEqual(paused, original)
        restored = service.toggle('alas')
        self.assertEqual(restored['enabledCount'], 2)
        self.assertEqual(configs.data['IslandPlan'], original)

    def test_legacy_global_config_without_scheduler_is_not_toggled(self):
        service, configs = self.build(enabled=['IslandFarm'])
        del configs.data['IslandPlan']['Scheduler']
        original = copy.deepcopy(configs.data['IslandPlan'])

        state = service.toggle('alas')
        self.assertEqual(state['total'], len(SCHEDULABLE) - 1)
        self.assertNotIn('IslandPlan', state['suspended'])
        service.toggle('alas')
        self.assertEqual(configs.data['IslandPlan'], original)


class IslandSuspendIntegrationTest(unittest.TestCase):
    """走真实 ConfigService：真实校验、真实原子写盘，确认端到端可用。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'config').mkdir()
        patcher = patch.object(island_suspend, 'filepath_config',
                               lambda name: str(self.root / 'config' / f'{name}.json'))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_suspend_and_restore_through_real_config_service(self):
        from module.api.config_service import ConfigService

        instance = 'islandtest'
        template = Path('config/template.json')
        if not template.is_file():
            self.skipTest('缺少 config/template.json，跳过真实配置服务用例')
        # 真实配置服务会按 args.json 校验整份配置，这里以模板为底再改岛屿任务的启用状态
        data = json.loads(template.read_text(encoding='utf-8'))
        for task, enabled in (('IslandPlan', True), ('IslandFarm', True),
                              ('IslandRestaurant', True), ('IslandRancher', False)):
            data.setdefault(task, {}).setdefault('Scheduler', {})['Enable'] = enabled
            data[task]['Scheduler']['NextRun'] = '2026-10-01 08:00:00'
        data.setdefault('IslandPlan', {}).setdefault('IslandPlan', {})['Season'] = 'winter'
        data['IslandPlan']['IslandPlan']['TaskAlignment'] = 'hour'
        (self.root / 'config' / f'{instance}.json').write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

        # 参数元数据仍从真实仓库加载（root 不变），只把配置目录指向临时目录，
        # 这样不会碰到用户真实的 config/。
        configs = ConfigService()
        configs.directory = self.root / 'config'
        service = IslandSuspendService(configs)

        state = service.toggle(instance)
        self.assertEqual(sorted(state['suspended']), ['IslandFarm', 'IslandPlan', 'IslandRestaurant'])

        saved = json.loads((self.root / 'config' / f'{instance}.json').read_text(encoding='utf-8'))
        self.assertFalse(saved['IslandFarm']['Scheduler']['Enable'])
        self.assertFalse(saved['IslandRestaurant']['Scheduler']['Enable'])
        self.assertFalse(saved['IslandPlan']['Scheduler']['Enable'])
        # 原本就关闭的任务不在记录里，恢复时也不会被打开
        self.assertFalse(saved['IslandRancher']['Scheduler']['Enable'])
        # 调度时间与全局配置（季节、时间对齐）原样保留
        self.assertEqual(saved['IslandFarm']['Scheduler']['NextRun'], '2026-10-01 08:00:00')
        self.assertEqual(saved['IslandPlan']['IslandPlan']['Season'], 'winter')
        self.assertEqual(saved['IslandPlan']['IslandPlan']['TaskAlignment'], 'hour')

        restored = service.toggle(instance)
        self.assertEqual(restored['suspendedCount'], 0)
        self.assertEqual(restored['enabledCount'], 3)
        saved = json.loads((self.root / 'config' / f'{instance}.json').read_text(encoding='utf-8'))
        self.assertTrue(saved['IslandFarm']['Scheduler']['Enable'])
        self.assertTrue(saved['IslandRestaurant']['Scheduler']['Enable'])
        self.assertTrue(saved['IslandPlan']['Scheduler']['Enable'])
        self.assertFalse(saved['IslandRancher']['Scheduler']['Enable'])
        self.assertEqual(saved['IslandPlan']['IslandPlan']['TaskAlignment'], 'hour')


if __name__ == '__main__':
    unittest.main()
