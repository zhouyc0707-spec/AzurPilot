"""严格读取岛屿普通订单的今日剩余次数，未知结果不能视为已完成。"""

import re

import numpy as np

import module.config.server as server
from module.base.button import Button
from module.island_daily_order.assets import DAILY_ORDER_CHECK
from module.ocr.ocr import Ocr


# CN 实图顶部计数为右对齐的 0/15～15/15，区域必须包含双位数的首位。
DAILY_ORDER_QUOTA = Button(area=(955, 23, 1015, 46), color=(255, 255, 255),
                           button=(), name='DAILY_ORDER_QUOTA')


class DailyOrderQuotaOcr(Ocr):
    """读取完整的「剩余次数/15」；页面、格式或范围未知时返回 None。"""

    def __init__(self):
        # 保留原始字符，不用数字白名单删除负号、错误文字后拼成有效计数。
        super().__init__(DAILY_ORDER_QUOTA, letter=(255, 255, 255), threshold=128)

    def after_process(self, result) -> int | None:
        """不截取子串，不裁剪越界值，不将空结果回退为零。"""
        if not isinstance(result, str):
            return None
        match = re.fullmatch(r'(0|[1-9][0-9]?)\s*/\s*15', result.strip())
        if match is None:
            return None
        remaining = int(match[1])
        return remaining if remaining <= 15 else None

    def ocr(self, image) -> int | None:
        """只读取已校准的 CN 订单页；多帧确认由订单任务的状态循环负责。"""
        if server.server != 'cn' or not isinstance(image, np.ndarray) or image.shape != (720, 1280, 3):
            return None
        if not DAILY_ORDER_CHECK.match(image, offset=(20, 20)):
            return None
        return super().ocr(image)
