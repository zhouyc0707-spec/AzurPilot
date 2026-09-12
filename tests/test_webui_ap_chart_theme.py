import contextlib
import re
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from module.webui.ap_chart_theme import (
    PALETTE_KEYS,
    chart_theme_group,
    chart_trend_colors,
    is_light_theme,
    palette_for_theme,
    series_colors,
)
from module.webui.app_stat_action_point import ActionPointStatisticsMixin


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestApChartPalette(unittest.TestCase):
    @staticmethod
    def _relative_luminance(color):
        """按 sRGB 相对亮度公式计算十六进制颜色亮度，用于对比度断言。"""
        channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [
            c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
            for c in channels
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    def test_every_theme_resolves_to_a_complete_palette(self):
        for theme in (
            "default",
            "dark",
            "advanced_material",
            "dark_advanced_material",
            None,
            "apple",
            "unknown",
        ):
            palette = palette_for_theme(theme)
            self.assertEqual(set(PALETTE_KEYS), set(palette), theme)
            self.assertTrue(all(palette.values()), theme)

    def test_theme_groups_split_light_and_dark(self):
        self.assertEqual("dark", chart_theme_group("dark"))
        self.assertEqual("dark", chart_theme_group("dark_advanced_material"))
        self.assertEqual("light", chart_theme_group("default"))
        self.assertEqual("light", chart_theme_group("advanced_material"))
        # 未知主题回退浅色，避免深色图表贴在默认浅色页面上
        self.assertEqual("light", chart_theme_group(None))
        self.assertTrue(is_light_theme("default"))
        self.assertFalse(is_light_theme("dark"))

    def test_dark_palette_keeps_historical_colors(self):
        palette = palette_for_theme("dark")

        self.assertEqual("#1a1a2e", palette["bg"])
        self.assertEqual("#2a2a3e", palette["grid"])
        self.assertEqual("#ce93d8", series_colors("dark")[1])
        self.assertEqual("#ef5350", palette["inc"])
        self.assertEqual("#26a69a", palette["dec"])

    def test_series_colors_match_canvas_line_colors(self):
        """图例/概览颜色必须与画布上的曲线颜色一致，避免同一条序列两种颜色。"""
        order = ("ap_point", "purple", "yellow", "asset", "distance")
        for theme in ("default", "dark"):
            palette = palette_for_theme(theme)
            self.assertEqual(
                [palette[key] for key in order],
                series_colors(theme),
            )

    def test_light_palette_uses_light_panel_and_page_accent(self):
        palette = palette_for_theme("default")

        self.assertEqual("#ffffff", palette["bg"])
        self.assertEqual("#dde0e5", palette["panel_border"])
        # 主曲线按涨跌着色，主色与 inc 一致
        self.assertEqual(palette["inc"], palette["ap_line"])
        # 浅底上不能用深色主题的浅色辅助序列（黄/紫），需换成深一档
        self.assertNotEqual(palette_for_theme("dark")["yellow"], palette["yellow"])
        self.assertNotEqual(palette_for_theme("dark")["purple"], palette["purple"])

    def test_ap_line_is_the_rising_color_in_both_families(self):
        """曲线按涨跌分段着色，主色必须等于上升色，避免图例与曲线对不上。"""
        for theme in ("default", "dark", "advanced_material"):
            palette = palette_for_theme(theme)
            self.assertEqual(palette["inc"], palette["ap_line"], theme)
            self.assertNotEqual(palette["inc"], palette["dec"], theme)

    def test_high_contrast_series_colors_on_light_background(self):
        palette = palette_for_theme("default")

        for key in ("ap_point", "purple", "yellow", "asset", "distance"):
            luminance = self._relative_luminance(palette[key])
            self.assertLess(luminance, 0.5, f"{key} 在浅色底上对比度不足")

    def test_trend_colors_flip_with_theme(self):
        light = chart_trend_colors("default")
        dark = chart_trend_colors("dark")

        self.assertEqual({"inc", "dec", "flat"}, set(light))
        self.assertNotEqual(light["inc"], dark["inc"])
        self.assertNotEqual(light["dec"], dark["dec"])

    def test_series_colors_match_javascript_series_order(self):
        js = (PROJECT_ROOT / "webapp" / "ap_chart.js").read_text(encoding="utf-8")
        self.assertIn('var seriesNames = ["体力", "紫币", "黄币", "资产", "海里数"]', js)
        self.assertEqual(5, len(series_colors("default")))

        # 画布上引用的 C.xxx 必须都在调色板里，否则会画出 undefined 颜色
        used = set(re.findall(r"\bC\.([a-z_0-9]+)", js))
        self.assertTrue(used)
        self.assertEqual(set(), used - set(PALETTE_KEYS))

    def test_javascript_no_longer_hardcodes_theme_colors(self):
        js = (PROJECT_ROOT / "webapp" / "ap_chart.js").read_text(encoding="utf-8")

        self.assertNotIn("#1a1a2e", js)
        self.assertNotIn("#64b5f6", js)
        self.assertIn("var C = __PALETTE__;", js)

    def test_javascript_draws_rising_and_falling_segments(self):
        """主曲线必须按涨跌逐段取色，不能退化成单色线。"""
        js = (PROJECT_ROOT / "webapp" / "ap_chart.js").read_text(encoding="utf-8")

        self.assertIn("ap[i] >= ap[i - 1] ? C.inc : C.dec", js)
        self.assertNotIn("smoothLine", js)
        # 单色曲线用的是 C.ap_line，出现即说明还有单色分支
        self.assertNotIn("strokeStyle = C.ap_line", js)

    def test_javascript_moves_period_buttons_into_the_legend_row(self):
        """put_html 生成的 div 不是 PyWebIO scope，按钮只能渲染到面板末尾；
        必须由前端把它搬进图例行，否则会掉到图表下方。"""
        js = (PROJECT_ROOT / "webapp" / "ap_chart.js").read_text(encoding="utf-8")
        source = (PROJECT_ROOT / "module/webui/app_stat_action_point.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("movePeriodSelectorToLegend", js)
        self.assertIn(".ap-legend-row", js)
        # 容器必须是 put_scope 建的（put_html 的 div 不作为 scope 目标）
        self.assertIn("put_scope(period_scope, [])", source)

    def test_panel_template_has_no_dark_only_colors(self):
        panel = (PROJECT_ROOT / "webapp" / "ap_chart_panel.html").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("#1a1a2e", panel)
        self.assertNotIn("#39394e", panel)
        self.assertIn("{chart_bg}", panel)
        self.assertIn("{ap_color}", panel)


class _ChartRenderHarness:
    """以最小依赖驱动 _render_ap_chart_content，捕获产出的 HTML 与 JS。"""

    theme = "default"

    def render(self, chart_data, auxiliary_data):
        harness = ActionPointStatisticsMixin()
        harness.theme = self.theme
        captured = {}
        with patch(
            "module.webui.app_stat_action_point.put_html",
            side_effect=lambda x: captured.setdefault("html", x),
        ), patch(
            "module.webui.app_stat_action_point.use_scope",
            side_effect=lambda *args, **kwargs: contextlib.nullcontext(),
        ), patch(
            "pywebio.session.run_js",
            side_effect=lambda x: captured.setdefault("js", x),
        ):
            harness._render_ap_chart_content(chart_data, auxiliary_data, self.theme)
        return captured["html"], captured["js"]


def _chart_data():
    ap = [120, 118, 125, 140, 138, 150]
    return dict(
        current_view="line",
        view_title="T",
        ap_cur=150,
        ap_now_cur=45,
        change_sign="+",
        ap_change=30,
        ap_max=150,
        ap_min=118,
        ap_avg=131,
        is_detail_mode=False,
        labels=[f"09-1{i} 10:00" for i in range(len(ap))],
        opens=ap,
        highs=ap,
        lows=ap,
        closes=ap,
        counts=[1] * len(ap),
        ap_list=ap,
        ap_ts=list(range(len(ap))),
        detail_sources=["cl1"] * len(ap),
    )


def _auxiliary_data():
    return dict(
        coins_stats_html="<i>c</i>",
        coins_legend_html="<i>l</i>",
        show_coins=True,
        yellow_coins_list=[10, 12, 15, 14, 18, 20],
        purple_coins_list=[1, 1, 2, 2, 3, 3],
        coins_sources_list=["cl1"] * 6,
        asset_list=[1.5, 2.5, 3.5, 4.5, 5.5, 6.5],
        asset_ts_list=list(range(6)),
        distance_list=[100, 101, 103, 106, 110, 115],
    )


class TestApChartThemeRendering(unittest.TestCase):
    def test_light_theme_renders_white_panel(self):
        harness = _ChartRenderHarness()
        harness.theme = "default"

        html, js = harness.render(_chart_data(), _auxiliary_data())

        self.assertIn("background:#ffffff", html)
        self.assertNotIn("#1a1a2e", html)
        self.assertIn('"bg": "#ffffff"', js)
        # 浅色主题也用红绿涨跌色，只是色号更深
        self.assertIn(f'"inc": "{palette_for_theme("default")["inc"]}"', js)
        self.assertIn(f'"dec": "{palette_for_theme("default")["dec"]}"', js)
        self.assertNotIn("smoothLine", js)

    def test_dark_theme_renders_dark_panel(self):
        harness = _ChartRenderHarness()
        harness.theme = "dark"

        html, js = harness.render(_chart_data(), _auxiliary_data())

        self.assertIn("background:#1a1a2e", html)
        self.assertIn('"bg": "#1a1a2e"', js)
        self.assertIn(f'"inc": "{palette_for_theme("dark")["inc"]}"', js)
        self.assertIn(f'"dec": "{palette_for_theme("dark")["dec"]}"', js)
        self.assertNotIn("smoothLine", js)

    def test_all_placeholders_are_substituted(self):
        harness = _ChartRenderHarness()
        harness.theme = "advanced_material"

        html, js = harness.render(_chart_data(), _auxiliary_data())

        for rendered in (html, js):
            leftovers = [
                line
                for line in rendered.splitlines()
                if "__" in line and "apChartCleanups" not in line
            ]
            self.assertEqual([], leftovers)

    def test_legend_row_reserves_a_slot_for_the_period_buttons(self):
        harness = _ChartRenderHarness()
        harness.theme = "default"

        html, _ = harness.render(_chart_data(), _auxiliary_data())

        self.assertIn('class="ap-chart-panel"', html)
        self.assertIn('class="ap-legend-row"', html)
        # 图例行左侧留给时间范围按钮（scope 由 put_scope 创建、渲染后由 JS 搬入）
        self.assertIn('.ap-legend-row > [id^="pywebio-scope-"]', html)
        self.assertIn("order: -1", html)
        # 标题与图例的上下留白收紧，避免标题到图表之间出现过大空白
        self.assertIn("margin-top:10px", html)
        self.assertNotIn("margin-top:16px", html)


class TestApChartPeriodFilter(unittest.TestCase):
    """图表时间范围：今日 / 本周（近 7 天）/ 本月（不裁剪）。"""

    @staticmethod
    def _points(days_ago_list, now):
        return [
            {"ts": (now - timedelta(days=days_ago)).isoformat(), "ap": 100}
            for days_ago in days_ago_list
        ]

    def test_month_keeps_every_point(self):
        now = datetime(2026, 9, 13, 12, 0, 0)
        points = self._points([0, 1, 3, 8, 20], now)

        kept = ActionPointStatisticsMixin._filter_points_by_period(
            points, "month", now
        )

        self.assertEqual(points, kept)

    def test_day_keeps_only_today(self):
        now = datetime(2026, 9, 13, 12, 0, 0)
        points = self._points([0, 1, 3, 8, 20], now)

        kept = ActionPointStatisticsMixin._filter_points_by_period(points, "day", now)

        self.assertEqual(1, len(kept))
        self.assertTrue(kept[0]["ts"].startswith("2026-09-13"))

    def test_week_keeps_last_seven_days(self):
        now = datetime(2026, 9, 13, 12, 0, 0)
        points = self._points([0, 1, 3, 6, 7, 8, 20], now)

        kept = ActionPointStatisticsMixin._filter_points_by_period(points, "week", now)

        # 近 7 天含今天，窗口从 09-07 00:00 起：0/1/3/6 天前（09-13/12/10/07）在内，
        # 7 天前的 09-06 12:00 已早于窗口起点，与 8/20 天前一起被裁掉
        self.assertEqual(4, len(kept))
        for point in kept:
            self.assertGreaterEqual(
                datetime.fromisoformat(point["ts"]), datetime(2026, 9, 7)
            )

    def test_broken_timestamps_are_dropped_not_crashing(self):
        now = datetime(2026, 9, 13, 12, 0, 0)
        points = [{"ts": "not-a-time", "ap": 1}, *self._points([0], now)]

        kept = ActionPointStatisticsMixin._filter_points_by_period(points, "day", now)

        self.assertEqual(1, len(kept))

    def test_unknown_period_falls_back_to_month(self):
        harness = ActionPointStatisticsMixin()
        harness._ap_chart_period_value = "fortnight"

        self.assertEqual("month", harness._ap_chart_period())
        harness._ap_chart_period_value = "day"
        self.assertEqual("day", harness._ap_chart_period())


if __name__ == "__main__":
    unittest.main()
