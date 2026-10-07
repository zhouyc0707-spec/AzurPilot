"""已有指挥喵自动锁定所需的保守识别。

锁定操作与扫描遍历由调用方负责。本模块只收集当前天赋页的身份、品质与天赋，
保留不完整原因；通过顶部、底部和连续行覆盖后才给出可用于自动解锁的完整结果。
"""

from dataclasses import dataclass, field

import numpy as np

from module.exception import (EmulatorNotRunningError, GameBugError, GameNotRunningError,
                              GamePageUnknownError, GameStuckError, GameTooManyClickError,
                              RequestHumanTakeover, ScriptEnd, ScriptError)
from module.meowfficer.cat_data import CATS
from module.meowfficer.scan_talent_template import read_exact_talent_title
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
    talents_complete: bool = False


@dataclass
class TalentRow:
    """可见图标框及其正向读取证据；边缘半行等待相邻截图补全。"""

    top: int
    bottom: int
    talent: Talent | None = None
    empty: bool = False
    complete: bool = False


def _add_reason(reasons, text):
    """同一失败原因只记录一次。"""
    if text not in reasons:
        reasons.append(text)


def _rgb_variants(image):
    """设备截图是 RGB；现有预处理与 AlOcr 的数组输入使用 BGR。"""
    return build_variants(image[..., ::-1].copy())


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


def _rarity_glyph_variants(crop):
    """按彩色品质字形去掉描边与背景，提供两种独立颜色分割证据。"""
    high, low = crop.max(axis=2), crop.min(axis=2)
    difference = high.astype(np.int16) - low.astype(np.int16)
    # SSR 金字、SR 紫字与 R 蓝字都比浅色底纹饱和；不以颜色本身判定品质。
    for threshold in (100, 110):
        glyph = np.where(difference >= threshold, 0, 255).astype(np.uint8)
        monochrome = np.repeat(glyph[:, :, None], 3, axis=2)
        yield build_variants(monochrome)['plain']


def _rarity_candidate(details):
    """返回精确品质、尚未确认或无效证据；高置信度矛盾不能被补读掩盖。"""
    if details is None or len(details) > 1:
        return None, False
    if not details:
        return None, True
    raw, confidence = details[0]
    if not np.isfinite(confidence):
        return None, False
    text = normalize(raw).upper()
    if confidence < 0.9:
        return None, True
    return (text, True) if text in ('SSR', 'SR', 'R') else (None, False)


def _read_rarity(image, ocr, reasons):
    """精确核验品质；艺术字增强变体偏低时用独立颜色字形补读，门槛不变。"""
    found = []
    crop = _crop(image, RARITY_AREA)
    for variant in _rgb_variants(crop).values():
        details = _details(ocr, variant, reasons, '品质')
        text, usable = _rarity_candidate(details)
        if not usable:
            _add_reason(reasons, '品质标记未能明确识别')
            return None
        if text is not None:
            found.append(text)
    if len(set(found)) > 1:
        _add_reason(reasons, '品质标记多次识别不一致')
        return None
    if len(found) == 2:
        return found[0]
    if not found:
        _add_reason(reasons, '品质标记未能明确识别')
        return None
    # 至少有一个原始变体精确确认后才补读；两种字形分割也必须各自高置信度。
    for variant in _rarity_glyph_variants(crop):
        details = _details(ocr, variant, reasons, '品质字形')
        text, usable = _rarity_candidate(details)
        if not usable or text is None:
            _add_reason(reasons, '品质标记未能明确识别')
            return None
        if text != found[0]:
            _add_reason(reasons, '品质标记多次识别不一致')
            return None
    return found[0]


def _visible_rows(image):
    """独立检测浅青色图标底框，包含上下边缘的半截框。

    两条窄带位于图标两侧，不依赖 OCR 是否读到名字。颜色条件不依赖 RGB/BGR
    的通道顺序；不满足实拍几何时由调用方保护处理，不能把检测失败当成零天赋。
    """
    if image is None or image.shape != (720, 1280, 3):
        return []
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


def _blank_talent_body(image, top):
    """正向确认未习得的浅青空图标和空白正文，排除漏读已学标题或效果。"""
    # 避开左侧圆角和上下虚线；实拍空位此区域超过 99% 为浅色背景。
    body = _crop(image, (866, top + 20, 1116, top + 68))
    dark = body.min(axis=2)
    icon = _crop(image, (765, top + 24, 833, top + 68))
    low, high = icon.min(axis=2), icon.max(axis=2)
    difference = high.astype(np.int16) - low.astype(np.int16)
    cyan = (low >= 185) & (high >= 218) & (difference >= 22) & (difference <= 60)
    return bool(body.size and (dark >= 220).mean() >= 0.99 and not (dark < 200).any()
                and cyan.mean() >= 0.8)


def _read_empty_row(image, top, ocr, reasons):
    """空白面板须同时具备两个高置信度的精确「未习得」图标标签。"""
    crop = _crop(image, (765, top + 24, 833, top + 68))
    for variant in _rgb_variants(crop).values():
        details = _details(ocr, variant, reasons, '未习得标记')
        if details is None or len(details) != 1:
            return False
        raw, confidence = details[0]
        if normalize(raw) != normalize('未习得') or not np.isfinite(confidence) or confidence < 0.9:
            return False
    return True


def _read_row_records(image, ocr, reasons):
    """逐框读取标题或明确空位；半截行保留位置，等待相邻截图补全。

    普通天赋的精确名称已包含对应等级，不能用模糊标题或罗马图标猜出另一等级。
    未习得必须通过独立图标文字和空白正文共同确认，不把没读到标题当作空位。
    """
    rows = _visible_rows(image)
    if not rows:
        _add_reason(reasons, '没有确认到天赋图标行')
        return []
    # 顶部半截行只剩底边可测，使用相邻底边距离；底部半截则使用上边距离。
    gaps = [(second[1] - first[1] if first[0] <= 152 else second[0] - first[0])
            for first, second in zip(rows, rows[1:])]
    geometry_valid = all(98 <= gap <= 106 for gap in gaps)
    if not geometry_valid:
        _add_reason(reasons, '天赋行框间距异常，不能排除漏行')
    records = []
    for top, bottom in rows:
        record = TalentRow(top, bottom)
        records.append(record)
        height = bottom - top
        if top <= 152 or bottom >= 588 or not 78 <= height <= 94:
            _add_reason(reasons, '天赋行被裁切或行框不完整')
            continue
        if _blank_talent_body(image, top):
            if _read_empty_row(image, top, ocr, reasons):
                record.empty = True
                record.complete = geometry_valid
            else:
                _add_reason(reasons, '空白天赋框未能高置信度确认未习得标记')
            continue
        crop = _crop(image, (855, top + 3, 1120, top + 42))
        template_name = read_exact_talent_title(crop)
        template_ref = TALENT_INDEX.get(normalize(template_name)) if template_name else None
        matches = []
        for variant in _rgb_variants(crop).values():
            details = _details(ocr, variant, reasons, '天赋标题')
            if details == [] and template_ref is not None:
                matches.append((template_ref, f'{template_name}（图像模板）'))
                continue
            if details is None or len(details) != 1:
                _add_reason(reasons, '天赋图标行与识别标题数量不一致')
                break
            raw, confidence = details[0]
            ref = TALENT_INDEX.get(normalize(raw))
            if not np.isfinite(confidence):
                _add_reason(reasons, '天赋标题不是高置信度的精确已知名称')
                break
            if ref is None or confidence < 0.9:
                if template_ref is not None:
                    # 完整字形独立确认标题，不能仅由 OCR 的「风之眼」等错字推断。
                    matches.append((template_ref, f'{template_name}（图像模板）'))
                    continue
                _add_reason(reasons, '天赋标题不是高置信度的精确已知名称')
                break
            if template_ref is not None and template_ref.name != ref.name:
                _add_reason(reasons, '同一天赋行多次识别不一致')
                break
            matches.append((ref, raw))
        if len(matches) != 2:
            continue
        if matches[0][0].name != matches[1][0].name:
            _add_reason(reasons, '同一天赋行多次识别不一致')
            continue
        ref, raw = matches[0]
        record.talent = Talent(name=ref.name, line=ref.line, level=ref.level, kind=ref.kind, raw=raw)
        record.complete = geometry_valid
    talents = [row.talent for row in records if row.talent is not None]
    if len({talent.line for talent in talents}) != len(talents):
        _add_reason(reasons, '不同天赋行识别为重复天赋线')
        for row in records:
            row.complete = False
    return records


def _read_rows(image, ocr, reasons):
    """兼容单帧读取：完整已学行和正向确认的空位共同构成全部可见行。"""
    rows = _read_row_records(image, ocr, reasons)
    talents = [row.talent for row in rows if row.talent is not None]
    return talents, bool(rows) and all(row.complete for row in rows)


def _same_panel(before, after):
    """行框实际位移也算变化，避免整面板平均差稀释了小范围滚动。"""
    if _visible_rows(before) != _visible_rows(after):
        return False
    if _mean_diff(_crop(before, PANEL_AREA), _crop(after, PANEL_AREA)) < 3:
        return True
    from module.meowfficer.scan_coverage import talent_panel_unchanged

    return talent_panel_unchanged(before, after)


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


def capture_current_cat(scanner, ocr, display_name, level, *, reset_history=True):
    """读取当前选中猫，给出自动修改锁状态前需要的保守判断。

    Pages:
        in: 当前猫的天赋页，已有可用的 1280×720 截图。
        out: 同一只猫的天赋页；本函数不点击锁按钮、不修改天赋。

    每次滚动必须确认实际重叠位移，边缘半行须由相邻截图补全。只有顶部、底部、
    全部行与当前猫身份都有正向证据时才完整；不能用「没有新名字」证明到底。
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
    # 独立读取在此结束旧阶段；连续同名遍历交由调用方完整比较后清理。
    # 同一阶段内仍保留重复控制保护与有限读取上限。
    if reset_history:
        scanner.device.click_record_remove('SWIPE')
    capture.rarity = _read_rarity(image, ocr, reasons)
    expected_rarity = CATS.get(capture.breed or '', {}).get('rarity')
    if expected_rarity and capture.rarity is not None and expected_rarity != capture.rarity:
        _add_reason(reasons, '品质标记与已知猫种不一致')
        capture.rarity = None
    if capture.rarity == 'R':
        # 蓝猫按既定策略跳过天赋评分，仍可参与连续遍历的身份和属性核对。
        capture.talents_complete = True
        capture.complete = True
        return capture
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
    from module.meowfficer.scan_coverage import TalentCoverage, measure_talent_shift, uncovered_tail

    issues = []
    rows = _read_row_records(frame, ocr, issues)
    # 起点由顶部正向核验约束；未知标题也保留物理行位置，不能靠 OCR 数量减行。
    full_rows = [row for row in rows if row.top > 152 and row.bottom < 588
                 and 78 <= row.bottom - row.top <= 94]
    origin = full_rows[0].top if full_rows else 153
    row_height = full_rows[0].bottom - full_rows[0].top if full_rows else 87
    coverage = TalentCoverage(origin, row_height)
    coverage.add(rows, issues=issues)
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
        same = same + 1 if stable and not changed else 0
        shift = measure_talent_shift(before, frame) if changed else 0
        if changed and (shift is None or shift <= 0):
            _add_reason(reasons, '天赋滚动前后重叠位移未能确认，不能排除漏行')
            break
        if changed or same >= 2:
            issues = []
            rows = _read_row_records(frame, ocr, issues)
            coverage.add(rows, shift=shift, issues=issues)
        if same >= 2:
            bottom_confirmed = True
            break
    if not bottom_confirmed:
        _add_reason(reasons, '未确认天赋列表底部')
    elif uncovered_tail(frame, rows):
        _add_reason(reasons, '列表底部仍有未对应行框的内容，不能排除漏行')
    covered = coverage.finish()
    for issue in coverage.reasons:
        _add_reason(reasons, issue)
    shown, final_level = scanner._read_current_cat(ocr)
    if not detail_page_confirmed(scanner.device.image) or not shown \
            or normalize(shown) != normalize(display_name) or final_level != actual_level \
            or _mean_diff(capture.identity_image, _crop(scanner.device.image, IDENTITY_AREA)) >= 3:
        capture.identity_confirmed = False
        _add_reason(reasons, '读取期间当前猫身份发生变化或未能再次确认')
    capture.talents = coverage.talents()
    capture.talents_complete = (capture.identity_confirmed and capture.rarity in ('SSR', 'SR')
                               and top_confirmed and bottom_confirmed and covered
                               and bool(capture.talents) and not reasons)
    if capture.breed is None:
        _add_reason(reasons, '自定义猫名未能确定原始猫种')
    capture.complete = capture.talents_complete and capture.breed is not None
    return capture
