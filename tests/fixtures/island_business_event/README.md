# 经营事件提示与关闭回归

`health_development_day.png` 来自 2026-11-08 04:04:16.616661 的真实错误帧，错误包编号 `1794081857183`。原图为运行时已经归一化的 1280×720 RGB 截图，显示岗位管理页上的“健康开发日”经营事件提示。

夹具只原样保留矩形 `(0, 0, 405, 310)` 的管理标题与页签、`(440, 250, 865, 465)` 的中央事件标识及提示文字，其余像素置黑。没有改变保留区域的坐标、缩放或颜色；夹具不包含账号名称、账号编号或密钥。

- 原始错误帧 SHA-256：`379a02bd60dadc4e1e15e818774344e48966bb89434fbefe9ae73432d069ceb9`
- 夹具 SHA-256：`ffb833be2ba315cd105be17732e1948acc5d7de2d6a1de6562964c489fbd7ef2`

## 识别资源来源

本次引入的资源来自 [LmeSzinc/AzurLaneAutoScript](https://github.com/LmeSzinc/AzurLaneAutoScript)，固定提交 `328121932060e01b593a3ae625635a9841882482`：

- `ISLAND_BUSINESS_EVENT_POPUP`：CN/EN 原图，JP/TW 按上游定义回退到 CN。CN 检测区域 `(593, 388, 604, 400)`，为事件条左侧的 N 徽标；EN 区域 `(562, 388, 574, 400)`。
- `ISLAND_BUSINESS_EVENT_POPUP_CANCEL`：CN/EN/JP/TW 各自原图，识别事件详情的“关闭”按钮。
- `ISLAND_BUSINESS_EVENT_SAFE_AREA`：从上游 CN `ISLAND_CLICK_SAFE_AREA.png` 原样复制并改名，其他服由生成器默认回退至 CN，点击区域 `(596, 687, 680, 720)`。

原始资源可从 [固定提交的 assets 目录](https://github.com/LmeSzinc/AzurLaneAutoScript/tree/328121932060e01b593a3ae625635a9841882482/assets) 核查；流程参考 [business.py 的 handle_restaurant_popup](https://github.com/LmeSzinc/AzurLaneAutoScript/blob/328121932060e01b593a3ae625635a9841882482/module/island/business.py#L54)。资源生成器仅生成三个新按钮定义，不调整匹配阈值。

## 证据边界

真实错误包的五帧均是第一阶段事件提示，CN 原始 N 徽标模板在五帧中的匹配分数均为约 `0.997862`。当前夹具只保存其中末帧，不能当作已经完成两阶段关闭的实机证据。

测试中的后续“详情关闭按钮”和“干净经营页签”由上游、现有模板资源合成，不是该次故障后续实屏。真实虚化页签负例从本夹具移除 N 徽标生成；这也是有意构造的过渡状态，不是另一次真实采集帧。四服测试只验证资源自匹配与生成坐标，没有宣称 EN/JP/TW 实机验证。

游戏行为另可对照 AzurLaneTools/AzurLaneLuaScripts 固定提交 `f5ec9aefb976ae4065b72cd714b4fdd5dfb3222c` 的 [IslandPostEventWindow](https://github.com/AzurLaneTools/AzurLaneLuaScripts/blob/f5ec9aefb976ae4065b72cd714b4fdd5dfb3222c/CN/mod/island/view/page/msgbox/window/islandposteventwindow.lua#L26)：首次点击展开详情，动画完成后才能关闭；“打开”按钮另有进入经营的行为，本修复只使用“关闭”。[事件定义](https://github.com/AzurLaneTools/AzurLaneLuaScripts/blob/f5ec9aefb976ae4065b72cd714b4fdd5dfb3222c/CN/sharecfg/island_manage_event.lua) 中“啾啾旅行团”“健康开发日”“美食评审”共用该窗口，不依赖具体事件标题识别。
