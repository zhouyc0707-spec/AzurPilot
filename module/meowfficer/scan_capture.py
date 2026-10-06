"""已有指挥喵自动锁定所需的保守识别。

锁定操作与扫描遍历由调用方负责。本模块只收集当前天赋页的身份、品质与天赋，
保留不完整原因；单屏可覆盖全部天赋时才给出可用于自动解锁的完整结果。
"""

from dataclasses import dataclass, field

import numpy as np

from module.exception import (EmulatorNotRunningError, GameBugError, GameNotRunningError,
                              GamePageUnknownError, GameStuckError, GameTooManyClickError,
                              RequestHumanTakeover, ScriptEnd, ScriptError)
from module.meowfficer.cat_data import CATS
from module.meowfficer.scan_utils import _crop, _mean_diff
from module.meowfficer.score import TALENT_INDEX, Talent, normalize
from module.meowfficer.score_ocr import build_variants


# 范围来自 1280×720 天赋页；避开锁按钮、舰队名与右侧面板的滚动内容。
IDENTITY_AREA = (160, 565, 455, 625)
RARITY_AREA = (36, 563, 151, 625)
PANEL_AREA = (744, 152, 1244, 588)
MAX_SCROLL_STEPS = 8
_FLOW_ERRORS = (EmulatorNotRunningError, GameBugError, GameNotRunningError,
                GamePageUnknownError, GameStuckError, GameTooManyClickError,
                RequestHumanTakeover, ScriptEnd, ScriptError)


@dataclass
class ScanCapture:
    """本次选中猫的识别结果；不完整数据只能用于报告，不能据此解锁。"""

    display_name: str
    talents: list[Talent]
    level: int | None
    breed: str | None
    rarity: str | None
    complete: bool
    identity_confirmed: bool
    reasons: list[str] = field(default_factory=list)
    identity_image: np.ndarray | None = None


def _add_reason(reasons, text):
    """同一失败原因只记录一次。"""
    if text not in reasons:
        reasons.append(text)


def _exact_breed(display_name):
    """原名或明确的「限定＋原名」才可确定评分猫种，不使用模糊匹配。"""
    name = normalize(display_name)
    if name.startswith('限定'):
        name = name[2:]
    return next((cat for cat in CATS if normalize(cat) == name), None)


def _details(ocr, image, reasons, context):
    """读取原始行信息，OCR 失败记录为不完整，游戏控制异常仍向上传播。"""
    try:
        results = ocr.det(image)
    except _FLOW_ERRORS:
        raise
    except Exception:
        _add_reason(reasons, f'{context} OCR 失败')
        return None
    details = []
    for item in results or []:
        if isinstance(item, dict):
            text, score = item.get('text', ''), item.get('score', 0)
        elif isinstance(item, (tuple, list)) and len(item) >= 3:
            text, score = item[0], item[2]
        else:
            _add_reason(reasons, f'{context} 缺少 OCR 置信度')
            return None
        if text and str(text).strip():
            try:
                confidence = float(score)
            except (TypeError, ValueError):
                _add_reason(reasons, f'{context} OCR 置信度无效')
                return None
            details.append((str(text), confidence))
    return details


def _read_rarity(image, ocr, reasons):
    """在独立品质区域正向识别 SSR/SR/R，两种预处理必须一致。"""
    found = []
    for variant in build_variants(_crop(image, RARITY_AREA)).values():
        details = _details(ocr, variant, reasons, '品质')
        valid = {(normalize(text).upper(), score) for text, score in details or []}
        if len(valid) != 1:
            _add_reason(reasons, '品质标记未能明确识别')
            return None
        text, confidence = next(iter(valid))
        if text not in ('SSR', 'SR', 'R') or not np.isfinite(confidence) or confidence < 0.9:
            _add_reason(reasons, '品质标记未能明确识别')
            return None
        found.append(text)
    if len(set(found)) != 1:
        _add_reason(reasons, '品质标记多次识别不一致')
        return None
    return found[0]


def _visible_rows(image):
    """独立检测浅青色图标底框，包含上下边缘的半截框。

    两条窄带位于图标两侧，不依赖 OCR 是否读到名字。颜色条件不依赖 RGB/BGR
    的通道顺序；不满足实拍几何时由调用方保护处理，不能把检测失败当成零天赋。
    """
    bands = np.concatenate((image[152:588, 756:762], image[152:588, 835:842]), axis=1)
    bright = bands.max(axis=2)
    dark = bands.min(axis=2)
    difference = bright.astype(np.int16) - dark.astype(np.int16)
    colored = (dark >= 185) & (bright >= 218) & (difference >= 22) & (difference <= 60)
    active = colored.mean(axis=1) >= 0.55
    # 抗锯齿会让边框断开一两像素，只填短缺口，不跨越正常的行间距。
    positions = np.flatnonzero(active)
    if not len(positions):
        return []
    groups = np.split(positions, np.flatnonzero(np.diff(positions) > 4) + 1)
    return [(int(group[0]) + 152, int(group[-1]) + 153)
            for group in groups if len(group) >= 5]


def _read_rows(image, ocr, reasons):
    """每个独立完整图标框必须对应一个精确已知的标题，两个变体相互核对。"""
    rows = _visible_rows(image)
    if not rows:
        _add_reason(reasons, '没有确认到天赋图标行')
        return [], False
    talents = []
    valid = True
    if any(not 98 <= second[0] - first[0] <= 106 for first, second in zip(rows, rows[1:])):
        _add_reason(reasons, '天赋行框间距异常，不能排除漏行')
        valid = False
    for top, bottom in rows:
        height = bottom - top
        if top <= 152 or bottom >= 588 or not 78 <= height <= 94:
            _add_reason(reasons, '天赋行被裁切或行框不完整')
            valid = False
            continue
        crop = _crop(image, (855, top + 3, 1120, top + 42))
        matches = []
        for variant in build_variants(crop).values():
            details = _details(ocr, variant, reasons, '天赋标题')
            if details is None or len(details) != 1:
                _add_reason(reasons, '天赋图标行与识别标题数量不一致')
                valid = False
                break
            raw, confidence = details[0]
            ref = TALENT_INDEX.get(normalize(raw))
            if ref is None or not np.isfinite(confidence) or confidence < 0.9:
                _add_reason(reasons, '天赋标题不是高置信度的精确已知名称')
                valid = False
                break
            matches.append((ref, raw))
        if len(matches) != 2:
            continue
        if matches[0][0].name != matches[1][0].name:
            _add_reason(reasons, '同一天赋行多次识别不一致')
            valid = False
            continue
        ref, raw = matches[0]
        talents.append(Talent(name=ref.name, line=ref.line, level=ref.level, kind=ref.kind, raw=raw))
    if len({talent.line for talent in talents}) != len(talents):
        _add_reason(reasons, '不同天赋行识别为重复天赋线')
        valid = False
    if len(talents) != len(rows):
        valid = False
    return talents, valid


def _same_panel(before, after):
    """行框实际位移也算变化，避免整面板平均差稀释了小范围滚动。"""
    return (_visible_rows(before) == _visible_rows(after)
            and _mean_diff(_crop(before, PANEL_AREA), _crop(after, PANEL_AREA)) < 3)


def _stable_frame(scanner):
    """通过持续截图确认两次连续稳定；截图与设备异常不在这里捕获。"""
    previous = scanner.device.image.copy()
    unchanged = 0
    for _ in range(8):
        scanner.device.screenshot()
        current = scanner.device.image.copy()
        unchanged = unchanged + 1 if _same_panel(previous, current) else 0
        if unchanged >= 2:
            return scanner.device.image.copy(), True
        previous = current
    return scanner.device.image.copy(), False


def _scroll(scanner, toward_bottom, reference):
    """在天赋列表内部小步滑动，随后由截图确认实际状态，不添加固定等待。"""
    from module.meowfficer.score_lock import detail_page_confirmed

    current = scanner.device.image
    if not detail_page_confirmed(current) or _mean_diff(reference, _crop(current, IDENTITY_AREA)) >= 3:
        raise RequestHumanTakeover('天赋页或当前猫身份发生变化，停止读取与滑动')
    start, end = ((980, 520), (980, 360)) if toward_bottom else ((980, 250), (980, 410))
    scanner.device.swipe(start, end, duration=0.45)


def capture_current_cat(scanner, ocr, display_name, level):
    """读取当前选中猫，给出自动修改锁状态前需要的保守判断。

    Pages:
        in: 当前猫的天赋页，已有可用的 1280×720 截图。
        out: 同一只猫的天赋页；本函数不点击锁按钮、不修改天赋。

    滚动场景仍收集已确认天赋用于报告，但目前不能独立证明所有行已被完整读取，
    因而保持 ``complete=False``。读取和滚动次数有限，不能用「没有新名字」证明到底。
    """
    reasons = []
    capture = ScanCapture(display_name, [], level, _exact_breed(display_name), None, False, False, reasons)
    image = scanner.device.image
    if image is None or image.shape != (720, 1280, 3):
        reasons.append('当前截图不是 1280×720 天赋页')
        return capture
    from module.meowfficer.score_lock import detail_page_confirmed

    if not detail_page_confirmed(image):
        raise RequestHumanTakeover('当前画面未能正向确认天赋页，停止读取与滑动')
    shown, actual_level = scanner._read_current_cat(ocr)
    if not shown or normalize(shown) != normalize(display_name) \
            or (level is not None and actual_level != level):
        reasons.append('当前猫名或等级与本次选中猫不一致')
        return capture
    capture.level = actual_level
    capture.identity_image = _crop(image, IDENTITY_AREA).copy()
    capture.identity_confirmed = True
    capture.rarity = _read_rarity(image, ocr, reasons)
    expected_rarity = CATS.get(capture.breed or '', {}).get('rarity')
    if expected_rarity and capture.rarity is not None and expected_rarity != capture.rarity:
        _add_reason(reasons, '品质标记与已知猫种不一致')
        capture.rarity = None
    if capture.rarity == 'R':
        capture.complete = True
        return capture
    if capture.breed is None:
        _add_reason(reasons, '自定义猫名未能确定原始猫种')
    frame, stable = _stable_frame(scanner)
    if not stable:
        _add_reason(reasons, '天赋面板未稳定')
    top_confirmed = False
    same = 0
    steps = 0
    for _ in range(4):
        before = frame.copy()
        _scroll(scanner, toward_bottom=False, reference=capture.identity_image)
        steps += 1
        frame, stable = _stable_frame(scanner)
        if not stable:
            _add_reason(reasons, '天赋面板未稳定')
        same = same + 1 if stable and _same_panel(before, frame) else 0
        if same >= 2:
            rows = _visible_rows(frame)
            top_confirmed = bool(rows and 153 <= rows[0][0] <= 157)
            break
    if not top_confirmed:
        _add_reason(reasons, '未确认天赋列表顶部')
    talents, all_rows_known = _read_rows(frame, ocr, reasons)
    top_talents = [(talent.name, talent.level) for talent in talents]
    found = {talent.line: talent for talent in talents}
    single_screen = True
    bottom_confirmed = False
    same = 0
    while steps < MAX_SCROLL_STEPS:
        before = frame.copy()
        _scroll(scanner, toward_bottom=True, reference=capture.identity_image)
        steps += 1
        frame, stable = _stable_frame(scanner)
        if not stable:
            _add_reason(reasons, '天赋面板未稳定')
        changed = not _same_panel(before, frame)
        if changed:
            single_screen = False
        same = same + 1 if stable and not changed else 0
        if changed or same >= 2:
            page_talents, known = _read_rows(frame, ocr, reasons)
            all_rows_known = all_rows_known and known
            for talent in page_talents:
                old = found.get(talent.line)
                if old is None or talent.level > old.level:
                    found[talent.line] = talent
            if single_screen and [(talent.name, talent.level) for talent in page_talents] != top_talents:
                _add_reason(reasons, '同一完整面板重复读取结果不一致')
        if same >= 2:
            bottom_confirmed = True
            break
    if not bottom_confirmed:
        _add_reason(reasons, '未确认天赋列表底部')
    if not single_screen:
        _add_reason(reasons, '天赋需滚动读取，尚不能独立证明全部行完整')
    shown, final_level = scanner._read_current_cat(ocr)
    if not detail_page_confirmed(scanner.device.image) or not shown \
            or normalize(shown) != normalize(display_name) or final_level != actual_level \
            or _mean_diff(capture.identity_image, _crop(scanner.device.image, IDENTITY_AREA)) >= 3:
        capture.identity_confirmed = False
        _add_reason(reasons, '读取期间当前猫身份发生变化或未能再次确认')
    capture.talents = list(found.values())
    capture.complete = (capture.identity_confirmed and capture.breed is not None
                        and capture.rarity in ('SSR', 'SR') and top_confirmed and bottom_confirmed
                        and single_screen and all_rows_known and bool(capture.talents) and not reasons)
    return capture
