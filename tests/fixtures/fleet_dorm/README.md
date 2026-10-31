# 换船休息离线截图

来源：用户在本次功能请求中提供的国服 1280×720 原始截图。

- `picker_selected.png`：`MuMu12-20261031-124333.png`；六艘已选，顶部容量等账号相关区域已遮黑。
- `picker_scrolled.png`：`MuMu12-20261031-124425.png`；滚动后的第 4 舰队三艘先锋，顶部已遮黑。
- `vanguard_top_slow.png` / `vanguard_row_slow.png`：`MuMu12-20261031-151041.png` / `MuMu12-20261031-151045.png`；顶部已选一艘第 1 舰队先锋，轻移一排后另两艘可见，顶部账号区域已遮黑。
- `training.png` / `rest.png`：`MuMu12-20261031-124243.png` / `MuMu12-20261031-124246.png`；仅保留页签、数量与关闭按钮，其他区域全部遮黑。

按钮素材由同批截图通过 `dev_tools.button_region_editor` 提取，运行 `dev_tools.button_extract` 生成。
识别测试使用项目引擎和显式 CPU OCR 配置；不连接模拟器，不读写真实账号配置。
