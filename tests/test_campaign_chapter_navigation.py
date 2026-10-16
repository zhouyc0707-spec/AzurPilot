"""关卡 OCR 边界、逐章截图确认及有界恢复；不加载真实配置或连接游戏。"""

from collections import deque
import importlib
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.base.button import Button
from module.campaign.campaign_ocr import CampaignOcr
from module.campaign.campaign_ui import CampaignUI, CHAPTER_NEXT, CHAPTER_PREV
from module.device.device import Device
from module.exception import CampaignNameError, ScriptEnd


def make_ocr(reader_type=CampaignOcr):
    reader = reader_type.__new__(reader_type)
    reader.device = Mock(image=object())
    reader.config = SimpleNamespace(MAP_HAS_MODE_SWITCH=False)
    reader.stage_entrance = {}
    return reader


def read_names(reader, names):
    """保留产品清洗、分离、投票和入口发布，仅隔离图像模型。"""
    buttons = [Button(area=(0, 0, 10, 10), color=(1, 2, 3), button=(5, 5, 15, 15), name='STAGE')
               for _ in names]
    reader.campaign_extract_name_image = Mock(return_value=buttons)
    with patch('module.campaign.campaign_ocr.Ocr') as ocr:
        ocr.return_value.ocr.return_value = names
        reader._get_stage_name(reader.device.image)
    return buttons


class ChapterOcrTests(unittest.TestCase):
    def test_main_chapter_names_are_complete_and_bounded(self):
        for name, expected in [('1-1', ('1', '1')), ('16-4', ('16', '4')), ('99-6', ('99', '6'))]:
            with self.subTest(name=name):
                self.assertEqual(CampaignOcr._campaign_separate_name(name), expected)

    def test_malformed_numeric_names_are_not_split_into_large_chapters(self):
        for name in ['16618', '1661-8', '0-1', '16-0', '16-7', '100-1', '16-4-1', '49x']:
            with self.subTest(name=name):
                self.assertEqual(CampaignOcr._campaign_separate_name(name), ('', ''))

    def test_existing_ocr_corrections_remain_accepted(self):
        for raw, expected in [('164', ('16', '4')), ('75', ('7', '5')), ('I1-1', ('11', '1')),
                              ('7--2', ('7', '2')), ('I-I', ('1', '1'))]:
            with self.subTest(raw=raw):
                name = CampaignOcr._campaign_ocr_result_process(raw)
                self.assertEqual(CampaignOcr._campaign_separate_name(name), expected)

    def test_garbage_does_not_vote_or_enter_cache(self):
        reader = make_ocr()
        buttons = read_names(reader, ['16618', 'IK', '16-4', '16618', 'ZZ1', '16-2'])
        self.assertEqual(reader.campaign_chapter, '16')
        self.assertEqual(set(reader.stage_entrance), {'16-4', '16-2'})
        self.assertIs(reader.stage_entrance['16-4'], buttons[2])
        self.assertEqual(buttons[2].area, buttons[2].button)
        self.assertEqual(buttons[2].name, '16-4')
        self.assertEqual(buttons[0].name, 'STAGE')

    def test_only_valid_entries_participate_in_majority(self):
        reader = make_ocr()
        read_names(reader, ['ZZ1', 'ZZ2', 'ZZ3', 'D1', 'D2', 'C1'])
        self.assertEqual(reader.campaign_chapter, 'd')
        self.assertEqual(set(reader.stage_entrance), {'d1', 'd2', 'c1'})

    def test_all_invalid_names_clear_old_entrance_and_raise(self):
        reader = make_ocr()
        reader.stage_entrance = {'16-4': object()}
        with self.assertRaises(CampaignNameError):
            read_names(reader, ['16618', 'IK', '0-1', '16-0'])
        self.assertEqual(reader.stage_entrance, {})

    def test_nonpositive_subclass_index_is_not_published(self):
        class IndexReader(CampaignOcr):
            @staticmethod
            def _campaign_get_chapter_index(name):
                return {'a': 0, 'b': -1, 'c': '1', 'd': 2}[name]

        reader = make_ocr(IndexReader)
        read_names(reader, ['A1', 'B1', 'C1', 'D1'])
        self.assertEqual(set(reader.stage_entrance), {'d1'})

    def test_sp_ex_and_standard_event_names_remain_valid(self):
        for raw, chapter in [('SP', 'ex_sp'), ('EX', 'ex_ex'), ('EXTRA', 'ex_ex'),
                             ('SP3', 'sp'), ('D3', 'd'), ('T3', 't')]:
            with self.subTest(raw=raw):
                reader = make_ocr()
                read_names(reader, [raw])
                self.assertEqual(reader.campaign_chapter, chapter)
                self.assertIn(raw.lower(), reader.stage_entrance)

    def test_real_event_and_archive_subclass_extensions_remain_valid(self):
        cases = [
            ('campaign.event_20240829_cn.campaign_base', 'TP', 'ex_sp', 1),
            ('campaign.event_20211125_cn.campaign_base', 'SSS', 'ex_sp', 2),
            ('campaign.war_archives_20231026_cn.campaign_base', 'T3', 't1', 1),
            ('campaign.war_archives_20231026_cn.campaign_base', 'T6', 't2', 2),
            ('campaign.war_archives_20220818_cn.campaign_base', 'ESP', 'sp_sp', 2),
            ('campaign.war_archives_20220818_cn.campaign_base', 'EX', 'sp_ex', 3),
        ]
        for module, raw, chapter, index in cases:
            with self.subTest(module=module, raw=raw):
                reader_type = importlib.import_module(module).CampaignBase
                reader = make_ocr(reader_type)
                read_names(reader, [raw])
                self.assertEqual(reader.campaign_chapter, chapter)
                self.assertEqual(reader._campaign_get_chapter_index(chapter), index)
                self.assertIn(raw.lower(), reader.stage_entrance)

    def test_old_event_parser_cannot_bypass_numeric_entry_validation(self):
        reader_type = importlib.import_module('campaign.event_20210722_cn.campaign_base').CampaignBase
        reader = make_ocr(reader_type)
        read_names(reader, ['16618', '1661-8', 'SP1'])
        self.assertEqual(reader.campaign_chapter, 'sp')
        self.assertEqual(set(reader.stage_entrance), {'sp1'})

    def test_d3_3_keeps_d3_entrance_alias(self):
        reader = make_ocr(CampaignUI)
        buttons = read_names(reader, ['D3'])
        self.assertIs(reader.campaign_get_entrance('d3_3'), buttons[0])
        self.assertEqual(buttons[0].name, 'd3_3')


class ChapterDevice:
    """新截图才发布点击结果，并复用真实的十二次连击保护。"""

    click_record_add = Device.click_record_add
    click_record_clear = Device.click_record_clear
    click_record_check = Device.click_record_check

    def __init__(self, chapter='1', step=1, provider=None, elapsed=0.25):
        self.chapter = str(chapter)
        self.step = step
        self.provider = provider
        self.elapsed = elapsed
        self.clock = 100.0
        self.frame = 0
        self.pending = None
        self.trace = []
        self.clicks = []
        self.removals = []
        self.image = object()
        self.click_record = deque(maxlen=15)
        self.save_screenshot = Mock()
        self.multi_click = Mock(side_effect=AssertionError('章节导航禁止盲目批量点击'))

    def screenshot(self):
        self.frame += 1
        if self.frame > 500:
            raise AssertionError('章节导航超过有限截图预算')
        self.clock += self.elapsed
        if self.provider is not None:
            self.chapter = str(self.provider(self.frame, self.chapter))
        elif self.pending is not None:
            self.chapter = str(self.pending)
        self.pending = None
        self.trace.append(('screenshot', self.frame, self.chapter))
        self.image = object()

    def click(self, button):
        self.click_record_add(button)
        self.click_record_check()
        self.clicks.append((self.frame, self.clock, button))
        self.trace.append(('click', self.frame, str(button)))
        if self.provider is None and self.chapter.isdigit():
            direction = 1 if button is CHAPTER_NEXT else -1
            self.pending = int(self.chapter) + direction * self.step

    def click_record_remove(self, button):
        self.removals.append((self.frame, button))
        return Device.click_record_remove(self, button)


class ChapterNavigationTests(unittest.TestCase):
    def setUp(self):
        for target in ['module.campaign.campaign_ui.logger', 'module.campaign.campaign_ocr.logger']:
            patcher = patch(target, Mock())
            patcher.start()
            self.addCleanup(patcher.stop)

    def campaign(self, reader_type=CampaignUI, **kwargs):
        reader = make_ocr(reader_type)
        device = ChapterDevice(**kwargs)
        reader.device = device
        reader.handle_chapter_additional = Mock(return_value=False)

        def chapter_index():
            reader.campaign_chapter = device.chapter
            return reader._campaign_get_chapter_index(device.chapter)

        reader.get_chapter_index = Mock(side_effect=chapter_index)
        reader.appear_then_click = Mock(side_effect=lambda button, **_: device.click(button) or True)
        patcher = patch('module.base.timer.time', lambda: device.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        return reader

    def test_long_valid_navigation_one_to_sixteen_keeps_click_protection(self):
        reader = self.campaign(chapter='1')
        reader.campaign_ensure_chapter('16')
        device = reader.device
        self.assertEqual(device.chapter, '16')
        self.assertEqual(len(device.clicks), 15)
        self.assertEqual(len(device.removals), 15)
        self.assertTrue(all(button is CHAPTER_NEXT for _, _, button in device.clicks))
        device.multi_click.assert_not_called()
        device.save_screenshot.assert_not_called()

    def test_each_click_requires_new_stable_screenshot(self):
        reader = self.campaign(chapter='12')
        reader.campaign_ensure_chapter('16')
        frames = [frame for frame, _, _ in reader.device.clicks]
        self.assertGreater(frames[0], 0)
        self.assertTrue(all(after - before >= 2 for before, after in zip(frames, frames[1:])))
        for call in reader.appear_then_click.call_args_list:
            self.assertEqual(call.kwargs.get('offset'), (20, 20))

    def test_one_chapter_at_a_time_in_reverse_direction(self):
        reader = self.campaign(chapter='16')
        reader.campaign_ensure_chapter('13')
        self.assertEqual(reader.device.chapter, '13')
        self.assertEqual(len(reader.device.clicks), 3)
        self.assertTrue(all(button is CHAPTER_PREV for _, _, button in reader.device.clicks))

    def test_shared_retry_cooldown_survives_direction_changes(self):
        reader = self.campaign(chapter='15', provider=lambda frame, _: '15' if frame < 5 else '17')
        with self.assertRaises(CampaignNameError):
            reader.campaign_ensure_chapter('16')
        clicks = reader.device.clicks
        self.assertTrue(any(button is CHAPTER_NEXT for _, _, button in clicks))
        self.assertTrue(any(button is CHAPTER_PREV for _, _, button in clicks))
        self.assertTrue(all(after[1] - before[1] > 1 for before, after in zip(clicks, clicks[1:])))

    def test_stalled_page_is_bounded_and_does_not_clear_click_history(self):
        reader = self.campaign(chapter='15', step=0)
        with self.assertRaises(CampaignNameError):
            reader.campaign_ensure_chapter('16')
        self.assertLess(reader.device.clock - 100, 12)
        self.assertGreater(len(reader.device.clicks), 0)
        self.assertEqual(reader.device.removals, [])
        self.assertEqual(len(reader.device.click_record), len(reader.device.clicks))
        reader.device.multi_click.assert_not_called()
        reader.device.save_screenshot.assert_called_once_with(genre='campaign_chapter_unknown', interval=0)

    def test_slow_frames_do_not_require_seven_more_frames_after_progress_stalls(self):
        reader = self.campaign(chapter='15', step=0, elapsed=3)
        with self.assertRaises(CampaignNameError):
            reader.campaign_ensure_chapter('16')
        # 稳定读取最迟第 6 秒建立基线，8 秒期限只允许一帧 3 秒的检测误差。
        self.assertLessEqual(reader.device.clock - 100, 17)
        self.assertLess(reader.device.frame, 7)
        self.assertGreater(len(reader.device.clicks), 0)
        reader.device.save_screenshot.assert_called_once_with(genre='campaign_chapter_unknown', interval=0)

    def test_oscillating_page_does_not_extend_progress_deadline(self):
        reader = self.campaign(chapter='14', provider=lambda frame, _: '14' if frame % 8 < 4 else '15')
        with self.assertRaises(CampaignNameError):
            reader.campaign_ensure_chapter('16')
        self.assertLess(reader.device.clock - 100, 15)
        reader.device.multi_click.assert_not_called()

    def test_wrong_chapter_type_is_bounded_without_clicks(self):
        reader = self.campaign(chapter='d')
        with self.assertRaises(CampaignNameError):
            reader.campaign_ensure_chapter('16')
        self.assertLess(reader.device.clock - 100, 50)
        self.assertEqual(reader.device.clicks, [])

    def test_slow_wrong_type_frames_stop_at_total_deadline_without_forty_six_frames(self):
        reader = self.campaign(chapter='d', elapsed=3)
        with self.assertRaises(CampaignNameError):
            reader.campaign_ensure_chapter('16')
        self.assertGreater(reader.device.clock - 100, 45)
        self.assertLessEqual(reader.device.clock - 100, 48)
        self.assertLess(reader.device.frame, 20)
        self.assertEqual(reader.device.clicks, [])
        reader.device.save_screenshot.assert_called_once_with(genre='campaign_chapter_unknown', interval=0)

    def test_equal_index_with_wrong_chapter_type_is_not_success(self):
        reader = self.campaign(chapter='d')
        with self.assertRaises(CampaignNameError):
            reader.campaign_ensure_chapter('2')
        self.assertEqual(reader.device.clicks, [])

    def test_legacy_integer_page_index_accepts_matching_event_chapter(self):
        reader_type = importlib.import_module('campaign.event_20200917_cn.campaign_base').CampaignBase
        reader = self.campaign(reader_type=reader_type, chapter='t')
        reader.campaign_ensure_chapter(1)
        self.assertEqual(reader.device.clicks, [])
        self.assertGreaterEqual(reader.device.frame, 2)
        reader.device.save_screenshot.assert_not_called()

    def test_invalid_target_never_clicks(self):
        for target in ['0', '100', '1661']:
            with self.subTest(target=target):
                reader = self.campaign(chapter='16')
                with self.assertRaises(CampaignNameError):
                    reader.campaign_ensure_chapter(target)
                self.assertEqual(reader.device.clicks, [])
                reader.device.save_screenshot.assert_called_once_with(genre='campaign_chapter_unknown', interval=0)

    def test_invalid_current_never_clicks(self):
        reader = self.campaign(chapter='1661')
        with self.assertRaises(CampaignNameError):
            reader.campaign_ensure_chapter('16')
        self.assertEqual(reader.device.clicks, [])
        reader.device.multi_click.assert_not_called()

    def test_unstable_target_frame_does_not_end_navigation(self):
        reader = self.campaign(chapter='15', provider=lambda frame, _: '16' if frame == 1 else '15')
        with self.assertRaises(CampaignNameError):
            reader.campaign_ensure_chapter('16')
        self.assertGreater(reader.device.frame, 1)
        self.assertTrue(reader.device.clicks)

    def test_reverse_movement_does_not_clear_pending_click_history(self):
        reader = self.campaign(chapter='15', step=-1)
        with self.assertRaises(CampaignNameError):
            reader.campaign_ensure_chapter('16')
        self.assertEqual(reader.device.removals, [])

    def test_jump_over_chapter_does_not_clear_pending_click_history(self):
        reader = self.campaign(chapter='12', step=2)
        reader.campaign_ensure_chapter('16')
        self.assertEqual(len(reader.device.clicks), 2)
        self.assertEqual(reader.device.removals, [])

    def test_type_change_after_click_does_not_clear_pending_click_history(self):
        reader = self.campaign(chapter='1', provider=lambda frame, _: '1' if frame < 4 else 'd')
        with self.assertRaises(CampaignNameError):
            reader.campaign_ensure_chapter('16')
        self.assertEqual(len(reader.device.clicks), 1)
        self.assertEqual(reader.device.removals, [])

    def test_missing_arrow_is_bounded_without_clicking(self):
        reader = self.campaign(chapter='15')
        reader.appear_then_click.side_effect = None
        reader.appear_then_click.return_value = False
        with self.assertRaises(CampaignNameError):
            reader.campaign_ensure_chapter('16')
        self.assertLess(reader.device.clock - 100, 12)
        self.assertEqual(reader.device.clicks, [])
        self.assertEqual(reader.device.removals, [])

    def test_arrival_at_target_requires_new_frame(self):
        reader = self.campaign(chapter='16')
        reader.campaign_ensure_chapter('16')
        self.assertGreaterEqual(reader.device.frame, 2)
        self.assertEqual(reader.device.clicks, [])

    def test_bad_ocr_get_chapter_timeout_saves_scene_and_never_clicks(self):
        reader = self.campaign(chapter='1661')
        del reader.get_chapter_index
        reader._get_stage_name = Mock(side_effect=CampaignNameError('无效章节 16618'))
        reader.handle_get_chapter_additional = Mock(return_value=False)
        with self.assertRaises(CampaignNameError):
            reader.campaign_ensure_chapter('16')
        self.assertGreater(reader.device.frame, 1)
        self.assertEqual(reader.device.clicks, [])
        reader.device.save_screenshot.assert_called_once_with(genre='campaign_chapter_unknown', interval=0)

    def test_scene_write_failure_keeps_original_chapter_error(self):
        reader = self.campaign(chapter='1661')
        reader.device.save_screenshot.side_effect = OSError('模拟磁盘不可写')
        with self.assertRaisesRegex(CampaignNameError, '当前章节索引无效'):
            reader.campaign_ensure_chapter('16')
        self.assertEqual(reader.device.clicks, [])

    def test_outer_ui_does_not_repeat_eight_second_stalled_navigation(self):
        reader = self.campaign(chapter='15', step=0)
        reader.campaign_set_chapter = Mock(
            side_effect=lambda *_: reader.campaign_ensure_chapter('16'))
        reader.campaign_get_entrance = Mock()
        reader.handle_campaign_ui_additional = Mock(return_value=False)
        with self.assertRaises(ScriptEnd):
            reader.ensure_campaign_ui('16-4')
        reader.campaign_set_chapter.assert_called_once_with('16-4', 'normal')
        reader.campaign_get_entrance.assert_not_called()
        self.assertLess(reader.device.clock - 100, 12)

    def test_outer_ui_accepts_successful_progress_taking_longer_than_five_seconds(self):
        reader = self.campaign(chapter='1')
        reader.campaign_set_chapter = Mock(
            side_effect=lambda *_: reader.campaign_ensure_chapter('16'))
        entrance = object()
        reader.campaign_get_entrance = Mock(return_value=entrance)
        reader.handle_campaign_ui_additional = Mock(return_value=False)
        self.assertTrue(reader.ensure_campaign_ui('16-4'))
        self.assertGreater(reader.device.clock - 100, 5)
        self.assertIs(reader.ENTRANCE, entrance)
        reader.campaign_set_chapter.assert_called_once_with('16-4', 'normal')
        reader.device.save_screenshot.assert_not_called()


if __name__ == '__main__':
    unittest.main()
