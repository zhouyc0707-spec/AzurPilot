"""统计页标题旁的图标按钮。

刷新这类操作收进标题右边的圆环箭头图标里，比在内容下方单占一行更紧凑。
图标以 data URI 作为 ``put_button`` 的背景图提供——``put_button`` 只能渲染
纯文本，空 label + CSS 背景既保留了 Python 回调，又是纯图标外观。
深浅两套描边色由 ``body.webio-theme-dark`` 选择，切换主题无需重新渲染。
"""

from __future__ import annotations

import math

# 图标描边色：浅色页面用深靛灰，深色页面用浅灰蓝
ICON_COLOR_LIGHT = "#43415f"
ICON_COLOR_DARK = "#c3c7d4"

# 图标 path 的几何参数：圆心、半径与开口位置
_ICON_CENTER = 24.0
_ICON_RADIUS = 17.0
_ICON_START_DEG = 100.0
_ICON_END_DEG = 40.0
_ICON_HEAD_DEG = 26.0
_ICON_HEAD_LEN = 7.5


def _arc_bezier(from_deg: float, to_deg: float) -> str:
    """返回一段圆弧的三次贝塞尔近似。"""
    k = 4 / 3 * math.tan((math.radians(to_deg) - math.radians(from_deg)) / 4)

    def point(deg: float) -> tuple[float, float]:
        rad = math.radians(deg)
        return (
            _ICON_CENTER + _ICON_RADIUS * math.cos(rad),
            _ICON_CENTER + _ICON_RADIUS * math.sin(rad),
        )

    a0, a1 = math.radians(from_deg), math.radians(to_deg)
    x0, y0 = point(from_deg)
    x1, y1 = point(to_deg)
    c1x = x0 - k * _ICON_RADIUS * math.sin(a0)
    c1y = y0 + k * _ICON_RADIUS * math.cos(a0)
    c2x = x1 + k * _ICON_RADIUS * math.sin(a1)
    c2y = y1 - k * _ICON_RADIUS * math.cos(a1)
    return f"C{c1x:.2f},{c1y:.2f} {c2x:.2f},{c2y:.2f} {x1:.2f},{y1:.2f}"


def _refresh_icon_paths() -> tuple[str, str]:
    """算出圆环与末端箭头的 path，返回 ``(圆环, 箭头)``。

    圆环用三段三次贝塞尔逼近（每段 100°），不用折线：折线的隐式坐标会超出
    SVG 的 8 位坐标上限，被浏览器按错误方式截断（实测只画出四分之一圆）。
    """
    # 顺时针意味着角度递增扫过去，跨度是 360 - (start - end)；直接取模会得到短弧
    span = (360 - (_ICON_START_DEG - _ICON_END_DEG)) % 360
    stops = [_ICON_START_DEG + span * i / 3 for i in range(4)]

    x0 = _ICON_CENTER + _ICON_RADIUS * math.cos(math.radians(stops[0]))
    y0 = _ICON_CENTER + _ICON_RADIUS * math.sin(math.radians(stops[0]))
    ring_d = f"M{x0:.2f},{y0:.2f}" + "".join(
        _arc_bezier(stops[i], stops[i + 1]) for i in range(3)
    )

    # 末端切线方向（顺时针运动方向），两翼沿反向切线张开 ±head_deg
    end_rad = math.radians(_ICON_END_DEG)
    tx, ty = -math.sin(end_rad), math.cos(end_rad)
    ex = _ICON_CENTER + _ICON_RADIUS * math.cos(end_rad)
    ey = _ICON_CENTER + _ICON_RADIUS * math.sin(end_rad)
    wings = []
    for sign in (1, -1):
        deg = math.radians(sign * _ICON_HEAD_DEG)
        cos_d, sin_d = math.cos(deg), math.sin(deg)
        dx = -tx * cos_d + ty * sin_d
        dy = -tx * sin_d - ty * cos_d
        wings.append(f"{ex + _ICON_HEAD_LEN * dx:.2f} {ey + _ICON_HEAD_LEN * dy:.2f}")
    return ring_d, f"M{wings[0]} L{ex:.2f} {ey:.2f} L{wings[1]}"


_RING_D, _HEAD_D = _refresh_icon_paths()

REFRESH_ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" '
    'fill="none" stroke="{color}" stroke-width="4" '
    'stroke-linecap="round" stroke-linejoin="round">'
    f'<path d="{_RING_D}"/>'
    f'<path d="{_HEAD_D}"/>'
    "</svg>"
)


def refresh_icon_data_uri(color: str) -> str:
    """把刷新图标转成可直接放进 CSS ``background-image`` 的 data URI。

    Args:
        color: 描边颜色，``#`` 开头即可，内部会转义成 URI 安全形式。

    Returns:
        str: 形如 ``url("data:image/svg+xml,...")`` 的 CSS 值。
    """
    svg = REFRESH_ICON_SVG.format(color=color)
    encoded = (
        svg.replace("<", "%3C")
        .replace(">", "%3E")
        .replace("#", "%23")
        .replace('"', "'")
        .replace(" ", "%20")
    )
    return f"url(\"data:image/svg+xml,{encoded}\")"


def build_title_icon_row(title: str, scope_id: str, margin_top: int = 24, margin_bottom: int = 8) -> str:
    """构造「标题 + 图标按钮」的一行。

    图标按钮由 PyWebIO 渲染进预留的 ``scope_id``（HTML 里的按钮无法回调
    Python），样式见 entry-alas.css 的 ``.stat-title-row`` 与
    ``#pywebio-scope-<scope_id>``。默认上边距与其它统计板块标题一致（24px）。

    Args:
        title: 标题文本（调用方需自行转义）。
        scope_id: 图标按钮要渲染进的 scope 名。
        margin_top: 该行的上外边距。
        margin_bottom: 该行的下外边距。

    Returns:
        str: 标题行 HTML。
    """
    return (
        f'<div class="stat-title-row" style="margin-top:{margin_top}px;'
        f' margin-bottom:{margin_bottom}px">'
        f'<div class="stat-title-text">{title}</div>'
        f'<div id="pywebio-scope-{scope_id}"></div>'
        "</div>"
    )


def refresh_icon_button_css(scope_id: str) -> str:
    """返回把空 label 按钮变成纯图标按钮的 ``<style>`` 片段。

    Args:
        scope_id: 图标按钮所在 scope 名，用于限定选择器。

    Returns:
        str: 一段 ``<style>`` HTML。
    """
    return (
        f"<style>#pywebio-scope-{scope_id} .btn {{"
        f" background-image: {refresh_icon_data_uri(ICON_COLOR_LIGHT)} !important;"
        " }"
        f"body.webio-theme-dark #pywebio-scope-{scope_id} .btn,"
        f"html[data-theme='dark'] #pywebio-scope-{scope_id} .btn {{"
        f" background-image: {refresh_icon_data_uri(ICON_COLOR_DARK)} !important;"
        " }</style>"
    )
