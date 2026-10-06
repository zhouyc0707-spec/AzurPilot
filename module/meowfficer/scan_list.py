"""猫窝列表的只读位置锚点，未知画面不推断为空或已选中。

坐标由 1280×720 猫窝页面校准。选择环、锁图标与名单内容分开识别，
允许相邻指挥喵同名同级，不能用名字变化替代卡片位置确认。
"""

import numpy as np

from module.meowfficer.scan_utils import _crop, parse_level, pick_cat_name
from module.meowfficer.score_ocr import build_variants


def card_center(index):
    """返回当前三行四列中指定卡片的实际中心，索引从零开始。"""
    if not isinstance(index, int) or not 0 <= index < 12:
        raise ValueError('猫窝卡片索引必须为 0 到 11')
    return 784 + 130 * (index % 4), 185 + 146 * (index // 4)


def _valid_image(image):
    return isinstance(image, np.ndarray) and image.shape == (720, 1280, 3)


def _region(image, index, relative):
    cx, cy = card_center(index)
    left, top, right, bottom = relative
    # 第三行姓名条被列表底缘裁切，不能把下面的容量／入口栏当成卡片内容。
    return _crop(image, (cx + left, cy + top, cx + right, min(cy + bottom, 550)))


def _yellow(image):
    """选择虚线的黄色像素，不把浅色姓名底板算成选择环。"""
    red, green, blue = image[..., 0], image[..., 1], image[..., 2]
    return (red > 215) & (green > 150) & (green < 235) & (blue < 145)


def selected_card(image):
    """只在唯一卡片的右侧虚线环至少两段命中时确认选中位置。

    窄条避开头像、姓名与锁按钮；容许三个像素的几何偏差。整片黄色或
    多个选中环均视为未知，不能由动画或锁图标推断选择成功。
    """
    if not _valid_image(image):
        return None
    selected = []
    for index in range(12):
        strip = _region(image, index, (44, -29, 67, 40))
        yellow = _yellow(strip)
        counts = [int(part.sum()) for part in np.array_split(yellow, 3, axis=0)]
        total = sum(counts)
        if 20 <= total <= 250 and sum(count >= 6 for count in counts) >= 2:
            selected.append(index)
    return selected[0] if len(selected) == 1 else None


def cattery_at_top(image):
    """以滚动条黄色滑块确认列表顶部，无滑块或未知几何返回 None。"""
    if not _valid_image(image):
        return None
    strip = _crop(image, (1245, 132, 1259, 550))
    rows = np.flatnonzero(_yellow(strip).sum(axis=1) >= 3)
    if not len(rows):
        return None
    groups = np.split(rows, np.flatnonzero(np.diff(rows) > 3) + 1)
    if len(groups) != 1 or not 10 <= len(groups[0]) <= 200:
        return None
    return int(groups[0][0]) + 132 <= 145


def _texts(ocr, image):
    """保留高置信度文本；缺失或异常置信度不能用于确认身份。"""
    texts = []
    for item in ocr.det(image) or []:
        if isinstance(item, dict):
            text, confidence = item.get('text', ''), item.get('score', 0)
        elif isinstance(item, (tuple, list)) and len(item) >= 3:
            text, confidence = item[0], item[2]
        else:
            return None
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            return None
        if not np.isfinite(confidence) or confidence < 0.9:
            return None
        if str(text).strip():
            texts.append(str(text).strip())
    return texts or None


def read_card_identity(image, index, ocr):
    """读取高置信且一致的姓名，等级无法独立确认时保留姓名并返回 None 级。"""
    card_center(index)
    if not _valid_image(image):
        return None
    names, levels = [], []
    for variant in build_variants(_region(image, index, (-60, 49, 60, 77))).values():
        texts = _texts(ocr, variant)
        name = pick_cat_name(texts or [])
        if not name or len(texts or []) != 1:
            return None
        names.append(name)
    if len(names) != 2 or names[0] != names[1]:
        return None
    for variant in build_variants(_region(image, index, (-52, 23, -15, 51))).values():
        texts = _texts(ocr, variant)
        level = parse_level(texts or [])
        if level is None or len(texts or []) != 1:
            return names[0], None
        levels.append(level)
    if len(levels) != 2 or levels[0] != levels[1]:
        return names[0], None
    return names[0], levels[0]


def card_is_empty(image, index):
    """只有脸部及姓名条均与邻近网格空隙一致且无纹理时正向确认空格。"""
    card_center(index)
    if not _valid_image(image):
        return False
    gap = _region(image, index, (65, -10, 70, 10))
    background = np.median(gap, axis=(0, 1))
    # 黑屏、遮罩、加载页和不属于猫窝的纯色页不能被当成空名单。
    if background.min() < 185 or background.max() - background.min() > 35:
        return False
    for region in (gap, _region(image, index, (-20, -20, 20, 20)),
                   _region(image, index, (-60, 49, 60, 77))):
        pixels = region.astype(np.float32)
        if pixels.std(axis=(0, 1)).max() > 2 or np.abs(pixels - background).mean() > 4:
            return False
    return True


def cattery_order_unchanged(before, after):
    """逐格比对脸部、姓名和等级，避开选择环及锁图标。

    每格都参与检查，包括先前及当前选中格；脸部左上角的选择标记不参与
    对比。视口位移、同名猫头像或等级改变均不能被整张面板均值稀释。
    """
    if not _valid_image(before) or not _valid_image(after):
        return False
    for index in range(12):
        regions = ((-12, -20, 30, 22), (-60, 49, 60, 77), (-43, 27, -23, 45))
        for relative in regions:
            first, second = _region(before, index, relative), _region(after, index, relative)
            difference = np.abs(first.astype(np.int16) - second.astype(np.int16))
            # 选择环光晕可延伸到姓名条和等级徽章，只屏蔽它占用的像素。
            valid = ~(_yellow(first) | _yellow(second))
            if valid.mean() < 0.6 or difference[valid].mean() > 2 \
                    or (difference.max(axis=2)[valid] > 20).mean() > 0.02:
                return False
    return True
