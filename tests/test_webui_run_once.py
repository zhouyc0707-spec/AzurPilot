"""旧版总览的单次任务按钮与实际任务分栏，不连接游戏进程。"""

import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.api.protocol import ApiError
from module.config.config import Function
from module.webui.app_dashboard import DashboardMixin
from tests.pywebio_stubs import _StubOutput


class _Dashboard(DashboardMixin):
    def __init__(self):
        self.alas_name = "alas"
        self.page = "Overview"
        self.visible = True
        self.alas = SimpleNamespace(
            alive=False, current_task=None, started_func=None, run_id="run-1"
        )
        self._overview_snapshot = None
        self._overview_scheduler_switch = Mock()
        self._refresh_scheduler_switch = Mock()
        self.alas_set_group = Mock()
        now = datetime(2026, 10, 9, 10)
        self.alas_config = SimpleNamespace(
            data={
                task: {"Scheduler": {
                    "Enable": True, "Command": task, "NextRun": next_run,
                }}
                for task, next_run in (
                    ("Research", now), ("Commission", now),
                    ("Exercise", now + timedelta(hours=1)),
                )
            },
            load=Mock(), get_next_task=Mock(),
        )
        self.alas_config.pending_task = [
            Function(self.alas_config.data[task])
            for task in ("Research", "Commission")
        ]
        self.alas_config.waiting_task = [
            Function(self.alas_config.data["Exercise"])
        ]


class TestOverviewRunOnce(unittest.TestCase):
    def setUp(self):
        self.gui = _Dashboard()
        self.buttons = []
        self.scope = []
        self.texts = []

        @contextmanager
        def use_scope(name):
            self.scope.append(name)
            try:
                yield
            finally:
                self.scope.pop()

        def put_button(**kwargs):
            self.buttons.append((tuple(self.scope), kwargs))
            return _StubOutput()

        def put_text(text):
            self.texts.append((tuple(self.scope), text))
            return _StubOutput()

        patchers = [
            patch("module.webui.app_dashboard.t", side_effect=lambda key: key),
            patch("module.webui.app_dashboard.read_file", return_value={
                task: {"Scheduler": {"Command": task}}
                for task in self.gui.alas_config.data
            }),
            patch("module.webui.app_dashboard.use_scope", side_effect=use_scope),
            patch("module.webui.app_dashboard.clear"),
            patch("module.webui.app_dashboard.put_button", side_effect=put_button),
            patch("module.webui.app_dashboard.put_text", side_effect=put_text),
            patch("module.webui.app_dashboard.put_row", return_value=_StubOutput()),
            patch("module.webui.app_dashboard.put_column", return_value=_StubOutput()),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _button(self, task, label):
        return next(
            button for scopes, button in self.buttons
            if f"overview-task_{task}" in scopes and button["label"] == label
        )

    def _task_scopes(self, column):
        return [
            scopes[-1] for scopes, text in self.texts
            if scopes[0] == column and text.startswith("Task.")
        ]

    def test_idle_pending_and_future_tasks_have_run_once(self):
        self.gui.alas_update_overview_task()
        for task in self.gui.alas_config.data:
            button = self._button(task, "Gui.Button.RunOnce")
            self.assertFalse(button["disabled"])
        self.assertEqual([], self._task_scopes("running_tasks"))
        self.assertEqual(
            ["overview-task_Research", "overview-task_Commission"],
            self._task_scopes("pending_tasks"),
        )

    def test_scheduler_shows_actual_task_instead_of_first_pending(self):
        self.gui.alas.alive = True
        self.gui.alas.current_task = "Commission"
        self.gui.alas_update_overview_task()
        self.assertEqual(
            ["overview-task_Commission"], self._task_scopes("running_tasks")
        )
        self.assertEqual(["overview-task_Research"], self._task_scopes("pending_tasks"))
        self.assertTrue(self._button("Research", "Gui.Button.RunOnce")["disabled"])
        self.assertNotIn("disabled", self._button("Research", "Gui.Button.Setting"))

    def test_single_task_visible_before_first_worker_event(self):
        self.gui.alas.alive = True
        self.gui.alas.started_func = "task:Exercise"
        self.gui.alas_update_overview_task()
        self.assertEqual(["overview-task_Exercise"], self._task_scopes("running_tasks"))
        self.assertEqual([], self._task_scopes("waiting_tasks"))
        self.assertFalse(self._button("Exercise", "Gui.Button.Stop").get("disabled", False))
        self.assertTrue(self._button("Research", "Gui.Button.RunOnce")["disabled"])

    def test_running_task_that_disabled_itself_stays_in_running_column(self):
        self.gui.alas.alive = True
        self.gui.alas.started_func = "task:Exercise"
        self.gui.alas_config.data["Exercise"]["Scheduler"]["Enable"] = False
        self.gui.alas_config.waiting_task = []
        self.gui.alas_update_overview_task()
        self.assertEqual(["overview-task_Exercise"], self._task_scopes("running_tasks"))
        self._button("Exercise", "Gui.Button.Stop")

    def test_finished_worker_returns_task_and_enables_buttons(self):
        self.gui.alas.started_func = "task:Commission"
        self.gui.alas.current_task = "Commission"
        self.gui.alas_update_overview_task()
        self.assertEqual([], self._task_scopes("running_tasks"))
        self.assertFalse(self._button("Commission", "Gui.Button.RunOnce")["disabled"])

    def test_each_run_callback_keeps_its_task_and_instance(self):
        self.gui.alas_update_overview_task()
        callbacks = [
            self._button(task, "Gui.Button.RunOnce")["onclick"]
            for task in ("Research", "Commission", "Exercise")
        ]
        self.gui.alas_name = "other"
        self.gui._overview_run_task = Mock()
        for callback in callbacks:
            callback()
        self.assertEqual(
            [("Research", "alas"), ("Commission", "alas"), ("Exercise", "alas")],
            [call.args for call in self.gui._overview_run_task.call_args_list],
        )

    def test_stop_callback_keeps_old_run_id_after_new_worker_started(self):
        self.gui.alas.alive = True
        self.gui.alas.started_func = "task:Commission"
        self.gui.alas_update_overview_task()
        callback = self._button("Commission", "Gui.Button.Stop")["onclick"]
        self.gui.alas.run_id = "run-2"
        self.gui.alas_name = "other"
        self.gui._overview_run_task = Mock()
        callback()
        self.gui._overview_run_task.assert_called_once_with("Commission", "alas", "run-1")

    def test_run_and_stop_use_shared_runtime_and_refresh_immediately(self):
        with (
            patch("module.webui.app_dashboard.run_once") as run_once,
            patch("module.webui.app_dashboard.stop_once") as stop_once,
            patch.object(self.gui, "alas_update_overview_task") as refresh,
        ):
            self.gui._overview_run_task("Commission", "alas")
            self.gui._overview_run_task("Commission", "alas", "run-1")
        run_once.assert_called_once_with("alas", "Commission")
        stop_once.assert_called_once_with("alas", "Commission", "run-1")
        self.assertEqual(2, refresh.call_count)
        self.assertEqual(2, self.gui._refresh_scheduler_switch.call_count)

    def test_business_error_does_not_report_task_started(self):
        with (
            patch("module.webui.app_dashboard.run_once", side_effect=ApiError(
                "INSTANCE_RUNNING", "实例已在运行"
            )),
            patch("module.webui.app_dashboard.toast") as toast,
            patch.object(self.gui, "alas_update_overview_task") as refresh,
        ):
            self.gui._overview_run_task("Commission", "alas")
        toast.assert_called_once_with("实例已在运行", color="warn")
        refresh.assert_called_once()

    def test_late_old_instance_result_does_not_refresh_new_page(self):
        self.gui.alas_name = "other"
        with (
            patch("module.webui.app_dashboard.stop_once") as stop,
            patch.object(self.gui, "alas_update_overview_task") as refresh,
        ):
            self.gui._overview_run_task("Commission", "alas", "run-1")
        stop.assert_called_once_with("alas", "Commission", "run-1")
        refresh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
