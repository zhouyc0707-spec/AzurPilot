"""工作啾体力降序首格选择的离线回归：真实资源、虚拟截图与时间线。"""

import unittest
from types import MethodType
from unittest.mock import Mock, patch

import numpy as np

import module.base.timer as timer_module
from module.base.utils import load_image
from module.island.assets import ISLAND_SELECT_CHARACTER_CHECK, SELECT_UI_CONFIRM
from module.island.island_select_character import SelectCharacter, UI, _match_sort_arrow
from module.island_select_character.assets import (
    SELECT_CHARACTER_FILTER,
    SELECT_CHARACTER_FILTER_CONFIRM,
    SELECT_CHARACTER_FILTER_STAMINA,
    SELECT_CHARACTER_SORT_ASC,
    SELECT_CHARACTER_SORT_CLICK,
    SELECT_CHARACTER_SORT_DESC,
    TEMPLATE_CHARACTER_WORKING,
    TEMPLATE_NEWJERSEY,
    TEMPLATE_WORKERJUU,
)
from tests.test_island_character_confirm_helpers import (
    FakeClock,
    FakeDevice,
    StubIsland,
    build_frame,
)


CHARACTER_PAGE = build_frame(
    ISLAND_SELECT_CHARACTER_CHECK, SELECT_UI_CONFIRM, SELECT_CHARACTER_FILTER,
)
FILTER_POPUP = build_frame(SELECT_CHARACTER_FILTER_CONFIRM, SELECT_CHARACTER_FILTER_STAMINA)


def character_frame(arrow=None, arrow_shift=(0, 0), characters=()):
    """用已存在的资源模拟角色页；头像元组为（列、角色名、是否工作中）。"""
    frame = CHARACTER_PAGE.copy()
    if arrow is not None:
        arrow_image = load_image(arrow.file)
        dx, dy = arrow_shift
        frame = np.maximum(frame, np.roll(arrow_image, (dy, dx), axis=(0, 1)))
    for column, name, working in characters:
        x, y = 58 + 140 * column, 112
        template = TEMPLATE_WORKERJUU if name == "WorkerJuu" else TEMPLATE_NEWJERSEY
        portrait = load_image(template.file)
        height, width = portrait.shape[:2]
        frame[y + 45:y + 45 + height, x + 33:x + 33 + width] = portrait
        if working:
            mark = load_image(TEMPLATE_CHARACTER_WORKING.file)
            height, width = mark.shape[:2]
            frame[y + 94:y + 94 + height, x + 18:x + 18 + width] = mark
    return frame


class SelectionDevice(FakeDevice):
    """模拟筛选弹窗与延迟生效的排序箭头。"""

    def __init__(self, clock):
        self.ascending = False
        self.popup = False
        self.stamina = False
        self.pending_sort = None
        self.sort_ready_at = None
        super().__init__(clock, self.frame, screenshot_cost=0.2, click_cost=0.05)

    def frame(self, _):
        if self.pending_sort is not None and self.clock.now >= self.sort_ready_at:
            self.ascending = self.pending_sort
            self.pending_sort = None
        if self.popup:
            return FILTER_POPUP
        characters = [(0, "WorkerJuu", False)] if self.stamina and not self.ascending else []
        return character_frame(
            SELECT_CHARACTER_SORT_ASC if self.ascending else SELECT_CHARACTER_SORT_DESC,
            characters=characters,
        )

    def click(self, button, control_check=True):
        super().click(button, control_check=control_check)
        if button.name == SELECT_CHARACTER_SORT_CLICK.name:
            self.pending_sort = not self.ascending
            self.sort_ready_at = self.clock.now + 0.6
        elif button.name == SELECT_CHARACTER_FILTER.name:
            self.popup = True
        elif button.name == SELECT_CHARACTER_FILTER_STAMINA.name:
            self.stamina = True
        elif button.name == SELECT_CHARACTER_FILTER_CONFIRM.name:
            self.popup = False


class FirstSlotSelectionDevice(SelectionDevice):
    """点击首格后显示真实检测区域的选中颜色，不模拟头像识别。"""

    def __init__(self, clock, *, selected=False, accept_click=True):
        self.selected = selected
        self.accept_click = accept_click
        super().__init__(clock)

    def frame(self, elapsed):
        frame = super().frame(elapsed).copy()
        if self.selected and not self.popup:
            frame[112 + 26:112 + 42, 58 + 86:58 + 119] = (19, 182, 234)
        return frame

    def click(self, button, control_check=True):
        super().click(button, control_check=control_check)
        if button.name == 'SELECT_CHARACTER_GRID_0_0' and self.accept_click:
            self.selected = not self.selected


class WorkerJuuSelectionTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.time_patch = patch.object(timer_module, "time", lambda: self.clock.now)
        self.time_patch.start()
        self.addCleanup(self.time_patch.stop)

    def selector(self, device):
        # 保留真实识别器、网格与业务方法，只隔离设备连接和真实游戏交互。
        with patch.object(UI, "__init__", return_value=None):
            selector = SelectCharacter()
        selector.device = device
        selector.config = None
        selector.interval_timer = {}
        for name in ("loop", "appear", "appear_then_click", "interval_clear"):
            setattr(selector, name, MethodType(getattr(StubIsland, name), selector))
        selector._get_stamina_value = Mock(return_value=40)
        return selector

    def make_selector(self, frame):
        device = FakeDevice(self.clock, lambda _: frame, screenshot_cost=0.2, click_cost=0.05)
        return self.selector(device), device

    def test_ascending_arrow_is_confirmed_without_click(self):
        selector, device = self.make_selector(character_frame(SELECT_CHARACTER_SORT_ASC))
        self.assertTrue(selector._set_character_sort(ascending=True))
        self.assertEqual(device.clicks, [])

    def test_descending_arrow_is_confirmed_without_click(self):
        selector, device = self.make_selector(character_frame(SELECT_CHARACTER_SORT_DESC))
        self.assertTrue(selector._set_character_sort(ascending=False))
        self.assertEqual(device.clicks, [])

    def test_arrow_matching_uses_strict_template_matching_with_offset(self):
        frame = character_frame(SELECT_CHARACTER_SORT_ASC, arrow_shift=(8, 2))
        selector, device = self.make_selector(
            frame,
        )
        self.assertTrue(_match_sort_arrow(frame, SELECT_CHARACTER_SORT_ASC))
        self.assertFalse(_match_sort_arrow(frame, SELECT_CHARACTER_SORT_DESC))
        self.assertTrue(selector._set_character_sort(ascending=True))
        self.assertEqual(device.clicks, [])

    def test_sort_arrow_threshold_is_not_lowered_for_high_resolution(self):
        frame = character_frame(SELECT_CHARACTER_SORT_ASC)
        with patch("module.island.island_select_character.cv2.matchTemplate") as matching:
            matching.return_value = np.asarray([[0.89]], dtype=np.float32)
            self.assertFalse(_match_sort_arrow(frame, SELECT_CHARACTER_SORT_ASC))
            matching.return_value = np.asarray([[0.91]], dtype=np.float32)
            self.assertTrue(_match_sort_arrow(frame, SELECT_CHARACTER_SORT_ASC))

    def test_short_lived_old_descending_frame_does_not_trigger_second_click(self):
        device = SelectionDevice(self.clock)
        selector = self.selector(device)
        self.assertTrue(selector._set_character_sort(ascending=True))
        self.assertEqual([name for _, name, _ in device.clicks], [SELECT_CHARACTER_SORT_CLICK.name])
        self.assertTrue(device.ascending)

    def test_failed_sort_click_is_retried_no_faster_than_two_seconds(self):
        selector, device = self.make_selector(character_frame(SELECT_CHARACTER_SORT_DESC))
        self.assertFalse(selector._set_character_sort(ascending=True))
        stamps = [stamp for stamp, name, _ in device.clicks if name == SELECT_CHARACTER_SORT_CLICK.name]
        self.assertGreaterEqual(len(stamps), 2)
        for previous, current in zip(stamps, stamps[1:]):
            self.assertGreaterEqual(current - previous, 2)

    def test_unknown_arrow_never_causes_sort_click(self):
        selector, device = self.make_selector(character_frame())
        self.assertFalse(selector._set_character_sort(ascending=True))
        self.assertEqual(device.clicks, [])

    def test_filter_popup_blocks_sort_click_even_with_background_arrow(self):
        frame = np.maximum(character_frame(SELECT_CHARACTER_SORT_DESC), FILTER_POPUP)
        selector, device = self.make_selector(frame)
        self.assertFalse(selector._set_character_sort(ascending=True))
        self.assertEqual(device.clicks, [])

    def test_arrow_without_character_page_does_not_cause_click(self):
        selector, device = self.make_selector(load_image(SELECT_CHARACTER_SORT_DESC.file))
        self.assertFalse(selector._set_character_sort(ascending=True))
        self.assertEqual(device.clicks, [])

    def test_waits_for_character_title_before_recognizing_arrow(self):
        ready = character_frame(SELECT_CHARACTER_SORT_ASC)
        device = FakeDevice(
            self.clock, lambda t: np.zeros_like(ready) if t < 0.8 else ready,
            screenshot_cost=0.2,
        )
        selector = self.selector(device)
        self.assertTrue(selector._set_character_sort(ascending=True))
        self.assertEqual(device.clicks, [])

    def test_waits_for_arrow_that_renders_after_character_title(self):
        ready = character_frame(SELECT_CHARACTER_SORT_ASC)
        device = FakeDevice(
            self.clock, lambda t: CHARACTER_PAGE if t < 0.8 else ready,
            screenshot_cost=0.2,
        )
        selector = self.selector(device)
        self.assertTrue(selector._set_character_sort(ascending=True))
        self.assertEqual(device.clicks, [])

    def test_worker_juu_uses_first_slot_without_avatar_or_stamina_recognition(self):
        selector, device = self.make_selector(character_frame(SELECT_CHARACTER_SORT_DESC))
        selector.select_character_filter = Mock(return_value=True)
        selector.recognize_target_characters = Mock(side_effect=AssertionError('不应遍历头像'))
        selector._recognize_character_status = Mock(side_effect=AssertionError('不应识别角色状态'))
        self.assertEqual(selector._select_first_available_character(["WorkerJuu"]), (0, 0))
        self.assertEqual(device.clicks, [])
        selector.select_character_filter.assert_called_once_with()
        selector.recognize_target_characters.assert_not_called()
        selector._get_stamina_value.assert_not_called()

    def test_worker_juu_filter_failure_stops_before_sort_or_selection(self):
        selector, device = self.make_selector(character_frame(SELECT_CHARACTER_SORT_DESC))
        selector.select_character_filter = Mock(return_value=False)
        selector._set_character_sort = Mock()
        self.assertFalse(selector.select_character('WorkerJuu'))
        selector._set_character_sort.assert_not_called()
        self.assertEqual(device.clicks, [])
        selector._get_stamina_value.assert_not_called()

    def test_worker_juu_unknown_descending_arrow_never_selects_first_slot(self):
        selector, device = self.make_selector(character_frame())
        selector.select_character_filter = Mock(return_value=True)
        self.assertFalse(selector.select_character('WorkerJuu'))
        self.assertEqual(device.clicks, [])
        selector._get_stamina_value.assert_not_called()

    def test_worker_juu_popup_blocks_first_slot_even_when_descending_arrow_is_visible(self):
        frame = np.maximum(character_frame(SELECT_CHARACTER_SORT_DESC), FILTER_POPUP)
        selector, device = self.make_selector(frame)
        selector.select_character_filter = Mock(return_value=True)
        self.assertFalse(selector.select_character('WorkerJuu'))
        self.assertEqual(device.clicks, [])

    def test_worker_juu_identity_has_unlimited_stamina_without_ocr(self):
        frame = character_frame(characters=[(0, "WorkerJuu", False)])
        selector, _ = self.make_selector(frame)
        status = selector._recognize_character_status(frame, selector.select_character_grid[0, 0])
        self.assertEqual(status["character_name"], "WorkerJuu")
        self.assertTrue(status["has_stamina"])
        self.assertEqual(status["stamina"], 999)
        selector._get_stamina_value.assert_not_called()

    def test_other_character_still_uses_ocr_and_stamina_threshold(self):
        frame = character_frame(characters=[(0, "NewJersey", False)])
        selector, _ = self.make_selector(frame)
        selector._get_stamina_value.return_value = 34
        button = selector.select_character_grid[0, 0]
        status = selector._recognize_character_status(frame, button)
        self.assertEqual(status["character_name"], "NewJersey")
        self.assertFalse(status["has_stamina"])
        self.assertEqual(status["stamina"], 34)
        selector._get_stamina_value.assert_called_once()

    def test_stamina_filter_then_existing_descending_needs_no_sort_click(self):
        device = SelectionDevice(self.clock)
        selector = self.selector(device)
        self.assertEqual(selector._select_first_available_character(["WorkerJuu"]), (0, 0))
        self.assertEqual([name for _, name, _ in device.clicks], [
            SELECT_CHARACTER_FILTER.name,
            SELECT_CHARACTER_FILTER_STAMINA.name,
            SELECT_CHARACTER_FILTER_CONFIRM.name,
        ])
        self.assertTrue(device.stamina)
        self.assertFalse(device.ascending)
        selector._get_stamina_value.assert_not_called()

    def test_ascending_list_filters_then_switches_to_descending_once(self):
        device = SelectionDevice(self.clock)
        device.ascending = True
        device.image = device.frame(0)
        selector = self.selector(device)
        self.assertEqual(selector._select_first_available_character(["WorkerJuu"]), (0, 0))
        self.assertEqual([name for _, name, _ in device.clicks], [
            SELECT_CHARACTER_FILTER.name,
            SELECT_CHARACTER_FILTER_STAMINA.name,
            SELECT_CHARACTER_FILTER_CONFIRM.name,
            SELECT_CHARACTER_SORT_CLICK.name,
        ])
        self.assertFalse(device.ascending)
        selector._get_stamina_value.assert_not_called()

    def test_mixed_priority_keeps_named_character_before_visible_worker(self):
        selector, device = self.make_selector(character_frame(
            SELECT_CHARACTER_SORT_DESC,
            characters=[(0, "WorkerJuu", False), (1, "NewJersey", False)],
        ))
        selector.select_character_filter = Mock()
        self.assertEqual(
            selector._select_first_available_character(["NewJersey", "WorkerJuu"]), (1, 0),
        )
        self.assertEqual(device.clicks, [])
        selector.select_character_filter.assert_not_called()
        selector._get_stamina_value.assert_called_once()

    def test_mixed_priority_skips_named_character_with_low_stamina(self):
        selector, _ = self.make_selector(character_frame(
            SELECT_CHARACTER_SORT_DESC,
            characters=[(0, "WorkerJuu", False), (1, "NewJersey", False)],
        ))
        selector._get_stamina_value.return_value = 1
        self.assertEqual(
            selector._select_first_available_character(["NewJersey", "WorkerJuu"]), (0, 0),
        )
        selector._get_stamina_value.assert_called_once()

    def test_mixed_priority_restores_descending_before_reading_named_characters(self):
        device = SelectionDevice(self.clock)
        device.ascending = True
        device.image = device.frame(0)
        original_frame = device.frame

        def after_sort_frame(elapsed):
            frame = original_frame(elapsed)
            if not device.ascending:
                return character_frame(SELECT_CHARACTER_SORT_DESC, characters=[(1, "NewJersey", False)])
            return frame

        device.timeline = after_sort_frame
        selector = self.selector(device)
        self.assertEqual(
            selector._select_first_available_character(["NewJersey", "WorkerJuu"]), (1, 0),
        )
        self.assertFalse(device.ascending)
        self.assertEqual([name for _, name, _ in device.clicks], [SELECT_CHARACTER_SORT_CLICK.name])

    def test_first_slot_clicks_once_and_requires_selected_marker(self):
        device = FirstSlotSelectionDevice(self.clock)
        selector = self.selector(device)
        selector.recognize_target_characters = Mock(side_effect=AssertionError('不应识别头像'))
        self.assertTrue(selector.select_character('WorkerJuu'))
        clicks = [(name, point) for _, name, point in device.clicks if name.startswith('SELECT_CHARACTER_GRID')]
        self.assertEqual(len(clicks), 1)
        self.assertEqual(clicks[0][0], 'SELECT_CHARACTER_GRID_0_0')
        x, y = clicks[0][1]
        self.assertTrue(58 <= x <= 178 and 112 <= y <= 272)
        self.assertTrue(device.selected)
        selector._get_stamina_value.assert_not_called()

    def test_already_selected_first_slot_is_not_toggled_off(self):
        device = FirstSlotSelectionDevice(self.clock, selected=True)
        selector = self.selector(device)
        self.assertTrue(selector.select_character('WorkerJuu'))
        self.assertFalse(any(name.startswith('SELECT_CHARACTER_GRID') for _, name, _ in device.clicks))
        self.assertTrue(device.selected)

    def test_unselected_first_slot_never_reports_success(self):
        device = FirstSlotSelectionDevice(self.clock, accept_click=False)
        selector = self.selector(device)
        self.assertFalse(selector.select_character('WorkerJuu'))
        self.assertEqual([name for _, name, _ in device.clicks if name.startswith('SELECT_CHARACTER_GRID')],
                         ['SELECT_CHARACTER_GRID_0_0'] * 5)
        self.assertFalse(device.selected)

    def test_special_character_scroll_search_restores_descending(self):
        """咖啡特殊角色的有界搜索仍会将角色页已有升序恢复为降序。"""
        device = SelectionDevice(self.clock)
        device.ascending = True
        original_frame = device.frame

        def frame(elapsed):
            original_frame(elapsed)
            return character_frame(
                SELECT_CHARACTER_SORT_ASC if device.ascending else SELECT_CHARACTER_SORT_DESC,
                characters=[(1, "NewJersey", False)],
            )

        device.timeline = frame
        selector = self.selector(device)
        selector._get_stamina_value.return_value = 60
        selector._swipe_character_list_to_top = Mock()
        result = selector.find_strict_available_character_with_scroll("NewJersey")
        self.assertEqual(result["character_name"], "NewJersey")
        self.assertFalse(device.ascending)
        self.assertEqual([name for _, name, _ in device.clicks], [SELECT_CHARACTER_SORT_CLICK.name])

    def test_daily_gather_strict_lookup_keeps_its_own_ascending_order(self):
        """每日采集不滚动的指定角色入口保留生活等级升序。"""
        selector, device = self.make_selector(character_frame(
            SELECT_CHARACTER_SORT_ASC, characters=[(1, "NewJersey", False)],
        ))
        selector._set_character_sort = Mock()
        result = selector.find_strict_available_character("NewJersey", min_stamina=35)
        self.assertEqual(result["character_name"], "NewJersey")
        selector._set_character_sort.assert_not_called()
        self.assertEqual(device.clicks, [])


if __name__ == "__main__":
    unittest.main()
