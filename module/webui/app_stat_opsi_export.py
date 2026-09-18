"""WebUI 短猫收获月度视图（本月耄耋相接收获）。"""

from module.webui.app_dependencies import (
    close_popup,
    current_time,
    datetime,
    popup,
    put_button,
    put_buttons,
    put_html,
    put_row,
    put_scope,
    put_text,
    t,
    toast,
    use_scope,
)

from module.webui.app_helpers import (
    build_simple_table,
    build_title_block,
)
from module.webui.stat_icon import refresh_icon_button_css


from module.webui.app_types import WebUIMixinBase


# 标题旁刷新图标按钮的作用域名，与 entry-alas.css 的规则配套
_MEOW_REFRESH_SCOPE = "meow_loot_refresh"


class OpsiExportMixin(WebUIMixinBase):
    """WebUI 短猫收获月度视图（本月耄耋相接收获）。"""

    def _render_meowofficer_farming(self):
        from module.statistics.azurstats import AzurStats
        from module.statistics.cl1_database import db as cl1_db

        # 只读渲染：开启 get_stats 缓存。本板块会分别取侵蚀 3 / 5 的耄耋统计，
        # 每次都要反序列化整个月度 JSON（实测 2~4 MB），缓存后只解析一次。
        with cl1_db.read_cache(), use_scope("meow_loot_scope", clear=True):
            # 只保留「本月耄耋相接收获」这一个标题（由 _render_monthly_meow_loot
            # 渲染，历史月份下自动变成「历史耄耋相接收获（YYYY-MM）」），
            # 不再另起一层板块标题 —— 两层标题叠在一起没有信息量。
            # 「上次记录时间」紧跟在标题下方、表格上方，与主表汇总行同一形式；
            # 它不随月份切换变化，所以渲染在 _render_monthly_meow_loot 之外：
            # 选历史月份时只有下面那张表会重绘。
            self._render_monthly_meow_loot(AzurStats)
            # 刷新按钮：作用域已由 _render_monthly_meow_loot 的标题行建好
            # （put_row 里的 put_scope），这里只往里面渲染按钮。
            # 整块重绘会连带清掉旧 DOM，因此作用域每轮重建是安全的；
            # 反过来若把 put_scope 放进 _render_monthly_meow_loot，切月份时
            # 同名元素仍在 DOM 里，PyWebIO 会插入灰条报错（duplicated_scope_name）。
            put_button(
                "",
                onclick=self._render_meowofficer_farming,
                color="off",
                scope=_MEOW_REFRESH_SCOPE,
            )
            put_html(refresh_icon_button_css(_MEOW_REFRESH_SCOPE))

    def _load_meow_cumulative_rows(self, AzurStats):
        """读取累计表的有效行（只保留有效战斗轮数 > 0 的侵蚀等级）。

        Returns:
            list: ``[(侵蚀等级, 上次记录时间戳, 有效战斗轮数, 平均黄币/轮,
            平均金菜/轮, 平均深渊/轮, 平均隐秘/轮), ...]``，读不到时返回空列表。
        """
        rows = []
        try:
            for row in AzurStats.load_meowofficer_farming():
                if float(row[2]) > 0:
                    rows.append([float(v) for v in row])
        except Exception:
            return []
        return rows

    def _meow_extra_columns(self, AzurStats):
        """累计表里要并入本月表的那些列，按侵蚀等级取用。

        Returns:
            dict: ``{侵蚀等级: [有效战斗轮数, 平均黄币/轮, 平均金菜/轮,
            平均深渊/轮, 平均隐秘/轮]}``，读不到时返回空字典。
        """
        return {
            int(row[0]): [
                # 轮数原始值是浮点，取整展示
                int(round(row[2])),
                # 四个平均值保留 6 位小数：数值本身很小（如 0.002），
                # 位数不够时看不出差异
                *(f"{v:.6f}" for v in row[3:7]),
            ]
            for row in self._load_meow_cumulative_rows(AzurStats)
        }

    def _render_meow_loot_last_record(self, AzurStats):
        """汇总行「上次记录时间」：取各侵蚀等级里最新的一次记录时间。"""
        rows = self._load_meow_cumulative_rows(AzurStats)
        latest = max((row[1] for row in rows), default=0)
        text = (
            datetime.fromtimestamp(latest).strftime("%Y-%m-%d %H:%M:%S")
            if latest > 0
            else "-"
        )
        put_row(
            [put_text(t("Gui.Stat.MeowLastRecord", value=text))],
        ).style("--meow-summary--")

    def _render_monthly_meow_loot(self, AzurStats):
        """渲染「本月/历史耄耋相接收获」表格（按侵蚀等级分列的月度掉落总数）。"""
        view_month = getattr(self, "_meow_loot_month", None)
        if view_month is None:
            now = current_time()
            year, month = now.year, now.month
            title = "本月耄耋相接收获"
        else:
            year, month = view_month
            title = f"历史耄耋相接收获（{year:04d}-{month:02d}）"
        month_str = f"{year:04d}-{month:02d}"

        loot_totals = AzurStats.get_meow_loot_monthly_totals(year=year, month=month)
        from module.statistics.cl1_database import db as cl1_db

        instance_name = getattr(self, "alas_name", None)
        if not instance_name:
            from module.config.utils import alas_instance

            all_instances = alas_instance()
            instance_name = all_instances[0] if all_instances else "default"

        rows = []
        for hazard_level in (3, 5):
            loot = loot_totals.get(hazard_level, {})
            # 战斗轮次：与数据收集表一致的有效轮次口径
            try:
                meow_data = cl1_db.get_meow_stats(
                    instance_name, year, month, hazard_level=hazard_level
                )
                rounds = round(float(meow_data.get("effective_rounds", 0) or 0), 1)
                if abs(rounds - int(rounds)) < 1e-6:
                    rounds = int(rounds)
            except Exception:
                rounds = 0
            rows.append(
                [
                    month_str,
                    hazard_level,
                    rounds,
                    int(loot.get("Plate", 0) or 0),
                    int(loot.get("GearDesignPlanT5", 0) or 0),
                    int(loot.get("OrdnanceTestingReportT4", 0) or 0),
                    int(loot.get("CoordinateObscure", 0) or 0),
                    int(loot.get("CoordinateAbyssal", 0) or 0),
                    int(loot.get("CatT3", 0) or 0),
                ]
            )

        # 追加累计表的 5 列（有效战斗轮数 + 四项「平均每轮」），按侵蚀等级取值。
        # 这些列是累计值、不随所选月份变化，但和本月数据同属一个侵蚀等级，
        # 放在同一张表里对照更直接。
        extra = self._meow_extra_columns(AzurStats)
        empty_extra = ["-"] * 5
        for row in rows:
            row.extend(extra.get(int(row[1]), empty_extra))

        # 月份切换按钮紧跟在标题右侧（标题列自适应内容宽度，按钮列吃掉剩余空间，
        # 因此按钮不会被推到最右边）；按钮统一用 color="off"，
        # 外观与其它统计按钮一致，由 entry-alas.css 的统一样式收口。
        # 标题用 .stat-row-title 与按钮同高，保证两者在同一行内中心对齐。
        # 已经在本月时不再显示「回到本月」。
        buttons = [{"label": "查看历史月份", "value": "history", "color": "off"}]
        if view_month is not None:
            buttons.append({"label": "回到本月", "value": "current", "color": "off"})
        # 刷新图标按钮紧跟月份按钮之后。作用域只在整块重绘的入口建一次：
        # PyWebIO 的 put_scope 在「同名元素已存在」时会插入灰条报错
        # （duplicated_scope_name），而本函数会因切月份/刷新被再次调用，
        # 所以不能放在这里建。
        put_row(
            [
                put_html(
                    build_title_block(
                        title,
                        margin_top=0,
                        margin_bottom=0,
                        class_name="stat-row-title",
                    )
                ),
                put_buttons(
                    buttons,
                    onclick=self._on_meow_loot_month_click,
                ),
                put_scope(_MEOW_REFRESH_SCOPE, []),
            ],
            size="auto auto 1fr",
        ).style(
            "align-items:center; gap:10px; margin-top:24px; margin-bottom:8px"
        )
        # 记录时间放在标题与表格之间（与主表汇总行同一形式）
        self._render_meow_loot_last_record(AzurStats)

        # 月份与掉落列（本月数据）+ 历史累计列（不随所选月份变化）
        put_html(
            build_simple_table(
                [
                    t("Gui.Stat.Month"),
                    t("Gui.Stat.HazardLevel"),
                    t("Gui.Stat.BattleRounds"),
                    "金菜",
                    "彩图纸",
                    "金机密",
                    "隐秘",
                    "深渊",
                    "金猫箱",
                    t("Gui.Stat.MeowEffectiveRounds"),
                    t("Gui.Stat.MeowAvgOperationCoin"),
                    t("Gui.Stat.MeowAvgPlate"),
                    t("Gui.Stat.MeowAvgAbyssal"),
                    t("Gui.Stat.MeowAvgObscure"),
                ],
                rows,
            )
        )

    def _on_meow_loot_month_click(self, value):
        """月份切换按钮回调：history 打开历史月份选择器，current 回到本月。"""
        if value == "history":
            self._show_meow_loot_month_picker()
        else:
            self._reset_meow_loot_month()

    def _show_meow_loot_month_picker(self):
        """弹出历史月份选择器。"""
        from module.statistics.azurstats import AzurStats

        now = current_time()
        months = AzurStats.get_meow_loot_available_months()
        buttons = [
            {
                "label": f"本月（{now.year:04d}-{now.month:02d}）",
                "value": None,
                "color": "primary",
            }
        ]
        buttons += [
            {"label": f"{y:04d}-{m:02d}", "value": (y, m), "color": "off"}
            for y, m in months
            if (y, m) != (now.year, now.month)
        ]
        if len(buttons) == 1:
            toast("暂无历史月份数据")
            return

        with popup("选择查看月份"):
            put_buttons(buttons, onclick=lambda v: self._set_meow_loot_month(v))

    def _set_meow_loot_month(self, value):
        """设置要查看的月份并重绘收获表格。value 为 None 表示本月。"""
        close_popup()
        self._meow_loot_month = value
        self._render_meowofficer_farming()

    def _reset_meow_loot_month(self):
        """回到本月视图。"""
        self._meow_loot_month = None
        self._render_meowofficer_farming()

