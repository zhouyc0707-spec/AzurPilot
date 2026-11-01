"""完整扫描的画面位移证据，不改变通用地图预测设置。"""

import cv2
import numpy as np

from module.logger import logger


def predict_scan_swipe(previous, current):
    """通过唯一舰队标记或多个双向唯一格子确认相机的整数位移。

    不接收请求向量，避免用期望位移补足画面证据。重复海面纹理、单个
    外观匹配或不同格子支持互相矛盾的位移时均返回 None。
    """
    observed = previous.predict_swipe(current, with_current_fleet=True, with_sea_grids=False)
    if observed is not None:
        return observed

    before = [grid for grid in previous if grid.is_in_detecting_area]
    after = [grid for grid in current if grid.is_in_detecting_area]
    if len(before) < 2 or len(after) < 2:
        return None

    def unique_matches(source, target):
        matches = {}
        for index, grid in enumerate(source):
            piece = grid._image_similar_piece
            # 常量图的归一化相关系数没有辨识意义。
            if np.std(piece) < 1:
                continue
            scores = np.array([
                cv2.minMaxLoc(cv2.matchTemplate(other._image_similar_full, piece, cv2.TM_CCOEFF_NORMED))[1]
                for other in target
            ])
            order = np.argsort(scores)
            best, second = order[-1], order[-2]
            if np.isfinite(scores[best]) and scores[best] >= 0.9 and scores[best] - scores[second] >= 0.05:
                matches[index] = int(best)
        return matches

    forward = unique_matches(before, after)
    reverse = unique_matches(after, before)
    offset = np.subtract(previous.center_loca, current.center_loca)
    movements = [tuple((np.subtract(before[a].location, after[b].location) - offset).tolist())
                 for a, b in forward.items() if reverse.get(b) == a]
    if len(movements) >= 2 and len(set(movements)) == 1:
        logger.attr('全图扫描画面位移', f'{movements[0]}，{len(movements)} 个双向唯一格子')
        return movements[0]
    logger.info(f'[全图扫描] 未取得一致位移证据：{len(movements)} 个双向唯一格子')
    return None
