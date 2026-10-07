"""通过实际图像位移和逐行覆盖证明天赋列表完整，不由名字数量猜测到底。"""

import cv2
import numpy as np

from module.meowfficer.scan_utils import _crop, _mean_diff, scroll_offset


SCROLL_AREA = (748, 152, 1120, 588)
ROW_STEP = 102


def measure_talent_shift(before, after):
    """返回可由重叠内容确认的向上位移；未知变化不能当作零位移。"""
    first, second = _crop(before, SCROLL_AREA), _crop(after, SCROLL_AREA)
    if _mean_diff(first, second) < 1:
        return 0
    shift = scroll_offset(first, second, max_shift=240)
    if shift <= 0:
        return None
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

    if not matches(shift):
        return None
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
