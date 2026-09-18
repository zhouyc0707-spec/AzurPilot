"""WebUI 大世界统计视图。"""

from html import escape

from module.webui.app_dependencies import (
    current_time,
    put_button,
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
from module.webui.stat_icon import (
    build_title_icon_row,
    refresh_icon_button_css,
)


from module.webui.app_types import WebUIMixinBase


# 标题旁刷新图标按钮的作用域名，与 entry-alas.css 的规则配套
_OPSI_REFRESH_SCOPE = "opsi_stats_refresh"


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
        # 本轮渲染只读：开启 get_stats 缓存。一次渲染会经由多条路径重复读取同一个
        # 月份的 blob（get_meow_stats 每个侵蚀等级一次等），而每次读取都要把整个
        # 月度 JSON（实测 2~4 MB）反序列化一遍，单次 17~34 ms、累计 150~200 ms。
        # 缓存只在本方法内有效，退出即清空。
        with cl1_db.read_cache():
            self._render_opsi_stats_locked(
                instance_name,
                summary,
                cl1_db,
                compute_monthly_cl1_akashi_ap,
                get_ship_exp_stats,
            )

    def _render_opsi_stats_locked(
        self,
        instance_name,
        summary,
        cl1_db,
        compute_monthly_cl1_akashi_ap,
        get_ship_exp_stats,
    ):
        """``_render_opsi_stats`` 的主体（在只读缓存作用域内执行）。"""
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
        labels, values, ap_bought, net_ap, loop_eff = self._build_cl1_summary(
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
        self._render_opsi_summary(labels, rows, ap_bought, net_ap, loop_eff)

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
            t("Gui.Stat.SirenResearchDevices"),
            t("Gui.Stat.SirenResearchRate"),
            t("Gui.Stat.AvgBattleTimeHeader"),
            t("Gui.Stat.AvgRoundTime"),
        ]

        # 循环效率与净赚体力都不进表格：两者都是侵蚀1 独有的口径（侵蚀3/5 不按
        # 每轮行动力核算），放在表格里对 3/5 行只能留空，改由汇总行单独显示
        values = [
            month,
            tb,
            rounds,
            sortie_cost,
            ak,
            akashi_rate,
            avg_ap,
            siren_research,
            siren_research_rate,
            avg_cl1_battle_str,
            avg_cl1_round_str,
        ]

        return labels, values, ap_bought, net_ap, loop_eff

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
        15、侵蚀5 每轮 30）。净赚体力与循环效率都不在这张表里 —— 两者都是侵蚀1
        独有的口径，放在表里对 3/5 行只能留空，因此统一由汇总行显示。

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

    # 汇总行正负着色的色值。刻意不跟随主题主色 —— 红涨绿跌是语义色，四个主题下
    # 都应保持红绿；但深浅主题族需要的明度不同（深绿在深色底上几乎看不见）。
    # 直接内联在这里，不走主题 CSS 变量：变量散在四个主题文件里，漏掉任何一个
    # （默认的 light-alas.css 就漏过）都会让 color 解析失败、静默回退成继承色。
    SUMMARY_GAIN_LIGHT = "#d32f2f"
    SUMMARY_LOSS_LIGHT = "#00897b"
    SUMMARY_GAIN_DARK = "#ef5350"
    SUMMARY_LOSS_DARK = "#26a69a"

    @classmethod
    def summary_sign_class(cls, raw_value):
        """按数值正负返回汇总项要用的类名，无法判定正负时返回空串。

        0 与 ``-``（无数据）都不着色，保持常规文字色。

        Args:
            raw_value: 可能是数字、数字字符串（含百分号）或 ``-``。

        Returns:
            str: ``alas-summary-gain`` / ``alas-summary-loss`` / ``""``。
        """
        try:
            # 循环效率是带百分号的字符串（如 "35.26%"），去掉后缀再解析
            number = float(str(raw_value).strip().rstrip("%"))
        except (TypeError, ValueError):
            return ""
        if number > 0:
            return "alas-summary-gain"
        if number < 0:
            return "alas-summary-loss"
        return ""

    @classmethod
    def summary_style_html(cls):
        """汇总行正负着色的 ``<style>`` 片段。

        浅色一套、深色一套，由主题选择器切换，因此切主题不需要重新渲染。
        深浅主题的标记方式与 ``stat_icon.refresh_icon_button_css`` 保持一致
        （``body.webio-theme-dark`` 与 ``html[data-theme='dark']`` 都覆盖）。

        Returns:
            str: 一段 ``<style>`` HTML。
        """
        scope = "#pywebio-scope-opsi_stats"
        rules = (
            ("alas-summary-gain", cls.SUMMARY_GAIN_LIGHT, cls.SUMMARY_GAIN_DARK),
            ("alas-summary-loss", cls.SUMMARY_LOSS_LIGHT, cls.SUMMARY_LOSS_DARK),
        )
        parts = []
        for name, light, dark in rules:
            parts.append(f"{scope} .{name}{{color:{light} !important;}}")
            parts.append(
                f"body.webio-theme-dark {scope} .{name},"
                f"html[data-theme='dark'] {scope} .{name}"
                f"{{color:{dark} !important;}}"
            )
        return "<style>" + "".join(parts) + "</style>"

    def _summary_item_html(self, key, raw_value, signed=False):
        """生成一条汇总项的 HTML。

        ``signed=True`` 时只有**数值**染色，标签文字保持默认色 —— 整条染色会让
        「侵蚀一净赚体力」这种文字标签也变红/变绿，读起来像标题出错了。

        标签与数值从 i18n 文案里按最后一个冒号拆开：本页的汇总文案都是
        ``标签: {value}`` 的形式，直接切分即可，不必再引入一个「纯标签」键。

        Args:
            key: i18n 键（``Gui.Stat.*``）。
            raw_value: 要显示的值。
            signed: 是否按正负给数值着色。

        Returns:
            str: 一条汇总项的 HTML。
        """
        text = escape(t(key, value=raw_value))
        cls = self.summary_sign_class(raw_value) if signed else ""
        if not cls:
            return f"<span>{text}</span>"

        # 只在有冒号时拆分；拆不开就整条染色（总比丢失颜色好）。
        # ``sep`` 是 ": "（含尾随空格），要原样留在标签一侧 —— 冒号与数值之间
        # 没有空格会显得挤，而没拆分的普通项带空格，两边会不一致。
        head, sep, tail = text.rpartition(": ")
        if not sep:
            return f'<span class="{cls}">{text}</span>'
        # 颜色只作用于后半段数值；外面再套一层 white-space:nowrap：标签与数值是
        # 两个 flex 子项，不绑在一起的话窄屏换行会把数值甩到下一行，像丢了数据
        return (
            '<span style="white-space: nowrap;">'
            f"<span>{head}{sep}</span>"
            f'<span class="{cls}">{tail}</span>'
            "</span>"
        )

    def _render_opsi_summary(self, labels, rows, ap_bought, net_ap, loop_eff):
        with use_scope("opsi_stats", clear=True):
            # 标题行用带图标槽的版本，刷新图标由 PyWebIO 渲染进预留作用域
            put_html(
                build_title_icon_row(
                    t("Gui.Stat.OpsiDataCollectionTitle"), _OPSI_REFRESH_SCOPE
                )
            )
            put_button(
                "",
                onclick=self._render_opsi_stats,
                color="off",
                scope=_OPSI_REFRESH_SCOPE,
            )
            put_html(refresh_icon_button_css(_OPSI_REFRESH_SCOPE))
            # 四条汇总并排一行：当月侵蚀一 购买体力 / 出击消耗 / 净赚体力 / 循环效率。
            # 后三项都是侵蚀1 独有的口径（侵蚀3/5 不按每轮行动力核算），放在表格里
            # 对 3/5 行只能留空，因此统一搬到这一行。
            # 用 put_html + flex 而不是 put_row：put_row 生成的是 grid，列宽走
            # grid-template-columns，窗口一窄就会把几条挤成多行；flex + wrap +
            # column-gap 的换行行为可预期，间距也不再依赖 None 占位项。
            try:
                sortie_cost_total = (
                    int(rows[0][labels.index(t("Gui.Stat.BattleCount"))]) + 1
                ) // 2 * 5
            except Exception:
                sortie_cost_total = "-"
            items = [
                self._summary_item_html("Gui.Stat.MonthlyPurchasedAP", ap_bought),
                self._summary_item_html(
                    "Gui.Stat.MonthlySortieCost", sortie_cost_total
                ),
                self._summary_item_html("Gui.Stat.MonthlyNetAP", net_ap, signed=True),
                self._summary_item_html(
                    "Gui.Stat.MonthlyLoopEfficiency", loop_eff, signed=True
                ),
            ]
            put_html(self.summary_style_html())
            # class="stat-summary" 给出「标题 → 这段文字 → 表格」的间距，
            # 规则见 entry-alas.css
            put_html(
                '<div class="stat-summary" style="display: flex; flex-wrap: wrap; '
                'align-items: baseline; column-gap: 24px; row-gap: 4px;">'
                + "".join(items)
                + "</div>"
            )
            put_html(build_simple_table(labels, rows))

            put_scope("meow_loot_scope")

            self._render_meowofficer_farming()
