"""岗位列表中的空格识别，未知外观交由原详情流程处理。"""

import cv2
import numpy as np

from module.island.assets import TEMPLATE_POST_EMPTY


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
    return score > 0.90


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
    return TEMPLATE_POST_EMPTY.match(image[top:bottom, left:right], similarity=0.95, direct_match=True)
