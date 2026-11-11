import re
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
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
        "gem_success": {"successes": 21, "completed": 61, "rate": 34.43},
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

    def test_monthly_gem_success_is_inside_gem_card(self):
        cards = self._cards(self.summary_html)
        gem_card = next(card for card in cards if "icon_1.png" in card)
        self.assertIn("34.43%", gem_card)
        self.assertIn("本月钻石委托成功率", gem_card)
        self.assertIn("21", gem_card)
        self.assertIn("61", gem_card)
        for card in cards[1:]:
            self.assertNotIn("commission-gem-success", card)


class TestCommissionRecentScreenshotButton(unittest.TestCase):
    """一条委托记录只对应一次收获，因此只渲染一张截图。"""

    @classmethod
    def setUpClass(cls):
        lang.reload()

    def _recent_html(self, screenshots, items=None, name=None):
        harness = _CardHarness()
        data = _income_data()
        data["item_name_lookup"] = {}
        data["tracked_items"] = ["Gem", "Cube", "Chip", "Oil", "Coin"]
        data["recent"] = [
            {
                "ts": "2026-09-14T21:26:50.845000",
                "items": {"Gem": 10} if items is None else items,
                "commission_name": name,
                "commission_count": 1,
                "screenshots": screenshots,
            }
        ]
        _, recent_html = harness._build_commission_income_html(data)
        return recent_html

    def test_non_gem_record_has_no_screenshot_button(self):
        html = self._recent_html(["alas/2026-09/oil.png"], items={"Oil": 282})
        self.assertNotIn("查看截图", html)
        self.assertNotIn("/static/commission_rewards/", html)

    def test_legacy_gem_alias_and_invalid_amounts_keep_valid_rewards_visible(self):
        html = self._recent_html(["alas/2026-09/gem.png"], items={"Gem": "bad", "Gems": "10", "Oil": 100})
        self.assertEqual(1, html.count("查看截图"))
        self.assertIn("x100", html)

    def test_negative_alias_does_not_hide_a_positive_gem_reward(self):
        html = self._recent_html(["alas/2026-09/gem.png"], items={"Gem": 10, "Gems": -20})
        self.assertEqual(1, html.count("查看截图"))

    def test_screenshot_attribute_quotes_are_escaped(self):
        html = self._recent_html(['alas/2026-09/a" onmouseover="x.png'])
        self.assertIn('a&quot; onmouseover=&quot;x.png', html)
        self.assertNotIn(' onmouseover="', html)

    def test_commission_name_is_escaped(self):
        html = self._recent_html([], name="<委托>")
        self.assertIn("&lt;委托&gt;", html)
        self.assertNotIn("<委托>", html)

    def test_one_screenshot_renders_one_button(self):
        html = self._recent_html(["alas/2026-09/shot_a_0.png"])

        self.assertEqual(1, html.count("查看截图"))
        self.assertIn("/static/commission_rewards/alas/2026-09/shot_a_0.png", html)

    def test_legacy_entry_with_two_screenshots_renders_only_the_first(self):
        """历史遗留的双截图记录也不再渲染「查看截图2」。"""
        html = self._recent_html(
            ["alas/2026-09/legacy_0.png", "alas/2026-09/legacy_1.png"]
        )

        self.assertEqual(1, html.count("查看截图"))
        self.assertNotIn("查看截图2", html)
        self.assertIn("legacy_0.png", html)
        self.assertNotIn("legacy_1.png", html)


class TestCommissionIncomeLoadingAndFilters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        lang.reload()

    def setUp(self):
        self.harness = _CardHarness()
        self.harness.alas_name = "alas"
        self.harness._commission_income_period = "day"

    def _load(self, entries):
        with patch("module.statistics.commission_income_stats.get_recent_commission_entries",
                   return_value=entries) as recent, \
                patch("module.statistics.commission_income_stats.get_commission_income_summary",
                      return_value={"detail_rows": []}) as summary, \
                patch("module.statistics.commission_income_stats.get_monthly_gem_commission_success",
                      return_value={"successes": 21, "completed": 61, "rate": 34.43}) as gem_success, \
                patch.object(self.harness, "_load_running_commissions", return_value=None):
            data = self.harness._load_commission_income_data()
        recent.assert_called_once_with("alas", limit=None)
        summary.assert_called_once_with("alas", period="day")
        gem_success.assert_called_once_with("alas")
        return data

    def test_day_summary_keeps_all_monthly_records_and_monthly_gem_rate(self):
        entries = [{"items": {"Coin": 100}, "screenshots": []} for _ in range(61)]
        data = self._load(entries)

        self.assertEqual(data["period"], "day")
        self.assertEqual(len(data["recent"]), 61)
        self.assertEqual(data["gem_success"]["rate"], 34.43)

    def test_gem_filter_accepts_positive_alias_with_screenshot_and_skips_bad_values(self):
        self.harness._commission_gem_screenshots_only = True
        entries = [
            {"items": {"Gem": 10}, "screenshots": ["one.png"]},
            {"items": {"Gems": "20"}, "screenshots": ["two.png"]},
            {"items": {"Gem": 10}, "screenshots": []},
            {"items": {"Gem": "bad"}, "screenshots": ["bad.png"]},
            {"items": {"Gem": 0}, "screenshots": ["zero.png"]},
            {"items": {"Oil": 100}, "screenshots": ["oil.png"]},
            {"items": None, "screenshots": ["unknown.png"]},
        ]

        data = self._load(entries)

        self.assertEqual(data["recent"], entries[:2])

    def test_filter_callback_resets_page_and_renders_in_reserved_scope(self):
        self.harness._commission_recent_page = 6
        with patch("module.webui.app_stat_commission.use_scope", side_effect=lambda *a, **k: nullcontext()) as scope, \
                patch("module.webui.app_stat_commission.put_html"), \
                patch("module.webui.app_stat_commission.put_buttons") as periods, \
                patch("module.webui.app_stat_commission.put_button") as filter_button, \
                patch.object(self.harness, "_put_refresh_icon_button"), \
                patch.object(self.harness, "_render_commission_income") as render:
            self.harness._output_commission_income("summary", "recent", "day", 0)
            scope.assert_any_call("commission_income_gem_filter", clear=True)
            toggle = filter_button.call_args.kwargs["onclick"]
            toggle()
            self.assertTrue(self.harness._commission_gem_screenshots_only)
            self.assertEqual(self.harness._commission_recent_page, 0)
            self.harness._commission_recent_page = 6
            toggle()
            self.assertFalse(self.harness._commission_gem_screenshots_only)
            self.assertEqual(self.harness._commission_recent_page, 0)
            self.harness._commission_recent_page = 6
            periods.call_args.kwargs["onclick"]("week")
            self.assertEqual(self.harness._commission_income_period, "week")
            self.assertEqual(self.harness._commission_recent_page, 0)
            self.assertEqual(render.call_count, 3)

    def test_last_page_after_fifth_page_renders_and_clamps_correctly(self):
        self.harness._commission_recent_page = 99
        data = _income_data()
        data["recent"] = [
            {"ts": "2026-09-14T21:26:50", "commission_name": f"委托{index}", "items": {}}
            for index in range(61)
        ]

        _, html = self.harness._build_commission_income_html(data)

        self.assertEqual(self.harness._commission_recent_page, 6)
        self.assertIn("委托60", html)
        self.assertNotIn("委托59", html)

    def test_pagination_window_reaches_pages_after_fifth_and_callbacks_clamp(self):
        self.harness._commission_recent_page = 5
        with patch("module.webui.app_stat_commission.put_buttons", return_value=_Stub()) as buttons, \
                patch.object(self.harness, "_render_commission_income") as render:
            self.harness._output_recent_pagination(61)
            definitions = buttons.call_args.args[0]
            self.assertEqual([button["label"] for button in definitions], ["上一页", "3", "4", "5", "6", "7", "下一页"])
            self.assertEqual(next(button["color"] for button in definitions if button["label"] == "6"), "primary")
            paginate = buttons.call_args.kwargs["onclick"]
            paginate("next")
            self.assertEqual(self.harness._commission_recent_page, 6)
            paginate("next")
            self.assertEqual(self.harness._commission_recent_page, 6)
            paginate("prev")
            self.assertEqual(self.harness._commission_recent_page, 5)
            paginate(2)
            self.assertEqual(self.harness._commission_recent_page, 2)
            self.assertEqual(render.call_count, 4)


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
        # 使用固定统计夹具，不依赖工作区中真实账号是否已有舰船经验记录。
        stats = SimpleNamespace(
            data={"ships": [{"position": "测试旗舰"}], "last_check_time": "2026-10-03 00:00:00"},
            get_exp_per_hour=lambda: 100,
            get_today_stats=lambda: {},
            calculate_progress=lambda *_: {
                "position": "测试旗舰", "level": 100, "current_exp": 1, "total_exp": 1,
                "target_exp": 2, "exp_needed": 1, "battles_needed": 1, "time_needed": "1分钟",
            },
        )

        with patch(
            "module.statistics.ship_exp_stats.get_ship_exp_stats", return_value=stats,
        ), patch(
            "module.statistics.opsi_month.get_opsi_stats",
            return_value=SimpleNamespace(summary=lambda: {"total_battles": 0}),
        ), patch(
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
