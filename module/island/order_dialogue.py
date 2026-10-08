"""只凭当前画面的完整对白纸框证据确定遮挡边界。

1280×720 实图的框边、圆角阶梯与阴影来自 2026-10-09 的十四张已有现场。
不把选中角标连接后的连通块外框当作对白，不依据订单圆心位置豁免检测。
"""

import numpy as np


# 固定资源尺寸下的实际主体为 461×75；首行只覆盖圆角间的 447px。
FRAME_WIDTH = 461
FRAME_HEIGHT = 75
TOP_WIDTH = 447
CORNER_SIZE = 18
SEARCH_AREA = (100, 520, 832, 710)
CORNER_INSETS = (
    (5, 3, 2, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    (9, 6, 5, 4, 3, 2, 2, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    (12, 9, 6, 5, 4, 3, 2, 2, 2, 1, 1, 1, 0, 0, 0, 0, 0, 0),
    (5, 3, 3, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
)


def _white_mask(image):
    return (image.min(axis=2) >= 225) & (np.ptp(image, axis=2) <= 25)


def _corner_matches(patch, insets):
    expected = np.arange(CORNER_SIZE)[None, :] >= np.asarray(insets)[:, None]
    return patch[expected].mean() >= 0.98 and patch[~expected].mean() <= 0.10


def _frame_matches(image, mask, bounds):
    left, top, right, bottom = bounds
    if left < SEARCH_AREA[0] or right > SEARCH_AREA[2] or bottom + 10 > image.shape[0]:
        return False
    corners = (
        mask[top:top + CORNER_SIZE, left:left + CORNER_SIZE],
        mask[top:top + CORNER_SIZE, right - CORNER_SIZE:right][:, ::-1],
        mask[bottom - CORNER_SIZE:bottom, left:left + CORNER_SIZE][::-1],
        mask[bottom - CORNER_SIZE:bottom, right - CORNER_SIZE:right][::-1, ::-1],
    )
    if not all(_corner_matches(patch, insets) for patch, insets in zip(corners, CORNER_INSETS)):
        return False

    # 四条边都要存在；左侧对白尾巴位于上半段，外侧对比取尾巴以下的直边。
    pairs = (
        ((slice(top, top + 3), slice(left + 32, right - 32)),
         (slice(top - 2, top), slice(left + 32, right - 32))),
        ((slice(bottom - 3, bottom), slice(left + 32, right - 32)),
         (slice(bottom, bottom + 3), slice(left + 32, right - 32))),
        ((slice(top + 34, bottom - 20), slice(left, left + 3)),
         (slice(top + 34, bottom - 20), slice(left - 3, left))),
        ((slice(top + 20, bottom - 20), slice(right - 3, right)),
         (slice(top + 20, bottom - 20), slice(right, right + 3))),
    )
    for inside, outside in pairs:
        if mask[inside].mean() < 0.99 or mask[outside].mean() > 0.08:
            return False
        if float(image[inside].mean()) - float(image[outside].mean()) < 15:
            return False

    # 顶部内侧的实际留白带不受一行或两行正文影响，空心白线框不能冒充白纸。
    if mask[top + 8:top + 15, left + 22:right - 22].mean() < 0.99:
        return False
    top_band = image[top:top + 3, left + 32:right - 32]
    bottom_band = image[bottom - 3:bottom, left + 32:right - 32]
    # 原纸框从上方暖白渐变到下方冷白；平白矩形不具备这项正向证据。
    if float(top_band.mean()) - float(bottom_band.mean()) < 5:
        return False
    if float(bottom_band[:, :, 2].mean()) - float(bottom_band[:, :, 0].mean()) < 5:
        return False
    near_shadow = image[bottom:bottom + 3, left + 32:right - 32]
    far_background = image[bottom + 7:bottom + 10, left + 32:right - 32]
    return float(far_background.mean()) - float(near_shadow.mean()) >= 8


def get_order_dialogue_bounds(image):
    """返回唯一正向确认的底部对白主体，证据缺失或歧义时返回 None。

    Args:
        image (np.ndarray): 当前 RGB 截图，尺寸必须为 1280×720。

    Returns:
        tuple[int, int, int, int] | None: (left, top, right, bottom)，右下界为
            Python 切片的半开边界。不包含框外阴影或左侧对白尾巴。
            圆角、完整四边、白纸渐变及阴影都必须在当前帧成立。
    """
    if not isinstance(image, np.ndarray) or image.shape != (720, 1280, 3) or image.dtype != np.uint8:
        return None
    mask = _white_mask(image)
    area_left, area_top, area_right, area_bottom = SEARCH_AREA
    candidates = []
    for top in range(area_top, min(area_bottom, image.shape[0] - FRAME_HEIGHT - 10) + 1):
        row = mask[top, area_left:area_right]
        changes = np.diff(np.r_[False, row, False].astype(np.int8))
        for start, end in zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)):
            if end - start != TOP_WIDTH:
                continue
            left = int(area_left + start - CORNER_INSETS[0][0])
            bounds = (left, top, left + FRAME_WIDTH, top + FRAME_HEIGHT)
            if _frame_matches(image, mask, bounds):
                candidates.append(bounds)
    return candidates[0] if len(candidates) == 1 else None
