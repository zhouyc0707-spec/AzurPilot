"""连续评分每十二只的锁状态双切，预防客户端旧选中项回退。"""

from time import sleep

import cv2
import numpy as np

from module.base.timer import Timer
from module.logger import logger
from module.meowfficer.scan_capture import _visible_rows
from module.meowfficer.scan_roster import STATIC_ATTRIBUTE_AREAS
from module.meowfficer.scan_utils import _crop, _mean_diff
from module.meowfficer.score_lock import (IDENTITY_AREA, LOCK_BUTTON, detail_page_confirmed,
                                          new_lock_action, read_lock_state)


LOCK_REFRESH_INTERVAL = 12


def new_refresh_action(capture):
    """操作前创建共享审计记录，任一步设备异常也能保存实际进度。"""
    return {'name': capture.display_name, 'before': None, 'after': None,
            'status': 'pending', 'reason': '等待双切并恢复原锁状态', 'steps': []}


def _same_identity(scanner, capture):
    reference = capture.identity_image
    return bool(capture.identity_confirmed and reference is not None and reference.size
                and detail_page_confirmed(scanner.device.image)
                and _mean_diff(reference, _crop(scanner.device.image, IDENTITY_AREA)) < 3)


def _full_rows(image):
    return tuple((top, bottom) for top, bottom in _visible_rows(image)
                 if top > 152 and bottom < 588 and 78 <= bottom - top <= 94)


def _visual_snapshot(image):
    """保存行框、静态属性和可见标题；避开动画立绘与跑马灯说明。"""
    rows = _full_rows(image)
    if not rows:
        return None
    attributes = tuple((area, _crop(image, area).copy()) for area in STATIC_ATTRIBUTE_AREAS)
    titles = []
    for top, _ in rows:
        area = (858, top + 8, 1110, top + 35)
        titles.append((area, _crop(image, area).copy()))
    return rows, attributes, tuple(titles)


def _same_static_text(reference, current):
    """复用静态标题的严格字形门槛，浅背景不能稀释单字或属性数字差异。"""
    reference = cv2.GaussianBlur(reference, (3, 3), 0)
    current = cv2.GaussianBlur(current, (3, 3), 0)
    difference = np.abs(reference.astype(np.int16) - current.astype(np.int16))
    if _mean_diff(reference, current) > 3 or (difference.max(axis=2) > 20).mean() > 0.02:
        return False
    ink_before = np.count_nonzero(reference.min(axis=2) < 180) >= 32
    ink_after = np.count_nonzero(current.min(axis=2) < 180) >= 32
    if ink_before != ink_after:
        return False
    if ink_before:
        similarity = float(cv2.matchTemplate(reference, current, cv2.TM_CCOEFF_NORMED)[0, 0])
        if not np.isfinite(similarity) or similarity < 0.97:
            return False
    return True


def _same_visuals(image, snapshot):
    """直接比对已有图像，不重新 OCR、读取天赋或移动列表。"""
    rows, attributes, titles = snapshot
    return (_full_rows(image) == rows and all(
        _same_static_text(reference, _crop(image, area))
        for area, reference in (*attributes, *titles)))


def refresh_lock_state(scanner, capture, entry=None):
    """间隔半秒连续点两次，随后截图确认原猫资料和原锁状态连续两次成立。

    Pages:
        in: 当前猫已完成评分和建议锁操作的天赋页。
        out: 同一只猫的天赋页；未核验完成时由上层停止保护。

    原状态来自本次最新截图，不使用建议操作前的锁状态。两次点击之间只等待
    半秒，不截图、重读天赋或滑动。设备异常直接上抛，最终失败不补第三次。
    """
    if entry is None:
        entry = new_refresh_action(capture)

    def fail(reason):
        entry.update(status='unconfirmed', reason=reason)
        logger.warning(f'[指挥喵-锁定] 周期双切未确认：{reason}')
        return entry

    timer = Timer(8, count=12).start()
    while True:
        scanner.device.screenshot()
        if not _same_identity(scanner, capture):
            return fail('双切前当前天赋页或原猫身份无法确认，未开始切换')
        original = read_lock_state(scanner.device.image)
        if original is not None:
            snapshot = _visual_snapshot(scanner.device.image)
            if snapshot is None:
                return fail('双切前可见天赋行框无法确认，未开始切换')
            entry.update(before=original, after=original)
            break
        if timer.reached():
            return fail('双切前原锁状态未能确认，未开始切换')

    logger.attr('[指挥喵-锁定] 周期双切原状态', '已锁定' if original else '未锁定')
    for index, target in enumerate((not original, original)):
        reason = '周期双切：切到反状态' if index == 0 else '周期双切：恢复原状态'
        step = new_lock_action(capture, target, reason)
        # 未读取中间帧，不能声称第一击已生效，也不能推断第二击前的实际锁状态。
        step.update(before=original if index == 0 else None, status='unconfirmed')
        entry['steps'].append(step)
        entry.update(status='unconfirmed', after=None, reason=f'{reason}尚未核验')
        scanner.device.click(LOCK_BUTTON)
        step.update(status='sent', reason=f'{reason}；点击已发送，未单独核验中间状态')
        if index == 0:
            # 用户指定的双击间隔；不放入截图状态循环，也不插入其他游戏操作。
            sleep(0.5)

    # 已发送两击仅表示指令发出，仍须用新截图确认结束时的原猫和原锁状态。
    timer = Timer(8, count=12).start()
    confirmed = 0
    pending_reason = '双切后原锁状态未能连续确认，未追加切换'
    while True:
        scanner.device.screenshot()
        if (not _same_identity(scanner, capture)
                or not _same_visuals(scanner.device.image, snapshot)):
            entry['after'] = None
            confirmed = 0
            pending_reason = '双切后原猫身份或静态资料无法确认，未追加切换'
        else:
            state = read_lock_state(scanner.device.image)
            entry['after'] = state
            confirmed = confirmed + 1 if state is original else 0
            pending_reason = '双切后原锁状态未能连续确认，未追加切换'
        if confirmed >= 2:
            entry.update(status='verified', reason='两次点击已发送，原猫可见静态资料与原锁状态连续两次确认')
            # 此双切阶段已完整结束；不清其他按钮或未确认阶段的保护记录。
            scanner.device.click_record_remove(LOCK_BUTTON)
            logger.attr('[指挥喵-锁定] 周期双切结果', '原锁状态已恢复')
            return entry
        if timer.reached():
            return fail(pending_reason)
