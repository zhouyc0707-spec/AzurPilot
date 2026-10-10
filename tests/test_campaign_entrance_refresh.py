"""入口过渡帧恢复：合成无账号截图、真实按钮颜色检查和虚拟时间。"""

import unittest
from contextlib import nullcontext
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from module.base.button import Button
from module.campaign.campaign_ui import CampaignUI
from module.exception import CampaignNameError, GameStuckError, RequestHumanTakeover
from module.map.map_operation import MapOperation


AREA_A = (100, 100, 180, 135)
AREA_B = (240, 150, 320, 185)
OLD_COLOR = (210, 40, 40)
NEW_COLOR = (40, 210, 40)


def entrance(name='d3', area=AREA_A, color=OLD_COLOR):
    return Button(area=area, color=color, button=area, name=name)


@dataclass
class Frame:
    page: str = 'stage'
    entries: dict = field(default_factory=dict)
    error: Exception | None = None
    elapsed: float = 1.1

    def image(self):
        # 只绘制检测区色块，不复制游戏截图、账号信息或资源数字。
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        for area, color in self.entries.values():
            x1, y1, x2, y2 = area
            image[y1:y2, x1:x2] = color
        return image


class FrameDevice:
    def __init__(self, frames):
        self.frames = frames
        self.index = 0
        self.clock = 100.0
        self.image = frames[0].image()
        self.clicks = []
        self.exhausted = GameStuckError('有限截图序列结束，模拟原有卡死保护')

    @property
    def frame(self):
        return self.frames[self.index]

    def screenshot(self):
        if self.index + 1 >= len(self.frames):
            raise self.exhausted
        self.index += 1
        self.clock += self.frame.elapsed
        self.image = self.frame.image()

    def click(self, button):
        self.clicks.append((self.index, button.name, tuple(button.button)))


class EntranceHarness:
    """仅隔离无关处理器，入口定位与进图循环调用产品实现。"""

    campaign_get_entrance = CampaignUI.campaign_get_entrance
    campaign_get_mode_names = CampaignUI.campaign_get_mode_names

    def __init__(self, frames, mode_switch=False):
        self.device = FrameDevice(frames)
        self.config = SimpleNamespace(
            MAP_HAS_MODE_SWITCH=mode_switch, campaign_name='d3', DropRecord_CombatRecord='disabled',
        )
        self.stat = SimpleNamespace(new=Mock(return_value=nullcontext(None)))
        self.map_clear_percentage_timer = Mock()
        self.map_is_auto_search = False
        self.stage_entrance = entrance()
        self.ENTRANCE = self.stage_entrance
        self.campaign_chapter = 'original'
        self.ocr_frames = []
        self.refresh_calls = []
        self._get_stage_name = Mock(side_effect=self._read_current_stage)
        for name in (
            'handle_handover_conflict', 'handle_auto_search_continue', 'handle_retirement',
            'handle_use_data_key', 'handle_submarine_support_popup', 'handle_combat_low_emotion',
            'handle_urgent_commission', 'handle_2x_book_popup', 'handle_submarine_cost_popup',
            'handle_story_skip', 'handle_map_mode_switch', 'handle_in_map_with_enemy_searching',
        ):
            setattr(self, name, Mock(return_value=False))

    def is_in_stage_page(self):
        return self.device.frame.page == 'stage'

    def is_in_map(self):
        return self.device.frame.page == 'map'

    def is_combat_loading(self):
        return self.device.frame.page == 'combat'

    def appear(self, button, **kwargs):
        # 本套测试不展示 DAILY_CHECK、舰队准备等无关模板。
        return False

    def appear_then_click(self, button):
        if button.appear_on(self.device.image, threshold=30):
            self.device.click(button)
            return True
        return False

    def _read_current_stage(self, image):
        assert image is self.device.image
        self.ocr_frames.append(self.device.index)
        self.stage_entrance = {}
        self.campaign_chapter = 'temporary'
        if self.device.frame.error:
            raise self.device.frame.error
        for name, (area, _) in self.device.frame.entries.items():
            button = entrance(name, area)
            button.load_color(image)
            self.stage_entrance[name] = button
        if not self.stage_entrance:
            raise CampaignNameError

    def campaign_refresh_entrance(self, name):
        self.refresh_calls.append((self.device.index, name, self.device.clock))
        return CampaignUI.campaign_refresh_entrance(self, name)


def stage(name='d3', area=AREA_A, color=NEW_COLOR, elapsed=1.1):
    return Frame(entries={name: (area, color)}, elapsed=elapsed)


def run_enter(harness, old=None):
    if old is None:
        old = harness.ENTRANCE
    with patch('module.base.timer.time', side_effect=lambda: harness.device.clock):
        return MapOperation.enter_map(harness, old)


class CampaignRefreshEntranceTests(unittest.TestCase):
    def test_current_frame_lookup_restores_published_state(self):
        harness = EntranceHarness([stage()])
        old = harness.stage_entrance
        found = harness.campaign_refresh_entrance('d3')
        self.assertEqual(found.name, 'd3')
        self.assertTrue(found.appear_on(harness.device.image, threshold=30))
        self.assertIs(harness.stage_entrance, old)
        self.assertEqual(harness.campaign_chapter, 'original')
        self.assertEqual(harness.device.clicks, [])

    def test_other_pages_do_not_read_or_click(self):
        for page in ('unknown', 'preparation', 'fleet', 'combat', 'map'):
            with self.subTest(page=page):
                harness = EntranceHarness([Frame(page=page)])
                old = harness.stage_entrance
                self.assertIsNone(harness.campaign_refresh_entrance('d3'))
                harness._get_stage_name.assert_not_called()
                self.assertIs(harness.stage_entrance, old)
                self.assertEqual(harness.device.clicks, [])

    def test_missing_target_keeps_old_button_and_chapter(self):
        harness = EntranceHarness([stage(name='d2')])
        old = harness.stage_entrance
        self.assertIsNone(harness.campaign_refresh_entrance('d3'))
        self.assertIs(harness.stage_entrance, old)
        self.assertEqual(harness.campaign_chapter, 'original')

    def test_transient_parser_errors_restore_state_and_return_none(self):
        for error in (CampaignNameError(), IndexError('过渡帧没有章节')):
            with self.subTest(error=type(error).__name__):
                harness = EntranceHarness([Frame(error=error)])
                old = harness.stage_entrance
                self.assertIsNone(harness.campaign_refresh_entrance('d3'))
                self.assertIs(harness.stage_entrance, old)
                self.assertEqual(harness.campaign_chapter, 'original')

    def test_unexpected_parser_errors_propagate_and_restore_state(self):
        error = RuntimeError('非识别故障')
        harness = EntranceHarness([Frame(error=error)])
        old = harness.stage_entrance
        with self.assertRaises(RuntimeError) as raised:
            harness.campaign_refresh_entrance('d3')
        self.assertIs(raised.exception, error)
        self.assertIs(harness.stage_entrance, old)
        self.assertEqual(harness.campaign_chapter, 'original')

    def test_getter_error_also_restores_state(self):
        harness = EntranceHarness([stage()])
        old = harness.stage_entrance
        error = RuntimeError('入口映射内部故障')
        harness.campaign_get_entrance = Mock(side_effect=error)
        with self.assertRaises(RuntimeError) as raised:
            harness.campaign_refresh_entrance('d3')
        self.assertIs(raised.exception, error)
        self.assertIs(harness.stage_entrance, old)
        self.assertEqual(harness.campaign_chapter, 'original')

    def test_alias_and_mode_mapping_keep_original_requested_name(self):
        for requested, shown, mode_switch in (
            ('d3_3', 'd3', False), ('d3', 'b3', True), ('d3_3', 'b3', True),
        ):
            with self.subTest(requested=requested, shown=shown):
                harness = EntranceHarness([stage(name=shown)], mode_switch=mode_switch)
                found = harness.campaign_refresh_entrance(requested)
                self.assertEqual(found.name, requested)
                self.assertEqual(found.button, AREA_A)

    def test_custom_getter_mapping_is_reused_and_state_restored(self):
        class MappedEntranceHarness(EntranceHarness):
            def campaign_get_entrance(self, name):
                self.mapped_request = name
                return super().campaign_get_entrance('vsp' if name == 'sp' else name)

        harness = MappedEntranceHarness([stage(name='vsp')])
        old = harness.stage_entrance
        found = harness.campaign_refresh_entrance('sp')
        self.assertEqual(harness.mapped_request, 'sp')
        self.assertEqual(found.name, 'vsp')
        self.assertTrue(found.appear_on(harness.device.image, threshold=30))
        self.assertIs(harness.stage_entrance, old)
        self.assertEqual(harness.campaign_chapter, 'original')


class EnterMapEntranceRefreshTests(unittest.TestCase):
    def test_stale_color_or_position_recovers_after_two_stable_frames(self):
        for area in (AREA_A, AREA_B):
            with self.subTest(area=area):
                harness = EntranceHarness([stage(area=area), stage(area=area), Frame(page='combat')])
                self.assertTrue(run_enter(harness))
                self.assertEqual(harness.device.clicks, [(1, 'd3', area)])
                self.assertEqual(harness.ocr_frames, [0, 1])
                self.assertEqual(harness.stage_entrance.button, area)
                self.assertIs(harness.ENTRANCE, harness.stage_entrance)

    def test_moving_candidate_waits_for_stable_position(self):
        harness = EntranceHarness([stage(area=AREA_A), stage(area=AREA_B), stage(area=AREA_B),
                                   Frame(page='combat')])
        self.assertTrue(run_enter(harness))
        self.assertEqual(harness.device.clicks, [(2, 'd3', AREA_B)])

    def test_changing_candidate_color_waits_for_stable_color(self):
        second_color = (40, 40, 210)
        harness = EntranceHarness([stage(), stage(color=second_color), stage(color=second_color),
                                   Frame(page='combat')])
        self.assertTrue(run_enter(harness))
        self.assertEqual(harness.device.clicks, [(2, 'd3', AREA_A)])

    def test_unstable_frames_never_click_and_keep_published_entrance(self):
        for frames in (
            [stage(area=AREA_A), stage(area=AREA_B), stage(area=AREA_A)],
            [stage(), stage(color=(40, 40, 210)), stage()],
        ):
            with self.subTest(moving=frames[1].entries['d3'][0] != AREA_A):
                harness = EntranceHarness(frames)
                old = harness.ENTRANCE
                with self.assertRaises(GameStuckError):
                    run_enter(harness)
                self.assertEqual(harness.device.clicks, [])
                self.assertIs(harness.ENTRANCE, old)
                self.assertIs(harness.stage_entrance, old)

    def test_missing_target_interrupts_consecutive_confirmation(self):
        harness = EntranceHarness([stage(), stage(name='d2'), stage(), stage(), Frame(page='combat')])
        self.assertTrue(run_enter(harness))
        self.assertEqual(harness.device.clicks, [(3, 'd3', AREA_A)])

    def test_missing_wrong_target_or_unconfirmed_pages_never_click(self):
        for frame in (Frame(), stage(name='d2'), Frame(page='unknown'), Frame(page='preparation'),
                      Frame(page='fleet')):
            with self.subTest(page=frame.page, entries=tuple(frame.entries)):
                harness = EntranceHarness([frame, frame, Frame(page='combat')])
                self.assertTrue(run_enter(harness))
                self.assertEqual(harness.device.clicks, [])
                self.assertEqual(harness.ENTRANCE.color, OLD_COLOR)

    def test_stable_original_button_clicks_without_ocr(self):
        harness = EntranceHarness([stage(color=OLD_COLOR), Frame(page='combat')])
        self.assertTrue(run_enter(harness))
        self.assertEqual(harness.device.clicks, [(0, 'd3', AREA_A)])
        harness._get_stage_name.assert_not_called()
        self.assertEqual(harness.refresh_calls, [])

    def test_refresh_none_allows_original_button_to_become_valid_again(self):
        harness = EntranceHarness([stage(), Frame(page='unknown'), stage(color=OLD_COLOR),
                                   Frame(page='combat')])
        self.assertTrue(run_enter(harness))
        self.assertEqual(harness.device.clicks, [(2, 'd3', AREA_A)])
        self.assertEqual(harness.ocr_frames, [0])

    def test_different_candidate_names_at_same_position_do_not_confirm(self):
        harness = EntranceHarness([stage(), stage(), Frame(page='combat')])
        harness.campaign_refresh_entrance = Mock(side_effect=[
            entrance('d3', color=NEW_COLOR), entrance('d2', color=NEW_COLOR), None,
        ])
        self.assertTrue(run_enter(harness))
        self.assertEqual(harness.device.clicks, [])
        self.assertEqual(harness.campaign_refresh_entrance.call_args_list[0].args, ('d3',))
        self.assertEqual(harness.campaign_refresh_entrance.call_args_list[1].args, ('d3',))

    def test_refresh_is_throttled_until_one_second_passes(self):
        frames = [stage()] + [stage(elapsed=0.2) for _ in range(7)] + [Frame(page='combat')]
        harness = EntranceHarness(frames)
        self.assertTrue(run_enter(harness))
        self.assertEqual(len(harness.ocr_frames), 2)
        first, second = harness.refresh_calls
        self.assertGreaterEqual(second[2] - first[2], 1)
        self.assertEqual(harness.device.clicks[0][0], second[0])

    def test_compatibility_aliases_work_in_full_enter_loop(self):
        for requested, shown, mode_switch in (
            ('d3_3', 'd3', False), ('d3', 'b3', True),
        ):
            with self.subTest(requested=requested):
                harness = EntranceHarness([stage(name=shown), stage(name=shown), Frame(page='combat')],
                                           mode_switch=mode_switch)
                harness.ENTRANCE = entrance(requested)
                self.assertTrue(run_enter(harness))
                self.assertEqual(harness.device.clicks, [(1, requested, AREA_A)])
                self.assertTrue(all(name == requested for _, name, _ in harness.refresh_calls))

    def test_failed_refresh_does_not_swallow_original_stuck_error(self):
        harness = EntranceHarness([Frame(), Frame()])
        with self.assertRaises(GameStuckError) as raised:
            run_enter(harness)
        self.assertIs(raised.exception, harness.device.exhausted)
        self.assertEqual(harness.device.clicks, [])

    def test_original_campaign_click_limit_still_requires_takeover(self):
        frames = [stage(), stage()] + [stage(elapsed=5.1) for _ in range(6)]
        harness = EntranceHarness(frames)
        with self.assertRaises(RequestHumanTakeover):
            run_enter(harness)
        self.assertEqual(len(harness.device.clicks), 6)
        self.assertEqual(harness.ocr_frames, [0, 1])

    def test_no_hook_keeps_legacy_success_and_stuck_behavior(self):
        for color, expected_clicks in ((OLD_COLOR, [(0, 'd3', AREA_A)]), (NEW_COLOR, [])):
            with self.subTest(color=color):
                harness = EntranceHarness([stage(color=color), Frame(page='combat')])
                harness.campaign_refresh_entrance = None
                self.assertTrue(run_enter(harness))
                self.assertEqual(harness.device.clicks, expected_clicks)
                harness._get_stage_name.assert_not_called()
        harness = EntranceHarness([Frame()])
        harness.campaign_refresh_entrance = None
        with self.assertRaises(GameStuckError):
            run_enter(harness)


if __name__ == '__main__':
    unittest.main()
