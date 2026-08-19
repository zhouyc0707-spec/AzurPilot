"""WebUI 统计页装配器。"""

from module.webui.app_dependencies import put_scope


from module.webui.app_types import WebUIMixinBase


class StatisticsPageMixin(WebUIMixinBase):
    """注册统计子视图及其离页可取消的刷新任务。"""

    def _mount_stat_panels(self) -> None:
        """创建并渲染统计图表面板（含周期刷新），渲染到当前所在作用域。

        统计图表既可整体铺满页面，也可内嵌到概览页的右侧区域，
        因此单独抽出为面板装配方法，由调用方决定其渲染位置。
        """
        if not hasattr(self, "_ap_chart_view"):
            self._ap_chart_view = "line"
        if not hasattr(self, "_commission_income_period"):
            self._commission_income_period = "month"

        # 独立 scope 使周期刷新不会清空其他统计区域。
        put_scope("ap_chart", [])
        self._render_ap_chart()
        self.task_handler.add(self._render_ap_chart, 60, True)
        # 隐藏全资源变化趋势图表（始终不渲染、不注册周期刷新）
        # 确保页面加载、刷新、切换选项卡等任何交互后均保持隐藏状态
        # put_scope("resource_chart", [])
        # self._render_resource_chart()
        # self.task_handler.add(self._render_resource_chart, 60, True)
        put_scope("opsi_stats", [])
        self._render_opsi_stats()
        self.task_handler.add(self._render_opsi_stats, 60, True)
        put_scope("ship_exp_table", [])
        self._render_ship_exp()
        self.task_handler.add(self._render_ship_exp, 60, True)
        put_scope("commission_income", [])
        self._render_commission_income()
        self.task_handler.add(self._render_commission_income, 60, True)
