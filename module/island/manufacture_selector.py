"""按真实配方卡片定位与读取库存，几何源自 ALAS 0078abb848de11d4dffb44fc558e82954f7ec682。"""

import re
import unicodedata

import cv2
import numpy as np

import module.config.server as server
from module.base.button import Button
from module.base.timer import Timer
from module.base.utils import area_offset, color_mask, color_similarity_2d, crop, extract_letters
from module.island.data import DIC_ISLAND_ITEM, DIC_ISLAND_RECIPE, DIC_ISLAND_SLOT
from module.island.manufacture_catalog import get_catalog
from module.island.planner_report import record_planner_stocks
from module.island.recipe_groups import GROUP_TO_PLACE, SEASONAL_RECIPE_GROUPS
from module.island_manufacture.assets import (
    ALAS_RECIPE_AMOUNT, ALAS_RECIPE_AMOUNT_MAX, ALAS_RECIPE_AMOUNT_MINUS, ALAS_RECIPE_AMOUNT_PLUS,
    ALAS_RECIPE_CHECK, TEMPLATE_ALAS_RECIPE_ANCHOR,
)
from module.logger import logger
from module.ocr.ocr import Ocr


RECIPE_SIZE = (280, 134)
RECIPE_DETECT_AREA = (181, 55, 460, 668)
RECIPE_ANCHOR_AREA = (58, 97, 102, 115)
RECIPE_PRODUCT_NAME_AREA = (123, 23, 269, 46)
RECIPE_PRODUCT_STOCK_AREA = (212, 92, 275, 110)
# 实际数量页的 MAX 可能相对模板横移 1px；小范围容差保留原相似度要求。
RECIPE_AMOUNT_MAX_OFFSET = (3, 20)
MAX_RECIPE_SWIPES = 16
SWIPE_NAME = 'MANUFACTURE_RECIPE_SEARCH'


def _normalize_name(value):
    return re.sub(r'[\W_]+', '', unicodedata.normalize('NFKC', str(value)).casefold())


def _recipe_ids_in_same_category(recipe_id):
    for recipes in get_catalog().values():
        ids = [entry['recipe_id'] for entry in recipes]
        if recipe_id in ids:
            return ids
    places = {slot['place'] for slot in DIC_ISLAND_SLOT.values()
              if recipe_id in slot['formula'] + slot['activity_formula']}
    if recipe_id in SEASONAL_RECIPE_GROUPS:
        places.add(GROUP_TO_PLACE[SEASONAL_RECIPE_GROUPS[recipe_id]])
    if len(places) == 1:
        # 固定版本岗位数据仅列当季配方；历季名称也属于同页识别范围，解锁仍由规划器判断。
        ids = {rid for slot in DIC_ISLAND_SLOT.values() if slot['place'] in places
               for rid in slot['formula'] + slot['activity_formula']}
        ids.update(rid for rid, group in SEASONAL_RECIPE_GROUPS.items() if GROUP_TO_PLACE[group] in places)
        return sorted(ids)
    raise ValueError(f'配方无法唯一对应生产场所：{recipe_id}')


def match_recipe_name(name, recipe_ids, language):
    """只接受标准化后的唯一全名；未知或同名不选择最近的商品。"""
    normalized = _normalize_name(name)
    if not normalized:
        return None
    matches = []
    for recipe_id in recipe_ids:
        product_id = next(iter(DIC_ISLAND_RECIPE[recipe_id]['commission_product']))
        expected = DIC_ISLAND_ITEM[product_id]['name'][language]
        if normalized == _normalize_name(expected):
            matches.append(recipe_id)
    return matches[0] if len(matches) == 1 else None


def _selected_recipe_tops(main):
    """用完整卡片蓝框补足选中后失真的乘号锚点，排除内部图标和截断行。"""
    left, top, _, bottom = RECIPE_DETECT_AREA
    width, height = RECIPE_SIZE
    mask = color_mask(crop(main.device.image, (left, top, left + width, bottom)),
                      (57, 189, 255), threshold=30)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    tops = []
    for contour in contours:
        x, y, detected_width, detected_height = cv2.boundingRect(contour)
        if (x <= 2 and abs(detected_width - width) <= 2 and abs(detected_height - height) <= 2
                and y + height <= bottom - top):
            tops.append(top + y)
    return tops


def _read_recipe_rows(main, recipe_ids):
    """合并官方乘号锚点和完整选中蓝框；仍用同帧完整商品名确认身份。"""
    anchor_area = (RECIPE_DETECT_AREA[0] + RECIPE_ANCHOR_AREA[0],
                   RECIPE_DETECT_AREA[1] + RECIPE_ANCHOR_AREA[1],
                   RECIPE_DETECT_AREA[2] - RECIPE_SIZE[0] + RECIPE_ANCHOR_AREA[2],
                   RECIPE_DETECT_AREA[3] - RECIPE_SIZE[1] + RECIPE_ANCHOR_AREA[3])
    image = crop(main.device.image, anchor_area)
    anchors = TEMPLATE_ALAS_RECIPE_ANCHOR.match_multi(image, similarity=0.75, threshold=5)
    tops = []
    candidates = [anchor.area[1] + RECIPE_DETECT_AREA[1] for anchor in anchors]
    candidates.extend(_selected_recipe_tops(main))
    for top in sorted(candidates):
        if not tops or top - tops[-1] > 5:
            tops.append(top)
    if not 1 <= len(tops) <= 4:
        return []
    if any(abs(second - first - 149) > 8 for first, second in zip(tops, tops[1:])):
        return []
    buttons = [Button(area=(181, top, 461, top + 134), color=(),
                      button=(181, top, 461, top + 134), name=f'MANUFACTURE_RECIPE_ROW_{index}')
               for index, top in enumerate(tops)]
    language = server.server
    lang = language if language in ('jp', 'tw') else 'cnocr'
    ocr = Ocr([], lang=lang, letter=(57, 59, 61), threshold=160, name='MANUFACTURE_RECIPE_NAME')
    names = ocr.ocr([crop(main.device.image, area_offset(RECIPE_PRODUCT_NAME_AREA, button.area[:2]))
                     for button in buttons], direct_ocr=True)
    if len(names) != len(buttons):
        return []
    return [(match_recipe_name(name, recipe_ids, language), button) for name, button in zip(names, buttons)]


def _is_selected(main, button):
    """沿用官方蓝色边框确认，卡片内部图标颜色不能冒充选中标记。"""
    mask = color_mask(crop(main.device.image, button.area), (57, 189, 255), threshold=30)
    mask[2:-2, 2:-2] = 0
    return cv2.countNonZero(mask) > 100


def _selected_recipe_row(main, recipe_id, recipe_ids):
    if not main.appear(ALAS_RECIPE_CHECK, offset=(20, 20)):
        return None
    if not main.appear(ALAS_RECIPE_AMOUNT_MAX, offset=RECIPE_AMOUNT_MAX_OFFSET):
        return None
    selected = [(detected, button) for detected, button in _read_recipe_rows(main, recipe_ids)
                if _is_selected(main, button)]
    return selected[0][1] if len(selected) == 1 and selected[0][0] == recipe_id else None


def select_manufacture_recipe(main, recipe_id, skip_first_screenshot=True):
    """寻找并确认指定商品，停在数量页；不派遣、不修改库存。

    Pages:
        in: 已选角色的 ISLAND_SELECT_PRODUCT_CHECK 页面
        out: 同一选品页，目标卡片名称与蓝框均已正向确认
    """
    recipe_ids = _recipe_ids_in_same_category(recipe_id)
    click_timer = Timer(2, count=4)
    swipes = 0
    # 从上一选品继承的位置向下寻找到底，再反向寻找；不每次机械归零。
    direction = -1
    seen_signature = None
    repeated = 0
    reversed_once = False
    for _ in main.loop(skip_first=skip_first_screenshot, timeout=Timer(45)):
        if not main.appear(ALAS_RECIPE_CHECK, offset=(20, 20)):
            continue
        rows = _read_recipe_rows(main, recipe_ids)
        candidates = [button for detected, button in rows if detected == recipe_id]
        if len(candidates) == 1:
            button = candidates[0]
            selected = [(detected, row) for detected, row in rows if _is_selected(main, row)]
            if any(detected == recipe_id for detected, _ in selected):
                if (len(selected) == 1
                        and main.appear(ALAS_RECIPE_AMOUNT_MAX, offset=RECIPE_AMOUNT_MAX_OFFSET)):
                    main._manufacture_selected_recipe_id = recipe_id
                    return True
                # 目标已选中，继续截图等数量页稳定；重复点击不能修复就绪识别。
                continue
            if click_timer.reached():
                main.device.click(button)
                click_timer.reset()
            continue
        if not rows:
            continue
        signature = tuple(detected for detected, _ in rows)
        if all(detected is None for detected in signature):
            continue
        repeated = repeated + 1 if signature == seen_signature else 0
        seen_signature = signature
        if repeated >= 2:
            if reversed_once:
                break
            direction = 1
            reversed_once = True
            repeated = 0
        if swipes >= MAX_RECIPE_SWIPES:
            break
        main.device.swipe_vector(vector=(0, direction * 300), box=(300, 142, 350, 602), name=SWIPE_NAME)
        main.device.click_record_remove(SWIPE_NAME)
        swipes += 1
    logger.warning(f'工坊选品未确认目标配方 {recipe_id}，滑动 {swipes} 次')
    return False


def read_selected_recipe_quantity(main, recipe_id):
    """同帧确认完整配方与蓝框后读取实际次数，未知不当成一或零。"""
    if _selected_recipe_row(main, recipe_id, _recipe_ids_in_same_category(recipe_id)) is None:
        return None
    text = str(Ocr(ALAS_RECIPE_AMOUNT, lang='cnocr', letter=(50, 50, 57), threshold=160,
                   alphabet='0123456789', name='MANUFACTURE_RECIPE_AMOUNT').ocr(main.device.image)).strip()
    return int(text) if re.fullmatch(r'\d+', text) and int(text) > 0 else None


def set_manufacture_quantity(main, number):
    """读取实际次数并用真实加减按钮闭环调整，不假设换品后已重置为一。"""
    recipe_id = getattr(main, '_manufacture_selected_recipe_id', None)
    if recipe_id is None:
        return False
    limit = DIC_ISLAND_RECIPE[recipe_id]['production_limit']
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= limit:
        raise ValueError(f'制造次数必须是 1–{limit} 的整数：{number}')
    confirmed = 0
    for _ in main.loop(skip_first=False, timeout=Timer(20)):
        current = read_selected_recipe_quantity(main, recipe_id)
        if current is None:
            confirmed = 0
            continue
        if current == number:
            confirmed += 1
            if confirmed >= 2:
                return True
            continue
        confirmed = 0
        button = ALAS_RECIPE_AMOUNT_PLUS if current < number else ALAS_RECIPE_AMOUNT_MINUS
        if main.appear_then_click(button, offset=(20, 20), interval=2):
            continue
    logger.warning(f'制造次数未确认到目标 {number}')
    return False


class ManufactureIngredientCounter(Ocr):
    """完整读取材料「现货/需求」，缺数字或分隔符返回未知。"""

    def __init__(self):
        super().__init__([], lang='cnocr', letter=(255, 255, 255), threshold=160,
                         alphabet='0123456789/IDSB()+', name='MANUFACTURE_INGREDIENT_COUNTER')

    def pre_process(self, image):
        background = color_similarity_2d(image, (80, 80, 80))
        background[background < 160] = 0
        indices = np.where(cv2.bitwise_and(background[0], background[-1]) > 200)[0]
        if len(indices):
            image = image[:, indices[0]:indices[-1] + 1]
        main = extract_letters(image, letter=self.letter, threshold=160)
        orange = color_similarity_2d(image, (253, 171, 34))
        orange[orange < 160] = 0
        if cv2.countNonZero(orange) > 30:
            other = extract_letters(image, letter=(253, 171, 34), threshold=160)
            cv2.bitwise_and(main, other, dst=main)
        return cv2.copyMakeBorder(main, 2, 4, 0, 0, cv2.BORDER_CONSTANT, value=255)

    def after_process(self, result):
        text = result.replace('I', '1').replace('D', '0').replace('S', '5').replace('B', '8')
        match = re.fullmatch(r'([0-9]+)/(?:\(([0-9]+)\+([0-9]+)\)|([0-9]+))', text)
        if not match:
            return None
        stock = int(match[1])
        required = int(match[4]) if match[4] is not None else int(match[2]) + int(match[3])
        return (stock, required) if required > 0 else None


def read_selected_recipe_inventory(main, recipe_id):
    """同帧确认选品后读取产物和全部材料库存；任一未知时整体返回 None。"""
    recipe_ids = _recipe_ids_in_same_category(recipe_id)
    button = _selected_recipe_row(main, recipe_id, recipe_ids)
    if button is None:
        return None
    recipe = DIC_ISLAND_RECIPE[recipe_id]
    materials = list(recipe['commission_cost'].items())
    count = len(materials)
    if not count:
        # 采矿和伐木没有材料栏，仍必须从已确认的产物卡片读取真实现货。
        stock = _read_product_stock(main, button)
        if stock is None:
            return None
        product_id = next(iter(recipe['commission_product']))
        record_planner_stocks(getattr(main, 'config', None), {product_id: stock}, '配方选品页')
        return {product_id: {'stock': stock, 'cost': 0, 'display_required': 0}}
    layouts = {1: (750, 0), 2: (663, 175), 3: (634, 116)}
    if count not in layouts:
        return None
    origin, delta = layouts[count]
    images = [crop(main.device.image, (origin + delta * index - 10, 540,
                                      origin + delta * index + 92, 558)) for index in range(count)]
    counters = ManufactureIngredientCounter().ocr(images, direct_ocr=True)
    if len(counters) != count or any(counter is None for counter in counters):
        return None
    observations = {}
    factors = set()
    for (item_id, cost), (stock, displayed) in zip(materials, counters):
        if cost <= 0 or displayed <= 0 or displayed % cost != 0:
            return None
        factors.add(displayed // cost)
        observations[item_id] = {'stock': stock, 'cost': cost, 'display_required': displayed}
    if len(factors) != 1:
        return None
    product_stock = _read_product_stock(main, button)
    if product_stock is None:
        return None
    product_id = next(iter(recipe['commission_product']))
    observations[product_id] = {'stock': product_stock, 'cost': 0, 'display_required': 0}
    record_planner_stocks(getattr(main, 'config', None),
                          {item: data['stock'] for item, data in observations.items()}, '配方选品页')
    return observations


def _read_product_stock(main, button):
    area = area_offset(RECIPE_PRODUCT_STOCK_AREA, button.area[:2])
    text = Ocr(area, lang='cnocr', letter=(80, 80, 80), threshold=160,
               name='RECIPE_PRODUCT_STOCK').ocr(main.device.image)
    # 数字区域可能保留「有:」的尾部；先核对完整读数，避免字符过滤吞掉数位。
    text = unicodedata.normalize('NFKC', str(text)).strip()
    match = re.fullmatch(r'(?:([^\d:]*):\s*)?([0-9]+|O)', text)
    if match is None:
        return None
    # 仅带库存标签分隔符的单个 O 对应零字形；裸 O、空值和混合数位仍是未知。
    value = match[2]
    if value == 'O' and match[1] is None:
        return None
    return 0 if value == 'O' else int(value)


def read_selected_recipe_stock(main, recipe_id):
    """读取正向确认的目标卡片现货，未知与真实零库存分别返回 None 和 0。"""
    button = _selected_recipe_row(main, recipe_id, _recipe_ids_in_same_category(recipe_id))
    if button is None:
        return None
    return _read_product_stock(main, button)
