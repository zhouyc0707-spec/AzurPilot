"""WebUI 体力趋势图的视图切换工具栏。"""

from module.webui.app_dependencies import (
    put_button,
    put_buttons,
    put_html,
    put_row,
    t,
)


from module.webui.app_types import WebUIMixinBase


class ActionPointToolbarMixin(WebUIMixinBase):
    """WebUI 体力趋势图的视图切换工具栏。"""

    def _render_ap_chart_toolbar(self, current_view, chart_id):
        def _switch_view(v):
            self._ap_chart_view = v
            self._render_ap_chart()

        put_html(f"""
        <style>
        [style*="--ap-chart-md3-toolbar-{chart_id}"] {{
            margin-top: 12px !important;
            padding: 10px 12px !important;
            border: 1px solid var(--alas-entry-border) !important;
            border-radius: 16px !important;
            background: var(--alas-entry-surface) !important;
            box-shadow: var(--alas-entry-panel-shadow) !important;
            align-items: center !important;
            column-gap: 10px !important;
        }}
        [style*="--ap-chart-md3-segment-{chart_id}"] {{
            display: inline-flex !important;
            width: auto !important;
            max-width: 100% !important;
            margin: 0 !important;
        }}
        [style*="--ap-chart-md3-refresh-{chart_id}"] {{
            margin: 0 !important;
            justify-self: end !important;
        }}
        @media (max-width: 720px) {{
            [style*="--ap-chart-md3-toolbar-{chart_id}"] {{
                grid-template-columns: 1fr auto !important;
                column-gap: 6px !important;
                padding: 6px 8px !important;
            }}
            [style*="--ap-chart-md3-toolbar-{chart_id}"] > :first-child {{
                display: none !important;
            }}
            [style*="--ap-chart-md3-segment-{chart_id}"] {{
                max-width: none !important;
                width: 100% !important;
                overflow-x: auto !important;
                -webkit-overflow-scrolling: touch !important;
            }}
            [style*="--ap-chart-md3-segment-{chart_id}"] .btn-group {{
                width: max-content !important;
                flex-wrap: nowrap !important;
                overflow: visible !important;
            }}
            [style*="--ap-chart-md3-segment-{chart_id}"] .btn {{
                flex: 0 0 auto !important;
                padding: 0 12px !important;
                white-space: nowrap !important;
            }}
            [style*="--ap-chart-md3-segment-{chart_id}"]::-webkit-scrollbar {{
                height: 3px !important;
            }}
            [style*="--ap-chart-md3-segment-{chart_id}"]::-webkit-scrollbar-thumb {{
                background: var(--alas-entry-border) !important;
                border-radius: 2px !important;
            }}
        }}
        </style>
        """)

        view_options = [
            (t("Gui.Stat.ViewLineButton"), "line"),
            (t("Gui.Stat.ViewDayButton"), "day"),
            (t("Gui.Stat.ViewMonthButton"), "month"),
            (t("Gui.Stat.ToggleDetailChart"), "detail"),
        ]
        view_buttons = [
            {
                "label": label,
                "value": value,
                "color": "primary" if current_view == value else "secondary",
            }
            for label, value in view_options
        ]
        put_row(
            [
                put_html(
                    f'<span style="display:inline-flex;align-items:center;gap:6px;'
                    f'font-size:12px;font-weight:600;color:var(--alas-entry-muted);white-space:nowrap;">'
                    f"{t('Gui.Stat.ViewLabel')}</span>"
                ),
                put_buttons(
                    view_buttons, onclick=_switch_view, group=True
                ).style(f"--ap-chart-md3-segment-{chart_id}:1;"),
                put_button(
                    t("Gui.Stat.Refresh"),
                    onclick=self._render_ap_chart,
                    color="off",
                ).style(f"--ap-chart-md3-refresh-{chart_id}:1; justify-self:end;"),
            ],
            size="auto auto 1fr",
        ).style(f"--ap-chart-md3-toolbar-{chart_id}:1;")
