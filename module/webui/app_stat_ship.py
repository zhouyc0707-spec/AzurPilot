"""WebUI 舰船经验统计视图。"""

from module.webui.app_dependencies import (
    alas_instance,
    put_button,
    put_html,
    put_row,
    put_text,
    t,
    use_scope,
)

from module.webui.app_helpers import (
    build_muted_notice,
    build_simple_table,
)
from module.webui.stat_icon import (
    build_title_icon_row,
    refresh_icon_button_css,
)


from module.webui.app_types import WebUIMixinBase


# 标题旁的刷新图标按钮作用域名，与 entry-alas.css 的规则配套
_SHIP_EXP_REFRESH_SCOPE = "ship_exp_refresh"


class ShipExperienceStatisticsMixin(WebUIMixinBase):
    """WebUI 舰船经验统计视图。"""

    def _render_ship_exp(self):
        try:
            from module.statistics.ship_exp_stats import get_ship_exp_stats
            from module.statistics.opsi_month import (
                get_opsi_stats as get_opsi_stats_func,
            )

            # 使用当前实例名称获取统计数据，确保不为空
            instance_name = getattr(self, "alas_name", None)
            if not instance_name:
                # 使用第一个可用的实例
                from module.config.utils import alas_instance

                all_instances = alas_instance()
                instance_name = all_instances[0] if all_instances else None
            stats = get_ship_exp_stats(instance_name=instance_name)
            if not stats.data or not stats.data.get("ships"):
                with use_scope("ship_exp_table", clear=True):
                    put_html(build_muted_notice(t("Gui.Stat.NoShipExpData")))
                return

            current_battles = (
                get_opsi_stats_func(instance_name=instance_name)
                .summary()
                .get("total_battles", 0)
            )
            target_level = stats.data.get("target_level", 125)
            exp_per_hour = stats.get_exp_per_hour()
            today_stats = stats.get_today_stats()

            # 从daily_stats获取今日战斗场次
            today_battles = today_stats.get("battle_count", 0) if today_stats else 0

            labels = [
                t("Gui.Stat.ShipSlot"),
                t("Gui.Stat.Level"),
                t("Gui.Stat.CurrentExpThisLevel"),
                t("Gui.Stat.TotalExp"),
                t("Gui.Stat.TargetExpRequired"),
                t("Gui.Stat.BattlesCompleted"),
                t("Gui.Stat.ExpRemaining"),
                t("Gui.Stat.SortiesNeeded"),
                t("Gui.Stat.EstimatedTime"),
            ]

            rows = []
            for ship in stats.data.get("ships", []):
                progress = stats.calculate_progress(ship, target_level, current_battles)
                # 使用今日daily_stats的battle_count作为已战斗场次
                rows.append(
                    [
                        progress["position"],
                        progress["level"],
                        progress["current_exp"],
                        progress["total_exp"],
                        progress["target_exp"],
                        today_battles,  # 使用今日battle_count而非计算值
                        progress["exp_needed"],
                        progress["battles_needed"],
                        progress["time_needed"],
                    ]
                )

            with use_scope("ship_exp_table", clear=True):
                # 标题行：标题在左、刷新图标紧邻其右；图标按钮渲染进预留的 scope
                put_html(
                    build_title_icon_row(
                        t("Gui.Stat.ShipExpProgressTitle"),
                        _SHIP_EXP_REFRESH_SCOPE,
                    )
                )
                put_button(
                    "",
                    onclick=self._render_ship_exp,
                    color="off",
                    scope=_SHIP_EXP_REFRESH_SCOPE,
                )
                put_html(refresh_icon_button_css(_SHIP_EXP_REFRESH_SCOPE))

                # 汇总一行：上次检查时间 + 今日经验 / 经验效率 / 今日运行。
                # 「今日战斗」不再显示 —— 下面表格里已有战斗场次，重复了；
                # 也不再单独占一行（原来检查时间与统计各占一行，行数偏多）。
                summary_items = [
                    put_text(
                        t(
                            "Gui.Stat.LastCheckTime",
                            value=stats.data.get("last_check_time", "-"),
                        )
                    )
                ]
                if today_stats:
                    run_minutes = int(today_stats.get("total_run_time", 0) // 60)
                    summary_items += [
                        put_text(
                            t(
                                "Gui.Stat.TodayExp",
                                value=today_stats.get("total_exp_gained", 0),
                            )
                        ),
                        put_text(
                            t(
                                "Gui.Stat.ExpEfficiency",
                                value=f"{exp_per_hour:.0f}",
                                unit=t("Gui.Stat.HourUnit"),
                            )
                        ),
                        put_text(
                            t(
                                "Gui.Stat.TodayRun",
                                value=run_minutes,
                                unit=t("Gui.Stat.MinuteUnit"),
                            )
                        ),
                    ]
                # size 必须显式给：put_row 不传 size 时 PyWebIO 会把每一项都设成
                # 1fr（等分整行），短文字只占列宽的一半，多出来的空白看起来就是
                # 巨大的间距 —— 之前只调 column-gap 没效果，主因在这里。
                # 改成「按内容取宽 + 末尾 1fr 吸收剩余空间」，各项自然靠拢。
                put_row(
                    summary_items,
                    size=" ".join(["auto"] * len(summary_items)) + " 1fr",
                ).style("--ship-exp-summary--")
                if not today_stats:
                    put_text(t("Gui.Stat.NoTodayBattleData"))

                put_html(
                    build_simple_table(labels, rows, extra_style=" margin-top:8px;")
                )
        except Exception as e:
            with use_scope("ship_exp_table", clear=True):
                put_text(t("Gui.Stat.LoadShipExpFailed", e=e))
