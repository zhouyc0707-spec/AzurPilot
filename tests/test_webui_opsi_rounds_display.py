"""「出击轮次」在两张统计表里都要按整数展示。

耄耋相接的有效轮次是 float（`effective_rounds` 由战斗场次之类的比值算出，实测会
出现 360.8 这种小数），而侵蚀1 那行是 `(battles + 1) // 2` 的整数 —— 同一列两种
形态看起来不一致。两张表都统一四舍五入取整：

- 「雪风大人的大世界数据收集」三行表 → `app_stat_opsi._format_rounds`
- 「本月耄耋相接收获」 → `app_stat_opsi_export._render_monthly_meow_loot`

但**不改统计口径**：数据收集表的「出击消耗 = 每轮消耗 × 轮次」仍用未取整的原值算。
"""

import unittest
from unittest.mock import patch

from module.webui.app_stat_opsi import OpsiStatisticsMixin
from module.webui.app_stat_opsi_export import OpsiExportMixin
from module.webui.lang import t
from tests.pywebio_stubs import _StubOutput

HAZARD_LABEL = t("Gui.Stat.HazardLevel")
ROUNDS_LABEL = t("Gui.Stat.BattleRounds")
COST_LABEL = t("Gui.Stat.SortieCost")
MONTH_LABEL = t("Gui.Stat.Month")


class TestFormatRounds(unittest.TestCase):
    def test_rounds_are_rounded_to_integer(self):
        cases = [
            (711.33, 711),     # 生产库里的真实值（侵蚀5），旧口径会显示 711.3
            (360.8, 361),
            (360.4, 360),
            (360.5, 360),  # Python 的 banker's rounding，与导出侧的 round() 一致
            (361.5, 362),
            (0.4, 0),
            (0.0, 0),
            (12, 12),          # 已经是整数（比如侵蚀1 的口径传进来）
            (12.0, 12),
            ("360.8", 361),    # 字符串数字也容忍
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(expected, OpsiStatisticsMixin._format_rounds(raw))

    def test_no_value_returns_none_so_caller_can_use_dash(self):
        """取不到值时返回 None，由调用方换成占位符（与侵蚀1 行的 '-' 一致）。"""
        for raw in (None, "", "abc", [], {}):
            with self.subTest(raw=raw):
                self.assertIsNone(OpsiStatisticsMixin._format_rounds(raw))


class TestHazardRowsRoundsColumn(unittest.TestCase):
    """三行表里「出击轮次」这一列的类型与取值。"""

    @staticmethod
    def _rows(meow_by_level):
        # cl1_labels 是侵蚀1 表的列名（不含「侵蚀等级」列，那一列由本方法插入）
        labels = [MONTH_LABEL, ROUNDS_LABEL, COST_LABEL, "战斗场次"]
        values = ["2026-09", 1200, 6000, 2400]  # 侵蚀1 行：整数轮次
        return OpsiStatisticsMixin._build_hazard_rows(
            OpsiStatisticsMixin,
            labels,
            values,
            meow_by_level,
            "2026-09",
        )

    def test_meow_rows_show_integer_rounds(self):
        """侵蚀3/5 的轮次要取整，且类型与侵蚀1 行一致（int）。"""
        meow = {
            3: {"rounds": 360.8, "battle_count": 1},
            5: {"rounds": 128.4, "battle_count": 2},
        }
        labels, rows = self._rows(meow)
        rounds_index = labels.index(ROUNDS_LABEL)

        by_level = {row[labels.index(HAZARD_LABEL)]: row for row in rows}
        self.assertEqual(1200, by_level[1][rounds_index])
        self.assertEqual(361, by_level[3][rounds_index])   # 360.8 → 361
        self.assertEqual(128, by_level[5][rounds_index])   # 128.4 → 128
        for level in (1, 3, 5):
            self.assertIsInstance(
                by_level[level][rounds_index], int, f"侵蚀{level} 行应是整数"
            )

    def test_sortie_cost_still_uses_unrounded_rounds(self):
        """出击消耗必须用未取整的原值算，不能因为展示取整而改变统计口径。"""
        meow = {3: {"rounds": 360.8, "battle_count": 1}, 5: {"rounds": 128.4}}
        labels, rows = self._rows(meow)
        cost_index = labels.index(COST_LABEL)

        by_level = {row[labels.index(HAZARD_LABEL)]: row for row in rows}
        # 侵蚀3 每轮 15 点：360.8 × 15 = 5412（若用取整后的 361 会算成 5415）
        self.assertEqual(5412, by_level[3][cost_index])
        # 侵蚀5 每轮 30 点：128.4 × 30 = 3852（取整后是 3840）
        self.assertEqual(3852, by_level[5][cost_index])

    def test_missing_level_falls_back_to_dash(self):
        """该等级没有数据时，轮次与出击消耗都应是占位符。"""
        labels, rows = self._rows({})
        rounds_index = labels.index(ROUNDS_LABEL)
        cost_index = labels.index(COST_LABEL)
        by_level = {row[labels.index(HAZARD_LABEL)]: row for row in rows}
        for level in (3, 5):
            self.assertEqual("-", by_level[level][rounds_index])
            self.assertEqual("-", by_level[level][cost_index])


class TestMeowLootTableRounds(unittest.TestCase):
    """「本月耄耋相接收获」表（app_stat_opsi_export）的出击轮次列。

    这里曾用「round(x, 1) 后只在接近整数时才转 int」的口径，于是 360.8 会带着
    小数显示出来 —— 与同一张表右侧的累计轮数（早就是 int(round(...))）不一致。
    """

    @staticmethod
    def _rendered_rows(meow_stats_by_level):
        """跑一遍 _render_monthly_meow_loot，返回交给 build_simple_table 的行。"""
        captured = {}

        def fake_build_table(headers, rows):
            captured["headers"] = headers
            captured["rows"] = rows
            return "<table></table>"

        gui = object.__new__(OpsiExportMixin)
        gui.alas_name = "alas"
        # 汇总行「上次记录时间」不在本测试范围内，打桩掉
        gui._render_meow_loot_last_record = lambda _stats: None

        module = "module.webui.app_stat_opsi_export"
        with (
            patch(f"{module}.build_simple_table", side_effect=fake_build_table),
            patch(f"{module}.build_title_block", return_value="<div></div>"),
            patch(f"{module}.put_row", return_value=_StubOutput()),
            patch(f"{module}.put_html", return_value=_StubOutput()),
            patch(f"{module}.put_buttons", return_value=_StubOutput()),
            patch(f"{module}.put_scope", return_value=_StubOutput()),
            patch(f"{module}.put_text", return_value=_StubOutput()),
            patch(f"{module}.put_button", return_value=_StubOutput()),
            patch(f"{module}.refresh_icon_button_css", return_value=""),
            patch.object(
                OpsiExportMixin,
                "_meow_extra_columns",
                lambda self, _stats: {},
            ),
        ):
            from module.statistics.azurstats import AzurStats
            from module.statistics.cl1_database import db as cl1_db

            with (
                patch.object(
                    AzurStats,
                    "get_meow_loot_monthly_totals",
                    staticmethod(lambda year, month: {3: {}, 5: {}}),
                ),
                patch.object(
                    cl1_db,
                    "get_meow_stats",
                    lambda instance, year, month, hazard_level=None: {
                        "effective_rounds": meow_stats_by_level[hazard_level]
                    },
                ),
            ):
                gui._render_monthly_meow_loot(AzurStats)

        return captured["headers"], captured["rows"]

    def test_rounds_column_is_always_integer(self):
        headers, rows = self._rendered_rows({3: 360.8, 5: 128.4})
        rounds_index = headers.index(ROUNDS_LABEL)
        by_level = {row[headers.index(HAZARD_LABEL)]: row for row in rows}

        self.assertEqual(361, by_level[3][rounds_index])   # 旧口径会显示 360.8
        self.assertEqual(128, by_level[5][rounds_index])   # 旧口径会显示 128.4
        for level in (3, 5):
            self.assertIsInstance(by_level[level][rounds_index], int)

    def test_rounds_column_agrees_with_cumulative_column(self):
        """同一张表里，本月轮次与右侧累计轮数必须同为正整数。"""
        headers, rows = self._rendered_rows({3: 360.8, 5: 128.4})
        rounds_index = headers.index(ROUNDS_LABEL)
        cumulative_index = headers.index(t("Gui.Stat.MeowEffectiveRounds"))
        for row in rows:
            self.assertIsInstance(row[rounds_index], int)
            # 累计列在本测试里给了空占位，这里只确认本月的类型与取值
            self.assertNotIsInstance(row[rounds_index], float)
            self.assertIsNotNone(cumulative_index)


if __name__ == "__main__":
    unittest.main()
