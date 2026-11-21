"""大世界（Operation Siren）月度统计模块。
从加密 SQLite 数据库中读取战斗数据，
计算月度练级效率、资源投入和战斗次数等汇总指标。"""

# 此文件专门用于统计分析大世界（Operation Siren）的月度练级效率与资源投入数据。
# 负责从加密 SQLite 数据库中读取统计数据，并具备计算概况与详细指标的功能。
from __future__ import annotations

from calendar import monthrange
from datetime import datetime, timedelta
from itertools import combinations
from pathlib import Path
from statistics import median
from typing import Dict, Any, Optional

from module.logger import logger
from module.statistics.cl1_database import db as cl1_db


class OpsiMonthStats:
    def __init__(self, instance_name: str | None = None) -> None:
        self._instance_name = instance_name or "default"

    def summary(
        self, year: int | None = None, month: int | None = None
    ) -> Dict[str, Any]:
        """获取指定月份的 CL1 统计基础摘要。

        Args:
            year (int | None, optional): 统计年份。默认为当前年。
            month (int | None, optional): 统计月份。默认为当前月。

        Returns:
            Dict[str, Any]: 包含总战斗次数、明石遭遇次数、塞壬装置数等摘要数据。
        """
        now = datetime.now()
        if year is None:
            year = now.year
        if month is None:
            month = now.month
        key = f"{year:04d}-{month:02d}"

        # 从数据库读取数据
        data = cl1_db.get_stats(self._instance_name, key)

        total = int(data.get("battle_count", 0))
        akashi = int(data.get("akashi_encounters", 0))
        siren_research_devices = cl1_db.get_siren_research_device_count(
            data, source="cl1"
        )

        return {
            "month": key,
            "total_battles": total,
            "akashi_encounters": akashi,
            "siren_research_devices": siren_research_devices,
            "raw": data,
        }

    def get_detailed_summary(
        self, year: int | None = None, month: int | None = None
    ) -> Dict[str, Any]:
        """
        获取详细的统计摘要,包含所有计算指标
        """
        now = datetime.now()
        if year is None:
            year = now.year
        if month is None:
            month = now.month
        key = f"{year:04d}-{month:02d}"

        # 从数据库读取数据
        data = cl1_db.get_stats(self._instance_name, key)

        # 基础数据
        battle_count = int(data.get("battle_count", 0))
        akashi_encounters = int(data.get("akashi_encounters", 0))
        akashi_ap = int(data.get("akashi_ap", 0))
        siren_research_devices = cl1_db.get_siren_research_device_count(
            data, source="cl1"
        )

        # 计算衍生指标
        battle_rounds = battle_count // 2
        sortie_cost = battle_rounds * 120

        akashi_probability = (
            round(akashi_encounters / battle_rounds, 4) if battle_rounds > 0 else 0.0
        )
        siren_research_probability = (
            round(siren_research_devices / battle_rounds, 4)
            if battle_rounds > 0
            else 0.0
        )
        average_stamina = (
            round(akashi_ap / akashi_encounters, 2) if akashi_encounters > 0 else 0.0
        )

        return {
            "month": key,
            "battle_count": battle_count,
            "battle_rounds": battle_rounds,
            "sortie_cost": sortie_cost,
            "akashi_encounters": akashi_encounters,
            "akashi_probability": akashi_probability,
            "siren_research_devices": siren_research_devices,
            "siren_research_probability": siren_research_probability,
            "average_stamina": average_stamina,
            "net_stamina_gain": akashi_ap,
        }


_singleton: Dict[str, OpsiMonthStats] = {}


def get_opsi_stats(instance_name: str | None = None) -> OpsiMonthStats:
    """获取指定实例的大世界月度统计单例对象。

    Args:
        instance_name (str | None, optional): 配置实例名称。默认为 "default"。

    Returns:
        OpsiMonthStats: 对应的月度统计管理器实例。
    """
    global _singleton
    key = instance_name or "default"
    if key not in _singleton:
        _singleton[key] = OpsiMonthStats(instance_name=instance_name)
    return _singleton[key]


def compute_monthly_cl1_akashi_ap(
    year: int | None = None,
    month: int | None = None,
    campaign: str = "opsi_akashi",
    instance_name: str | None = None,
) -> int:
    """
    计算指定月份从明石商店购买的行动力总额
    """
    now = datetime.now()
    if year is None:
        year = now.year
    if month is None:
        month = now.month
    key_prefix = f"{year:04d}-{month:02d}"

    instance_name = instance_name or "default"
    data = cl1_db.get_stats(instance_name, key_prefix)

    return int(data.get("akashi_ap", 0))


def get_ap_timeline(
    year: int | None = None, month: int | None = None, instance_name: str | None = None
) -> list:
    """
    获取行动力变化时间序列数据（真实体力剩余），用于绘制体力变化曲线。

    返回按时间排序的数据点列表，每个数据点包含:
    - ts: ISO 格式时间戳
    - ap: 当时的行动力剩余
    - source: 数据来源 (cl1 / meow)

    Args:
        year: 年份，默认当前年
        month: 月份，默认当前月
        instance_name: 实例名称

    Returns:
        list[dict]: 时间序列数据点
    """
    now = datetime.now()
    if year is None:
        year = now.year
    if month is None:
        month = now.month
    key_prefix = f"{year:04d}-{month:02d}"

    instance_name = instance_name or "default"
    data = cl1_db.get_stats(instance_name, key_prefix)

    snapshots = data.get("ap_snapshots", [])
    if not snapshots:
        return []

    # 按时间排序
    try:
        snapshots_sorted = sorted(snapshots, key=lambda e: e.get("ts", ""))
    except Exception:
        snapshots_sorted = snapshots

    return snapshots_sorted


def forecast_month_end_distance(snapshots: list, now: datetime) -> int | None:
    """按本月逐日实测的典型增速，从最近有效读数推算到月底。"""
    if not isinstance(snapshots, (list, tuple)):
        return None
    as_of = now.replace(tzinfo=None)
    start = as_of.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    daily = {}
    for row in snapshots:
        raw = row.get("distance") if isinstance(row, dict) else None
        if raw is None or isinstance(raw, bool):
            continue
        try:
            miles = int(raw)
            stamp = datetime.fromisoformat(row["ts"])
            if stamp.tzinfo is not None:
                stamp = stamp.astimezone(now.tzinfo).replace(tzinfo=None)
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            continue
        if miles < 0 or (isinstance(raw, float) and miles != raw) or not start <= stamp <= as_of:
            continue
        day = stamp.date()
        if day not in daily or stamp >= daily[day][0]:
            daily[day] = (stamp, miles)

    # 同月海里数只能持平或增加。最多 31 个日样本，取最长非递减序列，
    # 让单次 OCR 跳高或回落都能在后续正常读数出现后恢复。
    sequences = []
    for stamp, miles in sorted(daily.values()):
        prefix = max((seq for seq in sequences if seq[-1][1] <= miles), key=len, default=[])
        sequences.append([*prefix, (stamp, miles)])
    # 支持天数相同则选择尾值较低的序列，避免一次跳高后的首个正常读数仍被排除。
    samples = max(sequences, key=lambda seq: (len(seq), -seq[-1][1]), default=[])
    if len(samples) < 2 or samples[-1][0] - samples[0][0] < timedelta(days=1):
        return None

    # 各对观测按实际间隔折算增速，取中位数可减小单日停玩或集中刷图的影响。
    rate = median((miles_b - miles_a) / (time_b - time_a).total_seconds()
                  for (time_a, miles_a), (time_b, miles_b) in combinations(samples, 2))
    last_time, last_miles = samples[-1]
    end = start + timedelta(days=monthrange(start.year, start.month)[1])
    return round(last_miles + rate * (end - last_time).total_seconds())


def get_coins_timeline(
    year: int | None = None, month: int | None = None, instance_name: str | None = None
) -> list:
    """
    获取凭证变化时间序列数据（作战补给凭证/特别兑换凭证），用于绘制凭证变化曲线。

    返回按时间排序的数据点列表，每个数据点包含:
    - ts: ISO 格式时间戳
    - yellow_coins: 当时的作战补给凭证（黄币）数量
    - purple_coins: 当时的特别兑换凭证（紫币）数量
    - source: 数据来源 (cl1 / meow / other)

    Args:
        year: 年份，默认当前年
        month: 月份，默认当前月
        instance_name: 实例名称

    Returns:
        list[dict]: 时间序列数据点
    """
    now = datetime.now()
    if year is None:
        year = now.year
    if month is None:
        month = now.month
    key_prefix = f"{year:04d}-{month:02d}"

    instance_name = instance_name or "default"
    data = cl1_db.get_stats(instance_name, key_prefix)

    def time_key(row):
        try:
            return datetime.fromisoformat(row.get("ts", "")).isoformat(" ")
        except (TypeError, ValueError):
            return ""

    snapshots = list(data.get("coins_snapshots") or [])
    coin_times = {time_key(row) for row in snapshots} - {""}
    residue = data.get("coins_month_start_residue", [])
    has_residue = isinstance(residue, list) and any(
        isinstance(row, dict) and time_key(row) for row in residue
    )
    # 已归档的月初残留继续沿用原凭证序列的显示起点。
    start = min(coin_times, default=None) if has_residue else ""
    yellow_points = {}
    for row in data.get("yellow_coin_snapshots") or []:
        value = row.get("yellow_coin")
        stamp = time_key(row)
        if value is None or not stamp or start is None or stamp < start:
            continue
        point = {"ts": row["ts"], "yellow_coins": value}
        if "source" in row:
            point["source"] = row["source"]
        yellow_points[stamp] = point
    # 同时刻的完整凭证记录优先，黄币补点只提供已有的黄币字段。
    for row in snapshots:
        if row.get("yellow_coins") is not None:
            yellow_points.pop(time_key(row), None)
    snapshots.extend(yellow_points.values())
    return sorted(snapshots, key=time_key)


__all__ = [
    "get_opsi_stats",
    "OpsiMonthStats",
    "compute_monthly_cl1_akashi_ap",
    "forecast_month_end_distance",
    "get_ap_timeline",
    "get_coins_timeline",
    "get_asset_timeline",
    "get_resource_timeline",
]


def get_resource_timeline(
    instance_name: str | None = None, limit: int = 500
) -> list:
    """
    获取所有资源的快照时间序列数据，用于绘制资源变化趋势图。

    返回按时间排序的数据点列表，每个数据点包含:
    - ts: ISO 格式时间戳
    - oil, coin, gem, pt, cube, core, medal, merit, guild_coin,
      action_point, yellow_coin, purple_coin: 各资源数值（可能为 None）

    Args:
        instance_name: 实例名称
        limit: 最大返回条数

    Returns:
        list[dict]: 时间序列数据点
    """
    from module.statistics.resource_stats import get_resource_timeline as _get_timeline

    instance_name = instance_name or "default"
    return _get_timeline(instance=instance_name, limit=limit)


def get_asset_timeline(
    year: int | None = None, month: int | None = None, instance_name: str | None = None
) -> list:
    """获取 AP 快照中的资产时间线。"""
    now = datetime.now()
    if year is None:
        year = now.year
    if month is None:
        month = now.month
    key_prefix = f"{year:04d}-{month:02d}"

    data = cl1_db.get_stats(instance_name or "default", key_prefix)
    snapshots = data.get("ap_snapshots", [])
    return sorted([s for s in snapshots if s.get("ts") and s.get("ap_total") is not None
                   and s.get("source") != "hazard1_estimate"], key=lambda e: e.get("ts", ""))
