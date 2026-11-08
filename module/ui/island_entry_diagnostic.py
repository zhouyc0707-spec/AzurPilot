"""单次岛屿入口导航的限量现场记录；正常进入时不写盘。"""

import os
from datetime import datetime
from pathlib import Path

import numpy as np

from module.base.timer import Timer
from module.base.utils import save_image
from module.logger import logger


DIAGNOSTIC_ROOT = Path('D:/Codex/reports/island-entry-diagnostics')


def _navigation_image(image):
    """仅保留当前服务器的页面标识及入口证据，遮去账号和无关界面区域。"""
    from module.ui.page import page_dormmenu, page_island, page_main, page_main_white

    areas = [page.check_button.area for page in (page_main, page_main_white, page_dormmenu, page_island)]
    for page, target in ((page_main, page_dormmenu), (page_main_white, page_dormmenu),
                         (page_dormmenu, page_island)):
        button = page.links[target]
        areas.extend((button.area, button.button))
    masked = np.zeros_like(image)
    height, width = image.shape[:2]
    for x1, y1, x2, y2 in areas:
        # 与本次 ui_goto 默认模板偏移一致，保留原坐标便于回放识别。
        x1, y1 = max(0, x1 - 30), max(0, y1 - 30)
        x2, y2 = min(width, x2 + 30), min(height, y2 + 30)
        masked[y1:y2, x1:x2] = image[y1:y2, x1:x2]
    return masked


class IslandEntryDiagnostic:
    """只在重复入口或返回主界面时保存本次导航至多三张入口证据图。"""

    def __init__(self):
        self._first_frame = None
        self._started = False
        self._timer = Timer(0)
        self._folder = None
        self._saved = 0
        self._disabled = False

    def start(self, image):
        """首次点击岛屿入口前保留副本；后续调用不覆盖原帧或重新计时。"""
        if self._started or self._disabled:
            return False
        self._first_frame = image.copy()
        self._timer.start()
        self._started = True
        return True

    def capture(self, image, reason):
        """记录异常当前帧，首次异常额外保存首点帧；磁盘故障不影响导航。"""
        if not self._started or self._disabled or self._saved >= 3:
            return False
        try:
            if self._folder is None:
                stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
                self._folder = DIAGNOSTIC_ROOT / f'{stamp}-{os.getpid()}'
                self._folder.mkdir(parents=True, exist_ok=False)
            if self._first_frame is not None:
                save_image(_navigation_image(self._first_frame), self._folder / '01-first-click.png')
                self._saved += 1
                self._first_frame = None
            save_image(_navigation_image(image), self._folder / f'{self._saved + 1:02d}-observed.png')
            self._saved += 1
        except OSError as exc:
            self._disabled = True
            self._first_frame = None
            logger.warning(f'[UI] 岛屿入口诊断保存失败，本次停止记录：{exc}')
            return False
        logger.info(
            f'[UI] 岛屿入口诊断：{reason}，距首次点击 {self._timer.current_time():.2f} 秒，'
            f'已保存 {self._saved}/3 张，目录 {self._folder}')
        return True
