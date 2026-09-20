"""体力图表画布的缩放与「恢复可见后重新量尺寸」。

两个真实缺陷（都在 `webapp/ap_chart.js`）：

1. **dpr 缩放用了叠加的 `ctx.scale`**。`ctx.scale` 是乘法叠加，`ctx.setTransform`
   是直接赋值；一旦初始化函数在画布尺寸未变时被重跑就会把 dpr 再乘一遍。同目录的
   `resource_chart.js` 一直用的是 `setTransform`，两份脚本应对齐。

2. **图表隐藏期间重绘会写死 fallback 尺寸，恢复可见后没人重新量尺寸**。
   `display:none` 时 `clientWidth/clientHeight` 都是 0，`initChart()` 落到
   fallback 的 800×360；恢复可见时 `display` 切换**不触发 `window.resize`**，
   于是画布带着错误的内部分辨率显示，曲线被拉伸（现场实测：attrs=800×360，
   实际显示 731×262，纵向压掉 27%），只有整页刷新才恢复。
   这也是「关掉日志后曲线变粗」的真正原因。
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEBAPP = ROOT / "webapp"


class TestChartCanvasScale(unittest.TestCase):
    def test_chart_scripts_use_idempotent_settransform(self):
        for name in ("ap_chart.js", "resource_chart.js"):
            with self.subTest(script=name):
                source = (WEBAPP / name).read_text(encoding="utf-8")
                self.assertIn(
                    "setTransform(dpr, 0, 0, dpr, 0, 0)",
                    source,
                    f"{name} 必须用 setTransform 设置 dpr 缩放",
                )
                self.assertNotIn(
                    "ctx.scale(dpr, dpr)",
                    source,
                    f"{name} 不能用 ctx.scale 设置 dpr 缩放（会叠加）",
                )

    def test_ap_chart_cancels_pending_resize_timer_on_cleanup(self):
        """图表重建后不能留下未撤销的防抖定时器。"""
        source = (WEBAPP / "ap_chart.js").read_text(encoding="utf-8")
        cleanup_body = source[
            source.index("function cleanup()"):source.index("function addListener")
        ]
        self.assertIn("clearTimeout(resizeTimer)", cleanup_body)
        self.assertIn("resizeTimer = null", cleanup_body)

    def test_overlay_canvas_resets_transform_before_each_draw(self):
        """叠加层没有在初始化时缩放，必须在每个绘制入口自行复位。"""
        source = (WEBAPP / "ap_chart.js").read_text(encoding="utf-8")
        self.assertGreaterEqual(
            source.count("oc.setTransform(1, 0, 0, 1, 0, 0)"),
            5,
            "叠加层的每个绘制入口都应先复位变换矩阵",
        )


class TestChartRelayoutAfterHidden(unittest.TestCase):
    """隐藏期间重绘后，恢复可见必须重新量尺寸。"""

    def setUp(self):
        self.source = (WEBAPP / "ap_chart.js").read_text(encoding="utf-8")

    def test_ap_chart_exposes_relayout_registry(self):
        self.assertIn("window._alasApChartRelayout = window._alasApChartRelayout || {}", self.source)
        self.assertIn("window._alasApChartRelayout[chartId] = function ()", self.source)

    def test_relayout_compares_canvas_internal_size_not_display_size(self):
        """判断依据必须是「画布内部分辨率是否与显示尺寸相符」。

        只比显示尺寸会漏判：恢复可见时显示尺寸本来就没变（一直是 731×262），
        变的只是它在隐藏期间被错误地重绘过。
        """
        body = self.source[
            self.source.index("window._alasApChartRelayout[chartId] = function ()"):
            self.source.index("cleanupCallbacks.push(function ()")
        ]
        self.assertIn("cv.width === cw * ratio", body)
        self.assertIn("cv.height === ch * ratio", body)
        # 仍隐藏时必须直接返回，不能拿 0 尺寸去重绘
        self.assertRegex(body, r"if \(!cw \|\| !ch\) return;")

    def test_relayout_is_unregistered_on_cleanup(self):
        """图表重建后旧闭包不能留在注册表里被开关调用。"""
        self.assertIn(
            "delete window._alasApChartRelayout[chartId]",
            self.source,
        )
        self.assertIn("cleanupCallbacks", self.source)

    def test_log_toggle_notifies_chart_relayout(self):
        """日志开关切换后要通知图表重量尺寸（display 切换不触发 window.resize）。"""
        source = (ROOT / "module" / "webui" / "app_overview.py").read_text(
            encoding="utf-8"
        )
        body = source[
            source.index("def _apply_log_mode_display"):
            source.index("def _render_log_toggle_button")
        ]
        self.assertIn("window._alasApChartRelayout", body)
        self.assertIn("registry[key]()", body)
        # 通知必须放在 display 切换之后，否则量到的还是旧布局
        self.assertLess(
            body.index('charts.style.display = show_log ? "none" : ""'),
            body.index("registry[key]()"),
        )


if __name__ == "__main__":
    unittest.main()
