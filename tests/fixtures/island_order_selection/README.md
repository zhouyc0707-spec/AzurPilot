# 订单选中身份的脱敏夹具

来源为用户在本次任务中授权提供的 1280×720 普通订单截图。选中圆心为
约 `(749, 247)`，未选中对照圆心为约 `(703, 92)`。原始截图只保留在本地
忽略目录 `.cache/alas-island-order-assets/`，不进入仓库。

两个 PNG 只包含圆心外四个 22×22 角标区域的二值白色轮廓，其余像素置黑。
未保留角色头像、人物、库存、订单计数或账号信息。夹具圆心统一移至
`(69, 68)`，测试再放到不同订单位置，验证选中关联不依赖固定坐标。

游戏状态依据来自 `AzurLaneTools/AzurLaneLuaScripts` 固定提交
`0d8e215984ab15810b9be2ca6bc73e2ac36d88ee` 的
`CN/mod/island/view/page/order/islandorderpage.lua`：
`ClickOrder` 关闭上一订单的 `sel` 并开启目标订单的 `sel`；圆环颜色、
`finish` 绿勾及右侧货物详情均不能代替这一选中节点。
