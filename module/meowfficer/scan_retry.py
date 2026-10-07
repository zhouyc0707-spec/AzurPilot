"""仅在同一只猫的同一列表位置补读完整行，不以重试绕过完整性保护。"""

from module.meowfficer.scan_utils import _crop, _mean_diff


MAX_ROW_RETRIES = 2
_PERMANENT_ISSUES = frozenset((
    '不同天赋行识别为重复天赋线',
    '同一天赋行多次识别不一致',
    '同一天赋行跨截图识别结果不一致',
))
_GEOMETRY_ISSUES = frozenset((
    '天赋行框间距异常，不能排除漏行',
    '没有确认到天赋图标行',
))


def _needs_retry(rows, issues):
    """只有完整几何框的读取失败可补读；正常裁边留待相邻滚动画面补全。"""
    if any(issue in _PERMANENT_ISSUES or issue in _GEOMETRY_ISSUES for issue in issues):
        return False
    return any(row.top > 152 and row.bottom < 588
               and 78 <= row.bottom - row.top <= 94 and not row.complete for row in rows)


def retry_talent_rows(scanner, ocr, frame, rows, issues, identity_reference):
    """最多取两次新稳定画面补读，返回可按原物理位置交给覆盖器的新增证据。

    Pages:
        in: 已确认当前猫的天赋页，已有本位置的逐行识别结果。
        out: 同一只猫的同一列表位置；不滑动、点击或清除控制历史。

    完整帧及只有正常裁边的帧直接返回，不增加截图或 OCR。每次补读前必须确认
    天赋页、原身份及原列表位置；未知变化不能合并到旧位置。已知冲突永久保留，
    新证据必须逐批交给 TalentCoverage.add，不能用最后一批覆盖全部旧结果。

    Returns:
        tuple: 最后接受的帧、新增的 (frame, rows, issues) 批次、可选的保护原因。
    """
    if not _needs_retry(rows, issues):
        return frame, [], None

    # 延迟导入，scan_capture 的调用入口无需与本模块形成初始化循环。
    from module.meowfficer.scan_capture import (IDENTITY_AREA, _read_row_records, _same_panel,
                                               _stable_frame)
    from module.meowfficer.scan_coverage import talent_panel_unchanged
    from module.meowfficer.score_lock import detail_page_confirmed

    if identity_reference is None or not identity_reference.size:
        return frame, [], '天赋补读缺少当前猫身份依据，停止补读'

    baseline = frame
    accepted = frame
    batches = []
    for _ in range(MAX_ROW_RETRIES):
        current = scanner.device.image
        if not detail_page_confirmed(current) or _mean_diff(
                identity_reference, _crop(current, IDENTITY_AREA)) >= 3:
            return accepted, batches, '天赋补读期间当前猫身份或页面发生变化，停止补读'

        candidate, stable = _stable_frame(scanner)
        if not stable:
            return accepted, batches, '天赋补读期间面板未稳定，停止补读'
        if not detail_page_confirmed(candidate) or _mean_diff(
                identity_reference, _crop(candidate, IDENTITY_AREA)) >= 3:
            return accepted, batches, '天赋补读期间当前猫身份或页面发生变化，停止补读'
        # 整面板均差可能稀释单行标题变化；补读另须逐完整标题证明原位置不变。
        if not _same_panel(baseline, candidate) or not talent_panel_unchanged(baseline, candidate):
            return accepted, batches, '天赋补读期间列表位置或静态标题发生变化，停止补读'

        reread_issues = []
        reread_rows = _read_row_records(candidate, ocr, reread_issues)
        batches.append((candidate.copy(), reread_rows, reread_issues))
        accepted = candidate
        if not _needs_retry(reread_rows, reread_issues):
            break
    return accepted, batches, None
