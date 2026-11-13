"""困难图按真实维护日期选择每日服务器运行时间。"""
from datetime import datetime, time


def hard_run_time(server_day, run_at_18, maintenance_date=None):
    """返回指定服务器日期的运行时间，仅维护日可延后到18点。"""
    day = server_day.date() if isinstance(server_day, datetime) else server_day
    hour = 18 if run_at_18 and day == maintenance_date else 0
    return datetime.combine(server_day, time(hour=hour))
