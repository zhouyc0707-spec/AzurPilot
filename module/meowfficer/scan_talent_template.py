"""国服天赋标题的严格图像补充识别，不通过 OCR 模糊别名推断名称。"""

import cv2
import numpy as np

import module.config.server as server
from module.meowfficer.assets import TEMPLATE_MEOWFFICER_TALENT_HURRICANE_EYE


TITLE_SHAPE = (39, 265, 3)
TITLE_SIMILARITY = 0.97


def read_exact_talent_title(image):
    """识别已经确认完整行的标题裁剪，无法精确确认时返回 None。

    仅补充 OCR 容易漏首字的「飓风之眼」。完整四字必须唯一匹配、落在标题左端，
    且模板外只能保留浅色背景；额外文字、遮罩或其它服务器都不能据此认作已知天赋。

    Args:
        image (np.ndarray): RGB 裁剪，原图坐标为 (855, top+3, 1120, top+42)。

    Returns:
        str | None: 精确天赋名称，或不能确认。
    """
    if (server.server != 'cn' or not isinstance(image, np.ndarray)
            or image.shape != TITLE_SHAPE or image.dtype != np.uint8):
        return None
    template = TEMPLATE_MEOWFFICER_TALENT_HURRICANE_EYE.image
    height, width = template.shape[:2]
    # 使用固定严格阈值，不能受普通 UI 识别的全局相似度放宽设置影响。
    scores = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
    if not np.isfinite(scores).all():
        return None
    accepted = (scores >= TITLE_SIMILARITY).astype(np.uint8)
    count, _labels = cv2.connectedComponents(accepted)
    if count != 2:
        return None
    _low, high, _low_pos, (x, y) = cv2.minMaxLoc(scores)
    if high < TITLE_SIMILARITY or not (8 <= x <= 16 and 4 <= y <= 14):
        return None
    outside = np.ones(image.shape[:2], dtype=bool)
    outside[y:y + height, x:x + width] = False
    if (image[outside] < 200).any():
        return None
    return '飓风之眼'
