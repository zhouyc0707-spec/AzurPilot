import re
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import module.webui.lang as lang
from module.webui.app_stat_commission import CommissionIncomeStatisticsMixin
from module.webui.app_stat_ship import ShipExperienceStatisticsMixin
from module.webui.stat_icon import (
    ICON_COLOR_DARK,
    ICON_COLOR_LIGHT,
    refresh_icon_data_uri,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _Stub:
    """占位输出对象，只实现链式 style()。"""

    def style(self, _value):
        return self


class _CardHarness(CommissionIncomeStatisticsMixin):
    """直接调用卡片 HTML 构造函数，不需要 WebUI 会话。"""


def _income_data(rows=None):
    """构造 _build_commission_income_html 需要的最小载荷。"""
    import datetime

    rows = rows if rows is not None else [
        {"name": "Gem", "color": "#e91e63", "total": 326, "count": 17, "avg": 19.2},
        {"name": "Cube", "color": "#00bcd4", "total": 27, "count": 18, "avg": 1.5},
        {"name": "Chip", "color": "#9c27b0", "total": 1370, "count": 73, "avg": 18.8},
        {"name": "Oil", "color": "#607d8b", "total": 50947, "count": 389, "avg": 131.0},
        {"name": "Coin", "color": "#ffc107", "total": 26943, "count": 252, "avg": 106.9},
    ]
    return {
        "period": "month",
        "summary": {"total_commissions": 749, "detail_rows": rows},
        "recent": [],
        "item_name_map": {
            "Gem": "钻石",
            "Cube": "心智魔方",
            "Chip": "心智",
            "Oil": "石油",
            "Coin": "物资",
        },
        "item_icon_map": {
            "Gem": "static/assets/gui/icon/icon_1.png",
            "Cube": "static/assets/gui/icon/icon_2.png",
            "Chip": "static/assets/gui/icon/icon_3.png",
            "Oil": "static/assets/gui/icon/icon_4.png",
            "Coin": "static/assets/gui/icon/icon_5.png",
        },
        "datetime": datetime.datetime,
        "item_meta": {},
        "item_name_lookup": {},
        "tracked_items": [],
    }


class TestCommissionIncomeCards(unittest.TestCase):
    """委托收益的物品框内联展示委托次数与平均/次。"""

    @classmethod
    def setUpClass(cls):
        # t() 依赖 i18n 字典，测试进程里需要先加载
        lang.reload()

    def setUp(self):
        self.harness = _CardHarness()
        self.summary_html, self.recent_html = (
            self.harness._build_commission_income_html(_income_data())
        )

    @staticmethod
    def _cards(summary_html):
        """按卡片起点切分，返回每张卡片的 HTML 片段。"""
        marker = '<div class="commission-income-metric-card"'
        return summary_html.split(marker)[1:]

    @staticmethod
    def _stat_line(card_html):
        """取出卡片内第三行（委托次数 / 平均）的文本。"""
        match = re.search(r'font-size: 0\.72rem; opacity: 0\.55;">([^<]*)<', card_html)
        return match.group(1) if match else None

    def test_detail_table_is_gone(self):
        self.assertNotIn("<table", self.summary_html)
        self.assertNotIn("commission-income-table", self.summary_html)

    def test_each_item_card_carries_count_and_average(self):
        cards = self._cards(self.summary_html)
        self.assertEqual(5, len(cards))

        expectations = {
            "icon_1.png": ("17", "19.2"),
            "icon_2.png": ("18", "1.5"),
            "icon_3.png": ("73", "18.8"),
            "icon_4.png": ("389", "131.0"),
            "icon_5.png": ("252", "106.9"),
        }
        for icon, (count, avg) in expectations.items():
            card = next(card for card in cards if icon in card)
            line = self._stat_line(card)
            self.assertIsNotNone(line, icon)
            self.assertIn(count, line, icon)
            self.assertIn(avg, line, icon)

    def test_metrics_stay_inside_their_own_card(self):
        cards = self._cards(self.summary_html)
        gem_card = next(card for card in cards if "icon_1.png" in card)

        for other_avg in ("1.5", "18.8", "131.0", "106.9"):
            self.assertNotIn(other_avg, gem_card)

    def test_totals_remain_thousands_separated(self):
        oil_card = next(
            card for card in self._cards(self.summary_html) if "icon_4.png" in card
        )
        self.assertIn("+50,947", oil_card)

    def test_empty_detail_rows_render_no_cards(self):
        summary_html, _ = self.harness._build_commission_income_html(
            _income_data(rows=[])
        )
        self.assertEqual([], self._cards(summary_html))


class TestCommissionTitleRefreshIcon(unittest.TestCase):
    """刷新改成标题旁的图标按钮，并去掉重复的页码文字。"""
    @classmethod
    def setUpClass(cls):
        lang.reload()

    def setUp(self):
        self.harness = _CardHarness()
        self.summary_html, _ = self.harness._build_commission_income_html(
            _income_data()
        )

    def test_title_row_reserves_a_slot_for_the_icon(self):
        self.assertIn('class="stat-title-row"', self.summary_html)
        self.assertIn('class="stat-title-text"', self.summary_html)
        self.assertIn(
            'id="pywebio-scope-commission_income_refresh"', self.summary_html
        )
        # 标题行只保留一个容器，图标按钮由 PyWebIO 渲染进去
        self.assertNotIn("<button", self.summary_html)

    def test_icon_is_a_valid_svg_with_ring_and_arrowhead(self):
        svg = refresh_icon_data_uri(ICON_COLOR_LIGHT)

        self.assertTrue(svg.startswith('url("data:image/svg+xml,'))
        # 圆环 + 箭头两条 path
        self.assertEqual(2, svg.count("%3Cpath"))
        # 圆环必须用三段三次贝塞尔：折线写法会超出 SVG 坐标上限被浏览器截断
        self.assertEqual(3, len(re.findall(r"C\d", svg)))
        self.assertIn("%23", svg, "颜色未转义")
        # 引号外的空格必须转义，否则 CSS 解析会截断 URL
        self.assertNotIn(" ", svg.replace('url("', "").replace('")', ""))

    def test_icon_has_light_and_dark_variants(self):
        light = refresh_icon_data_uri(ICON_COLOR_LIGHT)
        dark = refresh_icon_data_uri(ICON_COLOR_DARK)

        self.assertNotEqual(light, dark)

    def test_pagination_no_longer_prints_the_page_number(self):
        """按钮组已高亮当前页，再写一遍“第 x / y 页”是重复信息。"""
        captured = []
        with patch(
            "module.webui.app_stat_commission.put_buttons",
            side_effect=lambda *a, **k: captured.append(("buttons", a[0], k)) or _Stub(),
        ), patch(
            "module.webui.app_stat_commission.put_text",
            side_effect=lambda *a, **k: captured.append(("text", a, k)) or _Stub(),
        ), patch(
            "module.webui.app_stat_commission.use_scope",
            side_effect=lambda *a, **k: nullcontext(),
        ):
            self.harness._output_recent_pagination(25)

        self.assertEqual(
            ["buttons"], [item[0] for item in captured], "不应再输出页码文字"
        )
        labels = [b["label"] for b in captured[0][1]]
        self.assertEqual(
            ["上一页", "1", "2", "3", "下一页"],
            labels,
        )
        self.assertEqual("primary", captured[0][1][1]["color"], "当前页应高亮")


class TestShipExpTitleRefreshIcon(unittest.TestCase):
    """每日经验检测的刷新同样收进标题旁的图标按钮。"""

    @classmethod
    def setUpClass(cls):
        lang.reload()

    def _source(self):
        return (PROJECT_ROOT / "module/webui/app_stat_ship.py").read_text(
            encoding="utf-8"
        )

    def test_title_row_reserves_a_slot_for_the_icon(self):
        src = self._source()

        self.assertIn("build_title_icon_row(", src)
        self.assertIn("_SHIP_EXP_REFRESH_SCOPE", src)
        self.assertIn('"ship_exp_refresh"', src)
        self.assertIn("refresh_icon_button_css(", src)

    def test_button_is_scoped_into_the_title_row(self):
        src = self._source()

        # 图标按钮必须渲染进标题行预留的 scope，而不是排在表格下方
        self.assertIn("scope=_SHIP_EXP_REFRESH_SCOPE", src)
        self.assertNotIn('put_button(\n                    t("Gui.Stat.Refresh")', src)

    def test_render_output_puts_the_button_in_the_title_row(self):
        captured = []
        harness = ShipExperienceStatisticsMixin()

        with patch(
            "module.webui.app_stat_ship.build_simple_table",
            return_value="<table></table>",
        ), patch(
            "module.webui.app_stat_ship.put_html",
            side_effect=lambda *a, **k: captured.append(("html", a)) or _Stub(),
        ), patch(
            "module.webui.app_stat_ship.put_text",
            side_effect=lambda *a, **k: _Stub(),
        ), patch(
            "module.webui.app_stat_ship.put_row",
            side_effect=lambda *a, **k: _Stub(),
        ), patch(
            "module.webui.app_stat_ship.put_button",
            side_effect=lambda *a, **k: captured.append(("button", a, k)) or _Stub(),
        ), patch(
            "module.webui.app_stat_ship.use_scope",
            side_effect=lambda *a, **k: nullcontext(),
        ):
            harness._render_ship_exp()

        title_html = next(
            payload
            for payload in (item[1][0] for item in captured if item[0] == "html")
            if isinstance(payload, str) and "stat-title-row" in payload
        )
        self.assertIn('id="pywebio-scope-ship_exp_refresh"', title_html)

        _, args, kwargs = next(item for item in captured if item[0] == "button")
        self.assertEqual("", args[0], "图标按钮不应带文字 label")
        self.assertEqual("ship_exp_refresh", kwargs["scope"])


if __name__ == "__main__":
    unittest.main()
