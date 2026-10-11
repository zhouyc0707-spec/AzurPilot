"""领取新指挥喵时读取清晰猫名及当前天赋，排除成长面板的升级预览。"""

import cv2
import numpy as np

from module.meowfficer.cat_data import CATS
from module.meowfficer.score import TALENT_INDEX, Talent, normalize


FRAME_SHAPE = (720, 1280, 3)
CAT_AREA = (995, 199, 1150, 228)
SLOT_LEFTS = (476, 600, 724)
CAT_CONFIDENCE = 0.85
TALENT_CONFIDENCE = 0.90


def _valid_frame(image):
    return (isinstance(image, np.ndarray) and image.shape == FRAME_SHAPE
            and image.dtype == np.uint8)


def _texts(results):
    """只接收带置信度的检测结果，不把普通字符串当成可靠识别。"""
    for item in results or ():
        if isinstance(item, dict):
            text, score = item.get('text', ''), item.get('score', 0.0)
        elif isinstance(item, (list, tuple)) and len(item) >= 3:
            text, score = item[0], item[2]
        else:
            continue
        if isinstance(text, str):
            try:
                confidence = float(score)
            except (ValueError, TypeError):
                continue
            if np.isfinite(confidence):
                yield text, confidence


def _pad(image):
    value = (255, 255, 255) if image.ndim == 3 else 255
    return cv2.copyMakeBorder(image, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=value)


def _recognize_exact(ocr, variants, index, threshold):
    """至少两路高置信度精确命中一致，已知名称互相矛盾时不作选择。"""
    accepted = {}
    for variant in variants:
        matches = []
        for text, confidence in _texts(ocr.det(variant)):
            key = normalize(text)
            if confidence >= threshold and key in index:
                matches.append((key, text))
        # 一个标题区域不能同时出现两个有效名称，也不能只取其中更像的一条。
        if len(matches) > 1:
            return None
        if matches:
            key, raw = matches[0]
            accepted.setdefault(key, []).append(raw)
    if len(accepted) != 1:
        return None
    key, raws = next(iter(accepted.items()))
    if len(raws) < 2:
        return None
    return index[key], raws[0]


def recognize_collection_cat(image, ocr, shifted=False):
    """读取没有成长弹窗遮罩的领取页猫名，不能确认时返回 None。

    Args:
        image (np.ndarray): 已稳定领取页的 1280×720 RGB 截图。
        ocr: 当前流程使用的 OCR 实例，只调用其 det 方法。
        shifted (bool): 领取页已确认向左上偏移。

    Returns:
        str | None: 精确已知猫名，不补全缺字、不作模糊别名匹配。
    """
    if not _valid_frame(image):
        return None
    dx, dy = (-40, -20) if shifted else (0, 0)
    x0, y0, x1, y1 = CAT_AREA
    crop = image[y0 + dy:y1 + dy, x0 + dx:x1 + dx]
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    binary = cv2.threshold(gray, 190, 255, cv2.THRESH_BINARY)[1]
    variants = [cv2.resize(_pad(part), None, fx=3, fy=3, interpolation=cv2.INTER_LANCZOS4)
                for part in (crop, gray, binary)]
    result = _recognize_exact(ocr, variants, {normalize(name): name for name in CATS}, CAT_CONFIDENCE)
    return result[0] if result else None


def _selected_slot(image):
    """要求当前图标外围四边的深色虚线框完整且唯一。"""
    pixels = image.astype(np.int16)
    dark = ((pixels.mean(axis=2) < 185) & (np.ptp(pixels, axis=2) < 45))
    selected = []
    for column, x in enumerate(SLOT_LEFTS):
        counts = (dark[303:315, x + 5:x + 76].sum(),
                  dark[383:398, x + 5:x + 76].sum(),
                  dark[315:384, x - 4:x + 5].sum(),
                  dark[315:384, x + 76:x + 85].sum())
        if all(count >= minimum for count, minimum in zip(counts, (70, 120, 70, 70))):
            selected.append(column)
    return selected[0] if len(selected) == 1 else None


def _visible_slots(image):
    """普通成长页有三个彩色图标，特殊天赋只有中间一个。"""
    hsv = cv2.cvtColor(image, cv2.COLOR_RGB2HSV)
    return tuple(int(((hsv[318:383, x + 5:x + 76, 1] > 80)
                      & (hsv[318:383, x + 5:x + 76, 2] > 150)).sum()) >= 1000
                 for x in SLOT_LEFTS)


def recognize_collection_talent(image, ocr):
    """读取成长页选中框对应的当前天赋，不读取同线未来等级。

    Args:
        image (np.ndarray): 成长弹窗已展开的 1280×720 RGB 截图。
        ocr: 当前流程使用的 OCR 实例，只调用其 det 方法。

    Returns:
        list[Talent]: 只含一条精确确认的当前天赋，或无法确认时的空列表。
    """
    if not _valid_frame(image):
        return []
    column = _selected_slot(image)
    if column is None:
        return []
    visible = _visible_slots(image)
    if visible == (True, True, True):
        kind = 'normal'
    elif visible == (False, True, False) and column == 1:
        kind = 'special'
    else:
        return []
    x = 452 + 124 * column
    crop = image[389:414, x:x + 132]
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    binary = cv2.threshold(gray, 190, 255, cv2.THRESH_BINARY)[1]
    # 最近邻保留原始细字笔画；纵向放大补充低分辨率字体，不修正文本文字。
    variants = [cv2.resize(_pad(part), None, fx=sx, fy=sy, interpolation=method)
                for part, sx, sy, method in (
                    (crop, 3, 3, cv2.INTER_NEAREST),
                    (crop, 3, 5, cv2.INTER_NEAREST),
                    (crop, 3, 6, cv2.INTER_NEAREST),
                    (crop, 3, 3, cv2.INTER_LANCZOS4),
                    (binary, 3, 3, cv2.INTER_LANCZOS4),
                )]
    result = _recognize_exact(ocr, variants, TALENT_INDEX, TALENT_CONFIDENCE)
    if result is None:
        return []
    ref, raw = result
    if ref.kind != kind or (kind == 'normal' and ref.level != column + 1):
        return []
    return [Talent(name=ref.name, line=ref.line, level=ref.level, kind=ref.kind,
                   raw=raw, inferred=False)]
