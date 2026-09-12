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
            self.assertTrue(
                selector.startswith(":is(") or selector.startswith("#pywebio-scope-"),
                selector,
            )
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
        self.assertIn("border: 1px solid var(--alas-entry-border) !important;", base)
        # 圆角与高度在所有按钮上一致
        radius = re.search(r"border-radius:\s*([^;]+);", base)
        self.assertIsNotNone(radius)
        self.assertIn(radius.group(1).strip(), self.block)

    def test_buttons_do_not_stretch_across_the_row(self):
        """alas-mobile.css 会给这些作用域里的按钮加 width:100%!important，
        而它排在 entry-alas.css 之后；按钮与分段容器必须显式按内容取宽，
        否则窄屏下今日/本周/本月与分页会被拉满整行。"""
        base = next(
            d for s, d in self.rules if re.search(r"\)\s*\.btn\s*$", s.strip())
        )
        group = next(
            d for s, d in self.rules if re.search(r"\)\s*\.btn-group\s*$", s.strip())
        )
        group_btn = next(
            d
            for s, d in self.rules
            if re.search(r"\)\s*\.btn-group\s+\.btn\s*$", s.strip())
        )

        for name, body in (("按钮", base), ("分段容器", group), ("组内按钮", group_btn)):
            self.assertIn("width: auto !important;", body, name)
        # 组内按钮还要钉住尺寸，抵消移动端更大的内边距把整组顶高
        self.assertIn("height: 30px !important;", group_btn)
        self.assertIn("padding: 0 14px !important;", group_btn)
        self.assertIn("line-height: 1 !important;", group_btn)

    def test_selected_state_keeps_the_neutral_shape(self):
        """选中态只换配色，不改形状，并排才不会一高一低。"""
        active = next(
            d for s, d in self.rules if re.search(r"\)\s*\.btn-primary\s*$", s.strip())
        )
        for prop in ("height", "padding", "border-radius", "font-size"):
            self.assertNotIn(prop, active, prop)
        # 选中态用主色实心填充 + 反色文字，形成明确的主次层次
        self.assertIn("background: var(--alas-entry-accent) !important;", active)
        self.assertIn("color: var(--alas-entry-on-accent) !important;", active)

    def test_segmented_buttons_keep_a_visible_surface(self):
        """组内按钮保留底色，分段边界在四个主题下都要看得见。"""
        group_btn = next(
            d
            for s, d in self.rules
            if re.search(r"\)\s*\.btn-group\s+\.btn\s*$", s.strip())
        )
        self.assertIn("background: var(--alas-entry-surface) !important;", group_btn)
        self.assertIn("border-left: 1px solid var(--alas-entry-border) !important;", group_btn)

    def test_segmented_group_has_no_extra_space_below(self):
        """组容器必须显式居中并清掉主题边距。

        否则：inline-flex 默认按基线对齐，按钮内没有文字基线可用，浏览器用底边
        当基线把整组往下顶；主题的 .btn-off 还带 .125rem 上下外边距。两者都会让
        按钮下方多出一段空白。
        """
        group = next(
            d for s, d in self.rules if re.search(r"\)\s*\.btn-group\s*$", s.strip())
        )
        self.assertIn("align-items: center !important;", group)
        self.assertIn("vertical-align: middle !important;", group)

        group_btn = next(
            d
            for s, d in self.rules
            if re.search(r"\)\s*\.btn-group\s+\.btn\s*$", s.strip())
        )
        self.assertIn("margin: 0 !important;", group_btn)

    def test_standalone_buttons_are_baseline_safe(self):
        """普通按钮也是 inline-flex，同样要降到中线，避免行内下沉。"""
        base = next(
            d for s, d in self.rules if re.search(r"\)\s*\.btn\s*$", s.strip())
        )
        self.assertIn("vertical-align: middle !important;", base)

    def test_selected_rule_comes_after_group_rule(self):
        """`:is(...) .btn-group .btn` 比 `:is(...) .btn-primary` 多一个类，
        特异性更高。选中态必须额外写一条组内版本，且两条都排在分段规则之后，
        否则组内选中项会被刷回白底。"""
        selectors = [s.strip() for s, _ in self.rules]

        group_index = next(
            i
            for i, s in enumerate(selectors)
            if re.search(r"\)\s*\.btn-group\s+\.btn\s*$", s)
        )
        grouped_selected_index = next(
            i
            for i, s in enumerate(selectors)
            if re.search(r"\)\s*\.btn-group\s+\.btn-primary", s)
        )
        self.assertLess(group_index, grouped_selected_index)

    def test_colors_come_from_theme_variables(self):
        """除阴影用的半透明黑之外，不应写死颜色。"""
        declarations = re.findall(r":\s*([^;{}]+);", self.block)
        literals = [
            value.strip()
            for value in declarations
            if re.search(r"#[0-9a-fA-F]{3,8}\b", value)
            or (
                re.search(r"\brgba?\(", value)
                and not re.search(r"rgba\(0,\s*0,\s*0,", value)
            )
        ]
        self.assertEqual([], literals, "统一按钮样式不应写死主题颜色")

    def test_row_title_matches_button_height(self):
        """与按钮同排的标题要与按钮等高，否则按钮看起来会偏上。"""
        title = next(
            d
            for s, d in self.rules
            if s.strip().startswith("#pywebio-scope-opsi_stats .stat-row-title")
        )
        self.assertIn("height: 30px !important;", title)
        self.assertIn("align-items: center !important;", title)
        self.assertIn("margin: 0 !important;", title)


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
