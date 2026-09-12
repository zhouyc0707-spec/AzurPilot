import threading
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from module.webui.app_statistics_page import StatisticsPageMixin


class _OutputStub:
    def style(self, _value):
        return self


class _NestedOutputStub(_OutputStub):
    """记录本次调用处于哪个 scope，用于断言装配结构。"""

    def __init__(self, rec, name, during=None):
        self._rec = rec
        self._name = name
        self._during = during

    def style(self, value):
        self._rec.append(("style", self._name, value, self._during))
        return self


class _ScopeRecorder:
    """以真实呈现顺序记录 put_scope / use_scope，供结构断言使用。"""

    def __init__(self):
        self.current = None
        self.calls = []

    def append(self, item):
        self.calls.append(item)

    def put_scope(self, name, content=None, **_kwargs):
        for item in content or []:
            if getattr(item, "_rec", None) is self:
                item._during = name
        # 记录创建时所在的 use_scope，用于断言 scope 的挂载位置
        self.calls.append(("put_scope", name, content or [], self.current))
        return _NestedOutputStub(self, name)

    def use_scope(self, name, **_kwargs):
        recorder = self

        class _Ctx:
            def __enter__(self):
                recorder.current = name
                return None

            def __exit__(self, *_exc):
                recorder.current = None
                return False

        return _Ctx()

    def names(self):
        return [name for kind, name, *_ in self.calls if kind == "put_scope"]

    def content_of(self, name):
        for kind, call_name, content, *_ in self.calls:
            if kind == "put_scope" and call_name == name:
                return content
        return None


class _TaskHandlerStub:
    def __init__(self):
        self.added = []

    def add(self, func, delay, pending_delete=False):
        self.added.append((func, delay, pending_delete))


class _StatisticsHarness(StatisticsPageMixin):
    def __init__(self):
        self.alas_name = "alas"
        self.page = "Overview"
        self._page_lock = threading.Lock()
        self._statistics_cache_key = None
        self._statistics_source_signature = None
        self._statistics_refresh_pending = False
        self.signature = "v1"
        self.rendered = []
        self.cleaned = []
        self.task_handler = _TaskHandlerStub()

    def init_menu(self, name=None):
        self.page = name

    def set_title(self, _title):
        return None

    def cleanup_client_resources(self, *names):
        self.cleaned.append(names)

    def _get_statistics_source_signature(self):
        return self.signature

    def alas_update_stat_resources(self, _clear=False):
        self.rendered.append("resources")

    def _render_ap_chart(self):
        self.rendered.append("ap")

    def _render_resource_chart(self):
        self.rendered.append("resource")

    def _render_opsi_stats(self):
        self.rendered.append("opsi")

    def _render_ship_exp(self):
        self.rendered.append("ship")

    def _render_commission_income(self):
        self.rendered.append("commission")


class TestStatisticsPageCache(unittest.TestCase):
    def setUp(self):
        self.gui = _StatisticsHarness()
        self.patches = (
            patch(
                "module.webui.app_statistics_page.use_scope",
                side_effect=lambda *_args, **_kwargs: nullcontext(),
            ),
            patch(
                "module.webui.app_statistics_page.put_scope",
                return_value=_OutputStub(),
            ),
            patch(
                "module.webui.app_statistics_page.put_button",
                return_value=_OutputStub(),
            ),
            patch("module.webui.app_statistics_page.t", side_effect=lambda key: key),
            patch("module.webui.app_statistics_page.run_js"),
        )
        for active_patch in self.patches:
            active_patch.start()

    def tearDown(self):
        for active_patch in reversed(self.patches):
            active_patch.stop()

    def test_reopening_unchanged_page_reuses_existing_render(self):
        self.gui.alas_set_stat()
        self.assertEqual(
            ["ap", "resource", "opsi", "ship", "commission"],
            self.gui.rendered,
        )

        self.gui.rendered.clear()
        self.gui.alas_set_stat()

        self.assertEqual([], self.gui.rendered)
        self.assertEqual(2, len(self.gui.task_handler.added))
        for callback, delay, pending_delete in self.gui.task_handler.added:
            self.assertEqual("_refresh_statistics_if_changed", callback.__name__)
            self.assertEqual(15, delay)
            self.assertTrue(pending_delete)

    def test_local_data_change_marks_refresh_without_replacing_sections(self):
        self.gui.alas_set_stat()
        self.gui.rendered.clear()
        self.gui.signature = "v2"

        self.gui._refresh_statistics_if_changed()

        self.assertEqual([], self.gui.rendered)
        self.assertTrue(self.gui._statistics_refresh_pending)

        self.gui._refresh_statistics_page()

        self.assertEqual(
            ["ap", "resource", "opsi", "ship", "commission"],
            self.gui.rendered,
        )
        self.assertEqual("v2", self.gui._statistics_source_signature)
        self.assertFalse(self.gui._statistics_refresh_pending)

    def test_switching_instance_replaces_cache_and_cleans_charts(self):
        self.gui.alas_set_stat()
        self.gui.rendered.clear()
        self.gui.alas_name = "alas2"

        self.gui.alas_set_stat()

        self.assertEqual(
            ["ap", "resource", "opsi", "ship", "commission"],
            self.gui.rendered,
        )
        self.assertEqual(
            [("__apChartCleanups", "__resourceChartCleanups")],
            self.gui.cleaned,
        )

    def test_background_check_does_not_render_after_navigation(self):
        self.gui.alas_set_stat()
        self.gui.rendered.clear()
        self.gui.signature = "v2"
        self.gui.page = "Overview"

        self.gui._refresh_statistics_if_changed()

        self.assertEqual([], self.gui.rendered)


class TestStatisticsPanelRegions(unittest.TestCase):
    """统计页分为固定的资源仪表盘区与自带滚动的图表区。"""

    def setUp(self):
        self.recorder = _ScopeRecorder()
        self.gui = _StatisticsHarness()
        self.patches = (
            patch(
                "module.webui.app_statistics_page.put_scope",
                side_effect=self.recorder.put_scope,
            ),
            patch(
                "module.webui.app_statistics_page.use_scope",
                side_effect=self.recorder.use_scope,
            ),
            patch(
                "module.webui.app_statistics_page.put_button",
                return_value=_OutputStub(),
            ),
            patch("module.webui.app_statistics_page.t", side_effect=lambda key: key),
        )
        for active_patch in self.patches:
            active_patch.start()

    def tearDown(self):
        for active_patch in reversed(self.patches):
            active_patch.stop()

    def test_mount_creates_dashboard_region_before_charts_region(self):
        self.gui._mount_stat_panels()

        # 子 scope 先于容纳它的父 scope 创建，仪表盘区必须排在图表区之前
        names = self.recorder.names()
        self.assertEqual(
            ["stat_resources", "stat_panels_dashboard", "ap_chart"],
            names[:3],
        )
        self.assertEqual("stat_panels_charts", names[-1])
        # 资源仪表盘必须落在 dashboard 区内
        dashboard_children = self.recorder.content_of("stat_panels_dashboard")
        self.assertEqual("stat_resources", dashboard_children[0]._name)
        # 仪表盘区内不得混入图表 scope
        for charts_scope in ("ap_chart", "opsi_stats", "ship_exp_table"):
            self.assertNotIn(
                charts_scope, [child._name for child in dashboard_children]
            )

    def test_charts_region_holds_every_scrolling_view(self):
        self.gui._mount_stat_panels()

        charts_children = [
            child._name for child in self.recorder.content_of("stat_panels_charts")
        ]
        self.assertEqual(
            ["ap_chart", "opsi_stats", "ship_exp_table", "commission_income"],
            charts_children,
        )

    def test_dashboard_scope_is_not_recreated_when_mounting_stat_page(self):
        """统计页复用总览页装配的面板，只补一个工具栏，不重建图表 scope。"""
        self.gui.page = "Stat"
        self.gui.alas_set_stat()

        self.assertEqual(["statistics-toolbar"], self.recorder.names())
        # 工具栏必须挂进图表区，才能随图表一起滚动
        self.assertEqual("stat_panels_charts", self.recorder.calls[0][3])

    def test_periodic_refresh_tasks_are_registered_once_per_region(self):
        self.gui._mount_stat_panels()

        delays = [(func.__name__, delay) for func, delay, _ in self.gui.task_handler.added]
        self.assertEqual(
            [
                ("alas_update_stat_resources", 10),
                ("_render_ap_chart", 60),
                ("_render_opsi_stats", 60),
                ("_render_ship_exp", 60),
                ("_render_commission_income", 60),
            ],
            delays,
        )


if __name__ == "__main__":
    unittest.main()
