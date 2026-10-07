"""国服已校准五槽天赋页的端点证据，未知布局继续原有滚动核验。"""

from module.config import server


# 客户端 CommanderConst.MAX_TELENT_COUNT 及 CommanderCatTalentPage.uilist:align
# 均约束当前天赋页为五槽；只在已校准的国服 1280×720 布局启用。
CN_TALENT_SLOT_COUNT = 5


def cn_talent_top_confirmed(image):
    """以固定五槽的完整顶部布局确认已到顶，不仅检查首框恰好顶齐。

    四个完整框与第五槽正常裁边须按原行距连续存在。该页面的全部滚动范围
    小于一行，因此当前布局不会与中部整行位移混淆；未知布局不使用此证据。
    """
    if server.server != 'cn' or image is None or image.shape != (720, 1280, 3):
        return False
    from module.meowfficer.scan_capture import _visible_rows

    rows = _visible_rows(image)
    if len(rows) != CN_TALENT_SLOT_COUNT:
        return False
    for index, (top, bottom) in enumerate(rows[:-1]):
        if not 153 + index * 102 <= top <= 157 + index * 102 \
                or not 78 <= bottom - top <= 94:
            return False
    top, bottom = rows[-1]
    return 561 <= top <= 565 and bottom == 588
