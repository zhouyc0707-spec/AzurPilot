"""经营事件按提示、详情、干净页签顺序关闭，点击冷却不得穿透遮罩。"""

import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from module.base import utils
from module.base.button import Button
from module.exception import GameStuckError
from module.island.assets import (
    ISLAND_BUSINESS_EVENT_POPUP, ISLAND_BUSINESS_EVENT_POPUP_CANCEL,
    ISLAND_BUSINESS_EVENT_SAFE_AREA, ISLAND_GATHER_COLLECT, ISLAND_GATHER_COLLECT_CHECK,
    POST_MANAGE_BUSINESS, POST_MANAGE_PRODUCTION,
)
from module.island.island_business import IslandBusiness
from tests.test_island_reward_state import FrameFlow


class IslandBusinessEventStateTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.island.island.logger'))
        self.enterContext(patch('module.island.island_business.logger'))

    def test_visibility_survives_click_cooldown_and_handler_reports_only_actions(self):
        for marker, clicked in ((ISLAND_BUSINESS_EVENT_POPUP, ISLAND_BUSINESS_EVENT_SAFE_AREA),
                                (ISLAND_BUSINESS_EVENT_POPUP_CANCEL, ISLAND_BUSINESS_EVENT_POPUP_CANCEL)):
            with self.subTest(marker=marker.name):
                flow = FrameFlow([])
                flow.frame = {marker}
                with flow.time_patch:
                    self.assertTrue(flow.task.is_business_event_popup())
                    self.assertTrue(flow.task.handle_business_event_popup())
                    self.assertTrue(flow.task.is_business_event_popup())
                    self.assertFalse(flow.task.handle_business_event_popup())
                    flow.now += 2.1
                    self.assertTrue(flow.task.handle_business_event_popup())
                self.assertEqual(flow.names(), [clicked.name, clicked.name])

    def test_event_and_detail_block_underlying_ready_business_tab(self):
        flow = FrameFlow([
            *[[ISLAND_BUSINESS_EVENT_POPUP, POST_MANAGE_BUSINESS]] * 6,
            *[[ISLAND_BUSINESS_EVENT_POPUP_CANCEL, POST_MANAGE_BUSINESS]] * 6,
            [POST_MANAGE_BUSINESS],
        ])
        self.assertTrue(flow.run('post_manage_mode', POST_MANAGE_BUSINESS))
        self.assertEqual(flow.names(), ['ISLAND_BUSINESS_EVENT_SAFE_AREA', 'ISLAND_BUSINESS_EVENT_POPUP_CANCEL'])
        self.assertEqual(flow.reads, 13)
        flow.task.device.sleep.assert_not_called()

    def test_event_and_detail_block_other_tab_clicks_until_fresh_clean_frame(self):
        flow = FrameFlow([
            *[[ISLAND_BUSINESS_EVENT_POPUP, POST_MANAGE_PRODUCTION]] * 6,
            *[[ISLAND_BUSINESS_EVENT_POPUP_CANCEL, POST_MANAGE_PRODUCTION]] * 6,
            [POST_MANAGE_PRODUCTION], [POST_MANAGE_BUSINESS],
        ])
        self.assertTrue(flow.run('post_manage_mode', POST_MANAGE_BUSINESS))
        self.assertEqual(flow.names(), ['ISLAND_BUSINESS_EVENT_SAFE_AREA',
                                       'ISLAND_BUSINESS_EVENT_POPUP_CANCEL', 'POST_MANAGE_PRODUCTION'])
        self.assertEqual(flow.clicks[-1][1], 13)

    def test_collection_entry_does_not_accept_underlying_collection_during_event(self):
        flow = FrameFlow([
            *[[ISLAND_BUSINESS_EVENT_POPUP, ISLAND_GATHER_COLLECT_CHECK, ISLAND_GATHER_COLLECT]] * 6,
            *[[ISLAND_BUSINESS_EVENT_POPUP_CANCEL, ISLAND_GATHER_COLLECT_CHECK, ISLAND_GATHER_COLLECT]] * 6,
            [ISLAND_GATHER_COLLECT_CHECK],
        ])
        self.assertTrue(flow.run('post_manage_mode_collection'))
        self.assertEqual(flow.names(), ['ISLAND_BUSINESS_EVENT_SAFE_AREA', 'ISLAND_BUSINESS_EVENT_POPUP_CANCEL'])
        self.assertEqual(flow.reads, 13)

    def test_collection_entry_waits_to_click_its_tab_until_event_details_close(self):
        flow = FrameFlow([
            [ISLAND_BUSINESS_EVENT_POPUP, ISLAND_GATHER_COLLECT],
            [ISLAND_BUSINESS_EVENT_POPUP_CANCEL, ISLAND_GATHER_COLLECT],
            [ISLAND_GATHER_COLLECT], [ISLAND_GATHER_COLLECT_CHECK],
        ])
        self.assertTrue(flow.run('post_manage_mode_collection'))
        self.assertEqual(flow.names(), ['ISLAND_BUSINESS_EVENT_SAFE_AREA',
                                       'ISLAND_BUSINESS_EVENT_POPUP_CANCEL', 'ISLAND_GATHER_COLLECT'])
        self.assertEqual(flow.clicks[-1][1], 3)

    def test_food_review_entry_switches_from_collection_without_guessing_a_popup(self):
        flow = FrameFlow([[ISLAND_GATHER_COLLECT_CHECK], [POST_MANAGE_BUSINESS]],
                         task_class=IslandBusiness)
        flow.run('_handle_food_review')
        self.assertEqual(flow.names(), ['POST_MANAGE_PRODUCTION'])
        self.assertEqual(flow.reads, 2)
        flow.task.device.sleep.assert_not_called()

    def test_persistent_event_times_out_with_bounded_click_frequency(self):
        for method, args in (('post_manage_mode', (POST_MANAGE_BUSINESS,)),
                             ('post_manage_mode_collection', ())):
            with self.subTest(method=method):
                flow = FrameFlow([[ISLAND_BUSINESS_EVENT_POPUP, POST_MANAGE_BUSINESS,
                                   ISLAND_GATHER_COLLECT_CHECK]] * 80)
                with self.assertRaises(GameStuckError):
                    flow.run(method, *args)
                self.assertEqual(set(flow.names()), {'ISLAND_BUSINESS_EVENT_SAFE_AREA'})
                stamps = [stamp for _, _, stamp in flow.clicks]
                self.assertTrue(all(b - a >= 2 for a, b in zip(stamps, stamps[1:])))

    def test_unknown_page_uses_existing_recovery_without_speculative_clicks(self):
        for method, args in (('post_manage_mode', (POST_MANAGE_BUSINESS,)),
                             ('post_manage_mode_collection', ())):
            with self.subTest(method=method):
                flow = FrameFlow([[]] * 80)
                with self.assertRaises(GameStuckError):
                    flow.run(method, *args)
                self.assertEqual(flow.clicks, [])


class IslandBusinessEventImageTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.island.island.logger'))
        self.addCleanup(utils.set_template_match_non_native_720p,
                        utils.TEMPLATE_MATCH_NON_NATIVE_720P,
                        utils.TEMPLATE_MATCH_NON_NATIVE_720P_RESOLUTION)
        utils.set_template_match_non_native_720p(False)
        self.fixture = utils.load_image(str(Path(__file__).parent /
                                           'fixtures/island_business_event/health_development_day.png'))
        for button in (ISLAND_BUSINESS_EVENT_POPUP, ISLAND_BUSINESS_EVENT_POPUP_CANCEL,
                       POST_MANAGE_BUSINESS, POST_MANAGE_PRODUCTION):
            button.clear_offset()
            self.addCleanup(button.clear_offset)

    def image_flow(self, image):
        flow = FrameFlow([]).use_images([])
        flow.task.device.image = image
        return flow

    def test_actual_failure_frame_matches_upstream_event_asset_without_threshold_relaxation(self):
        self.assertTrue(ISLAND_BUSINESS_EVENT_POPUP.match(self.fixture, offset=(50, 50), similarity=0.95))
        self.assertFalse(ISLAND_BUSINESS_EVENT_POPUP_CANCEL.match(self.fixture, offset=(20, 20)))
        flow = self.image_flow(self.fixture)
        self.assertTrue(flow.task.is_business_event_popup())
        with flow.time_patch:
            self.assertTrue(flow.task.handle_business_event_popup())
        self.assertEqual(flow.names(), ['ISLAND_BUSINESS_EVENT_SAFE_AREA'])

    def test_actual_first_stage_then_synthetic_details_and_clean_tab_finish_in_order(self):
        # 只有第一阶段来自故障帧；详情和最终页签由上游、现有资源构造。
        details = utils.load_image(ISLAND_BUSINESS_EVENT_POPUP_CANCEL.file)
        clean = utils.load_image(POST_MANAGE_BUSINESS.file)
        flow = FrameFlow([]).use_images([self.fixture] * 6 + [details] * 6 + [clean])
        self.assertTrue(flow.run('post_manage_mode', POST_MANAGE_BUSINESS))
        self.assertEqual(flow.names(), ['ISLAND_BUSINESS_EVENT_SAFE_AREA', 'ISLAND_BUSINESS_EVENT_POPUP_CANCEL'])
        self.assertEqual(flow.reads, 13)

    def test_normal_tabs_unknown_overlay_and_removed_marker_never_match_event(self):
        hidden_marker = self.fixture.copy()
        hidden_marker[338:450, 543:654] = 0
        candidates = [utils.load_image(button.file) for button in
                      (POST_MANAGE_BUSINESS, POST_MANAGE_PRODUCTION, ISLAND_GATHER_COLLECT_CHECK)]
        candidates.extend([np.zeros_like(self.fixture), hidden_marker])
        for index, image in enumerate(candidates):
            with self.subTest(frame=index):
                flow = self.image_flow(image)
                self.assertFalse(flow.task.is_business_event_popup())
                with flow.time_patch:
                    self.assertFalse(flow.task.handle_business_event_popup())
                self.assertEqual(flow.clicks, [])

    def test_actual_dimmed_tabs_without_event_markers_wait_for_clean_target(self):
        # 从真实覆盖层帧移除事件标志，保留原有虚化页签，模拟两阶段之间的过渡帧。
        obscured = self.fixture.copy()
        obscured[338:450, 543:654] = 0
        for method, args, target in (
                ('post_manage_mode', (POST_MANAGE_BUSINESS,), POST_MANAGE_BUSINESS),
                ('post_manage_mode_collection', (), ISLAND_GATHER_COLLECT_CHECK)):
            with self.subTest(method=method):
                clean = utils.load_image(target.file)
                flow = FrameFlow([]).use_images([obscured] * 6 + [clean])
                self.assertTrue(flow.run(method, *args))
                self.assertEqual(flow.reads, 7)
                self.assertEqual(flow.clicks, [])

    def test_all_servers_keep_upstream_geometry_and_resources(self):
        for name in (ISLAND_BUSINESS_EVENT_POPUP, ISLAND_BUSINESS_EVENT_POPUP_CANCEL):
            for server in ('cn', 'en', 'jp', 'tw'):
                with self.subTest(resource=name.name, server=server):
                    button = Button(area=name.raw_area[server], color=name.raw_color[server],
                                    button=name.raw_button[server], file=name.raw_file[server], name=name.name)
                    self.assertTrue(button.match(utils.load_image(button.file), offset=(20, 20), similarity=0.95))
        self.assertEqual(ISLAND_BUSINESS_EVENT_POPUP.raw_area['cn'], (593, 388, 604, 400))
        self.assertEqual(ISLAND_BUSINESS_EVENT_POPUP.raw_area['en'], (562, 388, 574, 400))
        for server in ('cn', 'en', 'jp', 'tw'):
            self.assertEqual(ISLAND_BUSINESS_EVENT_SAFE_AREA.raw_button[server], (596, 687, 680, 720))


if __name__ == '__main__':
    unittest.main()
