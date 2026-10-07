"""指挥喵天赋评分工具（工具）。

三种模式：

- ``screenshot``：扫描本地截图目录，逐张识别天赋并评分（默认）。
  可以直接指向 ``DropRecord_MeowfficerTalent`` 落盘的天赋截图，或自己的截图目录。
- ``device``：截图当前设备画面并评分，适合手动逐只翻猫时连续跑。
- ``scan``：自动遍历猫窝列表，逐只选中猫、打开天赋页截图识别，覆盖全部已拥有的猫。
  页面操作在 :mod:`module.meowfficer.scan` 里，本模块只负责评分与报告。

前两种模式**不操作游戏、不做页面导航**，因此按 ``module/daemon/ocr_benchmark.py`` 的形态
自写 ``__init__`` 而不继承 ``ModuleBase``，也就没有强制状态循环的约束；``scan`` 模式
的页面操作全部委托给 :class:`~module.meowfficer.scan.MeowfficerScanner`，同样不引入继承。

评分口径来自公开攻略（详见 :mod:`module.meowfficer.score`），不是游戏官方数值。
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from rich.table import Table

from deploy.atomic import atomic_write
from module.base.utils import save_image
from module.config.config import AzurLaneConfig
from module.exception import RequestHumanTakeover
from module.logger import logger
from module.meowfficer.score import evaluate
from module.meowfficer.score_report import render_text

# 支持的图片后缀
IMAGE_EXT = ('.png', '.jpg', '.jpeg', '.bmp', '.webp')


class MeowfficerScore:
    """指挥喵天赋评分任务。

    Attributes:
        config (AzurLaneConfig): 配置实例。
        device (Device): 设备实例，仅 ``device`` 模式使用，可为 ``None``。
        results (list): 本次运行收集到的评分结果，元素为
            ``(来源名, ScoreResult)``。
    """

    def __init__(self, config, device=None, task=None):
        """初始化指挥喵天赋评分任务。

        Args:
            config (AzurLaneConfig | str): ``AzurLaneConfig`` 实例或配置名（如 ``'alas'``）。
            device (Device, optional): 设备实例；``screenshot`` 模式下可为 ``None``。
            task (str, optional): 任务名，传入时会调用 ``init_task`` 绑定配置。
        """
        if isinstance(config, AzurLaneConfig):
            self.config = config
            if task is not None:
                self.config.init_task(task)
        else:
            self.config = AzurLaneConfig(config, task=task)
        self.device = device
        self.results = []
        self.lock_actions = []

    # ------------------------------------------------------------------
    # 配置读取
    # ------------------------------------------------------------------

    def _cfg(self, name, default=None):
        """读取本任务的配置项，缺省时回退到默认值。

        Args:
            name (str): 配置项名称后缀（如 'Folder', 'Source' 等）。
            default (Any, optional): 缺省默认值。

        Returns:
            Any: 配置项的取值。
        """
        return getattr(self.config, f'MeowfficerScore_{name}', default)

    # ------------------------------------------------------------------
    # 核心流程
    # ------------------------------------------------------------------

    def _collect_images(self, folder):
        """列出指定目录下待评分的截图文件。

        Args:
            folder (str): 截图所在目录路径。

        Returns:
            list[str]: 按修改时间升序排列的图片绝对路径列表。
        """
        if not folder or not os.path.isdir(folder):
            logger.warning(f'[指挥喵-评分] 截图目录不存在：{folder}')
            return []
        limit = int(self._cfg('MaxImages', 50) or 50)
        files = []
        for name in os.listdir(folder):
            if name.lower().endswith(IMAGE_EXT):
                files.append(os.path.join(folder, name))
        files.sort(key=lambda p: os.path.getmtime(p))
        if len(files) > limit:
            logger.info(f'[指挥喵-评分] 共 {len(files)} 张截图，按配置只取最近 {limit} 张')
            files = files[-limit:]
        return files

    def _score_image(self, image, ocr, name, cat=None):
        """识别单张截图中的天赋并执行评分。

        Args:
            image (np.ndarray): 截图 BGR 图像数组。
            ocr (AlOcr): 已初始化的 OCR 实例。
            name (str): 来源标识（文件名或设备截图序号），用于日志与报告。
            cat (str, optional): 手动指定的猫名；为 ``None`` 时自动识别。

        Returns:
            ScoreResult | None: 评分结果对象；截图未识别到有效天赋时返回 ``None``。
        """
        from module.meowfficer.score_ocr import recognize

        try:
            talents, detected_cat = recognize(image, ocr=ocr)
        except Exception as e:
            logger.warning(f'[指挥喵-评分] {name}：识别失败，跳过（{e}）')
            return None
        if not talents:
            logger.info(f'[指挥喵-评分] {name}：未识别到天赋，跳过')
            return None
        result = evaluate(talents, cat=cat or detected_cat)
        score = result.rubrics[result.primary[0]] if result.primary else None
        logger.attr(f'{name} 猫名', result.cat or '未知')
        if score is not None:
            logger.attr(f'{name} 评分', f'{score.label} {score.tier} {score.score100}/100')
        self.results.append((name, result))
        self._publish_report()
        return result

    def _load_ocr(self):
        """加载中文 OCR 模型。

        首次运行需要联网下载模型；失败时给出可操作的提示，而不是抛原始堆栈 ——
        否则每个模式都会在每张图上报一次 warning、最后"成功"却没有任何结果。

        Returns:
            AlOcr: 初始化成功的 OCR 实例。

        Raises:
            RequestHumanTakeover: OCR 模型下载或加载失败时抛出。
        """
        from module.ocr.al_ocr import AlOcr
        try:
            ocr = AlOcr(name='cn')
            ocr.init()
        except Exception as e:
            raise RequestHumanTakeover(
                f'OCR 模型加载失败（首次运行需联网下载，请检查网络后重试）：{e}') from e
        return ocr

    def _run_screenshots(self):
        """``screenshot`` 模式：批量评分本地截图。"""
        folder = self._cfg('Folder', './screenshots/meowfficer_talent')
        files = self._collect_images(folder)
        if not files:
            logger.warning(f'[指挥喵-评分] 目录里没有图片：{folder}')
            return
        logger.info(f'[指挥喵-评分] 待评分截图 {len(files)} 张，目录：{folder}')

        import cv2

        ocr = self._load_ocr()

        for index, path in enumerate(files, 1):
            logger.hr(f'第 {index}/{len(files)} 张', level=2)
            image = cv2.imread(path)
            if image is None:
                logger.warning(f'[指挥喵-评分] 读图失败：{path}')
                continue
            self._score_image(image, ocr, os.path.basename(path))

    @staticmethod
    def _fingerprint(image):
        """画面粗指纹：用于跳过没有变化的画面，避免空转 OCR。

        Args:
            image (np.ndarray): BGR 图像数组。

        Returns:
            str: 下采样后的 MD5 哈希字符串，画面不变则指纹不变。
        """
        import hashlib

        import numpy as np
        coarse = np.ascontiguousarray(image[::8, ::8])
        return hashlib.md5(coarse.tobytes()).hexdigest()

    def _run_device(self):
        """``device`` 模式：自动跟拍当前画面，检测到新的天赋面板就评分。

        不需要按任何按键：工具每隔 ``DeviceInterval`` 秒截一次屏，画面没变化就跳过，
        出现没见过的天赋组合（猫名 + 天赋集合）才识别评分，累计到 ``DeviceShots``
        只不同的猫为止。用户在游戏里翻猫即可，也可以直接停掉任务。
        """
        if self.device is None:
            raise RequestHumanTakeover(
                'device 模式需要连接设备：请确认模拟器已启动且 ADB 可连，'
                '或把「评分来源」改成「本地截图」')
        wanted = max(1, int(self._cfg('DeviceShots', 1) or 1))
        interval = max(0.5, float(self._cfg('DeviceInterval', 2) or 2))

        from module.meowfficer.score_ocr import recognize
        ocr = self._load_ocr()

        logger.info(f'[指挥喵-评分] 自动跟拍已开始：目标 {wanted} 只猫，轮询间隔 {interval}s，'
                    '请在游戏里逐只打开指挥喵天赋页')
        seen = set()
        last_fp = None
        polls = 0
        while len(self.results) < wanted:
            self.device.screenshot()
            fingerprint = self._fingerprint(self.device.image)
            if fingerprint == last_fp:
                time.sleep(interval)
                continue
            last_fp = fingerprint
            polls += 1

            try:
                talents, cat = recognize(self.device.image, ocr=ocr)
            except Exception as e:
                logger.warning(f'[指挥喵-评分] 识别失败，继续跟拍：{e}')
                time.sleep(interval)
                continue
            if not talents:
                time.sleep(interval)
                continue

            # 同一只猫的同一套天赋只评一次；OCR 抖动不会重复计数
            key = (cat, tuple(sorted(f'{t.line}:{t.level}' for t in talents)))
            if key in seen:
                time.sleep(interval)
                continue
            seen.add(key)

            name = f'auto_{len(self.results) + 1:02d}_{datetime.now().strftime("%H%M%S")}'
            self._score_image(self.device.image, ocr, name, cat=cat)
            logger.info(f'[指挥喵-评分] 进度 {len(self.results)}/{wanted}'
                        f'（已轮询 {polls} 次，共截图 {len(self.results)} 只）')
            time.sleep(interval)

        logger.info(f'[指挥喵-评分] 自动跟拍结束，共评出 {len(self.results)} 只')

    # ------------------------------------------------------------------
    # 输出
    # ------------------------------------------------------------------

    def _log_summary(self):
        """用 rich 表格输出本次运行的汇总。"""
        if not self.results:
            logger.warning('[指挥喵-评分] 本次没有产生任何评分结果')
            return
        table = Table(show_lines=True)
        table.add_column('来源', style='cyan', no_wrap=True)
        table.add_column('指挥喵')
        table.add_column('口径')
        table.add_column('档位')
        table.add_column('参考分', justify='right')
        for name, result in self.results:
            key = result.primary[0] if result.primary else None
            rubric = result.rubrics.get(key)
            table.add_row(
                name,
                result.cat or '未知',
                rubric.label if rubric else '-',
                rubric.tier if rubric else '-',
                f'{rubric.score100}/100' if rubric else '-',
            )
        logger.hr('评分汇总', level=1)
        logger.print(table, justify='center')

    def _publish_report(self):
        """逐只静默发布，报告失败不阻断游戏流程或掩盖原设备异常。"""
        try:
            self._save_report(quiet=True)
        except Exception as exc:
            logger.warning(f'[指挥喵-评分] 实时报告保存失败：{exc}')

    def _save_report(self, quiet=False):
        """原子更新 Markdown、HTML、JSON；逐只发布时不重复输出文件路径日志。"""
        path = self._cfg('ReportPath', './log/meowfficer_score.md')
        actions = getattr(self, 'lock_actions', [])
        scanned_count = getattr(self, 'scanned_count', None)
        if not path or (not self.results and not actions and scanned_count is None):
            return
        stamp = f'{datetime.now():%Y-%m-%d %H:%M:%S}'
        lines = ['# 指挥喵天赋评分报告', '',
                 f'生成时间：{stamp}',
                 f'共 {len(self.results)} 只', '',
                 '> 评分口径来自公开攻略（28法则执行篇 / 详细上手攻略），不是游戏官方数值。', '']
        if scanned_count is not None:
            lines[4:4] = [f'已读取 {scanned_count} 只（蓝猫跳过评分）', '']
        if actions:
            from module.meowfficer.score_report import render_lock_actions_text
            lines.extend(['## 锁定／解锁处理记录', '', render_lock_actions_text(actions), ''])
        for name, result in self.results:
            lines.append(f'## {result.cat or "未知"}（{name}）')
            lines.append('')
            lines.append('```')
            lines.append(render_text(result))
            lines.append('```')
            lines.append('')

        html_path = os.path.splitext(path)[0] + '.html'
        json_path = os.path.splitext(path)[0] + '.json'
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            atomic_write(path, '\n'.join(lines))
            if not quiet:
                logger.info(f'[指挥喵-评分] Markdown 报告已写入 {path}')
        except OSError as e:
            logger.warning(f'[指挥喵-评分] Markdown 报告写入失败：{e}')

        try:
            from module.meowfficer.score_report import render_html, to_payload
            payload = to_payload(self.results, generated_at=stamp)
            if actions:
                payload['lockActions'] = actions
            if scanned_count is not None:
                payload['scannedCount'] = scanned_count
            os.makedirs(os.path.dirname(os.path.abspath(html_path)), exist_ok=True)
            atomic_write(html_path, render_html(self.results, generated_at=stamp,
                                                lock_actions=actions, scanned_count=scanned_count))
            # JSON 最后替换，页面只能读到完整旧版或完整新版，不能撞上写了一半的文件。
            atomic_write(json_path, json.dumps(payload, ensure_ascii=False, indent=1))
            if not quiet:
                logger.info(f'[指挥喵-评分] HTML 报告已写入 {html_path}'
                            '（也可在 WebUI 打开 /reports/meowfficer_score 查看）')
                logger.info(f'[指挥喵-评分] JSON 报告已写入 {json_path}（供 WebUI 面板读取）')
        except Exception as e:
            logger.warning(f'[指挥喵-评分] HTML/JSON 报告写入失败：{e}')

    def _run_scan(self):
        """``scan`` 模式：自动遍历猫窝，逐只读取天赋并评分。

        页面操作全部交给 :class:`~module.meowfficer.scan.MeowfficerScanner`，
        这里只把每只猫的天赋送去评分并记录结果。
        """
        if self.device is None:
            raise RequestHumanTakeover(
                'scan 模式需要连接设备：请确认模拟器已启动且 ADB 可连，'
                '或把「评分来源」改成「本地截图」')

        from module.meowfficer.scan import MeowfficerScanner

        limit = max(0, int(self._cfg('ScanLimit', 0) or 0))
        passes = max(1, int(self._cfg('ScanPasses', 12) or 12))

        scanner = MeowfficerScanner(self.config, self.device)
        self.scanned_count = 0
        if self._cfg('LockByAdvice', False):
            import module.config.server as server
            if server.server == 'cn':
                self.lock_actions = []
                try:
                    scanner.scan_all(limit=limit, passes=passes, on_cat=self._score_and_apply_lock)
                except Exception:
                    # 现场接管或设备异常前保留已经核验的操作记录，不掩盖原异常。
                    self._publish_report()
                    raise
                return
            logger.warning('[指挥喵-评分] 当前服尚未校准锁状态资源，本次只评分、不操作锁定')
        reported = 0

        def on_result(current, entry):
            nonlocal reported
            self._record_scanned_scores([entry])
            reported += 1
            self.scanned_count = reported
            self._publish_report()

        try:
            scanned = scanner.scan_all(limit=limit, passes=passes, on_result=on_result)
        except Exception:
            # 连续只读扫描也可能中途接管，先评分保存已接受的猫，再传播原异常。
            try:
                self.scanned_count = len(scanner.scanned)
                self._record_scanned_scores(scanner.scanned[reported:])
            except Exception as exc:
                logger.warning(f'[指挥喵-评分] 部分结果评分失败：{exc}')
            try:
                self._save_report()
            except Exception as exc:
                logger.warning(f'[指挥喵-评分] 部分结果保存失败：{exc}')
            raise
        if not scanned:
            logger.warning('[指挥喵-评分] 扫描没有拿到任何指挥喵，'
                           '请确认游戏停留在「指挥喵 - 猫窝」页面后重试')
            return

        # 兼容未调用逐项回调的扫描器；已实时评分的前缀不能再次加入报告。
        self.scanned_count = len(scanned)
        self._record_scanned_scores(scanned[reported:])
        if reported < len(scanned):
            self._publish_report()

    def _record_scanned_scores(self, scanned):
        """记录只读扫描已接受的非空天赋，供正常结束或中途接管时保存。"""
        for cat, talents, level in scanned:
            if not talents:
                # 连续遍历计数包含按策略跳过天赋评分的蓝猫，不生成空天赋评分卡。
                continue
            result = evaluate(talents, cat=cat, level=level)
            rubric = result.rubrics[result.primary[0]] if result.primary else None
            logger.attr(f'{cat} 猫名', result.cat or '未知')
            if level is not None:
                logger.attr(f'{cat} 等级', f'Lv{level}')
            if rubric is not None:
                logger.attr(f'{cat} 评分', f'{rubric.label} {rubric.tier} {rubric.score100}/100')
            self.results.append((cat, result))

    def _score_and_apply_lock(self, scanner, capture):
        """当前猫尚在天赋页时评分、设置锁状态并记录核验结果。"""
        from module.meowfficer.score_lock import LOCK_BUTTON, lock_target, new_lock_action, set_lock_state
        result = None
        if capture.rarity != 'R' and capture.talents:
            try:
                # 自定义显示名不能模糊映射成别的猫种，按精确匹配的显示原名选择主口径。
                result = evaluate(capture.talents, cat=capture.breed, level=capture.level)
            except Exception as e:
                capture.complete = False
                capture.reasons.append(f'评分失败：{e}')
                logger.warning(f'[指挥喵-评分] {capture.display_name} 评分失败，保护锁定：{e}')
            else:
                self.results.append((capture.display_name, result))
                logger.info(f'[指挥喵-评分] {capture.display_name}\n{render_text(result)}')
        target, reason = lock_target(capture, result)
        action = new_lock_action(capture, target, reason)
        self.lock_actions.append(action)
        self.scanned_count = len(self.lock_actions)
        set_lock_state(scanner, capture, target, reason, entry=action)
        if action['status'] in ('changed', 'unchanged'):
            # 本只建议操作已正向完成，结束该按钮阶段，避免合法跨猫点击累计误报。
            scanner.device.click_record_remove(LOCK_BUTTON)
        from module.meowfficer.lock_refresh import (LOCK_REFRESH_INTERVAL, new_refresh_action,
                                                    refresh_lock_state)
        if (self.scanned_count % LOCK_REFRESH_INTERVAL == 0
                and action['status'] != 'unconfirmed'):
            refresh = new_refresh_action(capture)
            refresh['ordinal'] = self.scanned_count
            action['periodicRefresh'] = refresh
            logger.hr(f'已读取 {self.scanned_count} 只，双切锁状态刷新客户端记录', level=3)
            try:
                if action['status'] in ('changed', 'unchanged'):
                    refresh_lock_state(scanner, capture, entry=refresh)
                else:
                    refresh.update(status='unconfirmed', reason='建议锁操作未能确认，未开始周期双切')
            finally:
                if refresh['status'] == 'pending':
                    refresh.update(status='unconfirmed', reason='周期双切在开始前中断，未开始切换')
                action['reason'] += f'；每 {LOCK_REFRESH_INTERVAL} 只刷新：{refresh["reason"]}'
                if refresh['status'] != 'verified':
                    action['status'] = 'unconfirmed'
                    action['after'] = refresh['after']
        self._publish_report()
        logger.attr(f'[指挥喵-锁定] {capture.display_name}',
                    f'{action["status"]}：{action["reason"]}')
        if action['status'] == 'unconfirmed':
            raise RequestHumanTakeover('锁定切换后未能核验结果，已停止，避免重复切换；请检查当前页面')
        return action

    def run(self):
        """任务入口。

        Pages:
            in: any
            out: any
        """
        logger.hr('指挥喵天赋评分', level=1)
        source = self._cfg('Source', 'screenshot')
        self.results = []
        self.lock_actions = []
        if source == 'scan':
            # 新扫描以零进度替换上次报告，首只蓝猫也能呈现本次真实读取数量。
            self.scanned_count = 0
            self._publish_report()
        elif hasattr(self, 'scanned_count'):
            del self.scanned_count
        logger.attr('评分来源', source)
        if self._cfg('LockByAdvice', False) and source != 'scan':
            logger.info('[指挥喵-评分] 按建议锁定仅在自动遍历模式生效，本次不操作游戏')

        if source == 'device':
            self._run_device()
        elif source == 'scan':
            self._run_scan()
        else:
            self._run_screenshots()

        self._log_summary()
        self._save_report()

        if not self.results and not getattr(self, 'lock_actions', []):
            logger.error('[指挥喵-评分] 本次没有产生任何评分结果。请检查：'
                         '① 截图目录里是否有天赋面板截图（或设备是否停在指挥喵天赋页）；'
                         '② 日志里是否有 OCR 相关警告（首次运行需要联网下载模型）。')


def _save_failure_scene(device, reason):
    """只保存已有 RGB 截图与失败原因，不重新截图或读取用户配置。"""
    import numpy as np

    image = getattr(device, 'image', None) if device is not None else None
    if (not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3
            or image.size == 0 or image.dtype != np.uint8):
        return
    try:
        root = Path('log/error/meowfficer_score')
        root.mkdir(parents=True, exist_ok=True)
        # 普通 mkdir 继承父目录权限；Windows 上 tempfile 的 0700 会限制普通进程读取。
        # 时间便于查找，UUID 避免同一瞬间互相覆盖；碰撞时只换目录，不动已有原件。
        stamp = f'{datetime.now():%Y-%m-%d_%H-%M-%S-%f}'
        for _attempt in range(8):
            scene = root / f'{stamp}_{uuid4().hex}'
            try:
                scene.mkdir()
            except FileExistsError:
                continue
            break
        else:
            raise FileExistsError('无法创建唯一的指挥喵评分现场目录，已有现场已保留')
        # 设备统一截图管线输出 RGB，复用 PIL 保存，不做额外红蓝通道交换。
        save_image(image, scene / 'screen.png')
        (scene / 'reason.txt').write_text(reason + '\n', encoding='utf-8')
        logger.info(f'[指挥喵-评分] 已保存停止时的现有截图和原因：{scene}')
    except Exception as exc:
        logger.warning(f'[指挥喵-评分] 失败现场保存失败：{exc}')


def run_meowfficer_score(config, device=None):
    """工具任务包装函数，人工接管时记录原因并保存已有截图。

    Args:
        config (AzurLaneConfig | str): 配置实例或配置标识。
        device (Device, optional): 设备实例；``device`` 模式必须传入，否则会请求人工接管。

    Returns:
        bool: 成功为 ``True``，需要人工接管为 ``False``。
    """
    try:
        MeowfficerScore(config, device=device, task='MeowfficerScore').run()
        return True
    except RequestHumanTakeover as exc:
        reason = str(exc).strip() or type(exc).__name__
        logger.critical(f'[指挥喵-评分] 错误 请求人类接管：{reason}')
        _save_failure_scene(device, reason)
        return False
