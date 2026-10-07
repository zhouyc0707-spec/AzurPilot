"""完整天赋标题的字形补读；不拼接碎片，也不放宽精确名称门槛。"""

import cv2
import numpy as np

from module.meowfficer.score import TALENT_INDEX, normalize
from module.meowfficer.score_ocr import build_variants


def split_title_may_retry(details, boxes, image_shape):
    """仅同一标题行的两个有效碎片可重新读取；完整已知名冲突不能旁路。

    原检测框只决定是否有必要补读，不作为接受名称的证据。分框必须具有有效
    置信度、横向连续及相近的垂直范围，排除两个完整标题、上下两行和无效框。
    """
    if len(details) != 2 or len(boxes) != 2:
        return False
    height, width = image_shape[:2]
    bounds = []
    for (text, confidence), box in zip(details, boxes):
        if not np.isfinite(confidence) or not 0 <= confidence <= 1 \
                or normalize(text) in TALENT_INDEX:
            return False
        try:
            points = np.asarray(box, dtype=float)
        except (TypeError, ValueError):
            return False
        if points.shape != (4, 2) or not np.isfinite(points).all():
            return False
        left, top = points.min(axis=0)
        right, bottom = points.max(axis=0)
        if not (0 <= left < right <= width and 0 <= top < bottom <= height):
            return False
        if bottom - top < height * 0.35 or right - left < 8:
            return False
        bounds.append((left, top, right, bottom))
    first, second = bounds
    first_height, second_height = first[3] - first[1], second[3] - second[1]
    if first[0] >= second[0] or first[2] >= second[2]:
        return False
    if min(first_height, second_height) < max(first_height, second_height) * 0.55:
        return False
    if abs((first[1] + first[3]) - (second[1] + second[3])) > max(
            first_height, second_height) * 0.5:
        return False
    gap = second[0] - first[2]
    narrower = min(first[2] - first[0], second[2] - second[0])
    return bool(-0.35 * narrower <= gap <= max(first_height, second_height))


def split_title_variants(crop):
    """从原始 RGB 像素独立分离两种深色字形，再各自识别整个标题。

    两个阈值均保留标题完整范围及字符间距，只把浅色底纹变成白色；不使用
    上次 OCR 的文字、框裁剪或拼接推断名称。调用方仍须双路高置信度精确一致。
    """
    low = crop.min(axis=2)
    for threshold in (160, 185):
        glyph = np.where(low < threshold, 0, 255).astype(np.uint8)
        image = np.repeat(glyph[:, :, None], 3, axis=2)
        yield f'whole_title_glyph_{threshold}', build_variants(image)['plain']


def padded_title_variants(crop):
    """原图已确认完整已知名、增强图误读未知字时，补读两份完整原始字形。

    原始深色笔画须位于标题裁剪内部；先排除碰边裁切，再补白边，避免检测器
    在放大后的近边界笔画上多切出一个文字框。名称接受条件仍由调用方严格核验。
    """
    if crop.shape != (39, 265, 3) or crop.dtype != np.uint8:
        return
    mask = crop.min(axis=2) < 185
    if not mask.any() or mask[0].any() or mask[-1].any() \
            or mask[:, 0].any() or mask[:, -1].any():
        return
    padded = cv2.copyMakeBorder(crop, 8, 8, 8, 8,
                               cv2.BORDER_CONSTANT, value=(255, 255, 255))
    for name, variant in split_title_variants(padded):
        yield name.replace('whole_title_', 'whole_title_padded_'), variant
