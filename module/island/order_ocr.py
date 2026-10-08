"""岛屿订单的需求/现货识别。

数字排布和双色预处理来源于 ALAS 的 IslandReversedDigitCounter。
识别失败返回 None，避免把未知需求当成零需求。
"""

import re

import cv2
from jellyfish import levenshtein_distance

from module.base.utils import color_similarity_2d, extract_letters
from module.ocr.ocr import Ocr


class OrderDigitCounter(Ocr):
    """读取游戏的「现货/需求」，保留不足时的红色数字。"""

    def __init__(self, buttons):
        super().__init__(buttons, lang='cnocr', letter=(57, 59, 61), threshold=160,
                         alphabet='0123456789/IDSB()+', name='ORDER_REQUIREMENTS_COUNTER')

    def pre_process(self, image):
        main = extract_letters(image, letter=self.letter, threshold=self.threshold)
        mask = color_similarity_2d(image, color=(253, 97, 96))
        mask[mask < 160] = 0
        if cv2.countNonZero(mask) > 30:
            red = extract_letters(image, letter=(253, 97, 96), threshold=160)
            cv2.bitwise_and(main, red, dst=main)
        return cv2.copyMakeBorder(main, 2, 4, 0, 0, cv2.BORDER_CONSTANT, value=255)

    def after_process(self, result):
        text = result.replace('I', '1').replace('D', '0').replace('S', '5').replace('B', '8')
        match = re.fullmatch(r'([0-9]+)/(?:\(([0-9]+)\+([0-9]+)\)|([0-9]+))', text)
        if not match:
            return None
        stock = int(match[1])
        required = int(match[4]) if match[4] is not None else int(match[2]) + int(match[3])
        if required <= 0:
            return None
        return stock, required, stock - required


def match_item_name(name, items, language):
    """只接受精确匹配或唯一、距离受限的 OCR 修正。"""
    name = str(name).strip()
    if not name:
        return None
    if language == 'jp':
        name = name.replace('二', 'ニ').replace('力', 'カ')
    candidates = []
    for item_id, data in items.items():
        item_name = data['name'].get(language, '')
        if not item_name:
            continue
        if name == item_name:
            return item_id
        candidates.append((levenshtein_distance(name, item_name), item_id, item_name))
    if not candidates:
        return None
    # 一个字的残缺结果不能猜成多字货物；合法单字完整名已在上方精确返回。
    if len(name) < 2:
        return None
    candidates.sort()
    distance, item_id, item_name = candidates[0]
    limit = min(2, max(1, len(item_name) // 4))
    if distance > limit or (len(candidates) > 1 and candidates[1][0] == distance):
        return None
    return item_id


def validate_requirements(names, counters, items, language):
    """空白槽忽略；有文本或数字的槽必须同时完整识别，失败不交付或驳回。"""
    if len(names) != 3 or len(counters) != 3:
        return None
    requirements = {}
    for name, counter in zip(names, counters):
        if not str(name).strip() and counter is None:
            continue
        item_id = match_item_name(name, items, language)
        if item_id is None or counter is None or item_id in requirements:
            return None
        if len(counter) != 3 or counter[0] < 0 or counter[1] <= 0:
            return None
        requirements[item_id] = counter
    return requirements or None
