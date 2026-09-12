import re
import unittest

import module.webui.lang as lang
from module.webui.app_stat_commission import CommissionIncomeStatisticsMixin


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


if __name__ == "__main__":
    unittest.main()
