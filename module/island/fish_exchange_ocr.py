"""鱼肉加工分区识别；未知数量不回退为零，也不从混合文本抽取数字。"""

import re
import unicodedata

import numpy as np

from module.base.utils import color_similarity_2d, crop, crop_to_text
from module.island.data import DIC_ISLAND_EXCHANGE_RECIPE, DIC_ISLAND_ITEM
from module.island.island_fish_meat import (
    EXCHANGE_FISH_NAME_BACKGROUND, EXCHANGE_FISH_NAME_GRID,
    EXCHANGE_FISH_STOCK_GRID,
)
from module.ocr.ocr import Ocr


def parse_quantity_text(text, *, preview=False):
    """仅在已经隔离的数字区域修正 O/0；不丢弃 C 等其他字符。"""
    text = unicodedata.normalize('NFKC', str(text)).strip()
    pattern = r'\+[0-9Oo]+' if preview else r'[0-9Oo]+'
    if not re.fullmatch(pattern, text):
        return None
    return int(text.lstrip('+').replace('O', '0').replace('o', '0'))


def _read_numbers(images, *, letter, threshold, preview=False, badge=False):
    """颜色提取后批量读数字；仅完全没有蓝字的预览可以直接判零。"""
    reader = Ocr([], lang='azur_lane', letter=letter, threshold=threshold,
                 alphabet=None, name='FISH_EXCHANGE_NUMBER')
    results = [None] * len(images)
    prepared, positions = [], []
    for index, image in enumerate(images):
        if badge:
            # 与 ALAS 相同，仅保留深色数量徽标，避免浅色卡框被读成 C。
            ys, xs = np.where(image.max(axis=2) < 130)
            if not len(xs):
                continue
            image = image[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        processed = reader.pre_process(image)
        if np.all(processed == 255):
            if preview:
                results[index] = 0
            continue
        # 抗锯齿残留或单个噪点不能证明存在可读数量。
        if np.count_nonzero(processed < 128) < 6:
            continue
        prepared.append(crop_to_text(processed))
        positions.append(index)
    if prepared:
        texts = reader.cnocr.atomic_ocr_for_single_lines(prepared, None)
        if len(texts) != len(positions):
            return results
        for index, text in zip(positions, texts):
            results[index] = parse_quantity_text(''.join(text), preview=preview)
    return results


def read_exchange_amounts(image):
    """分开读取深色现货和蓝色 ``+预览``，任一未知时返回 None。"""
    # 左侧翻译文字可能伸入原数量区域，避开标签；被边界截断的数字不读取。
    region = crop(image, (990, 530, 1185, 560))
    edges = region[:, [0, -1]].astype(int)
    if np.any(edges.max(axis=2) < 130) or np.any(
            np.abs(edges - np.array((57, 189, 255))).max(axis=2) < 40):
        return None
    owned = _read_numbers([region], letter=(60, 60, 60), threshold=160)[0]
    gain = _read_numbers([region], letter=(57, 189, 255), threshold=128, preview=True)[0]
    return (owned, gain) if owned is not None and gain is not None else None


def read_exchange_cards(image, meat_id, language='cn'):
    """逐卡读取完整鱼名与右下库存；名称重复、数量未知的卡不参与兑换。"""
    indices = [
        index for index, button in enumerate(EXCHANGE_FISH_NAME_GRID.buttons)
        if np.mean(color_similarity_2d(crop(image, button.area), EXCHANGE_FISH_NAME_BACKGROUND) > 235) > 0.5
    ]
    if not indices:
        return {}
    language_model = language if language in ('jp', 'tw') else 'cnocr'
    name_reader = Ocr([], lang=language_model, letter=(255, 255, 255), threshold=128,
                      name='FISH_EXCHANGE_NAME')
    names = name_reader.ocr([crop(image, EXCHANGE_FISH_NAME_GRID.buttons[i].area) for i in indices],
                            direct_ocr=True)
    stocks = _read_numbers([crop(image, EXCHANGE_FISH_STOCK_GRID.buttons[i].area) for i in indices],
                           letter=(255, 255, 255), threshold=128, badge=True)
    candidates = {source for recipe in DIC_ISLAND_EXCHANGE_RECIPE.values() if meat_id in recipe['items']
                  for source in recipe['resource_consume']}
    expected = {fish: ''.join(unicodedata.normalize('NFKC', DIC_ISLAND_ITEM[fish]['name'][language]).split())
                for fish in candidates}
    matched = []
    for index, name, stock in zip(indices, names, stocks):
        name = ''.join(unicodedata.normalize('NFKC', str(name)).split())
        fish_ids = [fish for fish, full_name in expected.items() if full_name == name]
        if len(fish_ids) == 1:
            matched.append((fish_ids[0], index, stock))
    return {
        fish: {'stock': stock, 'button': EXCHANGE_FISH_NAME_GRID.buttons[index].area}
        for fish, index, stock in matched
        if stock is not None and stock > 0 and sum(row[0] == fish for row in matched) == 1
    }
