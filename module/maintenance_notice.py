"""国服官方港区改建公告的时间解析。"""
import html
import json
import re
from datetime import datetime, timedelta, timezone


CN = timezone(timedelta(hours=8))


def plain_text(value):
    value = re.sub(r'<(?:br\b[^>]*|/p|/div)>', '\n', value, flags=re.I)
    return html.unescape(re.sub(r'<[^>]*>', '', value))


def maintenance_window(title, body, published, *, require_downtime=False):
    """提取国服维护起止时间；排程只采用公告开头明确声明的停服时间。"""
    if '改建内容' not in body or not re.search(r'港区改建|维护公告', title):
        return None
    pattern = (r'(\d{1,2})月(\d{1,2})日\s*(\d{1,2})[:：](\d{2})'
               r'(?:\s*[~～—-]\s*(\d{1,2})[:：](\d{2}))?')
    if require_downtime:
        body = body.partition('改建内容')[0]
        if re.search(r'不停服|[无毋][需须]停服', body):
            return None
        pattern = r'(?:司令部将于|维护时间[』】]?[：:为]?)\s*' + pattern
    match = re.search(pattern, body)
    if not match:
        return None
    month, day, hour, minute, end_hour, end_minute = match.groups()
    posted = datetime.fromtimestamp(published, CN)
    year = posted.year + (1 if posted.month == 12 and int(month) == 1 else 0)
    start = datetime(year, int(month), int(day), int(hour), int(minute), tzinfo=CN)
    end = start.replace(hour=int(end_hour), minute=int(end_minute)) if end_hour else start
    if end < start:
        end += timedelta(days=1)
    return start, end


def cached_cn_start(directory, now):
    """从已保存的国服官方公告里取最近一次维护，不触发公告采集。"""
    path = directory / 'skin-collection' / 'announcements.json'
    try:
        archive = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(archive, dict) or archive.get('version') != 1:
        return None
    documents = archive.get('documents')
    if not isinstance(documents, dict):
        return None
    candidates = []
    for doc in documents.values():
        if not isinstance(doc, dict):
            continue
        try:
            published = doc['published']
            if published > now.timestamp():
                continue
            body = plain_text(doc['body'])
            window = maintenance_window(doc['title'], body, published, require_downtime=True)
            if window and window[0].date() >= now.date():
                candidates.append((window[0], int(doc.get('edited') or published)))
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            continue
    if candidates:
        return min(candidates, key=lambda item: (item[0].date(), -item[1]))[0]
    return None
