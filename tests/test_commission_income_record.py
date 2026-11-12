"""委托收益记录按次保存，含钻石的收获附带截图。

回归点：一个「获得道具」弹窗就是一次收获，必须单独成条；绝不能把多次收获
（多张截图）合并进同一条记录，也不能给一条记录塞两张截图。
"""

import unittest
from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import numpy as np

# WebUI 测试会往 sys.modules 注入假的 PIL 模块；同进程混跑时先摘掉它，
# 否则下面导入真实图像库的模块会报 ImportError
from module.webui.fake_pil_module import remove_fake_pil_module

remove_fake_pil_module()

from module.commission.commission import RewardCommission  # noqa: E402


def _item(name, amount):
    """构造识别结果里的物品对象。"""
    return SimpleNamespace(name=name, amount=amount, is_known_item=lambda: True)


class _RecognitionStub:
    """替身识别网格：按顺序返回预设识别结果，跳过模板匹配与数量 OCR。"""

    def __init__(self, results):
        self.results = list(results)
        self.templates = {'Oil': object()}
        self.colors = {'Oil': (1, 2, 3)}
        self.templates_hit = {'Oil': 0}
        self.next_template_index = 1
        self.items = []
        self.grids = None
        self.item_class = None
        self.similarity = None
        self.amount_ocr = None

    def load_template_folder(self, folder):  # noqa: ARG002 - 接口对齐
        self.templates = {'Oil': object()}

    def predict(self, image, **kwargs):  # noqa: ARG002 - 接口对齐
        self.items = self.results.pop(0) if self.results else []


class CommissionIncomeRecordHarness(unittest.TestCase):
    """直接驱动 _record_commission_income，只替换识别与落盘。"""

    def _run(self, images, results, page_hits=None, screenshot_paths=None, names=None):
        """
        Args:
            images: 本次领取收集到的截图列表。
            results: 每次 predict 返回的识别结果（按顺序消费）。
            page_hits: 每张截图是否命中「获取物品」页面的判定结果，默认全部命中。
            screenshot_paths: 落盘函数依次返回的路径。

        Returns:
            (stub, cl1_db)：方法对象与被记录的数据库替身。
        """
        grid = _RecognitionStub(results)
        paths = list(
            screenshot_paths
            if screenshot_paths is not None
            else [f"alas/2026-09/shot_{i}.png" for i in range(len(images))]
        )
        saved = []

        def save_screenshot(image, instance):
            saved.append((image, instance))
            return paths.pop(0) if paths else None

        stub = SimpleNamespace(
            config=SimpleNamespace(
                config_name="alas",
                Commission_CommissionNotifyReward=False,
            ),
            _commission_reward_images=list(images),
            _commission_reward_names={id(image): name for image, name in zip(images, names or [])},
            _save_commission_reward_screenshot=save_screenshot,
        )
        stub.saved_screenshots = saved

        # 上游把收入流程拆成识别 / 持久化 / 通知三个方法：直接用类方法驱动替身时，
        # 需要把它们绑到替身上（与 tests/test_commission_settlement.py 同做法），
        # 否则 _record_commission_income 会在替身上找不到这三个方法而整体异常。
        for name in (
            '_recognize_commission_income',
            '_persist_commission_income',
            '_notify_commission_income',
        ):
            setattr(stub, name, MethodType(getattr(RewardCommission, name), stub))
        # 含钻石的用例会走时长推断；它是静态方法，直接取函数即可（不能再绑 self）
        stub._guess_gem_duration = RewardCommission._guess_gem_duration

        hits = list(page_hits) if page_hits is not None else [True] * len(images)

        db = Mock()
        info_bar = Mock()
        info_bar.appear_on.return_value = False
        get_items_1 = Mock()
        get_items_1.match_template_color.side_effect = hits
        get_items_2 = Mock()
        get_items_2.match_template_color.return_value = False
        get_items_3 = Mock()
        get_items_3.match_template_color.return_value = False

        with (
            patch("module.statistics.item.ItemGrid", return_value=grid),
            patch("module.statistics.item.Item", object),
            patch("module.statistics.get_items.GetItemsStatistics", return_value=Mock()),
            patch("module.statistics.get_items.ITEM_GRIDS_1_ODD", object()),
            patch("module.statistics.get_items.ITEM_GRIDS_1_EVEN", object()),
            patch("module.statistics.get_items.ITEM_GRIDS_2", object()),
            patch("module.statistics.get_items.ITEM_GRIDS_3", object()),
            patch("module.statistics.cl1_database.db", db),
            patch("module.combat.assets.GET_ITEMS_1", get_items_1),
            patch("module.combat.assets.GET_ITEMS_2", get_items_2),
            patch("module.combat.assets.GET_ITEMS_3", get_items_3),
            patch("module.handler.assets.INFO_BAR_1", info_bar),
        ):
            RewardCommission._record_commission_income(stub)

        return stub, db

    @staticmethod
    def _records(db):
        """把 add_commission_income 的调用整理成 (items, commission_count, screenshots)。"""
        return [
            (
                call.args[1],
                call.kwargs["commission_count"],
                call.kwargs["screenshots"],
            )
            for call in db.add_commission_income.call_args_list
        ]

    def test_two_rewards_write_two_records_not_one_merged_record(self):
        """两个弹窗 = 两次收获 = 两条记录，各自带自己的那一张截图。"""
        stub, db = self._run(
            images=[object(), object()],
            results=[[_item("Oil", 282), _item("DecorCoins", 12)], [_item("Oil", 79)]],
        )

        self.assertEqual(
            [
                ({"Oil": 282}, 1, []),
                ({"Oil": 79}, 1, []),
            ],
            self._records(db),
        )

    def test_only_gem_harvest_carries_screenshot(self):
        stub, db = self._run(
            images=[object(), object(), object()],
            results=[[_item("Gem", 2)], [_item("Oil", 30)], [_item("Coin", 60)]],
        )

        records = self._records(db)
        self.assertEqual(3, len(records))
        self.assertEqual([len(row[2]) for row in records], [1, 0, 0])
        for _, commission_count, _ in records:
            self.assertEqual(1, commission_count)

    def test_each_harvest_keeps_its_confirmed_name(self):
        _, db = self._run(
            images=[object(), object()], results=[[_item('Gem', 12)], [_item('Oil', 79)]],
            names=['BIW要员护卫', None],
        )
        self.assertEqual([call.kwargs['commission_name'] for call in db.add_commission_income.call_args_list],
                         ['BIW要员护卫', None])

    def test_invalid_frame_is_dropped_and_writes_no_record(self):
        """没通过「获取物品」页面校验的帧既不落盘也不写记录。"""
        stub, db = self._run(
            images=[object(), object()],
            results=[[_item("Oil", 282)]],
            page_hits=[True, False],
        )

        self.assertEqual([({"Oil": 282}, 1, [])], self._records(db))
        self.assertEqual([], stub.saved_screenshots)

    def test_frame_without_tracked_item_writes_no_record(self):
        """只识别到未跟踪物品（如家具币）时不留空记录。"""
        stub, db = self._run(images=[object()], results=[[_item("DecorCoins", 12)]])

        self.assertEqual([], self._records(db))
        self.assertEqual([], stub.saved_screenshots)

    def test_screenshot_save_failure_still_records_items_once(self):
        """截图落盘失败时记录仍要写，且不带截图路径。"""
        stub, db = self._run(
            images=[object()],
            results=[[_item("Gem", 12)]],
            screenshot_paths=[None],
        )

        self.assertEqual([({"Gem": 12}, 1, [])], self._records(db))

    def test_uncertain_quantity_is_archived_without_an_empty_record(self):
        image = object()
        stub, db = self._run(images=[image], results=[[_item("Oil", 0)]])
        self.assertEqual([], self._records(db))
        self.assertEqual([(image, "alas")], stub.saved_screenshots)

    def test_bad_quantity_does_not_merge_or_lose_another_harvest(self):
        bad, good = object(), object()
        stub, db = self._run(images=[bad, good], results=[[_item("Oil", 0)], [_item("Oil", 79)]])
        self.assertEqual([({"Oil": 79}, 1, [])], self._records(db))
        self.assertEqual([(bad, "alas")], stub.saved_screenshots)


class TestCommissionIncomeNameCollection(unittest.TestCase):
    """通过真实领取状态循环核对完成页、奖励页与名称的关联。"""

    def test_name_reader_uses_existing_catalog_and_server_alias(self):
        from module.statistics.commission_drop import read_commission_name

        with patch('module.ocr.ocr.Ocr') as ocr:
            ocr.return_value.ocr.return_value = 'BIW巡视护卫'
            self.assertEqual(read_commission_name(object(), 'cn_ios'), 'BIW巡视护卫')
        self.assertEqual(ocr.call_args.kwargs['lang'], 'cnocr')

    def test_name_reader_rejects_bad_roman_ocr_and_accepts_stable_roman(self):
        from module.statistics.commission_drop import read_commission_name

        with patch('module.ocr.ocr.Ocr') as ocr:
            ocr.return_value.ocr.return_value = '-----------中型油田开发上------------'
            with self.assertRaises(ValueError):
                read_commission_name(object(), 'cn')
            for raw in ('中型油田开发I', '中型油田开发Ⅰ'):
                ocr.return_value.ocr.return_value = raw
                self.assertEqual(read_commission_name(object(), 'cn'), '中型油田开发I')

    def collect(self, kinds, names, stable_frames=None, blocked_exp_frames=(),
                capture_drop=False, trace=None, extra_exp_frames=()):
        from module.commission import commission as module
        from module.statistics.commission_drop import read_commission_name

        images = [np.full((1, 1, 3), index, dtype=np.uint8) for index in range(len(kinds))]
        handler = MagicMock()
        handler.config = SimpleNamespace(
            config_name='test', SERVER='cn', DropRecord_CommissionRecord='save' if capture_drop else 'do_not',
            DropRecord_CloudUpload=False, Commission_DetectShipDrop=False,
        )
        drop = Mock() if capture_drop else None
        handler.stat.new.return_value.__enter__.return_value = drop
        handler.device.image = images[0]
        position = 0
        events = trace if trace is not None else []

        def screenshot():
            nonlocal position
            position += 1
            events.append(('frame', position))
            handler.device.image = images[position]

        def frame_kind(image):
            return kinds[int(image[0, 0, 0])]

        def read_name(image, server):
            self.assertEqual(server, 'cn')
            events.append(('ocr', int(image[0, 0, 0])))
            ocr.return_value.ocr.return_value = names.get(int(image[0, 0, 0]), '')
            return read_commission_name(image, server)

        def stable_frame(**kwargs):
            stable = (stable_frames or {}).get(position)
            if stable is not None:
                handler.device.image = np.full((1, 1, 3), stable, dtype=np.uint8)

        def appear(button, **kwargs):
            kind = frame_kind(handler.device.image)
            return ((kind in ('done', 'perfect', 'exp') and button is module.EXP_INFO_S_REWARD
                     and position not in blocked_exp_frames)
                    or (position in extra_exp_frames and button is module.EXP_INFO_S_REWARD)
                    or (kind == 'reward' and button is module.GET_ITEMS_1))

        collected = []

        def record():
            events.append(('record', position))
            collected.extend(handler._commission_reward_names.get(id(image))
                             for image in handler._commission_reward_images)
            return True

        handler.device.screenshot.side_effect = screenshot
        handler.device.click.side_effect = lambda button: events.append(('click', position))
        handler._handle_research_genre_t_update.side_effect = lambda count: events.append(('completed', count))
        if drop is not None:
            drop.add.side_effect = lambda image: events.append(('drop', int(image[0, 0, 0])))
        handler.ui_page_appear.side_effect = lambda *args, **kwargs: frame_kind(handler.device.image) == 'exit'
        handler.appear.side_effect = appear
        handler.appear_then_click.return_value = False
        handler.ui_main_appear_then_click.return_value = False
        handler.ui_additional.return_value = False
        handler._record_commission_income.side_effect = record
        handler.ensure_no_info_bar.side_effect = stable_frame
        with (
            patch.object(module, 'Timer', side_effect=lambda *args: Mock(reached=Mock(return_value=True))),
            patch('module.statistics.cl1_database.db'),
            patch('module.ocr.ocr.Ocr') as ocr,
            patch('module.statistics.commission_drop.classify_frame', side_effect=frame_kind),
            patch('module.statistics.commission_drop.read_commission_name', side_effect=read_name) as name_reader,
        ):
            self.assertTrue(RewardCommission._commission_receive(handler))
        return collected, name_reader.call_count

    def test_same_name_consecutive_commissions_are_each_collected_without_upload(self):
        result, reads = self.collect(
            ['done', 'reward', 'reward', 'done', 'reward', 'reward', 'exit'],
            {0: 'BIW要员护卫', 3: 'BIW要员护卫'},
        )
        self.assertEqual(result, ['BIW要员护卫', 'BIW要员护卫'])
        self.assertEqual(reads, 4)

    def test_bad_initial_name_is_rechecked_from_stable_frame_before_click(self):
        result, reads = self.collect(
            ['done', 'reward', 'reward', 'exit'],
            {0: '-----------中型油田开发上------------', 127: '中型油田开发Ⅰ'},
            stable_frames={0: 127},
        )
        self.assertEqual(result, ['中型油田开发I'])
        self.assertEqual(reads, 2)

    def test_done_to_exp_keeps_confirmed_name_when_stable_read_fails(self):
        result, _ = self.collect(
            ['done', 'exp', 'reward', 'reward', 'exit'],
            {0: 'NYB要员护卫', 127: '无法确认'}, stable_frames={1: 127}, blocked_exp_frames={0},
        )
        self.assertEqual(result, ['NYB要员护卫'])

    def test_different_valid_initial_and_stable_names_are_unknown(self):
        result, _ = self.collect(
            ['done', 'reward', 'reward', 'reward', 'exit'],
            {0: 'BIW要员护卫', 127: 'NYB要员护卫'}, stable_frames={0: 127},
        )
        self.assertEqual(result, [None])

    def test_conflict_requires_matching_late_frames_and_consumes_exp_once(self):
        trace = []
        result, reads = self.collect(
            ['done', 'exp', 'reward', 'reward', 'exit'],
            {0: '日常资源开发IV', 127: '日常资源开发V', 1: '日常资源开发V'},
            stable_frames={0: 127}, capture_drop=True, trace=trace,
        )
        self.assertEqual(result, ['日常资源开发V'])
        self.assertEqual(reads, 3)
        self.assertLess(trace.index(('frame', 1)), trace.index(('ocr', 1)))
        self.assertEqual([item for item in trace if item[0] == 'drop'], [('drop', 1), ('drop', 3)])
        self.assertEqual([item for item in trace if item[0] == 'click'], [('click', 1), ('click', 3)])
        self.assertEqual([item for item in trace if item[0] == 'completed'], [('completed', 1)])
        self.assertEqual(len([item for item in trace if item[0] == 'record']), 1)

    def test_initial_majority_does_not_override_disagreeing_late_frames(self):
        result, reads = self.collect(
            ['done', 'exp', 'reward', 'reward', 'exit'],
            {0: '日常资源开发IV', 127: '日常资源开发V', 1: '日常资源开发IV'},
            stable_frames={0: 127},
        )
        self.assertEqual(result, [None])
        self.assertEqual(reads, 3)

    def test_conflict_review_error_keeps_unknown_reward_once(self):
        trace = []
        result, reads = self.collect(
            ['done', 'exp', 'reward', 'reward', 'exit'],
            {0: '日常资源开发IV', 127: '日常资源开发V'},
            stable_frames={0: 127}, trace=trace,
        )
        self.assertEqual(result, [None])
        self.assertEqual(reads, 3)
        self.assertEqual([item for item in trace if item[0] == 'completed'], [('completed', 1)])
        self.assertEqual(len([item for item in trace if item[0] == 'record']), 1)

    def test_conflict_review_cannot_read_a_reward_as_completion(self):
        trace = []
        result, reads = self.collect(
            ['done', 'reward', 'reward', 'reward', 'exit'],
            {0: '日常资源开发IV', 127: '日常资源开发V', 1: '日常资源开发V'},
            stable_frames={0: 127}, trace=trace, extra_exp_frames={1},
        )
        self.assertEqual(result, [None])
        self.assertEqual(reads, 2)
        self.assertEqual([item for item in trace if item[0] == 'completed'], [('completed', 0)])
        self.assertEqual([item for item in trace if item[0] == 'click'], [('click', 3)])

    def test_conflict_review_ship_boundary_cannot_leak_identity(self):
        result, reads = self.collect(
            ['done', 'ship', 'exp', 'reward', 'reward', 'exit'],
            {0: '日常资源开发IV', 127: '日常资源开发V', 1: '日常资源开发V'},
            stable_frames={0: 127},
        )
        self.assertEqual(result, [None])
        self.assertEqual(reads, 3)

    def test_conflict_does_not_flush_previous_reward_before_review(self):
        trace = []
        result, reads = self.collect(
            ['done', 'reward', 'reward', 'done', 'exp', 'reward', 'reward', 'exit'],
            {0: 'NYB要员护卫', 3: '日常资源开发IV', 127: '日常资源开发V', 4: '日常资源开发V'},
            stable_frames={3: 127}, trace=trace,
        )
        self.assertEqual(result, ['NYB要员护卫', '日常资源开发V'])
        self.assertEqual(reads, 5)
        self.assertEqual([item for item in trace if item[0] == 'record'], [('record', 4), ('record', 7)])

    def test_conflict_review_exit_preserves_previous_reward_only(self):
        trace = []
        result, _ = self.collect(
            ['done', 'reward', 'reward', 'done', 'exit'],
            {0: 'NYB要员护卫', 3: '日常资源开发IV', 127: '日常资源开发V'},
            stable_frames={3: 127}, capture_drop=True, trace=trace,
        )
        self.assertEqual(result, ['NYB要员护卫'])
        self.assertEqual([item for item in trace if item[0] == 'drop'], [('drop', 0), ('drop', 2)])
        self.assertEqual([item for item in trace if item[0] == 'completed'], [('completed', 1)])

    def test_next_exp_without_intermediate_reward_cannot_reuse_previous_name(self):
        result, _ = self.collect(
            ['done', 'exp', 'reward', 'reward', 'exit'], {0: 'NYB要员护卫'},
        )
        self.assertEqual(result, [None])

    def test_residual_completed_frame_does_not_restore_consumed_candidate(self):
        result, reads = self.collect(
            ['done', 'done', 'exp', 'reward', 'reward', 'exit'],
            {0: 'NYB要员护卫', 1: 'NYB要员护卫'}, blocked_exp_frames={1},
        )
        self.assertEqual(result, [None])
        self.assertEqual(reads, 3)

    def test_next_exp_after_reward_cannot_reuse_previous_name(self):
        result, _ = self.collect(
            ['done', 'reward', 'reward', 'exp', 'reward', 'reward', 'exit'], {0: 'NYB要员护卫'},
        )
        self.assertEqual(result, ['NYB要员护卫', None])

    def test_unknown_or_missing_status_keeps_name_unknown(self):
        for kinds in (['done', 'reward', 'reward', 'exit'], ['reward', 'reward', 'exit']):
            with self.subTest(kinds=kinds):
                result, _ = self.collect(kinds, {})
                self.assertEqual(result, [None])

    def test_multiple_reward_pages_without_boundary_have_unknown_names(self):
        result, _ = self.collect(
            ['done', 'reward', 'reward', 'reward', 'reward', 'exit'], {0: 'BIW要员护卫'})
        self.assertEqual(result, [None, None])


class TestCommissionIncomeScreenshotInvariant(unittest.TestCase):
    """存储边界兜底：一条委托记录最多只留一张截图。"""

    def test_extra_screenshots_are_dropped_on_write(self):
        import tempfile
        from pathlib import Path

        from module.statistics.cl1_database import Cl1Database
        from tests.opsi_test_support import install_vault

        with tempfile.TemporaryDirectory() as tmp:
            install_vault(self, tmp)
            database = Cl1Database(Path(tmp) / "config" / "cl1_data.db")
            database.add_commission_income(
                "alas",
                {"Oil": 282},
                commission_count=1,
                screenshots=["alas/2026-09/a_0.png", "alas/2026-09/a_1.png"],
            )
            entries = database.get_commission_income("alas")

        self.assertEqual(1, len(entries))
        self.assertEqual(["alas/2026-09/a_0.png"], entries[0]["screenshots"])


if __name__ == "__main__":
    unittest.main()
