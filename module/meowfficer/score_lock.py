"""已有指挥喵的锁状态设置：只在已核实的当前天赋页操作。"""

from module.base.button import Button
from module.base.timer import Timer
import module.config.server as server
from module.meowfficer.advice import VERDICT_FEED, reset_advice
from module.meowfficer.assets import (TEMPLATE_MEOWFFICER_DETAIL_LOCKED,
                                      TEMPLATE_MEOWFFICER_DETAIL_UNLOCKED)
from module.meowfficer.cat_data import CATS
from module.meowfficer.score import normalize
from module.meowfficer.scan_utils import _crop, _mean_diff


LOCK_MATCH_AREA = (16, 440, 92, 526)
TALENT_CHECK_AREA = (775, 87, 945, 145)
IDENTITY_AREA = (160, 565, 455, 625)
LOCK_BUTTON = Button(area=(40, 466, 68, 494), color=(0, 0, 0),
                     button=(40, 466, 68, 494), name='MEOWFFICER_DETAIL_LOCK_TOGGLE')


def detail_page_confirmed(image) -> bool:
    """用正向标题模板确认天赋页；模板尚未校准的服务器禁止操作。"""
    if server.server != 'cn' or image is None or image.shape[:2] != (720, 1280):
        return False
    from module.meowfficer.assets import TEMPLATE_MEOWFFICER_DETAIL_TALENT_CHECK
    return TEMPLATE_MEOWFFICER_DETAIL_TALENT_CHECK.match(
        _crop(image, TALENT_CHECK_AREA), similarity=0.90)


def read_lock_state(image) -> bool | None:
    """识别互斥的已锁／未锁状态，未知或两个状态同时命中返回 None。"""
    if not detail_page_confirmed(image):
        return None
    region = _crop(image, LOCK_MATCH_AREA)
    locked = TEMPLATE_MEOWFFICER_DETAIL_LOCKED.match(region, similarity=0.90)
    unlocked = TEMPLATE_MEOWFFICER_DETAIL_UNLOCKED.match(region, similarity=0.90)
    if locked == unlocked:
        return None
    return bool(locked)


def lock_target(capture, result) -> tuple[bool | None, str]:
    """确定目标状态，只有完整、明确的最终喂掉建议才解除金紫猫保护。"""
    if not capture.identity_confirmed:
        return None, '当前猫身份未确认，不操作'
    if capture.rarity == 'R':
        return False, '蓝猫不评分，保持未锁定'
    complete = (capture.complete and capture.breed in CATS and result is not None
                and result.cat == capture.breed and bool(result.primary)
                and result.primary[0] in result.rubrics and capture.rarity in ('SR', 'SSR')
                and result.talents and len(result.talents) == len(capture.talents)
                and all(t.kind != 'unknown' and not t.inferred and t.raw
                        and normalize(t.name) in normalize(t.raw) for t in result.talents))
    if not complete:
        reason = '；'.join(capture.reasons) or '猫种、品质或全部天赋未能完整确认'
        return True, f'保护锁定：{reason}'
    try:
        advice = reset_advice(result)
    except Exception as e:
        return True, f'培养建议生成失败，保护锁定：{e}'
    if advice is None:
        return True, '培养建议未知，保护锁定'
    return advice.verdict != VERDICT_FEED, advice.headline


def new_lock_action(capture, target, reason) -> dict:
    """操作前创建可持续更新的记录，设备异常也能保留本次尝试。"""
    return {'name': capture.display_name, 'before': None, 'after': None,
            'target': target, 'status': 'skipped', 'reason': reason}


def set_lock_state(scanner, capture, target: bool | None, reason: str, entry=None) -> dict:
    """设置目标状态并截图确认，未知页面不点击，单次只切换一次。

    Pages:
        in: meowfficer_talent
        out: meowfficer_talent

    Args:
        scanner: 当前设备的猫窝扫描器。
        capture: 刚刚读取的当前猫及身份区域快照。
        target: True 为锁定，False 为解锁，None 为不操作。
        reason: 评分建议或保护原因。
        entry: 调用方提前保存的共享记录，未传入时创建新记录。

    Returns:
        dict: 原状态、目标、核验结果以及操作原因；可写入评分报告。
    """
    if entry is None:
        entry = new_lock_action(capture, target, reason)
    if target is None or server.server != 'cn' or not capture.identity_confirmed:
        return entry
    reference = capture.identity_image
    if reference is None or reference.size == 0:
        entry['reason'] += '；缺少当前猫身份快照'
        return entry

    timer = Timer(8, count=12).start()
    clicked = False
    while True:
        scanner.device.screenshot()
        current = scanner.device.image
        if not detail_page_confirmed(current):
            entry['status'] = 'unconfirmed' if clicked else 'skipped'
            entry['reason'] += '；天赋页无法确认'
            return entry
        if _mean_diff(reference, _crop(current, IDENTITY_AREA)) >= 3:
            entry['status'] = 'unconfirmed' if clicked else 'skipped'
            entry['reason'] += '；当前猫信息发生变化'
            return entry
        state = read_lock_state(current)
        if state is not None:
            if entry['before'] is None:
                entry['before'] = state
            entry['after'] = state
            if state == target:
                entry['status'] = 'changed' if clicked else 'unchanged'
                return entry
            if not clicked:
                # 已识别相反状态才点击；切换按钮再次点击会撤销前次操作，故不盲目重试。
                entry['status'] = 'unconfirmed'
                entry['after'] = None
                scanner.device.click(LOCK_BUTTON)
                clicked = True
                continue
        if timer.reached():
            entry['status'] = 'unconfirmed' if clicked else 'skipped'
            entry['reason'] += '；锁状态未能确认，未重复点击'
            return entry
