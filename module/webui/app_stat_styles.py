"""WebUI 统计页的按钮与分段控件样式。

统计页原先混用了三套按钮配色：``color="off"`` 的白底描边（刷新/导出）、
Bootstrap 的深灰 ``secondary`` 与蓝色 ``primary``（委托周期、查看历史月份）。
三者的高度、圆角、字号都不一致，横向排布时也不与标题对齐。

这里用主题变量 ``--alas-entry-*`` 定义一套统一的操作按钮与分段控件，
在统计面板挂载时注入一次；四个主题自动跟随，不需要按主题各写一份。

样式选择器都带 ``!important``：注入的 ``<style>`` 位于主题样式表之后，
需要盖掉 Bootstrap 的 ``.btn-primary/.btn-secondary`` 以及高级材质主题里
带 ``!important`` 的按钮规则。
"""

# 需要统一样式的统计区作用域。图表视图工具栏虽然挂在 statistics-content 下，
# 但它是从总览页的图表卡片里渲染的，因此一并列出。
BUTTON_SCOPES = (
    "#pywebio-scope-stat_panels_charts",  # 统计页与总览页的图表卡片
    "#pywebio-scope-ap_chart",  # 图表视图工具栏
    "#pywebio-scope-statistics-toolbar",  # 统计页顶部刷新
    "#pywebio-scope-opsi_stats",
    "#pywebio-scope-meow_loot_scope",
    "#pywebio-scope-commission_income",
)

# 用 :is() 汇总作用域：:is() 取参数中最高的特异性（这里都是 id 选择器），
# 因此可以压过高级材质主题里不带作用域前缀的 .btn-primary/.btn-secondary。
_SCOPE = ":is(" + ", ".join(BUTTON_SCOPES) + ")"
# 单元素选择器：普通按钮
_SIMPLE = f"{_SCOPE} .btn"
_SIMPLE_HOVER = _SIMPLE.replace(".btn", ".btn:hover")
_SIMPLE_PRIMARY = _SIMPLE.replace(".btn", ".btn-primary")
_SIMPLE_PRIMARY_HOVER = _SIMPLE.replace(".btn", ".btn-primary:hover")
# 分段控件：容器与组内按钮
_GROUP = f"{_SCOPE} .btn-group"
_GROUP_BTN = f"{_SCOPE} .btn-group .btn"
_GROUP_BTN_HOVER = f"{_SCOPE} .btn-group .btn:hover"
_GROUP_PRIMARY = f"{_SCOPE} .btn-group .btn-primary"
_GROUP_PRIMARY_HOVER = f"{_SCOPE} .btn-group .btn-primary:hover"

# 注入的样式只作用于上列作用域，不波及其它页面的按钮。
BUTTON_STYLE = f"""
<style>
/* --- 统计页统一按钮样式开始 --- */
{_SIMPLE} {{
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    height: 30px !important;
    padding: 0 14px !important;
    border: 1px solid var(--alas-entry-border) !important;
    border-radius: 12px !important;
    background: var(--alas-entry-surface) !important;
    color: var(--alas-entry-text) !important;
    font-size: 12px !important;
    font-weight: 600 !important;
    line-height: 1 !important;
    box-shadow: none !important;
    text-shadow: none !important;
    transition: background-color .15s ease, color .15s ease, border-color .15s ease !important;
}}

{_SIMPLE_HOVER} {{
    background: var(--alas-entry-accent-soft) !important;
    border-color: var(--alas-entry-accent) !important;
    color: var(--alas-entry-text) !important;
}}

/* 选中态：图表视图、委托周期、分页当前页、弹窗里的“本月” */
{_SIMPLE_PRIMARY} {{
    background: var(--alas-entry-accent) !important;
    border-color: var(--alas-entry-accent) !important;
    color: var(--alas-entry-on-accent) !important;
}}

{_SIMPLE_PRIMARY_HOVER} {{
    background: var(--alas-entry-action-hover) !important;
    border-color: var(--alas-entry-action-hover) !important;
    color: var(--alas-entry-on-accent) !important;
}}

/* 分段控件：外框由容器负责，组内按钮只保留分隔线。
   圆角保持 12px，由容器的 overflow:hidden 裁掉外侧两角，
   这样不必再写 :first-child/:last-child 的圆角例外。 */
{_GROUP} {{
    display: inline-flex !important;
    overflow: hidden !important;
    border: 1px solid var(--alas-entry-border) !important;
    border-radius: 12px !important;
    background: var(--alas-entry-surface) !important;
    box-shadow: none !important;
}}

{_GROUP_BTN} {{
    border: 0 !important;
    border-left: 1px solid var(--alas-entry-border) !important;
    background: transparent !important;
    color: var(--alas-entry-text) !important;
}}

{_GROUP_BTN_HOVER} {{
    background: var(--alas-entry-accent-soft) !important;
    color: var(--alas-entry-text) !important;
}}

{_GROUP_PRIMARY} {{
    border-color: var(--alas-entry-accent) !important;
    background: var(--alas-entry-accent) !important;
    color: var(--alas-entry-on-accent) !important;
}}

{_GROUP_PRIMARY_HOVER} {{
    background: var(--alas-entry-action-hover) !important;
    color: var(--alas-entry-on-accent) !important;
}}

/* 标题与按钮同排：固定行高与按钮一致，按内容居中，避免按钮被标题行高顶高 */
#pywebio-scope-opsi_stats .stat-row-title {{
    display: flex !important;
    align-items: center !important;
    height: 30px !important;
    margin: 0 !important;
    line-height: 1 !important;
}}
/* --- 统计页统一按钮样式结束 --- */
</style>
"""
