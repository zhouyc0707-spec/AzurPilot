"""扫描全部指挥喵的天赋（工具评分任务的自动遍历方式）。

与 ``screenshot`` / ``device`` 两种方式不同，本模式**自己操作游戏**：
国服只在启动时核验猫窝起点与拥有数，进入首猫后在立绘上连续左滑；
同名同级按完整天赋与属性核对，结束时保持天赋页。其他服务器沿用卡片遍历。
默认结束后统一评分；启用建议锁定时，在当前猫的天赋页内评分并确认锁状态。

实机量测结论（1280×720）：

- 猫窝列表 4 列 × 3 行，列中心 784/914/1044/1174（列距 130），
  行中心 185/331/477（行距 146）。
- 点卡片即选中该猫，「天赋」页签随后显示**这只猫**的天赋。
- 「天赋」页签未激活时模板匹配 1.000，激活后降到 0.44，
  因此可以用它的出现/消失判断页面切换是否完成，不需要额外的页面资源。
- 天赋名自带等级（每条天赋线的 1/2/3 级名字不同），所以只 OCR 天赋名即可，
  不必识别图标左下角的罗马数字徽章。
- 返回箭头可以从天赋页回到猫窝列表，且**选中状态与滚动位置都会保留**。

注意：左侧「陪玩」页是消耗材料给猫涨经验的功能页，底部有破坏性的「确认」按钮，
本模块**不进入该页、也不点击任何确认按钮**。
"""

import time

import numpy as np

from module.base.button import Button
from module.base.timer import Timer
from module.exception import RequestHumanTakeover
from module.logger import logger
from module.meowfficer.assets import MEOWFFICER_TALENT_TAB
from module.meowfficer.base import MeowfficerBase
from module.meowfficer.score import Talent
from module.meowfficer.scan_utils import _crop, _mean_diff, parse_level, pick_cat_name, scroll_offset
from module.meowfficer.score_ocr import recognize
from module.ui.assets import MEOWFFICER_GOTO_DORMMENU
from module.meowfficer.scan_utils import (CATTERY_PANEL_AREA, CATTERY_SCREEN_HEIGHT, CATTERY_SWIPE_STEP,
                                          CURRENT_CAT_AREA, CURRENT_CAT_LEVEL_AREA, CURRENT_CAT_NAME_AREA, INERT_CLICK,
                                          MAX_TALENT_SWIPES, MEOWFFICER_CATTERY_GRID, MEOWFFICER_PLAY_CONFIRM,
                                          PLAY_CONFIRM_COUNT, PLAY_CONFIRM_THRESHOLD, STABLE_TOLERANCE,
                                          TALENT_OCR_AREA, TALENT_PANEL_AREA, TALENT_TAB_OFFSET)


class MeowfficerScanner(MeowfficerBase):
    """自动遍历猫窝并识别每只猫天赋的扫描器。

    负责「取图 + 识别」与逐只访问，评分、建议与报告由
    :class:`~module.meowfficer.score_task.MeowfficerScore` 负责；可通过回调在返回列表前处理当前猫。

    Attributes:
        scanned (list[tuple[str, list[Talent], int | None]]): 扫描结果列表，按扫描顺序存储 (猫名, 天赋列表, 等级)。
        _popup_warned (bool): 是否已对阻挡界面的陪玩结算弹窗输出过告警提示。
    """

    def __init__(self, config, device=None, task=None):
        """初始化指挥喵扫描器。

        Args:
            config (AzurLaneConfig): 任务配置对象。
            device (Device, optional): 设备交互实例。
            task (str, optional): 任务名称。
        """
        super().__init__(config, device, task)
        self.scanned = []
        # 已经提示过「疑似弹窗挡住页面」（只提示一次，避免刷屏）
        self._popup_warned = False

    # ------------------------------------------------------------------
    # 基础等待
    # ------------------------------------------------------------------

    def _wait_talent_tab(self, appear: bool, timeout: float = 10.0) -> bool:
        """等待「天赋」页签出现或消失。

        页签未激活（还在猫窝列表）时模板得分 1.000，激活（已进天赋页）后降到 0.44，
        所以「消失」就代表天赋页已经打开。

        Args:
            appear (bool): True 表示等待其出现（回到列表），False 表示等待其消失（进入天赋页）。
            timeout (float): 超时等待时间（秒）。

        Returns:
            bool: 是否在超时时间内等到了期望状态。
        """
        timer = Timer(timeout, count=int(timeout / 0.3) + 3).start()
        while 1:
            self.device.screenshot()
            matched = self.appear(MEOWFFICER_TALENT_TAB, offset=TALENT_TAB_OFFSET)
            if matched == appear:
                return True
            if timer.reached():
                logger.warning(f'[指挥喵-扫描] 等待天赋页签{"出现" if appear else "消失"}超时')
                return False

    def _wait_stable(self, area: tuple, timeout: float = 4.0, tolerance: float = STABLE_TOLERANCE) -> bool:
        """等待画面稳定：连续两次截图的面板区域平均差小于阈值。

        Args:
            area (tuple[int, int, int, int]): 参与比较的面板区域 (x1, y1, x2, y2)。
            timeout (float): 超时等待时间（秒）。
            tolerance (float): 平均像素差阈值。

        Returns:
            bool: 是否在超时前判定为稳定。
        """
        timer = Timer(timeout, count=int(timeout / 0.2) + 3).start()
        last = None
        while 1:
            self.device.screenshot()
            current = _crop(self.device.image, area).copy()
            if last is not None and _mean_diff(last, current) < tolerance:
                return True
            last = current
            if timer.reached():
                logger.debug('[指挥喵-扫描] 画面稳定等待超时，按当前画面继续')
                return False
            time.sleep(0.2)

    # ------------------------------------------------------------------
    # 页面操作
    # ------------------------------------------------------------------

    def _dump_popup_debug(self) -> None:
        """把当前画面存到 log/meowfficer_popup_debug.png 以供异常排查。"""
        import os

        import cv2

        try:
            path = os.path.join('log', 'meowfficer_popup_debug.png')
            os.makedirs(os.path.dirname(path), exist_ok=True)
            cv2.imwrite(path, self.device.image)
            logger.info(f'[指挥喵-扫描] 现场截图已存到 {path}')
        except Exception as e:
            logger.warning(f'[指挥喵-扫描] 现场截图保存失败：{e}')

    def _dismiss_play_popup(self) -> bool:
        """检测「陪玩」结算弹窗，只提示、不再自动点击。

        历史教训：这里原本会自动点右下角「确定」。但实测这个判据在正常画面上并不稳定 ——
        主界面底部导航、天赋页右下角的猫立绘都可能凑出足够的金色像素；误判后点下去会落到
        底部导航/别处，把界面带到聊天窗口，再叠加重试还会触发 ALAS 的「同一按钮点击次数
        过多」保护直接把任务搞崩。连续 4 次实机运行都栽在这里。

        所以现在改成：**导航流程不再自动点它**，只提示用户手点；用户点掉之后循环会自动继续。

        Returns:
            bool: 恒为 False（不再代替用户点击）。
        """
        if not self.image_color_count(MEOWFFICER_PLAY_CONFIRM,
                                      color=MEOWFFICER_PLAY_CONFIRM.color,
                                      threshold=PLAY_CONFIRM_THRESHOLD,
                                      count=PLAY_CONFIRM_COUNT):
            return False
        # 只警告一次，避免每轮循环刷屏
        if not self._popup_warned:
            self._popup_warned = True
            logger.warning('[指挥喵-扫描] 疑似「陪玩」结算弹窗挡住了页面：'
                           '请手动点掉右下角的「确定」，任务会自动继续')
        return False

    def _looks_like_meowfficer_entry(self) -> bool:
        """检查当前画面是否属于指挥喵相关的页面。

        只用在**盲点之后**做验证：盲点前没法判断是不是主界面，但点完必须能判断
        有没有真的进到生活区/指挥喵，否则就该收手。

        Returns:
            bool: 猫窝列表 / 指挥喵页 / 生活区页 任一成立即为 True。
        """
        from module.ui.assets import DORMMENU_CHECK, MEOWFFICER_CHECK

        return (self.appear(MEOWFFICER_TALENT_TAB, offset=TALENT_TAB_OFFSET)
                or self.appear(MEOWFFICER_GOTO_DORMMENU, offset=TALENT_TAB_OFFSET)
                or self.appear(MEOWFFICER_CHECK, offset=TALENT_TAB_OFFSET)
                or self.appear(DORMMENU_CHECK, offset=(30, 30)))

    def _ensure_cattery(self) -> None:
        """确保停在猫窝列表。

        刻意**不使用** ``ui_ensure``：实测部分客户端的主界面资源与仓库不一致
        （``MAIN_GOTO_FLEET`` 只有 0.24），而 ``ui_ensure`` 在页面未知时会
        **停掉并重启游戏**，对用户极不友好。这里只用「指挥喵页」与「生活区页」
        自身的资源做两步导航（实测 0.998 / 0.990），都识别不到就交给用户手动打开。

        Raises:
            RequestHumanTakeover: 无法识别当前页面或多次尝试后仍无法进入时抛出。
        """
        from module.ui.assets import (DORMMENU_CHECK, DORMMENU_GOTO_MEOWFFICER, MAIN_GOTO_DORMMENU,
                                      MEOWFFICER_CHECK)

        # 给足时间：如果弹出「陪玩」结算弹窗，需要用户手动点掉「确定」，循环会自动继续
        timer = Timer(120, count=60).start()
        blind_clicks = 0
        self.device.stuck_record_clear()
        with self.device.stuck_timeout_override(image_stuck=180):
            while 1:
                self.device.screenshot()
                # 判定顺序很重要：**先认已知页面，全都认不出来才去猜弹窗**。
                # 反过来的话，停在天赋页时（猫窝页签不可见）会先跑弹窗判定，
                # 而天赋页右下角猫的立绘也可能让金色计数超阈值 → 误判成弹窗 →
                # 点在右下角，实测**会把聊天窗口点开**，然后一路跑偏。
                # 已在猫窝列表：天赋页签可见即代表在列表上。
                if self.appear(MEOWFFICER_TALENT_TAB, offset=TALENT_TAB_OFFSET):
                    return
                # 在指挥喵页的其它视图（天赋/陪玩）。注意不能用 MEOWFFICER_CHECK 判断：
                # 它匹配的是页面标题文字，猫窝页是「指挥喵」、天赋页变成「天赋」，会识别不到。
                # 返回箭头才是所有指挥喵子视图都有的标志（实测 0.995）。
                if self.appear(MEOWFFICER_GOTO_DORMMENU, offset=TALENT_TAB_OFFSET):
                    if self.appear_then_click(MEOWFFICER_GOTO_DORMMENU, offset=TALENT_TAB_OFFSET, interval=3):
                        logger.info('[指挥喵-扫描] 从子视图返回猫窝列表')
                        time.sleep(1.0)
                        continue
                    # 页面首入可能弹出说明弹窗
                    if self.meow_additional():
                        continue
                elif self.appear(DORMMENU_CHECK, offset=(30, 30)):
                    # 在生活区页，点「指挥喵」卡片进入
                    if self.appear_then_click(DORMMENU_GOTO_MEOWFFICER, offset=(30, 30), interval=3):
                        logger.info('[指挥喵-扫描] 从生活区进入指挥喵')
                        time.sleep(1.5)
                        continue
                elif blind_clicks < 1:
                    # 已知页面都认不出来：先点左侧空白区收起可能开着的侧栏（聊天窗口等）。
                    # 放在弹窗判定**之前**，因为侧栏比弹窗常见得多，而且这一步对弹窗也无害
                    # （弹窗是模态的，点在左侧只会落在对话框上）。
                    blind_clicks += 1
                    logger.info('[指挥喵-扫描] 未识别到已知页面，先点左侧空白区收起侧栏')
                    self.device.click(Button(area=INERT_CLICK, color=(255, 255, 255),
                                             button=INERT_CLICK, name='INERT_CLICK'))
                    time.sleep(1.2)
                    self.device.screenshot()
                    if self._looks_like_meowfficer_entry():
                        continue
                    # 侧栏收掉了还认不出来，才去猜「陪玩」结算弹窗（它会盖住整页，
                    # 页签与返回箭头都看不见，所以只能兜底）
                    if self._dismiss_play_popup():
                        continue
                    # 收掉侧栏还是认不出来：再试一次底部「生活区」（部分客户端主界面资源
                    # 与仓库不一致，没法先判断是不是主界面）。点完必须立刻验证，
                    # 进不了生活区就报错收手，避免在未知页面上继续乱点。
                    logger.info('[指挥喵-扫描] 仍未识别，尝试点击底部「生活区」')
                    self.device.click(MAIN_GOTO_DORMMENU)
                    time.sleep(1.5)
                    self.device.screenshot()
                    if not self._looks_like_meowfficer_entry():
                        logger.warning('[指挥喵-扫描] 点了「生活区」也没进生活区，'
                                       '说明当前不在主界面，停止盲目点击')
                        raise RequestHumanTakeover(
                            '当前页面不是主界面也不是指挥喵页面（点「生活区」没有反应）：'
                            '请手动打开 生活区 → 指挥喵 后再运行本任务')
                    continue
                if timer.reached():
                    self._dump_popup_debug()
                    logger.warning('[指挥喵-扫描] 页面识别情况：'
                                   f'猫窝页签={self.appear(MEOWFFICER_TALENT_TAB, offset=TALENT_TAB_OFFSET)} '
                                   f'返回箭头={self.appear(MEOWFFICER_GOTO_DORMMENU, offset=TALENT_TAB_OFFSET)} '
                                   f'指挥喵页={self.appear(MEOWFFICER_CHECK, offset=TALENT_TAB_OFFSET)} '
                                   f'生活区页={self.appear(DORMMENU_CHECK, offset=(30, 30))}')
                    raise RequestHumanTakeover(
                        '没能进入「指挥喵」页面：请先在游戏里打开 生活区 → 指挥喵，再运行本任务'
                        '（现场截图已存到 log/meowfficer_popup_debug.png）')
                time.sleep(0.5)

    def _read_current_cat(self, ocr) -> tuple:
        """读左下角「当前显示的猫」名字与等级。

        猫名可能是玩家自定义的，所以这里不强行匹配天赋库里的猫名，
        只读取姓名框并去噪后原样返回，用于身份核验与日志，不将所属舰队名
        作为候选。等级单独从经验条读取，用来辅助区分同名猫。

        Args:
            ocr (AlOcr): 已初始化的 OCR 实例。

        Returns:
            tuple[str, int | None]: (猫名, 等级)；猫名读不到返回空串，等级读不到返回 None。
        """
        from module.meowfficer.score_ocr import _iter_det_results

        fields = []
        for label, area in (('猫名', CURRENT_CAT_NAME_AREA), ('等级', CURRENT_CAT_LEVEL_AREA)):
            image = self._crop_scale(_crop(self.device.image, area))
            try:
                texts = [text for text, _score in _iter_det_results(ocr.det(image))]
            except Exception as e:
                logger.warning(f'[指挥喵-扫描] {label}识别失败：{e}')
                texts = []
            fields.append(texts)
        name = pick_cat_name(fields[0])
        level = parse_level(fields[1])
        # 注意：指挥喵**可以自定义名字**，自定义名完全可能和天赋重名
        # （用户就有一只猫叫「不动如山」），所以这里绝不能用天赋库过滤猫名
        logger.debug(f'[指挥喵-扫描] 当前猫 OCR -> 姓名 {fields[0]}，等级 {fields[1]}，取 {name!r} Lv{level}')
        return name, level

    @staticmethod
    def _crop_scale(image: np.ndarray, scale: float = 3.0) -> np.ndarray:
        """放大图像以提升小字识别率（天赋名实拍校准用的就是 3 倍）。

        Args:
            image (np.ndarray): 输入图像数组。
            scale (float): 放大倍率，默认为 3.0。

        Returns:
            np.ndarray: 放大插值后的图像数组。
        """
        import cv2
        return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_LANCZOS4)

    def _select_card(self, button, ocr, previous: str = '') -> str:
        """点击一张猫窝卡片并返回选中后的猫名。

        点空或界面还没刷新时名字不会变，这里重试一次，避免把这张卡误当成
        「已扫过的猫」跳过、从而整只猫漏掉。

        Args:
            button (Button): 卡片按钮对象。
            ocr (AlOcr): 已初始化的 OCR 实例。
            previous (str, optional): 点击前显示的猫名，默认为空。

        Returns:
            str: 选中后的猫名；读不到返回空串。
        """
        name = previous
        for attempt in range(2):
            self.device.click(button)
            time.sleep(0.35)
            self._wait_stable(CATTERY_PANEL_AREA, timeout=3)
            self.device.stuck_record_clear()
            name, _level = self._read_current_cat(ocr)
            if name and name != previous:
                return name
            if attempt == 0:
                logger.debug(f'[指挥喵-扫描] 点击后猫名未变（{previous}），重试一次')
        return name

    def _open_talent(self) -> bool:
        """点开「天赋」页签。

        Returns:
            bool: 是否成功进入天赋页。
        """
        if not self.appear(MEOWFFICER_TALENT_TAB, offset=TALENT_TAB_OFFSET):
            # 找不到页签多半是被结算弹窗盖住了，点掉再来一次
            if self._dismiss_play_popup():
                self.device.screenshot()
                if self.appear(MEOWFFICER_TALENT_TAB, offset=TALENT_TAB_OFFSET):
                    return self._open_talent()
            logger.warning('[指挥喵-扫描] 猫窝列表上找不到天赋页签，跳过这只')
            return False
        self.device.stuck_record_clear()
        self.device.click(MEOWFFICER_TALENT_TAB)
        if not self._wait_talent_tab(appear=False):
            return False
        # 这里不再额外等画面稳定：页签状态变化本身已经同步了页面切换，
        # 而紧接着的 _reset_talent_scroll 还要滑两次（约 1.3 秒），面板足够时间渲染完
        return True

    def _back_to_cattery(self) -> bool:
        """从天赋页返回猫窝列表。

        Returns:
            bool: 是否成功返回猫窝列表。
        """
        if not self.appear(MEOWFFICER_GOTO_DORMMENU, offset=TALENT_TAB_OFFSET):
            logger.warning('[指挥喵-扫描] 天赋页上找不到返回箭头')
            return False
        self.device.stuck_record_clear()
        self.device.click(MEOWFFICER_GOTO_DORMMENU)
        if not self._wait_talent_tab(appear=True):
            return False
        self._wait_stable(CATTERY_PANEL_AREA, timeout=4)
        if not self.appear(MEOWFFICER_TALENT_TAB, offset=TALENT_TAB_OFFSET):
            return False
        # 猫窝列表已正向确认，当前猫的读取阶段结束，滑动历史不带入下一猫或列表翻页。
        self.device.click_record_remove('SWIPE')
        return True

    def _read_talents(self, ocr) -> list:
        """抓取当前猫的天赋：整屏识别 + 小步上滑，直到画面不再变化。

        整屏识别是刻意的：裁到面板会把最下面那条只露出一半的天赋切掉，
        而 ``recognize`` 里的天赋库白名单会把说明文字、按钮文字自动过滤掉。

        Args:
            ocr (AlOcr): 已初始化的 OCR 实例。

        Returns:
            list[Talent]: 按天赋线去重、保留最高等级的天赋列表。
        """
        found: dict[str, Talent] = {}
        # 面板会沿用上次的滚动位置，先回顶部，否则第一条天赋会被裁掉
        self._reset_talent_scroll()
        # OCR 期间画面本来就是静止的，放宽卡死检测，避免被误判成模拟器卡死
        with self.device.stuck_timeout_override(image_stuck=180):
            for attempt in range(MAX_TALENT_SWIPES + 1):
                self.device.screenshot()
                try:
                    # 只把天赋面板交给 OCR（整屏要慢 3 倍）
                    talents, _cat = recognize(_crop(self.device.image, TALENT_OCR_AREA), ocr=ocr)
                except Exception as e:
                    logger.warning(f'[指挥喵-扫描] 天赋识别失败：{e}')
                    talents = []
                known = len(found)
                for talent in talents:
                    old = found.get(talent.line)
                    if old is None or talent.level > old.level:
                        found[talent.line] = talent
                if attempt >= MAX_TALENT_SWIPES:
                    break
                # 这次滑动没带来任何新天赋 -> 已经到底。比「等画面不再变化」少滑一次、
                # 少识别一次（每只猫约省 2 秒），扫描速度主要就靠这个。
                if attempt > 0 and len(found) == known:
                    logger.debug('[指挥喵-扫描] 滑动后没有新天赋，认为已到底')
                    break
                self._swipe_panel(TALENT_PANEL_AREA)
        logger.info(f'[指挥喵-扫描] 本只猫识别到 {len(found)} 条天赋')
        return list(found.values())

    def _swipe_panel(self, area: tuple, distance: int = 150, duration: float = 0.9) -> None:
        """在指定面板内竖直滑动。

        刻意用「小步 + 慢速」：实测大幅快滑会带惯性一次冲过好几行，
        有整行猫被跳过的风险。

        Args:
            area (tuple[int, int, int, int]): 面板区域 (x1, y1, x2, y2)。
            distance (int): 滑动距离（像素）；**正数向上滑（看后面的内容），负数向下滑（回到顶部）**。
            duration (float): 滑动耗时（秒），越长越不容易触发惯性。
        """
        x0, y0, x1, y1 = area
        x = (x0 + x1) // 2
        if distance >= 0:
            # 向上滑：从面板下方向上拖，看到后面的内容
            y_from = y1 - 60
            y_to = max(y0 + 40, y_from - distance)
        else:
            # 向下滑（回顶部）：必须从面板上方往下拖，否则起止点会被钳到几乎不动
            y_from = y0 + 60
            y_to = min(y1 - 40, y_from - distance)
        self.device.swipe((x, y_from), (x, y_to), duration=duration)
        time.sleep(0.5)
        self.device.stuck_record_clear()

    def _reset_talent_scroll(self) -> None:
        """把天赋列表滑回顶部。

        实测天赋面板会**沿用上一次的滚动位置**：上一只猫读完时列表是往下滚过的，
        直接读下一只猫会把第一条天赋裁掉（漏识别）。

        注意**不能用一次大幅度下拉**（试过 -700）：从面板上部往下拖近 400px
        会被系统/游戏当成长滑手势，实测直接把游戏导航出了指挥喵页面。
        小步多次是安全的。
        """
        for _ in range(2):
            self._swipe_panel(TALENT_PANEL_AREA, distance=-180, duration=0.5)

    def _reset_cattery_scroll(self) -> None:
        """把猫窝列表滑回顶部。

        列表同样会沿用上次的滚动位置；不复位就从中间开始扫，**上方的猫会整批漏掉**。
        多滑几次确保到底，滑到顶后继续往下滑是无害的（只会回弹）。
        """
        for _ in range(4):
            self._swipe_panel(CATTERY_PANEL_AREA, distance=-320, duration=0.6)

    def _advance_cattery_screen(self) -> int:
        """把猫窝列表整屏前进，供「不重叠逐屏扫描」使用。

        滑动距离和实际滚动量不是 1:1（实机约 1.4~1.5 倍，还有惯性），
        所以这里不猜滑动量，而是**每一步都量出实际位移并累加**，累到一屏为止。
        这样每只猫只会被访问一次，就不需要按内容去重了
        （指挥喵可以重名，按名字/天赋合并会丢猫）。

        Returns:
            int: 本次实际向上滚动的像素总数；到底滑不动时接近 0。
        """
        self.device.screenshot()
        prev = _crop(self.device.image, CATTERY_PANEL_AREA).copy()
        total = 0
        for _ in range(8):
            self._swipe_panel(CATTERY_PANEL_AREA, distance=CATTERY_SWIPE_STEP, duration=0.7)
            self.device.screenshot()
            current = _crop(self.device.image, CATTERY_PANEL_AREA).copy()
            moved = scroll_offset(prev, current)
            prev = current
            total += moved
            if moved == 0:
                break
            if total >= CATTERY_SCREEN_HEIGHT - 20:
                break
        logger.debug(f'[指挥喵-扫描] 本屏向前滚动 {total}px（一屏按 {CATTERY_SCREEN_HEIGHT}px 计）')
        return total

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    def _supports_talent_swipe(self):
        """仅启用已校准国服猫窝标记及天赋标题的快速遍历。"""
        import module.config.server as server
        return server.server == 'cn'

    @staticmethod
    def _identity_matches(actual, expected):
        """只核对预期中已经确认的字段，未知等级不能作为猫已切换的证据。"""
        return actual[0] == expected[0] and (expected[1] is None or actual[1] == expected[1])

    def _stable_cattery_frame(self):
        """持续截图确认猫窝网格稳定，滚动判断不包含标题与底部入口。"""
        previous = None
        stable = 0
        for _ in range(12):
            self.device.screenshot()
            if not self.appear(MEOWFFICER_TALENT_TAB, offset=TALENT_TAB_OFFSET):
                previous = None
                stable = 0
                continue
            current = _crop(self.device.image, (718, 133, 1245, 550)).copy()
            stable = stable + 1 if previous is not None and _mean_diff(previous, current) < 3 else 0
            if stable >= 2:
                return current
            previous = current
        raise RequestHumanTakeover('猫窝列表页面或滚动状态无法确认，已停止扫描')

    def _reset_swipe_cattery(self):
        """用滑块顶部或实际停止滚动确认起点，避免从长列表中段开始。"""
        from module.meowfficer.scan_list import cattery_at_top

        before = self._stable_cattery_frame()
        for _ in range(40):
            if cattery_at_top(self.device.image) is True:
                return
            self.device.swipe((1100, 170), (1100, 490), duration=0.6, name='MEOWFFICER_LIST_SCROLL')
            after = self._stable_cattery_frame()
            if _mean_diff(before, after) < 3:
                # 小名单可以没有滑块；向顶部滚动后网格未移动，且页面持续正向确认。
                if cattery_at_top(self.device.image) is not False:
                    return
                raise RequestHumanTakeover('猫窝滚动没有进展且未到顶部，已停止扫描')
            self.device.click_record_remove('MEOWFFICER_LIST_SCROLL')
            before = after
        raise RequestHumanTakeover('猫窝列表仍未确认顶部，已停止，避免遗漏前面的指挥喵')

    def _advance_verified_page(self):
        """实测并对齐三行滚动；末页只允许已确认的整行重叠。"""
        from module.meowfficer.scan_list import selected_card

        before = self._stable_cattery_frame()
        total = 0
        ratio = 1.4
        reached_end = False
        for _ in range(8):
            remaining = CATTERY_SCREEN_HEIGHT - total
            if abs(remaining) <= 3:
                break
            distance = min(120, max(8, round(abs(remaining) / ratio)))
            forward = remaining > 0
            start = (1100, 450) if forward else (1100, 170)
            end = (1100, start[1] - distance if forward else start[1] + distance)
            self.device.swipe(start, end, duration=0.5, name='MEOWFFICER_LIST_SCROLL')
            after = self._stable_cattery_frame()
            moved = scroll_offset(before, after) if forward else scroll_offset(after, before)
            if moved == 0:
                if forward and _mean_diff(before, after) < 3:
                    reached_end = True
                    break
                raise RequestHumanTakeover('猫窝滚动位移无法确认，已停止，避免跳过指挥喵')
            first, second = (before, after) if forward else (after, before)
            overlap_first, overlap_second = first[moved:], second[:-moved]
            difference = np.abs(overlap_first.astype(np.int16) - overlap_second.astype(np.int16))
            if (_mean_diff(overlap_first, overlap_second) > 5
                    or (difference.max(axis=2) > 20).mean() > 0.1):
                raise RequestHumanTakeover('猫窝滚动前后内容不匹配，已停止，避免误算位移')
            total += moved if forward else -moved
            ratio = min(3.0, max(0.5, moved / distance))
            self.device.click_record_remove('MEOWFFICER_LIST_SCROLL')
            before = after
        rows = round(total / 146)
        if (rows < 0 or rows > 3 or abs(total - rows * 146) > 3
                or (rows < 3 and not reached_end)):
            raise RequestHumanTakeover('猫窝翻屏没有对齐完整行，已停止，避免重复或遗漏指挥喵')
        # 不足三行时，旧末只仍可见；用其选中圈独立核对实际位移。
        if rows in (1, 2) and selected_card(self.device.image) != 11 - rows * 4:
            raise RequestHumanTakeover('猫窝末屏的重叠位置无法确认，已停止扫描')
        logger.attr('[指挥喵-扫描] 翻屏', f'{total}px，{rows} 行')
        return rows

    def _select_verified_card(self, index, ocr, expected):
        """正向确认目标卡片被选中、姓名等级稳定后才打开天赋页。"""
        from module.meowfficer.scan_list import card_center, selected_card

        x, y = card_center(index)
        button = Button(area=(x - 10, y - 10, x + 10, y + 10), color=(0, 0, 0),
                        button=(x - 10, y - 10, x + 10, y + 10), name=f'MEOWFFICER_SCAN_CARD_{index}')
        previous = None
        stable = 0
        for attempt in range(2):
            if not self.appear(MEOWFFICER_TALENT_TAB, offset=TALENT_TAB_OFFSET):
                raise RequestHumanTakeover('无法确认猫窝列表，停止选择指挥喵')
            self.device.click(button)
            for _ in range(8):
                self.device.screenshot()
                if (not self.appear(MEOWFFICER_TALENT_TAB, offset=TALENT_TAB_OFFSET)
                        or selected_card(self.device.image) != index):
                    previous = None
                    stable = 0
                    continue
                identity = self._read_current_cat(ocr)
                stable = stable + 1 if identity == previous else 1
                previous = identity
                if (stable >= 2 and identity[0]
                        and (expected is None or self._identity_matches(identity, expected))):
                    self.device.stuck_record_clear()
                    return identity
            logger.warning(f'[指挥喵-扫描] 第 {index + 1} 格选中状态或身份未确认，第 {attempt + 1} 次尝试')
        raise RequestHumanTakeover('未能确认目标猫卡片的位置与身份，已停止，避免重复读取或错配')

    def _confirm_talent_identity(self, ocr, expected):
        """持续截图确认天赋标题与两次相同的当前猫身份。"""
        from module.meowfficer.score_lock import detail_page_confirmed

        stable = 0
        for _ in range(12):
            self.device.screenshot()
            matched = (detail_page_confirmed(self.device.image)
                       and self._read_current_cat(ocr) == expected)
            stable = stable + 1 if matched else 0
            if stable >= 2:
                return
        raise RequestHumanTakeover('天赋页或目标猫身份未能正向确认，已停止指挥喵扫描')

    def _confirm_cattery_position(self, index, before=None, other_index=None):
        """连续两帧确认选中位置；有基准截图时同时核验全页顺序。"""
        from module.meowfficer.scan_list import cattery_order_unchanged, selected_card

        # 整个面板均值稳定不代表选择环动画已经完成；未知帧只等待新截图。
        stable = 0
        previous = None
        reason = '未能正向确认猫窝列表页'
        for _ in range(12):
            self.device.screenshot()
            if not self.appear(MEOWFFICER_TALENT_TAB, offset=TALENT_TAB_OFFSET):
                stable = 0
                reason = '未能正向确认猫窝列表页'
                continue
            selected = selected_card(self.device.image)
            if selected is None or selected not in (index, other_index):
                stable = 0
                actual = '未知' if selected is None else f'第 {selected + 1} 格'
                reason = f'猫窝选中位置未通过核验：预期第 {index + 1} 格，实际 {actual}'
                continue
            if before is not None and not cattery_order_unchanged(before, self.device.image):
                stable = 0
                reason = '猫窝名单顺序或滚动视口未通过核验'
                continue
            reason = '猫窝返回状态尚未连续两帧确认'
            stable = stable + 1 if selected == previous else 1
            previous = selected
            if stable >= 2:
                return selected
        raise RequestHumanTakeover(f'{reason}；已停止扫描，避免重复读取或错配')

    def _return_verified_list(self, before, index, ocr=None, entry_identity=None, current_identity=None):
        """以相同选中状态核验完整名单，再恢复刚读完的猫作为下一步锚点。

        Pages:
            in: 刚读完的猫的天赋页。
            out: 猫窝列表，完整名单与原视口一致，刚读完的猫正向选中。
        """
        from module.meowfficer.scan_list import selected_card

        anchor = selected_card(before)
        if anchor is None:
            raise RequestHumanTakeover('进入天赋页前的猫窝选中位置未知，已停止扫描')
        if anchor != index and (ocr is None or entry_identity is None or current_identity is None):
            raise RequestHumanTakeover('猫窝选中状态核验缺少入口或当前猫身份，已停止扫描')
        if not self._back_to_cattery():
            raise RequestHumanTakeover('未能返回猫窝列表，已停止指挥喵扫描')
        if anchor == index:
            self._confirm_cattery_position(index, before=before)
            return

        # 选中圈会遮住姓名及等级笔画，不能把圈换位后的图像直接当成名单变化。
        # 先确认返回位置，再恢复基准图的选择状态；不裁掉文字、不放宽比较阈值。
        selected = self._confirm_cattery_position(index, other_index=anchor)
        if selected != anchor:
            self._select_verified_card(anchor, ocr, entry_identity)
        self._confirm_cattery_position(anchor, before=before)
        # 恢复刚读完的卡位，保留页末滚动重叠核验所需的最后一只锚点。
        self._select_verified_card(index, ocr, current_identity)

    def _scan_by_swipe(self, ocr, limit, passes, on_cat):
        """保留每屏位置锚点，在相邻身份可区分时连续左滑读取。"""
        from module.meowfficer.scan_capture import capture_current_cat
        from module.meowfficer.scan_list import card_is_empty, read_card_identity
        from module.meowfficer.scan_next import swipe_next_cat

        start_index = 0
        for page in range(1, passes + 1):
            self.device.screenshot()
            if not self.appear(MEOWFFICER_TALENT_TAB, offset=TALENT_TAB_OFFSET):
                raise RequestHumanTakeover('猫窝列表无法确认，已停止指挥喵扫描')
            page_image = self.device.image.copy()
            # 名称和等级只作下一格预期，最终身份必须在实际选中的猫上再次确认。
            identities = [read_card_identity(page_image, index, ocr) for index in range(12)]
            occupied = [index for index in range(start_index, 12) if not card_is_empty(page_image, index)]
            if not occupied:
                break
            identity = None
            in_detail = False
            for offset, index in enumerate(occupied):
                if not in_detail:
                    identity = self._select_verified_card(index, ocr, identities[index])
                    before = self.device.image.copy()
                    entry_identity = identity
                    if not self._open_talent():
                        raise RequestHumanTakeover('打开天赋页失败，已停止指挥喵扫描')
                    self._confirm_talent_identity(ocr, identity)
                    in_detail = True
                cat, level = identity
                logger.hr(f'第 {page} 屏 第 {index + 1} 张：{cat}', level=3)
                action = None
                if on_cat is None:
                    talents = self._read_talents(ocr)
                else:
                    capture = capture_current_cat(self, ocr, cat, level)
                    talents = capture.talents
                    action = on_cat(self, capture)
                if talents or on_cat is not None:
                    self.scanned.append((cat, talents, level))
                    logger.attr('[指挥喵-扫描] 已扫描', f'{len(self.scanned)} 只')
                else:
                    logger.warning(f'[指挥喵-扫描] {cat} 没识别到天赋，跳过评分')

                reached_limit = limit > 0 and len(self.scanned) >= limit
                next_index = occupied[offset + 1] if offset + 1 < len(occupied) else None
                expected = identities[next_index] if next_index is not None else None
                changed_lock = action and action.get('status') == 'changed'
                # 同名同级不能靠画面内容区分，提前返回列表按下一格正向选中。
                distinguishable = (expected is not None and
                                   (expected[0] != cat or
                                    (expected[1] is not None and level is not None and expected[1] != level)))
                can_swipe = (not reached_limit and not changed_lock and next_index == index + 1
                             and distinguishable)
                if can_swipe:
                    following = swipe_next_cat(self, ocr, cat, level)
                    if following is not None:
                        if not self._identity_matches(following, expected):
                            raise RequestHumanTakeover(
                                f'立绘切换后的猫与下一格身份不一致：实际 {following[0]} Lv{following[1]}，'
                                f'预期 {expected[0]} Lv{expected[1]}；停止，避免漏猫或错配')
                        identity = following
                        logger.info(f'[指挥喵-扫描] 天赋页左滑切换到 {identity[0]} Lv{identity[1]}')
                        continue
                    logger.info('[指挥喵-扫描] 左滑未确认下一只，返回猫窝按位置核验')
                self._return_verified_list(before, index, ocr=ocr,
                                           entry_identity=entry_identity, current_identity=identity)
                in_detail = False
                if reached_limit:
                    logger.info(f'[指挥喵-扫描] 已达到上限 {limit} 只，结束')
                    return self.scanned
            logger.info(f'[指挥喵-扫描] 第 {page} 屏结束，累计 {len(self.scanned)} 只')
            if page == passes or len(occupied) < 12:
                break
            rows = self._advance_verified_page()
            if rows == 0:
                break
            # 末尾不足一屏，前面的可见行已经读取，只访问旧末只之后的新行。
            start_index = 12 - rows * 4
        return self.scanned

    def scan_all(self, limit: int = 0, passes: int = 12, on_cat=None, on_result=None) -> list:
        """遍历猫窝列表，返回每只猫的天赋。

        指挥喵**可以自定义名字**，自定义名甚至可能和天赋名一样（用户就有一只猫叫
        「不动如山」），而且**可以重名**，所以这里刻意**不做任何按内容的去重**：
        按列表位置分别记录；国服可核验相邻身份时直接在天赋页左滑。

        Args:
            limit (int): 最多扫描多少只猫；``0`` 表示不限。
            passes (int): EN／JP／TW 最多翻几屏；CN 按实际拥有数连续读取。
            on_cat (Callable, optional): 当前猫仍在天赋页时的逐猫回调，接收
                ``(scanner, ScanCapture)``；启用后严格检查身份和天赋完整性。
            on_result (Callable, optional): 接受一条读取结果后、访问下一只前的只读回调，
                接收 ``(scanner, (猫名, 天赋列表, 等级))``；不启用锁操作或改变排序要求。

        Returns:
            list[tuple[str, list[Talent], int | None]]: ``(猫名, 天赋列表, 等级)`` 列表；
            等级读不到时为 ``None``。
        """
        self.scanned = []
        ocr = self._load_ocr()

        self._ensure_cattery()
        if self._supports_talent_swipe():
            self._reset_swipe_cattery()
        else:
            self._reset_cattery_scroll()
        self.device.stuck_record_clear()
        logger.hr('扫描全部指挥喵', level=2)
        if self._supports_talent_swipe():
            logger.info('[指挥喵-扫描] 从首猫连续左滑读取，按猫窝拥有数结束；'
                        f'扫描上限 {limit if limit > 0 else "不限"} 只')
            from module.meowfficer.scan_continuous import scan_continuous_detail
            return scan_continuous_detail(self, ocr, limit=limit, on_cat=on_cat, on_result=on_result)

        logger.info(f'[指挥喵-扫描] 最多 {passes} 屏，上限 '
                    f'{limit if limit > 0 else "不限"} 只')

        for page in range(1, passes + 1):
            read_in_page = 0
            previous = ''
            for index, button in enumerate(MEOWFFICER_CATTERY_GRID.buttons, 1):
                if limit > 0 and len(self.scanned) >= limit:
                    logger.info(f'[指挥喵-扫描] 已达到上限 {limit} 只，结束')
                    return self.scanned

                if on_cat is not None:
                    self.device.screenshot()
                    if not self.appear(MEOWFFICER_TALENT_TAB, offset=TALENT_TAB_OFFSET):
                        raise RequestHumanTakeover('猫窝列表无法确认，停止自动锁定，请检查当前页面')
                cat = self._select_card(button, ocr, previous=previous)
                if cat:
                    previous = cat
                if not cat and self._dismiss_play_popup():
                    # 中途弹出的结算弹窗会让猫名读不出来，点掉后再试一次
                    cat = self._select_card(button, ocr, previous=previous)
                    if cat:
                        previous = cat
                if not cat:
                    logger.warning(f'[指挥喵-扫描] 第 {page} 屏第 {index} 张卡片没读到猫名，跳过')
                    continue

                logger.hr(f'第 {page} 屏 第 {index} 张：{cat}', level=3)
                cattery_before = (_crop(self.device.image, CATTERY_PANEL_AREA).copy()
                                  if on_cat is not None else None)
                if not self._open_talent():
                    continue
                if on_cat is not None:
                    from module.meowfficer.score_lock import detail_page_confirmed
                    timer = Timer(10, count=12).start()
                    while True:
                        self.device.screenshot()
                        if detail_page_confirmed(self.device.image):
                            break
                        if timer.reached():
                            raise RequestHumanTakeover('天赋页未能正向确认，停止自动锁定，请检查游戏页面')
                # 防串数据：面板切换有一瞬间可能还显示上一只猫的内容，
                # 用左下角猫名核对，不一致就跳过这只（宁可漏也不要错配）
                shown, level = self._read_current_cat(ocr)
                if shown and shown != cat:
                    logger.warning(f'[指挥喵-扫描] 天赋页显示的是 {shown}，与选中的 {cat} 不一致，跳过')
                    returned = self._back_to_cattery()
                    if on_cat is not None and not returned:
                        raise RequestHumanTakeover('当前猫不一致且未能返回猫窝，停止自动锁定')
                    previous = shown
                    continue
                action = None
                if on_cat is None:
                    talents = self._read_talents(ocr)
                else:
                    from module.meowfficer.scan_capture import capture_current_cat
                    capture = capture_current_cat(self, ocr, cat, level)
                    talents = capture.talents
                    # 回调必须发生在返回列表之前，不能按猫名回查（允许同名猫）。
                    action = on_cat(self, capture)
                if not self._back_to_cattery():
                    if on_cat is not None:
                        raise RequestHumanTakeover('未能返回猫窝列表，停止自动锁定，请检查游戏页面')
                    # 回不去就重进页面，避免后面所有操作都落在错误页面上
                    logger.warning('[指挥喵-扫描] 返回猫窝列表失败，重新进入指挥喵页面')
                    self._ensure_cattery()
                    previous = ''
                elif action and action.get('status') == 'changed':
                    # 锁定筛选或排序可能使卡片重排，继续按旧位置访问会漏猫。
                    if not self._cattery_order_unchanged(cattery_before, index - 1):
                        raise RequestHumanTakeover(
                            '锁状态修改后猫窝列表发生变化，已停止扫描；请关闭锁定筛选或排序后重试')
                if not talents and on_cat is None:
                    logger.warning(f'[指挥喵-扫描] {cat} 没识别到天赋，跳过')
                    continue

                read_in_page += 1
                self.scanned.append((cat, talents, level))
                if on_result is not None:
                    on_result(self, self.scanned[-1])
                logger.attr('[指挥喵-扫描] 已扫描', f'{len(self.scanned)} 只')

            logger.info(f'[指挥喵-扫描] 第 {page} 屏结束，读到 {read_in_page} 只，'
                        f'累计 {len(self.scanned)} 只')
            if read_in_page == 0:
                logger.info('[指挥喵-扫描] 本屏一张卡片都没读到，结束')
                break
            # 整屏前进，保证不重叠（每只猫只访问一次，所以不需要去重）
            if self._advance_cattery_screen() < 40:
                logger.info('[指挥喵-扫描] 列表没有继续滚动，结束')
                break

        logger.info(f'[指挥喵-扫描] 扫描完成，共 {len(self.scanned)} 只猫')
        return self.scanned

    def _cattery_order_unchanged(self, before, selected_index) -> bool:
        """逐格核对其余卡片，排除当前猫锁图标的变化，防止筛选／排序重排。"""
        panel = _crop(self.device.image, CATTERY_PANEL_AREA)
        x0, y0, _, _ = CATTERY_PANEL_AREA
        for index, button in enumerate(MEOWFFICER_CATTERY_GRID.buttons):
            if index == selected_index:
                continue
            left, top, right, bottom = button.area
            area = (max(0, left - x0 - 30), max(0, top - y0 - 35),
                    min(panel.shape[1], right - x0 + 15), min(panel.shape[0], bottom - y0 + 30))
            if _mean_diff(_crop(before, area), _crop(panel, area)) > 1:
                return False
        return True

    def _load_ocr(self):
        """加载中文 OCR 模型（与评分任务一致的失败提示）。

        Returns:
            AlOcr: 已初始化成功的 OCR 实例。

        Raises:
            RequestHumanTakeover: OCR 模型加载失败时抛出。
        """
        from module.exception import RequestHumanTakeover
        from module.ocr.al_ocr import AlOcr
        try:
            ocr = AlOcr(name='cn')
            ocr.init()
        except Exception as e:
            raise RequestHumanTakeover(
                f'OCR 模型加载失败（首次运行需联网下载，请检查网络后重试）：{e}') from e
        return ocr
