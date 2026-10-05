"""用资源图和虚拟时间验证经营确认，以及重启前真实保存错误现场。"""

import os
import tempfile
import unittest
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from alas import AzurLaneAutoScript
from module.base.utils import load_image
from module.exception import GameStuckError
from module.handler.info_handler import GET_MISSION, POPUP_CANCEL, POPUP_CONFIRM, POPUP_SINGLE_WHITE
from module.island.island_business import (
    BUSINESS_START_IN_SHOP,
    ISLAND_BACK,
    ISLAND_GATHER_COLLECT_CHECK,
    POST_MANAGE_BUSINESS,
    POST_MANAGE_PRODUCTION,
    IslandBusiness,
)


def frame_with(*buttons):
    """只叠加按钮区域，不使用账号截图。"""
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    for button in buttons:
        x1, y1, x2, y2 = button.area
        frame[y1:y2, x1:x2] = load_image(button.file)[y1:y2, x1:x2]
    return frame


UNKNOWN = np.zeros((720, 1280, 3), dtype=np.uint8)
SHOP = frame_with(BUSINESS_START_IN_SHOP, ISLAND_BACK)
STARTED_SHOP = SHOP.copy()
x1, y1, x2, y2 = BUSINESS_START_IN_SHOP.area
gray = STARTED_SHOP[y1:y2, x1:x2].mean(axis=2).astype(np.uint8)
STARTED_SHOP[y1:y2, x1:x2] = gray[:, :, None]
POSTMANAGE = frame_with(POST_MANAGE_BUSINESS)
REVIEW_SHOP = frame_with(ISLAND_BACK)
REVIEW_SHOP[y1:y2, x1 + 150:x2 + 150] = SHOP[y1:y2, x1:x2]


class FakeClock:
    def __init__(self):
        self.now = 1000.0


class FakeDevice:
    def __init__(self, clock, timeline, screenshot_cost=0.5):
        self.clock = clock
        self.timeline = timeline
        self.t0 = clock.now
        self.image = UNKNOWN
        self.clicks = []
        self.sleeps = []
        self.screenshot_deque = deque(maxlen=5)
        self.package = 'test.package'
        self.screenshot_cost = screenshot_cost

    @property
    def elapsed(self):
        return self.clock.now - self.t0

    def clicks_for(self, button):
        name = Path(button.file).stem
        return [click for click in self.clicks if click['name'] == name]

    def screenshot(self):
        self.clock.now += self.screenshot_cost
        self.image = self.timeline(self)
        self.screenshot_deque.append({
            'time': datetime(2026, 10, 5) + timedelta(seconds=self.elapsed),
            'image': self.image,
        })
        return self.image

    def click(self, button, control_check=True):
        self.clicks.append({'time': self.elapsed, 'name': Path(button.file).stem, 'area': button.area})
        self.clock.now += 0.1

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.clock.now += seconds

    def stuck_record_add(self, button):
        pass


class BusinessUI(IslandBusiness):
    """跳过配置与设备初始化，保留真实识别、弹窗及确认逻辑。"""

    def __init__(self, device):
        self.device = device
        self.config = SimpleNamespace(BUTTON_OFFSET=30)
        self.interval_timer = {}
        self.goto_postmanage = Mock(side_effect=AssertionError('未知页面不应进入内部重启导航'))

    def ui_additional(self, get_ship=True):
        return False


class BusinessConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.enterContext(patch('module.base.timer.time', side_effect=lambda: self.clock.now))
        self.enterContext(patch('module.island.island_business.logger'))

    def run_confirmation(self, timeline):
        device = FakeDevice(self.clock, timeline)
        business = BusinessUI(device)
        result = business._confirm_business_start()
        self.assertTrue(result)
        self.assertTrue(all(seconds <= 0.1 for seconds in device.sleeps), '不能靠固定等待猜测页面就绪')
        business.goto_postmanage.assert_not_called()
        return device

    def test_slow_transition_waits_for_postmanage(self):
        def timeline(device):
            if not device.clicks_for(BUSINESS_START_IN_SHOP):
                return SHOP if device.elapsed >= 2 else UNKNOWN
            if backs := device.clicks_for(ISLAND_BACK):
                return POSTMANAGE if device.elapsed - backs[0]['time'] >= 4 else UNKNOWN
            return STARTED_SHOP

        device = self.run_confirmation(timeline)
        self.assertEqual(len(device.clicks_for(BUSINESS_START_IN_SHOP)), 1)
        self.assertEqual(len(device.clicks_for(ISLAND_BACK)), 1)
        self.assertGreaterEqual(device.elapsed - device.clicks_for(ISLAND_BACK)[0]['time'], 4)

    def test_delayed_popups_take_priority_over_background_page(self):
        for popup_buttons in [(GET_MISSION,), (POPUP_SINGLE_WHITE,), (POPUP_CANCEL, POPUP_CONFIRM)]:
            with self.subTest(popup=[button.name for button in popup_buttons]):
                popup_frame = frame_with(POST_MANAGE_BUSINESS, *popup_buttons)
                popup_names = {Path(button.file).stem for button in popup_buttons}

                def timeline(device):
                    if not device.clicks_for(BUSINESS_START_IN_SHOP):
                        return SHOP
                    if any(click['name'] in popup_names for click in device.clicks):
                        return POSTMANAGE if device.clicks_for(ISLAND_BACK) else STARTED_SHOP
                    if device.elapsed >= 2:
                        return popup_frame
                    return UNKNOWN

                device = self.run_confirmation(timeline)
                names = [click['name'] for click in device.clicks]
                popup_index = next(index for index, name in enumerate(names) if name in popup_names)
                self.assertLess(popup_index, names.index('ISLAND_BACK'))

    def test_missed_start_click_is_retried_only_while_blue(self):
        def timeline(device):
            if len(device.clicks_for(BUSINESS_START_IN_SHOP)) < 2:
                return SHOP
            return POSTMANAGE if device.clicks_for(ISLAND_BACK) else STARTED_SHOP

        device = self.run_confirmation(timeline)
        starts = device.clicks_for(BUSINESS_START_IN_SHOP)
        self.assertEqual(len(starts), 2)
        self.assertGreaterEqual(starts[1]['time'] - starts[0]['time'], 3)
        self.assertEqual(len(device.clicks_for(ISLAND_BACK)), 1)

    def test_missed_back_click_is_retried_with_new_screenshots(self):
        def timeline(device):
            if not device.clicks_for(BUSINESS_START_IN_SHOP):
                return SHOP
            return POSTMANAGE if len(device.clicks_for(ISLAND_BACK)) >= 2 else STARTED_SHOP

        device = self.run_confirmation(timeline)
        backs = device.clicks_for(ISLAND_BACK)
        self.assertEqual(len(backs), 2)
        self.assertGreaterEqual(backs[1]['time'] - backs[0]['time'], 3)

    def test_review_offset_uses_shifted_start_button(self):
        def timeline(device):
            if not device.clicks_for(BUSINESS_START_IN_SHOP):
                return REVIEW_SHOP
            return POSTMANAGE if device.clicks_for(ISLAND_BACK) else STARTED_SHOP

        device = self.run_confirmation(timeline)
        self.assertEqual(device.clicks_for(BUSINESS_START_IN_SHOP)[0]['area'][0], BUSINESS_START_IN_SHOP.area[0] + 150)

    def test_known_postmanage_tabs_exit_without_clicking_back(self):
        for marker in (POST_MANAGE_BUSINESS, POST_MANAGE_PRODUCTION, ISLAND_GATHER_COLLECT_CHECK):
            with self.subTest(marker=marker.name):
                frame = frame_with(marker)
                device = self.run_confirmation(lambda device: frame)
                self.assertEqual(device.clicks, [])

    def test_unknown_page_times_out_without_blind_clicks(self):
        device = FakeDevice(self.clock, lambda device: UNKNOWN)
        business = BusinessUI(device)
        with self.assertRaisesRegex(GameStuckError, '返回岗位管理超时'):
            business._confirm_business_start()
        self.assertEqual(device.clicks, [])
        self.assertGreaterEqual(device.elapsed, 30)
        self.assertLess(device.elapsed, 35)
        business.goto_postmanage.assert_not_called()

    def test_disabled_button_does_not_start_or_exit_shop(self):
        device = FakeDevice(self.clock, lambda device: STARTED_SHOP)
        business = BusinessUI(device)
        with self.assertRaises(GameStuckError):
            business._confirm_business_start()
        self.assertEqual(device.clicks, [])

    def test_slow_screenshots_keep_time_based_timeout(self):
        device = FakeDevice(self.clock, lambda device: UNKNOWN, screenshot_cost=3)
        business = BusinessUI(device)
        with self.assertRaises(GameStuckError):
            business._confirm_business_start()
        self.assertGreaterEqual(device.elapsed, 30)
        self.assertLess(device.elapsed, 34)
        self.assertEqual(device.clicks, [])

    def test_scheduler_saves_unknown_frames_before_requesting_restart(self):
        """执行真实确认、调度异常处理和 PNG／日志落盘，全部写入临时目录。"""
        unknown_frame = np.full((720, 1280, 3), 42, dtype=np.uint8)
        device = FakeDevice(self.clock, lambda device: unknown_frame)
        business = BusinessUI(device)
        business.appear = Mock(return_value=False)
        script = AzurLaneAutoScript.__new__(AzurLaneAutoScript)
        script.config_name = 'snapshot'
        script._channel_float_done = True
        script.__dict__['device'] = device
        script.__dict__['config'] = SimpleNamespace(
            Error_GameStuckRestart=False,
            Error_SaveError=True,
            Error_LlmAnalysis=False,
            Error_SaveErrorRetentionDays=0,
        )
        script.island_business = business._confirm_business_start
        script._check_sensitive_exit = Mock()
        script._notify_recoverable = Mock()

        with tempfile.TemporaryDirectory(prefix='business_error_') as folder:
            root = Path(folder)
            logfile = root / 'worker.log'
            logfile.write_text('经营返回未知页面，等待自动恢复\n', encoding='utf-8')
            events = []

            def request_restart(task):
                snapshots = list((root / 'log/error/snapshot').glob('*/*.png'))
                logs = list((root / 'log/error/snapshot').glob('*/log.txt'))
                self.assertEqual(len(snapshots), 5)
                self.assertEqual(len(logs), 1)
                self.assertIn('未知页面', logs[0].read_text(encoding='utf-8'))
                np.testing.assert_array_equal(np.asarray(Image.open(snapshots[-1])), unknown_frame)
                events.append(('现场已保存', task))

            script.config.task_call = request_restart
            previous_cwd = os.getcwd()
            try:
                os.chdir(root)
                with patch('alas.logger') as log, \
                        patch('module.handler.sensitive_info.handle_sensitive_image', side_effect=lambda image: image), \
                        patch('module.runtime.preview.set_task'):
                    log.log_file = str(logfile)
                    self.assertEqual(script.run('island_business', skip_first_screenshot=True), 'recoverable')
            finally:
                os.chdir(previous_cwd)
            self.assertEqual(events, [('现场已保存', 'Restart')])


if __name__ == '__main__':
    unittest.main()
