"""同一任务即时查看仓库详情，未知库存不转换为零，不保存跨任务缓存。"""

import re
import unicodedata

import module.config.server as server
from module.base.button import Button
from module.base.timer import Timer
from module.exception import GameStuckError
from module.island.assets import ISLAND_INFO_EXIT
from module.island.data import DIC_ISLAND_ITEM
from module.island.item_ids import resolve_item_id
from module.island.warehouse import WarehouseOCR
from module.logger import logger
from module.ocr.ocr import Ocr
from module.ui.assets import ISLAND_WAREHOUSE_CHECK


DETAIL_TITLE = {'cn': '详情', 'en': 'Details', 'jp': '詳細', 'tw': '詳情'}
OWN_LABEL = {'cn': '已拥有:', 'en': 'Owned:', 'jp': '所持中:', 'tw': '已擁有:'}
DETAIL_TITLE_AREA = (330, 130, 760, 210)
DETAIL_ITEM_AREA = (480, 220, 940, 340)
WAREHOUSE_TEXT_AREA = (280, 130, 1230, 550)
MAX_WAREHOUSE_SWIPES = 8
ROUND_WAREHOUSE_SWIPES = 64
RANCH_ITEM_IDS = frozenset((2600, 2601, 2602, 2603, 2604, 2605))


def normalize_probe_text(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(text)))


def _rect(box):
    return min(x for x, _ in box), min(y for _, y in box), max(x for x, _ in box), max(y for _, y in box)


def _texts(detections):
    return [(normalize_probe_text(text), _rect(box)) for text, box, score in detections if score >= 0.90]


def _inside(rect, area):
    return area[0] <= rect[0] < rect[2] <= area[2] and area[1] <= rect[1] < rect[3] <= area[3]


def parse_item_detail_stock(detections, item_id, language='cn'):
    """解析游戏详情的完整物品名与“已拥有”计数；明确的 0 可返回，未知为 None。

    标题、物品名和持有量均限定在详情弹窗区域；名称与持有量处于同一行。
    持有计数有两条、名字重复或分开的数字无法唯一关联时拒绝读取。
    """
    if language not in DETAIL_TITLE or item_id not in DIC_ISLAND_ITEM:
        raise ValueError(f'未知物品或语言：{item_id}/{language}')
    texts = _texts(detections)
    title_names = {normalize_probe_text(DETAIL_TITLE[language])}
    if language == 'cn':
        # 真实详情页的蓝色竖线会被识别成 I，仅在标题区域接受这个已核实的整词。
        title_names.add('I详情')
    titles = [rect for text, rect in texts if text in title_names and _inside(rect, DETAIL_TITLE_AREA)]
    if len(titles) != 1:
        return None
    label = normalize_probe_text(OWN_LABEL[language])
    own = []
    for text, rect in texts:
        if not _inside(rect, DETAIL_ITEM_AREA):
            continue
        match = re.fullmatch(re.escape(label) + r'(\d+)', text)
        if match:
            own.append((int(match[1]), rect))
        elif text == label:
            numbers = [(int(value), area) for value, area in texts if re.fullmatch(r'\d+', value)
                       and _inside(area, DETAIL_ITEM_AREA)
                       and rect[2] <= area[0] <= rect[2] + 120
                       and abs((area[1] + area[3] - rect[1] - rect[3]) / 2) <= 8]
            if len(numbers) == 1:
                count, number_area = numbers[0]
                own.append((count, (rect[0], min(rect[1], number_area[1]), number_area[2],
                                    max(rect[3], number_area[3]))))
    if len(own) != 1:
        return None
    count, area = own[0]
    expected = normalize_probe_text(DIC_ISLAND_ITEM[item_id]['name'][language])
    names = [rect for text, rect in texts if text == expected
             and _inside(rect, DETAIL_ITEM_AREA) and rect[2] <= area[0]
             and abs((rect[1] + rect[3] - area[1] - area[3]) / 2) <= 20]
    if len(names) != 1:
        return None
    return count


def find_warehouse_item_cards(detections, item_ids, language='cn'):
    """在已知仓库网格中按完整名称定位卡片，不凭陌生图标判断物品。"""
    names = {normalize_probe_text(DIC_ISLAND_ITEM[item]['name'][language]): item for item in item_ids}
    warehouse = WarehouseOCR()
    cells = [button.area for _, _, button in warehouse.warehouse_grid.generate()]
    result = {}
    for name, rect in _texts(detections):
        item = names.get(name)
        if item is None:
            continue
        cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
        # 图标本体 104×110，名称条处于其下；整格的间距沿用仓库现有网格。
        if not any(x1 <= cx <= x1 + 142 and y1 <= cy < y1 + 167 for x1, y1, _, _ in cells):
            continue
        # 分堆时同一名称可出现多张卡。详情显示 GetOwnCount 总库存，任取一张即可。
        result.setdefault(item, rect)
    return result


class StockProbeSession:
    """只在本次调用内保存观测值，所有页面操作均为查看和关闭。"""

    def __init__(self, main):
        self.main = main
        self.language = server.server
        self.ocr = Ocr([], lang=self.language if self.language in ('jp', 'tw') else 'cnocr',
                       name='ISLAND_STOCK_PROBE')

    def _detect(self):
        return self.ocr.cnocr.det(self.main.device.image)

    def _warehouse_view(self):
        # 此资源是筛选图标，游戏 VIEW 模式才显示 sort_panel；编辑出售模式不操作卡片。
        return self.main.match_template_color(ISLAND_WAREHOUSE_CHECK, offset=(30, 30),
                                              similarity=0.85, threshold=30)

    def _unknown(self, reason):
        self.main.device.save_screenshot(genre='island_stock_probe_unknown', interval=0)
        raise GameStuckError(f'岛屿库存只读观察未能确认页面：{reason}')

    def _wait_warehouse(self):
        last = None
        for _ in self.main.loop(skip_first=False, timeout=Timer(10)):
            if not self._warehouse_view():
                continue
            detections = self._detect()
            signature = tuple((text, tuple(round(value) for value in rect))
                              for text, rect in _texts(detections)
                              if WAREHOUSE_TEXT_AREA[0] <= rect[0] <= WAREHOUSE_TEXT_AREA[2]
                              and WAREHOUSE_TEXT_AREA[1] <= rect[1] <= WAREHOUSE_TEXT_AREA[3])
            if signature == last:
                return detections
            last = signature
        self._unknown('仓库未返回查看模式或列表仍在滚动')

    @staticmethod
    def _signature(detections):
        return tuple(sorted((text, tuple(round(value) for value in rect))
                            for text, rect in _texts(detections)
                            if WAREHOUSE_TEXT_AREA[0] <= rect[0] <= WAREHOUSE_TEXT_AREA[2]
                            and WAREHOUSE_TEXT_AREA[1] <= rect[1] <= WAREHOUSE_TEXT_AREA[3]))

    def _rewind(self, max_swipes):
        """筛选不会保证滚动条归顶；以稳定视窗确认上边界，保持有界。"""
        previous = self._signature(self._wait_warehouse())
        for _ in range(max_swipes):
            self.main.device.swipe_vector(vector=(0, 260), box=(310, 154, 1150, 485),
                                          duration=(0.3, 0.5), name='STOCK_PROBE_WAREHOUSE_TOP')
            current = self._signature(self._wait_warehouse())
            if current == previous:
                return
            previous = current
        logger.warning('[岛屿-库存观察] 仓库归顶达到滑动上限，未观察到的物品仍保留未知')

    def _close_detail(self):
        for _ in self.main.loop(skip_first=False, timeout=Timer(10)):
            # 关闭不依赖文字识别成功；漏点时按冷却重试，下一帧确认真正回到仓库。
            if self.main.match_template_color(ISLAND_INFO_EXIT, offset=(30, 30),
                                               similarity=0.85, threshold=30):
                self.main.appear_then_click(ISLAND_INFO_EXIT, offset=(30, 30), interval=2)
                continue
            if self._warehouse_view():
                return
        self._unknown('详情未安全关闭')

    def _read_card(self, item_id):
        clicked = False
        previous = None
        result = None
        for _ in self.main.loop(skip_first=False, timeout=Timer(12)):
            detections = self._detect()
            if clicked:
                quantity = parse_item_detail_stock(detections, item_id, self.language)
                if quantity is not None and quantity == previous:
                    result = quantity
                    break
                previous = quantity
                continue
            if self._warehouse_view():
                card = find_warehouse_item_cards(detections, (item_id,), self.language).get(item_id)
                if card is None:
                    return None
                self.main.device.click(Button(area=card, color=(), button=card,
                                              name=f'STOCK_PROBE_ITEM_{item_id}'))
                clicked = True
        if clicked:
            self._close_detail()
        return result

    def run(self, item_ids, max_swipes=MAX_WAREHOUSE_SWIPES, reset_to_top=False):
        """有限遍历当前仓库；遗漏的物品保留未知，绝不声称库存为零。"""
        filter_from = 'ranch' if set(item_ids) <= RANCH_ITEM_IDS else 'all_from'
        self.main.warehouse_filter('all_kind', filter_from)
        if reset_to_top:
            self._rewind(max_swipes)
        observed = {}
        previous_signature = None
        for page in range(max_swipes + 1):
            detections = self._wait_warehouse()
            # 同一视窗的未知详情不反复打开。重复只停止搜索，不把缺项当成零。
            signature = self._signature(detections)
            if signature == previous_signature:
                break
            cards = find_warehouse_item_cards(detections, set(item_ids) - set(observed), self.language)
            for item in cards:
                quantity = self._read_card(item)
                if quantity is not None:
                    observed[item] = quantity
            if len(observed) == len(item_ids):
                return observed
            if page == max_swipes:
                break
            previous_signature = signature
            self.main.device.swipe_vector(vector=(0, -260), box=(310, 154, 1150, 485),
                                          duration=(0.3, 0.5), name='STOCK_PROBE_WAREHOUSE')
        missing = sorted(set(item_ids) - set(observed))
        if missing:
            self.main.device.save_screenshot(genre='island_stock_probe_unknown', interval=0)
            logger.warning(f'[岛屿-库存观察] 未取得明确库存的物品 {missing}，保留未知供配方页复读')
        return observed


def read_item_stocks(main, item_ids, *, max_swipes=MAX_WAREHOUSE_SWIPES, reset_to_top=False):
    """取得当前任务最新、明确的物品总库存；未观测物品不出现在返回字典。

    缺项的 result.get(item_id) 为 None，只有详情明确显示已拥有 0 才返回 0。
    调用者可以在同一任务使用下游配方材料页复读缺项，不能用陈旧缓存补齐。

    Pages:
        in: 可导航的岛屿页面。
        out: 仓库 VIEW 页面，所有详情已关闭。
    """
    normalized = tuple(dict.fromkeys(resolve_item_id(item) for item in item_ids))
    if not normalized:
        return {}
    observed = StockProbeSession(main).run(normalized, max_swipes=max_swipes, reset_to_top=reset_to_top)
    return observed
