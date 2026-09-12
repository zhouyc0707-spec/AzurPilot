"""WebUI 体力趋势图的数据装配和图表渲染。"""

from datetime import timedelta

from module.webui.app_dependencies import (
    current_time,
    datetime,
    json,
    put_button,
    put_buttons,
    put_html,
    put_text,
    t,
    use_scope,
)

from module.webui.app_helpers import (
    build_muted_notice,
    read_webapp_template,
)
from module.webui.ap_chart_theme import (
    palette_for_theme,
    series_colors,
)


from module.webui.app_types import WebUIMixinBase


class ActionPointStatisticsMixin(WebUIMixinBase):
    """WebUI 体力趋势图的数据装配和图表渲染。"""

    # 图表时间范围：今日 / 本周（近 7 天）/ 本月（当月全部）
    _AP_PERIODS = ("day", "week", "month")

    def _ap_chart_period(self) -> str:
        """返回当前图表时间范围，非法值回退为「本月」。"""
        period = getattr(self, "_ap_chart_period_value", "month")
        return period if period in self._AP_PERIODS else "month"

    @staticmethod
    def _filter_points_by_period(points, period, now):
        """按时间范围过滤时间线原始点。

        数据源本身只覆盖当月，所以「本月」等价于不裁剪；「今日」与「本周」
        在这里裁掉更早的点，辅助序列随后按同样的图表点对齐。
        """
        if period == "day":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            days = 1
        elif period == "week":
            days = 7
            start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
                days=days - 1
            )
        else:
            return list(points)

        filtered = []
        for point in points:
            try:
                moment = datetime.fromisoformat(point.get("ts", ""))
            except Exception:
                continue
            if start <= moment <= now:
                filtered.append(point)
        return filtered

    def _load_ap_chart_timelines(self):
        """读取当前实例的行动力、凭证和资产时间线，并按所选范围裁剪。"""
        from module.statistics.opsi_month import (
            get_ap_timeline,
            get_asset_timeline,
            get_coins_timeline,
        )

        instance_name = getattr(self, "alas_name", None)
        if not instance_name:
            from module.config.utils import alas_instance

            all_instances = alas_instance()
            instance_name = all_instances[0] if all_instances else None
        timeline = get_ap_timeline(instance_name=instance_name)
        coins_timeline = get_coins_timeline(instance_name=instance_name)
        asset_timeline = get_asset_timeline(instance_name=instance_name)

        period = self._ap_chart_period()
        if period != "month":
            now = current_time()
            timeline = self._filter_points_by_period(timeline, period, now)
            coins_timeline = self._filter_points_by_period(coins_timeline, period, now)
            asset_timeline = self._filter_points_by_period(asset_timeline, period, now)
        return timeline, coins_timeline, asset_timeline

    def _render_ap_chart(self):
        self.cleanup_client_resources("__apChartCleanups")
        try:
            timeline, coins_timeline, asset_timeline = self._load_ap_chart_timelines()
        except Exception as e:
            with use_scope("ap_chart", clear=True):
                put_text(t("Gui.Stat.LoadApDataFailed", e=e))
            return

        if not timeline:
            with use_scope("ap_chart", clear=True):
                put_html(build_muted_notice(t("Gui.Stat.NoApData")))
                put_button(
                    t("Gui.Stat.Refresh"), onclick=self._render_ap_chart, color="off"
                )
            return

        raw_points = self._normalize_ap_chart_points(timeline)
        if not raw_points:
            with use_scope("ap_chart", clear=True):
                put_html(build_muted_notice(t("Gui.Stat.NoValidApData")))
            return

        # 画布不能读取 CSS 变量，配色统一由当前主题在渲染时确定
        theme = getattr(self, "theme", None)
        chart_data = self._build_ap_chart_series(raw_points, theme)
        if chart_data is None:
            with use_scope("ap_chart", clear=True):
                put_html(build_muted_notice(t("Gui.Stat.CannotAggregateKline")))
                put_button(
                    t("Gui.Stat.ViewLineShort"),
                    onclick=lambda: (
                        setattr(self, "_ap_chart_view", "line"),
                        self._render_ap_chart(),
                    ),
                    color="off",
                )
            return

        auxiliary_data = self._build_ap_chart_auxiliary_data(
            timeline=timeline,
            coins_timeline=coins_timeline,
            asset_timeline=asset_timeline,
            chart_points=chart_data["chart_points"],
            current_view=chart_data["current_view"],
            theme=theme,
        )
        self._render_ap_chart_content(chart_data, auxiliary_data, theme)

    @staticmethod
    def _normalize_ap_chart_points(timeline):
        """解析行动力快照并按时间排序。"""
        raw_points = []
        for pt in timeline:
            ts_raw = pt.get("ts", "")
            try:
                dt = datetime.fromisoformat(ts_raw)
            except Exception:
                continue
            raw_points.append(
                {
                    "dt": dt,
                    # ap_total 为含全部体力箱的总行动力（图表主序列），
                    # ap_now 为当前行动力（不含体力箱），仅用于数值展示。
                    "ap": int(pt.get("ap_total", pt.get("ap", 0))),
                    "ap_now": int(pt.get("ap", 0)),
                    "source": pt.get("source", "-"),
                }
            )

        raw_points.sort(key=lambda p: p["dt"])
        return raw_points

    def _build_ap_chart_series(self, raw_points, theme):
        """按当前视图构造折线或 K 线主序列及其摘要。

        Args:
            raw_points: 已排序的行动力快照点。
            theme: 当前 WebUI 主题，用于选取涨跌配色。
        """
        current_view = getattr(self, "_ap_chart_view", "line")

        labels = []
        opens = []
        highs = []
        lows = []
        closes = []
        counts = []
        ap_list = []
        ap_ts = []
        detail_sources = []
        chart_points = []
        is_detail_mode = False

        today = current_time().date()
        today_points = [p for p in raw_points if p["dt"].date() == today]
        if not today_points and raw_points:
            last_date = raw_points[-1]["dt"].date()
            today_points = [p for p in raw_points if p["dt"].date() == last_date]
            today = last_date

        if current_view == "detail":
            is_detail_mode = True
            if today_points:
                for p in today_points:
                    labels.append(p["dt"].strftime("%H:%M"))
                    ap_list.append(p["ap"])
                    ap_ts.append(int(p["dt"].timestamp() * 1000))
                    detail_sources.append(p.get("source", "-"))
                    chart_points.append(p)
                view_title = t("Gui.Stat.DetailChartTitle")
            else:
                for p in raw_points:
                    labels.append(p["dt"].strftime("%m-%d %H:%M"))
                    ap_list.append(p["ap"])
                    ap_ts.append(int(p["dt"].timestamp() * 1000))
                    chart_points.append(p)
                view_title = t("Gui.Stat.ViewTitleLine")
                is_detail_mode = False
                current_view = "line"
        elif current_view == "line":
            for p in raw_points:
                labels.append(p["dt"].strftime("%m-%d %H:%M"))
                ap_list.append(p["ap"])
                ap_ts.append(int(p["dt"].timestamp() * 1000))
                chart_points.append(p)
            view_title = t("Gui.Stat.ViewTitleLine")
        else:
            from collections import OrderedDict

            candles = OrderedDict()
            if current_view == "day":
                for p in today_points if today_points else raw_points[:24]:
                    hour_key = p["dt"].strftime("%H:00")
                    if hour_key not in candles:
                        candles[hour_key] = {
                            "open": p["ap"],
                            "high": p["ap"],
                            "low": p["ap"],
                            "close": p["ap"],
                            "count": 1,
                        }
                    else:
                        c = candles[hour_key]
                        c["high"] = max(c["high"], p["ap"])
                        c["low"] = min(c["low"], p["ap"])
                        c["close"] = p["ap"]
                        c["count"] += 1
                view_title = t("Gui.Stat.ViewTitleDay", day=today.strftime("%m-%d"))
            else:
                for p in raw_points:
                    day_key = p["dt"].strftime("%m-%d")
                    if day_key not in candles:
                        candles[day_key] = {
                            "open": p["ap"],
                            "high": p["ap"],
                            "low": p["ap"],
                            "close": p["ap"],
                            "count": 1,
                        }
                    else:
                        c = candles[day_key]
                        c["high"] = max(c["high"], p["ap"])
                        c["low"] = min(c["low"], p["ap"])
                        c["close"] = p["ap"]
                        c["count"] += 1
                view_title = t("Gui.Stat.ViewTitleMonth")

            if not candles:
                return None
            for k, v in candles.items():
                labels.append(k)
                opens.append(v["open"])
                highs.append(v["high"])
                lows.append(v["low"])
                closes.append(v["close"])
                counts.append(v["count"])

        all_ap = [p["ap"] for p in raw_points]
        ap_max = max(all_ap)
        ap_min = min(all_ap)
        ap_avg = int(sum(all_ap) / len(all_ap))
        ap_cur = all_ap[-1]
        # 当前行动力（不含体力箱），展示为「当前 / 总计」
        ap_now_cur = raw_points[-1].get("ap_now", 0)
        if current_view in ("line", "detail"):
            ap_change = ap_list[-1] - ap_list[0] if len(ap_list) >= 2 else 0
        else:
            ap_change = closes[-1] - opens[0] if len(closes) > 0 else 0
        change_sign = "+" if ap_change >= 0 else ""

        return {
            "current_view": current_view,
            "labels": labels,
            "opens": opens,
            "highs": highs,
            "lows": lows,
            "closes": closes,
            "counts": counts,
            "ap_list": ap_list,
            "ap_ts": ap_ts,
            "detail_sources": detail_sources,
            "chart_points": chart_points,
            "is_detail_mode": is_detail_mode,
            "view_title": view_title,
            "ap_cur": ap_cur,
            "ap_now_cur": ap_now_cur,
            "ap_change": ap_change,
            "ap_max": ap_max,
            "ap_min": ap_min,
            "ap_avg": ap_avg,
            "change_sign": change_sign,
        }

    @staticmethod
    def _align_ap_timeline(raw_points, chart_points):
        """按最近时间戳将辅助时间线对齐到图表点。"""
        raw_points.sort(key=lambda p: p["dt"])
        aligned_points = []
        point_idx = 0
        point_last = len(raw_points) - 1
        for chart_point in chart_points:
            while point_idx < point_last:
                cur_delta = abs(
                    (raw_points[point_idx]["dt"] - chart_point["dt"]).total_seconds()
                )
                next_delta = abs(
                    (
                        raw_points[point_idx + 1]["dt"] - chart_point["dt"]
                    ).total_seconds()
                )
                if next_delta > cur_delta:
                    break
                point_idx += 1
            aligned_points.append(raw_points[point_idx])
        return aligned_points

    def _build_ap_chart_auxiliary_data(
        self,
        timeline,
        coins_timeline,
        asset_timeline,
        chart_points,
        current_view,
        theme,
    ):
        """分别装配辅助序列，并按既有顺序组合图表载荷。

        Args:
            timeline: 行动力时间线原始点（含海里数）。
            coins_timeline: 黄币/紫币时间线原始点。
            asset_timeline: 资产时间线原始点。
            chart_points: 当前图表点，用于时间对齐。
            current_view: 当前视图（line/detail 才装配辅助序列）。
            theme: 当前 WebUI 主题，用于选取序列配色。
        """
        # 序列顺序与 ap_chart.js 的 seriesVisible 一致：体力/紫币/黄币/资产/海里数
        _, purple_color, yellow_color, asset_color, distance_color = series_colors(theme)
        distance_data = self._build_ap_chart_distance_data(
            timeline, chart_points, current_view, distance_color
        )
        coins_data = self._build_ap_chart_coins_data(
            coins_timeline,
            chart_points,
            current_view,
            purple_color=purple_color,
            yellow_color=yellow_color,
        )
        asset_data = self._build_ap_chart_asset_data(
            asset_timeline, current_view, asset_color
        )
        return self._combine_ap_chart_auxiliary_data(
            coins_data, distance_data, asset_data
        )

    @staticmethod
    def _ap_series_item(label, value_text, color, change_text, max_text, min_text):
        """构造序列概览项：当前值一行展示，明细放进鼠标悬停提示。

        Args:
            label: 序列名（行动力/黄币/紫币/海里数/资产）。
            value_text: 已格式化好的当前值文本。
            color: 当前值的颜色。
            change_text: 已带正负号的变化量文本。
            max_text: 最大值文本。
            min_text: 最小值文本。

        Returns:
            str: 概览项 HTML，悬停时用 CSS 显示 data-tip 内容。
        """
        tip = "\n".join(
            (f"变化: {change_text}", f"最高: {max_text}", f"最低: {min_text}")
        )
        return (
            f'<span class="ap-series-item" data-tip="{tip}">'
            f'{label}: <b style="color:{color}">{value_text}</b></span>'
        )

    @staticmethod
    def _ap_legend_item(label, series_index, color, dash=False):
        """构造图例项，颜色跟随当前主题，与画布中的曲线保持一致。

        Args:
            label: 序列名。
            series_index: 序列索引，对应 ap_chart.js 中的 seriesVisible。
            color: 图例色块颜色。
            dash: 是否为虚线序列（黄币/紫币）。

        Returns:
            str: 图例项 HTML。
        """
        dash_style = f" border-top:1px dashed {color};" if dash else ""
        return (
            f'<span class="ap-legend-item" data-series="{series_index}" '
            'style="display:flex; align-items:center; gap:4px;cursor:pointer;opacity:1;">'
            f'<span style="width:12px; height:2px; background:{color}; '
            f'border-radius:1px;{dash_style}"></span>{label}</span>'
        )

    def _build_ap_chart_coins_data(
        self,
        coins_timeline,
        chart_points,
        current_view,
        purple_color,
        yellow_color,
    ):
        """解析并对齐黄币、紫币时间线，构造对应统计和图例。

        Args:
            coins_timeline: 黄币/紫币时间线原始点。
            chart_points: 当前图表点，用于时间对齐。
            current_view: 当前视图（line/detail 才装配辅助序列）。
            purple_color: 紫币序列颜色，由当前主题决定。
            yellow_color: 黄币序列颜色，由当前主题决定。
        """
        yellow_coins_list = []
        purple_coins_list = []
        coins_sources_list = []
        show_coins = False
        stats_html = ""
        legend_html = ""

        if coins_timeline and chart_points and current_view in ("line", "detail"):
            coins_raw_points = []
            for pt in coins_timeline:
                ts_raw = pt.get("ts", "")
                try:
                    dt = datetime.fromisoformat(ts_raw)
                except Exception:
                    continue
                coins_raw_points.append(
                    {
                        "dt": dt,
                        "yellow_coins": int(pt.get("yellow_coins", 0)),
                        "purple_coins": int(pt["purple_coins"])
                        if "purple_coins" in pt
                        else None,
                        "source": pt.get("source", "-"),
                    }
                )

            if coins_raw_points:
                for coins_point in self._align_ap_timeline(
                    coins_raw_points, chart_points
                ):
                    yellow_coins_list.append(coins_point["yellow_coins"])
                    purple_coins_list.append(coins_point["purple_coins"])
                    coins_sources_list.append(coins_point.get("source", "-"))

                valid_yellow_coins = [v for v in yellow_coins_list if v is not None]
                valid_purple_coins = [
                    v for v in purple_coins_list if v is not None and v > 0
                ]
                show_coins = bool(valid_yellow_coins or valid_purple_coins)

                if valid_yellow_coins:
                    yc_cur = valid_yellow_coins[-1]
                    yc_change = (
                        valid_yellow_coins[-1] - valid_yellow_coins[0]
                        if len(valid_yellow_coins) >= 2
                        else 0
                    )
                    yc_change_sign = "+" if yc_change >= 0 else ""
                    yc_max = max(valid_yellow_coins)
                    yc_min = min(valid_yellow_coins)

                    stats_html += self._ap_series_item(
                        "黄币",
                        f"{yc_cur:,}",
                        yellow_color,
                        f"{yc_change_sign}{yc_change:,}",
                        f"{yc_max:,}",
                        f"{yc_min:,}",
                    )
                    legend_html += self._ap_legend_item(
                        "黄币", 2, yellow_color, dash=True
                    )

                if valid_purple_coins:
                    pc_cur = valid_purple_coins[-1]
                    pc_change = (
                        valid_purple_coins[-1] - valid_purple_coins[0]
                        if len(valid_purple_coins) >= 2
                        else 0
                    )
                    pc_change_sign = "+" if pc_change >= 0 else ""
                    pc_max = max(valid_purple_coins)
                    pc_min = min(valid_purple_coins)

                    stats_html += self._ap_series_item(
                        "紫币",
                        f"{pc_cur:,}",
                        purple_color,
                        f"{pc_change_sign}{pc_change:,}",
                        f"{pc_max:,}",
                        f"{pc_min:,}",
                    )
                    legend_html += self._ap_legend_item(
                        "紫币", 1, purple_color, dash=True
                    )

        return {
            "yellow_coins_list": yellow_coins_list,
            "purple_coins_list": purple_coins_list,
            "coins_sources_list": coins_sources_list,
            "show_coins": show_coins,
            "stats_html": stats_html,
            "legend_html": legend_html,
        }

    def _build_ap_chart_distance_data(
        self, timeline, chart_points, current_view, color
    ):
        """解析并对齐海里数时间线，构造对应统计和图例。

        Args:
            timeline: 行动力时间线原始点（含 distance 字段）。
            chart_points: 当前图表点，用于时间对齐。
            current_view: 当前视图（line/detail 才装配辅助序列）。
            color: 海里数序列颜色，由当前主题决定。
        """
        distance_raw_points = []
        if current_view in ("line", "detail"):
            for pt in timeline:
                distance_val = pt.get("distance")
                if distance_val is not None:
                    ts_raw = pt.get("ts", "")
                    try:
                        distance_dt = datetime.fromisoformat(ts_raw)
                        distance_raw_points.append(
                            {
                                "dt": distance_dt,
                                "distance": int(distance_val),
                            }
                        )
                    except Exception:
                        continue

        distance_list = []
        stats_html = ""
        legend_html = ""
        if distance_raw_points and chart_points and current_view in ("line", "detail"):
            for distance_point in self._align_ap_timeline(
                distance_raw_points, chart_points
            ):
                distance_list.append(distance_point["distance"])

            if distance_list:
                valid_distance = [v for v in distance_list if v is not None]
                if valid_distance:
                    d_cur = valid_distance[-1]
                    d_change = (
                        valid_distance[-1] - valid_distance[0]
                        if len(valid_distance) >= 2
                        else 0
                    )
                    d_change_sign = "+" if d_change >= 0 else ""
                    d_max = max(valid_distance)
                    d_min = min(valid_distance)

                    stats_html += self._ap_series_item(
                        "海里数",
                        f"{d_cur:,}",
                        color,
                        f"{d_change_sign}{d_change:,}",
                        f"{d_max:,}",
                        f"{d_min:,}",
                    )
                    legend_html += self._ap_legend_item("海里数", 4, color)

        return {
            "distance_list": distance_list,
            "stats_html": stats_html,
            "legend_html": legend_html,
        }

    def _build_ap_chart_asset_data(self, asset_timeline, current_view, color):
        """解析资产时间线，构造对应统计和图例。

        Args:
            asset_timeline: 资产时间线原始点。
            current_view: 当前视图（line/detail 才装配辅助序列）。
            color: 资产序列颜色，由当前主题决定。
        """
        asset_list = []
        asset_ts_list = []
        if asset_timeline and current_view in ("line", "detail"):
            for pt in asset_timeline:
                ts_raw = pt.get("ts", "")
                if ts_raw:
                    try:
                        va_dt = datetime.fromisoformat(ts_raw)
                        asset_value = self._snapshot_float(pt, "asset")
                        if asset_value is None:
                            continue
                        asset_list.append(asset_value)
                        asset_ts_list.append(int(va_dt.timestamp() * 1000))
                    except TypeError, ValueError:
                        continue

        stats_html = ""
        legend_html = ""
        if asset_list:
            valid_asset = [v for v in asset_list if v is not None]
            if valid_asset:
                a_cur = valid_asset[-1]
                a_change = (
                    valid_asset[-1] - valid_asset[0] if len(valid_asset) >= 2 else 0
                )
                a_change_sign = "+" if a_change >= 0 else ""
                a_max = max(valid_asset)
                a_min = min(valid_asset)

                stats_html += self._ap_series_item(
                    "资产",
                    f"{a_cur:,.1f}",
                    color,
                    f"{a_change_sign}{a_change:,.1f}",
                    f"{a_max:,.1f}",
                    f"{a_min:,.1f}",
                )
                legend_html += self._ap_legend_item("资产", 3, color)

        return {
            "asset_list": asset_list,
            "asset_ts_list": asset_ts_list,
            "stats_html": stats_html,
            "legend_html": legend_html,
        }

    @staticmethod
    def _combine_ap_chart_auxiliary_data(coins_data, distance_data, asset_data):
        """按模板约定组合辅助序列、摘要 HTML 与图例。"""
        show_coins = coins_data["show_coins"]
        if not show_coins and (
            asset_data["asset_list"]
            or coins_data["yellow_coins_list"]
            or coins_data["purple_coins_list"]
            or distance_data["distance_list"]
        ):
            show_coins = True

        return {
            "yellow_coins_list": coins_data["yellow_coins_list"],
            "purple_coins_list": coins_data["purple_coins_list"],
            "coins_sources_list": coins_data["coins_sources_list"],
            "distance_list": distance_data["distance_list"],
            "asset_list": asset_data["asset_list"],
            "asset_ts_list": asset_data["asset_ts_list"],
            "show_coins": show_coins,
            "coins_stats_html": (
                coins_data["stats_html"]
                + distance_data["stats_html"]
                + asset_data["stats_html"]
            ),
            "coins_legend_html": (
                coins_data["legend_html"]
                + distance_data["legend_html"]
                + asset_data["legend_html"]
            ),
        }

    @staticmethod
    def _snapshot_float(point, key):
        """将快照中的可选数值转换为浮点数。"""
        value = point.get(key)
        if value is None:
            return None
        return float(value)

    def _put_ap_period_selector(self, scope_id, period):
        """把「今日/本周/本月」渲染到面板图例行预留的 scope 里。

        Args:
            scope_id: 图例行里的 scope 名。
            period: 当前时间范围（day/week/month），用于高亮。
        """

        def on_period_click(selected_period):
            self._ap_chart_period_value = selected_period
            self._render_ap_chart()

        put_buttons(
            [
                {
                    "label": t("Gui.Stat.CommissionIncomeDay"),
                    "value": "day",
                    "color": "primary" if period == "day" else "off",
                },
                {
                    "label": t("Gui.Stat.CommissionIncomeWeek"),
                    "value": "week",
                    "color": "primary" if period == "week" else "off",
                },
                {
                    "label": t("Gui.Stat.CommissionIncomeMonth"),
                    "value": "month",
                    "color": "primary" if period == "month" else "off",
                },
            ],
            onclick=on_period_click,
            group=True,
            scope=scope_id,
        )

    def _render_ap_chart_content(self, chart_data, auxiliary_data, theme):
        """将已装配的数据填充到 HTML 和 JavaScript 模板。

        Args:
            chart_data: 主序列与摘要数据。
            auxiliary_data: 黄币/紫币/资产/海里数等辅助序列数据。
            theme: 当前 WebUI 主题，画布配色在渲染时按此确定。
        """
        current_view = chart_data["current_view"]
        chart_id = f"ap_cv_{id(self)}"
        detail_controls_display = (
            "display:flex;" if current_view in ("line", "detail") else "display:none;"
        )
        palette = palette_for_theme(theme)
        period_scope = f"{chart_id}_period"

        html_tpl = read_webapp_template("ap_chart_panel.html")
        html = html_tpl.format(
            chart_id=chart_id,
            view_title=chart_data["view_title"],
            ap_cur=chart_data["ap_cur"],
            ap_now_cur=chart_data["ap_now_cur"],
            ap_color=palette["ap_point"],
            change_sign=chart_data["change_sign"],
            ap_change=chart_data["ap_change"],
            ap_max=chart_data["ap_max"],
            ap_min=chart_data["ap_min"],
            ap_avg=chart_data["ap_avg"],
            detail_controls_display=detail_controls_display,
            coins_stats_html=auxiliary_data["coins_stats_html"],
            coins_legend_html=auxiliary_data["coins_legend_html"],
            chart_bg=palette["bg"],
            panel_border=palette["panel_border"],
            panel_shadow=palette["panel_shadow"],
            tip_bg=palette["tip_bg"],
            tip_border=palette["tip_border"],
            tip_text=palette["tip_text"],
            tip_shadow=palette["tip_shadow"],
            zoom_bg=palette["zoom_bg"],
            zoom_border=palette["zoom_border"],
            zoom_text=palette["zoom_text"],
            legend_text=palette["legend_text"],
            series_text=palette["series_text"],
            series_shadow=palette["series_shadow"],
            period_scope=period_scope,
        )

        js_tpl = read_webapp_template("ap_chart.js")
        js_code = (
            js_tpl.replace(
                "__CHART_TYPE__",
                "line" if chart_data["is_detail_mode"] else current_view,
            )
            .replace("__LABELS__", json.dumps(chart_data["labels"], ensure_ascii=False))
            .replace("__OPENS__", json.dumps(chart_data["opens"]))
            .replace("__HIGHS__", json.dumps(chart_data["highs"]))
            .replace("__LOWS__", json.dumps(chart_data["lows"]))
            .replace("__CLOSES__", json.dumps(chart_data["closes"]))
            .replace("__COUNTS__", json.dumps(chart_data["counts"]))
            .replace("__AP__", json.dumps(chart_data["ap_list"]))
            .replace("__AP_TS__", json.dumps(chart_data["ap_ts"]))
            .replace("__AVG__", str(chart_data["ap_avg"]))
            .replace("__CHART_ID__", chart_id)
            .replace(
                "__IS_DETAIL_MODE__",
                "true" if chart_data["is_detail_mode"] else "false",
            )
            .replace(
                "__SOURCES__",
                json.dumps(
                    chart_data["detail_sources"]
                    if chart_data["is_detail_mode"]
                    else []
                ),
            )
            .replace("__YELLOW_COINS__", json.dumps(auxiliary_data["yellow_coins_list"]))
            .replace("__PURPLE_COINS__", json.dumps(auxiliary_data["purple_coins_list"]))
            .replace("__COINS_SOURCES__", json.dumps(auxiliary_data["coins_sources_list"]))
            .replace("__ASSET__", json.dumps(auxiliary_data["asset_list"]))
            .replace("__ASSET_TS__", json.dumps(auxiliary_data["asset_ts_list"]))
            .replace("__DISTANCE__", json.dumps(auxiliary_data["distance_list"]))
            .replace(
                "__SHOW_COINS__",
                "true" if auxiliary_data["show_coins"] else "false",
            )
            .replace("__PALETTE__", json.dumps(palette))
            .replace("__SERIES_COLORS__", json.dumps(series_colors(theme)))
        )
        from pywebio.session import run_js

        with use_scope("ap_chart", clear=True):
            put_html(html)
            # 时间范围按钮渲染进图例行左侧预留的 scope；按钮必须在图表 HTML
            # 之后渲染，scope 才有对应节点
            self._put_ap_period_selector(period_scope, self._ap_chart_period())
            run_js(js_code)
