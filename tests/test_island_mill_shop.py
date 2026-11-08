"""商店直达磨坊的截图、名称识别及有界导航回归。"""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from module.base.button import Button
from module.base import utils
from module.island import island as island_module
from module.island.island import Island
from module.island import assets as island_assets
from module.island_exchange import assets as exchange_assets
from tests.test_island_goto_management import FakeClock, FakeDevice


class TestPage:
    def __init__(self, name, check_button):
        self.name = name
        self.check_button = check_button
        self.links = {}


def detection(text, x=25, y=315, score=0.99):
    return text, [(x, y), (x + 44, y), (x + 44, y + 20), (x, y + 20)], score


class IslandMillShopTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.enterContext(patch('module.base.timer.time', lambda: self.clock.now))
        self.enterContext(patch('module.island.island.logger'))
        self.enterContext(patch('module.island.island.is_daily_supply_reward', return_value=False))
        self.addCleanup(utils.set_template_match_non_native_720p,
                        utils.TEMPLATE_MATCH_NON_NATIVE_720P,
                        utils.TEMPLATE_MATCH_NON_NATIVE_720P_RESOLUTION)
        utils.set_template_match_non_native_720p(False)
        names = ('ISLAND_CHECK', 'ISLAND_MILL_CHECK', 'ISLAND_MAP_CHECK', 'ISLAND_GOTO_MAP',
                 'ISLAND_ACCESS_MAP', 'AIR_DROP_RUN_AWAY', 'ISLAND_GET', 'ISLAND_INFO_EXIT',
                 'ISLAND_POSTMANAGE_CHECK', 'ISLAND_MANAGEMENT_CHECK', 'ISLAND_WAREHOUSE_CHECK',
                 'POST_MANAGE_PRODUCTION', 'ISLAND_POSTMANAGE_GOTO_MANAGEMENT',
                 'ISLAND_POST_CHECK', 'ISLAND_POST_VACANT_CHECK', 'ISLAND_POST_SELECT',
                 'ISLAND_WORKING', 'POST_ADD', 'POST_GET', 'ISLAND_SHOPPING_CHECK')
        originals = {name: getattr(island_module, name) for name in names}
        originals.update(
            ISLAND_MILL_PROCESS_CHECK=island_assets.ISLAND_MILL_CHECK,
            ALAS_EXCHANGE_GOTO_SHOP=exchange_assets.ALAS_EXCHANGE_GOTO_SHOP,
            ALAS_EXCHANGE_SHOP_CHECK=exchange_assets.ALAS_EXCHANGE_SHOP_CHECK,
        )
        self.buttons = {
            name: Button(area=button.raw_area['cn'], color=button.raw_color['cn'],
                         button=button.raw_button['cn'], file=button.raw_file['cn'], name=name)
            for name, button in originals.items()
        }
        self.enterContext(patch.multiple(island_module, **self.buttons, create=True))
        self.pages = {
            name: TestPage(name, self.buttons[button])
            for name, button in (
                ('page_island', 'ISLAND_CHECK'),
                ('page_island_postmanage', 'ISLAND_POSTMANAGE_CHECK'),
                ('page_island_management', 'ISLAND_MANAGEMENT_CHECK'),
                ('page_island_warehouse', 'ISLAND_WAREHOUSE_CHECK'),
                ('page_island_mill', 'ISLAND_MILL_CHECK'),
                ('page_island_exchange_shop', 'ALAS_EXCHANGE_SHOP_CHECK'))
        }
        self.pages['page_island_postmanage'].links[self.pages['page_island_management']] = (
            self.buttons['ISLAND_POSTMANAGE_GOTO_MANAGEMENT'])
        self.enterContext(patch.multiple(island_module, **self.pages))
        self.fixtures = {
            name: utils.load_image(str(Path(__file__).parent / 'fixtures/island_mill_shop' / f'{name}.png'))
            for name in ('items', 'mill', 'transition')
        }
        self.empty = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.home = self.frame('ISLAND_CHECK', 'ALAS_EXCHANGE_GOTO_SHOP')
        self.tab = Button(area=(25, 385, 69, 405), color=(), button=(25, 385, 69, 405),
                          name='MILL_TAB_TEST')

    def frame(self, *names):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        for name in names:
            frame = np.maximum(frame, utils.load_image(self.buttons[name].file))
        return frame

    def flow(self, timeline):
        task = Island.__new__(Island)
        task.config = SimpleNamespace(SERVER='cn', BUTTON_OFFSET=(20, 20))
        task.device = FakeDevice(self.clock, timeline, max_screenshots=220)
        task.interval_timer = {}
        task.ui_goto = Mock()
        task.ui_additional = Mock(return_value=False)
        task.game_tips_appear = Mock(return_value=None)
        task.is_business_event_popup = Mock(return_value=False)
        task.handle_business_event_popup = Mock(return_value=False)
        task.handle_island_reward_popup = Mock(return_value=False)
        task._click_mill_shop_tab = Mock(side_effect=lambda: (task.device.click(self.tab) or True))
        for name in ('island_map_goto', 'island_up', 'island_left', 'island_down', 'island_right',
                     'set_buy_number', 'confirm_island_shop_purchase', 'select_character',
                     'confirm_post_add_order'):
            setattr(task, name, Mock(side_effect=AssertionError(f'磨坊导航不得调用 {name}')))
        return task

    def names(self, task):
        return [name for _, name in task.device.clicks]

    def test_real_mill_requires_title_and_process_tab(self):
        task = self.flow(lambda _: self.fixtures['mill'])
        self.assertTrue(task._mill_shop_ready())
        task.device.image = self.fixtures['items']
        self.assertFalse(task._mill_shop_ready())
        task.device.image = self.fixtures['transition']
        self.assertTrue(task.match_template_color(self.buttons['ISLAND_MILL_CHECK'], offset=(20, 20)))
        self.assertFalse(task._mill_shop_ready())

    def test_already_in_real_mill_waits_for_three_fresh_frames(self):
        task = self.flow(lambda _: self.fixtures['mill'])
        self.assertTrue(task.goto_mill())
        self.assertGreaterEqual(task.device.screenshot_count, 3)
        self.assertEqual(task.device.clicks, [])
        task.ui_goto.assert_not_called()
        task._click_mill_shop_tab.assert_not_called()

    def test_real_transition_title_does_not_complete_or_click_background(self):
        task = self.flow(lambda t: self.fixtures['transition'] if t < 2 else self.fixtures['mill'])
        self.assertTrue(task.goto_mill())
        self.assertGreaterEqual(self.clock.now - task.device.t0, 2.5)
        self.assertEqual(task.device.clicks, [])
        task.ui_goto.assert_not_called()

    def test_transient_ready_frame_resets_stability(self):
        def timeline(t):
            if t < 0.7 or t >= 2:
                return self.fixtures['mill']
            return self.fixtures['transition']

        task = self.flow(timeline)
        self.assertTrue(task.goto_mill())
        self.assertGreaterEqual(self.clock.now - task.device.t0, 2.5)

    def test_home_to_items_to_mill_never_uses_map_or_purchase(self):
        current = {'image': self.home}
        task = self.flow(lambda _: current['image'])
        click = task.device.click

        def advance(button):
            click(button)
            current['image'] = (self.fixtures['items'] if button.name == 'ALAS_EXCHANGE_GOTO_SHOP'
                                else self.fixtures['mill'])

        task.device.click = advance
        self.assertTrue(task.goto_mill())
        self.assertEqual(self.names(task), ['ALAS_EXCHANGE_GOTO_SHOP', 'MILL_TAB_TEST'])
        task.ui_goto.assert_not_called()

    def test_stuck_shop_tab_is_rate_limited_and_bounded(self):
        task = self.flow(lambda _: self.fixtures['items'])
        self.assertFalse(task.goto_mill())
        stamps = [stamp for stamp, _ in task.device.clicks]
        self.assertGreaterEqual(len(stamps), 2)
        self.assertLessEqual(len(stamps), 20)
        for before, after in zip(stamps, stamps[1:]):
            self.assertGreaterEqual(after - before, 2)
        self.assertEqual(set(self.names(task)), {'MILL_TAB_TEST'})

    def test_unknown_page_times_out_without_speculative_clicks(self):
        task = self.flow(lambda _: self.empty)
        self.assertFalse(task.goto_mill())
        self.assertEqual(task.device.clicks, [])
        task._click_mill_shop_tab.assert_not_called()

    def test_popup_blocks_ready_mill_even_if_handler_is_in_cooldown(self):
        task = self.flow(lambda _: self.fixtures['mill'])
        task.game_tips_appear.return_value = self.buttons['ISLAND_INFO_EXIT']
        self.assertFalse(task.goto_mill())
        self.assertEqual(task.device.clicks, [])
        task.ui_goto.assert_not_called()

    def test_quantity_dialog_does_not_count_as_ready_mill(self):
        # 数量弹窗会覆盖加工页签，不用逐像素取亮的叠图破坏弹窗模板。
        popup = self.frame('ISLAND_SHOPPING_CHECK')
        task = self.flow(lambda _: popup)
        task._mill_shop_ready = Mock(return_value=True)
        self.assertFalse(task.goto_mill())
        self.assertEqual(task.device.clicks, [])
        task.ui_goto.assert_not_called()
        task._mill_shop_ready.assert_not_called()

    def test_postmanage_return_waits_for_management_before_home_navigation(self):
        post = self.frame('ISLAND_POSTMANAGE_CHECK', 'POST_MANAGE_PRODUCTION',
                          'ISLAND_POSTMANAGE_GOTO_MANAGEMENT')
        management = self.frame('ISLAND_MANAGEMENT_CHECK')
        current = {'image': post, 'back_at': None}

        def timeline(t):
            if current['back_at'] is not None and t > current['back_at'] + 1:
                current['image'] = management
                current['back_at'] = None
            return current['image']

        task = self.flow(timeline)
        click = task.device.click

        def advance(button):
            click(button)
            if button.name == 'ISLAND_POSTMANAGE_GOTO_MANAGEMENT':
                current['image'] = self.empty
                current['back_at'] = task.device.clock.now - task.device.t0
            elif button.name == 'ALAS_EXCHANGE_GOTO_SHOP':
                current['image'] = self.fixtures['items']
            else:
                current['image'] = self.fixtures['mill']

        def navigate(destination, **kwargs):
            self.assertIs(destination, self.pages['page_island'])
            self.assertIs(task.device.image, management)
            current['image'] = self.home

        task.device.click = advance
        task.ui_goto.side_effect = navigate
        self.assertTrue(task.goto_mill())
        task.ui_goto.assert_called_once()
        self.assertEqual(self.names(task), ['ISLAND_POSTMANAGE_GOTO_MANAGEMENT',
                                           'ALAS_EXCHANGE_GOTO_SHOP', 'MILL_TAB_TEST'])

    def click_with_detections(self, rows, server='cn'):
        task = self.flow(lambda _: self.fixtures['items'])
        task.config.SERVER = server
        task.device.click = Mock(wraps=task.device.click)
        with patch('module.island.island.Ocr') as factory:
            factory.return_value.cnocr.det.return_value = rows
            result = Island._click_mill_shop_tab(task)
        return result, task

    def test_sidebar_cn_name_takes_priority_over_english_subtitle(self):
        result, task = self.click_with_detections([detection('磨坊'), detection('Mill', y=337)])
        self.assertTrue(result)
        self.assertEqual(len(task.device.clicks), 1)
        self.assertEqual(task.device.click.call_args.args[0].button, (25, 385, 69, 405))

    def test_sidebar_exact_english_name_and_subtitle_are_supported(self):
        for label in ('Flour Mill', 'Mill'):
            with self.subTest(label=label):
                result, task = self.click_with_detections([detection(label)], server='en')
                self.assertTrue(result)
                self.assertEqual(len(task.device.clicks), 1)

    def test_sidebar_uses_official_jp_and_tw_names_at_detected_positions(self):
        for server, label, y in (('jp', '製粉所', 430), ('tw', '磨坊', 250)):
            with self.subTest(server=server):
                result, task = self.click_with_detections([detection(label, y=y)], server=server)
                self.assertTrue(result)
                self.assertEqual(task.device.click.call_args.args[0].button, (25, y + 70, 69, y + 90))

    def test_sidebar_unknown_low_confidence_and_duplicate_names_are_rejected(self):
        for rows in ([], [detection('磨坊', score=0.89)], [detection('磨坊加工')],
                     [detection('磨坊'), detection('磨坊', y=420)],
                     [detection('Mill'), detection('Mill', y=420)]):
            with self.subTest(rows=rows):
                result, task = self.click_with_detections(rows)
                self.assertFalse(result)
                self.assertEqual(task.device.clicks, [])


if __name__ == '__main__':
    unittest.main()
