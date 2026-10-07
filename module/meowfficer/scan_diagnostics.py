"""为保护锁定保留裁剪后的天赋读取证据，不参与识别或游戏操作。"""

from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
import json
from pathlib import Path
from uuid import uuid4

import numpy as np
from PIL import Image, ImageDraw

from deploy.atomic import atomic_write
from module.exception import (EmulatorNotRunningError, GameBugError, GameNotRunningError,
                              GamePageUnknownError, GameStuckError, GameTooManyClickError,
                              RequestHumanTakeover, ScriptEnd, ScriptError)
from module.logger import logger


# 只保留天赋列表，不写入整屏、金币、账号信息或当前猫的立绘。
DIAGNOSTIC_AREA = (744, 152, 1244, 588)
_FLOW_ERRORS = (EmulatorNotRunningError, GameBugError, GameNotRunningError,
                GamePageUnknownError, GameStuckError, GameTooManyClickError,
                RequestHumanTakeover, ScriptEnd, ScriptError)


@dataclass
class TalentReadFrame:
    """调用方已读完的一帧；图像应为当时截图的副本，不再访问设备。"""

    stage: str
    image: np.ndarray
    rows: list
    issues: list[str] = field(default_factory=list)
    offset: int | None = 0


def _panel_image(frame):
    """仅裁剪符合本项目尺寸约定的 RGB 游戏画面。"""
    image = frame.image
    if not isinstance(image, np.ndarray) or image.shape != (720, 1280, 3) \
            or image.dtype != np.uint8:
        raise ValueError('诊断截图不是 1280×720 RGB uint8 画面')
    left, top, right, bottom = DIAGNOSTIC_AREA
    return Image.fromarray(image[top:bottom, left:right].copy())


def _png_bytes(image):
    """从裁剪像素生成 PNG，不转存原截图附带的元数据。"""
    output = BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()


def _row_payload(row):
    """保存成功标题、原始 OCR 及物理证据，不把未知或低分读数补成已知天赋。"""
    talent = row.talent
    return {
        'top': int(row.top),
        'bottom': int(row.bottom),
        'height': int(row.bottom - row.top),
        'complete': bool(row.complete),
        'empty': bool(row.empty),
        'talent': ({'name': talent.name, 'level': talent.level, 'raw': talent.raw}
                   if talent is not None else None),
        'ocrReadings': list(getattr(row, 'readings', [])),
    }


def _marked_panel(image, rows):
    """在裁剪副本上标出每行边框，绿色完整、橙色未完整确认。"""
    marked = image.copy()
    draw = ImageDraw.Draw(marked)
    panel_top = DIAGNOSTIC_AREA[1]
    for row in rows:
        top = max(0, int(row.top) - panel_top)
        bottom = min(image.height - 1, int(row.bottom) - panel_top - 1)
        if top > bottom or bottom < 0 or top >= image.height:
            continue
        color = (0, 160, 80) if row.complete else (240, 140, 0)
        draw.rectangle((0, top, image.width - 1, bottom), outline=color, width=2)
    return marked


def save_incomplete_capture(capture, frames, *, directory='./log/meowfficer_diagnostics'):
    """只在完整性不足时保存现有证据，失败不改变原有评分保护。

    Args:
        capture: 已结束读取的 ScanCapture，不修改其中的任何字段。
        frames: TalentReadFrame 序列，包含顶部、滚动帧和有限补读帧。
        directory: 诊断根目录；每次读取使用独立子目录，避免覆盖以前的现场。

    Returns:
        Path | None: 完整写入后的证据目录；无需保存或普通保存失败时为 None。

    本函数不截图、不点击、不滑动，也不从异常恢复分支替换游戏控制异常。
    """
    if capture.complete or not frames:
        return None
    try:
        frames = list(frames)
        if not frames:
            return None
        first_index = next((index for index, frame in enumerate(frames) if frame.stage == 'top'), 0)
        first = frames[first_index]
        last = frames[-1]
        stamp = datetime.now()
        payload = {
            'version': 1,
            'createdAt': stamp.isoformat(timespec='seconds'),
            'panelArea': list(DIAGNOSTIC_AREA),
            'capture': {
                'displayName': capture.display_name,
                'level': capture.level,
                'breed': capture.breed,
                'rarity': capture.rarity,
                'complete': bool(capture.complete),
                'identityConfirmed': bool(capture.identity_confirmed),
                'talentsComplete': bool(capture.talents_complete),
                'reasons': list(capture.reasons),
            },
            'frames': [
                {'stage': frame.stage, 'offset': int(frame.offset) if frame.offset is not None else None,
                 'issues': list(frame.issues), 'rows': [_row_payload(row) for row in frame.rows]}
                for frame in frames
            ],
            'images': {'top': 'top.png', 'bottom': 'bottom.png',
                       'topRows': 'top_rows.png', 'bottomRows': 'bottom_rows.png'},
            'imageFrames': {'top': first_index, 'bottom': len(frames) - 1},
        }
        # 先验证并编码全部数据，再开始写文件，避免坏帧留下可误读的半份现场。
        images = {}
        for name, frame in (('top', first), ('bottom', last)):
            panel = _panel_image(frame)
            images[f'{name}.png'] = _png_bytes(panel)
            images[f'{name}_rows.png'] = _png_bytes(_marked_panel(panel, frame.rows))
        evidence = json.dumps(payload, ensure_ascii=False, indent=2)
        target = Path(directory) / f'{stamp:%Y%m%d_%H%M%S_%f}_{uuid4().hex[:8]}'
        target.mkdir(parents=True, exist_ok=False)
        for name, image_data in images.items():
            atomic_write(str(target / name), image_data)
        # JSON 最后发布；存在完整 evidence.json 才表示本组图片已经全部保存。
        atomic_write(str(target / 'evidence.json'), evidence)
        logger.info(f'[指挥喵-评分] 保护锁定诊断已保存：{target}')
        return target
    except _FLOW_ERRORS:
        raise
    except Exception as exc:
        logger.warning(f'[指挥喵-评分] 保护锁定诊断保存失败：{exc}')
        return None
