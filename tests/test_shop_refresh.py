"""商店刷新离线回放：确认弹窗优先、点击冷却及返回商店的正向确认。"""

import unittest
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import Mock

from module.exception import GameStuckError
from module.handler.assets import POPUP_CANCEL, POPUP_CONFIRM
from module.shop.assets import (
    SHOP_BUY_CONFIRM_MISTAKE, SHOP_CLICK_SAFE_AREA, SHOP_REFRESH, SHOP_REFRESH_CHECK,
)
from module.shop.shop_guild import GuildShop_250814
from module.shop.ui import ShopUI
from module.ui.assets import SHOP_BACK_ARROW
from module.ui_white.assets import POPUP_CONFIRM_WHITE


@dataclass
class Screen:
    """描述当前可见状态，模板可见与遮罩后的颜色匹配分别提供。"""

    visible: frozenset = field(default_factory=frozenset)
    arrow_color: bool = False
    refresh_color: tuple | None = None
    click_ready: bool = True


def shop_screen(refresh_color=(49, 142, 207)):
    return Screen(
        frozenset((SHOP_REFRESH_CHECK.name, SHOP_BACK_ARROW.name)),
        arrow_color=True, refresh_color=refresh_color,
    )


def popup_screen(*, white=False, click_ready=True, mistake=False):
    buttons = {SHOP_BACK_ARROW.name}
    if white:
        buttons.add(POPUP_CONFIRM_WHITE.name)
    else:
        buttons.update((POPUP_CONFIRM.name, POPUP_CANCEL.name))
    if mistake:
        buttons.add(SHOP_BUY_CONFIRM_MISTAKE.name)
    # 模拟背景箭头仍能匹配，不能据此跳过前景确认弹窗。
    return Screen(frozenset(buttons), arrow_color=True, click_ready=click_ready)


class ShopReplay:
    """复用真实截图循环和弹窗处理器，仅替换截图与识别输入，不接触游戏。"""

    def __init__(self, frames, cls=ShopUI):
        self.frames = list(frames)
        self.index = 0
        self.clicks = []
        self.shop = object.__new__(cls)
        self.shop.device = Mock(
            image=self.frames[0],
            screenshot=Mock(side_effect=self.screenshot),
            click=Mock(side_effect=self.click),
        )
        self.shop.appear = Mock(side_effect=self.appear)
        self.shop.match_template_color = Mock(side_effect=self.match_template_color)
        self.shop.image_color_count = Mock(side_effect=self.image_color_count)
        self.shop.handle_info_bar = Mock(return_value=False)
        # 保留真实处理器，让蓝色双按钮和白色按钮各走实际确认路径。
        self.shop.handle_popup_confirm = Mock(wraps=self.shop.handle_popup_confirm)
        self.shop.handle_popup_cancel = Mock(wraps=self.shop.handle_popup_cancel)

    @property
    def screen(self):
        return self.shop.device.image

    def screenshot(self):
        self.index += 1
        if self.index >= len(self.frames):
            raise GameStuckError('离线截图耗尽，模拟设备卡住检测')
        self.shop.device.image = self.frames[self.index]

    def click(self, button):
        self.clicks.append((self.index, button.name))

    def appear(self, button, offset=0, interval=0, **kwargs):
        if interval and not self.screen.click_ready:
            return False
        return button.name in self.screen.visible

    def match_template_color(self, button, **kwargs):
        if button is not SHOP_BACK_ARROW:
            raise AssertionError(f'未预期的颜色模板：{button.name}')
        return self.screen.arrow_color

    def image_color_count(self, button, color, **kwargs):
        return color == self.screen.refresh_color


class ShopRefreshTests(unittest.TestCase):
    def test_popup_with_background_arrow_is_confirmed_before_returning(self):
        """重现本次缺陷：确认弹窗和背景箭头同帧可见，仍须点确认并再截图。"""
        replay = ShopReplay([shop_screen(), popup_screen(), shop_screen()])

        self.assertTrue(replay.shop.shop_refresh())

        self.assertEqual(replay.clicks, [
            (0, SHOP_REFRESH.name), (1, 'POPUP_CONFIRM_SHOP_REFRESH_CONFIRM'),
        ])
        self.assertEqual(replay.shop.device.screenshot.call_count, 2)
        replay.shop.handle_popup_confirm.assert_called_once_with(
            'SHOP_REFRESH_CONFIRM', offset=(30, 30),
        )
        replay.shop.handle_info_bar.assert_called_once()

    def test_popup_click_cooldown_keeps_waiting_instead_of_using_background_arrow(self):
        replay = ShopReplay([
            shop_screen(), popup_screen(click_ready=False), popup_screen(click_ready=False),
            popup_screen(), shop_screen(),
        ])

        self.assertTrue(replay.shop.shop_refresh())

        self.assertEqual(replay.clicks, [
            (0, SHOP_REFRESH.name), (3, 'POPUP_CONFIRM_SHOP_REFRESH_CONFIRM'),
        ])
        self.assertEqual(replay.shop.device.screenshot.call_count, 4)

    def test_white_confirmation_uses_real_popup_handler(self):
        replay = ShopReplay([shop_screen(), popup_screen(white=True), shop_screen()])

        self.assertTrue(replay.shop.shop_refresh())

        self.assertEqual(replay.clicks[-1], (1, 'POPUP_CONFIRM_WHITE_SHOP_REFRESH_CONFIRM'))
        self.assertEqual(replay.shop.device.screenshot.call_count, 2)

    def test_confirmation_still_visible_after_click_does_not_finish(self):
        replay = ShopReplay([
            shop_screen(), popup_screen(), popup_screen(click_ready=False),
            Screen(), shop_screen(),
        ])

        self.assertTrue(replay.shop.shop_refresh())

        self.assertEqual(len(replay.clicks), 2)
        self.assertEqual(replay.shop.device.screenshot.call_count, 4)

    def test_dimmed_background_arrow_after_confirmation_is_not_success(self):
        replay = ShopReplay([
            shop_screen(), popup_screen(),
            Screen(frozenset((SHOP_BACK_ARROW.name,)), arrow_color=False), shop_screen(),
        ])

        self.assertTrue(replay.shop.shop_refresh())

        self.assertEqual(replay.shop.device.screenshot.call_count, 3)
        self.assertEqual(len(replay.clicks), 2)

    def test_both_active_refresh_colors_are_supported(self):
        for color in ((49, 142, 207), (54, 117, 161)):
            with self.subTest(color=color):
                replay = ShopReplay([shop_screen(color), popup_screen(), shop_screen()])
                self.assertTrue(replay.shop.shop_refresh())
                self.assertEqual(replay.clicks[0], (0, SHOP_REFRESH.name))

    def test_disabled_refresh_returns_false_without_clicking(self):
        replay = ShopReplay([shop_screen((52, 74, 94))])

        self.assertFalse(replay.shop.shop_refresh())

        self.assertEqual(replay.clicks, [])
        replay.shop.device.screenshot.assert_not_called()
        replay.shop.handle_popup_confirm.assert_not_called()

    def test_mistaken_purchase_popup_is_closed_without_confirmation(self):
        replay = ShopReplay([
            shop_screen(), popup_screen(mistake=True),
            popup_screen(mistake=True, click_ready=False), popup_screen(), shop_screen(),
        ])

        self.assertFalse(replay.shop.shop_refresh())

        self.assertEqual(replay.clicks, [
            (0, SHOP_REFRESH.name), (1, SHOP_CLICK_SAFE_AREA.name),
            (3, 'POPUP_CANCEL_SHOP_REFRESH_ERROR'),
        ])
        replay.shop.handle_popup_confirm.assert_not_called()
        self.assertEqual(replay.shop.device.screenshot.call_count, 4)

    def test_unconfirmed_popup_disappearance_is_not_a_success(self):
        replay = ShopReplay([shop_screen(), popup_screen(click_ready=False), shop_screen()])

        self.assertFalse(replay.shop.shop_refresh())

        self.assertEqual(replay.clicks, [(0, SHOP_REFRESH.name)])
        self.assertEqual(replay.shop.device.screenshot.call_count, 2)

    def test_unknown_page_keeps_stuck_exception_for_upper_recovery(self):
        replay = ShopReplay([Screen(), Screen()])

        with self.assertRaises(GameStuckError):
            replay.shop.shop_refresh()

        self.assertEqual(replay.clicks, [])
        replay.shop.handle_info_bar.assert_not_called()

    def test_clicked_confirmation_without_returning_to_shop_is_not_success(self):
        replay = ShopReplay([shop_screen(), popup_screen(), Screen()])

        with self.assertRaises(GameStuckError):
            replay.shop.shop_refresh()

        self.assertEqual(replay.clicks[-1], (1, 'POPUP_CONFIRM_SHOP_REFRESH_CONFIRM'))
        replay.shop.handle_info_bar.assert_not_called()


class GuildShopRefreshTests(unittest.TestCase):
    @staticmethod
    def make_shop(*, refresh=True, currency=110, shop_filter='PlateT4'):
        shop = object.__new__(GuildShop_250814)
        shop.config = SimpleNamespace(GuildShop_Refresh=refresh)
        shop.__dict__['shop_filter'] = shop_filter
        shop._currency = currency
        shop.shop_buy = Mock(return_value=True)
        shop.shop_refresh = Mock(return_value=False)
        return shop

    def test_confirmed_refresh_runs_second_purchase_pass(self):
        """真实刷新完成后再进入第二轮购买，原余额门槛仍生效。"""
        replay = ShopReplay([shop_screen(), popup_screen(), shop_screen()], cls=GuildShop_250814)
        shop = replay.shop
        shop.config = SimpleNamespace(GuildShop_Refresh=True)
        shop.__dict__['shop_filter'] = 'PlateT4'
        purchases = []

        def buy():
            purchases.append(replay.index)
            shop._currency = 26792 if len(purchases) == 1 else 109
            return True

        shop.shop_buy = Mock(side_effect=buy)

        shop.run()

        self.assertEqual(purchases, [0, 2])
        self.assertEqual(replay.clicks, [
            (0, SHOP_REFRESH.name), (1, 'POPUP_CONFIRM_SHOP_REFRESH_CONFIRM'),
        ])

    def test_refresh_disabled_keeps_single_purchase_pass(self):
        shop = self.make_shop(refresh=False)

        shop.run()

        shop.shop_buy.assert_called_once()
        shop.shop_refresh.assert_not_called()

    def test_refresh_currency_boundary_remains_110(self):
        for currency, expected in ((109, 0), (110, 1)):
            with self.subTest(currency=currency):
                shop = self.make_shop(currency=currency)
                shop.run()
                self.assertEqual(shop.shop_refresh.call_count, expected)
                shop.shop_buy.assert_called_once()

    def test_failed_refresh_does_not_start_second_purchase_pass(self):
        shop = self.make_shop()

        shop.run()

        shop.shop_refresh.assert_called_once()
        shop.shop_buy.assert_called_once()

    def test_empty_filter_does_not_purchase_or_refresh(self):
        shop = self.make_shop(shop_filter='')

        shop.run()

        shop.shop_buy.assert_not_called()
        shop.shop_refresh.assert_not_called()

    def test_unsuccessful_purchase_pass_does_not_refresh(self):
        shop = self.make_shop()
        shop.shop_buy.return_value = False

        shop.run()

        shop.shop_refresh.assert_not_called()


if __name__ == '__main__':
    unittest.main()
