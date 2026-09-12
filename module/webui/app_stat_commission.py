"""WebUI 委托收益统计视图。"""

from html import escape

from module.webui.app_dependencies import (
    logger,
    put_button,
    put_buttons,
    put_html,
    put_text,
    t,
    use_scope,
)


from module.webui.app_types import WebUIMixinBase


_COMMISSION_RECENT_PAGE_SIZE = 10
_COMMISSION_RECENT_TOTAL = 50

def _refresh_icon_paths(
    center: float = 24.0,
    radius: float = 17.0,
    start_deg: float = 100.0,
    end_deg: float = 40.0,
    head_deg: float = 26.0,
    head_len: float = 7.5,
) -> tuple[str, str]:
    """按角度算出圆环弧与末端箭头，返回两条 SVG path 的 d。

    圆环用三段三次贝塞尔逼近（每段 100°），不用折线也不用 SVG 的
    large-arc/sweep：折线的隐式坐标会超出 8 位上限，被浏览器按错误的方式
    截断（实测只会画出四分之一圆）。

    Args:
        center: 圆心坐标（正方形 viewBox 内）。
        radius: 半径。
        start_deg: 圆弧起始角（度，0 度指向右侧，角度增大为顺时针）。
        end_deg: 圆弧结束角（度），需小于起始角以留出开口。
        head_deg: 箭头两翼相对端点切线的张开角。
        head_len: 箭头两翼长度。

    Returns:
        tuple[str, str]: 圆环路径与箭头路径。
    """
    import math

    def point(deg: float) -> tuple[float, float]:
        rad = math.radians(deg)
        return center + radius * math.cos(rad), center + radius * math.sin(rad)

    def bezier(from_deg: float, to_deg: float) -> str:
        """一段圆弧的三次贝塞尔近似。"""
        a0, a1 = math.radians(from_deg), math.radians(to_deg)
        k = 4 / 3 * math.tan((a1 - a0) / 4)
        x0, y0 = point(from_deg)
        x1, y1 = point(to_deg)
        c1x = x0 - k * radius * math.sin(a0)
        c1y = y0 + k * radius * math.cos(a0)
        c2x = x1 + k * radius * math.sin(a1)
        c2y = y1 - k * radius * math.cos(a1)
        return f"C{c1x:.2f},{c1y:.2f} {c2x:.2f},{c2y:.2f} {x1:.2f},{y1:.2f}"

    # 开口在右上：从 start_deg 顺时针（角度递增）绕到 end_deg，分三段画。
    # 顺时针意味着角度要递增扫过去，所以跨度是 360 - (start - end)= 300 度；
    # 直接写 (start - end) % 360 会得到 60 度、只画出短弧。
    span = (360 - (start_deg - end_deg)) % 360
    stops = [start_deg + span * i / 3 for i in range(4)]
    sx, sy = point(stops[0])
    ring_d = f"M{sx:.2f},{sy:.2f}" + "".join(
        bezier(stops[i], stops[i + 1]) for i in range(3)
    )

    # 末端切线方向（顺时针运动方向），两翼沿反向切线张开 ±head_deg
    end_rad = math.radians(end_deg)
    tx, ty = -math.sin(end_rad), math.cos(end_rad)
    ex, ey = point(end_deg)
    wings = []
    for sign in (1, -1):
        deg = math.radians(sign * head_deg)
        cos_d, sin_d = math.cos(deg), math.sin(deg)
        dx = -tx * cos_d + ty * sin_d
        dy = -tx * sin_d - ty * cos_d
        wings.append(f"{ex + head_len * dx:.2f} {ey + head_len * dy:.2f}")
    head_d = f"M{wings[0]} L{ex:.2f} {ey:.2f} L{wings[1]}"
    return ring_d, head_d


_RING_D, _HEAD_D = _refresh_icon_paths()

# 「委托收益统计」标题右侧的刷新图标：开口圆环箭头。
# SVG 作为背景图时 currentColor 拿不到 CSS 颜色，所以要烘进 data URI；
# 深浅两套由 --alas-stat-icon-color 决定（见 entry-alas.css / dark-alas.css），
# 按钮的 background-image 由下方 _put_refresh_icon_button 注入。
_REFRESH_ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" '
    'fill="none" stroke="{color}" stroke-width="4" '
    'stroke-linecap="round" stroke-linejoin="round">'
    f'<path d="{_RING_D}"/>'
    f'<path d="{_HEAD_D}"/>'
    "</svg>"
)

# 图标按钮作用域名，与 entry-alas.css 的 .stat-icon-btn 规则配套
_REFRESH_BTN_SCOPE = "commission_income_refresh"

# 图标描边色：浅色页面用深靛灰，深色页面用浅灰蓝（配色见 --alas-stat-icon-color）
_ICON_COLOR_LIGHT = "#43415f"
_ICON_COLOR_DARK = "#c3c7d4"


def _refresh_icon_data_uri(color: str) -> str:
    """把刷新图标转成可直接放进 CSS ``background-image`` 的 data URI。

    Args:
        color: 描边颜色，``#`` 开头即可，内部会转义成 URI 安全形式。

    Returns:
        str: 形如 ``url("data:image/svg+xml,...")`` 的 CSS 值。
    """
    svg = _REFRESH_ICON_SVG.format(color=color)
    encoded = (
        svg.replace("<", "%3C")
        .replace(">", "%3E")
        .replace("#", "%23")
        .replace('"', "'")
        .replace(" ", "%20")
    )
    return f"url(\"data:image/svg+xml,{encoded}\")"


class CommissionIncomeStatisticsMixin(WebUIMixinBase):
    """WebUI 委托收益统计视图。"""

    def _render_commission_income(self):
        try:
            income_data = self._load_commission_income_data()
            if income_data is None:
                self._show_commission_income_no_data()
                return

            summary_html, recent_html = self._build_commission_income_html(income_data)
            self._output_commission_income(
                summary_html,
                recent_html,
                income_data["period"],
                len(income_data["recent"]),
            )
        except Exception as e:
            with use_scope("commission_income", clear=True):
                put_text(t("Gui.Stat.CommissionIncomeNoData"))
                logger.warning(f"[WebUI-统计] 委托收入渲染失败: {e}")

    def _load_commission_income_data(self):
        from datetime import datetime
        from module.statistics.commission_income_stats import (
            get_commission_income_summary,
            get_recent_commission_entries,
            COMMISSION_ITEM_META,
            COMMISSION_ITEM_NAME_MAP,
            COMMISSION_TRACKED_ITEMS,
        )

        instance_name = getattr(self, "alas_name", None)
        if not instance_name:
            from module.config.utils import alas_instance

            all_instances = alas_instance()
            instance_name = all_instances[0] if all_instances else None
        if not instance_name:
            return None

        item_name_map = {
            "Gem": t("Gui.Stat.CommissionIncomeItemGem"),
            "Cube": t("Gui.Stat.CommissionIncomeItemCube"),
            "Chip": t("Gui.Stat.CommissionIncomeItemChip"),
            "Oil": t("Gui.Stat.CommissionIncomeItemOil"),
            "Coin": t("Gui.Stat.CommissionIncomeItemCoin"),
        }
        item_icon_map = {
            "Gem": "static/assets/gui/icon/icon_1.png",
            "Cube": "static/assets/gui/icon/icon_2.png",
            "Chip": "static/assets/gui/icon/icon_3.png",
            "Oil": "static/assets/gui/icon/icon_4.png",
            "Coin": "static/assets/gui/icon/icon_5.png",
        }
        period = self._commission_income_period

        return {
            "period": period,
            "summary": get_commission_income_summary(instance_name, period=period),
            "recent": get_recent_commission_entries(
                instance_name, limit=_COMMISSION_RECENT_TOTAL
            ),
            "item_name_map": item_name_map,
            "item_icon_map": item_icon_map,
            "datetime": datetime,
            "item_meta": COMMISSION_ITEM_META,
            "item_name_lookup": COMMISSION_ITEM_NAME_MAP,
            "tracked_items": COMMISSION_TRACKED_ITEMS,
        }

    def _build_commission_income_html(self, income_data):
        summary = income_data["summary"]
        rows = summary.get("detail_rows", [])
        item_name_map = income_data["item_name_map"]
        item_icon_map = income_data["item_icon_map"]
        return (
            self._build_commission_summary_html(rows, item_name_map, item_icon_map),
            self._build_commission_recent_html(
                income_data["recent"],
                summary,
                item_name_map,
                item_icon_map,
                income_data["datetime"],
                income_data["item_meta"],
                income_data["item_name_lookup"],
                income_data["tracked_items"],
            ),
        )

    def _build_commission_summary_html(self, rows, item_name_map, item_icon_map):
        html = """
                <style>
                    #commission_income_container > div,
                    #commission_income_container table {
                        width: 100% !important;
                        max-width: 100% !important;
                    }
                    #commission_income_container img {
                        background: transparent !important;
                        border: none !important;
                        box-shadow: none !important;
                        margin: 0 !important;
                        padding: 0 !important;
                    }
                </style>
                <div id="commission_income_container" class="commission-income-summary" style="padding: 0; width: 100%; box-sizing: border-box;">
                """

        # 标题行：标题在左、刷新图标紧邻其右。图标按钮由 PyWebIO 渲染到
        # 预留的 scope 里（HTML 里的按钮无法回调 PyWebIO），行内布局用 CSS 收口。
        html += (
            '<div class="stat-title-row">'
            '<div class="stat-title-text">'
            f'{t("Gui.Stat.CommissionIncomeTitle")}'
            "</div>"
            f'<div id="pywebio-scope-{_REFRESH_BTN_SCOPE}"></div>'
            "</div>"
        )

        html += '<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr)); gap: 12px; margin-bottom: 20px; width: 100%;">'
        for row in rows:
            display_name = item_name_map.get(row["name"], row["name"])
            icon_path = item_icon_map.get(row["name"], "")
            total_str = f"+{row['total']:,}" if row["total"] > 0 else "0"
            # 委托次数与平均/次原先单独占一张表，现在并入对应物品的框内
            stat_line = t(
                "Gui.Stat.CommissionIncomeCardStat",
                count=f"{row['count']:,}",
                avg=f"{row['avg']:.1f}",
            )

            icon_html = (
                (
                    f'<div style="width: 34px; height: 34px; display: flex; align-items: center; justify-content: center; background: {row["color"]}1a; border-radius: 8px; flex-shrink: 0;">'
                    f'<img src="{icon_path}" style="width: 24px; height: 24px; object-fit: contain; background: transparent;">'
                    f"</div>"
                )
                if icon_path
                else f'<div style="width: 12px; height: 12px; border-radius: 50%; background: {row["color"]}; flex-shrink: 0;"></div>'
            )

            html += f"""
                    <div class="commission-income-metric-card" style="display: flex; align-items: center; gap: 10px; padding: 12px 14px; background: rgba(128, 128, 128, 0.05); border-radius: 6px; border: 1px solid rgba(128, 128, 128, 0.15);">
                        {icon_html}
                        <div style="display: flex; flex-direction: column; gap: 1px;">
                            <span style="font-size: 0.78rem; opacity: 0.65;">{display_name}</span>
                            <span style="font-size: 1.15rem; font-weight: 400; color: inherit;">{total_str}</span>
                            <span style="font-size: 0.72rem; opacity: 0.55;">{stat_line}</span>
                        </div>
                    </div>"""
        return html + "</div>"

    def _build_commission_recent_html(
        self,
        recent,
        summary,
        item_name_map,
        item_icon_map,
        datetime_class,
        item_meta,
        item_name_lookup,
        tracked_items,
    ):
        # 最近委托记录分页：仅渲染当前页的 10 条
        total_pages = (
            (len(recent) + _COMMISSION_RECENT_PAGE_SIZE - 1)
            // _COMMISSION_RECENT_PAGE_SIZE
            if recent
            else 0
        )
        page = getattr(self, "_commission_recent_page", 0)
        page = max(0, min(page, total_pages - 1)) if total_pages else 0
        self._commission_recent_page = page
        start = page * _COMMISSION_RECENT_PAGE_SIZE
        recent_page = recent[start : start + _COMMISSION_RECENT_PAGE_SIZE]

        html = '<div class="commission-income-recent" style="width: 100% !important; max-width: none !important; display: block !important; box-sizing: border-box;">'
        if recent_page:
            html += f'<div style="height: 1px; background: rgba(128, 128, 128, 0.2); margin: 24px 0;"></div>'
            html += f'<div style="font-size: 0.9rem; font-weight: 500; color: inherit; margin-bottom: 10px;">{t("Gui.Stat.CommissionIncomeRecentTitle")}</div>'
            html += '<div style="font-size: 13px; width: 100%;">'
            for entry in recent_page:
                ts = entry.get("ts", "")
                try:
                    dt = datetime_class.fromisoformat(ts)
                    time_str = dt.strftime("%m-%d %H:%M")
                except Exception:
                    time_str = ts[:16] if ts else "--"
                items = entry.get("items", {})
                item_parts = []
                for raw_name, amount in items.items():
                    if not amount or int(amount) <= 0:
                        continue
                    mapped_name = item_name_lookup.get(raw_name, raw_name)
                    if mapped_name not in tracked_items:
                        continue
                    meta = item_meta.get(mapped_name, {"color": "#888"})
                    icon_path = item_icon_map.get(mapped_name, "")
                    display = item_name_map.get(mapped_name, mapped_name)

                    icon_html = (
                        (
                            f'<div style="width: 22px; height: 22px; display: inline-flex; align-items: center; justify-content: center; background: {meta["color"]}1a; border-radius: 4px; margin-right: 6px; vertical-align: middle;">'
                            f'<img src="{icon_path}" style="width: 16px; height: 16px; object-fit: contain; background: transparent;">'
                            f"</div>"
                        )
                        if icon_path
                        else f'<span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: {meta["color"]}; margin-right: 4px;"></span>'
                    )

                    item_parts.append(
                        f'<span style="display: inline-flex; align-items: center; margin-right: 12px; height: 24px;">'
                        f"{icon_html}"
                        f'<span style="color: inherit;">{display}</span>'
                        f'<span style="opacity: 0.65; margin-left: 2px;">x{int(amount)}</span>'
                        f"</span>"
                    )
                items_str = (
                    "".join(item_parts)
                    if item_parts
                    else '<span style="opacity: 0.6;">--</span>'
                )

                # 查看截图按钮：跟随在记录行内，点击在新标签页单独打开截图
                shots = entry.get("screenshots") or []
                shot_links = ""
                for shot_index, shot in enumerate(shots):
                    label = "查看截图" if shot_index == 0 else f"查看截图{shot_index + 1}"
                    shot_links += (
                        f'<a href="/static/commission_rewards/{escape(shot, quote=True)}" '
                        f'target="_blank" rel="noopener" '
                        f'style="flex-shrink: 0; margin-left: 8px; font-size: 0.7rem; padding: 2px 10px; '
                        f'border: 1px solid rgba(128, 128, 128, 0.35); border-radius: 4px; '
                        f'background: rgba(128, 128, 128, 0.08); color: inherit; text-decoration: none; '
                        f'cursor: pointer;">{label}</a>'
                    )

                html += (
                    f'<div class="commission-income-recent-row" style="display: flex; align-items: center; flex-wrap: wrap; padding: 6px 0; border-bottom: 1px solid rgba(128, 128, 128, 0.1);">'
                    f'<span style="opacity: 0.65; min-width: 80px; font-size: 12px; flex-shrink: 0;">{time_str}</span>'
                    f'<span>{items_str}</span>'
                    f"{shot_links}"
                    f"</div>"
                )
            html += "</div>"

        html += f'<p style="font-size: 0.75rem; opacity: 0.5; margin-top: 10px;">{t("Gui.Stat.CommissionIncomeTotalCommissions", value=summary["total_commissions"])}</p>'
        return html + "</div>"

    def _output_commission_income(self, summary_html, recent_html, period, recent_count):
        with use_scope("commission_income", clear=True):
            put_html(summary_html)

            def on_period_click(selected_period):
                self._commission_income_period = selected_period
                self._commission_recent_page = 0
                self._render_commission_income()

            # 刷新收进标题右侧的图标按钮，放回标题行预留的 scope
            with use_scope(_REFRESH_BTN_SCOPE, clear=True):
                self._put_refresh_icon_button()

            # 周期按钮统一用 color="off"，外观由 entry-alas.css 的
            # 「统计页统一按钮样式」收口，选中区间用 primary 标记。
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
                scope="commission_income",
            )
            put_html(recent_html, scope="commission_income")
            if recent_count > _COMMISSION_RECENT_PAGE_SIZE:
                self._output_recent_pagination(recent_count)

    def _put_refresh_icon_button(self):
        """渲染标题旁的刷新图标按钮。

        按钮必须是 PyWebIO 的 ``put_button``（只有它才能回调 Python），因此把
        label 留空、图标作为按钮背景图由 CSS 提供，形成纯图标按钮。
        深浅两套图标按主题分别定义，切换主题时无需重新渲染。
        """
        put_button(
            "",
            onclick=self._render_commission_income,
            color="off",
            scope=_REFRESH_BTN_SCOPE,
        )
        put_html(
            f"<style>#pywebio-scope-{_REFRESH_BTN_SCOPE} .btn {{"
            f" background-image: {_refresh_icon_data_uri(_ICON_COLOR_LIGHT)} !important;"
            " background-repeat: no-repeat !important;"
            " background-position: center !important;"
            " background-size: 22px 22px !important;"
            " }"
            f"body.webio-theme-dark #pywebio-scope-{_REFRESH_BTN_SCOPE} .btn,"
            f"html[data-theme='dark'] #pywebio-scope-{_REFRESH_BTN_SCOPE} .btn {{"
            f" background-image: {_refresh_icon_data_uri(_ICON_COLOR_DARK)} !important;"
            " }</style>"
        )

    def _output_recent_pagination(self, recent_count):
        """渲染最近委托记录的分页控件。"""
        total_pages = (
            recent_count + _COMMISSION_RECENT_PAGE_SIZE - 1
        ) // _COMMISSION_RECENT_PAGE_SIZE
        page = getattr(self, "_commission_recent_page", 0)
        page = max(0, min(page, total_pages - 1))
        self._commission_recent_page = page

        def on_pagination(value):
            new_page = getattr(self, "_commission_recent_page", 0)
            if value == "prev":
                new_page -= 1
            elif value == "next":
                new_page += 1
            else:
                new_page = int(value)
            new_page = max(0, min(new_page, total_pages - 1))
            self._commission_recent_page = new_page
            self._render_commission_income()

        pagination_buttons = [
            {"label": "上一页", "value": "prev", "color": "off"},
        ]
        for index in range(1, min(5, total_pages) + 1):
            pagination_buttons.append(
                {
                    "label": str(index),
                    "value": index - 1,
                    "color": "primary" if (index - 1) == page else "off",
                }
            )
        pagination_buttons.append(
            {"label": "下一页", "value": "next", "color": "off"}
        )

        # 页码文字去掉：按钮组里已经把当前页高亮出来了，再写一遍是重复信息。
        put_buttons(
            pagination_buttons,
            onclick=on_pagination,
            group=True,
            scope="commission_income",
        ).style("margin-top:10px")

    @staticmethod
    def _show_commission_income_no_data():
        with use_scope("commission_income", clear=True):
            put_text(t("Gui.Stat.CommissionIncomeNoData"))
