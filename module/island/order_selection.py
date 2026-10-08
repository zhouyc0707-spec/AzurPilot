"""以订单圆环外的四个白色 L 角确认选中身份。

游戏 Lua 的 IslandOrderPage.ClickOrder 独立切换订单节点 ``sel``。
视觉几何来自用户授权的 1280×720 订单截图；圆环颜色和可交付绿勾
均不能替代这一标记。只读取图像，不点击，也不读取货物或账号数据。
"""

import cv2
import numpy as np


# 四角白色部分各 22×22；统一位移容忍圆心识别误差。
# 实际截图的横向及纵向角起点间距为 103 或 104 像素，各只允许整体尺寸差一像素。
CORNER_SIZE = 22
CORNER_OFFSETS = ((-64, -63), (40, -63), (-64, 40), (40, 40))
CENTER_TOLERANCE = 6
VERTICAL_SPACING_VARIANTS = (0, 1)
HORIZONTAL_SPACING_VARIANTS = (-1, 0)
RIGHT_OVERLAY_LEFT = 832


def _white_mask(image):
    """仅取近中性的高亮白色，订单蓝圈、绿勾和地图颜色均不作正向依据。"""
    return (image.min(axis=2) >= 225) & (np.ptp(image, axis=2) <= 25)


def _corner_matches(patch, index):
    if index >= 2:
        patch = patch[::-1]
    if index % 2:
        patch = patch[:, ::-1]
    # 先拒绝白臂不足，再要求两臂属于同一个连通块，不能拼接两个独立文字片段。
    if patch[:6, 8:].mean() < 0.9 or patch[8:, :6].mean() < 0.9:
        return False
    count, labels = cv2.connectedComponents(np.ascontiguousarray(patch, dtype=np.uint8), connectivity=8)
    for label in range(1, count):
        component = labels == label
        # 地图的独立白斑不属于角标；连接到 L 的白块仍参加内部空白校验。
        if (component[:6, 8:].mean() >= 0.9
                and component[8:, :6].mean() >= 0.9
                and component[9:, 9:].mean() <= 0.12):
            return True
    return False


def _dialogue_corner_matches(patch, index, left, top, dialogue_bounds):
    """仅核验已确认对白框之外的下角，至少保留 24 个白臂像素位置。"""
    if index not in (2, 3) or dialogue_bounds is None:
        return False
    x1, y1, x2, y2 = dialogue_bounds
    # 圆角边缘不能当作实心遮挡；下角须位于白框中部，避开左右和底部圆角。
    if left < x1 + 12 or left + CORNER_SIZE > x2 - 12 or top + CORNER_SIZE > y2 - 12:
        return False
    yy, xx = np.ogrid[top:top + CORNER_SIZE, left:left + CORNER_SIZE]
    covered = (xx >= x1) & (xx < x2) & (yy >= y1) & (yy < y2)
    if not covered.any():
        return False
    # 遮挡必须来自实际对白框；框外白臂仍按原密度核验，不能任意省略下角。
    visible = ~covered[::-1]
    patch = patch[::-1]
    if index % 2:
        patch = patch[:, ::-1]
        visible = visible[:, ::-1]
    arm = np.zeros((CORNER_SIZE, CORNER_SIZE), dtype=bool)
    arm[:6, 8:] = True
    arm[8:, :6] = True
    if np.count_nonzero(arm & visible) < 24:
        return False
    count, labels = cv2.connectedComponents(np.ascontiguousarray(patch & visible, dtype=np.uint8),
                                           connectivity=8)
    for label in range(1, count):
        component = labels == label
        horizontal = visible[:6, 8:]
        vertical = visible[8:, :6]
        if horizontal.any() and component[:6, 8:][horizontal].mean() < 0.9:
            continue
        if vertical.any() and component[8:, :6][vertical].mean() < 0.9:
            continue
        inner_visible = visible[9:, 9:]
        if inner_visible.any() and component[9:, 9:][inner_visible].mean() > 0.12:
            continue
        return True
    return False


def _has_selection_corners(mask, position, *, allow_right_occlusion=False, dialogue_bounds=None):
    """共享位移与整体尺寸版本，只在已确认的叠层区域内处理遮挡。"""
    x, y = position
    height, width = mask.shape
    for dy in range(-CENTER_TOLERANCE, CENTER_TOLERANCE + 1):
        for dx in range(-CENTER_TOLERANCE, CENTER_TOLERANCE + 1):
            for horizontal_extra in HORIZONTAL_SPACING_VARIANTS:
                for vertical_extra in VERTICAL_SPACING_VARIANTS:
                    corners = []
                    for index, (ox, oy) in enumerate(CORNER_OFFSETS):
                        left = x + ox + dx + (horizontal_extra if index % 2 else 0)
                        top = y + oy + dy + (vertical_extra if index >= 2 else 0)
                        if left < 0 or top < 0 or left + CORNER_SIZE > width or top + CORNER_SIZE > height:
                            break
                        corners.append((left, top, mask[top:top + CORNER_SIZE, left:left + CORNER_SIZE]))
                    else:
                        if not _corner_matches(corners[0][2], 0):
                            continue
                        matches = [True] + [_corner_matches(corners[index][2], index) for index in (1, 2, 3)]
                        if all(matches):
                            return True
                        # 被遮挡时无法观测宽度，最靠左的103px版本也须完整位于叠层内。
                        if allow_right_occlusion and matches[2] and all(
                                corners[index][0] - horizontal_extra + min(HORIZONTAL_SPACING_VARIANTS)
                                >= RIGHT_OVERLAY_LEFT for index in (1, 3)):
                            return True
                        if dialogue_bounds is not None and matches[1] and all(
                                matches[index] or _dialogue_corner_matches(
                                    corners[index][2], index, corners[index][0], corners[index][1], dialogue_bounds)
                                for index in (2, 3)):
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


def get_selected_order_position(image, positions, *, allow_right_occlusion=False, allow_dialogue_occlusion=False):
    """返回唯一具备选中角标证据的圆心，任一可见角证据不足返回 None。

    Args:
        image (np.ndarray): 当前 RGB 游戏截图，尺寸必须为 1280×720。
        positions (Iterable[tuple[int, int]]): 当前所有订单种类的圆心，不能只传目标类别。
        allow_right_occlusion (bool): 默认要求完整四角。调用方已在当前帧正向确认订单页、
            需求及对应交付按钮，表明已知顶部和右侧叠层布局时，才可设为 True；
            仅允许两种宽度下的两个右角均完整落在 x>=832 的已知叠层内，
            左侧角对仍须完整匹配；不能根据被遮住的角猜测宽度。
        allow_dialogue_occlusion (bool): 调用方已在当前帧正向确认订单页、需求与对应交付按钮时
            才可启用。另须识别对白框实际边界、完整上角对和框外下角白臂，不能按固定高度放行。

    Returns:
        tuple[int, int] | None: 唯一选中的订单圆心；可见角缺失、多个选中或未知画面为 None。
    """
    if not isinstance(image, np.ndarray) or image.shape != (720, 1280, 3):
        return None
    candidates = _unique_positions(positions)
    if not candidates:
        return None
    mask = _white_mask(image)
    dialogue_bounds = None
    if allow_dialogue_occlusion:
        from module.island.order_dialogue import get_order_dialogue_bounds
        dialogue_bounds = get_order_dialogue_bounds(image)
    selected = [position for position in candidates
                if _has_selection_corners(mask, position, allow_right_occlusion=allow_right_occlusion,
                                          dialogue_bounds=dialogue_bounds)]
    return selected[0] if len(selected) == 1 else None


def is_order_selected(image, target_position, positions, *, allow_right_occlusion=False,
                      allow_dialogue_occlusion=False):
    """目标圆心必须与画面中唯一的选中标记关联；遮挡选项的前置条件同上。"""
    selected = get_selected_order_position(image, positions, allow_right_occlusion=allow_right_occlusion,
                                          allow_dialogue_occlusion=allow_dialogue_occlusion)
    return selected is not None and np.linalg.norm(np.subtract(selected, target_position)) <= 12
