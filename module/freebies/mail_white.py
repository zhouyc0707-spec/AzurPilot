"""
邮件白色主题 UI 处理模块。

处理碧蓝航线邮件页面的完整交互流程，包括：
- 邮件页面的进入与退出
- 按类型筛选并批量领取邮件奖励（功勋、维护补偿、贸易许可证）
- 批量删除已领取的邮件
- 处理白色主题 UI 下的邮件相关弹窗和确认框

该模块专为白色主题 UI 设计，通过 MailSelectSetting 配置
邮件内容筛选条件（魔方、金币、石油、功勋、钻石等）。
"""
import re

from module.base.decorator import cached_property
from module.base.timer import Timer
from module.base.utils import color_similar, lower_template_match_similarity, rgb2luma
from module.combat.assets import GET_ITEMS_1, GET_ITEMS_2
import module.config.server as server
from module.exception import GamePageUnknownError
from module.freebies.assets import *
from module.logger import logger
from module.ocr.ocr import Ocr
from module.ui.page import GOTO_MAIN_WHITE, page_mail, page_main, page_main_white
from module.ui.setting import Setting
from module.ui.ui import UI


class MailCountOcr(Ocr):
    """同时保留浅色的现有数量与深色的容量，避免只读到半个计数。"""

    def pre_process(self, image):
        return rgb2luma(image)


MAIL_COUNT = MailCountOcr((204, 135, 279, 166), alphabet='0123456789/', name='MAIL_COUNT')


class MailSelectSetting(Setting):
    """
    邮件筛选设置管理器。

    继承自 Setting，用于管理邮件内容类型的筛选选项。
    通过检测选项按钮的颜色（深灰色 (57, 56, 57)）判断选项是否激活。
    """

    def is_option_active(self, option: Button) -> bool:
        """检测指定选项按钮当前是否处于激活选定状态。

        Args:
            option (Button): 选项按钮。

        Returns:
            bool: 处于激活状态返回 True，否则返回 False。
        """
        return self.main.image_color_count(option, color=(57, 56, 57), threshold=30, count=50)


class MailWhite(UI):
    """
    白色主题邮件处理器。

    负责白色主题 UI 下邮件的领取和清理操作。支持以下功能：
    - 按内容类型筛选邮件（功勋、维护补偿、贸易许可证）
    - 批量领取符合条件的邮件奖励
    - 批量删除已领取的邮件

    使用 MailSelectSetting 管理筛选条件，包含两个设置实例：
    - mail_select_setting: 按类型筛选（魔方、金币、石油、功勋、钻石）
    - mail_select_all_setting: 全选模式，用于批量删除

    Attributes:
        mail_select_setting: 按内容类型筛选的设置实例（cached_property）。
        mail_select_all_setting: 全选模式的设置实例（cached_property）。
    """
    @cached_property
    def mail_select_setting(self):
        """获取按内容类型筛选邮件的设置管理器。"""
        setting = MailSelectSetting('Mail', main=self)
        setting.reset_first = False
        setting.need_deselect = True
        setting.add_setting(
            setting='contains',
            option_buttons=[MAIL_SELECT_CUBE, MAIL_SELECT_COINS, MAIL_SELECT_OIL, MAIL_SELECT_MERIT, MAIL_SELECT_GEMS],
            option_names=['cube', 'coins', 'oil', 'merit', 'gems'],
            option_default='merit'
        )
        return setting

    @cached_property
    def mail_select_all_setting(self):
        """获取全选邮件的设置管理器。"""
        setting = MailSelectSetting('MailAll', main=self)
        setting.reset_first = False
        setting.add_setting(
            setting='all',
            option_buttons=[MAIL_SELECT_ALL],
            option_names=['all'],
            option_default='all'
        )
        return setting

    def _mail_manage_button(self):
        """在已确认的邮件页中定位管理按钮，兼容国服当前字体。

        Returns:
            Button | None: 识别到的管理按钮，包含当前画面的实际偏移。
        """
        if self.match_template_color(MAIL_MANAGE, offset=(30, 30)):
            return MAIL_MANAGE
        if server.server == 'cn':
            # 仅搜索原按钮附近；旧模板失败产生的偏移不能用于新模板的点击。
            x1, y1, x2, y2 = MAIL_MANAGE.area
            area = (x1 - 30, y1 - 30, x2 + 30, y2 + 30)
            similarity, button = TEMPLATE_MAIL_MANAGE_CURRENT.match_result(
                self.image_crop(area), name=MAIL_MANAGE.name,
            )
            if (similarity > lower_template_match_similarity(0.85)
                    and color_similar(button.color, MAIL_MANAGE.color, threshold=30)):
                return button.move(area[:2], name=MAIL_MANAGE.name)
        return None

    def _mail_is_empty(self):
        """读取正向空列表证据；国服只接受完整的零数量／有效容量。"""
        if server.server == 'cn':
            # 旧国服空态素材为日文，不能用它或 OCR 失败时的默认零来判空。
            result = MAIL_COUNT.ocr(self.device.image)
            return re.fullmatch(r'0/[1-9][0-9]*', result.strip()) is not None
        return self.appear(MAIL_WHITE_EMPTY, offset=(20, 20))

    def _mail_enter(self, skip_first_screenshot=True):
        """
        进入邮件页面。

        Args:
            skip_first_screenshot (bool): 是否跳过首次截图，默认 True。

        Returns:
            bool: 成功进入且有邮件返回 True，邮件列表为空返回 False。

        Raises:
            GamePageUnknownError: 持续无法确认邮件状态，保存现场后交由上层恢复。

        Pages:
            in: page_main_white 或 MAIL_MANAGE
            out: MAIL_BATCH_CLAIM
        """
        logger.info('进入邮件')
        self.interval_clear([
            MAIL_MANAGE
        ])
        timeout = Timer(20, count=10).start()
        empty_confirm = Timer(0.6, count=2)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # End
            if self.appear(MAIL_BATCH_CLAIM, offset=(20, 20)):
                logger.info('[免费福利-邮件] 已进入邮件管理')
                return True

            if self._handle_mail_reward():
                empty_confirm.clear()
                continue

            # 返回主页图标在有邮件时同样存在，只能作为页面前置条件。
            if (self.appear(page_mail.check_button, offset=(20, 20))
                    and self.appear(GOTO_MAIN_WHITE, offset=(20, 20))):
                manage = self._mail_manage_button()
                if manage is not None:
                    empty_confirm.clear()
                    if self.get_interval_timer(MAIL_MANAGE, interval=3).reached():
                        self.device.click(manage)
                        self.interval_reset(MAIL_MANAGE)
                        continue
                elif self._mail_is_empty():
                    empty_confirm.start()
                    if empty_confirm.reached():
                        logger.info('[免费福利-邮件] 已连续确认邮件列表为空')
                        return False
                else:
                    empty_confirm.clear()
            else:
                empty_confirm.clear()

            # Click
            if self.ui_main_appear_then_click(page_mail, offset=(30, 30), interval=3):
                continue
            if timeout.reached():
                logger.warning('[免费福利-邮件] 未能确认邮件状态，保存现场，不判定为空邮箱')
                try:
                    self.device.save_screenshot(genre='mail_unknown', interval=0)
                except OSError as exc:
                    logger.warning(f'[免费福利-邮件] 保存未知页面失败: {exc}')
                raise GamePageUnknownError('[免费福利-邮件] 无法确认邮件管理或空邮箱状态')

    def _mail_quit(self, skip_first_screenshot=True):
        """
        退出邮件页面。

        Pages:
            in: page_mail 中的任意页面
            out: page_main_white
        """
        logger.info('退出邮件')
        self.interval_clear([
            MAIL_BATCH_CLAIM,
            GOTO_MAIN_WHITE,
            GET_ITEMS_1,
            GET_ITEMS_2,
        ])
        self.popup_interval_clear()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # End
            if self.is_in_main():
                logger.info('退出邮件 to main')
                break

            # Click
            if self.handle_popup_confirm('MAIL_QUIT'):
                continue
            if self.appear(MAIL_BATCH_CLAIM, offset=(30, 30), interval=3):
                logger.info(f'{MAIL_BATCH_CLAIM} -> {MAIL_MANAGE}')
                self.device.click(MAIL_MANAGE)
                continue
            if self.appear_then_click(GOTO_MAIN_WHITE, offset=(30, 30), interval=3):
                continue
            if self._handle_mail_reward():
                continue

    def _handle_mail_reward(self):
        """
        处理邮件奖励领取后的物品获取弹窗。

        检测 GET_ITEMS_1 或 GET_ITEMS_2 弹窗出现时，自动点击确认
        以完成奖励领取流程。

        Returns:
            bool: 是否检测到并处理了物品获取弹窗。
        """
        if self.appear(GET_ITEMS_1, offset=(30, 30), interval=3):
            logger.info(f'{GET_ITEMS_1} -> {MAIL_BATCH_CLAIM}')
            self.device.click(MAIL_BATCH_CLAIM)
            return True
        if self.appear(GET_ITEMS_2, offset=(30, 30), interval=3):
            logger.info(f'{GET_ITEMS_2} -> {MAIL_BATCH_CLAIM}')
            self.device.click(MAIL_BATCH_CLAIM)
            return True
        return False

    def _mail_claim_execute(self, skip_first_screenshot=True):
        """
        执行邮件批量领取。

        Pages:
            in: MAIL_BATCH_CLAIM
            out: page_main_white，可能带有 info_bar

        Returns:
            int: 是否领取成功。
        """
        self.handle_info_bar()
        self.interval_clear([
            MAIL_BATCH_CLAIM,
            GET_ITEMS_1,
            GET_ITEMS_2,
        ])
        self.popup_interval_clear()

        claimed = False
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # End
            if claimed and self.appear(MAIL_BATCH_CLAIM, offset=(30, 30)):
                break
            # Click
            if not claimed and self.appear_then_click(MAIL_BATCH_CLAIM, offset=(30, 30), interval=3):
                continue
            if self.handle_popup_confirm('MAIL_CLAIM'):
                claimed = True
                continue
            if self._handle_mail_reward():
                claimed = True
                continue

        success = self.info_bar_count() > 0
        logger.info(f'邮件领取成功: {success}')
        return success

    def _mail_delete(self, skip_first_screenshot=True):
        """
        批量删除已领取的邮件。

        Pages:
            in: MAIL_BATCH_DELETE
            out: MAIL_BATCH_DELETE
        """
        self.handle_info_bar()
        self.interval_clear([
            MAIL_BATCH_DELETE
        ])
        self.popup_interval_clear()

        deleted = False
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # End
            if deleted and self.appear(MAIL_BATCH_DELETE, offset=(30, 30)):
                break
            # Click
            if not deleted and self.appear_then_click(MAIL_BATCH_DELETE, offset=(30, 30), interval=3):
                continue
            if self.handle_popup_confirm('MAIL_CLAIM'):
                deleted = True
                continue
            if self._handle_mail_reward():
                continue

        # 成功删除邮件或无邮件可删时会出现 info_bar
        return True

    def mail_claim(
            self,
            merit=True,
            maintenance=False,
            trade_license=False,
            delete=True,
    ):
        """
        领取邮件奖励。

        Args:
            merit (bool): 是否领取功勋邮件。
            maintenance (bool): 是否领取维护补偿邮件。
            trade_license (bool): 是否领取贸易许可证邮件。
            delete (bool): 是否删除已领取的邮件。

        Pages:
            in: page_main_white 或 MAIL_MANAGE
            out: MAIL_BATCH_CLAIM
        """
        if not self._mail_enter():
            return

        if merit:
            logger.hr('邮件功勋', level=2)
            if not self._mail_enter():
                return
            self.mail_select_setting.set(contains=['merit'])
            self._mail_claim_execute()
        if maintenance:
            logger.hr('邮件维护', level=2)
            if not self._mail_enter():
                return
            self.mail_select_setting.set(contains=['coins', 'oil'])
            self._mail_claim_execute()
            if not self._mail_enter():
                return
            self.mail_select_setting.set(contains=['coins', 'oil', 'gems'])
            self._mail_claim_execute()
        if trade_license:
            logger.hr('邮件贸易许可', level=2)
            if not self._mail_enter():
                return
            self.mail_select_setting.set(contains=['coins', 'oil', 'cube'])
            self._mail_claim_execute()
        if delete:
            logger.hr('邮件删除', level=2)
            if not self._mail_enter():
                return
            self.mail_select_all_setting.set(contains=['all'])
            self._mail_delete()

        self._mail_quit()

    def run(self):
        merit = self.config.Mail_ClaimMerit
        maintenance = self.config.Mail_ClaimMaintenance
        trade_license = self.config.Mail_ClaimTradeLicense
        delete = self.config.Mail_DeleteCollected
        logger.info(f'[免费福利-邮件] 邮件奖励: 功勋={merit}, 维护补偿={maintenance}, '
                    f'贸易许可={trade_license}, 删除={delete}')
        if not merit and not maintenance and not trade_license:
            logger.warning('无内容可领取')
            return False

        # 必须使用白色主题 UI
        self.ui_ensure(page_main)
        if self.appear(page_main_white.check_button, offset=(30, 30)):
            logger.info('在白色主页')
            pass
        elif self.appear(page_main.check_button, offset=(5, 5)):
            logger.info('在主页')
            pass
        else:
            logger.warning('[免费福利-邮件] 未知的主页面，无法进入邮件页面')
            return False

        # 领取
        self.mail_claim(
            merit=merit,
            maintenance=maintenance,
            trade_license=trade_license,
            delete=delete,
        )
        return True
