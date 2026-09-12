import re
import unittest

from module.webui.app_stat_styles import BUTTON_SCOPES, BUTTON_STYLE


class TestStatButtonStyle(unittest.TestCase):
    """统计页统一按钮样式：作用域受限、覆盖足够、选择器合法。"""

    @staticmethod
    def _selectors():
        """取出样式块里的全部选择器（去掉注释与声明）。"""
        css = BUTTON_STYLE.split("<style>", 1)[1].rsplit("</style>", 1)[0]
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        return [
            block.split("{", 1)[0].strip()
            for block in re.findall(r"[^{}]+\{[^{}]*\}", css)
            if block.split("{", 1)[0].strip()
        ]

    def test_every_selector_is_scoped_to_a_stats_scope(self):
        selectors = self._selectors()
        self.assertTrue(selectors)

        for selector in selectors:
            self.assertTrue(
                selector.startswith(":is(") or selector.startswith("#pywebio-scope-"),
                selector,
            )
            self.assertTrue(
                any(scope in selector for scope in (BUTTON_SCOPES + (
                    "#pywebio-scope-opsi_stats",
                ))),
                selector,
            )

    def test_selectors_have_no_concatenation_artifacts(self):
        """.btn 与状态伪类之间必须保留空格，历史实现漏过这个空格。"""
        selectors = self._selectors()

        for selector in selectors:
            self.assertNotRegex(selector, r"\)\.\w", selector)
            self.assertNotRegex(selector, r"\.btn\.(?!primary)", selector)

    def test_button_box_model_is_normalised(self):
        css = BUTTON_STYLE

        # 高度、内边距、圆角、字号必须一致，否则按钮无法与标题对齐
        for prop in ("height", "padding", "border-radius", "font-size", "line-height"):
            self.assertIn(f"{prop}:", css, prop)
        self.assertIn("height: 30px !important;", css)
        self.assertIn("padding: 0 14px !important;", css)

    def test_theme_classes_are_overridden_with_important(self):
        """必须压过 Bootstrap 与高级材质主题里带 !important 的按钮规则。"""
        css = BUTTON_STYLE
        active_rule = re.search(
            r"\)\s*\.btn-primary\s*\{([^}]*)\}", css, flags=re.S
        )

        self.assertIsNotNone(active_rule)
        body = active_rule.group(1)
        self.assertIn("var(--alas-entry-accent-soft)", body)
        self.assertIn("var(--alas-entry-accent)", body)
        for declaration in ("background", "border-color", "color"):
            self.assertRegex(body, rf"{declaration}: [^;]*!important")

    def test_selected_state_keeps_the_neutral_button_shape(self):
        """选中态只换底色与文字色，形状与中性态一致，并排才不会一高一低。"""
        css = BUTTON_STYLE
        neutral = re.search(r"\)\s*\.btn\s*\{([^}]*)\}", css, flags=re.S)
        active = re.search(r"\)\s*\.btn-primary\s*\{([^}]*)\}", css, flags=re.S)

        self.assertIsNotNone(neutral)
        self.assertIsNotNone(active)
        # 选中态不覆写尺寸类属性，形状完全继承中性态
        for prop in ("height", "padding", "border-radius", "font-size"):
            self.assertNotIn(prop, active.group(1), prop)
        self.assertIn("border-radius: 12px !important;", neutral.group(1))

    def test_colors_come_from_theme_variables(self):
        """不写死颜色，四个主题才能自动跟随。"""
        declarations = re.findall(r":\s*([^;{}]+);", BUTTON_STYLE)

        literals = [
            value.strip()
            for value in declarations
            if re.search(r"#[0-9a-fA-F]{3,8}\b", value)
            or re.search(r"\brgba?\(", value)
        ]
        self.assertEqual([], literals)

    def test_row_title_matches_button_height(self):
        title_rule = re.search(
            r"\.stat-row-title\s*\{([^}]*)\}", BUTTON_STYLE, flags=re.S
        )

        self.assertIsNotNone(title_rule)
        body = title_rule.group(1)
        self.assertIn("height: 30px !important;", body)
        self.assertIn("align-items: center !important;", body)
        self.assertIn("margin: 0 !important;", body)


if __name__ == "__main__":
    unittest.main()
