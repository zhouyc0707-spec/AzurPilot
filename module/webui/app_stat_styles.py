"""WebUI 统计页的按钮与分段控件样式。

统计页原先混用了三套按钮配色：``color="off"`` 的白底描边（刷新/导出）、
Bootstrap 的深灰 ``secondary`` 与蓝色 ``primary``（委托周期、查看历史月份）。
三者的高度、圆角、字号都不一致，横向排布时也不与标题对齐。

这里统一成「刷新」按钮那套白底描边：中性态与选中态只在底色和文字颜色上
区分，形状、字号、边框宽度完全一致，因此并排时不会一高一低。配色走主题
变量 ``--alas-entry-*``，四个主题自动跟随，不需要按主题各写一份。

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
# 按钮本体与选中态（Bootstrap 用 .btn-primary 表示当前项）
_BUTTON = f"{_SCOPE} .btn"
_BUTTON_HOVER = _BUTTON.replace(".btn", ".btn:hover")
_BUTTON_ACTIVE = _BUTTON.replace(".btn", ".btn-primary")
_BUTTON_ACTIVE_HOVER = _BUTTON.replace(".btn", ".btn-primary:hover")
# 分段控件：容器与组内按钮
_GROUP = f"{_SCOPE} .btn-group"
_GROUP_BTN = f"{_SCOPE} .btn-group .btn"
_GROUP_BTN_HOVER = f"{_SCOPE} .btn-group .btn:hover"

# 注入的样式只作用于上列作用域，不波及其它页面的按钮。
BUTTON_STYLE = f"""
<style>
/* --- 统计页统一按钮样式开始 --- */
/* 中性态：与「刷新」按钮一致的白底描边 */
{_BUTTON} {{
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

{_BUTTON_HOVER} {{
    background: var(--alas-entry-accent-soft) !important;
    border-color: var(--alas-entry-accent) !important;
    color: var(--alas-entry-text) !important;
}}

/* 选中态：保持同样的白底描边形状，只把底色换成浅主色、文字换主色。
   图表视图、委托周期、分页当前页、弹窗里的“本月”都用这个状态。 */
{_BUTTON_ACTIVE} {{
    border-color: var(--alas-entry-accent) !important;
    background: var(--alas-entry-accent-soft) !important;
    color: var(--alas-entry-accent) !important;
    font-weight: 700 !important;
}}

{_BUTTON_ACTIVE_HOVER} {{
    border-color: var(--alas-entry-accent) !important;
    background: var(--alas-entry-accent-soft) !important;
    color: var(--alas-entry-accent) !important;
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

/* 分段控件里的选中项：容器已占用外框与圆角，这里只填底色 */
{_SCOPE} .btn-group .btn-primary {{
    background: var(--alas-entry-accent-soft) !important;
    color: var(--alas-entry-accent) !important;
    font-weight: 700 !important;
}}

{_SCOPE} .btn-group .btn-primary:hover {{
    background: var(--alas-entry-accent-soft) !important;
    color: var(--alas-entry-accent) !important;
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
