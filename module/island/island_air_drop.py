"""岛屿空投任务模块。

处理岛屿每日空投领取逻辑，按日常补给入口文字与奖励状态确认领取。
管理好友空投冷却时间（5小时）与每日重置边界，保留走位及补滑配置。
"""
from module.island.island import *
from module.ui.scroll import Scroll
from module.base.decorator import cached_property
from datetime import timedelta
from module.config.time_source import now as current_time
from module.config.utils import get_server_last_update


AIR_DROP_RECEIVE = Button(
    area=(795, 374, 1029, 412), color=(), button=(880, 379, 1000, 409),
    name='AIR_DROP_RECEIVE',
)


def is_daily_supply_reward(image):
    """在归一化截图中同时确认补给奖励标题与空白关闭提示。

    仅在固定奖励区域严格匹配，不随非原生分辨率降低阈值；背景岛屿
    图标或一处文字单独出现，不能证明当前画面可以点击关闭。
    """
    if image is None or image.shape[:2] != (720, 1280):
        return False
    return (
        TEMPLATE_AIR_DROP_REWARD_TITLE.match(
            image[160:240, 490:795], similarity=0.9, direct_match=True)
        and TEMPLATE_AIR_DROP_REWARD_CLOSE.match(
            image[545:595, 550:730], similarity=0.9, direct_match=True)
    )


class IslandAirDrop(Island):
    """岛屿空投日常任务执行器。"""

    AIR_DROP_TIMEOUT = 15

    @cached_property
    def air_drop_action_ocr(self):
        # 分享与接收共用白色交互按钮，只读取文字，不把分享资源当作领取模板。
        return Ocr((860, 369, 1036, 418), lang='cnocr', letter=(74, 77, 74),
                   threshold=128, name='AIR_DROP_ACTION')

    def run(self):
        """执行岛屿空投任务流程。

        包含自己岛屿空投领取、好友岛屿补给拜访、冷却调度与卡死异常处理。
        """
        self.island_error = False
        now = current_time()
        # 每日边界以服务器 0 点为准（换算为本机时间轴）
        today = get_server_last_update('00:00')
        next_daily_time = today + timedelta(days=1, hours=1)  # 服务器次日 01:00
        last_steal_time = self.config.IslandAirDrop_LastSteal
        next_steal_time = now + timedelta(hours=5)
        last_attempt_today = today + timedelta(hours=23)  # 服务器当日 23:00
        # 用户停止任务时可能仍停在补给奖励页；先关闭已明确识别的现场，
        # 再走正常每日检查，不能把残留弹窗单独当作本日任务已完成。
        self.device.screenshot()
        if is_daily_supply_reward(self.device.image):
            self.island_air_drop()
            if self.is_in_friend_island() and not self.exit_friend_island():
                self.device.save_screenshot(genre='island_air_drop_unknown', interval=0)
                raise GameStuckError('岛屿补给：残留奖励处理后未能返回本岛，已保存现场')
        if last_steal_time < today:
            self.goto_postmanage()
            if self.appear_then_click(MY_AIR_DROP_ALREADY):
                self.ui_goto(page_island, get_ship=False)
                collected = self.island_air_drop()
                self.island_walk_route('AirDropSelf')
                if not collected:
                    collected = self.island_air_drop()
                if not collected:
                    self.device.save_screenshot(genre='island_air_drop_unknown', interval=0)
                    logger.warning('[岛屿-每日补给] 未确认自己的补给已领取，保留领取日期，延后至少五分钟并按全局对齐复查')
                    self.config.task_delay(target=current_time() + timedelta(minutes=5))
                    return
        # 是否前往其他玩家岛屿拿补给
        if self.config.IslandAirDrop_VisitOtherIsland:
            has_drops = True
            self.goto_management()
            while 1:
                self.ui_goto(page_island_visit, get_ship=False)
                ocr_air_drop = DigitCounter(
                    OCR_AIR_DROP,
                    name="air_drop",
                    letter=(150, 150, 150),
                    threshold=80,
                    alphabet="0123456789/",
                )
                image = self.device.screenshot()
                number1, number2, number3 = ocr_air_drop.ocr(image)
                if number1 > 0:
                    has_drops = True
                    if self.find_air_drop():
                        self.device.sleep(1)
                        collected = self.run_and_get()
                        if not collected:
                            logger.warning('[岛屿-每日补给] 未确认好友补给已领取，保留剩余次数并按冷却复查')
                            break
                        self.device.sleep(3)
                        # 每次完成补给后立即重新检测剩余次数，确认消耗是否成功
                        # 因为运行可能失败导致次数没使用成功
                        self.ui_goto(page_island_visit, get_ship=False)
                        ocr_air_drop = DigitCounter(
                            OCR_AIR_DROP,
                            name="air_drop",
                            letter=(150, 150, 150),
                            threshold=80,
                            alphabet="0123456789/",
                        )
                        image = self.device.screenshot()
                        number1, number2, number3 = ocr_air_drop.ocr(image)
                        if number1 > 0:
                            logger.info(
                                f"剩余补给次数: {number1}/{number2}，继续执行好友补给"
                            )
                            continue
                        else:
                            logger.info("[岛屿-每日补给] 补给次数已用尽")
                            has_drops = False
                            break
                    else:
                        break
                else:
                    has_drops = False
                    break
        else:
            logger.info("[岛屿-每日补给] 已禁用拜访其他玩家岛屿，跳过好友补给")
            has_drops = False
        self.config.IslandAirDrop_LastSteal = current_time().replace(microsecond=0)

        if has_drops and next_steal_time < last_attempt_today:
            self.config.task_delay(target=next_steal_time)
        elif has_drops and next_steal_time > last_attempt_today > now:
            self.config.task_delay(target=last_attempt_today)
        else:
            self.config.task_delay(target=next_daily_time)
        if self.island_error:
            from module.exception import GameBugError

            raise GameBugError("检测到岛屿拜访卡死，需要重启")
        self.device.sleep(1)

    def find_air_drop(self):
        """在好友岛屿拜访列表中搜索可用的空投目标。

        Returns:
            bool: 找到并成功进入可用好友岛屿返回 True，未找到或不可用返回 False。
        """
        search_area = (662, 90, 720, 660)
        VISIT_SCROLL = Scroll(
            VISIT_SCROLL_AREA, color=(255, 255, 255), name="VISIT_SCROLL"
        )
        max_swipe_attempts = 15
        swipe_count = 0
        last_attempt_swipe = 1
        while swipe_count <= max_swipe_attempts:
            self.device.screenshot()
            region_image = self.image_crop(search_area, copy=False)
            air_drop_buttons = TEMPLATE_AIR_DROP.match_multi(
                region_image, similarity=0.85, threshold=5, name="air_drop_buttons"
            )

            if not air_drop_buttons:
                logger.info("[岛屿-每日补给] 在指定区域内未找到补给")

                # 检查是否在底部
                if VISIT_SCROLL.at_bottom(main=self) and last_attempt_swipe > 0:
                    last_attempt_swipe -= 1
                    logger.info("[岛屿-每日补给] 滑动槽已在底部，最后尝试滑动")
                    continue
                elif VISIT_SCROLL.at_bottom(main=self) and last_attempt_swipe <= 0:
                    logger.info("[岛屿-每日补给] 滑动槽已在底部，停止搜索")
                    return False
                # 如果还有滑动次数，尝试滑动
                if swipe_count < max_swipe_attempts:
                    logger.info(f"[岛屿-每日补给] 滑动尝试 {swipe_count + 1}/{max_swipe_attempts}")
                    self.visit_swipe(480)
                    swipe_count += 1
                    self.device.sleep(0.5)
                    continue
                else:
                    logger.info("[岛屿-每日补给] 达到最大滑动次数，停止搜索")
                    return False

            logger.info(f"[岛屿-每日补给] 找到 {len(air_drop_buttons)} 个补给目标")
            air_drop_buttons.sort(key=lambda btn: btn.area[1])

            # 标记是否有至少一个可以点击的补给
            has_clickable_air_drop = False

            for air_drop_button in air_drop_buttons:
                air_drop_button_x = air_drop_button.area[0] + search_area[0]
                air_drop_button_y = air_drop_button.area[1] + search_area[1]

                visit_button = self.calculate_visit_position(
                    air_drop_button_x, air_drop_button_y
                )
                if not self.check_visit_friend_island(visit_button):
                    continue
                has_clickable_air_drop = True
                return True
            # 如果当前页面所有补给都不可用（全部skip或timeout）
            if not has_clickable_air_drop:
                logger.info("[岛屿-每日补给] 当前页面没有可用补给目标")
                # 检查是否在底部
                if VISIT_SCROLL.at_bottom(main=self) and last_attempt_swipe > 0:
                    last_attempt_swipe -= 1
                    logger.info("[岛屿-每日补给] 滑动槽已在底部，最后尝试滑动")
                    continue
                elif VISIT_SCROLL.at_bottom(main=self) and last_attempt_swipe <= 0:
                    logger.info("[岛屿-每日补给] 滑动槽已在底部，停止搜索")
                    return False
                # 滑动继续查找
                if swipe_count < max_swipe_attempts:
                    logger.info(f"[岛屿-每日补给] 滑动尝试 {swipe_count + 1}/{max_swipe_attempts}")
                    self.visit_swipe(480)
                    swipe_count += 1
                    self.device.sleep(0.5)
                    continue
                else:
                    logger.info("[岛屿-每日补给] 达到最大滑动次数，停止搜索")
                    return False

            if swipe_count < max_swipe_attempts:
                logger.info(
                    f"所有补给尝试失败，滑动尝试 {swipe_count + 1}/{max_swipe_attempts}"
                )
                self.visit_swipe(480)
                swipe_count += 1
                self.device.sleep(0.5)
                continue

        logger.info("[岛屿-每日补给] 未检测到可用补给")
        return False

    def calculate_visit_position(self, air_drop_button_x, air_drop_button_y):
        """根据空投图标位置计算对应的拜访按钮区域。

        Args:
            air_drop_button_x (int): 空投图标左上角 X 坐标。
            air_drop_button_y (int): 空投图标左上角 Y 坐标。

        Returns:
            Button: 计算得到的拜访按钮实例。
        """
        visit_button_x1 = air_drop_button_x + 225  # x偏移
        visit_button_y1 = air_drop_button_y + 25  # y偏移
        visit_button_width = 73  # 960 - 887 = 73
        visit_button_height = 24  # 302 - 278 = 24
        visit_button_x2 = visit_button_x1 + visit_button_width
        visit_button_y2 = visit_button_y1 + visit_button_height

        visit_button = Button(
            area=(visit_button_x1, visit_button_y1, visit_button_x2, visit_button_y2),
            color=(),
            button=(visit_button_x1, visit_button_y1, visit_button_x2, visit_button_y2),
            name="visit_button",
        )
        return visit_button

    def visit_swipe(self, distance):
        """在好友拜访列表中向上滑动并点击停止滑动惯性。

        Args:
            distance (int): 滑动距离像素值。
        """
        stop_button = Button(
            area=(500, 90, 630, 660),
            color=(),
            button=(500, 90, 630, 660),
            name="stop_button",
        )
        self.device.swipe_vector(
            vector=(0, -distance), box=(500, 90, 630, 660), name="VisitSwipe"
        )
        self.device.click(stop_button)
        self.device.click_record_clear()

    def island_air_drop(self):
        """识别日常补给入口并确认结果，分享剩余补给只作为结束状态。

        Returns:
            bool: 已观察到分享状态或领取奖励并回到场景返回 True；
                正常场景暂未找到接收入口返回 False，由调用方继续补滑。

        Pages:
            in: 自己或好友岛屿的补给交互／领取过场。
            out: 自己或好友岛屿场景。
        """
        reward_seen = False
        ready = Timer(0.8, count=2)
        click = Timer(2)
        for _ in self.loop(skip_first=False, timeout=Timer(self.AIR_DROP_TIMEOUT)):
            # 奖励可能覆盖背景分享按钮，先关闭已识别奖励，不能提前结束留住弹窗。
            if (is_daily_supply_reward(self.device.image)
                    or self.appear(GET_ITEMS_ISLAND, offset=(20, 20))
                    or self.appear(ISLAND_GET, offset=30)):
                reward_seen = True
                ready.clear()
                if click.reached():
                    self.device.click(ISLAND_CLICK_SAFE_AREA)
                    click.reset()
                continue
            if self.appear(AIR_DROP_SKIP, offset=(20, 20)):
                ready.clear()
                self.appear_then_click(AIR_DROP_SKIP, offset=(20, 20), interval=2)
                continue
            if self.appear(ISLAND_PHONE_CHECK, offset=1):
                ready.clear()
                self.appear_then_click(ISLAND_PHONE_GOTO_ISLAND, offset=1, interval=2)
                continue
            if self.appear(ISLAND_SEASON_CHECK, offset=1):
                ready.clear()
                self.appear_then_click(ISLAND_SEASON_GOTO_ISLAND, offset=1, interval=2)
                continue
            if self.appear(ISLAND_CHECK) or self.is_in_friend_island():
                # A/B/C 资源均是分享文字，匹配它们只能停止，绝不继续点该位置。
                if self.appear(ISLAND_AIR_DROP_A, offset=(30, 30)):
                    logger.info('[岛屿-每日补给] 已显示分享剩余补给，停止领取，不点击分享')
                    return True
                action = ''.join(str(self.air_drop_action_ocr.ocr(self.device.image) or '').split())
                if action == '分享剩余补给':
                    logger.info('[岛屿-每日补给] 已识别分享剩余补给，停止领取')
                    return True
                # 自己岛屿的领取入口实际显示「日常补给」，保留已核实的
                # 「接收补给」文字兼容；只接受此交互区域中的完整名称。
                if action in ('日常补给', '接收补给'):
                    ready.clear()
                    if click.reached():
                        logger.info(f'[岛屿-每日补给] 已识别{action}，点击补给入口')
                        self.device.click(AIR_DROP_RECEIVE)
                        click.reset()
                    continue
                ready.start()
                if ready.reached():
                    return reward_seen
                continue
            ready.clear()
        self.device.save_screenshot(genre='island_air_drop_unknown', interval=0)
        raise GameStuckError('岛屿补给：领取后页面未确认，已保存现场')

    def run_and_get(self):
        """在好友岛屿上跑位寻路并拾取空投补给。

        规则（方向与时长）走岛屿计划全局配置 `IslandPlan.IslandWalk.AirDrop`，
        对所有控制方式生效；拿不到补给时的补滑走 `AirDropRetry`。
        """
        self.island_walk_route('AirDrop')
        collected = self.island_air_drop()
        # 补滑中途要反复检测补给，方向固定为 上/右/下，配置只给每一步的时长
        retry_durations = self.island_walk_composite_durations('AirDropRetry', 3)
        for move, duration in zip((self.island_up, self.island_right, self.island_down), retry_durations):
            if collected:
                break
            move(duration)
            collected = self.island_air_drop()
        self.exit_friend_island()
        return collected

    def test(self):
        """测试 OCR 参数提取效果。"""
        image = self.device.screenshot()
        area = OCR_AIR_DROP.area if hasattr(OCR_AIR_DROP, "area") else OCR_AIR_DROP
        cropped = crop(image, area)

        # 测试不同参数
        letter = (150, 150, 150)
        threshold = 80
        processed = extract_letters(cropped, letter=letter, threshold=threshold)

        # 显示处理后的图像
        cv2.imshow("Processed OCR", processed)
        cv2.waitKey(0)
        cv2.destroyAllWindows()

    def test1(self):
        """测试进入岛屿管理页面。"""
        self.goto_management()


if __name__ == "__main__":
    az = IslandAirDrop("alas", task="Alas")
    az.device.screenshot()
    az.test1()
