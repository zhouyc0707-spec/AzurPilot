"""WebUI实例概览和守护模式"""

from collections.abc import Callable

from module.webui.app_dependencies import (
    BinarySwitchButton,
    RichLog,
    clear,
    deep_iter,
    get_device_id,
    get_localstorage,
    json,
    logger,
    put_button,
    put_html,
    put_none,
    put_scope,
    put_text,
    run_js,
    set_localstorage,
    t,
    updater,
    use_scope,
)

from module.webui.app_helpers import (
    DEMO_DEVICE_ID_TEXT,
    is_demo_mode,
)


from module.webui.app_types import WebUIMixinBase


class OverviewMixin(WebUIMixinBase):
    """WebUI实例概览和守护模式"""

    def _mount_scheduler_switch(self, start: Callable[[], None]) -> BinarySwitchButton:
        """创建调度器启停按钮，并让点击结果立即反映到按钮文案上。

        按钮文案原本只由 1 秒间隔的轮询任务刷新，点击后要等下一帧才变化；
        这里在启停动作结束后立刻推进一次切换器，用户点击后即可看到状态翻转。

        Args:
            start: 启动调度器的回调（概览页与守护页的启动参数不同）。

        Returns:
            BinarySwitchButton: 已渲染到 ``scheduler_btn`` 作用域的切换按钮。
        """
        switch = None

        def stop_scheduler() -> None:
            self.alas.stop_by_user(self.alas_config.Optimization_WhenSchedulerStopped)
            self._refresh_scheduler_switch(switch)

        def start_scheduler() -> None:
            start()
            self._refresh_scheduler_switch(switch)

        switch = BinarySwitchButton(
            label_on=t("Gui.Button.Stop"),
            label_off=t("Gui.Button.Start"),
            onclick_on=stop_scheduler,
            onclick_off=start_scheduler,
            get_state=lambda: self.alas.alive,
            color_on="off",
            color_off="on",
            scope="scheduler_btn",
        )
        return switch

    @staticmethod
    def _refresh_scheduler_switch(switch: BinarySwitchButton | None) -> None:
        """按当前状态重绘调度器按钮，状态未变化时切换器自身会跳过重绘。"""
        if switch is not None:
            switch.switch()

    @use_scope("content", clear=True)
    def alas_overview(self) -> None:
        self.init_menu(name="Overview")
        self.set_title(t(f"Gui.MenuAlas.Overview"))
        self._overview_snapshot = None
        # 总览页整页重建时 stat_panels 及其下的日志区 DOM 也一并重建，日志面板
        # 必须跟着重新渲染；不重置这个标志就会出现「面板 div 还在、里面的日志
        # 容器没了」——表现就是点「总览」重进页面后日志一片空白
        self._log_panel_mounted = False
        # 日志跟随任务的位置（_log_tail）挂在 RichLog 实例上，这里不能动

        put_scope("overview", [put_scope("schedulers"), put_scope("stat_panels")])

        with use_scope("schedulers"):
            put_scope(
                "scheduler-bar",
                [
                    put_text(t("Gui.Overview.Scheduler")).style(
                        "font-size: 1.25rem; margin: auto .5rem auto;"
                    ),
                    put_scope("scheduler_btn"),
                ],
            )
            put_scope(
                "stat-bar",
                [
                    put_text(t("Gui.Overview.Log")).style(
                        "font-size: 1.25rem; margin: auto .5rem auto;"
                    ),
                    put_scope("overview_log_btn"),
                ],
            )
            put_scope(
                "running",
                [
                    put_text(t("Gui.Overview.Running")),
                    put_html('<hr class="hr-group">'),
                    put_scope("running_tasks"),
                ],
            )
            put_scope(
                "pending",
                [
                    put_text(t("Gui.Overview.Pending")),
                    put_html('<hr class="hr-group">'),
                    put_scope("pending_tasks"),
                ],
            )
            put_scope(
                "waiting",
                [
                    put_text(t("Gui.Overview.Waiting")),
                    put_html('<hr class="hr-group">'),
                    put_scope("waiting_tasks"),
                ],
            )

        switch_scheduler = self._mount_scheduler_switch(self._alas_start)

        # April Fools: runaway start button
        if getattr(self, "af_flag", False):
            run_js("""
(function(){
    var surrendered = false;
    var bar = document.getElementById('pywebio-scope-scheduler-bar');
    if (!bar) return;
    bar.style.position = 'relative';
    bar.style.overflow = 'hidden';

    var flag = document.createElement('button');
    flag.textContent = '🏳️';
    flag.title = 'I give up...';
    flag.style.cssText = 'border:none;background:transparent;font-size:1.1rem;cursor:pointer;padding:0 4px;margin:auto 2px;opacity:0.45;transition:opacity .2s;flex-shrink:0;';
    flag.onmouseenter = function(){ flag.style.opacity='1'; };
    flag.onmouseleave = function(){ flag.style.opacity='0.45'; };
    flag.onclick = function(){
        surrendered = true;
        flag.style.display = 'none';
        var b = bar.querySelector('.btn-on');
        if(b){ b.style.transition='transform .35s cubic-bezier(.34,1.56,.64,1)'; b.style.transform=''; }
    };
    bar.appendChild(flag);

    bar.addEventListener('mousemove', function(e){
        if (surrendered) return;
        var btn = bar.querySelector('.btn-on');
        if (!btn) return;
        var r = btn.getBoundingClientRect();
        var bx = r.left + r.width/2, by = r.top + r.height/2;
        var dx = bx - e.clientX, dy = by - e.clientY;
        var dist = Math.sqrt(dx*dx + dy*dy);
        if (dist < 100 && dist > 1) {
            var pr = bar.getBoundingClientRect();
            var push = 100 - dist;
            var nx = dx/dist * push, ny = dy/dist * push * 0.3;
            var cur = btn.style.transform.match(/translate\\(([^,]+)px,\\s*([^)]+)px\\)/);
            var ox = cur ? parseFloat(cur[1]) : 0, oy = cur ? parseFloat(cur[2]) : 0;
            var tx = ox + nx, ty = oy + ny;
            var maxX = (pr.width - r.width) / 2 - 4;
            var maxY = (pr.height - r.height) / 2;
            tx = Math.max(-maxX, Math.min(maxX, tx));
            ty = Math.max(-maxY, Math.min(maxY, ty));
            btn.style.transition = 'transform .13s ease-out';
            btn.style.transform = 'translate('+tx+'px,'+ty+'px)';
        }
    });
})();
""")

        # 右侧统计图表面板
        with use_scope("stat_panels"):
            self._mount_stat_panels()

        # 日志区与图表区共用下方滚动区域，按上次的状态恢复显示
        show_log = self._get_log_mode()
        self._enter_log_mode(show_log)
        self._render_log_toggle_button(show_log)

        self.task_handler.add(switch_scheduler.g(), 1, True)
        self.task_handler.add(self.alas_update_overview_task, 10, True)

    def _get_log_mode(self) -> bool:
        """返回概览页下方区域是否显示日志（会话级记忆，刷新后保持）。"""
        if self._overview_show_log is None:
            self._overview_show_log = get_localstorage("overview_show_log") == "1"
        return self._overview_show_log

    def _set_log_mode(self, show_log: bool) -> None:
        """记录下方区域的显示模式，供切换页面/刷新后恢复。

        ``set_localstorage`` 走 ``eval_js``，在无会话上下文时可能抛异常；模式本身
        只存在实例属性里也必须生效，因此这里对持久化做容错。
        """
        self._overview_show_log = show_log
        try:
            set_localstorage("overview_show_log", "1" if show_log else "0")
        except Exception as e:  # noqa: BLE001 - 持久化失败不影响本次切换
            logger.warning(f"[WebUI-概览] 记录日志模式到 localStorage 失败: {e}")

    def _enter_log_mode(self, show_log: bool) -> None:
        """切换概览页下方区域：统计图表区 or 日志区。

        仪表盘固定在面板上方不受影响，切换只发生在下方滚动卡片区，因此这里
        只调整两个区域的显示状态，不重建任何 scope，也不重绘图表。

        Args:
            show_log: True 显示日志区，False 显示统计图表区。
        """
        if show_log and not self._log_panel_mounted:
            # 日志栏里的「自动滚动 / 截图预览」按钮只在首次进入时创建，
            # 反复切换不重建，避免按钮状态被重置
            with use_scope("stat_panels_log"):
                self._mount_log_panel()
            # 面板 DOM 每次（重）挂载都是空的，而跟随位置还停在旧内容之后，
            # 只追加新字节会让日志区只剩寥寥几行。丢弃位置，让跟随任务重新
            # 回读文件末尾的内容。
            self._log.reset_log_tail()
            # 自动滚到底由前端观察器完成（任务线程里 run_js 无效），
            # 必须在会话线程这里注册
            self._log.enable_auto_scroll()
            self._log_panel_mounted = True

        if show_log:
            self._ensure_log_follow_task()

        self._apply_log_mode_display(show_log)

    def _ensure_log_follow_task(self) -> bool:
        """确保日志跟随任务在任务列表里，需要时补注册。

        页面重挂载（点「总览」/切换菜单）会走 ``init_menu`` →
        ``remove_pending_task()``，把所有待删任务一并移除，日志跟随任务就在其中。
        只看自己记的标志会以为「任务还在」而永不补注册，日志区从此空白。
        因此每帧都回到任务处理器的真实列表里核对一次。

        Returns:
            bool: True 表示任务在列表里（原本就在，或本次补注册成功）。
        """
        added = getattr(self, "_log_follow_task", None)
        for task in getattr(self.task_handler, "tasks", []):
            if task is added or task.name == "append_log_from_file":
                self._log_follow_task = task
                self._log_task_added = True
                return True

        if not hasattr(self, "alas") or self.alas is None:
            self._log_task_added = False
            return False
        config_name = self.alas_name
        self.task_handler.add(
            lambda: self._log.append_log_from_file(config_name), 0.25, True
        )
        self._log_follow_task = self.task_handler.get_task("append_log_from_file")
        self._log_task_added = True
        return True

    @staticmethod
    def _apply_log_mode_display(show_log: bool) -> None:
        """按模式显示/隐藏图表区与日志区（纯前端切换，不重绘内容）。"""
        run_js(
            """
            (function () {
                var charts = document.getElementById("pywebio-scope-stat_panels_charts");
                var logPanel = document.getElementById("pywebio-scope-stat_panels_log");
                if (charts) charts.style.display = show_log ? "none" : "";
                if (logPanel) logPanel.style.display = show_log ? "flex" : "none";
            })();
            """,
            show_log=show_log,
        )

    def _render_log_toggle_button(self, show_log: bool) -> None:
        """渲染左侧「日志」一行的开关按钮，文案跟随当前模式。

        Args:
            show_log: True 表示日志已展开，按钮显示「关闭日志」。
        """
        with use_scope("overview_log_btn", clear=True):
            put_button(
                label=t("Gui.Button.CloseLog") if show_log else t("Gui.Button.Open"),
                onclick=self.alas_toggle_log,
                color="on",
            )

    def alas_toggle_log(self) -> None:
        """就地切换概览页下方区域：统计图表 ↔ 日志。

        只切换显示与按钮文案，不重建面板，因此来回切换不会重置图表状态，
        也不会重新拉取统计数据。
        """
        show_log = not self._get_log_mode()
        self._set_log_mode(show_log)
        self._enter_log_mode(show_log)
        self._render_log_toggle_button(show_log)

    def _clear_log_view(self) -> None:
        """清空日志区已显示的内容，并把跟随位置重置到文件末尾。

        供「清空日志」类操作调用：只丢弃面板里的内容，不动磁盘上的日志文件。
        """
        self._log.reset_log_tail()
        clear(self._log.scope)

    def _mount_log_panel(self) -> None:
        """创建日志栏与日志内容容器（渲染进当前 scope，即 stat_panels_log）。"""
        if (
            self._overview_log is None
            or self._overview_log_config_name != self.alas_name
        ):
            # 同一个实例复用同一个 RichLog（含日志跟随位置），换实例才重建
            self._overview_log = RichLog("log")
            self._overview_log_config_name = self.alas_name
            # 新建了 RichLog 就必须重新注册跟随任务：旧任务持有的是旧实例的
            # 引用，写不到新实例上，日志区会一直空白
            self._log_task_added = False
        else:
            self._overview_log.scope = "log"
        log = self._overview_log
        log.first_display = True
        log.last_display_time = {}
        self._log = log

        if "Maa" in self.ALAS_ARGS:
            (
                put_scope(
                    "log-bar",
                    [
                        put_text(t("Gui.Overview.Log")).style(
                            "font-size: 1.25rem; margin: auto .5rem auto;"
                        ),
                        put_scope(
                            "log-bar-btns",
                            [
                                put_scope("log_scroll_btn"),
                            ],
                        ),
                    ],
                ),
            )
        else:
            (
                put_scope(
                    "log-bar",
                    [
                        put_text(t("Gui.Overview.Log")).style(
                            "font-size: 1.25rem; margin: auto .5rem auto;"
                        ),
                        put_scope(
                            "log-bar-btns",
                            [
                                put_scope("log_scroll_btn"),
                                put_button(
                                    label="截图预览",
                                    onclick=lambda: run_js(
                                        f"window.alasToggleLivePreview({json.dumps(self.alas_name)});"
                                    ),
                                    color="off",
                                ),
                            ],
                        ),
                    ],
                ),
            )
        # version
        local_commit = updater.get_commit(short_sha1=True)
        version = local_commit[0] if local_commit and local_commit[0] else "Unknown"
        device_id = DEMO_DEVICE_ID_TEXT if is_demo_mode() else get_device_id()
        put_scope("log-container", [put_scope("log", [put_html("")])]).style(
            f"--device-id: '{device_id}'; --version: 'Ver.{version}';"
        )
        log.console.width = log.get_width()

        switch_log_scroll = BinarySwitchButton(
            label_on=t("Gui.Button.ScrollON"),
            label_off=t("Gui.Button.ScrollOFF"),
            onclick_on=lambda: log.set_scroll(False),
            onclick_off=lambda: log.set_scroll(True),
            get_state=lambda: log.keep_bottom,
            color_on="on",
            color_off="off",
            scope="log_scroll_btn",
        )
        self.task_handler.add(switch_log_scroll.g(), 1, True)

    @use_scope("content", clear=True)
    def alas_set_log(self) -> None:
        """打开日志：进入概览页并把下方区域切到日志。"""
        self._set_log_mode(True)
        self.alas_overview()

    @use_scope("content", clear=True)
    def alas_daemon_overview(self, task: str) -> None:
        self.init_menu(name=task)
        self.set_title(t(f"Task.{task}.name"))

        log = RichLog("log")

        if self.is_mobile:
            put_scope(
                "daemon-overview",
                [
                    put_scope("scheduler-bar"),
                    put_scope("stat-bar"),
                    put_scope("groups"),
                    put_scope("log-bar"),
                    put_scope("log", [put_html("")]),
                ],
            )
        else:
            put_scope(
                "daemon-overview",
                [
                    put_none(),
                    put_scope(
                        "_daemon",
                        [
                            put_scope(
                                "_daemon_upper",
                                [put_scope("scheduler-bar"), put_scope("log-bar")],
                            ),
                            put_scope("groups"),
                            put_scope("log", [put_html("")]),
                        ],
                    ),
                    put_none(),
                ],
            )

        log.console.width = log.get_width()

        with use_scope("scheduler-bar"):
            put_text(t("Gui.Overview.Scheduler")).style(
                "font-size: 1.25rem; margin: auto .5rem auto;"
            )
            put_scope("scheduler_btn")

        with use_scope("stat-bar"):
            put_text(t("Gui.Overview.Log")).style(
                "font-size: 1.25rem; margin: auto .5rem auto;"
            )
            put_button(
                label=t("Gui.Button.Open"),
                onclick=self.alas_set_log,
                color="on",
            )

        switch_scheduler = self._mount_scheduler_switch(lambda: self.alas.start(task))

        with use_scope("log-bar"):
            put_text(t("Gui.Overview.Log")).style(
                "font-size: 1.25rem; margin: auto .5rem auto;"
            )
            put_scope(
                "log-bar-btns",
                [
                    put_scope("log_scroll_btn"),
                    put_button(
                        label="截图预览",
                        onclick=lambda: run_js(
                            f"window.alasToggleLivePreview({json.dumps(self.alas_name)});"
                        ),
                        color="off",
                    ),
                ],
            )

        switch_log_scroll = BinarySwitchButton(
            label_on=t("Gui.Button.ScrollON"),
            label_off=t("Gui.Button.ScrollOFF"),
            onclick_on=lambda: log.set_scroll(False),
            onclick_off=lambda: log.set_scroll(True),
            get_state=lambda: log.keep_bottom,
            color_on="on",
            color_off="off",
            scope="log_scroll_btn",
        )

        config = self.alas_config.read_file(self.alas_name)
        for group, arg_dict in deep_iter(self.ALAS_ARGS[task], depth=1):
            if group[0] == "Storage":
                continue
            self.set_group(group, arg_dict, config, task)

        run_js(
            """
            $("#pywebio-scope-log").css(
                "grid-row-start",
                -2 - $("#pywebio-scope-_daemon").children().filter(
                    function(){
                        return $(this).css("display") === "none";
                    }
                ).length
            );
            $("#pywebio-scope-log").css(
                "grid-row-end",
                -1
            );
        """
        )

        self.task_handler.add(switch_scheduler.g(), 1, True)
        self.task_handler.add(switch_log_scroll.g(), 1, True)
        if hasattr(self, "alas") and self.alas is not None:
            self.task_handler.add(log.put_log(self.alas), 0.25, True)
