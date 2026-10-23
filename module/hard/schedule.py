"""困难图按维护星期选择每日服务器运行时间。"""
from datetime import datetime, time


def hard_run_time(server_day, run_at_18, maintenance_weekday):
    """返回指定服务器日期的运行时间，仅维护日可延后到18点。"""
    weekdays = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')
    hour = 18 if run_at_18 and server_day.weekday() == weekdays.index(maintenance_weekday) else 0
    return datetime.combine(server_day, time(hour=hour))
