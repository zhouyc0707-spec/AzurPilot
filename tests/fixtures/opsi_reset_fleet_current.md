# 国服大世界重置舰队窗口

`opsi_reset_fleet_current.png` 来自 2026-11-01 的错误现场，仅保留底部公共提示文字与「立刻前往」按钮；其余区域置黑，不包含账号、舰队配置、角色或指挥喵信息。

旧 `RESET_FLEET_PREPARATION` 提示在原始画面上的匹配相似度约 0.743，低于阈值；普通 `FLEET_PREPARATION` 模板相似度约 0.950，导致错误进入普通战役的左上角取消分支。本夹具保留这两处识别条件，用于确认改为大世界专用入口后，点击冷却期间仍不会误点取消。

`assets/cn/os_handler/RESET_FLEET_PREPARATION_2.png` 只含当前公共绿色提示文字，`.BUTTON.png` 定义与原资源相同的右下角点击区。新模板仅用于国服；其他服务器沿用原模板。
