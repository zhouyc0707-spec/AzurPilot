"""旧版单次按钮的隔离浏览器夹具：不注册业务 API，不创建真实工作进程。"""

import copy
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import uvicorn
from pywebio import config
from pywebio.output import put_button, put_scope, put_text
from pywebio.platform.fastapi import asgi_app
from pywebio.session import hold
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from module.api.protocol import ApiError
from module.config.config import Function
from module.webui import app_dashboard, lang
from module.webui.app_dashboard import DashboardMixin
from module.webui.app_overview import OverviewMixin


ROOT = Path(__file__).resolve().parents[1]
MANAGERS = {}
TEMPLATE = {
    task: {"Scheduler": {"Command": task}}
    for task in ("Research", "Commission", "Exercise")
}


def _run_once(instance, task):
    manager = MANAGERS[instance]
    if manager.alive:
        raise ApiError("INSTANCE_RUNNING", "夹具实例已在运行")
    manager.alive = True
    manager.started_func = f"task:{task}"
    manager.current_task = None
    manager.run_id = uuid4().hex
    return manager


def _stop_once(instance, task, run_id):
    manager = MANAGERS[instance]
    if manager.run_id != run_id or manager.started_func != f"task:{task}":
        raise ApiError("TASK_RUN_CHANGED", "夹具运行轮次已变化")
    manager.alive = False
    manager.current_task = None
    return manager


class _FixtureTaskHandler:
    def add(self, task, delay, *args):
        # 夹具只立即挂载首帧，后续由模拟动作刷新，不启动后台任务线程。
        if callable(task):
            task()
        else:
            next(task)
            next(task)


class _FixtureGUI(DashboardMixin, OverviewMixin):
    def __init__(self, data):
        self.alas_name = f"fixture-{uuid4().hex}"
        self.page = "Overview"
        self.visible = True
        self.is_mobile = False
        self.task_handler = _FixtureTaskHandler()
        self.alas = SimpleNamespace(
            alive=False, current_task=None, started_func=None, run_id=None,
            stop_by_user=lambda *args: self._complete(),
        )
        MANAGERS[self.alas_name] = self.alas
        self.alas_config = SimpleNamespace(
            data=data, load=lambda: None, get_next_task=self._queue,
            Optimization_WhenSchedulerStopped="stay_there",
        )
        self._queue()

    def _queue(self):
        self.alas_config.pending_task = [
            Function(self.alas_config.data[task]) for task in ("Research", "Commission")
        ]
        self.alas_config.waiting_task = [Function(self.alas_config.data["Exercise"])]

    def init_menu(self, name):
        self.page = name

    def set_title(self, title):
        pass

    def _mount_stat_panels(self):
        put_text("隔离验收，不连接游戏或真实实例")
        put_button("模拟自然完成", onclick=self._complete)

    def _get_log_mode(self):
        return False

    def _enter_log_mode(self, show):
        pass

    def _render_log_toggle_button(self, show):
        pass

    def _alas_start(self):
        self.alas.started_func = None
        self.alas.alive = True
        self.alas.current_task = "Commission"
        self.alas.run_id = uuid4().hex
        self.alas_update_overview_task()

    def _complete(self):
        self.alas.alive = False
        self.alas.current_task = None
        self.alas_update_overview_task()
        self._refresh_scheduler_switch(self._overview_scheduler_switch)

    def alas_set_group(self, task):
        put_text(f"已打开设置：{task}", scope="fixture-feedback")


def main():
    # 只替换待验证视图调用的运行边界，真实 ProcessManager 从未参与夹具。
    app_dashboard.run_once = _run_once
    app_dashboard.stop_once = _stop_once
    app_dashboard.read_file = lambda *args: copy.deepcopy(TEMPLATE)
    lang.reload()
    now = datetime(2026, 10, 9, 10)
    data = {
        task: {"Scheduler": {"Enable": True, "Command": task, "NextRun": next_run.isoformat()}}
        for task, next_run in (
            ("Research", now), ("Commission", now), ("Exercise", now + timedelta(hours=1)),
        )
    }
    with tempfile.TemporaryDirectory(prefix="azurpilot-legacy-run-once-") as temporary:
        path = Path(temporary) / "fixture.json"
        path.write_text(json.dumps(data), encoding="utf-8")

        @config(css_file=["static/alas.css", "static/light-alas.css"], css_style="""
            #pywebio-scope-content {position: static; height: 850px; width: 100%;}
            #pywebio-scope-schedulers {grid-template-rows: auto auto 1fr 1fr 1fr; width: 400px;}
            #pywebio-scope-overview {grid-template-columns: 400px 1fr; gap: 16px;}
        """)
        def index():
            fixture = json.loads(path.read_text(encoding="utf-8"))
            for groups in fixture.values():
                groups["Scheduler"]["NextRun"] = datetime.fromisoformat(groups["Scheduler"]["NextRun"])
            put_scope("content")
            put_scope("fixture-feedback")
            _FixtureGUI(fixture).alas_overview()
            hold()

        app = asgi_app(index, cdn=False, static_dir=str(ROOT / "assets/gui/css"))
        app.router.routes.append(Route("/healthz", lambda request: PlainTextResponse("ok")))
        uvicorn.run(app, host="127.0.0.1", port=22395, log_level="warning")


if __name__ == "__main__":
    main()
