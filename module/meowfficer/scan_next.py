"""天赋页内切换下一只指挥喵，只在新身份确认后结束当前阶段。"""

from module.base.timer import Timer
from module.exception import RequestHumanTakeover
from module.logger import logger
from module.meowfficer.scan_utils import CURRENT_CAT_LEVEL_AREA, CURRENT_CAT_NAME_AREA, _crop, _mean_diff
from module.meowfficer.score_lock import detail_page_confirmed


MIN_SWITCH_OBSERVATIONS = 12
MAX_SWITCH_OBSERVATIONS = 40
SWITCH_TIMEOUT = 8


def _switch_view_unchanged(before, after):
    """只核验姓名、等级和实际天赋列表，资料底纹及旁边按钮动画不提供身份证据。"""
    if before is None:
        return False
    if not all(_mean_diff(_crop(before, area), _crop(after, area)) < 3
               for area in (CURRENT_CAT_NAME_AREA, CURRENT_CAT_LEVEL_AREA)):
        return False
    from module.meowfficer.scan_capture import _same_panel

    return _same_panel(before, after)


def swipe_next_cat(scanner, ocr, current_name, current_level, *, defer_same_name=False,
                   reset_history=True):
    """在立绘区域向左滑动一次，持续截图确认下一只。

    Pages:
        in: 当前猫的天赋页，当前猫的读取及锁操作已经结束。
        out: 稳定候选猫的天赋页；同名候选是否下一只仍由调用方完整比较。

    Args:
        scanner: 提供截图、手势和当前猫身份读取的扫描器。
        ocr: 已初始化的 OCR 实例。
        current_name: 刚读完的当前猫姓名。
        current_level: 当前猫已读等级，未知时为 None。
        defer_same_name: 仅连续扫描启用，将未知等级的同名候选交回完整比较。
        reset_history: 普通扫描在新姓名或等级确认后结束旧阶段；异常恢复关闭，
            由调用方在完整核验预期猫之后清理，错误跳转仍保留保护记录。

    Returns:
        tuple | None: 实际候选姓名、等级；同名同级时返回 None，不能据此断言
            已到末尾。defer_same_name 仅供连续遍历：同名候选的等级一端未知时
            交回实际读数，保留操作历史，由完整天赋及属性比较裁决是否切换。

    Raises:
        RequestHumanTakeover: 页面、身份或稳定性无法确认。
    """
    scanner.device.screenshot()
    if not detail_page_confirmed(scanner.device.image):
        raise RequestHumanTakeover('切换前无法确认天赋页，停止指挥喵扫描')
    name, level = scanner._read_current_cat(ocr)
    if name != current_name or (current_level is not None and level != current_level):
        raise RequestHumanTakeover('切换前当前猫身份不一致，停止指挥喵扫描')

    # 手势避开左侧页签、锁按钮、底部资料和右侧天赋面板。
    scanner.device.swipe((560, 350), (220, 350), duration=0.45, name='MEOWFFICER_NEXT')
    # 正常不同猫尽快返回；短暂失配不以十二次快速截图直接判失败。
    # 同名至少观察原有十二帧，之后受时间和总帧数共同约束，不重复发送手势。
    timer = Timer(SWITCH_TIMEOUT).start()
    previous = None
    stable = 0
    candidate = None
    confirmed = 0
    counts = {'frames': 0, 'pageUnknown': 0, 'viewUnstable': 0, 'nameUnknown': 0}
    last_read = None
    reason = '当前猫尚未连续确认'
    for index in range(MAX_SWITCH_OBSERVATIONS):
        if index >= MIN_SWITCH_OBSERVATIONS and timer.reached():
            break
        scanner.device.screenshot()
        counts['frames'] += 1
        image = scanner.device.image
        if not detail_page_confirmed(image):
            counts['pageUnknown'] += 1
            reason = '天赋页未能正向确认'
            previous = None
            stable = confirmed = 0
            candidate = None
            continue
        current = image.copy()
        same = _switch_view_unchanged(previous, current)
        stable = stable + 1 if same else 0
        previous = current
        if stable < 2:
            counts['viewUnstable'] += 1
            reason = '姓名、等级或天赋列表尚未连续稳定'
            confirmed = 0
            candidate = None
            continue
        name, level = scanner._read_current_cat(ocr)
        identity = (name, level)
        last_read = identity
        if not name:
            counts['nameUnknown'] += 1
            reason = '当前猫名未能读清'
            confirmed = 0
            candidate = None
            continue
        confirmed = confirmed + 1 if identity == candidate else 1
        candidate = identity
        reason = '当前猫身份尚未连续两次一致'
        changed = (name != current_name
                   or (current_level is not None and level is not None and level != current_level))
        if changed and confirmed >= 2:
            # 只清理已证实完成的阶段，未知状态不消除重复操作保护。
            if reset_history:
                scanner.device.click_record_remove('MEOWFFICER_NEXT')
                scanner.device.click_record_remove('SWIPE')
                scanner.device.stuck_record_clear()
            return identity
        if confirmed >= 2 and name == current_name:
            reason = '同名猫等级尚未完整确认'
            if counts['frames'] >= MIN_SWITCH_OBSERVATIONS:
                if identity == (current_name, current_level):
                    return None
                if defer_same_name and (current_level is None or level is None):
                    # 未证明切换，只交回当前观察，不清手势历史、不填上一只的等级。
                    logger.info('[指挥喵-扫描] 同名候选等级暂缺，交由完整天赋比较；保留切换保护')
                    return identity
    logger.attr('[指挥喵-切换] 停止诊断', {
        **counts, 'stable': stable, 'confirmed': confirmed, 'lastRead': last_read, 'reason': reason,
    })
    raise RequestHumanTakeover(
        f'立绘滑动后页面或当前猫身份无法确认：{reason}；已停止扫描，请检查游戏页面')
