"""以订单圆环外的四个白色 L 角确认选中身份。

游戏 Lua 的 IslandOrderPage.ClickOrder 独立切换订单节点 ``sel``。
视觉几何来自用户授权的 1280×720 订单截图；圆环颜色和可交付绿勾
均不能替代这一标记。只读取图像，不点击，也不读取货物或账号数据。
"""

import numpy as np


# 校准图中圆心 (749, 247)，四角白色部分各 22×22；统一位移容忍圆心识别误差。
CORNER_SIZE = 22
CORNER_OFFSETS = ((-64, -63), (40, -63), (-64, 40), (40, 40))
CENTER_TOLERANCE = 6


def _white_mask(image):
    """仅取近中性的高亮白色，订单蓝圈、绿勾和地图颜色均不作正向依据。"""
    return (image.min(axis=2) >= 225) & (np.ptp(image, axis=2) <= 25)


def _corner_matches(patch, index):
    if index >= 2:
        patch = patch[::-1]
    if index % 2:
        patch = patch[:, ::-1]
    # 两条实心白臂必须各自完整；内部空白用于排除白色矩形和文字块。
    return (patch[:6, 8:].mean() >= 0.9
            and patch[8:, :6].mean() >= 0.9
            and patch[9:, 9:].mean() <= 0.12)


def _has_four_corners(mask, position):
    x, y = position
    height, width = mask.shape
    for dy in range(-CENTER_TOLERANCE, CENTER_TOLERANCE + 1):
        for dx in range(-CENTER_TOLERANCE, CENTER_TOLERANCE + 1):
            for index, (ox, oy) in enumerate(CORNER_OFFSETS):
                left, top = x + ox + dx, y + oy + dy
                if left < 0 or top < 0 or left + CORNER_SIZE > width or top + CORNER_SIZE > height:
                    break
                patch = mask[top:top + CORNER_SIZE, left:left + CORNER_SIZE]
                if not _corner_matches(patch, index):
                    break
            else:
                return True
    return False


def _unique_positions(positions):
    result = []
    for position in positions:
        if len(position) != 2 or not np.all(np.isfinite(position)):
            return []
        candidate = tuple(int(round(value)) for value in position)
        # 同一圆环可能由两种颜色重复检出；合并识别抖动，不合并相邻订单。
        if not any(np.linalg.norm(np.subtract(candidate, existing)) <= 12 for existing in result):
            result.append(candidate)
    return result


def get_selected_order_position(image, positions):
    """返回唯一具有四个选中角标的圆心，任一证据不足返回 None。

    Args:
        image (np.ndarray): 当前 RGB 游戏截图，尺寸必须为 1280×720。
        positions (Iterable[tuple[int, int]]): 当前所有订单种类的圆心，不能只传目标类别。

    Returns:
        tuple[int, int] | None: 唯一选中的订单圆心；缺角、多个选中或未知画面为 None。
    """
    if not isinstance(image, np.ndarray) or image.shape != (720, 1280, 3):
        return None
    candidates = _unique_positions(positions)
    if not candidates:
        return None
    mask = _white_mask(image)
    selected = [position for position in candidates if _has_four_corners(mask, position)]
    return selected[0] if len(selected) == 1 else None


def is_order_selected(image, target_position, positions):
    """目标圆心必须与画面中唯一的四角选中标记关联才返回 True。"""
    selected = get_selected_order_position(image, positions)
    return selected is not None and np.linalg.norm(np.subtract(selected, target_position)) <= 12
