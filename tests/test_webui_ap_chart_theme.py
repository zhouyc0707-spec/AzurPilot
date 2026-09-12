import contextlib
import re
import unittest
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
        self.assertEqual("#64b5f6", palette["ap_point"])
        self.assertEqual("#ce93d8", series_colors("dark")[1])
        self.assertEqual("#ef5350", palette["inc"])

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
        # 曲线使用页面主色系，与 Light 主题的侧边栏/按钮色协调
        self.assertEqual("#3f51b5", palette["ap_line"])
        # 浅底上不能用深色主题的浅色辅助序列（黄/紫），需换成深一档
        self.assertNotEqual(palette_for_theme("dark")["yellow"], palette["yellow"])
        self.assertNotEqual(palette_for_theme("dark")["purple"], palette["purple"])

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
        self.assertIn("var smoothLine = __SMOOTH_LINE__;", js)

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
    def test_light_theme_renders_white_panel_and_smooth_line(self):
        harness = _ChartRenderHarness()
        harness.theme = "default"

        html, js = harness.render(_chart_data(), _auxiliary_data())

        self.assertIn("background:#ffffff", html)
        self.assertNotIn("#1a1a2e", html)
        self.assertIn("var smoothLine = true", js)
        self.assertIn('"bg": "#ffffff"', js)

    def test_dark_theme_renders_dark_panel_and_segmented_line(self):
        harness = _ChartRenderHarness()
        harness.theme = "dark"

        html, js = harness.render(_chart_data(), _auxiliary_data())

        self.assertIn("background:#1a1a2e", html)
        self.assertIn("var smoothLine = false", js)
        self.assertIn('"bg": "#1a1a2e"', js)

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


if __name__ == "__main__":
    unittest.main()
