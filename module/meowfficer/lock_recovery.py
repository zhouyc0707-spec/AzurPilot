"""改锁触发旧选中项刷新时，只沿本轮已核验序列在天赋页恢复目标。"""

from dataclasses import dataclass, replace

import numpy as np

from module.logger import logger
from module.meowfficer.scan_capture import capture_current_cat
from module.meowfficer.scan_next import swipe_next_cat
from module.meowfficer.scan_roster import STATIC_ATTRIBUTE_AREAS, read_static_attributes
from module.meowfficer.scan_utils import _crop, _mean_diff
from module.meowfficer.score_lock import IDENTITY_AREA, detail_page_confirmed


_UNREAD = object()


def _usable(capture):
    return bool(capture.identity_confirmed and capture.display_name and capture.level is not None
                and capture.rarity in ('R', 'SR', 'SSR') and capture.talents_complete)


def _talents(capture):
    return sorted((item.name, item.line, item.level, item.kind) for item in capture.talents)


def _same_details(first, second):
    """未知资料返回 None，不能在唯一性检查中当作已排除的另一只猫。"""
    if first.display_name != second.display_name:
        return False
    if first.level is not None and second.level is not None and first.level != second.level:
        return False
    if not _usable(first) or not _usable(second):
        return None
    return first.rarity == second.rarity and _talents(first) == _talents(second)


@dataclass
class _Record:
    """只持有小块资料截图，避免每只猫保留一张完整 720p 图像。"""

    original: object
    capture: object
    attribute_crops: tuple
    attributes: object = _UNREAD

    def read_attributes(self, ocr):
        if self.attributes is _UNREAD:
            if not self.attribute_crops:
                self.attributes = None
            else:
                # 属性识别器只读取这三个区域；临时底图不进入记录，也不伪造其他资料。
                image = np.zeros((720, 1280, 3), dtype=np.uint8)
                for area, crop in zip(STATIC_ATTRIBUTE_AREAS, self.attribute_crops):
                    x0, y0, x1, y1 = area
                    image[y0:y1, x0:x1] = crop
                self.attributes = read_static_attributes(image, ocr)
        return self.attributes


class TalentPageRecovery:
    """本轮连续扫描的恢复上下文；不返回猫窝、不改锁、不重复发布评分。"""

    def __init__(self, scanner, ocr):
        self.scanner = scanner
        self.ocr = ocr
        self.records = []
        self.attempted = set()

    def remember(self, capture, image):
        """正常读取零额外 OCR，仅保存恢复时需要的独立资料裁剪。"""
        valid = image is not None and image.shape == (720, 1280, 3) and image.dtype == np.uint8
        identity = _crop(image, IDENTITY_AREA).copy() if valid else None
        snapshot = replace(capture, talents=[replace(item) for item in capture.talents],
                           reasons=list(capture.reasons),
                           identity_image=identity)
        crops = tuple(_crop(image, area).copy() for area in STATIC_ATTRIBUTE_AREAS) if valid else ()
        self.records.append(_Record(capture, snapshot, crops))

    def _same_records(self, first, second):
        same = _same_details(first.capture, second.capture)
        if same is not True:
            return same
        attributes = first.read_attributes(self.ocr)
        other = second.read_attributes(self.ocr)
        return None if attributes is None or other is None else attributes == other

    def _unique(self, index):
        record = self.records[index]
        if (not _usable(record.capture) or record.capture.identity_image is None
                or record.read_attributes(self.ocr) is None):
            return False
        return all(self._same_records(record, other) is False
                   for other_index, other in enumerate(self.records) if other_index != index)

    def _read_current(self):
        scanner = self.scanner
        scanner.device.screenshot()
        if not detail_page_confirmed(scanner.device.image):
            return None
        name, level = scanner._read_current_cat(self.ocr)
        if not name or level is None:
            return None
        capture = capture_current_cat(scanner, self.ocr, name, level, reset_history=False)
        if not _usable(capture):
            return None
        attributes = read_static_attributes(scanner.device.image, self.ocr)
        return (capture, attributes) if attributes is not None else None

    def _matches(self, record, current):
        capture, attributes = current
        if _same_details(record.capture, capture) is not True:
            return False
        return (record.read_attributes(self.ocr) == attributes
                and _mean_diff(record.capture.identity_image,
                               _crop(self.scanner.device.image, IDENTITY_AREA)) < 3)

    def _accept_stage(self):
        # 完整接受当前身份之后才结束旧手势阶段，未知或未切换时保留保护记录。
        self.scanner.device.click_record_remove('MEOWFFICER_NEXT')
        self.scanner.device.click_record_remove('SWIPE')
        self.scanner.device.stuck_record_clear()

    def target_confirmed(self, capture):
        """最终锁核验也完整确认原目标，不能只凭同名同级资料区域认定身份。"""
        if not self.records or self.records[-1].original is not capture:
            return False
        current = self._read_current()
        if current is None or not self._matches(self.records[-1], current):
            return False
        self._accept_stage()
        return True

    def restore(self, capture, entry):
        """有限左滑恢复原猫，逐只完整比对已读记录；返回 True 仍须只读核验锁。

        Pages:
            in: 改锁后仍显示另一只猫的天赋页。
            out: 原目标天赋页，或无法安全定位时停在当前天赋页。

        恢复不沿用普通扫描的四次相同容忍策略，资料重复或未知时不能猜测位置。
        每个目标最多尝试一次，手势上限为唯一锚点到目标的已读距离。
        """
        indexes = [index for index, record in enumerate(self.records) if record.original is capture]
        if len(indexes) != 1 or indexes[0] != len(self.records) - 1:
            return False
        target = indexes[0]
        if target in self.attempted:
            return False
        self.attempted.add(target)
        recovery = {'status': 'pending', 'reason': '', 'toOrdinal': target + 1, 'swipes': 0}
        entry['recovery'] = recovery

        def fail(reason):
            recovery.update(status='failed', reason=reason)
            entry['reason'] += f'；天赋页恢复停止：{reason}'
            logger.warning(f'[指挥喵-锁定] 天赋页恢复停止：{reason}')
            return False

        if not self._unique(target):
            return fail('原目标资料不完整或与已读猫重复，不能安全定位')
        current = self._read_current()
        if current is None:
            return fail('当前猫的完整天赋、等级或静态属性无法确认')
        anchors = [index for index, record in enumerate(self.records) if self._matches(record, current)]
        if len(anchors) != 1:
            return fail('当前猫不能唯一对应本轮已读记录')
        start = anchors[0]
        if start >= target:
            return fail('当前猫不是原目标之前的唯一已读位置')
        recovery['fromOrdinal'] = start + 1
        # 先完整检查整条路径；存在相同猫或未确认的候选，不发第一个恢复手势。
        if not all(self._unique(index) for index in range(start, target + 1)):
            return fail('恢复路径含资料未知或重复的猫，不能确认每次切换')
        logger.info(f'[指挥喵-锁定] 已确认列表刷新回到第 {start + 1} 只，'
                    f'在天赋页沿已读序列恢复第 {target + 1} 只，不重复切锁')
        self._accept_stage()
        for index in range(start + 1, target + 1):
            previous = self.records[index - 1].capture
            recovery['swipes'] += 1
            swipe_next_cat(self.scanner, self.ocr, previous.display_name, previous.level,
                           defer_same_name=True, reset_history=False)
            current = self._read_current()
            if current is None or not self._matches(self.records[index], current):
                return fail(f'第 {index + 1} 只未能完整对应原读取序列，未继续滑动')
            self._accept_stage()
            logger.attr('[指挥喵-锁定] 天赋页恢复进度', f'{index + 1}/{target + 1}，资料已完整核验')
        recovery.update(status='restored', reason='原目标完整资料已恢复，等待只读锁状态核验')
        return True
