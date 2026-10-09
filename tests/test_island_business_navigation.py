"""用真实店名和角色模板验证经营列表重排、当前视野继续及回顶兜底。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from module.base.utils import load_image
from module.island.island_business import IslandBusiness
from module.island_select_character.assets import TEMPLATE_EUGEN, TEMPLATE_HELENA


FISH = {'name': '有鱼餐馆', 'config_key': '1'}
TEA = {'name': '白熊饮品', 'config_key': '2'}
COFFEE = {'name': '啾咖啡', 'config_key': '5'}
STATUS_COLORS = {
    'blue': (82, 197, 255),
    'yellow': (230, 192, 71),
    'darkblue': (60, 67, 84),
    'gray': (190, 190, 190),
}


def list_frame(rows):
    """在不同位置摆放真实列表店名，经营按钮使用其真实状态色。"""
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    for shop, status, y in rows:
        image = load_image(IslandBusiness.SHOP_LIST_TEMPLATE_MAP[shop['name']].file)
        h, w = image.shape[:2]
        frame[y:y + h, 340:340 + w] = image
        x1, x2 = IslandBusiness._SHOP_BUTTON_X_RANGE
        by = y + IslandBusiness._SHOP_LABEL_TO_BUTTON_OFFSET_Y
        frame[by:by + IslandBusiness._SHOP_BUTTON_HEIGHT, x1:x2] = STATUS_COLORS[status]
    return frame


def character_frame(template=None, position=(250, 180)):
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    if template is not None:
        image = load_image(template.file)
        x, y = position
        h, w = image.shape[:2]
        frame[y:y + h, x:x + w] = image
    return frame


class ResourceDevice:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.image = list_frame(rows)
        self.screenshots = 0
        self.clicks = []
        self.swipes = []
        self.last_shop = None
        self.role_frame = None
        self.top_role_frame = None

    def screenshot(self):
        self.screenshots += 1
        self.image = self.role_frame if self.role_frame is not None else list_frame(self.rows)
        return self.image

    def click(self, button, **kwargs):
        self.clicks.append(button.area)
        for shop, _, y in self.rows:
            by = y + IslandBusiness._SHOP_LABEL_TO_BUTTON_OFFSET_Y
            if button.area == (1020, by, 1154, by + 27):
                self.last_shop = shop

    def swipe_vector(self, vector, box=None, name=None, **kwargs):
        self.swipes.append(name)
        if name == 'BusinessCharSwipeReset' and self.top_role_frame is not None:
            self.role_frame = self.top_role_frame

    def sleep(self, seconds):
        pass


def business_with(device):
    business = IslandBusiness.__new__(IslandBusiness)
    business.device = device
    business.shops = [FISH, TEA, COFFEE]
    business.config = SimpleNamespace(task_delay=Mock())
    business._get_batch2_shops = Mock(return_value=[])
    business._set_task_delay = Mock()
    business._detect_current_shop = Mock(side_effect=lambda: device.last_shop)
    business.post_manage_mode = Mock()
    business._ocr_and_delay_business_remain = Mock()
    return business


class BusinessListNavigationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.island.island_business.logger'))

    def test_return_refreshes_reordered_positions_before_next_click(self):
        device = ResourceDevice([(FISH, 'blue', 160), (TEA, 'blue', 320)])
        business = business_with(device)
        started = []

        def start_shop(shop):
            started.append(shop['name'])
            if shop == FISH:
                # 开业后店铺移位；第二家不再沿用原 y=320 的旧坐标。
                device.rows = [(TEA, 'blue', 220), (FISH, 'darkblue', 380)]
            else:
                device.rows = [(FISH, 'darkblue', 160), (TEA, 'darkblue', 320)]

        business._process_shop_entry = start_shop
        result = business._run_batch([FISH, TEA])

        self.assertEqual(result, {FISH['name'], TEA['name']})
        self.assertEqual(started, [FISH['name'], TEA['name']])
        self.assertEqual(device.clicks, [(1020, 248, 1154, 275), (1020, 308, 1154, 335)])
        # 入口回顶和所有待办完成后的兜底回顶；两家之间不再回顶。
        self.assertEqual(device.swipes, ['BusinessListSwipeTop', 'BusinessListSwipeTop'])
        business._set_task_delay.assert_called_once()

    def test_started_shop_with_delayed_blue_state_is_not_entered_twice(self):
        device = ResourceDevice([(FISH, 'blue', 160), (TEA, 'blue', 320)])
        business = business_with(device)
        processed = []

        def start_shop(shop):
            processed.append(shop['name'])
            # 第一次开业后按钮仍短暂呈蓝色，去重不能被当前位置优化绕开。
            if shop == TEA:
                device.rows = [(FISH, 'blue', 160), (TEA, 'darkblue', 320)]

        business._process_shop_entry = start_shop
        business._run_batch([FISH, TEA])
        self.assertEqual(processed, [FISH['name'], TEA['name']])
        self.assertEqual(len(device.clicks), 2)

    def test_claimed_yellow_is_skipped_while_other_visible_shop_continues(self):
        device = ResourceDevice([(FISH, 'yellow', 160), (TEA, 'blue', 320)])
        business = business_with(device)
        business._claim_business_reward = Mock()

        def start_shop(shop):
            device.rows = [(FISH, 'yellow', 160), (TEA, 'darkblue', 320)]

        business._process_shop_entry = Mock(side_effect=start_shop)
        result = business._run_batch([FISH, TEA])
        self.assertEqual(result, {TEA['name']})
        business._claim_business_reward.assert_called_once_with(button_already_clicked=True)
        business._process_shop_entry.assert_called_once_with(TEA)
        self.assertEqual(len(device.clicks), 2)

    def test_current_view_is_fresh_and_does_not_include_other_batch(self):
        device = ResourceDevice([(FISH, 'blue', 160)])
        business = business_with(device)
        device.rows = [(TEA, 'blue', 220), (COFFEE, 'blue', 380)]
        visible = business._batch_view_after_return([FISH, TEA], {FISH['name']}, set())
        self.assertEqual([info['shop'] for info in visible], [TEA])
        self.assertEqual(visible[0]['button_rect'], (1020, 308, 1154, 335))
        self.assertEqual(device.screenshots, 1)
        self.assertEqual(device.swipes, [])

    def test_no_visible_pending_shop_restores_original_top_search(self):
        for rows in ([], [(FISH, 'darkblue', 160)], [(FISH, 'yellow', 160)]):
            with self.subTest(rows=rows):
                device = ResourceDevice(rows)
                business = business_with(device)
                visible = business._batch_view_after_return(
                    [FISH, TEA], set(), {FISH['name']})
                self.assertIsNone(visible)
                self.assertEqual(device.swipes, ['BusinessListSwipeTop'])

    def test_visible_target_does_not_skip_remaining_shop_outside_view(self):
        device = ResourceDevice([(TEA, 'blue', 220)])
        business = business_with(device)
        self.assertIsNone(business._batch_view_after_return([FISH, TEA], set(), set()))
        self.assertEqual(device.swipes, ['BusinessListSwipeTop'])


class BusinessCharacterNavigationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.island.island_business.logger'))

    def make_business(self, device):
        business = business_with(device)
        business.character_priority = ['Helena', 'Eugen']
        business.character_templates = {'Helena': TEMPLATE_HELENA, 'Eugen': TEMPLATE_EUGEN}
        business.select_character_filter = Mock(return_value=True)
        business.selected_area_relative = (86, 26, 119, 42)
        return business

    def test_visible_configured_character_is_selected_without_top_swipe(self):
        device = ResourceDevice()
        # 初始画面是旧帧；只有重新截图才能看到当前角色位置。
        device.role_frame = character_frame(TEMPLATE_EUGEN, (500, 260))
        business = self.make_business(device)
        business.character_priority = ['Eugen']
        self.assertEqual(business._find_and_select_character(), 'Eugen')
        self.assertEqual(device.swipes, [])
        # 当前视野定位后，头像只点击一次，并用新截图复核选中态。
        self.assertEqual(device.screenshots, 2)
        self.assertEqual(device.clicks[0][:2], (500, 260))
        business.select_character_filter.assert_not_called()

    def test_current_view_selected_character_is_not_toggled_off(self):
        device = ResourceDevice()
        device.role_frame = character_frame(TEMPLATE_EUGEN, (500, 260))
        # 头像坐标为 (500,260)，真实单元锚点偏移与蓝色选中区域沿用角色模块。
        device.role_frame[241:257, 553:586] = (19, 182, 234)
        business = self.make_business(device)
        business.character_priority = ['Eugen']
        self.assertEqual(business._find_and_select_character(), 'Eugen')
        self.assertEqual(device.clicks, [])
        self.assertEqual(device.swipes, [])
        self.assertEqual(device.screenshots, 1)

    def test_missing_selection_marker_does_not_repeat_toggle_click(self):
        device = ResourceDevice()
        device.role_frame = character_frame(TEMPLATE_EUGEN, (500, 260))
        business = self.make_business(device)
        business.character_priority = ['Eugen']
        self.assertEqual(business._find_and_select_character(), 'Eugen')
        self.assertEqual(len(device.clicks), 1)
        self.assertEqual(device.clicks[0][:2], (500, 260))

    def test_single_configured_character_does_not_probe_second_slot(self):
        from module.island_business.assets import BUSINESS_PLUS_A
        device = ResourceDevice()
        business = self.make_business(device)
        business.character_priority = ['Eugen']
        business._appear_at_positions = Mock(return_value=None)
        business._get_review_button = Mock(return_value=None)
        business._select_business_characters()
        self.assertTrue(business._appear_at_positions.called)
        for call in business._appear_at_positions.call_args_list:
            self.assertIs(call.args[0], BUSINESS_PLUS_A)

    def test_only_lower_candidate_visible_keeps_original_top_search(self):
        device = ResourceDevice()
        device.role_frame = character_frame(TEMPLATE_EUGEN, (500, 260))
        device.top_role_frame = character_frame(TEMPLATE_HELENA)
        business = self.make_business(device)
        self.assertEqual(business._find_and_select_character(), 'Helena')
        self.assertEqual(device.swipes, ['BusinessCharSwipeReset'])
        business.select_character_filter.assert_not_called()

    def test_missing_current_character_falls_back_to_top_search(self):
        device = ResourceDevice()
        device.role_frame = character_frame()
        device.top_role_frame = character_frame(TEMPLATE_HELENA)
        business = self.make_business(device)
        self.assertEqual(business._find_and_select_character(), 'Helena')
        self.assertEqual(device.swipes, ['BusinessCharSwipeReset'])
        business.select_character_filter.assert_not_called()

    def test_unknown_character_page_keeps_bounded_sorting_fallback(self):
        device = ResourceDevice()
        device.role_frame = character_frame()
        device.top_role_frame = character_frame()
        business = self.make_business(device)
        self.assertFalse(business._find_and_select_character())
        self.assertEqual(device.swipes.count('BusinessCharSwipeReset'), 2)
        self.assertEqual(device.swipes.count('BusinessCharSwipe'), 10)
        business.select_character_filter.assert_called_once()


if __name__ == '__main__':
    unittest.main()
