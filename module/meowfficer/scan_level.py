"""当前猫等级的精确读取；小号白字漏检时以完整字形双路补证。"""

import re

import cv2
import numpy as np

from module.config import server
from module.exception import (EmulatorNotRunningError, GameBugError, GameNotRunningError,
                              GamePageUnknownError, GameStuckError, GameTooManyClickError,
                              RequestHumanTakeover, ScriptEnd, ScriptError)
from module.logger import logger
from module.meowfficer.scan_utils import _crop


# 位于原等级条内部，只覆盖右端 LV 数字；舰队名、属性及经验值不进入候选。
LEVEL_GLYPH_AREA = (365, 600, 438, 623)
FLOW_ERRORS = (EmulatorNotRunningError, GameBugError, GameNotRunningError,
               GamePageUnknownError, GameStuckError, GameTooManyClickError,
               RequestHumanTakeover, ScriptEnd, ScriptError)


def _level_evidence(results):
    """返回数值、是否高置信确认、是否允许补证；低分数字同样保留以核验冲突。"""
    if results is None:
        return None, False, True
    if not isinstance(results, (tuple, list)):
        return None, False, False
    if len(results) == 0:
        return None, False, True
    if len(results) != 1:
        return None, False, False
    item = results[0]
    if isinstance(item, dict) and 'score' in item:
        text, score = item.get('text'), item['score']
    elif isinstance(item, (tuple, list)) and len(item) >= 3:
        text, score = item[0], item[2]
    else:
        return None, False, False
    try:
        confidence = float(score)
    except (TypeError, ValueError):
        return None, False, False
    if not np.isfinite(confidence) or not 0 <= confidence <= 1 or not isinstance(text, str):
        return None, False, False
    # 兼容已有的明确前缀误读，不把 I 当成 1，也不从其他文字里搜索数字。
    match = re.fullmatch(r'(?:LV|LW|IV|WV)[ .:]*([0-9]{1,2})', text.strip(), re.IGNORECASE)
    if match is None or not 1 <= int(match[1]) <= 60:
        return None, False, False
    return int(match[1]), confidence >= 0.9, True


def level_glyph_variants(image):
    """在校准的国服等级框中保留完整白字，提供两种亮度阈值的补读图。"""
    if (server.server != 'cn' or not isinstance(image, np.ndarray)
            or image.shape != (720, 1280, 3) or image.dtype != np.uint8):
        return None
    crop = _crop(image, LEVEL_GLYPH_AREA)
    low = crop.min(axis=2)
    variants = []
    for threshold in (235, 245):
        # 白字三通道都须足够亮；经验条的黄色边框不提供等级证据。
        mask = low >= threshold
        ys, xs = np.nonzero(mask)
        if (not len(xs) or xs.min() < 1 or xs.max() >= crop.shape[1] - 1
                or ys.min() < 1 or ys.max() >= crop.shape[0] - 1
                or not 10 <= ys.max() - ys.min() + 1 <= 18
                or xs.max() - xs.min() + 1 < 12):
            return None
        glyph = np.repeat(np.where(mask, 0, 255).astype(np.uint8)[..., None], 3, axis=2)
        padded = cv2.copyMakeBorder(glyph, 24, 24, 24, 24,
                                    cv2.BORDER_CONSTANT, value=(255, 255, 255))
        variants.append(cv2.resize(padded, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST))
    return variants


def read_cat_level(image, ocr, original_results):
    """普通等级直接读取；仅漏检或低分有效等级可由两份精确一致字形补证。"""
    original, confirmed, usable = _level_evidence(original_results)
    if not usable:
        return None
    if confirmed:
        return original
    variants = level_glyph_variants(image)
    if variants is None:
        return None
    supplementary = []
    for variant in variants:
        try:
            results = ocr.det(variant)
        except FLOW_ERRORS:
            raise
        except Exception:
            logger.warning('[指挥喵-扫描] 等级字形补读失败')
            return None
        value, reliable, valid = _level_evidence(results)
        if not valid or not reliable:
            return None
        supplementary.append(value)
    if len(set(supplementary)) != 1 or (original is not None and original != supplementary[0]):
        logger.debug('[指挥喵-扫描] 等级原读数与双路补读不一致')
        return None
    logger.debug(f'[指挥喵-扫描] 完整等级白字双路确认 Lv{supplementary[0]}')
    return supplementary[0]
