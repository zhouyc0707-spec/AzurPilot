"""连续评分每十二只的锁状态双切，预防客户端旧选中项回退。"""

from module.base.timer import Timer
from module.logger import logger
from module.meowfficer.scan_utils import _crop, _mean_diff
from module.meowfficer.score_lock import (IDENTITY_AREA, LOCK_BUTTON, detail_page_confirmed,
                                          new_lock_action, read_lock_state, set_lock_state)


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


def _confirm_current(scanner, capture):
    """连续扫描上下文可用时复核完整资料，不能把同名同级的别猫当作原猫。"""
    recovery = getattr(scanner, '_meowfficer_lock_recovery', None)
    if recovery is not None:
        return recovery.target_confirmed(capture)
    scanner.device.screenshot()
    return _same_identity(scanner, capture)


def refresh_lock_state(scanner, capture, entry=None):
    """先切到反状态、确认后切回，最终确认原猫和原锁状态连续两次成立。

    Pages:
        in: 当前猫已完成评分和建议锁操作的天赋页。
        out: 同一只猫的天赋页；未核验完成时由上层停止保护。

    原状态来自本次最新截图，不使用建议操作前的锁状态。每个切换阶段最多
    点击一次；第一阶段未确认不能开始第二阶段，第二阶段失败不补第三次。
    """
    if entry is None:
        entry = new_refresh_action(capture)

    def fail(reason):
        entry.update(status='unconfirmed', reason=reason)
        logger.warning(f'[指挥喵-锁定] 周期双切未确认：{reason}')
        return entry

    timer = Timer(8, count=12).start()
    while True:
        if not _confirm_current(scanner, capture) or not _same_identity(scanner, capture):
            return fail('双切前当前天赋页或原猫身份无法确认，未开始切换')
        original = read_lock_state(scanner.device.image)
        if original is not None:
            entry.update(before=original, after=original)
            break
        if timer.reached():
            return fail('双切前原锁状态未能确认，未开始切换')

    logger.attr('[指挥喵-锁定] 周期双切原状态', '已锁定' if original else '未锁定')
    for index, target in enumerate((not original, original)):
        reason = '周期双切：切到反状态' if index == 0 else '周期双切：恢复原状态'
        step = new_lock_action(capture, target, reason)
        entry['steps'].append(step)
        entry.update(status='unconfirmed', after=None, reason=f'{reason}尚未核验')
        set_lock_state(scanner, capture, target, reason, entry=step)
        entry['after'] = step['after']
        if (step['status'] != 'changed' or step['before'] is not (not target)
                or step['after'] is not target):
            return fail(f'{reason}未能完成确认：{step["reason"]}；未追加切换')
        if index == 0:
            # 第一阶段的身份与反状态再次确认，才允许在原猫上执行恢复点击。
            entry['after'] = None
            if not _confirm_current(scanner, capture):
                return fail('第一次切换后原猫完整身份无法确认，未开始恢复点击')
            state = read_lock_state(scanner.device.image)
            entry['after'] = state
            if state is not target:
                return fail('第一次切换后反状态无法再次确认，未开始恢复点击')

    # 恢复点击已由普通锁状态循环确认；独立新帧再验证原状态，不能凭两次点击推断。
    timer = Timer(8, count=12).start()
    confirmed = 0
    while True:
        if not _confirm_current(scanner, capture):
            entry['after'] = None
            return fail('双切后原猫身份无法确认，未追加切换')
        state = read_lock_state(scanner.device.image)
        entry['after'] = state
        confirmed = confirmed + 1 if state is original else 0
        if confirmed >= 2:
            entry.update(status='verified', reason='两次切换已完成，原猫与原锁状态连续两次确认')
            # 此双切阶段已完整结束；不清其他按钮或未确认阶段的保护记录。
            scanner.device.click_record_remove(LOCK_BUTTON)
            logger.attr('[指挥喵-锁定] 周期双切结果', '原锁状态已恢复')
            return entry
        if timer.reached():
            return fail('双切后原锁状态未能连续确认，未追加切换')
