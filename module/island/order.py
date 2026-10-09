"""ALAS 普通、紧急、季节订单流程与本地过滤、确认及调度适配。"""

from datetime import timedelta

import cv2
import numpy as np

import module.config.server as server
from module.base.button import Button, ButtonGrid
from module.base.decorator import cached_property
from module.base.timer import Timer
from module.base.utils import color_similarity_2d
from module.config.time_source import now as current_time
from module.config.utils import get_nearest_weekday_date, get_server_next_update, server_time_offset
from module.exception import GameStuckError
from module.island.data import DIC_ISLAND_ITEM, DIC_ISLAND_SEASON_ORDER
from module.island.island_daily_order import IslandDailyOrder
from module.island.order_detail import get_order_detail_signature, order_detail_changed, same_order_detail
from module.island.order_ocr import OrderDigitCounter, validate_requirements
from module.island.order_selection import get_selected_order_position
from module.island.order_stock import get_menu_reserve_items, get_order_effective_stock, menu_reservations_known
from module.island.planner_report import invalidate_planner_stocks, record_planner_stocks
from module.island.utils import get_active_island_activity_ids, load_hard_floor_items, normalize_item_keys
from module.island_daily_order.assets import (
    ALAS_ORDER_ACCEPT, ALAS_ORDER_BACKGROUND, ALAS_ORDER_COOLDOWN_SPEED_UP,
    ALAS_ORDER_COOLDOWN_TIME, ALAS_ORDER_LEVEL_UP, ALAS_ORDER_REJECT,
    ALAS_ORDER_REQUIREMENTS_CHECK, ALAS_ORDER_URGENT_ACCEPT, DAILY_ORDER_CHECK,
    POPUP_ORDER_CANNOT_REPLACE,
)
from module.island.assets import ISLAND_BACK, ISLAND_CLICK_SAFE_AREA, ISLAND_GET
from module.logger import logger
from module.ocr.ocr import Duration, Ocr
from module.ui.page import page_island, page_island_phone


ORDER_COLORS = {
    'regular': (57, 189, 255),
    'cooldown': (173, 227, 255),
    'season': (255, 173, 27),
    'urgent': (135, 122, 239),
}


def detect_order_circles(image, color, area=(60, 40, 832, 700)):
    """仅在左侧订单区识别 ALAS 的彩色圆环，排除右侧物品图标。"""
    x1, y1, x2, y2 = area
    mask = color_similarity_2d(image[y1:y2, x1:x2], color=color)
    cv2.threshold(mask, 240, 255, cv2.THRESH_BINARY, dst=mask)
    mask = cv2.GaussianBlur(mask, (7, 7), sigmaX=1.5, sigmaY=1.5)
    circles = cv2.HoughCircles(mask, cv2.HOUGH_GRADIENT, dp=1, minDist=88,
                               param1=100, param2=30, minRadius=42, maxRadius=54)
    if circles is None:
        return []
    return sorted([(int(x) + x1, int(y) + y1) for x, y, _ in circles[0]], key=lambda p: (p[1], p[0]))


def get_season_order_id(requirements, time=None):
    """重复季节需求优先对应当前活动；无法唯一对应时保留订单，不猜编号。"""
    if not requirements:
        return None
    request = {item: counter[1] for item, counter in requirements.items()}
    activity_ids = set(get_active_island_activity_ids(time))
    matches = [item for item, data in DIC_ISLAND_SEASON_ORDER.items()
               if data.get('request') == request and data.get('activity_id') in activity_ids]
    return matches[0] if len(matches) == 1 else None


class IslandOrder(IslandDailyOrder):
    """在订单原页连续处理，汇总冷却后按最早时间调度。

    Pages:
        in: page_island / page_island_phone。
        out: page_island_phone。
    """

    # 底部冷却订单的圆心约在 y=600，旧范围裁掉其圆环会漏算下一次刷新。
    LEFT_PANEL_AREA = (60, 40, 832, 700)

    @property
    def requirement_grid(self):
        return ButtonGrid(origin=(884, 240), delta=(0, 79), button_shape=(334, 78),
                          grid_shape=(1, 3), name='ORDER_REQUIREMENTS')

    @cached_property
    def requirement_name_ocr(self):
        area = (81, 12, 328, 40) if server.server == 'en' else (168, 12, 328, 40)
        language = {'jp': 'jp', 'tw': 'tw'}.get(server.server, 'cnocr')
        return Ocr(self.requirement_grid.crop(area).buttons, lang=language,
                   letter=(57, 59, 61), threshold=160, name='ORDER_REQUIREMENTS_NAMES')

    @cached_property
    def requirement_counter_ocr(self):
        area = (238, 44, 326, 70) if server.server == 'en' else (238, 44, 327, 65)
        return OrderDigitCounter(self.requirement_grid.crop(area).buttons)

    def scan_current_order_requirements(self):
        names = self.requirement_name_ocr.ocr(self.device.image)
        counters = self.requirement_counter_ocr.ocr(self.device.image)
        result = validate_requirements(names, counters, DIC_ISLAND_ITEM, server.server)
        if result is None:
            return None
        # 两种 OCR 同时漏掉一个有图标的货物槽时，仍不可把订单当成已读全。
        for name, counter, area in zip(names, counters, self.ITEM_SLOT_AREAS):
            if not str(name).strip() and counter is None:
                icon = self.image_crop(area)
                if float(np.std(icon.astype(float), axis=(0, 1)).max()) > 16:
                    return None
        return result

    def _is_preparing(self):
        return self.appear(ALAS_ORDER_COOLDOWN_SPEED_UP, offset=20) or super()._is_preparing()

    def _handle_popups(self):
        """只在实际点击时返回 True，自动消失的提示由新截图继续观察。"""
        if self.appear_then_click(ALAS_ORDER_LEVEL_UP, offset=20, interval=2):
            return True
        if self.appear(ISLAND_GET, offset=30):
            return self.appear_then_click(ISLAND_GET, offset=30, interval=2)
        return False

    def _submit_order(self, button, must_appear=False):
        """保留严格交付确认，兼容 ALAS 的空白/冷却页与升级模板。"""
        from module.island_daily_order.assets import POPUP_RESOURCE_INSUFFICIENT
        self._submit_needs_reenter = False
        if must_appear and not self.appear(button, offset=20):
            return None
        was_preparing = self._is_preparing()
        self.device.click(button)
        shortage = False
        reward = False
        stable = Timer(self.SUBMIT_STABLE_SECONDS, count=2)
        click = Timer(2)
        for _ in self.loop(skip_first=False, timeout=Timer(self.SUBMIT_CONFIRM_TIMEOUT)):
            if self.appear(POPUP_RESOURCE_INSUFFICIENT, offset=30):
                shortage = True
                stable.clear()
                continue
            if self.appear(ALAS_ORDER_LEVEL_UP, offset=20) or self.appear(ISLAND_GET, offset=30):
                reward = True
                stable.clear()
                if click.reached():
                    self.device.click(ISLAND_CLICK_SAFE_AREA)
                    click.reset()
                continue
            returned = self.appear(DAILY_ORDER_CHECK) and (
                shortage or reward or (not was_preparing and self._is_preparing())
                or self.appear(ALAS_ORDER_BACKGROUND, offset=20))
            if returned:
                stable.start()
                if stable.reached():
                    self._submit_unconfirmed_count = 0
                    return not shortage
            else:
                stable.clear()
        self._submit_needs_reenter = True
        if getattr(self, '_submit_unconfirmed_count', 0):
            self._unknown('重进后仍未确认交付结果')
        self._submit_unconfirmed_count = 1
        return None

    def _enter_daily_order(self):
        # 复用原有入口；状态循环本身不再添加固定等待。
        from module.island_daily_order.assets import ISLAND_PHONE_DAILY_ORDER
        for _ in self.loop(skip_first=False, timeout=Timer(30)):
            if self.appear(DAILY_ORDER_CHECK):
                return
            if self.appear_then_click(ISLAND_PHONE_DAILY_ORDER, interval=2):
                continue
            self._handle_popups()
        self._unknown('无法确认进入订单页')

    def _unknown(self, reason):
        self.device.save_screenshot(genre='island_order_unknown', interval=0)
        raise GameStuckError(f'岛屿订单：{reason}，已保存现场')

    def _back_to_island_phone(self):
        for _ in self.loop(skip_first=False, timeout=Timer(30)):
            if self.ui_page_appear(page_island_phone):
                return
            if self._handle_popups():
                continue
            self.appear_then_click(ISLAND_BACK, interval=2)
        self._unknown('未确认返回岛屿手机')

    def _order_button(self, position):
        x, y = position
        return Button(area=(x - 52, y - 52, x + 52, y + 52), color=(),
                      button=(x - 16, y - 16, x + 16, y + 16), name=f'ORDER_AT_{x}_{y}')

    def detect_all_orders(self):
        orders = {kind: detect_order_circles(self.device.image, color, self.LEFT_PANEL_AREA)
                  for kind, color in ORDER_COLORS.items()}
        # 淡蓝冷却外圈有时仍残留蓝色像素，同一订单不能同时作为可交付订单。
        orders['regular'] = [p for p in orders['regular']
                             if not any(np.linalg.norm(np.subtract(p, q)) < 20
                                        for q in orders['cooldown'])]
        self._order_positions = [position for positions in orders.values() for position in positions]
        return orders

    def _click_order(self, button, kind):
        if not hasattr(self, '_order_positions'):
            self.detect_all_orders()
        # 先记录点击前的静态文字；右侧仍显示上一单时不能仅凭需求页出现放行。
        before_ready = self.appear(DAILY_ORDER_CHECK)
        before = get_order_detail_signature(self.device.image) if server.server == 'cn' and before_ready and (
            self.appear(ALAS_ORDER_REQUIREMENTS_CHECK, offset=20)) else None
        empty_before = before_ready and (
            self.appear(ALAS_ORDER_BACKGROUND, offset=20)
            or self.appear(ALAS_ORDER_COOLDOWN_SPEED_UP, offset=20))
        self.device.click(button)
        target = ((button.area[0] + button.area[2]) // 2, (button.area[1] + button.area[3]) // 2)
        positions = self._order_positions
        stable = Timer(0.5, count=1)
        retry = Timer(2).start()
        candidate = None
        popup_seen = False
        for _ in self.loop(skip_first=False, timeout=Timer(8)):
            if self._handle_popups():
                stable.clear()
                candidate = None
                popup_seen = True
                continue
            cooldown = self.appear(ALAS_ORDER_COOLDOWN_SPEED_UP, offset=20)
            accept = ALAS_ORDER_URGENT_ACCEPT if kind == 'urgent' else ALAS_ORDER_ACCEPT
            detail = self.appear(ALAS_ORDER_REQUIREMENTS_CHECK, offset=20) and self.appear(accept, offset=20)
            page_ready = self.appear(DAILY_ORDER_CHECK)
            after = get_order_detail_signature(self.device.image) if server.server == 'cn' and detail else None
            if popup_seen:
                # 弹窗处理可能改变上一单，重新建立点击前基准后再点目标。
                if page_ready and (detail or cooldown or self.appear(ALAS_ORDER_BACKGROUND, offset=20)):
                    before = after
                    empty_before = cooldown or self.appear(ALAS_ORDER_BACKGROUND, offset=20)
                    popup_seen = False
                    self.device.click(button)
                    retry.reset()
                continue
            selected_position = get_selected_order_position(
                self.device.image, positions, allow_right_occlusion=page_ready and detail,
                allow_dialogue_occlusion=page_ready and detail)
            selected = selected_position is not None and np.linalg.norm(np.subtract(selected_position, target)) <= 12
            other_selected = selected_position is not None and not selected
            changed = after is not None and (empty_before or order_detail_changed(before, after))
            # 详情实际切换可独立确认遮挡订单；明确选中其他订单时仍拒绝旧详情。
            confirmed = selected or (changed and not other_selected)
            if confirmed and page_ready and (cooldown or detail):
                if after is not None and not same_order_detail(candidate, after):
                    stable.clear()
                candidate = after
                stable.start()
                if stable.reached():
                    if not selected:
                        logger.info('[岛屿-订单] 右侧委托人/货物已切换并稳定，确认目标订单')
                    return 'cooldown' if cooldown else 'detail'
            else:
                stable.clear()
                candidate = None
                if page_ready and retry.reached():
                    self.device.click(button)
                    retry.reset()
        if self.appear(DAILY_ORDER_CHECK):
            self.device.save_screenshot(genre='island_order_unknown', interval=0)
            logger.warning('[岛屿-订单] 未完整确认目标选中及详情状态，保留订单，五分钟后复查')
            return 'unconfirmed'
        self._unknown('点击订单后页面未确认')

    def _read_time(self, button):
        try:
            remaining = Duration(button, letter=(57, 59, 61), name='ORDER_REMAIN_TIME').ocr(self.device.image)
            return remaining if timedelta(0) < remaining < timedelta(days=8) else None
        except (TypeError, ValueError):
            return None

    def _record_deadline(self, remaining, fallback=timedelta(hours=1)):
        self.next_runtime.append(current_time() + (remaining or fallback))

    def _reject_order(self):
        """只在正向识别冷却页之后计数，不能替换时保留订单。"""
        clicked = False
        for _ in self.loop(skip_first=False, timeout=Timer(15)):
            if self._handle_popups():
                continue
            if self.appear(POPUP_ORDER_CANNOT_REPLACE, offset=30):
                self._record_deadline(None)
                return False
            if clicked and self.appear(ALAS_ORDER_COOLDOWN_SPEED_UP, offset=20):
                self.reject_count += 1
                self._record_deadline(self._read_time(ALAS_ORDER_COOLDOWN_TIME))
                return True
            if self.appear_then_click(ALAS_ORDER_REJECT, offset=20, interval=2):
                clicked = True
        self._unknown('驳回后未确认冷却状态')

    def _filter_rejects(self, requirements):
        selected = str(self.config.IslandDailyOrder_RejectFilter or '').lower()
        names = {DIC_ISLAND_ITEM[item]['name'].get('en', '').lower() for item in requirements}
        # 本地模板再校验一次，名称表或 OCR 修正也不能绕过用户过滤。
        return (('cheese' in selected and 'cheese' in names)
                or ('tofu' in selected and 'tofu' in names)
                or self._check_items_for_reject(skip_screenshot=True))

    def is_order_satisfied(self, requirements, kind, force=False):
        if not requirements:
            return False
        priority = kind in ('urgent', 'season') or force
        for item, (stock, required, _) in requirements.items():
            usable = get_order_effective_stock(stock, self.hard_floor.get(item, 0),
                                               self.reserve.get(item, 0), priority=priority)
            if required > usable:
                logger.info(f'[岛屿-订单] 库存不足：{item} 现货={stock} 可用={usable} 需求={required}')
                return False
        return True

    def _process_order(self, position, kind):
        button = self._order_button(position)
        state = self._click_order(button, kind)
        if state == 'unconfirmed':
            self._record_deadline(timedelta(minutes=5))
            return False
        if state == 'cooldown':
            self._record_deadline(self._read_time(ALAS_ORDER_COOLDOWN_TIME))
            return False
        requirements = None
        for _ in range(3):
            requirements = self.scan_current_order_requirements()
            if requirements:
                break
            self.device.screenshot()
            if not self.appear(ALAS_ORDER_REQUIREMENTS_CHECK, offset=20):
                self._unknown('读取货物时离开需求页')
        if not requirements:
            self.device.save_screenshot(genre='island_order_unknown', interval=0)
            logger.warning('[岛屿-订单] 需求未完整识别，保留订单，五分钟后复查')
            self._record_deadline(timedelta(minutes=5))
            return False
        record_planner_stocks(self.config, {item: counter[0] for item, counter in requirements.items()}, '订单需求页')
        if kind == 'regular' and self._filter_rejects(requirements):
            return self._reject_order()
        force = kind == 'regular' and (
            get_server_next_update(self.config.Scheduler_ServerUpdate) - current_time() <= timedelta(hours=2))
        if kind == 'regular' and not force and not getattr(self, 'reserve_known', True):
            logger.warning('[岛屿-订单] 未生成规划且手工经营菜单全空，无法确认预留，保留普通订单')
            self._record_deadline(timedelta(minutes=5))
            return False
        if self.is_order_satisfied(requirements, kind, force):
            accept = ALAS_ORDER_URGENT_ACCEPT if kind == 'urgent' else ALAS_ORDER_ACCEPT
            result = self._submit_order(accept, must_appear=True)
            if result is None:
                self._reenter()
                return True
            if result:
                invalidate_planner_stocks(self.config, requirements, '订单交付后')
                return True
        if kind == 'regular':
            return self._reject_order()
        if kind == 'urgent':
            remaining = self._read_time(button.crop((10, 119, 100, 145)))
            deadline = current_time() + (remaining or timedelta(hours=8))
            self.config.IslandDailyOrder_UrgentDetectRefreshTime = deadline
            self.next_runtime.append(deadline)
        else:
            season_id = get_season_order_id(requirements, current_time() - server_time_offset())
            if season_id is not None:
                self.stuck_season_order_id = season_id
                completed = self.config.cross_get(
                    'IslandPlan.IslandProductionPlanner.CompletedManufactureOrderId', 0)
                if completed == season_id:
                    from module.island.production_planner import manufacture_order_targets
                    _, final = manufacture_order_targets(season_id)
                    if any(requirements.get(item, (0, 0, 0))[0] < target for item, target in final.items()):
                        self.config.cross_set('IslandPlan.IslandProductionPlanner.CompletedManufactureOrderId', 0)
                        self._needs_production_plan = True
            else:
                logger.warning('[岛屿-订单] 季节需求无法对应当前活动，保留原规划，不猜订单编号')
                self._record_deadline(timedelta(hours=1))
        return False

    def run(self):
        logger.hr('岛屿订单：ALAS 流程', level=1)
        self.ui_ensure(page_island)
        self.ui_goto(page_island_phone, get_ship=False)
        self.device.screenshot()
        refresh = self._get_urgent_refresh_time()
        if not refresh or current_time() >= refresh:
            remaining = self._ocr_urgent_remaining()
            if remaining == 0:
                self.config.IslandDailyOrder_UrgentDetectRefreshTime = get_nearest_weekday_date(0)
            elif remaining is not None:
                self.config.IslandDailyOrder_UrgentDetectRefreshTime = self.DEFAULT_URGENT_REFRESH_TIME
        self.reject_count = self.config.IslandDailyOrder_RejectCount
        self._submit_unconfirmed_count = 0
        self._needs_production_plan = False
        self.next_runtime = []
        refresh = self._get_urgent_refresh_time()
        if refresh and refresh > current_time():
            self.next_runtime.append(refresh)
        self.stuck_season_order_id = int(getattr(self.config, 'IslandDailyOrder_StuckSeasonOrderId', 0) or 0)
        old_season_id = self.stuck_season_order_id
        self.hard_floor = normalize_item_keys(load_hard_floor_items(
            self.config.cross_get('IslandPlan.IslandProductionPlanner.HardFloorItems', '{}')))
        self.reserve = get_menu_reserve_items(self.config)
        self.reserve_known = menu_reservations_known(self.config)
        self._enter_daily_order()
        for _ in range(100):
            self.device.screenshot()
            if not self.appear(DAILY_ORDER_CHECK):
                if self._handle_popups():
                    continue
                self._unknown('扫描订单时页面未确认')
            orders = self.detect_all_orders()
            handled = False
            refresh = self._get_urgent_refresh_time()
            for kind in ('urgent', 'regular', 'season'):
                if kind == 'urgent' and refresh and current_time() < refresh:
                    continue
                for position in orders[kind]:
                    if self._process_order(position, kind):
                        handled = True
                        break
                if handled:
                    break
            if handled:
                continue
            if any(orders.values()) and not orders['season']:
                self.stuck_season_order_id = 0
            for position in orders['cooldown']:
                button = self._order_button(position)
                remaining = self._read_time(button.crop((10, 119, 100, 145)))
                if remaining is None:
                    state = self._click_order(button, 'regular')
                    if state == 'cooldown':
                        remaining = self._read_time(ALAS_ORDER_COOLDOWN_TIME)
                    else:
                        self._record_deadline(timedelta(minutes=5))
                        continue
                self._record_deadline(remaining)
            if not any(orders.values()):
                # 圆环完全未检出不等价于全图无订单，短延后避免直接漏到次日。
                self._record_deadline(timedelta(minutes=5))
            break
        else:
            self._unknown('订单连续处理次数异常')
        self._back_to_island_phone()
        with self.config.multi_set():
            self.config.IslandDailyOrder_RejectCount = self.reject_count
            self.config.IslandDailyOrder_StuckSeasonOrderId = self.stuck_season_order_id
            if not self.stuck_season_order_id:
                self.config.cross_set('IslandPlan.IslandProductionPlanner.CompletedManufactureOrderId', 0)
        from module.island.production_planner import IslandPlanningError, IslandProductionPlanner
        if self._needs_production_plan or old_season_id != self.stuck_season_order_id or not self.config.cross_get(
                'IslandPlan.IslandProductionPlanner.PlanFingerprint', ''):
            try:
                IslandProductionPlanner(self.config, self.device).run(stuck_season_order_id=self.stuck_season_order_id)
            except IslandPlanningError as exc:
                logger.warning(f'[岛屿-订单] 规划未更新，保留原计划及季节订单：{exc}')
                self._record_deadline(timedelta(hours=1))
        self.ui_ensure(page_island_phone)
        if self.next_runtime:
            self.config.task_delay(target=min(self.next_runtime), server_update=True)
        else:
            self.config.task_delay(server_update=True)
        logger.info('[岛屿-订单] 完成，按最早冷却/每日刷新调度并应用全局对齐')
