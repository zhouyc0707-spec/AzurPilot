import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 统计界面里可能出现按钮的作用域
STAT_SCOPES = (
    "#pywebio-scope-stat_panels_charts",
    "#pywebio-scope-ap_chart",
    "#pywebio-scope-statistics-toolbar",
    "#pywebio-scope-opsi_stats",
    "#pywebio-scope-meow_loot_scope",
    "#pywebio-scope-commission_income",
)


def _read_css():
    return (PROJECT_ROOT / "assets/gui/css/entry-alas.css").read_text(encoding="utf-8")


def _button_style_block():
    """取出统一样式块（已去注释，避免把说明文字当成规则）。"""
    css = _read_css()
    # 从起始注释结束处取到结束注释开始处，再统一去掉块内注释
    start = css.index("统计页统一按钮样式开始")
    start = css.index("*/", start) + len("*/")
    end = css.index("/* --- 统计页统一按钮样式结束")
    return css[start:end]


def _rules(block):
    """把样式块拆成 (选择器, 声明) 列表，忽略注释。"""
    block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
    result = []
    for body in re.findall(r"[^{}]+\{[^{}]*\}", block):
        selector, _, declarations = body.partition("{")
        selector = selector.strip()
        if selector:
            result.append((selector, declarations))
    return result


class TestStatButtonStyle(unittest.TestCase):
    """统计界面按钮统一样式写在样式表里，且必须覆盖全部统计作用域。"""

    def setUp(self):
        self.css = _read_css()
        self.block = _button_style_block()
        self.rules = _rules(self.block)

    def test_style_lives_in_stylesheet_not_runtime_injection(self):
        """样式放样式表里，页面一加载就生效，不依赖 Python 运行时注入。"""
        self.assertTrue(self.css.count("统计页统一按钮样式开始"), 1)
        # 不应再有运行时注入的样式模块
        self.assertFalse(
            (PROJECT_ROOT / "module/webui/app_stat_styles.py").exists(),
            "统一样式已移入 CSS，不应保留运行时注入模块",
        )

    def test_every_rule_is_scoped_to_a_stat_scope(self):
        self.assertTrue(self.rules)
        for selector, _ in self.rules:
            self.assertTrue(selector.startswith(":is("), selector)
            self.assertTrue(
                any(scope in selector for scope in STAT_SCOPES), selector
            )

    def test_no_selector_concatenation_artifacts(self):
        for selector, _ in self.rules:
            self.assertNotRegex(selector, r"\)\.[a-z]", selector)

    def test_button_box_model_is_normalised(self):
        base = next(
            d for s, d in self.rules if re.search(r"\)\s*\.btn\s*$", s.strip())
        )
        self.assertIn("height: 30px !important;", base)
        self.assertIn("padding: 0 14px !important;", base)
        self.assertIn("border-radius: 12px !important;", base)
        self.assertIn("border: 1px solid var(--alas-entry-border) !important;", base)

    def test_selected_state_keeps_the_neutral_shape(self):
        """选中态只换底色与文字色，不改形状，并排才不会一高一低。"""
        active = next(
            d for s, d in self.rules if re.search(r"\)\s*\.btn-primary\s*$", s.strip())
        )
        for prop in ("height", "padding", "border-radius", "font-size"):
            self.assertNotIn(prop, active, prop)
        self.assertIn("var(--alas-entry-accent-soft)", active)
        self.assertIn("var(--alas-entry-accent)", active)

    def test_segmented_buttons_keep_a_visible_surface(self):
        """组内按钮保留底色，分段边界在四个主题下都要看得见。"""
        group_btn = next(
            d
            for s, d in self.rules
            if re.search(r"\)\s*\.btn-group\s+\.btn\s*$", s.strip())
        )
        self.assertIn("background: var(--alas-entry-surface) !important;", group_btn)
        self.assertIn("border-left: 1px solid var(--alas-entry-border) !important;", group_btn)

    def test_colors_come_from_theme_variables(self):
        literals = [
            value
            for value in re.findall(r":\s*([^;{}]+);", self.block)
            if re.search(r"#[0-9a-fA-F]{3,8}\b", value) or re.search(r"\brgba?\(", value)
        ]
        self.assertEqual([], literals, "统一按钮样式不应写死颜色")


class TestStatButtonCallSites(unittest.TestCase):
    """各按钮调用点必须用统一的 color 取值，不再混用 Bootstrap 配色。"""

    def _source(self, name):
        return (PROJECT_ROOT / "module/webui" / name).read_text(encoding="utf-8")

    def test_commission_buttons_use_off_and_primary(self):
        src = self._source("app_stat_commission.py")

        # 不再出现 Bootstrap 的 secondary 配色
        self.assertNotIn('"secondary"', src)
        # 选中区间用 primary 标记，其余为 off
        self.assertIn('"primary" if period ==', src)
        self.assertIn('else "off"', src)
        # 分页当前页用 primary 标记，其余为 off
        self.assertIn('"primary" if (index - 1) == page else "off"', src)
        # 刷新按钮用 off
        self.assertIn('color="off",', src)

    def test_meow_month_buttons_use_off(self):
        src = self._source("app_stat_opsi_export.py")

        self.assertIn('"value": "history", "color": "off"', src)
        self.assertIn('"value": "current", "color": "off"', src)
        self.assertNotIn('"color": "secondary"', src)

    def test_chart_view_toolbar_uses_off_buttons_without_theme_palette(self):
        src = self._source("app_stat_action_point_toolbar.py")

        self.assertIn('"color": "primary" if current_view == value else "off"', src)
        self.assertIn('color="off",', src)
        # 原先工具栏自带一套按主题分支的配色，已删除
        self.assertNotIn("md3_colors", src)
        self.assertNotIn("self.theme", src)

    def test_export_buttons_use_off(self):
        src = self._source("app_stat_opsi.py")

        self.assertEqual(2, src.count('color="off"'))


if __name__ == "__main__":
    unittest.main()
