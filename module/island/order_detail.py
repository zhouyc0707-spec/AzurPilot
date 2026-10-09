"""订单右侧静态文字的切换证据，不把库存变化或背景动画当作换单。"""

import numpy as np


# CN 的委托人和三格货物名称；不包含现货、倒计时、奖励和左侧对白。
DETAIL_TEXT_AREAS = ((944, 185, 1105, 217),) + tuple(
    (1052, 252 + 79 * index, 1212, 280 + 79 * index) for index in range(3))


def get_order_detail_signature(image):
    """只保留文字形状；委托人及至少一格货物必须有足够的有效笔画。"""
    if not isinstance(image, np.ndarray) or image.shape != (720, 1280, 3):
        return None
    masks = []
    for x1, y1, x2, y2 in DETAIL_TEXT_AREAS:
        crop = image[y1:y2, x1:x2]
        masks.append((crop.min(axis=2) >= 25) & (crop.max(axis=2) <= 140)
                     & (np.ptp(crop, axis=2) <= 35))
    if np.count_nonzero(masks[0]) < 40 or not any(np.count_nonzero(mask) >= 40 for mask in masks[1:]):
        return None
    return tuple(masks)


def same_order_detail(first, second, tolerance=0.08):
    """逐区比较文字，不让长货物名称掩盖短委托人的变化。"""
    if first is None or second is None:
        return False
    return all(np.count_nonzero(left ^ right) <= max(8, tolerance * np.count_nonzero(left | right))
               for left, right in zip(first, second))


def order_detail_changed(before, after):
    """仅接受明确文字变化；少量抗锯齿差异不能证明点击生效。"""
    return before is not None and after is not None and not same_order_detail(before, after, tolerance=0.25)
