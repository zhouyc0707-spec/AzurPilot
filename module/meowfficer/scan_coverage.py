"""通过实际图像位移和逐行覆盖证明天赋列表完整，不由名字数量猜测到底。"""

import cv2
import numpy as np

from module.meowfficer.scan_utils import _crop, _mean_diff, scroll_offset


SCROLL_AREA = (748, 152, 1120, 588)
ROW_STEP = 102


def _matching_titles(before, after, distance):
    """全部重叠完整行须匹配；至少两个非空标题才能独立证明位移。"""
    from module.meowfficer.scan_capture import _visible_rows

    def full_rows(image):
        return [(top, bottom) for top, bottom in _visible_rows(image)
                if top > 152 and bottom < 588 and 78 <= bottom - top <= 94]

    first, second = full_rows(before), full_rows(after)
    supported = 0
    for top, bottom in first:
        moved_top, moved_bottom = top - distance, bottom - distance
        if moved_top <= 152 or moved_bottom >= 588:
            continue
        if (moved_top, moved_bottom) not in second:
            return False
        a = cv2.GaussianBlur(_crop(before, (855, top + 3, 1120, top + 42)), (3, 3), 0)
        b = cv2.GaussianBlur(_crop(after, (855, moved_top + 3, 1120, moved_top + 42)), (3, 3), 0)
        difference = np.abs(a.astype(np.int16) - b.astype(np.int16))
        if _mean_diff(a, b) > 3 or (difference.max(axis=2) > 20).mean() > 0.02:
            return False
        ink_a, ink_b = np.count_nonzero(a.min(axis=2) < 180), np.count_nonzero(b.min(axis=2) < 180)
        if (ink_a >= 32) != (ink_b >= 32):
            return False
        if ink_a >= 32:
            # 均差容易被浅色背景稀释，字形还须独立满足完整标题模板的严格相似度。
            similarity = float(cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)[0, 0])
            if not np.isfinite(similarity) or similarity < 0.97:
                return False
            supported += 1
    return supported >= 2


def talent_panel_unchanged(before, after):
    """行框与全部完整标题保持原位时，正文跑马灯和图标闪光不算纵向滚动。"""
    from module.meowfficer.scan_capture import _visible_rows

    return (_visible_rows(before) == _visible_rows(after)
            and _matching_titles(before, after, 0))


def _title_shift(before, after):
    """由行框提出候选，再以静态标题证明唯一位移；不能只凭周期边框猜测。"""
    from module.meowfficer.scan_capture import _visible_rows

    first, second = _visible_rows(before), _visible_rows(after)
    candidates = {top - moved_top for top, bottom in first for moved_top, moved_bottom in second
                  if top > 152 and bottom < 588 and moved_top > 152 and moved_bottom < 588
                  and 0 < top - moved_top <= 240}
    accepted = [distance for distance in candidates if _matching_titles(before, after, distance)]
    return accepted[0] if len(accepted) == 1 else None


def measure_talent_shift(before, after):
    """返回可由重叠内容确认的向上位移；未知变化不能当作零位移。"""
    first, second = _crop(before, SCROLL_AREA), _crop(after, SCROLL_AREA)
    if _mean_diff(first, second) < 1:
        return 0
    shift = scroll_offset(first, second, max_shift=240)
    # 去掉视口边缘的抗锯齿，保留至少近两行重叠；不接受仅凭行距猜出的位移。
    def matches(distance):
        a, b = first[distance + 4:-4], second[4:-distance - 4]
        if a.shape[0] < 180:
            return False
        # 游戏滚动会产生亚像素抗锯齿，只平滑一圈边缘，不放宽正文变化阈值。
        a = cv2.GaussianBlur(a, (3, 3), 0)
        b = cv2.GaussianBlur(b, (3, 3), 0)
        difference = np.abs(a.astype(np.int16) - b.astype(np.int16))
        return _mean_diff(a, b) <= 3 and (difference.max(axis=2) > 20).mean() <= 0.02

    if shift <= 0 or not matches(shift):
        # 效果说明会横向滚动，图标也会闪光；只用静态完整标题补证，不放宽原阈值。
        if talent_panel_unchanged(before, after):
            return 0
        return _title_shift(before, after)
    # 连续空位外观相同，整行周期的其他位移也匹配时不能猜测移动了几行。
    if any(matches(other) for other in (shift - ROW_STEP, shift + ROW_STEP)
           if 0 < other <= 240):
        return None
    return shift


def uncovered_tail(image, rows):
    """第二路检查末行漏框：已检测行下面仍有图标或文字时拒绝完整结论。"""
    if not rows:
        return True
    bottom = max(row.bottom for row in rows)
    tail = _crop(image, (756, min(588, bottom + 4), 1120, 588))
    # 只检查列表内部；正常底部空白没有这些深色图标、标题或虚线笔画。
    return bool(tail.size and np.count_nonzero(tail.max(axis=2) < 180) > 12)


class TalentCoverage:
    """只合并有真实位置依据的完整行，边缘半行须在相邻画面得到补全。"""

    def __init__(self, origin, row_height):
        self.origin = origin
        self.row_height = row_height
        self.offset = 0
        self.last_index = -1
        self.covered = {}
        self.pending = {}
        self.reasons = []

    def _reason(self, text):
        if text not in self.reasons:
            self.reasons.append(text)

    def add(self, rows, shift=0, issues=()):
        """将可见行映射到顶部起算的位置，已知冲突不能被取最高级掩盖。"""
        self.offset += shift
        for issue in ('不同天赋行识别为重复天赋线', '同一天赋行多次识别不一致'):
            if issue in issues:
                self._reason(issue)
        for row in rows:
            top = row.top
            if top <= SCROLL_AREA[1]:
                top = row.bottom - self.row_height
            location = (top + self.offset - self.origin) / ROW_STEP
            index = round(location)
            if index < 0 or abs(location - index) * ROW_STEP > 4:
                self._reason('天赋行位置与实际滚动位移不一致，不能排除漏行')
                continue
            self.last_index = max(self.last_index, index)
            previous = self.covered.get(index)
            identity = (row.talent.name, row.talent.line, row.talent.level) if row.talent else None
            # 行框有其他不完整原因，也不能忽略这次已经精确读出的矛盾标题或空位。
            if previous is not None and (row.talent is not None or row.empty):
                old = None if previous.empty else (previous.talent.name, previous.talent.line,
                                                   previous.talent.level)
                if identity != old:
                    self._reason('同一天赋行跨截图识别结果不一致')
                    continue
            if identity is not None and any(
                    other_index != index and not other.empty and other.talent.line == row.talent.line
                    for other_index, other in self.covered.items()):
                self._reason('不同天赋行识别为重复天赋线')
            if not row.complete:
                self.pending[index] = list(issues)
                continue
            self.covered[index] = row
            self.pending.pop(index, None)

    def finish(self):
        """顶部和底部由调用方另行证明，本处要求中间所有行均有完整证据。"""
        if self.last_index < 0:
            self._reason('没有确认到天赋图标行')
        for index in range(self.last_index + 1):
            if index not in self.covered:
                self._reason(f'第 {index + 1} 行天赋未能完整确认，不能排除漏读')
                for issue in self.pending.get(index, ()):
                    self._reason(issue)
        return not self.reasons and self.last_index >= 0

    def talents(self):
        """按实际行序返回已确认天赋，未习得占位不参与评分。"""
        return [row.talent for _, row in sorted(self.covered.items()) if not row.empty]
