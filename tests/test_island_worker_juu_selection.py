"""工作啾按身份选择的离线回归：真实资源、虚拟截图与时间线。"""

import unittest
from itertools import cycle
from pathlib import Path
from types import MethodType
from unittest.mock import Mock, patch

import numpy as np

import module.base.timer as timer_module
from module.base.utils import load_image, random_rectangle_point
from module.island.assets import ISLAND_SELECT_CHARACTER_CHECK, SELECT_UI_CONFIRM
from module.island.island_select_character import SelectCharacter, UI, _match_sort_arrow
from module.island_select_character.assets import (
    SELECT_CHARACTER_FILTER,
    SELECT_CHARACTER_FILTER_CONFIRM,
    SELECT_CHARACTER_FILTER_STAMINA,
    SELECT_CHARACTER_SORT_ASC,
    SELECT_CHARACTER_SORT_CLICK,
    SELECT_CHARACTER_SORT_DESC,
    TEMPLATE_CHAO_HO,
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
WORKER_FIXTURES = Path(__file__).parent / 'fixtures/island_worker_selection'


def character_frame(arrow=None, arrow_shift=(0, 0), characters=(), selected=()):
    """用已有资源模拟角色页；位置可用首行列号或（列、行）表示。"""
    frame = CHARACTER_PAGE.copy()
    if arrow is not None:
        arrow_image = load_image(arrow.file)
        dx, dy = arrow_shift
        frame = np.maximum(frame, np.roll(arrow_image, (dy, dx), axis=(0, 1)))
    for position, name, working in characters:
        column, row = position if isinstance(position, tuple) else (position, 0)
        x, y = 58 + 140 * column, 112 + 180 * row
        template = {
            "WorkerJuu": TEMPLATE_WORKERJUU,
            "NewJersey": TEMPLATE_NEWJERSEY,
            "ChaoHo": TEMPLATE_CHAO_HO,
        }[name]
        portrait = load_image(template.file)
        height, width = portrait.shape[:2]
        frame[y + 45:y + 45 + height, x + 33:x + 33 + width] = portrait
        if working:
            mark = load_image(TEMPLATE_CHARACTER_WORKING.file)
            height, width = mark.shape[:2]
            frame[y + 94:y + 94 + height, x + 18:x + 18 + width] = mark
    for position in selected:
        column, row = position if isinstance(position, tuple) else (position, 0)
        x, y = 58 + 140 * column, 112 + 180 * row
        frame[y + 26:y + 42, x + 86:x + 119] = (19, 182, 234)
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
        characters = [(0, "WorkerJuu", False)] if self.ascending else []
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


class CharacterSelectionDevice(FakeDevice):
    """头像与选中状态分别渲染，按实际随机坐标判断角色卡是否命中。

    使用既有截图的白卡框模拟命中，不将修复后的安全内框当成游戏热区边界。
    """

    def __init__(self, clock, *, characters=None, selected=(), accept_click=True):
        self.characters = characters if characters is not None else [(0, "WorkerJuu", False)]
        self.ascending = True
        self.selected = set(selected)
        self.accept_click = accept_click
        self.after_click = None
        self.selected_frames = 0
        super().__init__(clock, self.frame, screenshot_cost=0.2, click_cost=0.05)

    def frame(self, elapsed):
        if any(name == 'WorkerJuu' and column in self.selected
               for column, name, _ in self.characters):
            self.selected_frames += 1
        return character_frame(
            SELECT_CHARACTER_SORT_ASC if self.ascending else SELECT_CHARACTER_SORT_DESC,
            characters=self.characters, selected=self.selected,
        )

    def click(self, button, control_check=True):
        super().click(button, control_check=control_check)
        if button.name == SELECT_CHARACTER_SORT_CLICK.name:
            self.ascending = not self.ascending
        if button.name.startswith('SELECT_CHARACTER_GRID_'):
            column, row = map(int, button.name.split('_')[-2:])
            position = (column, row) if row else column
            x, y = self.clicks[-1][2]
            left, top = 56 + 140 * column, 139 + 180 * row
            if self.accept_click and left <= x <= left + 124 and top <= y <= top + 164:
                self.selected = set() if position in self.selected else {position}
            if self.after_click is not None:
                self.after_click(position)


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

    def test_worker_juu_returns_verified_position_without_stamina_ocr(self):
        selector, device = self.make_selector(character_frame(
            SELECT_CHARACTER_SORT_ASC,
            characters=[(0, 'ChaoHo', False), (2, 'WorkerJuu', False)],
            selected=[0],
        ))
        selector.select_character_filter = Mock(return_value=True)
        self.assertEqual(selector._select_first_available_character(["WorkerJuu"]), (2, 0))
        self.assertEqual(device.clicks, [])
        selector.select_character_filter.assert_not_called()
        selector._get_stamina_value.assert_not_called()

    def test_worker_juu_selection_does_not_depend_on_filter_availability(self):
        device = CharacterSelectionDevice(self.clock)
        selector = self.selector(device)
        selector.select_character_filter = Mock(return_value=False)
        self.assertTrue(selector.select_character('WorkerJuu'))
        selector.select_character_filter.assert_not_called()
        self.assertEqual([name for _, name, _ in device.clicks], ['SELECT_CHARACTER_GRID_0_0'])
        selector._get_stamina_value.assert_not_called()

    def test_worker_juu_unknown_arrow_never_selects_first_slot(self):
        selector, device = self.make_selector(character_frame())
        selector.select_character_filter = Mock(return_value=True)
        self.assertFalse(selector.select_character('WorkerJuu'))
        self.assertEqual(device.clicks, [])
        selector._get_stamina_value.assert_not_called()

    def test_worker_juu_popup_blocks_selection_even_when_ascending_arrow_is_visible(self):
        frame = np.maximum(character_frame(SELECT_CHARACTER_SORT_ASC), FILTER_POPUP)
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

    def test_existing_ascending_needs_no_sort_or_filter_click(self):
        device = SelectionDevice(self.clock)
        device.ascending = True
        device.image = device.frame(0)
        selector = self.selector(device)
        selector.select_character_filter = Mock(side_effect=AssertionError('工作啾不应打开筛选'))
        self.assertEqual(selector._select_first_available_character(["WorkerJuu"]), (0, 0))
        self.assertEqual(device.clicks, [])
        self.assertFalse(device.stamina)
        self.assertTrue(device.ascending)
        selector._get_stamina_value.assert_not_called()

    def test_descending_list_switches_to_ascending_once_without_filter(self):
        device = SelectionDevice(self.clock)
        selector = self.selector(device)
        selector.select_character_filter = Mock(side_effect=AssertionError('工作啾不应打开筛选'))
        self.assertEqual(selector._select_first_available_character(["WorkerJuu"]), (0, 0))
        self.assertEqual([name for _, name, _ in device.clicks], [
            SELECT_CHARACTER_SORT_CLICK.name,
        ])
        self.assertTrue(device.ascending)
        self.assertFalse(device.stamina)
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

    def test_verified_worker_clicks_once_and_requires_two_selected_frames(self):
        device = CharacterSelectionDevice(self.clock)
        selector = self.selector(device)
        selector.select_character_filter = Mock(return_value=True)
        device.sleep = Mock(side_effect=AssertionError('工作啾选人不应使用固定等待'))
        self.assertTrue(selector.select_character('WorkerJuu'))
        clicks = [(name, point) for _, name, point in device.clicks if name.startswith('SELECT_CHARACTER_GRID')]
        self.assertEqual(len(clicks), 1)
        self.assertEqual(clicks[0][0], 'SELECT_CHARACTER_GRID_0_0')
        x, y = clicks[0][1]
        self.assertTrue(78 <= x <= 158 and 182 <= y <= 262)
        self.assertEqual(device.selected, {0})
        self.assertGreaterEqual(device.selected_frames, 2)
        selector._get_stamina_value.assert_not_called()

    def test_worker_safe_click_uses_real_random_extremes_and_normalized_log_samples(self):
        # 归一化重放同一随机偏向：旧框能落到日志失效的上缘，新框应首点命中。
        samples = [(58, 112), (178, 272), (102, 138), (123, 118), (144, 128), (118, 133)]
        for old_point in samples:
            with self.subTest(old_point=old_point):
                fx, fy = (old_point[0] - 58) / 120, (old_point[1] - 112) / 160
                fractions = cycle([fx] * 3 + [fy] * 3)
                device = CharacterSelectionDevice(self.clock)
                selector = self.selector(device)
                with patch('module.base.utils.random.randint',
                           side_effect=lambda a, b: round(a + (b - a) * next(fractions))):
                    self.assertEqual(random_rectangle_point(selector.select_character_grid[0, 0].button),
                                     old_point)
                    self.assertTrue(selector.select_character('WorkerJuu'))
                self.assertEqual(len(device.clicks), 1)
                self.assertEqual(device.clicks[0][1], 'SELECT_CHARACTER_GRID_0_0')
                self.assertEqual(device.clicks[0][2], (round(78 + 80 * fx), round(182 + 80 * fy)))
                self.assertEqual(device.selected, {0})
                self.assertGreaterEqual(device.selected_frames, 2)

    def test_worker_retry_in_second_row_uses_safe_area_without_moving_recognition_grid(self):
        device = CharacterSelectionDevice(self.clock, characters=[((4, 1), 'WorkerJuu', False)],
                                          accept_click=False)
        device.after_click = lambda _: setattr(device, 'accept_click', True)
        selector = self.selector(device)
        fractions = cycle([0] * 6 + [1] * 6)
        with patch('module.base.utils.random.randint',
                   side_effect=lambda a, b: round(a + (b - a) * next(fractions))):
            self.assertTrue(selector.select_character('WorkerJuu'))
        self.assertEqual([(name, point) for _, name, point in device.clicks], [
            ('SELECT_CHARACTER_GRID_4_1', (638, 362)),
            ('SELECT_CHARACTER_GRID_4_1', (718, 442)),
        ])
        self.assertGreaterEqual(device.clicks[1][0] - device.clicks[0][0], 2)
        self.assertEqual(device.selected, {(4, 1)})
        self.assertEqual(selector.select_character_grid[0, 0].area, (58, 112, 178, 272))
        self.assertEqual(selector.select_character_grid[4, 1].area, (618, 292, 738, 452))
        selector._get_stamina_value.assert_not_called()

    def test_already_selected_verified_worker_is_not_toggled_off(self):
        device = CharacterSelectionDevice(self.clock, selected=[0])
        selector = self.selector(device)
        selector.select_character_filter = Mock(return_value=True)
        self.assertTrue(selector.select_character('WorkerJuu'))
        self.assertFalse(any(name.startswith('SELECT_CHARACTER_GRID') for _, name, _ in device.clicks))
        self.assertEqual(device.selected, {0})

    def test_failed_worker_click_is_bounded_and_retried_no_faster_than_two_seconds(self):
        device = CharacterSelectionDevice(self.clock, accept_click=False)
        selector = self.selector(device)
        selector.select_character_filter = Mock(return_value=True)
        device.sleep = Mock(side_effect=AssertionError('工作啾选人不应使用固定等待'))
        self.assertFalse(selector.select_character('WorkerJuu'))
        clicks = [(stamp, name) for stamp, name, _ in device.clicks
                  if name.startswith('SELECT_CHARACTER_GRID')]
        self.assertGreaterEqual(len(clicks), 2)
        self.assertLessEqual(len(clicks), 5)
        self.assertTrue(all(name == 'SELECT_CHARACTER_GRID_0_0' for _, name in clicks))
        for (previous, _), (current, _) in zip(clicks, clicks[1:]):
            self.assertGreaterEqual(current - previous, 2)
        self.assertLessEqual(self.clock.now - device.t0, 12)
        self.assertFalse(device.selected)

    def test_preselected_chao_ho_does_not_replace_worker_in_another_slot(self):
        device = CharacterSelectionDevice(
            self.clock,
            characters=[(0, 'ChaoHo', False), (2, 'WorkerJuu', False)],
            selected=[0],
        )
        selector = self.selector(device)
        selector.select_character_filter = Mock(return_value=True)
        self.assertTrue(selector.select_character('WorkerJuu'))
        self.assertEqual([name for _, name, _ in device.clicks], ['SELECT_CHARACTER_GRID_2_0'])
        self.assertEqual(device.selected, {2})
        selector._get_stamina_value.assert_not_called()

    def test_only_preselected_chao_ho_never_reports_worker_or_clicks_any_card(self):
        device = CharacterSelectionDevice(
            self.clock, characters=[(0, 'ChaoHo', False)], selected=[0],
        )
        selector = self.selector(device)
        selector.select_character_filter = Mock(return_value=True)
        self.assertFalse(selector.select_character('WorkerJuu'))
        self.assertEqual(device.clicks, [])
        self.assertLessEqual(self.clock.now - device.t0, 5)
        selector._get_stamina_value.assert_not_called()

    def test_worker_lookup_waits_for_delayed_list_refresh(self):
        waiting = character_frame(
            SELECT_CHARACTER_SORT_ASC, characters=[(0, 'ChaoHo', False)], selected=[0],
        )
        ready = character_frame(
            SELECT_CHARACTER_SORT_ASC,
            characters=[(0, 'ChaoHo', False), (3, 'WorkerJuu', False)], selected=[0],
        )
        device = FakeDevice(self.clock, lambda t: waiting if t < 1.2 else ready, screenshot_cost=0.2)
        selector = self.selector(device)
        selector.select_character_filter = Mock(return_value=True)
        self.assertEqual(selector._select_first_available_character(['WorkerJuu']), (3, 0))
        self.assertGreaterEqual(self.clock.now - device.t0, 1.2)
        self.assertEqual(device.clicks, [])
        selector._get_stamina_value.assert_not_called()

    def test_identity_changed_after_click_never_accepts_the_other_characters_selection(self):
        device = CharacterSelectionDevice(self.clock)
        device.after_click = lambda _: setattr(device, 'characters', [(0, 'ChaoHo', False)])
        selector = self.selector(device)
        selector.select_character_filter = Mock(return_value=True)
        self.assertFalse(selector.select_character('WorkerJuu'))
        self.assertEqual([name for _, name, _ in device.clicks], ['SELECT_CHARACTER_GRID_0_0'])
        self.assertEqual(device.selected, {0})
        selector._get_stamina_value.assert_not_called()

    def test_single_selected_worker_frame_followed_by_other_identity_is_not_success(self):
        device = CharacterSelectionDevice(self.clock)
        original_frame = device.frame

        def frame(elapsed):
            if device.selected_frames >= 1:
                device.characters = [(0, 'ChaoHo', False)]
            return original_frame(elapsed)

        device.timeline = frame
        selector = self.selector(device)
        selector.select_character_filter = Mock(return_value=True)
        self.assertFalse(selector.select_character('WorkerJuu'))
        self.assertEqual(device.selected_frames, 1)
        self.assertEqual([name for _, name, _ in device.clicks], ['SELECT_CHARACTER_GRID_0_0'])

    def test_working_worker_is_not_selected(self):
        device = CharacterSelectionDevice(self.clock, characters=[(0, 'WorkerJuu', True)])
        selector = self.selector(device)
        selector.select_character_filter = Mock(return_value=True)
        self.assertFalse(selector.select_character('WorkerJuu'))
        self.assertEqual(device.clicks, [])
        selector._get_stamina_value.assert_not_called()

    def test_worker_moved_after_click_is_relocated_before_selection(self):
        device = CharacterSelectionDevice(self.clock)

        def move_once(column):
            if column == 0:
                device.characters = [(0, 'ChaoHo', False), (2, 'WorkerJuu', False)]

        device.after_click = move_once
        selector = self.selector(device)
        selector.select_character_filter = Mock(return_value=True)
        self.assertTrue(selector.select_character('WorkerJuu'))
        self.assertEqual([name for _, name, _ in device.clicks], [
            'SELECT_CHARACTER_GRID_0_0', 'SELECT_CHARACTER_GRID_2_0',
        ])
        self.assertEqual(device.selected, {2})
        self.assertGreaterEqual(device.clicks[1][0] - device.clicks[0][0], 2)

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

    def test_real_speed_ascending_page_identifies_idle_first_worker_without_filter_or_sort_click(self):
        """现场原像素仅保留标题、工作啾卡和排序控件，右侧个人面板已匿名化。"""
        frame = load_image(str(WORKER_FIXTURES / 'speed_ascending_worker.png'))
        selector, device = self.make_selector(frame)
        selector.select_character_filter = Mock(side_effect=AssertionError('工作啾不应打开筛选'))
        self.assertEqual(frame.shape, (720, 1280, 3))
        self.assertFalse(np.any(frame[:, 900:]))
        self.assertTrue(_match_sort_arrow(frame, SELECT_CHARACTER_SORT_ASC))
        self.assertFalse(_match_sort_arrow(frame, SELECT_CHARACTER_SORT_DESC))
        status = selector._recognize_character_status(frame, selector.select_character_grid[0, 0])
        self.assertEqual(status['character_name'], 'WorkerJuu')
        self.assertFalse(status['is_working'])
        self.assertFalse(status['is_selected'])
        self.assertEqual(status['stamina'], 999)
        self.assertEqual(selector._select_first_available_character(['WorkerJuu']), (0, 0))
        selector.select_character_filter.assert_not_called()
        selector._get_stamina_value.assert_not_called()
        self.assertEqual(device.clicks, [])


if __name__ == "__main__":
    unittest.main()
