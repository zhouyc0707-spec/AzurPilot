"""定位选品页材料计数的灰底标签，保留白色和橙色耗料数字。"""

import re

import cv2
import numpy as np

from module.base.utils import crop
from module.logger import logger
from module.ocr.ocr import Ocr


class _MaterialCounterOcr(Ocr):
    def pre_process(self, image):
        # 标签内的白字、彩字已一起转成黑字，不能再按白色提取一次。
        return image


def material_counter_images(image):
    """提取完整灰底标签的两种亮度二值图；截断或定位不唯一时返回空列表。"""
    region = crop(image, (690, 530, 890, 575))
    maximum = region.max(axis=2)
    minimum = region.min(axis=2)
    mask = ((maximum <= 110) & (maximum.astype(int) - minimum <= 30)).astype(np.uint8)
    _, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    candidates = []
    for index, (x, y, width, height, _) in enumerate(stats[1:], 1):
        # 标签随进场动画横移，按中心范围定位，不能固定截取一段数字。
        if (width < 24 or not 12 <= height <= 28
                or abs(690 + x + width / 2 - 775) >= 40
                or abs(530 + y + height / 2 - 549) >= 12
                or x == 0 or y == 0
                or x + width >= region.shape[1] or y + height >= region.shape[0]):
            continue
        candidates.append((index, x, y, width, height))
    if len(candidates) != 1:
        return []
    index, x, y, width, height = candidates[0]
    contours, _ = cv2.findContours((labels == index).astype(np.uint8),
                                  cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    hull = cv2.convexHull(max(contours, key=cv2.contourArea))
    filled = np.zeros(mask.shape, dtype=np.uint8)
    cv2.drawContours(filled, [hull], -1, 1, cv2.FILLED)
    images = []
    for cutoff in (150, 160):
        binary = np.full(mask.shape, 255, dtype=np.uint8)
        binary[(filled != 0) & (maximum > cutoff)] = 0
        images.append(np.pad(binary[y:y + height, x:x + width], 2, constant_values=255))
    return images


def read_material_counter_text(image):
    """两路读数须一致；仅允许完整需求括号在另一路漏掉一个。"""
    images = material_counter_images(image)
    if not images:
        logger.warning('[岛屿] 未定位到完整的材料计数标签')
        return ''
    reader = _MaterialCounterOcr([], alphabet='0123456789/+()IDSB', name='ISLAND_MATERIAL_COUNTER')
    texts = reader.ocr(images, direct_ocr=True)
    if len(texts) == 2:
        if texts[0] == texts[1]:
            return texts[0]
        for complete, partial in (texts, texts[::-1]):
            # 必须已有完整文本，且数字、斜杠、加号逐字相同，不能拼补数字。
            matched = re.fullmatch(r'([0-9]+/)\(([0-9]+(?:\+[0-9]+)*)\)', complete)
            if matched and partial in (f'{matched[1]}{matched[2]})', f'{matched[1]}({matched[2]}'):
                return complete
    logger.warning(f'[岛屿] 材料计数交叉识别不一致: {texts!r}')
    return ''
