"""旧界面的岛屿规划只读详情，不注册配置 Pin 或触发游戏巡检。"""

from datetime import datetime
from html import escape
from math import isfinite

from module.webui.app_dependencies import put_collapse, put_html, t
from module.webui.app_helpers import build_simple_table
from module.webui.app_types import WebUIMixinBase


TEXT = {
    'Title': '查看规划详情',
    'GeneratedAt': '规划生成时间',
    'DailyRevenue': '理论日均收入',
    'DailyProfit': '理论日均净收益',
    'Disabled': '自动规划已关闭，以下保留最近规划供查阅。',
    'LegacyNotice': '旧计划仅能恢复目标和菜单；生产配方详情等待下一轮规划补齐。',
    'ObservationNotice': '现货是所列时间的最近实读结果。缺口仅按该次观测计算；最近下单仅表示预计产出，不抵扣现货缺口。'
                         '菜单为最近保存的规划安排；实际经营可能按季节或加成换菜，以经营任务当前结果为准。',
    'Items': '物品目标',
    'Item': '物品',
    'Target': '目标现货',
    'Floor': '库存保留线',
    'Buffer': '原料周转量',
    'Reserve': '预留量',
    'Demand': '额外及订单需求',
    'IdlePerDay': '余岗积累／日',
    'Observations': '最近巡检与下单',
    'Stock': '最近实读现货',
    'ObservedAt': '观测时间',
    'Source': '来源',
    'Shortfall': '按该次观测的现货缺口',
    'DispatchAmount': '最近下单预计产出',
    'DispatchAt': '下单时间',
    'PendingInspect': '待巡检',
    'PendingReview': '待复核',
    'NoDispatch': '暂无下单记录',
    'Production': '日均生产安排',
    'Recipe': '配方',
    'Place': '生产地点',
    'BatchesPerDay': '日均批次',
    'Menus': '经营菜单',
    'Menu': '店铺',
    'Capacity': '一货架数量',
    'PerDay': '理论日销量',
    'Empty': '暂无规划详情。',
}


def _text(key):
    full_key = f'Gui.IslandPlannerReport.{key}'
    translated = t(full_key)
    return TEXT[key] if not translated or translated == full_key else translated


def _number(value):
    """只接受有限数值；缺失值不能作为零库存参与缺口计算。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _amount(value):
    number = _number(value)
    # 配方日均批次可小于 0.01，保留报告的六位精度，避免把有效安排显示成零。
    return '-' if number is None else f'{number:.6f}'.rstrip('0').rstrip('.')


def _time(value):
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value).strftime('%Y-%m-%d %H:%M:%S')
        except ValueError:
            pass
    return '-'


def _name(item, id_key='id'):
    name = str(item.get('name') or item.get(id_key) or '-')
    item_id = item.get(id_key)
    return f'{name} ({item_id})' if item_id is not None and str(item_id) != name else name


def _table(keys, rows):
    # 报告含物品名称与来源文本；旧统计表模板不转义，在此统一处理。
    headers = [escape(_text(key)) for key in keys]
    safe_rows = [[escape(str(value)) for value in row] for row in rows]
    return '<div style="overflow-x:auto">' + build_simple_table(headers, safe_rows) + '</div>'


def _records(value):
    return [entry for entry in value if isinstance(entry, dict)] if isinstance(value, list) else []


def _observation_row(item, observations, dispatches):
    item_id = str(item.get('id'))
    observation = observations.get(item_id)
    observed_at, source = '-', '-'
    if not isinstance(observation, dict):
        stock_text = shortfall = _text('PendingInspect')
    else:
        observed_at = _time(observation.get('at'))
        source = observation.get('source') or '-'
        stock = _number(observation.get('stock'))
        if observation.get('stale') or stock is None or stock < 0 or observed_at == '-':
            stock_text = shortfall = _text('PendingReview')
        else:
            stock_text = _amount(stock)
            target = _number(item.get('target'))
            shortfall = _amount(max(target - stock, 0)) if target is not None else _text('PendingReview')

    dispatch = dispatches.get(item_id)
    if isinstance(dispatch, dict):
        amount = _number(dispatch.get('amount'))
        dispatch_at = _time(dispatch.get('at'))
        dispatch_amount = _amount(amount) if amount is not None and amount >= 0 else '-'
        dispatch_source = dispatch.get('source') or '-'
    else:
        dispatch_amount, dispatch_at, dispatch_source = _text('NoDispatch'), '-', '-'
    return [_name(item), stock_text, observed_at, source, shortfall,
            dispatch_amount, dispatch_at, dispatch_source]


def build_island_planner_report_html(report):
    """渲染已有报告快照，缺口不扣除预计产出，观测失效时不推断现货。"""
    if not isinstance(report, dict) or not report:
        return f'<p>{escape(_text("Empty"))}</p>'
    parts = ['<div class="island-planner-report">']
    if report.get('enabled') is False:
        parts.append(f'<p>{escape(_text("Disabled"))}</p>')
    if report.get('legacy'):
        parts.append(f'<p>{escape(_text("LegacyNotice"))}</p>')
    for key, value in (
        ('GeneratedAt', _time(report.get('generated_at'))),
        ('DailyRevenue', _amount(report.get('daily_revenue'))),
        ('DailyProfit', _amount(report.get('daily_profit'))),
    ):
        parts.append(f'<p>{escape(_text(key))}：{escape(value)}</p>')

    items = _records(report.get('items'))
    if items:
        parts.append(f'<h5>{escape(_text("Items"))}</h5>')
        keys = ('target', 'floor', 'buffer', 'reserve', 'demand', 'idle_per_day')
        parts.append(_table(
            ('Item', 'Target', 'Floor', 'Buffer', 'Reserve', 'Demand', 'IdlePerDay'),
            [[_name(item), *[_amount(item.get(key)) for key in keys]] for item in items],
        ))
        parts.append(f'<h5>{escape(_text("Observations"))}</h5>')
        parts.append(f'<p>{escape(_text("ObservationNotice"))}</p>')
        observations = report.get('observations')
        dispatches = report.get('dispatches')
        observations = observations if isinstance(observations, dict) else {}
        dispatches = dispatches if isinstance(dispatches, dict) else {}
        parts.append(_table(
            ('Item', 'Stock', 'ObservedAt', 'Source', 'Shortfall', 'DispatchAmount', 'DispatchAt', 'Source'),
            [_observation_row(item, observations, dispatches) for item in items],
        ))

    production = _records(report.get('production'))
    if production:
        parts.append(f'<h5>{escape(_text("Production"))}</h5>')
        parts.append(_table(('Recipe', 'Place', 'BatchesPerDay'), [
            [_name(recipe, 'recipe_id'), recipe.get('place') or '-', _amount(recipe.get('batches_per_day'))]
            for recipe in production
        ]))

    menus = _records(report.get('menus'))
    if menus:
        parts.append(f'<h5>{escape(_text("Menus"))}</h5>')
        menu_rows = []
        for menu in menus:
            foods = _records(menu.get('items'))
            for food in foods:
                menu_rows.append([_name(menu), _amount(menu.get('capacity')), _name(food), _amount(food.get('per_day'))])
            if not foods:
                menu_rows.append([_name(menu), _amount(menu.get('capacity')), '-', '-'])
        parts.append(_table(('Menu', 'Capacity', 'Item', 'PerDay'), menu_rows))
    parts.append('</div>')
    return ''.join(parts)


def _load_planner_report(config):
    # 仅进入岛屿规划组才加载报告实现，避免旧界面启动时加载游戏图像模块。
    from module.island.planner_report import get_planner_report
    return get_planner_report(config)


class IslandPlannerReportMixin(WebUIMixinBase):
    """把独立只读折叠详情交给配置页的批量输出树。"""

    def _build_island_planner_details(self, config):
        report = _load_planner_report(config)
        return put_collapse(
            title=_text('Title'),
            content=[put_html(build_island_planner_report_html(report))],
            open=False,
        )
