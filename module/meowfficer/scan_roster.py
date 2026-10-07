"""国服猫窝总数与排序的只读确认，未知结果不能替代扫描终止证据。"""

import re

import cv2
import numpy as np

import module.config.server as server
from module.exception import (EmulatorNotRunningError, GameBugError, GameNotRunningError,
                              GamePageUnknownError, GameStuckError, GameTooManyClickError,
                              RequestHumanTakeover, ScriptEnd, ScriptError)
from module.logger import logger
from module.meowfficer.scan_utils import _crop
from module.meowfficer.score_ocr import build_variants


COUNT_AREA = (717, 550, 829, 584)
SORT_AREA = (1162, 91, 1249, 129)
STATIC_ATTRIBUTE_AREAS = ((224, 642, 267, 673), (410, 642, 452, 673), (586, 642, 638, 673))
_FLOW_ERRORS = (EmulatorNotRunningError, GameBugError, GameNotRunningError,
                GamePageUnknownError, GameStuckError, GameTooManyClickError,
                RequestHumanTakeover, ScriptEnd, ScriptError)


def _valid_image(image):
    return (server.server == 'cn' and isinstance(image, np.ndarray)
            and image.shape == (720, 1280, 3) and image.dtype == np.uint8)


def _variants(image, area):
    """设备 RGB 转为 OCR 的 BGR，补边防止数字贴边导致整行漏检。"""
    crop = _crop(image, area)[..., ::-1].copy()
    color = tuple(int(value) for value in crop[0, 0])
    padded = cv2.copyMakeBorder(crop, 8, 8, 8, 8, cv2.BORDER_CONSTANT, value=color)
    return build_variants(padded).values()


def _items(ocr, image):
    """所有文字均须高置信；普通模型异常视为未知，流程异常继续上抛。"""
    try:
        results = ocr.det(image)
    except _FLOW_ERRORS:
        raise
    except Exception:
        return None
    items = []
    for item in results or []:
        if isinstance(item, dict):
            text, box, confidence = item.get('text', ''), item.get('box'), item.get('score', 0)
        elif isinstance(item, (tuple, list)) and len(item) >= 3:
            text, box, confidence = item[:3]
        else:
            return None
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            return None
        if not np.isfinite(confidence) or confidence < 0.9 or not isinstance(text, str):
            return None
        text = text.strip()
        if not text:
            return None
        items.append((text, box))
    return items or None


def read_roster_count(image, ocr) -> int | None:
    """双变体精确确认「拥有数/容量」，返回拥有数；失败或矛盾返回 None。"""
    if not _valid_image(image):
        return None
    found = []
    for variant in _variants(image, COUNT_AREA):
        items = _items(ocr, variant)
        if items is None or len(items) != 1:
            return None
        match = re.fullmatch(r'([0-9]{1,5})\s*/\s*([0-9]{1,5})', items[0][0])
        if match is None:
            return None
        owned, capacity = (int(value) for value in match.groups())
        if not 0 <= owned <= capacity <= 10000:
            return None
        found.append((owned, capacity))
    return found[0][0] if len(found) == 2 and found[0] == found[1] else None


def _level_sort(items):
    """只接收完整「等级」，或有同一行位置证据的精确「等」「级」两框。"""
    if items is None:
        return False
    if len(items) == 1:
        return items[0][0] == '等级'
    if len(items) != 2 or {item[0] for item in items} != {'等', '级'}:
        return False
    positioned = []
    for text, box in items:
        try:
            points = np.asarray(box, dtype=float)
        except (TypeError, ValueError):
            return False
        if points.shape != (4, 2) or not np.isfinite(points).all():
            return False
        left, top = points.min(axis=0)
        right, bottom = points.max(axis=0)
        if right <= left or bottom <= top:
            return False
        positioned.append((left, top, right, bottom, text))
    positioned.sort(key=lambda item: item[0])
    first, second = positioned
    if (first[4], second[4]) != ('等', '级'):
        return False
    height = max(first[3] - first[1], second[3] - second[1])
    width = max(first[2] - first[0], second[2] - second[0])
    centers_diff = abs((first[1] + first[3]) - (second[1] + second[3])) / 2
    overlap = min(first[3], second[3]) - max(first[1], second[1])
    gap = second[0] - first[2]
    return bool(centers_diff <= height * 0.3 and overlap >= height * 0.6
                and -width * 0.3 <= gap <= width * 0.8)


def lock_independent_sort(image, ocr) -> bool:
    """仅双变体正向确认「等级」排序；锁定排序或任何未知标签均返回 False。"""
    if not _valid_image(image):
        return False
    for variant in _variants(image, SORT_AREA):
        if not _level_sort(_items(ocr, variant)):
            return False
    return True


def read_static_attributes(image, ocr) -> tuple[int, int, int] | None:
    """双路精确读取静态数值；一路未确认时须由两份完整灰字补证，门槛不变。"""
    if not _valid_image(image):
        return None
    attributes = []
    for label, area in zip(('后勤', '指挥', '战术'), STATIC_ATTRIBUTE_AREAS):
        evidence = []
        for variant in _variants(image, area):
            evidence.append(_attribute_evidence(ocr, variant))
        if (len(evidence) != 2 or not all(item[2] for item in evidence)
                or len({item[0] for item in evidence if item[0] is not None}) > 1):
            _attribute_failure(label, evidence, '原始证据无效或数值矛盾')
            return None
        confirmed = [item[0] for item in evidence if item[1]]
        if not confirmed:
            _attribute_failure(label, evidence, '原始两路均未确认')
            return None
        value = confirmed[0]
        if len(confirmed) < 2:
            glyphs = _attribute_glyph_variants(_crop(image, area))
            if glyphs is None:
                _attribute_failure(label, evidence, '灰字触及边缘或字形不完整')
                return None
            supplementary = [_attribute_evidence(ocr, variant) for variant in glyphs]
            if (len(supplementary) != 2
                    or any(not item[1] or not item[2] or item[0] != value
                           for item in supplementary)):
                _attribute_failure(label, evidence, '灰字补证未确认或数值矛盾', supplementary)
                return None
        attributes.append(value)
    return tuple(attributes)


def _attribute_evidence(ocr, image):
    """保留置信度不足的纯数字证据；非数字、多框和模型失败不能由补证覆盖。"""
    try:
        results = ocr.det(image)
    except _FLOW_ERRORS:
        raise
    except Exception:
        return None, False, False, '模型失败'
    if results is None:
        return None, False, True, '未检测到数字'
    if not isinstance(results, (tuple, list)):
        return None, False, False, '结果格式无效'
    if len(results) == 0:
        return None, False, True, '未检测到数字'
    if len(results) != 1:
        return None, False, False, '多个文本框'
    item = results[0]
    if isinstance(item, dict):
        if 'score' not in item:
            return None, False, False, '缺少置信度'
        text, score = item.get('text'), item['score']
    elif isinstance(item, (tuple, list)) and len(item) >= 3:
        text, score = item[0], item[2]
    else:
        return None, False, False, '结果格式无效'
    try:
        confidence = float(score)
    except (TypeError, ValueError):
        return None, False, False, '置信度无效'
    if not np.isfinite(confidence) or not 0 <= confidence <= 1:
        return None, False, False, '置信度无效'
    if not isinstance(text, str) or re.fullmatch(r'[0-9]{1,5}', text.strip()) is None:
        return None, False, False, '非纯数字'
    value = int(text.strip())
    detail = f'{text.strip()}（置信度 {confidence:.5f}）'
    if not 0 <= value <= 10000:
        return None, False, False, f'{detail}，超出范围'
    return value, confidence >= 0.9, True, detail


def _attribute_glyph_variants(crop):
    """按两种灰字亮度阈值分割，保留全部像素并正向确认字形未碰裁剪边缘。"""
    high, low = crop.max(axis=2), crop.min(axis=2)
    difference = high.astype(np.int16) - low.astype(np.int16)
    # 固定数字框只应有灰字和浅色底；不能把混入的彩色文字或图标过滤掉后判成功。
    if np.any((low <= 180) & (difference > 25)):
        return None
    variants = []
    for threshold in (160, 180):
        mask = (high <= threshold) & (difference <= 25)
        ys, xs = np.nonzero(mask)
        if (not len(xs) or xs.min() < 1 or xs.max() >= crop.shape[1] - 1
                or ys.min() < 2 or ys.max() >= crop.shape[0] - 2
                or not 13 <= ys.max() - ys.min() + 1 <= 24
                or xs.max() - xs.min() + 1 < 4):
            return None
        glyph = np.repeat(np.where(mask, 0, 255).astype(np.uint8)[:, :, None], 3, axis=2)
        padded = cv2.copyMakeBorder(glyph, 8, 8, 8, 8,
                                    cv2.BORDER_CONSTANT, value=(255, 255, 255))
        variants.append(build_variants(padded)['plain'])
    return variants


def _attribute_failure(label, evidence, reason, supplementary=None):
    """失败日志仅记录数值字段、数字及置信度，不输出猫名或账号画面。"""
    original = '；'.join(item[3] for item in evidence)
    extra = '' if supplementary is None else '；灰字补证：' + '；'.join(
        item[3] for item in supplementary)
    logger.warning(f'[指挥喵-评分] {label}静态属性核验失败：{reason}；原始两路：{original}{extra}')
