"""旧版统计页的整页数据。

上游的新统计页（``frontend/src/pages/Statistics.tsx`` + ``statistics_service``）按分类
提供数据，而本地保留的旧版统计页是「一页多面板」：资源仪表盘、体力变化图表、
大世界数据收集、本月耄耋相接收获、舰船经验、委托收益统计。旧版主题
（``legacy-light`` / ``legacy-dark``）下要在 React 里整页还原这六个面板，因此这里
按面板组织一次返回。

口径必须与旧界面一致，对应实现见：

- 体力变化图表：``module/webui/app_stat_action_point.py``
- 大世界数据收集：``module/webui/app_stat_opsi.py``
  （``_build_cl1_summary`` / ``_build_meow_stats_by_level`` / ``_build_hazard_rows``）
- 本月耄耋相接收获：``module/webui/app_stat_opsi_export.py``
- 舰船经验：``module/webui/app_stat_ship.py``
- 委托收益统计：``module/webui/app_stat_commission.py``

两边任何一边改了统计口径，都要同步另一边。这里只返回**数据与 i18n 键**，不做
翻译与文案拼接 —— 文案由前端用 ``Gui.Stat.*`` 的既有翻译渲染，语言与旧界面一致。
"""
from datetime import datetime, timedelta

from module.api.protocol import ApiError

# 资源仪表盘显示的 8 项（旧版统计页顶部）：行动力/黄币/紫币/舰队币不显示，
# 避免与下方体力图表重复。顺序与 module/webui/app_dashboard.py 的显示清单一致。
DASHBOARD_KEYS = ('Oil', 'Coin', 'Gem', 'Pt', 'Cube', 'Core', 'Medal', 'Merit')

# 三行表里各侵蚀等级每轮的行动力消耗（侵蚀1 一轮 = 2 场 × 5 点）。
AP_COST_PER_ROUND = {1: 5, 3: 15, 5: 30}
# 三行表的行序：侵蚀1 与耄耋相接（3 / 5）合并显示。
HAZARD_ROW_ORDER = (1, 5, 3)
# 该格无数据时的占位，与旧界面一致的 ASCII 短横。
DASH = '-'

# 委托收益「最近记录」的分页口径（旧界面：最多 50 条、每页 10 条、最多 5 页）。
COMMISSION_RECENT_LIMIT = 50
COMMISSION_PAGE_SIZE = 10
COMMISSION_MAX_PAGES = 5

# 面板列描述：key 是 i18n 键，format 决定前端的数值格式。
def _column(key, fmt='text'):
    return {'key': key, 'format': fmt}


def _int_or_dash(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return DASH


def _float_or_dash(value, digits=1):
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return DASH


def _sign(value):
    """汇总行的正负着色：正数 gain（红）、负数 loss（深绿），0 与无数据不着色。"""
    try:
        number = float(str(value).strip().rstrip('%'))
    except (TypeError, ValueError):
        return ''
    if number > 0:
        return 'gain'
    if number < 0:
        return 'loss'
    return ''


def _parse_month(month):
    """校验并拆分 ``YYYY-MM``，缺省为当前月。"""
    now = datetime.now()
    if not month:
        return now.year, now.month, f'{now.year:04d}-{now.month:02d}'
    try:
        selected = datetime.strptime(month, '%Y-%m')
    except ValueError as exc:
        raise ApiError('INVALID_PARAMS', '月份格式应为 YYYY-MM') from exc
    if not 2020 <= selected.year <= 9998:
        raise ApiError('INVALID_PARAMS', '统计月份应在 2020 至 9998 年之间')
    return selected.year, selected.month, f'{selected.year:04d}-{selected.month:02d}'


def _ap_panel(instance):
    """体力变化图表的序列。

    旧界面的时间范围按钮（近24小时/近七天/本月，默认近七天）只在已有数据上过滤，
    因此这里一次给出「上个月 + 本月」的点，由前端按范围裁剪：跨月的近七天不需要
    再请求一次。序列与旧图例一致：行动力 / 黄币 / 紫币 / 海里数 / 资产。
    """
    from module.statistics.opsi_month import get_ap_timeline, get_coins_timeline

    now = datetime.now()
    previous = now.replace(day=1) - timedelta(days=1)
    months = [(previous.year, previous.month), (now.year, now.month)]

    ap_rows, coin_rows = [], []
    for year, month in months:
        ap_rows.extend(get_ap_timeline(year, month, instance))
        coin_rows.extend(get_coins_timeline(year, month, instance))

    def points(rows, key, transform=None, fallback_key=None, extra_key=None):
        result = []
        for row in rows:
            value = row.get(key)
            # 同一列不同记录可能只有其中一个字段：逐行回落到旧字段
            if value is None and fallback_key is not None:
                value = row.get(fallback_key)
            if value is None:
                continue
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if transform is not None and not transform(value):
                continue
            point = {'time': str(row.get('ts', '')).replace('T', ' '), 'value': value}
            if extra_key is not None and row.get(extra_key) is not None:
                # 体力现值：旧界面概览行显示成「(现值) 总量」
                point['apNow'] = float(row[extra_key])
            result.append(point)
        result.sort(key=lambda item: item['time'])
        return result

    # 图例文案在旧界面里是硬编码中文（模板与 Python 生成的都是），因此这里给出
    # 固定文案而不是 i18n 键，保持与旧页面逐字一致。
    return {
        'series': [
            {'key': 'ap', 'label': '体力',
             'points': points(ap_rows, 'ap_total', fallback_key='ap', extra_key='ap_now')},
            {'key': 'yellow_coins', 'label': '黄币', 'points': points(coin_rows, 'yellow_coins')},
            # 旧界面只在紫币大于 0 时才画（未进入大世界时恒为 0，画出来是一条贴底的直线）
            {'key': 'purple_coins', 'label': '紫币',
             'points': points(coin_rows, 'purple_coins', lambda value: value > 0)},
            {'key': 'distance', 'label': '海里数', 'points': points(ap_rows, 'distance')},
            {'key': 'asset', 'label': '资产', 'points': points(ap_rows, 'asset')},
        ],
    }


def _opsi_panel(instance):
    """「雪风大人的大世界数据收集」：一张表三行（侵蚀1 / 5 / 3）+ 汇总行。"""
    from module.statistics.cl1_database import db as cl1_db
    from module.statistics.opsi_month import get_opsi_stats, compute_monthly_cl1_akashi_ap
    from module.statistics.ship_exp_stats import get_ship_exp_stats

    summary = get_opsi_stats(instance_name=instance).summary()
    month = summary.get('month', DASH)
    total_battles = summary.get('total_battles', DASH)

    try:
        battles = int(total_battles)
        rounds = (battles + 1) // 2
        sortie_cost = rounds * 5
    except (TypeError, ValueError):
        battles, rounds, sortie_cost = DASH, 0, 0

    encounters = _int_or_dash(summary.get('akashi_encounters', 0))
    devices = _int_or_dash(summary.get('siren_research_devices', 0) or 0)
    ap_bought = _int_or_dash(compute_monthly_cl1_akashi_ap(instance_name=instance))
    net_ap = ap_bought - sortie_cost if isinstance(ap_bought, int) and sortie_cost else DASH
    loop_eff = round(net_ap / sortie_cost * 100, 2) if isinstance(net_ap, int) and sortie_cost else DASH

    try:
        exp_stats = get_ship_exp_stats(instance_name=instance)
        avg_cl1_battle = _float_or_dash(exp_stats.get_average_battle_time())
        avg_cl1_round = _float_or_dash(exp_stats.get_average_round_time())
    except Exception:
        avg_cl1_battle = avg_cl1_round = DASH

    cl1_values = {
        'Gui.Stat.Month': month,
        'Gui.Stat.BattleCount': battles,
        'Gui.Stat.BattleRounds': rounds,
        'Gui.Stat.SortieCost': sortie_cost,
        'Gui.Stat.AkashiEncounters': encounters,
        'Gui.Stat.AkashiRate': round(encounters / rounds * 100, 2) if rounds else DASH,
        'Gui.Stat.AverageAP': int(ap_bought / encounters + 0.5) if isinstance(ap_bought, int) and encounters else DASH,
        'Gui.Stat.SirenResearchDevices': devices,
        'Gui.Stat.SirenResearchRate': round(devices / rounds * 100, 2) if rounds else DASH,
        'Gui.Stat.AvgBattleTimeHeader': avg_cl1_battle,
        'Gui.Stat.AvgRoundTime': avg_cl1_round,
    }

    # 侵蚀 3 / 5 取耄耋相接的月度数据。展示取整但计算仍用未取整的 effective_rounds，
    # 与旧界面一致（出击消耗 = 每轮消耗 × 未取整轮次）。
    meow = {}
    try:
        now = datetime.now()
        for hazard_level in (3, 5):
            data = cl1_db.get_meow_stats(instance or 'default', now.year, now.month, hazard_level=hazard_level)
            meow_rounds = float(data.get('effective_rounds', 0) or 0)
            meow_encounters = int(data.get('akashi_encounters', 0) or 0)
            meow_ap = int(data.get('akashi_ap', 0) or 0)
            meow_devices = int(data.get('siren_research_devices', 0) or 0)
            avg_battle_time = float(data.get('avg_battle_time', 0.0) or 0)
            avg_round_time = float(data.get('avg_round_time', 0.0) or 0)
            meow[hazard_level] = {
                'rounds': meow_rounds,
                'battle_count': int(data.get('battle_count', 0) or 0),
                'akashi_encounters': meow_encounters,
                'akashi_rate': round(meow_encounters / meow_rounds * 100, 2) if meow_rounds > 0 else DASH,
                'avg_ap': str(int(meow_ap / meow_encounters + 0.5)) if meow_encounters > 0 else DASH,
                'siren_devices': meow_devices,
                'siren_rate': round(meow_devices / meow_rounds * 100, 2) if meow_rounds > 0 else DASH,
                'avg_battle_time': _float_or_dash(avg_battle_time) if avg_battle_time > 0 else DASH,
                'avg_round_time': _float_or_dash(avg_round_time) if avg_round_time > 0 else DASH,
            }
    except Exception:
        meow = {}

    columns = [
        _column('Gui.Stat.Month'),
        _column('Gui.Stat.HazardLevel', 'int'),
        _column('Gui.Stat.BattleCount', 'int'),
        _column('Gui.Stat.BattleRounds', 'int'),
        _column('Gui.Stat.SortieCost', 'int'),
        _column('Gui.Stat.AkashiEncounters', 'int'),
        _column('Gui.Stat.AkashiRate', 'percent'),
        _column('Gui.Stat.AverageAP', 'int'),
        _column('Gui.Stat.SirenResearchDevices', 'int'),
        _column('Gui.Stat.SirenResearchRate', 'percent'),
        _column('Gui.Stat.AvgBattleTimeHeader', 'seconds'),
        # 列头与取值统一用 AvgRoundTime：旧界面取值走 AvgMeowRoundTime，
        # 两键在英文下文案不同，导致英文界面侵蚀3/5 行该列恒为占位符。
        _column('Gui.Stat.AvgRoundTime', 'seconds'),
    ]

    rows = []
    for hazard_level in HAZARD_ROW_ORDER:
        if hazard_level == 1:
            row = [month, hazard_level, battles]
            for label in ('Gui.Stat.BattleRounds', 'Gui.Stat.SortieCost', 'Gui.Stat.AkashiEncounters',
                          'Gui.Stat.AkashiRate', 'Gui.Stat.AverageAP', 'Gui.Stat.SirenResearchDevices',
                          'Gui.Stat.SirenResearchRate', 'Gui.Stat.AvgBattleTimeHeader', 'Gui.Stat.AvgRoundTime'):
                row.append(cl1_values.get(label, DASH))
            rows.append(row)
            continue
        data = meow.get(hazard_level)
        if data is None:
            # 该侵蚀等级这个月没有任何耄耋相接记录：整行占位符
            rows.append([month, hazard_level, *([DASH] * 10)])
            continue
        rounds_value = float(data.get('rounds') or 0)
        cost_per_round = AP_COST_PER_ROUND.get(hazard_level, 0)
        rows.append([
            month,
            hazard_level,
            data.get('battle_count', DASH),
            # 出击轮次取整展示（0 也照旧显示 0，与旧界面一致）
            int(round(rounds_value)),
            # 出击消耗按未取整的轮次算
            int(round(rounds_value * cost_per_round)) if rounds_value > 0 and cost_per_round > 0 else DASH,
            data.get('akashi_encounters', DASH),
            data.get('akashi_rate', DASH),
            data.get('avg_ap', DASH),
            data.get('siren_devices', DASH),
            data.get('siren_rate', DASH),
            data.get('avg_battle_time', DASH),
            data.get('avg_round_time', DASH),
        ])

    summary_items = [
        {'key': 'Gui.Stat.MonthlyPurchasedAP', 'value': ap_bought, 'format': 'int', 'sign': ''},
        {'key': 'Gui.Stat.MonthlySortieCost', 'value': sortie_cost if rounds else DASH, 'format': 'int', 'sign': ''},
        {'key': 'Gui.Stat.MonthlyNetAP', 'value': net_ap, 'format': 'int', 'sign': _sign(net_ap)},
        {'key': 'Gui.Stat.MonthlyLoopEfficiency', 'value': loop_eff, 'format': 'percent', 'sign': _sign(loop_eff)},
    ]
    return {'summary': summary_items, 'columns': columns, 'rows': rows}


def _meow_loot_panel(instance, year, month):
    """「本月 / 历史耄耋相接收获」：月度掉落 + 累计平均值，按侵蚀等级两行。"""
    from module.statistics.azurstats import AzurStats
    from module.statistics.cl1_database import db as cl1_db

    month_str = f'{year:04d}-{month:02d}'
    # 掉落列与可用月份**不按实例过滤**，与旧界面一致：掉落库里 2026-09 之前的记录
    # instance 列是 NULL（实例隔离是后来才加的），按实例查会把这些历史数据全部滤掉，
    # 面板看起来就像「数据都没了」。只有下面的「出击轮次」按实例取（也跟旧界面一致）。
    loot_totals = AzurStats.get_meow_loot_monthly_totals(year=year, month=month)
    # 历史月份选择器里只列「有掉落数据的其它月份」，当前月由「回到本月」按钮承担
    current_month = (datetime.now().year, datetime.now().month)
    available_months = [
        f'{item_year:04d}-{item_month:02d}'
        for item_year, item_month in (AzurStats.get_meow_loot_available_months() or [])
        if (item_year, item_month) != current_month
    ]

    try:
        cumulative = [[float(value) for value in row] for row in AzurStats.load_meowofficer_farming() if float(row[2]) > 0]
    except Exception:
        cumulative = []
    extra = {
        int(row[0]): [
            int(round(row[2])),
            # 四个平均值本身很小（如 0.002），保留 6 位小数才看得出差异
            *(f'{value:.6f}' for value in row[3:7]),
        ]
        for row in cumulative
    }

    rows = []
    for hazard_level in (3, 5):
        loot = loot_totals.get(hazard_level, {}) or {}
        try:
            meow_data = cl1_db.get_meow_stats(instance, year, month, hazard_level=hazard_level)
            rounds = int(round(float(meow_data.get('effective_rounds', 0) or 0)))
        except Exception:
            rounds = 0
        row = [
            month_str, hazard_level, rounds,
            int(loot.get('Plate', 0) or 0),
            int(loot.get('GearDesignPlanT5', 0) or 0),
            int(loot.get('OrdnanceTestingReportT4', 0) or 0),
            int(loot.get('CoordinateObscure', 0) or 0),
            int(loot.get('CoordinateAbyssal', 0) or 0),
            int(loot.get('CatT3', 0) or 0),
            *extra.get(hazard_level, [DASH] * 5),
        ]
        rows.append(row)

    latest = max((row[1] for row in cumulative), default=0)
    return {
        'month': month_str,
        'isCurrentMonth': (year, month) == (datetime.now().year, datetime.now().month),
        'availableMonths': available_months,
        'lastRecord': datetime.fromtimestamp(latest).strftime('%Y-%m-%d %H:%M:%S') if latest > 0 else DASH,
        'columns': [
            _column('Gui.Stat.Month'),
            _column('Gui.Stat.HazardLevel', 'int'),
            _column('Gui.Stat.BattleRounds', 'int'),
            # 这六列旧界面就是硬编码中文（不分语言），保持原样以免与旧页面对不上
            _column('金菜', 'int'),
            _column('彩图纸', 'int'),
            _column('金机密', 'int'),
            _column('隐秘', 'int'),
            _column('深渊', 'int'),
            _column('金猫箱', 'int'),
            _column('Gui.Stat.MeowEffectiveRounds', 'int'),
            _column('Gui.Stat.MeowAvgOperationCoin'),
            _column('Gui.Stat.MeowAvgPlate'),
            _column('Gui.Stat.MeowAvgAbyssal'),
            _column('Gui.Stat.MeowAvgObscure'),
        ],
        'rows': rows,
    }


def _ship_panel(instance):
    """「每日经验检测 / 舰船升级进度」面板。"""
    from module.statistics.opsi_month import get_opsi_stats
    from module.statistics.ship_exp_stats import get_ship_exp_stats

    stats = get_ship_exp_stats(instance_name=instance)
    data = stats.data or {}
    ships = data.get('ships') or []
    if not ships:
        return {'hasData': False, 'columns': [], 'rows': []}

    current_battles = get_opsi_stats(instance_name=instance).summary().get('total_battles', 0)
    target_level = data.get('target_level', 125)
    today_stats = stats.get_today_stats()
    rows = []
    for ship in ships:
        progress = stats.calculate_progress(ship, target_level, current_battles)
        rows.append([
            progress['position'],
            progress['level'],
            progress['current_exp'],
            progress['total_exp'],
            progress['target_exp'],
            today_stats.get('battle_count', 0) if today_stats else 0,
            progress['exp_needed'],
            progress['battles_needed'],
            progress['time_needed'],
        ])
    return {
        'hasData': True,
        'hasToday': bool(today_stats),
        'lastCheckTime': data.get('last_check_time', DASH),
        'expPerHour': round(float(stats.get_exp_per_hour() or 0)),
        'todayExp': (today_stats or {}).get('total_exp_gained', 0),
        'todayRunMinutes': int((today_stats or {}).get('total_run_time', 0) // 60),
        'columns': [
            _column('Gui.Stat.ShipSlot'),
            _column('Gui.Stat.Level', 'int'),
            _column('Gui.Stat.CurrentExpThisLevel', 'int'),
            _column('Gui.Stat.TotalExp', 'int'),
            _column('Gui.Stat.TargetExpRequired', 'int'),
            _column('Gui.Stat.BattlesCompleted', 'int'),
            _column('Gui.Stat.ExpRemaining', 'int'),
            _column('Gui.Stat.SortiesNeeded', 'int'),
            _column('Gui.Stat.EstimatedTime'),
        ],
        'rows': rows,
    }


def _commission_periods(instance):
    """委托收益的三个区间摘要（今日 / 本周 / 本月）。

    旧界面切区间只重算卡片与脚注，因此三个区间一次算完，切区间不必再请求；
    区间口径沿用旧界面的 ``get_commission_income_summary``（只读当前自然月桶）。
    """
    from module.statistics.commission_income_stats import (
        get_commission_income_summary,
        COMMISSION_ITEM_META,
        COMMISSION_TRACKED_ITEMS,
    )

    periods = {}
    for period in ('day', 'week', 'month'):
        summary = get_commission_income_summary(instance, period)
        items = summary.get('items', {})
        cards = []
        for index, name in enumerate(COMMISSION_TRACKED_ITEMS, start=1):
            item = items.get(name) or {}
            cards.append({
                'name': name,
                'index': index,
                'color': (COMMISSION_ITEM_META.get(name) or {}).get('color', '#888'),
                'labelKey': f'Gui.Stat.CommissionIncomeItem{name}',
                'total': item.get('total', 0) or 0,
                'count': item.get('count', 0) or 0,
                'avg': round(float(item.get('avg', 0) or 0), 1),
            })
        periods[period] = {'cards': cards, 'totalCommissions': summary.get('total_commissions', 0) or 0}
    return periods


def _commission_recent(instance):
    """「最近委托记录」：最新 50 条，10 条一页，含收益截图链接。

    旧界面每条记录是一行「时间 + 物品胶囊 + 查看截图」，不是表格：时间取
    ``MM-DD HH:MM``；只显示被统计的五种资源（数量 > 0），其余物品不显示；
    没有可显示物品时前端给一个弱化的占位。
    """
    from module.statistics.commission_income_stats import (
        get_recent_commission_entries,
        COMMISSION_ITEM_META,
        COMMISSION_ITEM_NAME_MAP,
        COMMISSION_TRACKED_ITEMS,
    )

    entries = get_recent_commission_entries(instance, limit=COMMISSION_RECENT_LIMIT)
    rows = []
    for entry in entries:
        raw_ts = str(entry.get('ts', ''))
        try:
            time_text = datetime.fromisoformat(raw_ts).strftime('%m-%d %H:%M')
        except ValueError:
            time_text = raw_ts[:16] if raw_ts else '--'

        items = []
        for raw_name, amount in (entry.get('items') or {}).items():
            if not amount or int(amount) <= 0:
                continue
            name = COMMISSION_ITEM_NAME_MAP.get(raw_name, raw_name)
            if name not in COMMISSION_TRACKED_ITEMS:
                continue
            items.append({
                'name': name,
                'labelKey': f'Gui.Stat.CommissionIncomeItem{name}',
                'color': (COMMISSION_ITEM_META.get(name) or {}).get('color', '#888'),
                'icon': COMMISSION_TRACKED_ITEMS.index(name) + 1,
                'amount': int(amount),
            })
        # 旧界面一条记录只对应一次收获，因此只渲染第一张截图
        screenshots = [str(path) for path in (entry.get('screenshots') or [])]
        rows.append({
            'time': time_text,
            'items': items,
            'screenshot': f'/static/commission_rewards/{screenshots[0]}' if screenshots else None,
        })
    return {
        'rows': rows,
        'pageSize': COMMISSION_PAGE_SIZE,
        'maxPages': COMMISSION_MAX_PAGES,
        'limit': COMMISSION_RECENT_LIMIT,
    }


def _commission_running(instance):
    """「正在进行」：读 worker 写的运行状态文件。"""
    from module.commission.running_state import read_running_state

    state = read_running_state(instance)
    return {
        'available': state.available,
        'scannedAt': datetime.fromtimestamp(state.updated_at).strftime('%H:%M') if state.updated_at else None,
        'items': [
            {'name': item['name'], 'finish': item['finish'], 'rare': item['rare']}
            for item in (state.commissions or [])
        ],
    }


def report(configs, instance, month=None):
    """返回旧版统计页整页数据。

    Args:
        configs: 配置服务，用于校验实例存在。
        instance: 实例名。
        month: 耄耋相接收获查看的月份（``YYYY-MM``），缺省为当前月。

    Returns:
        dict: 见各 ``_*_panel`` 的返回结构；``instance`` 与 ``month`` 回显请求值。
    """
    configs.path(instance)
    year, month_number, month_key = _parse_month(month)

    # 一次渲染会经由多条路径重复读取同一个月份的月度 blob（每次都要反序列化
    # 数 MB 的 JSON），旧界面用只读缓存包住整轮渲染，这里同样处理。
    from module.statistics.cl1_database import db as cl1_db
    with cl1_db.read_cache():
        return {
            'instance': instance,
            'month': month_key,
            'dashboardKeys': list(DASHBOARD_KEYS),
            'apChart': _ap_panel(instance),
            'opsi': _opsi_panel(instance),
            'meowLoot': _meow_loot_panel(instance, year, month_number),
            'shipExp': _ship_panel(instance),
            'commission': {
                'periods': _commission_periods(instance),
                'recent': _commission_recent(instance),
                'running': _commission_running(instance),
            },
        }
