"""岛屿科技定位与有界扫描，几何源自 ALAS 0078abb848de11d4dffb44fc558e82954f7ec682。"""

import cv2
import numpy as np

from module.base.button import ButtonGrid
from module.base.decorator import cached_property
from module.base.mask import Mask
from module.base.timer import Timer
from module.base.utils import area_offset, color_mask, crop, load_image, random_rectangle_vector, rgb2luma
from module.exception import GameStuckError
from module.island.assets import ALAS_TECHNOLOGY_TAB1
from module.island.data import DIC_ISLAND_TECHNOLOGY
from module.island.ui import IslandUI
from module.logger import logger
from module.ui.navbar import Navbar
from module.ui.page import page_island_technology


DELTA_X = 136 + 2 / 3
DELTA_Y = 60
ORIGIN_X = -5 / 3
ORIGIN_Y = 46
LEFT_STRIP = 167
MASK_ISLAND_TECHNOLOGY = Mask('./assets/mask/MASK_ISLAND_TECHNOLOGY.png')
DETECTION_AREA = (167, 54, 1280, 720)
DETECTION_AREA_MASK = (1098, 646, 1280, 720)
BUTTON_AREA = (-110, -26, 110, 26)
MIN_FLOWCHART_SIMILARITY = 0.7
MAX_RESET_SWIPES = 12
MAX_SCAN_SWIPES = 20
SUPPORTED_TABS = (2, 3, 4, 5, 6)


def extract_flowchart(image):
    """提取科技节点和连线轮廓；仅接受框架规定的 1280×720 截图。"""
    if image.shape[:2] != (720, 1280):
        raise GameStuckError(f'科技扫描截图尺寸错误：{image.shape[:2]}')
    brightness_mask = cv2.inRange(rgb2luma(image), 160, 255)
    black_mask = color_mask(image, (7, 10, 17), threshold=10)
    mask = cv2.bitwise_or(brightness_mask, black_mask)
    contours, _ = cv2.findContours(mask.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled_mask = np.zeros_like(mask)
    cv2.drawContours(filled_mask, contours, -1, 255, thickness=cv2.FILLED)
    return MASK_ISLAND_TECHNOLOGY.apply(filled_mask)[:, LEFT_STRIP:]


def get_technology_tab_and_position(index):
    """将官方科技 ID 映射为分类与科技树中的节点中心。"""
    info = DIC_ISLAND_TECHNOLOGY[index]
    axis_x, axis_y = info['axis']
    return info['tech_belong'], (ORIGIN_X + DELTA_X * axis_x, ORIGIN_Y + DELTA_Y * axis_y)


class IslandTechnologyScanner(IslandUI):
    """扫描科技分类 2–6，仅返回实际观察过的科技状态。"""

    @cached_property
    def _island_technology_side_navbar(self):
        grids = ButtonGrid(origin=(13, 107), delta=(0, 196 / 3), button_shape=(128, 43),
                           grid_shape=(1, 5), name='ISLAND_TECHNOLOGY_SIDE_NAVBAR')
        return Navbar(grids=grids, active_color=(30, 143, 255), inactive_color=(50, 52, 55),
                      active_count=500, inactive_count=500)

    @cached_property
    def _technology_charts(self):
        return {tab: load_image(f'./assets/island/technology/technology_chart_{tab}.png')
                for tab in SUPPORTED_TABS}

    def _island_technology_side_navbar_get_active(self):
        """以激活色确认分类；只有总览图标和五个未激活项均可见时才认定分类 1。"""
        active, left, right = self._island_technology_side_navbar.get_info(main=self)
        if active is not None:
            return active + 2
        if left == 0 and right == 4 and self.appear(ALAS_TECHNOLOGY_TAB1, offset=(20, 20)):
            return 1
        return None

    def island_technology_side_navbar_ensure(self, tab=1, skip_first_screenshot=True):
        """切换分类并以激活状态确认；超时交由已有卡死恢复机制处理。

        Pages:
            in: page_island_technology
            out: page_island_technology 的目标分类
        """
        if tab not in (1, *SUPPORTED_TABS):
            raise ValueError(f'不支持的科技分类：{tab}')
        click_timer = Timer(2, count=4)
        for _ in self.loop(skip_first=skip_first_screenshot, timeout=12):
            if not self.appear(page_island_technology.check_button, offset=(20, 20)):
                continue
            if self._island_technology_side_navbar_get_active() == tab:
                return True
            _, left, right = self._island_technology_side_navbar.get_info(main=self)
            if left == 0 and right == 4 and click_timer.reached():
                button = ALAS_TECHNOLOGY_TAB1 if tab == 1 else self._island_technology_side_navbar.grids.buttons[tab - 2]
                self.device.click(button)
                click_timer.reset()
        raise GameStuckError(f'科技分类 {tab} 切换未得到正向确认')

    def get_technology_view_position(self, tab):
        """通过完整科技图定位视口；低相似度不得当成合法偏移继续扫描。"""
        if tab not in SUPPORTED_TABS:
            raise ValueError(f'不支持的科技分类：{tab}')
        chart = self._technology_charts[tab]
        if chart.ndim == 3:
            chart = rgb2luma(chart)
        flowchart = extract_flowchart(self.device.image)
        foreground_ratio = np.count_nonzero(flowchart) / flowchart.size
        if not 0.01 < foreground_ratio < 0.85:
            raise GameStuckError(f'科技分类 {tab} 缺少有效节点轮廓：前景比例 {foreground_ratio:.3f}')
        result = cv2.matchTemplate(chart, flowchart, cv2.TM_CCOEFF_NORMED)
        _, similarity, _, location = cv2.minMaxLoc(result)
        if not np.isfinite(similarity) or similarity < MIN_FLOWCHART_SIMILARITY or location[1] != 0:
            raise GameStuckError(f'科技分类 {tab} 定位不可靠：相似度 {similarity:.3f}，位置 {location}')
        logger.attr(f'TechnologyTab{tab}', f'x={location[0]}, similarity={similarity:.3f}')
        return location[0]

    def _island_technology_swipe(self, forward=True):
        """在科技树有效区域水平拖动，不触碰侧栏和右下遮挡区。"""
        direction = (-600, 0) if forward else (600, 0)
        p1, p2 = random_rectangle_vector(direction, box=(167, 80, 1255, 620),
                                         random_range=(-50, -50, 50, 50), padding=20)
        self.device.drag(p1, p2, segments=2, shake=(0, 25), point_random=(0, 0, 0, 0),
                         shake_random=(0, -5, 0, 5))
        # 合法遍历需要多次拖动；次数另由扫描上限约束，不掩盖按钮点击异常。
        self.device.click_record_remove('DRAG')

    def technology_reset_view(self, skip_first_screenshot=True, tab=None):
        """恢复到科技树最左端；定位确认成功后才结束，最多拖动十二次。"""
        if tab is None:
            tab = self._island_technology_side_navbar_get_active()
        if tab not in SUPPORTED_TABS:
            raise GameStuckError(f'无法确认科技分类：{tab}')
        for attempt in range(MAX_RESET_SWIPES + 1):
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()
            if self.get_technology_view_position(tab) < 3:
                return True
            if attempt < MAX_RESET_SWIPES:
                self._island_technology_swipe(forward=False)
        raise GameStuckError(f'科技分类 {tab} 无法恢复到最左端')

    def _scan_visible_technology(self, positions, position_x, observations):
        """读取完整可见节点；遮挡和画面边缘节点留待后续视口观察。"""
        for index, (tech_x, tech_y) in positions.items():
            screen_x = LEFT_STRIP + tech_x - position_x
            if not DETECTION_AREA[0] - BUTTON_AREA[0] <= screen_x <= DETECTION_AREA[2] - BUTTON_AREA[2]:
                continue
            if tech_y + BUTTON_AREA[3] > DETECTION_AREA_MASK[1] and screen_x + BUTTON_AREA[2] > DETECTION_AREA_MASK[0]:
                continue
            image = crop(self.device.image, area=area_offset(BUTTON_AREA, (screen_x, tech_y)))
            active = bool(np.mean(rgb2luma(image)) > 160)
            observations[index] = observations.get(index, False) or active

    def scan_all(self):
        """有界遍历五个分类，缺少任何节点时抛错，不伪造未解锁状态。"""
        technology_by_tab = {tab: {} for tab in SUPPORTED_TABS}
        for index in DIC_ISLAND_TECHNOLOGY:
            tab, position = get_technology_tab_and_position(index)
            if tab in technology_by_tab:
                technology_by_tab[tab][index] = position
        observations = {}
        for tab, positions in technology_by_tab.items():
            self.island_technology_side_navbar_ensure(tab)
            self.technology_reset_view(tab=tab)
            position_old = None
            stalled = 0
            for attempt, _ in enumerate(self.loop(timeout=60)):
                position_x = self.get_technology_view_position(tab)
                self._scan_visible_technology(positions, position_x, observations)
                missing = positions.keys() - observations.keys()
                if not missing:
                    break
                if position_old is not None:
                    stalled = stalled + 1 if position_x - position_old < 5 else 0
                if stalled >= 3 or attempt >= MAX_SCAN_SWIPES:
                    raise GameStuckError(f'科技分类 {tab} 扫描未覆盖节点：{sorted(missing)}')
                position_old = position_x
                self._island_technology_swipe(forward=True)
            else:
                raise GameStuckError(f'科技分类 {tab} 扫描超时')
        return observations

    def get_technology_status(self):
        """进入科技页并返回 ID→已解锁状态，缓存和保存由生产规划模块管理。

        Pages:
            in: 可导航的岛屿或手机页面
            out: page_island_technology
        """
        self.ui_ensure(page_island_technology)
        return self.scan_all()
