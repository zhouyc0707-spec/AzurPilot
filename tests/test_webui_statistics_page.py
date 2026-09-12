import threading
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from module.webui.app_statistics_page import StatisticsPageMixin


class _OutputStub:
    def style(self, _value):
        return self


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

    def alas_update_stat_resources(self, _clear=False):
        """资源概览条刷新任务：真实实现来自 Dashboard mixin，这里只记录注册行为。"""
        self.rendered.append("stat_resources")

    def _get_statistics_source_signature(self):
        return self.signature

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
            patch("module.webui.app_statistics_page.t", side_effect=lambda key: key),
            patch("module.webui.app_statistics_page.run_js"),
        )
        self.started = {}
        for active_patch in self.patches:
            self.started[active_patch.attribute] = active_patch.start()

    def tearDown(self):
        for active_patch in reversed(self.patches):
            active_patch.stop()

    def test_reopening_unchanged_page_reuses_existing_render(self):
        self.gui.alas_set_stat()
        self.assertEqual(
            ["ap", "opsi", "ship", "commission"],
            self.gui.rendered,
        )

        self.gui.rendered.clear()
        self.gui.alas_set_stat()

        self.assertEqual([], self.gui.rendered)
        # 首次挂载会额外注册资源概览条的周期刷新，重新进页只追加轻量轮询任务
        self.assertEqual(3, len(self.gui.task_handler.added))
        mount_task, *refresh_tasks = self.gui.task_handler.added
        self.assertEqual("alas_update_stat_resources", mount_task[0].__name__)
        self.assertEqual(10, mount_task[1])
        for callback, delay, pending_delete in refresh_tasks:
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
            ["ap", "opsi", "ship", "commission"],
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
            ["ap", "opsi", "ship", "commission"],
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

    def test_resource_strip_is_mounted_first(self):
        """资源仪表盘必须是页面第一个区块（配合 sticky 常驻上方），且趋势图不再挂载。"""
        self.gui.alas_set_stat()

        scopes = [
            call.args[0]
            for call in self.started["put_scope"].call_args_list
            if call.args
        ]

        self.assertEqual("stat_resources", scopes[0])
        self.assertNotIn("statistics-toolbar", scopes)
        self.assertNotIn("resource_chart", scopes)


if __name__ == "__main__":
    unittest.main()
