"""商店 UI 导航处理，管理商店页面的标签切换和导航栏。
支持 2025-08-14 新 UI 的底部导航栏和标签页切换。
"""

from module.base.button import ButtonGrid
from module.base.decorator import cached_property
from module.handler.assets import POPUP_CONFIRM
from module.logger import logger
from module.shop.assets import *
from module.ui.assets import ACADEMY_GOTO_MUNITIONS, SHOP_BACK_ARROW
from module.ui.navbar import Navbar
from module.ui.page import page_academy, page_munitions
from module.ui.switch import Switch
from module.ui.ui import UI
from module.ui_white.assets import POPUP_CONFIRM_WHITE


class ShopUI(UI):
    @cached_property
    def _shop_bottom_navbar(self):
        """商店底部导航栏组件。

        包含 5 个选项：medal（勋章）、guild（舰队）、prototype（原型）、
        core（核心）、merit（功勋）。

        Returns:
            Navbar: 底部导航栏控制器实例。
        """
        shop_bottom_navbar = ButtonGrid(
            origin=(399, 619), delta=(182, 0),
            button_shape=(56, 42), grid_shape=(5, 1),
            name='SHOP_BOTTOM_NAVBAR')

        return Navbar(grids=shop_bottom_navbar,
                      active_color=(33, 195, 239),
                      inactive_color=(181, 178, 181))

    def shop_bottom_navbar_ensure(self, left=None, right=None):
        """确保能够跳转到对应页面，且页面已完全加载。

        Args:
            left (int): 取决于商店导航栏的位置
            right (int): 取决于商店导航栏的位置

        Returns:
            bool: 底部导航栏是否设置成功
        """
        if self._shop_bottom_navbar.set(self, left=left, right=right):
            return True
        return False

    @cached_property
    def shop_nav_250814(self):
        """250814 版商店顶部导航栏切换器。

        包含「通用」和「月度」两个导航选项，用于在不同商店大类之间切换。

        Pages:
            in: page_munitions

        Returns:
            Switch: 顶部导航栏 Switch 状态机。
        """
        switch = Switch('shop_nav_250814', is_selector=True, offset=(20, 20))
        switch.add_state(NAV_GENERAL, check_button=NAV_GENERAL)
        switch.add_state(NAV_MONTHLY, check_button=NAV_MONTHLY)
        return switch

    @cached_property
    def shop_tab_250814(self):
        """250814 版商店分类标签切换器。

        包含 9 个标签页：通用、功勋、舰队、META、奖励、
        核心限定、核心月度、勋章、原型，用于切换不同商店分类。

        Pages:
            in: page_munitions

        Returns:
            Switch: 分类标签 Switch 状态机。
        """
        switch = Switch('shop_tab_250814', is_selector=True, offset=(20, 20))
        switch.add_state(TAB_GENERAL, check_button=TAB_GENERAL)
        switch.add_state(TAB_MERIT, check_button=TAB_MERIT)
        switch.add_state(TAB_GUILD, check_button=TAB_GUILD)
        switch.add_state(TAB_META, check_button=TAB_META)
        switch.add_state(TAB_PRIZE, check_button=TAB_PRIZE)
        switch.add_state(TAB_CORE_LIMITED, check_button=TAB_CORE_LIMITED)
        switch.add_state(TAB_CORE_MONTHLY, check_button=TAB_CORE_MONTHLY)
        switch.add_state(TAB_MEDAL, check_button=TAB_MEDAL)
        switch.add_state(TAB_PROTOTYPE, check_button=TAB_PROTOTYPE)
        return switch

    def shop_refresh(self):
        """
        执行商店刷新操作。

        流程：点击刷新按钮，优先处理确认框，再确认已返回商店。
        刷新按钮有两种激活颜色状态，若为暗色则表示不可刷新。

        Pages:
            in: page_munitions (SHOP_BACK_ARROW 可见)
            out: page_munitions（刷新完成，或不可刷新／误入购买弹窗已关闭）

        Returns:
            bool: 是否已点击刷新确认并确认返回商店。
        """
        logger.info('[商店-UI] 刷新商店')
        refreshed = False
        refresh_failed = False
        confirmation_seen = False

        # 弹窗遮罩下返回箭头的形状仍可能匹配，先处理弹窗再判断结束。
        for _ in self.loop():
            if self.appear(SHOP_BUY_CONFIRM_MISTAKE, offset=(200, 200)):
                if not refresh_failed:
                    logger.warning('[商店-UI] 刷新误入商品购买弹窗，取消本次刷新')
                refresh_failed = True
                refreshed = False
                if self.appear(SHOP_BUY_CONFIRM_MISTAKE, interval=3, offset=(200, 200)):
                    self.device.click(SHOP_CLICK_SAFE_AREA)
                continue

            if self.appear(POPUP_CONFIRM, offset=(30, 30)) \
                    or self.appear(POPUP_CONFIRM_WHITE, offset=(30, 30)):
                confirmation_seen = True
                if refresh_failed:
                    self.handle_popup_cancel('SHOP_REFRESH_ERROR', offset=(30, 30))
                elif self.handle_popup_confirm('SHOP_REFRESH_CONFIRM', offset=(30, 30)):
                    refreshed = True
                # 点击仍在冷却时也不能用背景箭头结束，下一轮获取新截图。
                continue

            if (refreshed or refresh_failed or confirmation_seen) \
                    and self.match_template_color(SHOP_BACK_ARROW, offset=(30, 30)):
                if refreshed:
                    logger.info('[商店-UI] 刷新已确认，已返回商店')
                elif not refresh_failed:
                    logger.warning('[商店-UI] 刷新确认未完成，已返回商店')
                break

            if refreshed or refresh_failed or confirmation_seen:
                continue

            # SHOP_REFRESH_CHECK 是刷新图标
            # SHOP_REFRESH 是带背景的刷新图标
            if self.appear(SHOP_REFRESH_CHECK, offset=(30, 30)):
                # SHOP_REFRESH 激活时有两种颜色状态
                if self.image_color_count(SHOP_REFRESH.button, color=(49, 142, 207), threshold=30, count=50) \
                        or self.image_color_count(SHOP_REFRESH.button, color=(54, 117, 161),
                                                  threshold=30, count=50):
                    if self.appear(SHOP_REFRESH_CHECK, offset=(30, 30), interval=3):
                        self.device.click(SHOP_REFRESH)
                    continue
                if self.image_color_count(SHOP_REFRESH.button, color=(52, 74, 94), threshold=30, count=50):
                    logger.info('[商店-UI] 刷新不可用')
                    break

        # 刷新后会出现信息栏，等待其消失再结束
        self.ensure_no_info_bar(timeout=1)
        return refreshed

    def ui_goto_shop(self):
        """
        导航到 page_munitions（军需商店）。
        此路由保证进入时位于通用商店。

        Pages:
            in: Any
            out: page_munitions
        """
        if self.ui_get_current_page() == page_munitions:
            logger.info(f'[商店-UI] 已在 {page_munitions}')
            return

        self.ui_ensure(page_academy)

        skip_first_screenshot = True
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(page_munitions.check_button, offset=(20, 20)):
                break

            # 使用较大偏移量，因为学院中的摄像机可以移动
            if self.appear_then_click(ACADEMY_GOTO_MUNITIONS, offset=(200, 200), interval=5):
                continue
