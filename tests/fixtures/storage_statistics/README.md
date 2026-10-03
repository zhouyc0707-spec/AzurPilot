# 仓库识别截图夹具

新增 `live_*` 样本来自 2026-10-03 国服 MuMu 的 1280×720 原生截图。数量由人工查看原始画面核对；不使用旧 OCR 输出推导真值，不含账号资料。

- `live_scroll_start.png` / `live_scroll_overlap.png`：顶部与第一次翻页，包含虹彩变化及完整重叠行。
- `live_glow_1.png` / `live_glow_2.png`：同一物品的不同虹彩动画帧。
- `live_subpixel_rows.png`：同一行七种未知舰船蓝图在两处滚动位置的原生 128px 格，按两行七列拼接；同时验证同行匹配与不同蓝图拒绝。
- `live_amount_46.png` / `live_amount_10598.png` / `live_amount_9657.png`：完整物品格，覆盖抗锯齿数量、五位数量与首位被擦除时拒绝读取。
- `live_digits.png` / `live_digits.json`：125 个 102×27 原生数量区域纵向拼接及人工核对的物品、数量索引。包含翻页中的研发图纸、突破部件、心智单元及金部件字形；测试按切片恢复到数量区。样本数量仅描述采集时的画面，不代表当前库存。

这些夹具可离线回归识别与虚拟滚动，不连接模拟器，也不写用户配置或统计数据库。`assets/stats/storage_items/amount_digits.png` 中新增字形从完整原生数字逐位归一化而来，保留既有置信度及完整性检查。
