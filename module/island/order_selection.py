"""以订单圆环外的四个白色 L 角确认选中身份。

游戏 Lua 的 IslandOrderPage.ClickOrder 独立切换订单节点 ``sel``。
视觉几何来自用户授权的 1280×720 订单截图；圆环颜色和可交付绿勾
均不能替代这一标记。只读取图像，不点击，也不读取货物或账号数据。
"""

import numpy as np


# 四角白色部分各 22×22；统一位移容忍圆心识别误差。
# 实际截图的上下角起点间距为 103 或 104 像素，只允许整体高度差一像素。
CORNER_SIZE = 22
CORNER_OFFSETS = ((-64, -63), (40, -63), (-64, 40), (40, 40))
CENTER_TOLERANCE = 6
VERTICAL_SPACING_VARIANTS = (0, 1)
RIGHT_OVERLAY_LEFT = 832


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


def _has_selection_corners(mask, position, *, allow_right_occlusion=False):
    """共享位移与高度版本；右侧遮挡只可省略完全位于叠层内的两个右角。"""
    x, y = position
    height, width = mask.shape
    for dy in range(-CENTER_TOLERANCE, CENTER_TOLERANCE + 1):
        for dx in range(-CENTER_TOLERANCE, CENTER_TOLERANCE + 1):
            for vertical_extra in VERTICAL_SPACING_VARIANTS:
                corners = []
                for index, (ox, oy) in enumerate(CORNER_OFFSETS):
                    left = x + ox + dx
                    top = y + oy + dy + (vertical_extra if index >= 2 else 0)
                    if left < 0 or top < 0 or left + CORNER_SIZE > width or top + CORNER_SIZE > height:
                        break
                    corners.append((left, mask[top:top + CORNER_SIZE, left:left + CORNER_SIZE]))
                else:
                    if not all(_corner_matches(corners[index][1], index) for index in (0, 2)):
                        continue
                    if all(_corner_matches(corners[index][1], index) for index in (1, 3)):
                        return True
                    if allow_right_occlusion and all(corners[index][0] >= RIGHT_OVERLAY_LEFT for index in (1, 3)):
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


def get_selected_order_position(image, positions, *, allow_right_occlusion=False):
    """返回唯一具备选中角标证据的圆心，任一可见角证据不足返回 None。

    Args:
        image (np.ndarray): 当前 RGB 游戏截图，尺寸必须为 1280×720。
        positions (Iterable[tuple[int, int]]): 当前所有订单种类的圆心，不能只传目标类别。
        allow_right_occlusion (bool): 默认要求完整四角。调用方已在当前帧正向确认订单页、
            需求及对应交付按钮，表明已知顶部和右侧叠层布局时，才可设为 True；
            仅允许两个右角完整落在 x>=832 的已知叠层内，左侧角对仍须完整匹配。

    Returns:
        tuple[int, int] | None: 唯一选中的订单圆心；可见角缺失、多个选中或未知画面为 None。
    """
    if not isinstance(image, np.ndarray) or image.shape != (720, 1280, 3):
        return None
    candidates = _unique_positions(positions)
    if not candidates:
        return None
    mask = _white_mask(image)
    selected = [position for position in candidates
                if _has_selection_corners(mask, position, allow_right_occlusion=allow_right_occlusion)]
    return selected[0] if len(selected) == 1 else None


def is_order_selected(image, target_position, positions, *, allow_right_occlusion=False):
    """目标圆心必须与画面中唯一的选中标记关联；遮挡选项的前置条件同上。"""
    selected = get_selected_order_position(image, positions, allow_right_occlusion=allow_right_occlusion)
    return selected is not None and np.linalg.norm(np.subtract(selected, target_position)) <= 12
