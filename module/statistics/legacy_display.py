"""旧版统计展示的记录存在判断，不改变统计聚合与持久化口径。"""

from datetime import datetime
from math import isfinite


def _has_positive(data, keys):
    if not isinstance(data, dict):
        return False
    for key in keys:
        try:
            value = float(data.get(key, 0) or 0)
        except (TypeError, ValueError):
            continue
        if isfinite(value) and value > 0:
            return True
    return False


def _has_samples(data, keys):
    if not isinstance(data, dict):
        return False
    for key in keys:
        values = data.get(key)
        if isinstance(values, dict):
            values = values.get('samples')
        if isinstance(values, list) and values:
            return True
    return False


def has_cl1_records(summary, ship_data=None):
    """只接受月度活动或当前月的经验记录，不把默认耗时当成活动。"""
    if _has_positive(summary, ('total_battles', 'akashi_encounters', 'siren_research_devices')):
        return True
    raw = summary.get('raw') or {}
    if _has_positive(raw, ('battle_count', 'akashi_encounters')):
        return True
    # 旧版总购买值也会累计耄耋购买；有分源明细时只认侵蚀一来源。
    if not raw.get('akashi_ap_entries') and _has_positive(raw, ('akashi_ap',)):
        return True
    if _has_samples(raw, ('battle_times', 'round_times')):
        return True
    for key in ('akashi_ap_entries', 'siren_research_device_entries'):
        if any(isinstance(row, dict) and row.get('source') == 'cl1' for row in raw.get(key, [])):
            return True
    daily = (ship_data or {}).get('daily_stats') or {} if isinstance(ship_data, dict) else {}
    month = str(summary.get('month', ''))
    return any(str(day).startswith(month + '-') and _has_positive(
        value, ('battle_count', 'total_run_time', 'total_exp_gained'))
        for day, value in daily.items())


def has_meow_records(data, hazard_level, month_data=None):
    """用未取整轮次及对应等级的实际活动/样本判断，避免跨等级样本混入。"""
    keys = ('effective_rounds', 'battle_count', 'battle_raw_count', 'akashi_encounters',
            'akashi_ap', 'siren_research_devices', 'avg_battle_time', 'avg_round_time', 'sample_count')
    if _has_positive(data, keys):
        return True
    by_hazard = data.get('by_hazard') or {} if isinstance(data, dict) else {}
    if _has_positive(by_hazard.get(str(hazard_level), {}), keys):
        return True
    if not isinstance(month_data, dict):
        return False
    buckets = month_data.get('meow_hazard_stats') or {}
    bucket = buckets.get(str(hazard_level), buckets.get(hazard_level, {}))
    if _has_positive(bucket, keys) or _has_samples(bucket, ('round_times', 'battle_times')):
        return True
    # 旧格式的全局耗时列表只接受明确标注当前等级的样本。
    return any(isinstance(row, dict) and row.get('hazard_level') == hazard_level
               for row in month_data.get('meow_round_times', []))


def monthly_meow_record_levels(year, month, instance=None):
    """读取本月掉落明细的等级；无高价值物品的奖励也是真实记录。

    月度收获省略实例，数据收集传当前实例。复用统计读取接口的
    数据库迁移检查及旧载荷兼容读取；未知或无效等级仍归入侵蚀 5。
    """
    from module.statistics.azurstats import AzurStats

    start = datetime(year, month, 1)
    end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    try:
        scope = {'instance': instance} if instance is not None else {}
        rows = AzurStats.load_opsi_drop_rows(
            start=int(start.timestamp()), end=int(end.timestamp()), task='opsi_meowfficer_farming', **scope)
    except Exception:
        from module.logger import logger

        logger.warning('[统计-旧版] 读取月度耄耋记录等级失败', exc_info=True)
        return set()
    levels = set()
    for row in rows:
        try:
            level = int(row.get('hazard_level'))
        except (TypeError, ValueError):
            level = 5
        levels.add(level if 1 <= level <= 6 else 5)
    return levels
