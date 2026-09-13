"""WebUI 短猫收获月度视图（耄耋相接收获）。"""

from module.webui.app_dependencies import (
    close_popup,
    current_time,
    datetime,
    popup,
    put_buttons,
    put_html,
    put_row,
    t,
    toast,
    use_scope,
)

from module.webui.app_helpers import (
    build_muted_notice,
    build_simple_table,
    build_stat_section_title,
)


from module.webui.app_types import WebUIMixinBase


class OpsiExportMixin(WebUIMixinBase):
    """WebUI 短猫收获月度视图（耄耋相接收获）。"""

    def _render_meowofficer_farming(self):
        from module.statistics.azurstats import AzurStats

        with use_scope("meow_loot_scope", clear=True):
            self._render_monthly_meow_loot(AzurStats)

            all_data = AzurStats.load_meowofficer_farming()
            meow_rows = []
            for row in all_data:
                if row[2] > 0:
                    meow_row = [
                        int(row[0]),
                        datetime.fromtimestamp(row[1]).strftime("%Y-%m-%d %H:%M:%S"),
                        int(row[2]),
                    ] + list(row[3:])

                    meow_rows.append(meow_row)

            put_html(build_stat_section_title(t("Gui.Stat.MeowLootTitle")))
            if meow_rows:
                put_html(
                    build_simple_table(AzurStats.meowofficer_farming_labels, meow_rows)
                )
            else:
                put_html(build_muted_notice(t("Gui.Stat.NoMeowDataNotice")))

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

        # 月份切换按钮紧跟在标题右侧（标题列自适应内容宽度，按钮列吃掉剩余空间，
        # 因此按钮不会被推到最右边）；按钮统一用 color="off"，
        # 外观与其它统计按钮一致，由 entry-alas.css 的统一样式收口。
        # 标题用 .stat-row-title 与按钮同高，保证两者在同一行内中心对齐。
        # 已经在本月时不再显示「回到本月」。
        buttons = [{"label": "查看历史月份", "value": "history", "color": "off"}]
        if view_month is not None:
            buttons.append({"label": "回到本月", "value": "current", "color": "off"})
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
            ],
            size="auto 1fr",
        ).style(
            "align-items:center; gap:10px; margin-top:24px; margin-bottom:8px"
        )
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

