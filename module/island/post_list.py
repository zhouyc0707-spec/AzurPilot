"""岗位列表中的空格识别，未知外观交由原详情流程处理。"""

import cv2
import numpy as np

from module.base.utils import area_offset
from module.island.assets import (
    TEMPLATE_POST_EMPTY, TEMPLATE_POST_EMPTY_COMPAT, TEMPLATE_POST_ORCHARD_TITLE_COMPAT,
)


# 国服连续实屏确认的八行顺序，供导航和统一收获共用。
CN_POST_ROWS = (
    (('island', 'FARM'), ('island_rancher', 'RANCH')),
    (('island_rancher', 'FISHERY'), ('island_mine_forest', 'MINE')),
    (('island_mine_forest', 'FOREST'), ('island_farm', 'ORCHARD')),
    (('island_farm', 'NURSERY'), ('island_restaurant', 'RESTAURANT')),
    (('island_teahouse', 'TEAHOUSE'), ('island_juu_eatery', 'JUU_EATERY')),
    (('island_grill', 'GRILL'), ('island_manufacture', 'WOOD_PROCESSING')),
    (('island_manufacture', 'INDUSTRIAL'), ('island_manufacture', 'ELECTRONIC_PROCESSING')),
    (('island_manufacture', 'HANDMADE'), ('island_juu_coffee', 'JUU_COFFEE')),
)


def is_post_title_confirmed(image, post):
    """严格复核本帧定位处的类别标题，不受高分辨率全局降阈值影响。"""
    if not isinstance(image, np.ndarray) or image.shape != (720, 1280, 3):
        return False
    if post.image is None or isinstance(post.image, list):
        return False
    dx, dy = np.subtract(post.button[:2], post._button[:2])
    x1, y1, x2, y2 = np.asarray(post.area) + (dx, dy, dx, dy)
    if x1 < 0 or y1 < 0 or x2 > 1280 or y2 > 720:
        return False
    title = image[y1:y2, x1:x2]
    if title.shape != post.image.shape:
        return False
    # 同列“木料加工”“电子加工”在放宽到 0.75 时可串行定位，不能据此跳过巡检。
    score = float(cv2.matchTemplate(title, post.image, cv2.TM_CCOEFF_NORMED).max())
    if score > 0.90:
        return True
    # 非原生分辨率按运行时缩放后，果园字形与旧截图不同；只补充该类别完整标题。
    return (post.name.startswith('ISLAND_ORCHARD_POST')
            and title.shape == TEMPLATE_POST_ORCHARD_TITLE_COMPAT.image.shape
            and TEMPLATE_POST_ORCHARD_TITLE_COMPAT.match(title, similarity=0.90, direct_match=True))


def match_post_title(image, post, *, strict=True):
    """整列定位标题；导航固定 0.85，跳过空岗保持 0.90 严格证明。"""
    post.clear_offset()
    if not isinstance(image, np.ndarray) or image.shape != (720, 1280, 3):
        return False
    post.ensure_template()
    if post.image is None or isinstance(post.image, list):
        return False
    x1, y1, x2, _ = post.area
    region = image[69:655, x1 - 3:x2 + 3]
    templates = [(post.image, 0.90 if strict else 0.85)]
    if (post.name.startswith('ISLAND_ORCHARD_POST')
            and post.image.shape == TEMPLATE_POST_ORCHARD_TITLE_COMPAT.image.shape):
        # 兼容标题独立找位置，不能依赖旧模板先找到同一个峰值。
        templates.append((TEMPLATE_POST_ORCHARD_TITLE_COMPAT.image, 0.90))
    candidates = []
    for template, threshold in templates:
        result = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, point = cv2.minMaxLoc(result)
        if score > threshold:
            candidates.append((score, point))
    if not candidates:
        return False
    _, (dx, dy) = max(candidates)
    post._button_offset = area_offset(post._button, (dx - 3, 69 + dy - y1))
    return True


def post_scroll_direction(image, post, server='cn'):
    """用本帧可靠类别判断方向：-1 向前、1 向后，0 表示不能确认。

    国服八行顺序经同一账号连续实屏确认；其他服务器没有等效证据，交由回顶搜索。
    模板采集时的原始纵坐标来自不同滚动位置，不参与类别排序。
    """
    if server != 'cn':
        return 0
    from importlib import import_module

    rows = CN_POST_ROWS
    name, separator, number = post.name.rpartition('_POST')
    if not separator or not number.isdigit():
        return 0
    target = next((index for index, row in enumerate(rows)
                   if any(name == f'ISLAND_{category}' for _, category in row)), None)
    if target is None:
        return 0
    visible = []
    for index, row in enumerate(rows):
        for module, category in row:
            anchor = getattr(import_module(f'module.{module}.assets'), f'ISLAND_{category}_POST1')
            if match_post_title(image, anchor):
                visible.append(index)
    if visible and target < min(visible):
        return -1
    if visible and target > max(visible):
        return 1
    return 0


def is_empty_post_slot(image, button):
    """仅以完整灰色加号卡片确认空岗，不根据商品识别失败推断。

    Args:
        image (np.ndarray): 1280×720 的当前岗位列表截图。
        button (tuple): 本帧标题定位后目标岗位的点击区域。

    Returns:
        bool: 卡片完整可见且与空岗模板高度匹配。
    """
    if not isinstance(image, np.ndarray) or image.shape != (720, 1280, 3):
        return False
    if len(button) != 4:
        return False
    x1, y1, x2, y2 = button
    if x2 <= x1 or y2 <= y1:
        return False
    cx, cy = round((x1 + x2) / 2), round((y1 + y2) / 2)
    # 点击区域有些略小于卡片；围绕中心取完整卡片，不能取到边缘裁断或列表外。
    left, top, right, bottom = cx - 45, cy - 45, cx + 45, cy + 45
    if left < 190 or right > 1225 or top < 115 or bottom > 655:
        return False
    # 相关性匹配会忽略整体明暗变化；被弹窗压暗的背景不能当作可直接巡检的列表。
    interior = image[cy - 28:cy + 28, cx - 28:cx + 28]
    gray = np.median(interior.reshape(-1, 3), axis=0)
    if np.max(np.abs(gray - 224)) > 25 or np.ptp(gray) > 8:
        return False
    card = image[top:bottom, left:right]
    # 两份真实空卡覆盖缩放后的像素差异，保持原阈值及上方明暗、完整可见性保护。
    return (TEMPLATE_POST_EMPTY.match(card, similarity=0.95, direct_match=True)
            or TEMPLATE_POST_EMPTY_COMPAT.match(card, similarity=0.95, direct_match=True))
