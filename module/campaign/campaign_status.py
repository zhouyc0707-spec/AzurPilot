"""战役状态检测模块。

通过 OCR 读取战役页面上的数值信息，包括：
- 物资（金币）数量
- 石油数量
- 活动 PT（点数）
- 石油和物资限制检测

这些信息用于判断是否满足停止条件（如石油耗尽、物资溢出等）。

PtOcr 类专门处理活动 PT 数字的 OCR 识别，
需要特殊的图像预处理（反色、背景去除等）。

继承自 UI，利用页面导航能力。
"""

import datetime
import re

import cv2
import numpy as np

import module.config.server as server

from module.base.timer import Timer
from module.campaign.assets import OCR_EVENT_PT, OCR_COIN, OCR_OIL, OCR_COIN_LIMIT, OCR_OIL_LIMIT, OCR_OIL_CHECK
from module.base.utils import color_similar, get_color
from module.logger import logger
from module.ocr.ocr import Digit, Ocr
from module.ui.ui import UI
from module.log_res import LogRes

#if server.server != 'jp':
#    OCR_COIN = Digit(OCR_COIN, name='OCR_COIN', letter=(239, 239, 239), threshold=128)
#else:
#    OCR_COIN = Digit(OCR_COIN, name='OCR_COIN', letter=(201, 201, 201), threshold=128)

class PtOcr(Ocr):
    """活动 PT 数字专用的 OCR 识别器。"""

    def __init__(self, *args, **kwargs):
        """初始化 PT 识别器。

        Args:
            *args: 位置参数，传递给父类。
            **kwargs: 关键字参数，传递给父类。
        """
        super().__init__(*args, lang='azur_lane', alphabet='X0123456789', **kwargs)

    def pre_process(self, image):
        """对 PT 数字图像进行预处理。

        取 RGB 三通道最大值并反色，去除背景干扰。

        Args:
            image (np.ndarray): 形状为 (height, width, channel) 的彩色图像。

        Returns:
            np.ndarray: 形状为 (height, width) 的灰度图像。
        """
        # 取 RGB 三通道的最大值（等价于反色图取最小值后取反，避免分配中间数组）
        r, g, b = cv2.split(image)
        cv2.max(r, g, dst=r)
        cv2.max(r, b, dst=r)
        cv2.bitwise_not(r, dst=r)
        # 去除背景，将 0-192 映射到 0-255
        cv2.convertScaleAbs(r, alpha=255 / 192, dst=r)
        return r


OCR_PT = PtOcr(OCR_EVENT_PT)


class CampaignStatus(UI):
    """战役资源状态检测混入类。

    提供活动 PT、物资（金币）、石油数量的读取与更新方法。
    """

    def get_event_pt(self, update=False):
        """获取活动 PT 数量。

        Args:
            update (bool): 是否将读取到的数值写回配置。默认 False。

        Returns:
            int: PT 数量，解析失败返回 0 且保留已有记录。

        Pages:
            in: page_event、page_sp 等显示活动 PT 的选关页
        """
        text = OCR_PT.ocr(self.device.image).strip()
        # 活动页既有 X 前缀，也有纯数字格式；必须确认整个结果，不能截取混杂文本。
        res = re.fullmatch(r'X?([0-9]+)', text)
        if res:
            pt = int(res.group(1))
            logger.attr('活动PT', pt)
            LogRes(self.config).Pt = pt
        else:
            logger.warning(f'无效的PT结果: {text}')
            pt = 0
        if update:
            self.config.update()
        return pt

    def get_coin(self, skip_first_screenshot=True, update=False):
        """获取金币数量。

        Args:
            skip_first_screenshot (bool): 是否跳过首次截图。默认 True。
            update (bool): 是否将读取到的数值写回配置。默认 False。

        Returns:
            int: 金币当前数量；未取得有效读数返回 0，保留已有记录。

        Pages:
            in: 显示顶部物资／石油资源栏的页面，如 page_campaign、page_event、page_sp
        """
        return self._get_resource_balance('Coin', '物资', OCR_COIN, OCR_COIN_LIMIT,
                                          (239, 239, 239), skip_first_screenshot, update)

    def _get_resource_balance(self, resource, label, value_button, limit_button, letter,
                              skip_first_screenshot, update):
        """截图重试资源读数，低余额需稳定确认，失败不覆盖记录。

        读取失败返回 0 供停止条件保守处理，仪表盘及资源观测保持原值；
        上限单独读取失败不覆盖已有上限。
        """
        candidate = None
        confirm = Timer(0.5, count=2)
        for _ in self.loop(skip_first=skip_first_screenshot, timeout=Timer(1, count=3)):
            values = {
                'Value': self._get_num(value_button, f'OCR_{resource.upper()}', letter, require_valid=True),
                'Limit': self._get_num(limit_button, f'OCR_{resource.upper()}_LIMIT', letter, require_valid=True),
            }
            value = values['Value']
            if value is not None and value >= 0:
                if candidate != value:
                    candidate = value
                    confirm.reset()
                # 沿用正常余额的快速读取；低值也可能是真实余额，须确认后返回实际值。
                if value >= 100 or confirm.reached():
                    LogRes(self.config).record(resource, {key: value for key, value in values.items() if value is not None},
                                               observed=True)
                    if update:
                        self.config.update()
                    return value
            else:
                candidate = None
                confirm.clear()

        logger.warning(f'获取{label}超时，未取得有效读数，保留上次有效记录')
        if update:
            self.config.update()
        return 0

    def _get_num(self, _button, name, letter=(247, 247, 247), require_valid=False):
        """动态检测背景颜色并识别指定区域的数字。

        Args:
            _button: 按钮区域对象。
            name (str): 识别器标识名称。
            letter (tuple[int, int, int]): 字符目标颜色 RGB 值。默认 (247, 247, 247)。
            require_valid (bool): 背景或数字无效时返回 None，交由调用方截图重试。

        Returns:
            int | None: 识别到的数值，要求有效性且读取失败时返回 None。
        """
        # OCR 数字使用固定 area；探针也必须同坐标系，不能沿用模板匹配遗留的点击偏移。
        color = get_color(self.device.image, OCR_OIL_CHECK.area)
        if color_similar(color, OCR_OIL_CHECK.color):
            # 原始颜色
            if isinstance(_button, Ocr):
                ocr = _button
            else:
                if server.server != 'jp':
                    ocr = Digit(_button, name=name, letter=letter, threshold=128)
                else:
                    ocr = Digit(_button, name=name, letter=(201, 201, 201), threshold=128)
        elif color_similar(color, (59, 59, 64)):
            # 带黑色遮罩
            ocr = Digit(_button, name=name, letter=(165, 165, 165), threshold=128)
        else:
            if require_valid:
                # 过渡帧不猜测字符颜色；有界重试结束后由调用方报告失败。
                logger.debug(f'[战役-状态] {name}资源栏背景未就绪: {color}')
                return None
            logger.warning(f'[战役-状态] {name}的OCR_OIL_CHECK颜色异常: {color}')
            ocr = Digit(_button, name=name, letter=(247, 247, 247), threshold=128)

        value = ocr.ocr(self.device.image)
        return None if require_valid and not getattr(ocr, 'last_valid', False) else value

    def get_oil(self, skip_first_screenshot=True, update=False):
        """获取石油数量。

        Args:
            skip_first_screenshot (bool): 是否跳过首次截图。默认 True。
            update (bool): 是否将读取到的数值写回配置。默认 False。

        Returns:
            int: 石油当前数量；未取得有效读数返回 0，保留已有记录。

        Pages:
            in: 显示顶部物资／石油资源栏的页面，如 page_campaign、page_event、page_sp
        """
        return self._get_resource_balance('Oil', '石油', OCR_OIL, OCR_OIL_LIMIT,
                                          (247, 247, 247), skip_first_screenshot, update)

    def is_balancer_task(self):
        """判断当前任务是否为活动任务（排除每日活动任务）。

        Returns:
            bool: 若为活动任务返回 True，否则返回 False。
        """
        tasks = [
            'Event',
            'Event2',
            'Raid',
            'Coalition',
            'GemsFarming',
            'ThreeOilLowCost',
        ]
        command = self.config.Scheduler_Command
        if command in tasks:
            if self.config.Campaign_Event == 'campaign_main':
                return False
            else:
                return True
        else:
            return False
