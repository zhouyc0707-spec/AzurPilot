"""体力趋势图的主题调色板。

画布（canvas）无法读取 CSS 变量，图表颜色只能在渲染时由 Python 侧确定。
本模块把统计图表用到的全部颜色按主题族集中定义：深色主题沿用原有配色，
浅色主题（Light / 高级材质）改用与页面浅色卡片协调的一套颜色，避免出现
深色图表贴在浅色页面上的割裂感。
"""

from __future__ import annotations

from typing import Any

# 应用在 canvas 上的调色板键，保持与 ap_chart.js 中 C.xxx 的用法一致。
PALETTE_KEYS = (
    # 画布与面板
    "bg",
    "grid",
    "text",
    "panel_border",
    "panel_shadow",
    # 序列
    "ap_line",
    "ap_soft",
    "ap_point",
    "purple",
    "yellow",
    "asset",
    "distance",
    # 交互
    "crosshair",
    "bead_ring",
    "select_fill",
    "select_stroke",
    "tip_bg",
    "tip_border",
    "tip_text",
    "tip_muted",
    "tip_subtle",
    "tip_shadow",
    "ma5",
    "ma10",
    "avg_line",
    "zoom_bg",
    "zoom_border",
    "zoom_text",
    "legend_text",
    "series_text",
    "series_shadow",
    # 涨跌
    "inc",
    "dec",
    "flat",
)

# 深色主题族：与改造前完全一致（仅统一资产/海里数的图例与线条颜色），保持原有观感。
_DARK_PALETTE: dict[str, str] = {
    "bg": "#1a1a2e",
    "grid": "#2a2a3e",
    "text": "#666666",
    "panel_border": "#3a3a55",
    "panel_shadow": "none",
    "ap_line": "#64b5f6",
    "ap_soft": "rgba(100, 181, 246, 0.09)",
    "ap_point": "#64b5f6",
    "purple": "#ce93d8",
    "yellow": "#ffd54f",
    "asset": "#81c784",
    "distance": "#64b5f6",
    "crosshair": "rgba(255, 255, 255, 0.18)",
    "bead_ring": "#ffffff",
    "select_fill": "rgba(100, 181, 246, 0.08)",
    "select_stroke": "rgba(100, 181, 246, 0.5)",
    "tip_bg": "rgba(57, 57, 78, 0.95)",
    "tip_border": "#555555",
    "tip_text": "#dddddd",
    "tip_muted": "#888888",
    "tip_subtle": "#666666",
    "tip_shadow": "0 8px 20px rgba(0, 0, 0, 0.3)",
    "ma5": "#ffeb3b",
    "ma10": "#e91e63",
    "avg_line": "#ff9800",
    "zoom_bg": "#333333",
    "zoom_border": "#555555",
    "zoom_text": "#aaaaaa",
    "legend_text": "#888888",
    "series_text": "#aaaaaa",
    "series_shadow": "none",
    "inc": "#ef5350",
    "dec": "#26a69a",
    "flat": "#888888",
}

# 浅色主题族：文字与曲线使用更深一档的颜色，保证浅底上的对比度。
_LIGHT_PALETTE: dict[str, str] = {
    "bg": "#ffffff",
    "grid": "#eceef3",
    "text": "#8b8f98",
    "panel_border": "#dde0e5",
    "panel_shadow": "0 1px 2px rgba(0, 0, 0, 0.06)",
    "ap_line": "#3f51b5",
    "ap_soft": "rgba(63, 81, 181, 0.10)",
    "ap_point": "#5c6bc0",
    "purple": "#8e24aa",
    "yellow": "#ef6c00",
    "asset": "#0097a7",
    "distance": "#1976d2",
    "crosshair": "rgba(63, 81, 181, 0.22)",
    "bead_ring": "#ffffff",
    "select_fill": "rgba(63, 81, 181, 0.08)",
    "select_stroke": "rgba(63, 81, 181, 0.45)",
    "tip_bg": "rgba(255, 255, 255, 0.97)",
    "tip_border": "#d8dbe2",
    "tip_text": "#3c4043",
    "tip_muted": "#7a7f88",
    "tip_subtle": "#9aa0a6",
    "tip_shadow": "0 6px 18px rgba(31, 35, 48, 0.12)",
    "ma5": "#f9a825",
    "ma10": "#d81b60",
    "avg_line": "#ef6c00",
    "zoom_bg": "#ffffff",
    "zoom_border": "#d8dbe2",
    "zoom_text": "#5f6368",
    "legend_text": "#6f737a",
    "series_text": "#6f737a",
    "series_shadow": "0 1px 2px rgba(0, 0, 0, 0.04)",
    "inc": "#d32f2f",
    "dec": "#00897b",
    "flat": "#9aa0a6",
}

# 深色主题族：图表保持深色底与红绿分段线，无需跟随页面换色。
DARK_THEMES = frozenset({"dark", "dark_advanced_material"})

# 图例与统计概览使用的序列颜色，键为序列索引：0 体力、1 紫币、2 黄币、3 资产、4 海里数。
_SERIES_COLOR_KEYS = ("ap_point", "purple", "yellow", "asset", "distance")


def chart_theme_group(theme: str | None) -> str:
    """返回主题所属的明暗分组。

    Args:
        theme: WebUI 主题名，未知或空值按浅色处理。

    Returns:
        str: ``"dark"`` 或 ``"light"``。
    """
    return "dark" if theme in DARK_THEMES else "light"


def palette_for_theme(theme: str | None) -> dict[str, str]:
    """返回指定主题对应的图表调色板。

    Args:
        theme: WebUI 主题名。

    Returns:
        dict[str, str]: 颜色字典，键与 ``PALETTE_KEYS`` 一致。
    """
    return dict(_DARK_PALETTE if chart_theme_group(theme) == "dark" else _LIGHT_PALETTE)


def series_colors(theme: str | None) -> list[str]:
    """返回图例与概览行所需的五个序列颜色。

    Args:
        theme: WebUI 主题名。

    Returns:
        list[str]: 与 ``ap_chart.js`` 中 ``seriesColors`` 顺序一致的十六进制颜色。
    """
    palette = palette_for_theme(theme)
    return [palette[key] for key in _SERIES_COLOR_KEYS]


def is_light_theme(theme: str | None) -> bool:
    """判断主题是否属于浅色族（Light 与高级材质共用浅色图表）。

    Args:
        theme: WebUI 主题名。

    Returns:
        bool: 浅色主题返回 True。
    """
    return chart_theme_group(theme) == "light"


def chart_trend_colors(theme: str | None) -> dict[str, Any]:
    """返回涨跌配色，供概览行的数值着色使用。

    Args:
        theme: WebUI 主题名。

    Returns:
        dict[str, Any]: 含 ``inc``（上升）、``dec``（下降）、``flat``（持平）颜色。
    """
    palette = palette_for_theme(theme)
    return {
        "inc": palette["inc"],
        "dec": palette["dec"],
        "flat": palette["flat"],
    }
