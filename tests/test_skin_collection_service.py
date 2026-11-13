"""验证公告的保守解析、日期门槛、增量缓存与后台请求共享。"""
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from module.api.skin_collection_service import (
    CN, SEED_ID, SEED_TOPIC, SINCE, SkinCollectionService, _candidates, _maintenance_at,
)


TITLE = '联动活动即将开启！11月12日10:00港区改建'
BODY = '''各位亲爱的指挥官：<br/>司令部将于11月12日10:00~15:00对以下港区进行改造建设。<br/>
『改建内容』<br/>一、内容新增<br/>1.开启限时联动活动<br/>
完成累计击沉敌人的活动任务即可获得角色「莉莉艾露 天使空降队/理理沙」和限定外观等奖励。<br/>
最快可于活动首日获得限定角色「莉莉艾露 天使空降队/理理沙」。<br/>
完成活动任务即可获得限定角色「美莉艾拉 天使空降队/美花莉」。<br/>
2.换装商店限时全新上架以下换装：<br/>
莉莉艾露 天使空降队/理理沙-「身心皆奉」【动态立绘】<br/>
美莉艾拉 天使空降队/美花莉-「献给幸运的你」【动态立绘】<br/>
3.家具商店限时上架：<br/>莉莉艾露 天使空降队/理理沙-「家具名字」<br/>
4.下列角色新增专属特殊兵装：<br/>
莉莉艾露 天使空降队/理理沙 -「莉莉艾露的法杖」、美莉艾拉 天使空降队/美花莉 -「美莉艾拉的弓」<br/>
二、内容优化<br/>修复获得角色「不应导入」的显示问题。'''
PUBLISHED = int(datetime(2026, 11, 10, 14, tzinfo=CN).timestamp())
NOW = datetime(2026, 11, 13, 14, tzinfo=CN)


def document(body=BODY, title=TITLE, published=PUBLISHED):
    return {'body': body, 'title': title, 'published': published}


def metadata(name):
    return {'name': name.replace('/', '／'), 'rarity': 'super_rare',
            'aliases': [name, name.replace('/', '／')]}


class SkinCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skin-announcements-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = SkinCollectionService(self.root)

    def wait(self, service=None):
        service = service or self.service
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = service.get()
            if not result['refreshing']:
                return result
            time.sleep(.01)
        self.fail('后台刷新未结束')

    def test_current_announcement_is_two_characters_two_skins_not_equipment(self):
        candidates = _candidates(BODY)
        self.assertEqual(len(candidates), 2)
        self.assertEqual([s['name'] for c in candidates for s in c['skins']], ['身心皆奉', '献给幸运的你'])
        self.assertTrue(all(c['acquisition'] == '联动活动' for c in candidates))

    def test_construction_return_and_gift_while_rental_is_excluded(self):
        body = '''1.限时建造新增以下角色：
海上传奇：新角色甲 1.2%
超稀有：
新角色乙（2%）
新角色丙（2%）
2.常驻建造新增角色：
新角色丁（精锐）、新角色戊（稀有）
3.活动任务：
完成任务可获得新角色乙换装「活动礼服」。
4.换装商店限时返场：
新角色丁-「旧日礼服」
5.换装体验新增：
新角色乙-「不应增加」'''
        entries = {entry['name']: entry for entry in _candidates(body)}
        self.assertEqual(set(entries), {'新角色甲', '新角色乙', '新角色丙', '新角色丁', '新角色戊'})
        self.assertEqual(entries['新角色甲']['rarity'], 'ultra_rare')
        self.assertEqual(entries['新角色丁']['rarity'], 'elite')
        self.assertEqual(entries['新角色乙']['skins'], [{'name': '活动礼服', 'acquisition': '活动赠送'}])
        self.assertEqual(entries['新角色丁']['skins'], [{'name': '旧日礼服', 'acquisition': '限时返场'}])

    def test_before_baseline_excluded_but_future_maintenance_collected_on_publication(self):
        self.service._data['documents'] = {
            'old': document(BODY.replace('11月12日', '11月5日')),
            SEED_ID: document(),
            'future': document(BODY.replace('11月12日', '11月19日').replace('身心皆奉', '未来礼服')),
        }
        with patch.object(self.service, '_wiki', side_effect=metadata):
            self.service._merge(self.service._data)
        self.assertEqual(len(self.service._data['entries']), 2)
        self.assertEqual(sum(len(e['skins']) for e in self.service._data['entries']), 3)
        self.assertEqual(_maintenance_at(TITLE, '司令部将于11月12日10:00~15:00。改建内容', PUBLISHED),
                         SINCE.replace(hour=15))

    def test_unknown_rarity_is_pending_then_retried_and_deduplicated(self):
        data = self.service._data
        data['documents'][SEED_ID] = document()
        with patch.object(self.service, '_wiki', side_effect=ValueError('离线')):
            self.service._merge(data)
        self.assertEqual(data['entries'], [])
        self.assertEqual(len(data['pendingNames']), 2)
        with patch.object(self.service, '_wiki', side_effect=metadata):
            self.service._merge(data)
            # 后续维护再次返场不会重复创建角色或同名换装。
            data['documents']['return'] = document()
            self.service._merge(data)
        self.assertEqual(len(data['entries']), 2)
        self.assertEqual(sum(len(e['skins']) for e in data['entries']), 2)
        self.assertEqual(data['pendingNames'], [])
        self.assertFalse(any('collected' in e or 'owned' in e for e in data['entries']))

    def test_wiki_empty_harmony_and_retrofit_do_not_change_base_rarity(self):
        text = '''{{舰娘图鉴
|名称=测试舰
|和谐名=
|英文名=Test
|日文名=テスト
|稀有度=精锐
}}
{{舰娘图鉴
|名称=测试舰·改
|稀有度=超稀有
}}'''
        with patch.object(self.service, '_request', return_value={'parse': {'wikitext': {'*': text}}}):
            self.assertEqual(self.service._wiki('测试舰'),
                             {'name': '测试舰', 'rarity': 'elite', 'aliases': ['测试舰', 'Test', 'テスト']})
        text = text.replace('|和谐名=', '|和谐名=和谐舰名')
        with patch.object(self.service, '_request', return_value={'parse': {'wikitext': {'*': text}}}):
            self.assertIn('和谐舰名', self.service._wiki('测试舰')['aliases'])
        text = text.replace('|英文名=Test', '|英文名={{链接|Test}}').replace('|日文名=テスト', '|日文名=[[テスト]]')
        with patch.object(self.service, '_request', return_value={'parse': {'wikitext': {'*': text}}}):
            self.assertEqual(self.service._wiki('测试舰')['aliases'], ['测试舰', '和谐舰名'])

    def test_rarity_without_verified_names_waits_then_merges_canonical_aliases(self):
        body = '''司令部将于11月12日10:00~15:00改建。<br/>『改建内容』<br/>
1.限定建造新增角色：<br/>超稀有：和谐舰名（2%）<br/>
2.换装商店全新上架：<br/>和谐舰名-「新礼服」'''
        data = self.service._data
        data['documents'][SEED_ID] = document(body)
        catalog = {'和谐舰名': {'name': '和谐舰名', 'rarity': 'super_rare', 'aliases': ['和谐舰名', 'Local Name']}}
        with patch.object(self.service, '_catalog', return_value=catalog), \
                patch.object(self.service, '_wiki', side_effect=OSError('暂不可用')):
            self.service._merge(data)
        self.assertEqual(data['entries'], [])
        self.assertEqual(data['pendingNames'], ['和谐舰名'])
        verified = {'name': '正式舰名', 'rarity': 'super_rare',
                    'aliases': ['正式舰名', '和谐舰名', 'English Name', '日本語名']}
        with patch.object(self.service, '_catalog', return_value=catalog), \
                patch.object(self.service, '_wiki', return_value=verified):
            self.service._merge(data)
            # 后续公告改用正式名称，同一件换装也只保留一次。
            data['documents']['return'] = document(body.replace('和谐舰名', '正式舰名'))
            self.service._merge(data)
        self.assertEqual(data['pendingNames'], [])
        self.assertEqual(len(data['entries']), 1)
        self.assertEqual(data['entries'][0]['name'], '正式舰名')
        self.assertEqual(len(data['entries'][0]['skins']), 1)
        self.assertEqual(set(data['entries'][0]['aliases']),
                         {'正式舰名', '和谐舰名', 'English Name', '日本語名', 'Local Name'})

    def test_catalog_includes_only_explicit_clean_language_names(self):
        path = self.root / 'assets/ship/ship_data.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'1': {'name': {'cn': '测试舰', 'en': 'Test Ship', 'jp': 'テスト',
                                                  'tw': '測試艦', 'kr': '{{unknown}}'},
                                           'rarity_name': 'SSR'}}), encoding='utf-8')
        self.assertEqual(set(self.service._catalog()['测试舰']['aliases']), {'测试舰', 'Test Ship', 'テスト', '測試艦'})

    def test_local_catalog_rarity_precedes_wiki(self):
        self.service._data['documents'][SEED_ID] = document()
        local = {'莉莉艾露天使空降队/理理沙': {'name': '莉莉艾露 天使空降队/理理沙', 'rarity': 'elite'}}
        with patch.object(self.service, '_catalog', return_value=local), patch.object(self.service, '_wiki', side_effect=metadata):
            self.service._merge(self.service._data)
        self.assertEqual(self.service._data['entries'][0]['rarity'], 'elite')

    def test_background_request_shared_and_persistent_entries_survive_failure(self):
        started, release = threading.Event(), threading.Event()

        def discover(data):
            started.set()
            release.wait(3)
            data['documents'][SEED_ID] = document()

        with patch.object(self.service, '_discover', side_effect=discover) as request, \
                patch.object(self.service, '_wiki', side_effect=metadata):
            initial = self.service.get()
            self.assertTrue(started.wait(1))
            self.assertTrue(initial['refreshing'])
            self.assertEqual(initial['entries'], [])
            for _ in range(10):
                self.assertTrue(self.service.get()['refreshing'])
            self.assertEqual(request.call_count, 1)
            release.set()
            ready = self.wait()
            self.assertEqual(len(ready['entries']), 2)
            self.service.get()
            self.assertEqual(request.call_count, 1)
        restored = SkinCollectionService(self.root)
        with patch.object(restored, '_discover', side_effect=OSError('离线')):
            result = self.wait(restored)
        self.assertEqual(result['entries'], ready['entries'])
        self.assertIn('离线', result['error'])
        self.assertEqual(json.loads(restored.path.read_text(encoding='utf-8'))['entries'], ready['entries'])

    def test_official_pagination_and_distinct_topic_id(self):
        recent = int(NOW.timestamp())
        rows = [
            {'data': {'list': [{'moment': {'id_str': 'unrelated', 'is_official': True,
                                          'created_time': recent, 'topic': {'title': '其他消息'}}}],
                      'next_page': '/webapiv2/feed/v7/by-group?from=10&group_id=2657'}},
            {'data': {'list': [{'moment': {'id_str': SEED_ID, 'is_official': True,
                                          'created_time': PUBLISHED, 'edited_time': PUBLISHED,
                                          'topic': {'title': TITLE, 'id_str': SEED_TOPIC}}}],
                      'next_page': None}},
        ]
        with patch.object(self.service, '_request', side_effect=rows) as request, \
                patch.object(self.service, '_topic', return_value=document()) as topic:
            self.service._discover(self.service._data)
        self.assertEqual(request.call_count, 2)
        self.assertTrue(all(call.args == (SEED_TOPIC,) for call in topic.call_args_list))
        self.assertIn(SEED_ID, self.service._data['documents'])
        self.assertEqual(self.service._data['latestPublishedAt'], recent)

    def test_later_news_watermark_does_not_hide_edited_maintenance(self):
        data = self.service._data
        data['documents'][SEED_ID] = {**document(), 'edited': PUBLISHED}
        with patch.object(self.service, '_wiki', side_effect=metadata):
            self.service._merge(data)
        recent = int(datetime(2026, 11, 21, 14, tzinfo=CN).timestamp())
        data['latestPublishedAt'] = recent
        edited_body = BODY.replace('3.家具', '莉莉艾露 天使空降队/理理沙-「补充换装」<br/>3.家具')
        rows = [
            {'data': {'list': [{'moment': {'id_str': 'newer-news', 'is_official': True,
                                          'created_time': recent, 'topic': {'title': '其他消息'}}}],
                      'next_page': '/webapiv2/feed/v7/by-group?from=10&group_id=2657'}},
            {'data': {'list': [{'moment': {'id_str': SEED_ID, 'is_official': True,
                                          'created_time': PUBLISHED, 'edited_time': recent,
                                          'topic': {'title': TITLE, 'id_str': SEED_TOPIC}}}],
                      'next_page': None}},
        ]
        with patch.object(self.service, '_request', side_effect=rows), \
                patch.object(self.service, '_topic', return_value=document(edited_body)) as topic:
            self.service._discover(data)
            self.service._merge(data)
        topic.assert_called_once_with(SEED_TOPIC)
        self.assertEqual(sum(len(e['skins']) for e in data['entries']), 3)
        # 未改动的文章在下一轮仅检查列表，不再下载全文。
        with patch.object(self.service, '_request', side_effect=rows), \
                patch.object(self.service, '_topic') as topic:
            self.service._discover(data)
        topic.assert_not_called()


if __name__ == '__main__':
    unittest.main()
