"""仓库筛选确认必须等待弹窗关闭，再允许读取库存。"""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call

from module.base.utils import load_image
from module.exception import GameStuckError
from module.island.island import FILTER_CONFIRM, ISLAND_WAREHOUSE_CHECK, Island
from module.island.round_warehouse import RoundWarehouseReader, WarehousePage
from module.ui.page import page_island_warehouse_filter


class FilterFrames:
    """提供截图序列；点击不直接改变画面，下一帧决定是否生效。"""

    def __init__(self, *sequences):
        self.sequences = iter(sequences)
        self.frame = 'stale'
        self.reads = 0
        self.events = []
        self.main = SimpleNamespace(
            device=SimpleNamespace(screenshot=Mock(), sleep=Mock(), click=Mock()),
            loop=Mock(side_effect=self.loop),
            appear=Mock(side_effect=self.appear),
            appear_then_click=Mock(side_effect=self.click_confirm),
            match_template_color=Mock(side_effect=self.warehouse_visible),
        )
        self.main.warehouse_filter_confirm = lambda: Island.warehouse_filter_confirm(self.main)

    def loop(self, **kwargs):
        for frame in next(self.sequences):
            self.main.device.screenshot()
            self.frame = frame
            self.reads += 1
            self.events.append(('screenshot', frame))
            yield None

    def appear(self, button, **kwargs):
        if button is page_island_warehouse_filter.check_button:
            return self.frame in ('filter', 'filter_over_warehouse')
        return False

    def click_confirm(self, button, **kwargs):
        if button is FILTER_CONFIRM:
            self.events.append(('click_confirm', self.frame))
            return True
        return False

    def warehouse_visible(self, button, **kwargs):
        self.events.append(('check_warehouse', self.frame))
        return button is ISLAND_WAREHOUSE_CHECK and self.frame in ('warehouse', 'filter_over_warehouse')


class WarehouseFilterConfirmTests(unittest.TestCase):
    def test_real_filter_capture_is_retried_before_normal_or_empty_warehouse(self):
        fixtures = Path(__file__).parent / 'fixtures/island_round_warehouse'
        for ready_image in ('farm.png', 'coffee_empty.png'):
            with self.subTest(ready_image=ready_image):
                images = {
                    'filter': load_image(str(fixtures / 'filter_juu_eatery.png')),
                    'warehouse': load_image(str(fixtures / ready_image)),
                }
                flow = FilterFrames(['filter', 'filter', 'warehouse'])
                flow.main.appear.side_effect = lambda button, **kwargs: button.match(
                    images[flow.frame], **kwargs)
                flow.main.match_template_color.side_effect = lambda button, **kwargs: button.match_template_color(
                    images[flow.frame], **kwargs)

                # 形状匹配可能命中蒙层背景，颜色检查必须拒绝错误现场。
                self.assertFalse(ISLAND_WAREHOUSE_CHECK.match_template_color(
                    images['filter'], offset=(30, 30), similarity=0.85, threshold=30))
                flow.main.warehouse_filter_confirm()

                self.assertEqual(flow.reads, 3)
                self.assertEqual(flow.main.appear_then_click.call_count, 2)
                self.assertEqual(flow.frame, 'warehouse')

    def test_first_click_does_not_close_filter_and_next_click_recovers(self):
        flow = FilterFrames(['filter', 'filter', 'warehouse'])

        flow.main.warehouse_filter_confirm()

        self.assertEqual(flow.reads, 3)
        self.assertEqual(flow.main.appear_then_click.call_args_list,
                         [call(FILTER_CONFIRM, interval=1)] * 2)
        flow.main.loop.assert_called_once_with(skip_first=False, timeout=12)
        flow.main.device.sleep.assert_not_called()

    def test_filter_takes_priority_over_visible_warehouse_background(self):
        flow = FilterFrames(['filter_over_warehouse', 'warehouse'])

        flow.main.warehouse_filter_confirm()

        self.assertEqual(flow.events, [
            ('screenshot', 'filter_over_warehouse'),
            ('click_confirm', 'filter_over_warehouse'),
            ('screenshot', 'warehouse'),
            ('check_warehouse', 'warehouse'),
        ])

    def test_filter_that_never_closes_raises_without_reading_background(self):
        flow = FilterFrames(['filter_over_warehouse'] * 4)

        with self.assertRaisesRegex(GameStuckError, '仓库筛选确认超时，筛选弹窗未关闭'):
            flow.main.warehouse_filter_confirm()

        self.assertEqual(flow.reads, 4)
        flow.main.loop.assert_called_once_with(skip_first=False, timeout=12)
        flow.main.match_template_color.assert_not_called()

    def test_transition_is_not_success_until_warehouse_is_confirmed(self):
        flow = FilterFrames(['filter', 'transition', 'transition', 'warehouse'])

        flow.main.warehouse_filter_confirm()

        self.assertEqual(flow.reads, 4)
        flow.main.appear_then_click.assert_called_once_with(FILTER_CONFIRM, interval=1)
        self.assertEqual(flow.main.match_template_color.call_count, 3)
        flow.main.match_template_color.assert_called_with(
            ISLAND_WAREHOUSE_CHECK, offset=(30, 30), similarity=0.85, threshold=30)

    def test_unknown_page_times_out_instead_of_assuming_filter_is_closed(self):
        flow = FilterFrames(['unknown'] * 3)

        with self.assertRaisesRegex(GameStuckError, '仓库筛选确认超时，仓库列表未就绪'):
            flow.main.warehouse_filter_confirm()

        self.assertEqual(flow.reads, 3)
        flow.main.appear_then_click.assert_not_called()

    def make_reader(self, confirm_frames):
        # 前两组帧分别用于重置筛选和确认产地选择，第三组才是确认弹窗流程。
        flow = FilterFrames(['filter'], ['filter'], confirm_frames)
        flow.main.ui_goto = Mock()
        flow.main.warehouse_filter_kind = {(0, 0): object()}
        flow.main.warehouse_filter_from = {(0, 0): object(), (1, 0): object()}
        flow.main.warehouse_filter_button_selected = Mock(return_value=True)
        flow.main.warehouse_filter = lambda *args: Island.warehouse_filter(flow.main, *args)
        reader = RoundWarehouseReader(flow.main)
        return flow, reader

    def test_inventory_read_starts_only_after_real_filter_flow_confirms_warehouse(self):
        flow, reader = self.make_reader(['filter_over_warehouse', 'filter', 'warehouse'])
        page = WarehousePage((((2000, 7), None, None, None, None, None),), (261,), True)

        def read_page(templates):
            self.assertEqual(flow.frame, 'warehouse')
            self.assertEqual(flow.reads, 5)
            return page

        reader.stable_page = Mock(side_effect=read_page)

        self.assertEqual(reader.read_group('farm', {2000: []}, {2000}), {2000: 7})
        reader.stable_page.assert_called_once_with({2000: []})
        flow.main.device.sleep.assert_not_called()

    def test_failed_filter_confirmation_never_starts_inventory_read(self):
        flow, reader = self.make_reader(['filter_over_warehouse'] * 3)
        reader.stable_page = Mock()

        with self.assertRaisesRegex(GameStuckError, '筛选弹窗未关闭'):
            reader.read_group('farm', {2000: []}, {2000})

        reader.stable_page.assert_not_called()
        flow.main.device.sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()
