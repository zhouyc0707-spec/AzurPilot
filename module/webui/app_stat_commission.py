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


from module.webui.stat_icon import (
    build_title_icon_row,
    refresh_icon_button_css,
)
from module.webui.app_types import WebUIMixinBase


_COMMISSION_RECENT_PAGE_SIZE = 10
_COMMISSION_RECENT_TOTAL = 50

# 标题旁的刷新图标按钮作用域名，与 entry-alas.css 里 #pywebio-scope-<name> 的规则配套
_REFRESH_BTN_SCOPE = "commission_income_refresh"
# 标题与物品卡片之间的时间范围按钮作用域名
_PERIOD_BTN_SCOPE = "commission_income_period"


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
            "running": self._load_running_commissions(instance_name),
            "item_name_map": item_name_map,
            "item_icon_map": item_icon_map,
            "datetime": datetime,
            "item_meta": COMMISSION_ITEM_META,
            "item_name_lookup": COMMISSION_ITEM_NAME_MAP,
            "tracked_items": COMMISSION_TRACKED_ITEMS,
        }

    @staticmethod
    def _load_running_commissions(instance_name):
        """读取运行中的委托状态（由 ALAS worker 写入状态文件）。

        读失败不该让整个委托收益板块渲染不出来，因此这里兜底返回「无数据」。

        Args:
            instance_name: 实例名。

        Returns:
            RunningState: 见 ``module.commission.running_state``。
        """
        from module.commission.running_state import RunningState, read_running_state

        try:
            return read_running_state(instance_name)
        except Exception:
            return RunningState(available=False, updated_at=0.0, commissions=[])

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
                income_data.get("running"),
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

        # 标题行：标题在左、刷新图标紧邻其右；下面紧跟时间范围按钮插槽。
        # 图标与范围按钮都由 PyWebIO 渲染到预留的 scope 里（HTML 里的按钮无法
        # 回调 PyWebIO），行内布局用 CSS 收口。
        html += build_title_icon_row(
            t("Gui.Stat.CommissionIncomeTitle"),
            _REFRESH_BTN_SCOPE,
            period_scope_id=_PERIOD_BTN_SCOPE,
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

    @staticmethod
    def _build_running_commissions_html(running, datetime_class):
        """构造「正在进行」区块：每个委托一个圆角矩形卡片，横向排成一行。

        区分两种空态：状态文件还没生成（worker 尚未跑过委托任务）显示
        「尚未获取到委托状态」，文件在但确实没有运行中的委托显示「当前无运行中
        委托」—— 前者说明「还没看」，后者说明「看过了，确实没有」。

        Args:
            running: ``RunningState``，或 None（读取失败）。
            datetime_class: datetime 类，便于测试注入。

        Returns:
            str: 区块 HTML。
        """
        from module.commission.running_state import RunningState

        state = running if isinstance(running, RunningState) else RunningState()
        commissions = state.commissions or []

        if not state.available:
            body = (
                f'<div style="font-size: 12px; opacity: 0.6;">'
                f'{escape(t("Gui.Stat.RunningCommissionUnknown"))}</div>'
            )
        elif not commissions:
            body = (
                f'<div style="font-size: 12px; opacity: 0.6;">'
                f'{escape(t("Gui.Stat.RunningCommissionNone"))}</div>'
            )
        else:
            cards = []
            for entry in commissions:
                try:
                    finish_text = datetime_class.fromtimestamp(
                        entry["finish"]
                    ).strftime("%H:%M")
                except Exception:
                    finish_text = "--"
                # 卡片用 flex:1 1 <基准宽>：窗口够宽时几张等分铺满一行，
                # 窗口窄了自动换行而不是把文字压到换行
                cards.append(
                    f'<div class="commission-running-card" style="flex: 1 1 150px; '
                    f'min-width: 0; box-sizing: border-box; padding: 6px 10px; '
                    f'border: 1px solid rgba(128, 128, 128, 0.25); '
                    f'border-radius: 10px;">'
                    f'<div style="font-size: 13px; word-break: break-all;">'
                    f'{escape(str(entry["name"]))}</div>'
                    f'<div style="font-size: 11px; opacity: 0.65; margin-top: 2px;">'
                    f'{escape(t("Gui.Stat.RunningCommissionFinish", value=finish_text))}'
                    f"</div></div>"
                )
            body = (
                '<div class="commission-running-cards" style="display: flex; '
                f'flex-wrap: wrap; gap: 8px;">{"".join(cards)}</div>'
            )

        return (
            '<div class="commission-running" style="width: 100%;">'
            f'<div style="font-size: 0.9rem; font-weight: 500; color: inherit; '
            f'margin-bottom: 8px;">{escape(t("Gui.Stat.RunningCommissionTitle"))}</div>'
            f"{body}</div>"
        )

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
        running=None,
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
        # 「正在进行」放在最近委托记录上方。它显示的是当前状态，与所选时间范围
        # （今日/本周/本月）无关，所以即使没有最近记录也要渲染。
        html += self._build_running_commissions_html(running, datetime_class)
        if recent_page:
            # 分隔线上下留白收紧：原来 24px/24px 与物品卡片网格的 20px 下边距
            # 叠加后，标题上方有 49px 空白，占了近半屏的一行高
            html += f'<div style="height: 1px; background: rgba(128, 128, 128, 0.2); margin: 12px 0;"></div>'
            html += f'<div style="font-size: 0.9rem; font-weight: 500; color: inherit; margin-bottom: 8px;">{t("Gui.Stat.CommissionIncomeRecentTitle")}</div>'
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

                # 查看截图按钮：一条记录只对应一次委托收获，因此只渲染这一张截图
                # 外观由 entry-alas.css 的 .alas-shot-link 负责（与同页按钮同规格），
                # 行内只保留布局用的 flex-shrink / margin-left
                shots = entry.get("screenshots") or []
                shot_links = ""
                if shots:
                    shot_links = (
                        f'<a href="/static/commission_rewards/{escape(shots[0], quote=True)}" '
                        f'target="_blank" rel="noopener" class="alas-shot-link" '
                        f'style="flex-shrink: 0; margin-left: 8px;">{t("Gui.Stat.ViewScreenshot")}</a>'
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

            # 时间范围按钮放在标题与物品卡片之间：容器由 put_scope 建
            # （put_buttons 的目标必须是 PyWebIO 作用域），再用 order 把它排到
            # 物品卡片之前 —— 它紧跟在标题行后面，卡片网格在 summary_html 里
            with use_scope(_PERIOD_BTN_SCOPE, clear=True):
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
        put_html(refresh_icon_button_css(_REFRESH_BTN_SCOPE))

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
