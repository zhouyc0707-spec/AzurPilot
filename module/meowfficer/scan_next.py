"""天赋页内切换下一只指挥喵，只在新身份确认后结束当前阶段。"""

from module.exception import RequestHumanTakeover
from module.meowfficer.scan_utils import CURRENT_CAT_AREA, TALENT_PANEL_AREA, _crop, _mean_diff
from module.meowfficer.score_lock import detail_page_confirmed


def swipe_next_cat(scanner, ocr, current_name, current_level):
    """在立绘区域向左滑动一次，持续截图确认下一只。

    Pages:
        in: 当前猫的天赋页，当前猫的读取及锁操作已经结束。
        out: 已确认下一只的天赋页，或身份未变的当前天赋页。

    Returns:
        tuple | None: 新猫的姓名、等级；同名同级或未切换时返回 None，
            交给调用方继续核验，不能据此断言已到末尾。国服连续遍历通过
            完整天赋与静态属性比较，不返回猫窝。

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
    previous = None
    stable = 0
    candidate = None
    confirmed = 0
    for _ in range(12):
        scanner.device.screenshot()
        image = scanner.device.image
        if not detail_page_confirmed(image):
            previous = None
            stable = confirmed = 0
            candidate = None
            continue
        current = (_crop(image, CURRENT_CAT_AREA).copy(), _crop(image, TALENT_PANEL_AREA).copy())
        same = previous is not None and all(_mean_diff(a, b) < 3 for a, b in zip(previous, current))
        stable = stable + 1 if same else 0
        previous = current
        if stable < 2:
            confirmed = 0
            candidate = None
            continue
        name, level = scanner._read_current_cat(ocr)
        identity = (name, level)
        if not name:
            confirmed = 0
            candidate = None
            continue
        confirmed = confirmed + 1 if identity == candidate else 1
        candidate = identity
        changed = (name != current_name
                   or (current_level is not None and level is not None and level != current_level))
        if changed and confirmed >= 2:
            # 只清理已证实完成的阶段，未知状态不消除重复操作保护。
            scanner.device.click_record_remove('MEOWFFICER_NEXT')
            scanner.device.click_record_remove('SWIPE')
            scanner.device.stuck_record_clear()
            return identity
    if (stable >= 2 and confirmed >= 2 and candidate == (current_name, current_level)):
        return None
    raise RequestHumanTakeover('立绘滑动后页面或当前猫身份无法确认，已停止扫描，请检查游戏页面')
