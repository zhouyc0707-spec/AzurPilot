"""统一收获后按产地读取仓库列表；不打开物品详情或生产选品页。"""

from collections import defaultdict
from dataclasses import dataclass
import re
import unicodedata

import numpy as np

import module.config.server as server
from module.base.button import Button
from module.base.timer import Timer
from module.config.config import TaskEnd
from module.exception import GameStuckError
from module.island.data import DIC_ISLAND_ITEM
from module.island.item_ids import resolve_item_id
from module.island.warehouse import WarehouseQuantityDigit
from module.logger import logger
from module.ocr.ocr import Ocr
from module.ui.assets import ISLAND_WAREHOUSE_CHECK


LIST_AREA = (290, 140, 1215, 575)
UNKNOWN = (-1, None)


def _text(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value)))


def warehouse_catalog(runners):
    """从现有任务目录收集图标及产地，补上跨任务使用的牧产品等材料。"""
    from module.island.assets import TEMPLATE_MILK
    from module.island_teahouse.assets import TEMPLATE_FRESH_HONEY

    groups = defaultdict(dict)

    def add(source, entries):
        for entry in entries:
            item = resolve_item_id(entry['name'])
            templates = groups[source].setdefault(item, [])
            template = entry.get('template')
            # 旧定义曾将文件柜图用于滤芯，不能把它作为滤芯库存证据。
            if template is not None and entry['name'] != 'filter_element' and template not in templates:
                templates.append(template)

    for runner in runners:
        for attribute in ('INVENTORY_CONFIG', 'inventory_config'):
            for group in getattr(runner, attribute, {}).values():
                if group.get('filter'):
                    add(group['filter'], group['items'])
        if getattr(runner, 'filter_asset', None):
            add(runner.filter_asset, getattr(runner, 'shop_items', ()))
        if hasattr(runner, 'FISHERY_ITEMS'):
            add('fishery', runner.FISHERY_ITEMS)
        fishery = getattr(runner, '_round_fishery', None)
        if fishery is not None:
            add('fishery', fishery.FISHERY_ITEMS)
    add('ranch', [{'name': item, 'template': TEMPLATE_MILK if item == 2603 else None}
                  for item in range(2600, 2606)])
    # 煤炭属于沉石矿山；旧矿林手工目录未列入，但制造配方会使用。
    add('mine', [{'name': 2700}])
    # 鱼肉由兑换获得，仓库产地归在“其他”，不能按渔场筛选。
    add('other_from', [{'name': item} for item in (2521, 2522)])
    add('other_from', [{'name': 2606, 'template': TEMPLATE_FRESH_HONEY}])
    return dict(groups)


@dataclass(frozen=True)
class WarehousePage:
    """完整可见行的逐格读数；空格为 None，无法识别的卡片为 UNKNOWN。"""

    rows: tuple
    positions: tuple
    complete: bool
    bottom_partial: bool = False


def _blank_cell(image, x, title_y):
    """仅把没有图标、边框或文字的平坦明亮背景作为空格。"""
    tile = image[title_y - 108:title_y + 28, x:x + 104]
    if tile.shape != (136, 104, 3):
        return False
    return _blank_background(tile)


def _blank_background(tile):
    pixels = tile.reshape(-1, 3)
    # 两张实屏空格中位数 241–250、各通道 1%–99% 范围不超过 16；暗蒙层不认空。
    return bool(np.min(np.median(pixels, axis=0)) >= 225
                and np.max(np.percentile(pixels, 99, axis=0)
                           - np.percentile(pixels, 1, axis=0)) <= 20)


def read_warehouse_page(main, templates):
    """按完整名称及现有图标识别可见卡片，一批读取数字，空 OCR 保持未知。"""
    image = main.device.image
    if (getattr(image, 'shape', None) != (720, 1280, 3)
            or not main.match_template_color(ISLAND_WAREHOUSE_CHECK, offset=(30, 30),
                                              similarity=0.85, threshold=30)):
        return None
    language = server.server
    names = {_text(data['name'][language]): item for item, data in DIC_ISLAND_ITEM.items()}
    detections = Ocr([], lang=language if language in ('jp', 'tw') else 'cnocr',
                     name='ROUND_WAREHOUSE_NAMES').cnocr.det(image)
    labels = []
    for value, box, score in detections:
        left, top = min(point[0] for point in box), min(point[1] for point in box)
        right, bottom = max(point[0] for point in box), max(point[1] for point in box)
        if float(score) < 0.90 or not (290 <= left < right <= 1215 and 140 <= top < bottom <= LIST_AREA[3]):
            continue
        value = _text(value)
        item = names.get(value)
        if item is not None:
            labels.append((item, int(round((left + right) / 2)), int(round(top))))
    # 标题位置决定当前滚动相位，不能将翻页后的卡片强塞进首屏固定网格。
    anchors = []
    for _, _, y in sorted(labels, key=lambda entry: entry[2]):
        if not anchors or y - anchors[-1] > 12:
            anchors.append(y)
    if not anchors:
        anchors = [261, 428]
    else:
        phase = anchors[0]
        predicted = [phase + offset * 167 for offset in range(-3, 4)]
        anchors = [min(anchors, key=lambda value: abs(value - y))
                   if min(abs(value - y) for value in anchors) <= 12 else y
                   for y in predicted if LIST_AREA[1] <= y - 111 and y + 28 <= LIST_AREA[3]]
    rows, positions, areas, links = [], [], [], []
    complete = True
    for y in anchors:
        if y - 111 < LIST_AREA[1] or y + 28 > LIST_AREA[3]:
            continue
        row = []
        for column in range(6):
            x = 301 + 142 * column
            named = {item for item, cx, cy in labels if x - 5 <= cx < x + 137 and abs(cy - y) <= 12}
            tile = image[y - 111:y - 1, x:x + 104]
            if not named:
                matched = {item for item, variants in templates.items()
                           if any(template.match(tile, similarity=0.85, direct_match=True)
                                  for template in variants)}
                if matched:
                    named = matched
            if len(named) == 1:
                item = named.pop()
                row.append((item, None))
                areas.append(Button(area=(x + 45, y - 25, x + 99, y + 1), color=(),
                                    button=(x + 45, y - 25, x + 99, y + 1), name='ROUND_WAREHOUSE_NUMBER'))
                links.append((len(rows), len(row) - 1, item))
            elif not named and _blank_cell(image, x, y):
                row.append(None)
            else:
                row.append(UNKNOWN)
                complete = False
        rows.append(row)
        positions.append(y)
    if not rows:
        return WarehousePage((), (), False)
    if areas:
        digit = WarehouseQuantityDigit(areas, letter=(255, 255, 255), threshold=200,
                                       alphabet='0123456789', name='ROUND_WAREHOUSE_NUMBERS')
        quantities = digit.ocr(image)
        if not isinstance(quantities, list):
            quantities = [quantities]
        validity = digit.region_validity
        for index, (row, column, item) in enumerate(links):
            if (index < len(quantities) and index < len(validity) and validity[index]
                    and isinstance(quantities[index], (int, np.integer))
                    and not isinstance(quantities[index], (bool, np.bool_)) and quantities[index] >= 0):
                rows[row][column] = item, int(quantities[index])
    following_top = positions[-1] + 167 - 111
    partial = (following_top < LIST_AREA[3] - 15
               and any(not _blank_background(image[following_top:LIST_AREA[3], x:x + 104])
                       for x in (301 + 142 * column for column in range(6))))
    return WarehousePage(tuple(tuple(row) for row in rows), tuple(positions), complete, partial)


def stocks_from_rows(rows, requested, complete):
    """保留当前页明确读数；只有完整空白/完整列表才把缺项确认为零。"""
    stocks, counts, unknown = defaultdict(int), defaultdict(int), set()
    for row in rows:
        for cell in row:
            if cell is None:
                continue
            if cell == UNKNOWN:
                complete = False
                continue
            item, quantity = cell
            counts[item] += 1
            if quantity is None:
                unknown.add(item)
            else:
                stocks[item] += quantity
    # 没读完整时，同名多格不能据可见部分推断全部分堆之和。
    unknown.update(item for item, count in counts.items() if count > 1 and not complete)
    return {item: stocks.get(item, 0) for item in requested
            if item not in unknown and (item in stocks or complete)}


class RoundWarehouseReader:
    """每个产地筛选一次，只读取稳定的当前页。"""

    def __init__(self, main):
        self.main = main

    def stable_page(self, templates):
        previous = None
        for _ in self.main.loop(skip_first=False, timeout=Timer(12)):
            event = getattr(getattr(self.main, 'config', None), 'stop_event', None)
            if event is not None and event.is_set():
                raise TaskEnd('仓库集中盘点期间收到停止请求')
            page = read_warehouse_page(self.main, templates)
            if page is not None and page == previous:
                return page
            previous = page
        raise GameStuckError('仓库筛选后的列表未稳定，未建立本轮库存')

    def read_group(self, source, templates, requested):
        self.main.warehouse_filter(*(source if isinstance(source, tuple) else (source,)))
        page = self.stable_page(templates)
        return stocks_from_rows(page.rows, requested, page.complete and not page.bottom_partial)


def read_round_warehouse_stocks(main, runners, items):
    """统一收获后按需求并集盘点一次，返回物品编号到明确库存的映射。

    Pages:
        in: 岗位管理页。
        out: 岗位管理页，所有后续任务可直接复用库存记忆。
    """
    requested = {resolve_item_id(item) for item in items}
    if not requested:
        return {}
    main.goto_postmanage()
    if not main.post_close():
        raise GameStuckError('集中盘点前未能关闭岗位详情')
    groups = warehouse_catalog(runners)
    reader = RoundWarehouseReader(main)
    result = {}
    for source, templates in groups.items():
        wanted = requested & templates.keys()
        if wanted:
            logger.info(f'[岛屿仓库] 按 {source} 筛选，读取本轮 {len(wanted)} 项库存')
            result.update(reader.read_group(source, templates, wanted))
    main.goto_postmanage()
    if not main.post_close():
        raise GameStuckError('集中盘点后未能返回岗位管理页')
    missing = requested - result.keys()
    if missing:
        logger.warning(f'[岛屿仓库] 未明确识别的库存保留未知：{sorted(missing)}')
    return result
