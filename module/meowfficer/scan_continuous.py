"""国服从首猫开始连续读取天赋，不依赖逐屏卡片预读或返回猫窝。"""

from module.exception import RequestHumanTakeover
from module.logger import logger
from module.meowfficer.scan_capture import capture_current_cat
from module.meowfficer.scan_next import swipe_next_cat
from module.meowfficer.scan_roster import lock_independent_sort, read_roster_count, read_static_attributes


IDENTICAL_LIMIT = 5


def identical_capture(previous, current, previous_image, current_image, ocr):
    """同名同级时比较完整天赋和精确属性，不把滚动子集变化当作新猫。

    蓝猫按既定策略不读取天赋、不评分，仅比较明确品质与三项属性。
    无法完整读取时不能断言相同或不同，也不能累计到五次停止条件。
    """
    if previous.display_name != current.display_name:
        return False
    if previous.level is None or current.level is None:
        raise RequestHumanTakeover('同名猫等级未能确认，已停在天赋页并保留结果')
    if previous.level != current.level:
        return False
    if not previous.talents_complete or not current.talents_complete:
        raise RequestHumanTakeover('同名同级猫的全部天赋未能完整确认，已停在天赋页并保留结果')
    first = read_static_attributes(previous_image, ocr)
    second = read_static_attributes(current_image, ocr)
    if first is None or second is None:
        raise RequestHumanTakeover('同名同级猫的三项属性未能精确确认，已停在天赋页并保留结果')
    first_talents = sorted((t.name, t.line, t.level, t.kind) for t in previous.talents)
    second_talents = sorted((t.name, t.line, t.level, t.kind) for t in current.talents)
    return (previous.rarity, first_talents, first) == (current.rarity, second_talents, second)


def scan_continuous_detail(scanner, ocr, limit=0, on_cat=None):
    """只在启动时访问猫窝，进入首猫后保持天赋页连续读取。

    Pages:
        in: 猫窝列表顶部，已有稳定截图。
        out: 最后读取的猫的天赋页；拥有数为零时保持猫窝。

    总数来自猫窝拥有数，不用画面不变推断末猫。用户明确选择允许相同内容：
    连续第 1～4 次相同仍按下一只记录，第 5 次认定手势未生效，停止且不再记录。
    """
    total = read_roster_count(scanner.device.image, ocr)
    if total is None:
        raise RequestHumanTakeover('猫窝拥有数量未能精确确认，已停止，避免猜测扫描范围')
    logger.attr('[指挥喵-扫描] 猫窝拥有数', total)
    if total == 0:
        return scanner.scanned
    if on_cat is not None and not lock_independent_sort(scanner.device.image, ocr):
        raise RequestHumanTakeover('连续改锁需要猫窝按等级排序，请调整后重试；未操作任何锁按钮')
    target = min(total, limit) if limit > 0 else total
    identity = scanner._select_verified_card(0, ocr, None)
    if not scanner._open_talent():
        raise RequestHumanTakeover('打开首猫天赋页失败，已停止连续扫描')
    scanner._confirm_talent_identity(ocr, identity)
    # 新扫描的首猫与页面均已确认，结束上一次任务的手势阶段。
    scanner.device.click_record_remove('MEOWFFICER_NEXT')
    scanner.device.click_record_remove('SWIPE')
    scanner.device.stuck_record_clear()
    previous = None
    previous_image = None
    identical_runs = 0
    for ordinal in range(1, target + 1):
        name, level = identity
        logger.hr(f'连续读取第 {ordinal}/{target} 只：{name}', level=3)
        capture = capture_current_cat(scanner, ocr, name, level, reset_history=False)
        if not capture.identity_confirmed:
            raise RequestHumanTakeover('当前猫身份无法确认，已停在天赋页并保留结果')
        image = scanner.device.image.copy()
        same = previous is not None and identical_capture(previous, capture, previous_image, image, ocr)
        identical_runs = identical_runs + 1 if same else 0
        if identical_runs >= IDENTICAL_LIMIT:
            logger.warning('[指挥喵-扫描] 连续五次左滑后姓名、等级、天赋及属性完全相同，'
                           '按用户设置认定手势未生效；停在天赋页，保留已读结果')
            logger.attr('[指挥喵-扫描] 已记录/猫窝拥有数', f'{len(scanner.scanned)}/{total}')
            return scanner.scanned
        if same:
            logger.info(f'[指挥喵-扫描] 连续第 {identical_runs} 次内容相同，按下一只记录')
        if on_cat is not None:
            on_cat(scanner, capture)
        scanner.scanned.append((name, capture.talents, capture.level))
        logger.attr('[指挥喵-扫描] 已扫描', f'{len(scanner.scanned)}/{target} 只')
        previous, previous_image = capture, image
        # 用户允许相同内容的前四次继续；仅在完整比较并接受本次读取后结束旧阶段。
        scanner.device.click_record_remove('MEOWFFICER_NEXT')
        scanner.device.click_record_remove('SWIPE')
        scanner.device.stuck_record_clear()
        if ordinal == target:
            break
        following = swipe_next_cat(scanner, ocr, name, capture.level)
        # None 表示持续稳定地读到同名同级；完整天赋与属性在下一轮再核对。
        identity = following if following is not None else (name, capture.level)
    logger.info(f'[指挥喵-扫描] 连续读取结束，已记录 {len(scanner.scanned)}/{total} 只，停在天赋页')
    return scanner.scanned
