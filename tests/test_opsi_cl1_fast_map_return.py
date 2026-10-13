"""使用真实计时器和截图状态回放验证侵蚀一结算回图提速，不连接游戏。"""

from dataclasses import dataclass
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from module.base import utils
from module.os.assets import GLOBE_GOTO_MAP, MAP_GOTO_GLOBE_FOG
from module.os_handler.assets import AUTO_SEARCH_REWARD, IN_MAP, STORAGE_CHECK
from module.os_handler.map_event import MapEventHandler
from module.ui.assets import BACK_ARROW


@dataclass(frozen=True)
class ReturnFrame:
    """一张新截图的可见状态；截图到来时才推进虚拟时间。"""

    name: str
    reward: bool = False
    in_map: bool = False
    map_color: bool = False
    fog_color: bool = False
    map_event: bool = False
    globe: bool = False
    storage: bool = False
    cleared: bool = False
    elapsed: float = 0.2
    pixels: np.ndarray | None = None


class ReplayDevice:
    def __init__(self, owner, frames):
        self.owner = owner
        self.frames = iter(frames)
        self.clicks = []
        self.screenshots = []
        self.set_frame(next(self.frames))

    def set_frame(self, frame):
        self.frame = frame
        self.image = frame.pixels if frame.pixels is not None else frame

    def screenshot(self):
        try:
            frame = next(self.frames)
        except StopIteration as exc:
            raise AssertionError('结算没有在提供的模拟截图内完成') from exc
        self.owner.monotonic += frame.elapsed
        self.set_frame(frame)
        self.screenshots.append(frame.name)

    def click(self, button):
        # 点击不直接改变旧截图，必须由下一次 screenshot 取得页面变化。
        self.clicks.append((button, self.frame.name, self.owner.monotonic))

    def sleep(self, seconds):
        # appear_then_click 的已有防连点间隔，不代替截图变化。
        self.owner.monotonic += seconds


class MapReturnReplay(MapEventHandler):
    def __init__(self, frames, command='OpsiHazard1Leveling', override=None):
        self.monotonic = 100.0
        self.config = SimpleNamespace(task=SimpleNamespace(command=command))
        if override is not None:
            self.config._bind_task_override = override
        self.interval_timer = {}
        self.device = ReplayDevice(self, frames)
        self.color_checks = []
        self.map_event_checks = []
        self.info_bar_count = Mock(side_effect=lambda: int(self.device.frame.cleared))
        self.ensure_no_info_bar = Mock(side_effect=lambda: self.device.frame.cleared)
        self.os_auto_search_capture_reward_pages = Mock()

    def appear(self, button, offset=0, interval=0, **kwargs):
        visible = {
            AUTO_SEARCH_REWARD: self.device.frame.reward,
            GLOBE_GOTO_MAP: self.device.frame.globe,
            STORAGE_CHECK: self.device.frame.storage,
        }.get(button, False)
        # 保留真实按钮冷却语义，raw appear 则只反映当前截图。
        timer = self.get_interval_timer(button, interval, renew=True) if interval else None
        if timer is not None and not timer.reached():
            return False
        if visible and timer is not None:
            timer.reset()
        return visible

    def is_in_map(self):
        return self.device.frame.in_map

    def match_template_color(self, button, offset=0, **kwargs):
        self.color_checks.append((self.device.frame.name, button, offset))
        if self.device.frame.pixels is not None:
            return button.match_template_color(self.device.image, offset=offset, **kwargs)
        if button is IN_MAP:
            return self.device.frame.map_color
        if button is MAP_GOTO_GLOBE_FOG:
            return self.device.frame.fog_color
        return False

    def handle_map_event(self):
        self.map_event_checks.append(self.device.frame.name)
        if self.device.frame.map_event:
            self.device.click('地图事件')
            return True
        return False


class Cl1FastMapReturnTests(unittest.TestCase):
    def run_replay(self, frames, *, command='OpsiHazard1Leveling', override=None,
                   drop=None, cooldown=False):
        handler = MapReturnReplay(frames, command=command, override=override)
        with patch('module.base.timer.time', side_effect=lambda: handler.monotonic):
            if cooldown:
                handler.get_interval_timer(AUTO_SEARCH_REWARD, interval=2).reset()
            cleared = handler.os_auto_search_quit(drop=drop)
        return handler, cleared

    @staticmethod
    def stable_maps(count=10, *, color=True, elapsed=0.2):
        return [ReturnFrame(f'地图{i}', in_map=True, map_color=color, elapsed=elapsed)
                for i in range(count)]

    def test_cl1_returns_on_first_fresh_colored_map_after_reward_click(self):
        handler, cleared = self.run_replay([
            ReturnFrame('结算', reward=True, cleared=True),
            ReturnFrame('回到地图', in_map=True, map_color=True),
        ])
        self.assertTrue(cleared)
        self.assertEqual(handler.device.screenshots, ['回到地图'])
        self.assertEqual([(button, frame) for button, frame, _ in handler.device.clicks],
                         [(AUTO_SEARCH_REWARD, '结算')])
        self.assertAlmostEqual(handler.monotonic - 100, 0.2)
        self.assertEqual(handler.color_checks, [('回到地图', IN_MAP, (200, 5))])

    def test_stale_reward_with_background_map_cannot_finish_or_repeat_capture(self):
        drop = Mock()
        handler, cleared = self.run_replay([
            ReturnFrame('结算', reward=True, in_map=True, map_color=True, cleared=True),
            ReturnFrame('奖励仍在一', reward=True, in_map=True, map_color=True),
            ReturnFrame('奖励仍在二', reward=True, in_map=True, map_color=True),
            ReturnFrame('地图', in_map=True, map_color=True),
        ], drop=drop)
        self.assertTrue(cleared)
        self.assertEqual(handler.device.screenshots, ['奖励仍在一', '奖励仍在二', '地图'])
        self.assertEqual(len(handler.device.clicks), 1)
        self.assertEqual(handler.map_event_checks, ['地图'])
        self.assertEqual(handler.color_checks, [('地图', IN_MAP, (200, 5))])
        handler.ensure_no_info_bar.assert_called_once_with()
        drop.handle_add.assert_called_once_with(main=handler, before=4)
        handler.os_auto_search_capture_reward_pages.assert_called_once_with(drop)

    def test_visible_reward_in_existing_cooldown_blocks_background_map(self):
        handler, _ = self.run_replay([
            ReturnFrame('奖励冷却', reward=True, in_map=True, map_color=True),
            ReturnFrame('仍在冷却', reward=True, in_map=True, map_color=True),
            ReturnFrame('奖励允许补点', reward=True, elapsed=2.1),
            ReturnFrame('地图', in_map=True, map_color=True),
        ], cooldown=True)
        self.assertEqual([(button, frame) for button, frame, _ in handler.device.clicks],
                         [(AUTO_SEARCH_REWARD, '奖励允许补点')])
        self.assertEqual(handler.map_event_checks, ['地图'])

    def test_unknown_frames_do_not_end_before_positive_map(self):
        handler, _ = self.run_replay([
            ReturnFrame('结算', reward=True),
            ReturnFrame('过场'),
            ReturnFrame('未知页', elapsed=2),
            ReturnFrame('地图', in_map=True, map_color=True),
        ])
        self.assertEqual(handler.device.screenshots, ['过场', '未知页', '地图'])
        self.assertEqual(handler.color_checks, [('地图', IN_MAP, (200, 5))])

    def test_extra_map_event_is_handled_before_search_and_uses_new_frame(self):
        handler, _ = self.run_replay([
            ReturnFrame('结算', reward=True),
            ReturnFrame('地图事件遮罩', map_event=True, in_map=True, map_color=True),
            ReturnFrame('事件关闭后地图', in_map=True, map_color=True),
        ])
        self.assertEqual([(button, frame) for button, frame, _ in handler.device.clicks],
                         [(AUTO_SEARCH_REWARD, '结算'), ('地图事件', '地图事件遮罩')])
        self.assertEqual(handler.color_checks, [('事件关闭后地图', IN_MAP, (200, 5))])

    def test_globe_recovers_before_fast_map_exit(self):
        handler, _ = self.run_replay([
            ReturnFrame('结算', reward=True),
            ReturnFrame('误入地球仪', globe=True, in_map=True, map_color=True),
            ReturnFrame('地图', in_map=True, map_color=True),
        ])
        self.assertEqual([(button, frame) for button, frame, _ in handler.device.clicks],
                         [(AUTO_SEARCH_REWARD, '结算'), (GLOBE_GOTO_MAP, '误入地球仪')])
        self.assertEqual(handler.color_checks, [('地图', IN_MAP, (200, 5))])

    def test_storage_recovers_before_fast_map_exit(self):
        handler, _ = self.run_replay([
            ReturnFrame('结算', reward=True),
            ReturnFrame('误入仓库', storage=True, in_map=True, map_color=True),
            ReturnFrame('地图', in_map=True, map_color=True),
        ])
        self.assertEqual([(button, frame) for button, frame, _ in handler.device.clicks],
                         [(AUTO_SEARCH_REWARD, '结算'), (BACK_ARROW, '误入仓库')])
        self.assertEqual(handler.color_checks, [('地图', IN_MAP, (200, 5))])

    def test_globe_in_click_cooldown_still_blocks_fast_map_exit(self):
        handler, _ = self.run_replay([
            ReturnFrame('结算', reward=True),
            ReturnFrame('误入地球仪', globe=True, in_map=True, map_color=True),
            ReturnFrame('地球仪补点冷却', globe=True, in_map=True, map_color=True),
            ReturnFrame('地图', in_map=True, map_color=True),
        ])
        self.assertEqual(handler.device.screenshots,
                         ['误入地球仪', '地球仪补点冷却', '地图'])
        self.assertEqual([button for button, _, _ in handler.device.clicks],
                         [AUTO_SEARCH_REWARD, GLOBE_GOTO_MAP])
        self.assertEqual(handler.color_checks, [('地图', IN_MAP, (200, 5))])

    def test_storage_in_click_cooldown_still_blocks_fast_map_exit(self):
        handler, _ = self.run_replay([
            ReturnFrame('结算', reward=True),
            ReturnFrame('误入仓库', storage=True, in_map=True, map_color=True),
            ReturnFrame('仓库补点冷却', storage=True, in_map=True, map_color=True),
            ReturnFrame('地图', in_map=True, map_color=True),
        ])
        self.assertEqual(handler.device.screenshots,
                         ['误入仓库', '仓库补点冷却', '地图'])
        self.assertEqual([button for button, _, _ in handler.device.clicks],
                         [AUTO_SEARCH_REWARD, BACK_ARROW])
        self.assertEqual(handler.color_checks, [('地图', IN_MAP, (200, 5))])

    def test_fog_map_has_its_own_positive_color_confirmation(self):
        handler, _ = self.run_replay([
            ReturnFrame('结算', reward=True),
            ReturnFrame('雾天地图', in_map=True, fog_color=True),
        ])
        self.assertEqual(handler.color_checks, [
            ('雾天地图', IN_MAP, (200, 5)),
            ('雾天地图', MAP_GOTO_GLOBE_FOG, (5, 5)),
        ])
        self.assertAlmostEqual(handler.monotonic - 100, 0.2)

    def test_proxy_bound_cl1_also_uses_fast_return(self):
        handler, _ = self.run_replay([
            ReturnFrame('结算', reward=True),
            ReturnFrame('地图', in_map=True, map_color=True),
        ], command='OpsiScheduler', override='OpsiHazard1Leveling')
        self.assertEqual(handler.device.screenshots, ['地图'])

    def test_cl1_primary_command_remains_fast_with_unrelated_override(self):
        handler, _ = self.run_replay([
            ReturnFrame('结算', reward=True),
            ReturnFrame('地图', in_map=True, map_color=True),
        ], override='OpsiExplore')
        self.assertEqual(handler.device.screenshots, ['地图'])

    def test_unrelated_proxy_keeps_stable_confirmation(self):
        handler, _ = self.run_replay([
            ReturnFrame('结算', reward=True), *self.stable_maps(),
        ], command='OpsiScheduler', override='OpsiMeowfficerFarming')
        self.assertGreater(handler.monotonic - 100, 1.2)
        self.assertEqual(handler.color_checks, [])

    def test_other_tasks_keep_original_time_and_multiframe_confirmation(self):
        for command in ('OpsiExplore', 'OpsiMeowfficerFarming', 'OpsiAbyssal'):
            with self.subTest(command=command):
                handler, _ = self.run_replay([
                    ReturnFrame('结算', reward=True), *self.stable_maps(),
                ], command=command)
                self.assertGreater(handler.monotonic - 100, 1.2)
                self.assertGreaterEqual(len(handler.device.screenshots), 4)
                self.assertEqual(handler.color_checks, [])

    def test_cl1_without_current_reward_click_keeps_original_confirmation(self):
        handler, cleared = self.run_replay(self.stable_maps())
        self.assertFalse(cleared)
        self.assertGreater(handler.monotonic - 100, 1.2)
        self.assertGreaterEqual(len(handler.device.screenshots), 3)
        self.assertEqual(handler.device.clicks, [])
        self.assertEqual(handler.color_checks, [])

    def test_color_not_ready_uses_original_fallback(self):
        handler, _ = self.run_replay([
            ReturnFrame('结算', reward=True), *self.stable_maps(color=False),
        ])
        self.assertGreater(handler.monotonic - 100, 1.2)
        self.assertGreaterEqual(len(handler.device.screenshots), 4)

    def test_multiframe_confirmation_is_not_bypassed_by_slow_screenshot(self):
        handler, _ = self.run_replay([
            ReturnFrame('结算', reward=True),
            *self.stable_maps(color=False, elapsed=1.5),
        ])
        self.assertEqual(len(handler.device.screenshots), 4)
        self.assertAlmostEqual(handler.monotonic - 100, 6)

    def test_color_recovery_enables_fast_exit_on_that_frame(self):
        handler, _ = self.run_replay([
            ReturnFrame('结算', reward=True),
            ReturnFrame('地图仍暗', in_map=True),
            ReturnFrame('地图颜色恢复', in_map=True, map_color=True),
        ])
        self.assertEqual(handler.device.screenshots, ['地图仍暗', '地图颜色恢复'])
        self.assertAlmostEqual(handler.monotonic - 100, 0.4)

    def test_drop_capture_preserves_cleared_true_or_false(self):
        for cleared in (False, True):
            with self.subTest(cleared=cleared):
                drop = Mock()
                handler, result = self.run_replay([
                    ReturnFrame('结算', reward=True, cleared=cleared),
                    ReturnFrame('地图', in_map=True, map_color=True),
                ], drop=drop)
                self.assertEqual(result, cleared)
                handler.ensure_no_info_bar.assert_called_once_with()
                handler.info_bar_count.assert_not_called()
                drop.handle_add.assert_called_once_with(main=handler, before=4)
                handler.os_auto_search_capture_reward_pages.assert_called_once_with(drop)
                self.assertEqual(len(handler.device.clicks), 1)

    def test_dark_real_map_template_matches_luma_but_cannot_fast_return(self):
        """线性压暗仍有相同图案，颜色校验必须阻止直接使用该帧。"""
        previous_mode = utils.TEMPLATE_MATCH_NON_NATIVE_720P
        previous_resolution = utils.TEMPLATE_MATCH_NON_NATIVE_720P_RESOLUTION
        self.addCleanup(utils.set_template_match_non_native_720p,
                        previous_mode, previous_resolution)
        utils.set_template_match_non_native_720p(False, resolution=(1280, 720))
        self.addCleanup(IN_MAP.clear_offset)
        self.addCleanup(MAP_GOTO_GLOBE_FOG.clear_offset)
        source = utils.load_image(IN_MAP.file)
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        x1, y1, x2, y2 = IN_MAP.area
        image[y1:y2, x1:x2] = source[y1:y2, x1:x2]
        dark = (image.astype(float) * 0.4).astype(np.uint8)
        self.assertTrue(IN_MAP.match_luma(dark, offset=(200, 5)))
        self.assertFalse(IN_MAP.match_template_color(dark, offset=(200, 5)))
        self.assertTrue(IN_MAP.match_template_color(image, offset=(200, 5)))

        handler, _ = self.run_replay([
            ReturnFrame('结算', reward=True),
            ReturnFrame('亮度仍匹配的暗帧', in_map=True, pixels=dark),
            ReturnFrame('原色地图', in_map=True, pixels=image),
        ])
        self.assertEqual(handler.device.screenshots, ['亮度仍匹配的暗帧', '原色地图'])
        self.assertAlmostEqual(handler.monotonic - 100, 0.4)


if __name__ == '__main__':
    unittest.main()
