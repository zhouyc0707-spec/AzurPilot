"""从国服官方维护公告增量收集角色与换装，不读取或修改用户收藏。"""
import copy
import html
import json
import re
import threading
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from deploy.atomic import atomic_write
from module.base import http


CN = timezone(timedelta(hours=8))
SINCE = datetime(2026, 11, 12, tzinfo=CN)
SEED_ID = '869277954591424622'
SEED_TOPIC = '55020313'
TAP_API = 'https://www.taptap.cn/webapiv2/'
TAP_UA = 'V=1&PN=WebApp&VN=0.1.0&LANG=zh_CN&PLT=PC'
RARITIES = {'UR': 'ultra_rare', 'DR': 'ultra_rare', 'SSR': 'super_rare', 'SR': 'elite',
            'R': 'rare', 'N': 'common', '海上传奇': 'ultra_rare', '决战方案': 'ultra_rare',
            '超稀有': 'super_rare', '最高方案': 'super_rare', '精锐': 'elite',
            '稀有': 'rare', '普通': 'common'}
RARITY_PATTERN = r'海上传奇|决战方案|超稀有|最高方案|精锐|稀有|普通'


def _key(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value)).casefold()


def _plain(value):
    value = re.sub(r'<(?:br\b[^>]*|/p|/div)>', '\n', value, flags=re.I)
    return html.unescape(re.sub(r'<[^>]*>', '', value))


def _maintenance_at(title, body, published):
    """提取维护日期，以限定本次及后续公告，不将普通单品预告当作维护。"""
    if '改建内容' not in body or not re.search(r'港区改建|维护公告', title):
        return None
    match = re.search(r'(\d{1,2})月(\d{1,2})日\s*(\d{1,2})[:：](\d{2})'
                      r'(?:\s*[~～—-]\s*(\d{1,2})[:：](\d{2}))?', body)
    if not match:
        return None
    month, day, hour, minute, end_hour, end_minute = match.groups()
    posted = datetime.fromtimestamp(published, CN)
    year = posted.year + (1 if posted.month == 12 and int(month) == 1 else 0)
    at = datetime(year, int(month), int(day), int(end_hour or hour), int(end_minute or minute), tzinfo=CN)
    if end_hour and (int(end_hour), int(end_minute)) < (int(hour), int(minute)):
        at += timedelta(days=1)
    return at


def _candidates(body):
    """只接受明确的角色获取语句和换装段；编号变化即结束上一段。"""
    result = {}
    section = ''
    in_skins = False
    rarity_group = None
    collab = False

    def add(name, rarity=None, acquisition='', skin=None):
        name = name.strip(' \t：:；;。、「」『』•·')
        name = re.sub(r'\s*[（(][^）)]*[）)]\s*$', '', name).strip()
        if not name or len(name) > 80 or any(x in name for x in ('概率', '以下', '新增', '奖励', '获得')):
            return
        entry = result.setdefault(_key(name), {'name': name, 'rarity': rarity,
                                              'acquisition': acquisition, 'skins': []})
        entry['rarity'] = entry['rarity'] or rarity
        if acquisition and not entry['acquisition']:
            entry['acquisition'] = acquisition
        if skin and _key(skin['name']) not in {_key(s['name']) for s in entry['skins']}:
            entry['skins'].append(skin)

    for line in _plain(body).splitlines():
        line = line.strip()
        if not line:
            continue
        if re.match(r'[二三四五六七八九十]+、(?:内容优化|问题修复|其他)', line):
            break
        if re.match(r'\d+[.．、]\s*|[一二三四五六七八九十]+、', line):
            section = line
            rarity_group = None
            collab = '联动' in line
            in_skins = bool('换装' in line and re.search(r'上架|新增|复刻|返场', line)
                            and not re.search(r'体验|试用|租赁', line))
        elif re.fullmatch(r'◆[^◆]+◆', line):
            section = line
            rarity_group = None
            in_skins = False
        elif len(line) < 120 and re.search(r'建造.*(?:新增|追加|角色)|(?:新增|追加).*建造', line):
            section = line
            rarity_group = None
            in_skins = False
        if in_skins:
            for match in re.finditer(r'(?:^|[、；;])\s*([^「」\n]+?)\s*[-—–－]\s*「([^」]+)」', line):
                name, skin = match.groups()
                if not re.search(r'兵装|家具|装备|礼包', name):
                    add(name, acquisition='联动活动' if collab else '',
                        skin={'name': skin, 'acquisition': '限时返场' if '返场' in section or '复刻' in section
                              else '钻石购买'})
            continue
        if re.search(r'兵装|家具|装备|修复|优化|体验|试用', section):
            continue
        acquisition = ('联动活动' if collab else '限定建造' if '建造' in section
                       else '活动获取')
        for match in re.finditer(r'(?:获得|获取|领取|兑换)(?:限定|联动|超稀有|精锐|海上传奇|稀有|普通)*角色「([^」]+)」', line):
            rarity = re.search(RARITY_PATTERN, line[:match.end()])
            add(match.group(1), RARITIES.get(rarity.group()) if rarity else None, acquisition)
        # 建造段的概率列表，以及常驻新增、活动兑换段中的稀有度分组。
        if re.search(r'建造|角色|兑换|回归', section):
            grouped = re.match(r'(?:[•·◆]\s*)?(' + RARITY_PATTERN + r')\s*[:：]\s*(.*)', line)
            if grouped:
                rarity_group = RARITIES[grouped.group(1)]
                names = grouped.group(2)
            elif rarity_group and re.fullmatch(r'[^。！？※：:]+(?:[%％][）)]?|」)', line):
                names = line
            else:
                names = ''
            if names:
                for name in re.split(r'[、，,；;]', names):
                    name = re.sub(r'[（(][^）)]*[）)]', '', name)
                    name = re.sub(r'\s*\d+(?:\.\d+)?\s*%.*$', '', name).strip()
                    add(name, rarity_group, acquisition)
            for match in re.finditer(r'(?:^|[：:、，,；;])\s*([^「」（）():：、，,；;]+?)\s*[（(]('
                                     + RARITY_PATTERN + r')[）)]', line):
                add(match.group(1), RARITIES[match.group(2)],
                    '常驻建造' if '常驻' in section or '常驻' in line else acquisition)
        # 免费活动换装同样要求角色名和换装名在同一句中明确出现。
        if re.search(r'获得|领取|兑换', line):
            for match in re.finditer(r'(?:获得|领取|兑换)\s*「?([^「」]+?)」?的?换装「([^」]+)」', line):
                add(match.group(1), acquisition='联动活动' if collab else '',
                    skin={'name': match.group(2), 'acquisition': '活动赠送'})
    return list(result.values())


class SkinCollectionService:
    """五分钟共享快照；外网请求在独立线程执行，不阻塞界面消息通道。"""

    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / 'config' / 'skin-collection' / 'announcements.json'
        self._lock = threading.Lock()
        self._refreshing = False
        self._last_fetch = None
        self._ttl = 300
        self._data = {'entries': [], 'documents': {}, 'checkedAt': None, 'error': None,
                      'pendingNames': [], 'latestPublishedAt': 0}
        try:
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            if (saved.get('version') != 1 or not isinstance(saved.get('entries'), list)
                    or not isinstance(saved.get('documents'), dict)):
                raise ValueError('公告缓存格式不正确')
            self._data.update(saved)
        except FileNotFoundError:
            pass
        except (ValueError, OSError) as exc:
            self._data['error'] = f'读取公告缓存失败：{exc}'

    def get(self):
        with self._lock:
            if not self._refreshing and (self._last_fetch is None or time.monotonic() - self._last_fetch >= self._ttl):
                self._refreshing = True
                threading.Thread(target=self._refresh, name='skin-collection-announcements', daemon=True).start()
            return {**copy.deepcopy({key: self._data[key] for key in
                                     ('entries', 'checkedAt', 'error', 'pendingNames')}),
                    'refreshing': self._refreshing}

    @staticmethod
    def _request(url, params):
        response = http.get(url, params=params, timeout=12, headers={
            'User-Agent': http.BROWSER_USER_AGENT, 'Referer': 'https://www.taptap.cn/app/31597/topic?type=official'})
        response.raise_for_status()
        payload = response.json()
        if payload.get('success') is False or 'error' in payload:
            raise ValueError('公告或舰船资料接口未返回有效数据')
        return payload

    def _topic(self, topic_id):
        data = self._request(TAP_API + 'topic/v1/detail', {'id': topic_id, 'X-UA': TAP_UA})['data']
        topic = data['topic']
        body = data['first_post']['contents']['text']
        if not isinstance(body, str) or not body:
            raise ValueError('官方维护公告正文为空')
        return {'title': topic['title'], 'body': body, 'published': topic['created_time']}

    def _discover(self, data):
        documents = data['documents']
        # moment 与 topic 的编号不是同一个字段；种子保证首次运行也能补到本次维护。
        if SEED_ID not in documents:
            documents[SEED_ID] = self._topic(SEED_TOPIC)
        params = {'group_id': 2657, 'type': 'official', 'sort': 'created', 'X-UA': TAP_UA}
        baseline = int((SINCE - timedelta(days=14)).timestamp())
        # 保留两周重叠，后发动态不会让前次维护的正文更正被水位跳过。
        floor = max(baseline, data['latestPublishedAt'] - 14 * 86400)
        latest = data['latestPublishedAt']
        seen_pages = set()
        for _ in range(100):
            feed = self._request(TAP_API + 'feed/v7/by-group', params)['data']
            rows = feed.get('list', [])
            reached = not rows
            for row in rows:
                moment = row.get('moment', {})
                published = moment.get('created_time', 0)
                if not published:
                    continue
                latest = max(latest, published)
                if published < floor:
                    reached = True
                    if str(moment.get('id_str')) not in documents:
                        continue
                topic = moment.get('topic') or {}
                if not moment.get('is_official') or not re.search(r'港区改建|维护公告', topic.get('title', '')):
                    continue
                mid = str(moment['id_str'])
                edited = moment.get('edited_time', published)
                if mid not in documents or documents[mid].get('edited') != edited:
                    documents[mid] = {**self._topic(topic['id_str']), 'edited': edited}
            next_page = feed.get('next_page')
            if reached or not next_page:
                data['latestPublishedAt'] = latest
                return
            if next_page in seen_pages:
                raise ValueError('官方公告分页游标重复，稍后重试')
            seen_pages.add(next_page)
            parsed = urlsplit(next_page)
            if parsed.path != '/webapiv2/feed/v7/by-group':
                raise ValueError('官方公告分页地址异常')
            params = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
            params.update(group_id=2657, type='official', sort='created', **{'X-UA': TAP_UA})
        raise ValueError('官方公告分页未完成，稍后重试')

    def _catalog(self):
        try:
            rows = json.loads((self.root / 'assets/ship/ship_data.json').read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {}
        result = {}
        for row in rows.values():
            name = row.get('name', {}).get('cn', '')
            rarity = RARITIES.get(row.get('rarity_name'))
            if name and rarity and not row.get('is_retrofit'):
                aliases = [value.strip() for value in row['name'].values()
                           if isinstance(value, str) and value.strip() and not re.search(r'[<>{}\[\]]', value)]
                result[_key(name)] = {'name': name, 'rarity': rarity, 'aliases': list(dict.fromkeys(aliases))}
        return result

    def _wiki(self, name):
        # 斜杠是 MediaWiki 子页面分隔符，国服图鉴标题使用全角斜杠。
        data = self._request('https://wiki.biligame.com/blhx/api.php', {
            'action': 'parse', 'page': name.replace('/', '／'), 'prop': 'wikitext', 'format': 'json'})
        text = data['parse']['wikitext']['*']
        start = re.search(r'\{\{\s*舰娘图鉴\s*\n', text)
        if not start:
            return None
        # 只读取第一份基础图鉴；后面的改造模板不能改变角色基础稀有度。
        first = re.split(r'\{\{\s*舰娘图鉴', text[start.end():], maxsplit=1)[0]
        fields = {}
        for field in ('名称', '和谐名', '英文名', '日文名', '稀有度'):
            match = re.search(r'^\|[ \t]*' + field + r'[ \t]*=[ \t]*([^\n]*)', first, re.M)
            fields[field] = match.group(1) if match else ''
        rarity = RARITIES.get(fields.get('稀有度', '').strip())
        canonical = fields.get('名称', '').strip()
        if not rarity or not canonical or re.search(r'[<>{}\[\]]', canonical):
            return None
        aliases = [name, canonical]
        harmony = fields.get('和谐名', '').strip()
        if harmony and not re.search(r'[<>{}\[\]]', harmony):
            aliases.extend(re.split(r'[、,，]', harmony))
        for field in ('英文名', '日文名'):
            value = fields[field].strip()
            if value and not re.search(r'[<>{}\[\]]', value):
                aliases.append(value)
        return {'name': canonical, 'rarity': rarity, 'aliases': list(dict.fromkeys(aliases))}

    def _merge(self, data):
        catalog = self._catalog()
        entries = data['entries']
        known = {_key(alias): entry for entry in entries for alias in [entry['name'], *entry['aliases']]}
        pending = set()
        for mid, doc in sorted(data['documents'].items(), key=lambda item: item[1]['published']):
            at = _maintenance_at(doc['title'], _plain(doc['body']), doc['published'])
            if at is None or at < SINCE:
                continue
            for candidate in _candidates(doc['body']):
                name = candidate['name']
                entry = known.get(_key(name))
                if entry is None:
                    metadata = catalog.get(_key(name))
                    try:
                        wiki = self._wiki(name)
                    except Exception:
                        wiki = None
                    # 稀有度已知并不代表公告使用了正式舰名；未核验别名时暂缓输出。
                    if not wiki:
                        pending.add(name)
                        continue
                    rarity = ((metadata or {}).get('rarity') or candidate['rarity']
                              or (wiki or {}).get('rarity'))
                    if not rarity:
                        pending.add(name)
                        continue
                    canonical = (wiki or metadata or {}).get('name', name)
                    aliases = list(dict.fromkeys([name, canonical, *wiki.get('aliases', []),
                                                  *(metadata or {}).get('aliases', [])]))
                    entry = next((known[_key(alias)] for alias in aliases if _key(alias) in known), None)
                    if entry is None:
                        entry = {'name': canonical, 'aliases': aliases, 'rarity': rarity,
                                 'acquisition': candidate['acquisition'], 'skins': [],
                                 'sourceUrl': f'https://www.taptap.cn/moment/{mid}?group_id=2657',
                                 'announcementId': mid}
                        entries.append(entry)
                    else:
                        entry['aliases'] = list(dict.fromkeys([*entry['aliases'], *aliases]))
                    for alias in aliases:
                        known[_key(alias)] = entry
                if not entry['acquisition'] and candidate['acquisition']:
                    entry['acquisition'] = candidate['acquisition']
                existing = {_key(s['name']) for s in entry['skins']}
                for skin in candidate['skins']:
                    if _key(skin['name']) not in existing:
                        entry['skins'].append(skin)
                        existing.add(_key(skin['name']))
        data['pendingNames'] = sorted(pending)

    def _refresh(self):
        with self._lock:
            data = copy.deepcopy(self._data)
        errors = []
        try:
            self._discover(data)
        except Exception as exc:
            errors.append(f'获取官方公告失败：{exc}')
        try:
            self._merge(data)
        except Exception as exc:
            errors.append(f'解析官方公告失败：{exc}')
        data.update(version=1, checkedAt=datetime.now(CN).isoformat(), error='；'.join(errors) or None)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(str(self.path), json.dumps(data, ensure_ascii=False, indent=2) + '\n')
        except Exception as exc:
            data['error'] = f'保存公告缓存失败：{exc}'
        with self._lock:
            self._data = data
            self._last_fetch = time.monotonic()
            self._refreshing = False
