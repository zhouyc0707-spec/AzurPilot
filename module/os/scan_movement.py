"""完整扫描的画面位移证据，不改变通用地图预测设置。"""

import cv2
import numpy as np

from module.logger import logger
from module.map_detection.utils_assets import ASSETS


def _scan_grid_images(view):
    """按整格透视变换取得纹理，排除完整搜索范围内的UI和屏幕边界。"""
    # 与图像使用相同的透视变换；额外留出2个原图像素，防止插值混入遮罩边缘。
    safe_mask = cv2.erode(ASSETS.ui_mask_os_in_map, np.ones((5, 5), np.uint8),
                         borderType=cv2.BORDER_CONSTANT, borderValue=0)
    images = []
    for grid in view:
        mask = cv2.warpPerspective(safe_mask, grid.homo_data, grid.config.HOMO_TILE)
        if not np.all(mask == 255):
            continue
        full = cv2.cvtColor(grid.image_trans, cv2.COLOR_RGB2GRAY)
        # 默认140像素整格，中央100像素为模板，其余用于容纳小幅识别偏差。
        y, x = np.array(full.shape) // 7
        piece = full[y:-y, x:-x]
        if np.std(piece) >= 1:
            images.append((grid.location, piece, full))
    return images


def predict_scan_swipe(previous, current):
    """通过唯一舰队标记或多个双向唯一格子确认相机的整数位移。

    不接收请求向量，避免用期望位移补足画面证据。重复海面纹理、单个
    外观匹配或不同格子支持互相矛盾的位移时均返回 None。
    """
    observed = previous.predict_swipe(current, with_current_fleet=True, with_sea_grids=False)
    if observed is not None:
        return observed

    # 屏幕矩形裁片只做缩放，纵向移动后透视仍不同，重复海纹可能形成假唯一匹配。
    # 使用现有的整格透视矫正图，让同一地形在前后帧中保持相同几何形状。
    before = _scan_grid_images(previous)
    after = _scan_grid_images(current)
    if len(before) < 2 or len(after) < 2:
        return None

    def unique_matches(source, target):
        matches = {}
        for index, (_, piece, _) in enumerate(source):
            scores = np.array([
                cv2.minMaxLoc(cv2.matchTemplate(full, piece, cv2.TM_CCOEFF_NORMED))[1]
                for _, _, full in target
            ])
            order = np.argsort(scores)
            best, second = order[-1], order[-2]
            if np.isfinite(scores[best]) and scores[best] >= 0.9 and scores[best] - scores[second] >= 0.05:
                matches[index] = int(best)
        return matches

    forward = unique_matches(before, after)
    reverse = unique_matches(after, before)
    offset = np.subtract(previous.center_loca, current.center_loca)
    movements = [tuple((np.subtract(before[a][0], after[b][0]) - offset).tolist())
                 for a, b in forward.items() if reverse.get(b) == a]
    if len(movements) >= 2 and len(set(movements)) == 1:
        logger.attr('全图扫描画面位移', f'{movements[0]}，{len(movements)} 个双向唯一格子')
        return movements[0]
    logger.info(f'[全图扫描] 未取得一致位移证据：{len(movements)} 个双向唯一格子')
    return None
