"""WebUI 大世界统计视图。"""

from module.webui.app_dependencies import (
    current_time,
    put_html,
    put_row,
    put_scope,
    put_text,
    t,
    time,
    use_scope,
)

from module.webui.app_helpers import (
    build_muted_notice,
    build_simple_table,
    build_stat_section_title,
)


from module.webui.app_types import WebUIMixinBase


class OpsiStatisticsMixin(WebUIMixinBase):
    """WebUI 大世界统计视图。"""

    def _render_opsi_stats(self):
        dependencies = self._load_opsi_stats_dependencies()
        if dependencies is None:
            return

        (
            instance_name,
            summary,
            cl1_db,
            compute_monthly_cl1_akashi_ap,
            get_ship_exp_stats,
        ) = dependencies
        exp_data = self._load_ship_exp_data(get_ship_exp_stats, instance_name)
        if exp_data is None:
            return

        exp_stats, ships_data, target_level, last_check_time = exp_data
        self._render_daily_exp_stats(
            instance_name,
            exp_stats,
            ships_data,
            target_level,
            last_check_time,
        )
        labels, values, ap_bought, loop_eff = self._build_cl1_summary(
            instance_name,
            summary,
            compute_monthly_cl1_akashi_ap,
            get_ship_exp_stats,
        )
        # 把侵蚀1 与耄耋相接的数据合成一张按侵蚀等级分行的表
        month = summary.get("month", "-")
        meow_by_level = self._build_meow_stats_by_level(cl1_db, instance_name)
        labels, rows = self._build_hazard_rows(
            labels, values, meow_by_level, month
        )
        self._render_opsi_summary(labels, rows, ap_bought, loop_eff)

    def _load_opsi_stats_dependencies(self):
        try:
            from module.statistics.opsi_month import (
                get_opsi_stats,
                compute_monthly_cl1_akashi_ap,
            )
            from module.statistics.cl1_database import db as cl1_db
            from module.statistics.ship_exp_stats import get_ship_exp_stats

            instance_name = getattr(self, "alas_name", None)
            if not instance_name:
                from module.config.utils import alas_instance

                all_instances = alas_instance()
                instance_name = all_instances[0] if all_instances else None
            summary = get_opsi_stats(instance_name=instance_name).summary()
        except Exception as e:
            with use_scope("opsi_stats", clear=True):
                put_text(t("Gui.Stat.LoadOpsiStatsFailed", e=e))
            return None

        return (
            instance_name,
            summary,
            cl1_db,
            compute_monthly_cl1_akashi_ap,
            get_ship_exp_stats,
        )

    def _load_ship_exp_data(self, get_ship_exp_stats, instance_name):
        try:
            exp_stats = get_ship_exp_stats(instance_name=instance_name)
            exp_data = exp_stats.data
            ships_data = exp_data.get("ships", []) if exp_data else []
            target_level = exp_data.get("target_level", 125) if exp_data else 125
            last_check_time = exp_data.get("last_check_time", "-") if exp_data else "-"
        except Exception as e:
            with use_scope("opsi_stats", clear=True):
                put_text(t("Gui.Stat.LoadExpStatsFailed", e=e))
            return None

        return exp_stats, ships_data, target_level, last_check_time

    def _render_daily_exp_stats(
        self, instance_name, exp_stats, ships_data, target_level, last_check_time
    ):
        with use_scope("opsi_stats", clear=True):
            put_html(build_stat_section_title(t("Gui.Stat.DailyExpCheckTitle")))
            put_row(
                [
                    put_text(t("Gui.Stat.CheckTime", value=last_check_time)),
                    put_text(t("Gui.Stat.TargetLevel", value=target_level)),
                ]
            )
            if ships_data:
                exp_labels = [
                    t("Gui.Stat.ShipSlot"),
                    t("Gui.Stat.Level"),
                    t("Gui.Stat.CurrentExpThisLevel"),
                    t("Gui.Stat.TotalExp"),
                    t("Gui.Stat.ExpToTarget"),
                    t("Gui.Stat.SortiesNeeded"),
                    t("Gui.Stat.EstimatedTime"),
                ]
                exp_rows = []
                from module.statistics.opsi_month import (
                    get_opsi_stats as get_opsi_stats_inner,
                )

                current_battles = (
                    get_opsi_stats_inner(instance_name=instance_name)
                    .summary()
                    .get("total_battles", 0)
                )
                for ship in ships_data:
                    progress = exp_stats.calculate_progress(
                        ship, target_level, current_battles
                    )
                    exp_rows.append(
                        [
                            progress["position"],
                            progress["level"],
                            progress["current_exp"],
                            progress["total_exp"],
                            progress["exp_needed"]
                            if progress["exp_needed"] > 0
                            else "-",
                            progress["battles_needed"]
                            if progress["battles_needed"] > 0
                            else "-",
                            progress["time_needed"],
                        ]
                    )

                put_html(build_simple_table(exp_labels, exp_rows))
            else:
                put_html(build_muted_notice(t("Gui.Stat.NoExpData")))

    def _build_cl1_summary(
        self,
        instance_name,
        summary,
        compute_monthly_cl1_akashi_ap,
        get_ship_exp_stats,
    ):
        month = summary.get("month", "-")
        total = summary.get("total_battles", "-")
        try:
            tb = int(total)
            rounds = (tb + 1) // 2
            sortie_cost = rounds * 5
        except Exception:
            tb = total
            rounds = "-"
            sortie_cost = "-"

        akashi = summary.get("akashi_encounters", 0)
        try:
            ak = int(akashi)
        except Exception:
            ak = akashi

        try:
            if isinstance(rounds, int) and rounds > 0:
                rate = float(ak) / float(rounds)
                akashi_rate = f"{rate * 100:.2f}%"
            else:
                akashi_rate = "-"
        except Exception:
            akashi_rate = "-"

        try:
            siren_research = int(summary.get("siren_research_devices", 0) or 0)
        except Exception:
            siren_research = 0

        try:
            if isinstance(rounds, int) and rounds > 0:
                siren_research_rate = f"{siren_research / float(rounds) * 100:.2f}%"
            else:
                siren_research_rate = "-"
        except Exception:
            siren_research_rate = "-"

        try:
            ap_bought = compute_monthly_cl1_akashi_ap(instance_name=instance_name)
        except Exception:
            ap_bought = "-"

        try:
            if isinstance(ap_bought, (int, float)) and isinstance(ak, int) and ak > 0:
                avg_ap = int(float(ap_bought) / ak + 0.5)
            else:
                try:
                    ap_tmp = int(ap_bought)
                    if isinstance(ak, int) and ak > 0:
                        avg_ap = int(ap_tmp / ak + 0.5)
                    else:
                        avg_ap = "-"
                except Exception:
                    avg_ap = "-"
        except Exception:
            avg_ap = "-"

        try:
            net_ap = int(ap_bought) - int(sortie_cost)
        except Exception:
            net_ap = "-"

        try:
            eff = int(net_ap) / int(sortie_cost) * 100
            loop_eff = f"{eff:.2f}%"
        except Exception:
            loop_eff = "-"

        # 获取侵蚀1的平均时长
        try:
            exp_stats = get_ship_exp_stats(instance_name=instance_name)
            avg_cl1_battle_time = exp_stats.get_average_battle_time()
            avg_cl1_round_time = exp_stats.get_average_round_time()

            avg_cl1_battle_str = f"{avg_cl1_battle_time:.1f}{t('Gui.Stat.SecondUnit')}"
            avg_cl1_round_str = f"{avg_cl1_round_time:.1f}{t('Gui.Stat.SecondUnit')}"
        except Exception:
            avg_cl1_battle_str = "-"
            avg_cl1_round_str = "-"

        labels = [
            t("Gui.Stat.Month"),
            t("Gui.Stat.BattleCount"),
            t("Gui.Stat.BattleRounds"),
            t("Gui.Stat.SortieCost"),
            t("Gui.Stat.AkashiEncounters"),
            t("Gui.Stat.AkashiRate"),
            t("Gui.Stat.AverageAP"),
            t("Gui.Stat.NetAP"),
            t("Gui.Stat.SirenResearchDevices"),
            t("Gui.Stat.SirenResearchRate"),
            t("Gui.Stat.AvgBattleTimeHeader"),
            t("Gui.Stat.AvgRoundTime"),
        ]

        # 循环效率不进表格：它是侵蚀1 独有的口径（侵蚀3/5 不按每轮行动力核算），
        # 放在表格里对 3/5 行只能留空，改到汇总行单独显示（见 _render_opsi_summary）
        values = [
            month,
            tb,
            rounds,
            sortie_cost,
            ak,
            akashi_rate,
            avg_ap,
            net_ap,
            siren_research,
            siren_research_rate,
            avg_cl1_battle_str,
            avg_cl1_round_str,
        ]

        return labels, values, ap_bought, loop_eff

    # 三行表里各侵蚀等级每轮的行动力消耗，既用于算「净赚体力」，
    # 也用于算「出击消耗 = 每轮消耗 × 出击轮次」
    # （侵蚀1 的一轮 = 2 场战斗，每场 5 点，见 _build_cl1_summary）
    AP_COST_PER_ROUND = {1: 5, 3: 15, 5: 30}
    # 三行表的行序（与页面展示顺序一致）
    HAZARD_ROW_ORDER = (1, 5, 3)
    # 该格无数据时的占位（与既有约定一致的 ASCII 短横）
    DASH = "-"

    def _build_meow_stats_by_level(self, cl1_db, instance_name):
        """按侵蚀等级汇总耄耋相接数据，供三行表按等级取用。

        Args:
            cl1_db: 侵蚀一/耄耋相接数据库实例。
            instance_name: 实例名。

        Returns:
            dict: ``{侵蚀等级: 指标字典}``，取不到数据时返回空字典。
        """
        stats = {}
        try:
            now = current_time()
            for hazard_level in (3, 5):
                data = cl1_db.get_meow_stats(
                    instance_name or "default",
                    now.year,
                    now.month,
                    hazard_level=hazard_level,
                )
                rounds = float(data.get("effective_rounds", 0) or 0)
                encounters = int(data.get("akashi_encounters", 0) or 0)
                akashi_ap = int(data.get("akashi_ap", 0) or 0)
                battles = int(data.get("battle_count", 0) or 0)
                avg_battle_time = float(data.get("avg_battle_time", 0.0) or 0)
                avg_round_time = float(data.get("avg_round_time", 0.0) or 0)
                siren_count = int(data.get("siren_research_devices", 0) or 0)
                stats[hazard_level] = {
                    "battle_count": battles,
                    "rounds": rounds,
                    "akashi_encounters": encounters,
                    "akashi_rate": (
                        f"{encounters / rounds * 100:.2f}%" if rounds > 0 else "-"
                    ),
                    "avg_ap": (
                        str(int(akashi_ap / encounters + 0.5))
                        if encounters > 0
                        else "-"
                    ),
                    "net_ap": int(
                        round(
                            akashi_ap
                            - rounds * self.AP_COST_PER_ROUND.get(hazard_level, 0)
                        )
                    ),
                    "siren_devices": siren_count,
                    "siren_rate": (
                        f"{siren_count / rounds * 100:.2f}%" if rounds > 0 else "-"
                    ),
                    "avg_battle_time": (
                        f"{avg_battle_time:.1f}{t('Gui.Stat.SecondUnit')}"
                        if avg_battle_time > 0
                        else "-"
                    ),
                    "avg_round_time": (
                        f"{avg_round_time:.1f}{t('Gui.Stat.SecondUnit')}"
                        if avg_round_time > 0
                        else "-"
                    ),
                }
        except Exception:
            return {}
        return stats

    def _build_hazard_rows(self, cl1_labels, cl1_values, meow_by_level, month):
        """把侵蚀1 与耄耋相接的数据按侵蚀等级合成三行。

        「雪风大人的大世界数据收集」是一张按侵蚀等级分行的表：侵蚀等级 1 用侵蚀1
        的数据，3 / 5 用耄耋相接的数据。表格在最前面插入「侵蚀等级」列。
        出击消耗对三行都算：每轮行动力消耗 × 出击轮次（侵蚀1 每轮 5、侵蚀3 每轮
        15、侵蚀5 每轮 30）。循环效率不在这张表里 —— 它是侵蚀1 独有的口径，
        放在表里对 3/5 行只能留空，因此改由汇总行显示。

        Args:
            cl1_labels: 侵蚀1 表的列名（不含侵蚀等级）。
            cl1_values: 侵蚀1 那一行的值。
            meow_by_level: ``_build_meow_stats_by_level`` 的结果。
            month: 月份，填进「月份」列。

        Returns:
            tuple: ``(labels, rows)`` —— 插入侵蚀等级列后的列名与三行数据，
            行序见 ``HAZARD_ROW_ORDER``。
        """
        hazard_label = t("Gui.Stat.HazardLevel")
        month_label = t("Gui.Stat.Month")
        # 侵蚀1 的列里本来就有「月份」，只保留最前面那一列，避免重复
        body_labels = [label for label in cl1_labels if label != month_label]
        labels = [month_label, hazard_label, *body_labels]
        cl1_row = dict(zip(cl1_labels, cl1_values))

        meow_columns = {
            t("Gui.Stat.BattleCount"): "battle_count",
            t("Gui.Stat.AkashiEncounters"): "akashi_encounters",
            t("Gui.Stat.AkashiRate"): "akashi_rate",
            t("Gui.Stat.AverageAP"): "avg_ap",
            t("Gui.Stat.NetAP"): "net_ap",
            t("Gui.Stat.SirenResearchDevices"): "siren_devices",
            t("Gui.Stat.SirenResearchRate"): "siren_rate",
            t("Gui.Stat.AvgBattleTimeHeader"): "avg_battle_time",
            t("Gui.Stat.AvgMeowRoundTime"): "avg_round_time",
        }
        rounds_label = t("Gui.Stat.BattleRounds")

        rows = []
        sortie_cost_label = t("Gui.Stat.SortieCost")
        dash = self.DASH
        for hazard_level in self.HAZARD_ROW_ORDER:
            row = [month, hazard_level]
            if hazard_level == 1:
                # 侵蚀1 行严格按 cl1_labels 取值：cl1_row 就是按它建的，
                # 若改成遍历去重后的 labels，一旦两者列集不同就会漏值导致错位
                for label in cl1_labels:
                    if label != month_label:
                        row.append(cl1_row.get(label, dash))
                rows.append(row)
                continue
            data = meow_by_level.get(hazard_level)
            for label in body_labels:
                if label == rounds_label:
                    # 出击轮次直接取耄耋相接的有效轮次
                    row.append((data or {}).get("rounds", dash))
                elif label == sortie_cost_label:
                    # 出击消耗 = 每轮行动力消耗 × 出击轮次
                    rounds = float((data or {}).get("rounds", 0) or 0)
                    cost_per_round = self.AP_COST_PER_ROUND.get(hazard_level, 0)
                    row.append(
                        int(round(rounds * cost_per_round))
                        if rounds > 0 and cost_per_round > 0
                        else dash
                    )
                elif label in meow_columns and data:
                    row.append(data[meow_columns[label]])
                else:
                    # 该等级无数据，或该列在耄耋相接里不统计
                    row.append(dash)
            rows.append(row)
        return labels, rows

    def _render_opsi_summary(self, labels, rows, ap_bought, loop_eff):
        with use_scope("opsi_stats", clear=True):
            put_html(build_stat_section_title(t("Gui.Stat.OpsiDataCollectionTitle")))
            # 三条汇总并排一行：当月侵蚀一购买体力 / 出击消耗 / 循环效率
            #（后两者与表格「出击消耗」列同一口径：(场次+1)//2×5；
            #  循环效率只对侵蚀1 有意义，因此从表格搬到这一行）
            try:
                sortie_cost_total = (
                    int(rows[0][labels.index(t("Gui.Stat.BattleCount"))]) + 1
                ) // 2 * 5
            except Exception:
                sortie_cost_total = "-"
            put_row(
                [
                    put_text(t("Gui.Stat.MonthlyPurchasedAP", value=ap_bought)),
                    # None 是 put_row 的「间距」占位项，配合 size 里的 24px
                    # 把三条汇总分开（put_row 内部是 grid，列宽由 size 决定，
                    # CSS 里不要再改 display，否则 grid-template-columns 失效）
                    None,
                    put_text(t("Gui.Stat.MonthlySortieCost", value=sortie_cost_total)),
                    None,
                    put_text(t("Gui.Stat.MonthlyLoopEfficiency", value=loop_eff)),
                ],
                size="auto 24px auto 24px 1fr",
            ).style("--opsi-summary--")
            put_html(build_simple_table(labels, rows))

            put_scope("meow_loot_scope")

            self._render_meowofficer_farming()
