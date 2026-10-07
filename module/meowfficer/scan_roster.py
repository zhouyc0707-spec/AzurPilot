"""国服猫窝总数与排序的只读确认，未知结果不能替代扫描终止证据。"""

import re

import cv2
import numpy as np

import module.config.server as server
from module.exception import (EmulatorNotRunningError, GameBugError, GameNotRunningError,
                              GamePageUnknownError, GameStuckError, GameTooManyClickError,
                              RequestHumanTakeover, ScriptEnd, ScriptError)
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
    """精确读取后勤、指挥、战术三项静态数值，任一项未知则整体返回 None。"""
    if not _valid_image(image):
        return None
    attributes = []
    for area in STATIC_ATTRIBUTE_AREAS:
        found = []
        for variant in _variants(image, area):
            items = _items(ocr, variant)
            if (items is None or len(items) != 1
                    or re.fullmatch(r'[0-9]{1,5}', items[0][0]) is None):
                return None
            value = int(items[0][0])
            if not 0 <= value <= 10000:
                return None
            found.append(value)
        if len(found) != 2 or found[0] != found[1]:
            return None
        attributes.append(found[0])
    return tuple(attributes)
