"""委托收益记录：一次委托收获 = 一条记录 = 一张截图。

回归点：一个「获得道具」弹窗就是一次收获，必须单独成条；绝不能把多次收获
（多张截图）合并进同一条记录，也不能给一条记录塞两张截图。
"""

import unittest
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

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
        self.templates = [object()]
        self.items = []
        self.grids = None
        self.item_class = None
        self.similarity = None
        self.amount_ocr = None

    def load_template_folder(self, folder):  # noqa: ARG002 - 接口对齐
        self.templates = [object()]

    def predict(self, image, **kwargs):  # noqa: ARG002 - 接口对齐
        self.items = self.results.pop(0) if self.results else []


class CommissionIncomeRecordHarness(unittest.TestCase):
    """直接驱动 _record_commission_income，只替换识别与落盘。"""

    def _run(self, images, results, page_hits=None, screenshot_paths=None):
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
                ({"Oil": 282}, 1, ["alas/2026-09/shot_0.png"]),
                ({"Oil": 79}, 1, ["alas/2026-09/shot_1.png"]),
            ],
            self._records(db),
        )

    def test_every_record_carries_exactly_one_screenshot(self):
        stub, db = self._run(
            images=[object(), object(), object()],
            results=[[_item("Gem", 2)], [_item("Oil", 30)], [_item("Coin", 60)]],
        )

        records = self._records(db)
        self.assertEqual(3, len(records))
        for _, commission_count, screenshots in records:
            self.assertEqual(1, commission_count)
            self.assertEqual(1, len(screenshots))

    def test_invalid_frame_is_dropped_and_writes_no_record(self):
        """没通过「获取物品」页面校验的帧既不落盘也不写记录。"""
        stub, db = self._run(
            images=[object(), object()],
            results=[[_item("Oil", 282)]],
            page_hits=[True, False],
        )

        self.assertEqual([({"Oil": 282}, 1, ["alas/2026-09/shot_0.png"])], self._records(db))
        self.assertEqual(1, len(stub.saved_screenshots))

    def test_frame_without_tracked_item_writes_no_record(self):
        """只识别到未跟踪物品（如家具币）时不留空记录。"""
        stub, db = self._run(images=[object()], results=[[_item("DecorCoins", 12)]])

        self.assertEqual([], self._records(db))
        self.assertEqual([], stub.saved_screenshots)

    def test_screenshot_save_failure_still_records_items_once(self):
        """截图落盘失败时记录仍要写，且不带截图路径。"""
        stub, db = self._run(
            images=[object()],
            results=[[_item("Oil", 282)]],
            screenshot_paths=[None],
        )

        self.assertEqual([({"Oil": 282}, 1, [])], self._records(db))


class TestCommissionIncomeScreenshotInvariant(unittest.TestCase):
    """存储边界兜底：一条委托记录最多只留一张截图。"""

    def test_extra_screenshots_are_dropped_on_write(self):
        import tempfile
        from pathlib import Path

        from module.statistics.cl1_database import Cl1Database

        with tempfile.TemporaryDirectory() as tmp:
            database = Cl1Database(Path(tmp) / "cl1_data.db")
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
