# 前端

远程页的 `src/remote/PairingPanel.tsx` 展示服务器生成的配对码与标准二维码（`src/remote/PairingQr.tsx`）。
`PairingQr` 将 128 位单次配对密钥编码为 `azurpilot://pair?secret=<secret>` 协议二维码并支持一键复制六码与链接；AFA 在原生网关完成配对与认证，见 [远程访问](remote-access.md)。

二维码使用相连的圆角模块与圆角定位框，保留四模块静区、32px 纯白内边距及深色码点；主题色只少量混入深色基底。等待配对时，`PairingScanGuide` 在操作区旁展示手机对准二维码、扫描与识别反馈的六秒循环示意。示意图使用二维码图标，不接收配对密钥，其识别反馈不表示实际配对成功；真实二维码保持静止。窄屏时示意移至下方，离开视口或切到后台时暂停；系统开启减少动态效果时保留静态取景画面。示意图只保留辅助阅读标签，不展示说明文字。

> React 控制台（frontend/）：React + TypeScript + Vite 实现的 WebUI，业务通信统一走 `/api/v1/ws`。详细界面设计与协议见 frontend/README.md 与 frontend/API.md。

## 1. 模块概述

[资源管理](resource-management.md) 在所有主题均位于「资源统计」内的「资源趋势」旁边，路径为 `/i/:instance/statistics?view=management`；旧 `/resources` 链接统一自动跳转，不再保留一级资源管理入口。旧版独立统计页顶部提供「资源趋势／资源管理」切换，「资源趋势」继续使用本地整页统计；总览内嵌统计不显示切换条、不跟随外层 `view` 参数。切换主题后保留当前资源管理视图。由 `pages/ResourceManagement.tsx` 与 `resources/` 提供 ECharts 桑基图、库存与收支明细。图节点支持资源与任务筛选，明细支持分页和完整区间 CSV 导出；原调度的石油自动控制使用现有配置事务接口保存。五种语言、窄屏和空数据状态均沿用控制台组件与主题。本地保留 `zh-MIAO` 的已保存偏好、原有字典与配置翻译；新增通用文案先继承简体中文，日期格式仍使用 `zh-CN`。

所有主题在「舰队管理」下提供「舰队信息」、[心智单元计算器](mind-calculator.md)、[舰队科技](fleet-technology.md) 与智能换船。计算器路径为 `/i/:instance/task/MindCalculator`，旧 `/mind-calculator` 自动跳转；沿用 `components/taskNavItems.ts` 的 WebUI 页面导航机制，同时覆盖侧栏树、悬浮菜单及顶栏任务跳转，不注册新的游戏任务。「舰队信息」由 `pages/FleetInfoPage.tsx` 提供舰队卡片和扫描开关，调用 `tasks.run(FleetScan)`，只在该扫描运行时调用 `scheduler.stop`；其他任务运行、更新或连接不可用时禁用。启动前等待配置队列保存，再以最新修订保存 `FleetInfo.FleetInfo.UpdateEmotion`（扫描时更新心情）选项；关闭时保留已有舰位心情参考，不进行被动学习。扫描运行期间及结束后重读舰队信息，显示扫描确认的舰位，不为历史记录推断舰位。浏览页面不启动游戏，也不修改实例自启设置；手动指定舰位互换尚未提供独立按钮，智能换船使用下述策略页。

舰队信息的[桌面截图](images/fleet-management-desktop.png)与[手机截图](images/fleet-management-mobile.png)
来自隔离服务及合成记录，只验证舰位、未知心情和扫描选项展示，不代表实际账号操作。

「舰队管理」另有 `/i/:instance/task/FleetRotation` 智能换船页面，属于独立配置页，
不注册游戏任务，也不显示调度状态或启动任务按钮。顶部启用开关默认关闭；六支水面
舰队的36个舰位默认均为“不变”，可逐位改为“换船”，并为每支目标队多选候补队。
规则保存到 `FleetInfo.FleetRotation.Enabled/Rules`，不调用 `tasks.run`。页面显示已保存
的上次来源与本轮进度，任务运行时只刷新进度，不覆盖尚未保存的规则编辑。
清空记录按钮仅在任务停止时可用，不允许直接改写内部进度。当前效果见
[桌面截图](images/fleet-rotation-desktop.png)与[手机截图](images/fleet-rotation-mobile.png)，
均为隔离服务和合成配置。实际刷图触发由战役出击前策略负责。

舰队科技路径为 `/i/:instance/task/FleetTechnology`，同样覆盖侧栏树、悬浮菜单及顶栏跳转并隐藏任务右栏；只在用户启动扫描时运行 `FleetTechScan`，人工编辑和心智清单同步仅更新当前实例记录。

[心智单元计算器](mind-calculator.md) 的新旧入口共用舰船清单、待核对状态、导入导出与费用计算。该页隐藏任务右栏；自动船坞扫描属于独立游戏工具，仅支持国服且要求当前实例已停止，其余计算和文件处理无需操作游戏。

`TaskConfig` 把 `Task.<task>.help` 展示为参数列首卡，并在舰队信息中显示心情；旧数据缺少心情时显示未知。商店使用上游的任务参数和过滤器界面，活动商店的 `物品:数量上限` 兼容文本由配置翻译说明并原样提交。旧版统计整页、实例 `Activity` 缓存、顶栏重启与主题切换、模拟器运行状态、岛屿任务总开关及实时指挥喵报告继续沿用本地实现。

旧版总览的日志／统计切换卡片中央提供「维护日」星期选择器（`components/MaintenanceWeekday.tsx`），绑定同一个 `Exercise.Exercise.MaintenanceWeekday`，不另存星期或日期。选择后复用实例编辑队列立即保存，并显示待保存、成功或失败状态；演习提前和困难图维护日18点各自开关保持原值，排程重查由既有 `ConfigService.patch()` 处理。该星期每周重复，直到用户修改。返回页面、连接恢复或重新聚焦时重新读取；旧读取不覆盖新的保存回执，实例切换丢弃旧控件状态。仅旧版主题显示，布局样式位于 `legacy.css`，不随总览的高频资源推送反复读取整份配置。

frontend/ 是 AzurPilot 的 React 浏览器控制台，覆盖主页与实例导航、任务配置表单、总览日志与截图预览、统计图表、系统设置、远程访问与更新器；本地保留的 PyWebIO 是另一个运行入口。前端不包含任何游戏逻辑，所有业务操作都通过 WebSocket API 交给 [API 服务](api.md)执行。

frontend/README.md 与 frontend/API.md 已经是本前端的详细文档：前者覆盖界面行为、主题材质、启动开发与迁移边界，后者定义消息协议。本文不重复其内容，只作为模块文档体系的索引篇，说明目录分工、构建测试入口与生成产物红线。

公告加载不作为首屏就绪条件：`app/announcement.ts` 让外壳、主页和公告页共享一次在途查询和一个两分钟轮询；最后一个已连接使用者离开时清理轮询，重连恢复查询。手动刷新与正在进行的查询合并，已有正文和已读状态保留。后端只让 `announcement.get` 独立等待外网结果，避免公告超时阻塞实例、配置架构和总览；响应仍使用原请求 ID，其他操作顺序不变。回归入口为 `src/app/announcement.test.ts`、`tests/test_announcement_service.py` 和 `tests/test_announcement_socket.py`。

注意：项目中的 **1280×720 是游戏截图识别的约束，不适用于 WebUI 布局**。前端按普通响应式网页开发，支持移动端折叠菜单，e2e 视口为 1440×1100。

任务侧栏由 `TaskNav` 按设备布局分流，旧版和新版主题一致：宽度大于 950px 且支持悬停时，分类的二级任务使用 `TaskNavFlyout` 在主侧栏右侧展开，选中任务、点击外部或按 Escape 收起；窄屏和触屏使用 `TaskNavTree` 内嵌折叠。旧版保留自身配色、圆角与选中样式，树状高度动画仅作用于 `.task-group` 内，避免右侧浮层内容被隐藏。

任务队列在旧版左列、新版右栏及配置页共用 `TaskQueue`：已启用的待运行和等待中任务提供「执行一次」，单次运行的任务提供「停止」。操作按钮与配置导航分开，点击不会进入配置页；启动前等待当前实例的配置保存完成，断线或请求处理中禁用操作，同实例运行时禁用其他任务的启动。调度器卡片按 `singleTask` 区分单次执行，单次任务结束后不连带启动调度器。

移动端在 950px 及以下使用导航与调度抽屉。新版主题的调度栏必须使用固定定位，桌面 L 形容器中的相对定位仅限 951px 及以上，隐藏抽屉不得占用正文高度；看图模式把手也仅在桌面显示。新版首页在窄屏按公告、实例列表顺序单列排列，由首页内容区整体滚动。480px 及以下，新版总览的日志工具栏独占一行，避免覆盖日志与截图切换入口；统计表格搜索也另起一行，保留标题和导出操作的可读宽度。新版分类统计在 950px 及以下统一使用下拉菜单，完整列出分类名称，不裁切横排标签；编辑模式的合并页面开关保留在下拉旁边。桌面仍使用分段分类栏，紧凑主题按可用宽度改用下拉。相关回归位于 `frontend/e2e/mobile/mock.spec.ts`，使用触摸移动视口检查正文可见性、滚动可达性、抽屉与桌面断点切换，运行 `npm run test:e2e:mock --prefix frontend -- e2e/mobile/mock.spec.ts`。

统计页各分类均保留历史展示与筛选，并提供导出入口：页面可导出本类数据，表格有各自的导出明细按钮，图表支持保存为图片。

旧版统计的「大世界数据收集」与「本月／历史耄耋相接收获」按对应月份隐藏无记录的侵蚀等级，已有记录的零收益行仍显示。「查看历史月份」弹窗的累计行由独立 `cumulativeRows` 提供，本月未运行的等级仍可查看以前的累计数据；旧响应缺少该字段时兼容原有累计列。

新版分类统计只有在图表或原始记录卡至少一张可见时才请求时间序列；两者都隐藏时，汇总指标与其他表格继续读取。旧版整页统计仍走 `statistics.legacy`，不应用新版卡片隐藏规则。背景关闭时保留之前的地址和图库选择，重新启用相应来源即可恢复；侧栏把手仅在实际使用壁纸时显示。

React 旧版统计在首次显示、手动刷新、60 秒轮询及 `statistics` 事件时重取报表；统计事件按实例筛选并合并 300 毫秒内的通知，同一查询最多一个在途请求，期间通知合并为下一次刷新。`overview` 推送直接更新独立统计页的资源卡，不触发整页统计重查；总览内嵌统计复用外层资源卡，不重复请求资源。页面或浏览器标签隐藏时暂停刷新，恢复后先显示已有内容再静默更新；月份切换保留内容与滚动，切实例才清空，旧查询响应不能覆盖当前结果。原 PyWebIO 每 15 秒检查总库版本并提示刷新，仍单独维护。

茗喵证券交易所的注册和登录使用 `src/stock/Captcha.tsx` 与 `recaptcha.ts` 中的 Google reCAPTCHA v2，脚本及验证 iframe 统一走 `www.recaptcha.net`，官方静态依赖使用 `www.gstatic.com/recaptcha/`。认证请求字段为 `recaptchaToken`；私密密钥由 Go 交易所环境变量 `RECAPTCHA_SECRET_KEY` 读取。站点停用域名验证，Go 不匹配 hostname 或 action。切换注册/登录时立即清空 token，提交后重置，组件卸载后忽略延迟回调；前端与 Go 服务须同步升级。

## 2. 模块职责

### 负责

- 渲染单页应用（hash 路由）：主页、实例总览、任务配置、统计、系统设置、更新器等页面
- WebSocket 连接管理：认证、心跳、请求关联、超时与重连（`src/api/client.ts`）
- 配置表单展示与保存：字段保存队列、草稿恢复、重试与数值校验（`src/config/EditQueue.ts`）
- 通用设置的任务优先级由 `FieldInput` 的 `task_priority` 专用文本框显示，每行保留英文任务标识，并以 `# 本地化任务名` 附加已有翻译；`src/config/taskPriorityText.ts` 的纯函数负责显示转换与提交清理，保存到 `General.YukikazeTaskManager.TaskPriorityAdjustment` 时仅提交英文标识，使用 `\n> ` 分隔，翻译注释不写入配置（兼容注释行与全角箭头）
- 主题、语言、背景等浏览器侧偏好的保存与应用（localStorage / IndexedDB）
- 日志面板在数据进入 React state 前只保留最近 1000 条，作为后端环形缓冲之外的独立防线；切到截图页后仅接收有界缓冲，返回日志时一次合并，隐藏期间不持续更新日志 DOM。卡片聚合按重叠窗口复用未变中段，只重算被截断的首部和可能续行的尾部；筛选、排序或内容变化时重新核验。高度缓存随窗口淘汰，保留未变卡片的测量值。延迟历史响应不得覆盖较新的实时推送，游标回退与实时重置分别处理，防止跨进程混入旧日志
- 独立 mock 服务（`mock/`），无需 Python、ADB 或模拟器即可开发验证前端交互

### 不负责

- API 协议、认证会话与业务适配：在 `module/api`，见 [API 服务](api.md)
- 进程、OCR、更新与认证等运行时服务：在 `module/runtime`，见 [WebUI 总览](index.md)
- 游戏配置定义与翻译生成：在 `module/config/`，见 [配置系统](../config.md)
- 真实运行与完整业务校验：mock 服务只验证前端交互，安全策略以 Python API 测试为准

## 3. 模块位置

```text
frontend/
├── src/main.tsx              # 应用入口：hash 路由表、错误兜底、先加载主题再挂载
├── src/api/                  # client.ts 连接层；generated.ts 与 contract.json 为生成产物
├── src/app/                  # 布局、主题系统（theme.ts 按需加载）、连接上下文、各类偏好
├── src/pages/                # 页面：Home / Overview / TaskConfig / Statistics / Settings / Updater 等
├── src/stock/                # 茗喵证券交易终端、开户登录、身份信息与行情图表
├── src/components/           # 可复用组件：FormControls、FieldInput（含任务优先级文本框）、LogPanel、StatisticsChart、实例切换等
├── src/config/               # EditQueue 跨页面字段保存队列、草稿恢复、输入校验、taskPriorityText 任务优先级显示与提交清理
├── src/styles/               # 设计变量与界面样式（apple / forms / compact / minimal 等 css）
├── src/i18n.ts               # 控制台固定文案（五种语言），独立于游戏翻译 module/config/i18n
├── e2e/                      # Playwright 浏览器测试
├── mock/                     # 内存模拟服务（server.mjs、state.mjs）及其测试
├── playwright.config.ts      # e2e 主配置：连接 tests/serve_frontend.py 临时后端
├── playwright.mock.config.ts # e2e mock 配置：连接独立 mock server 与 Vite mock 模式
└── vite.config.ts            # 开发代理与相对构建（base: './'）
```

更细的文件级职责表见 frontend/README.md「目录职责」。

## 4. 核心入口

追代码从 `src/main.tsx` 开始：hash 路由表、顶层 ErrorBoundary 与主题加载流程都在这里。

茗交所入口为 `src/pages/StockExchange.tsx`，实例侧栏的「茗喵证券交易所」导航位于「资源统计」下方，由 `src/app/App.tsx` 提供；移动端从导航抽屉进入。交易终端独立顶栏固定在窗口顶部，返回总览位于用户名左侧，与用户入口、亮暗主题按钮同排；窄屏下用户名截断。`src/stock/OverviewLink.tsx` 统一提供返回链接和应用初始化、代码加载、渲染异常的居中兜底页；状态、行情加载和错误页也始终提供屏幕中间的返回入口。开户、登录弹窗的返回入口水平居中。玩家数据错误由入口页承接，包含完全重建本地账户的范围预览和确认；实例配置管理不会展示交易身份错误。`src/stock/theme.tsx` 管理独立主题，默认暗色，通过 `localStorage` 的 `azurpilot.stock-theme` 记住本机选择，不跟随 WebUI 或系统主题；`src/stock/theme.css` 定义亮色语义配色，图表和验证码使用同一主题上下文，全屏图表的 Portal 显式携带主题属性。切换保留页面、表单与图表缩放范围；交互与联调方式见 [前端 README「茗喵证券交易所」](../../../frontend/README.md#茗喵证券交易所)。

行情涨跌幅统一以 `stock.open`（上海时间今日第一条已保存的行动力报价）为基准，市场列表、排序、滚动行情与证券详情保持一致；补传或修正会更新开盘价，跨日重新取值，当日无报价或开盘价为 0 时显示 `—`，切换历史图表不会改用历史开盘价。总行动力低于控制台阈值（默认 500 点）时，每月 5 日起且开赛后本月强制退市：相关挂单撤销、多空持仓按触发报价结算，次月重新判断。股票退市不影响玩家登录、后台上传、排行或交易其他股票；界面显示退市状态并禁止该股票的委托。

`src/app/InstancePageActivities.tsx` 按当前实例保存普通外壳与交易终端。`App` 在总览及交易路由下保持同一总览节点，交易路由的 `Outlet` 留空；交易代码首次访问时才加载。隐藏分支使用 [React Activity](https://react.dev/reference/react/Activity) 保留 DOM 与状态并清理副作用，返回时重新订阅并静默刷新。日志、旧版统计和行情的初始化副作用必须区分「实例或查询目标改变」与「同实例页面恢复」，后者不清空已有内容。两个内容区分别使用 `main-content` 和 `stock-main-content`，滚动与动效应作用于可见分支。实例 key 改变、浏览器刷新或进入登录页后缓存销毁；不将账号、日志或行情写入浏览器持久缓存。切换性能回归见 `e2e/stock-overview-navigation.spec.ts`。

旧版总览内部的日志／统计也按首次访问创建独立 `Activity`，往返保留搜索、筛选、时间范围、图表缩放及各自滚动位置。隐藏时销毁 ECharts 实例并保存缩放，恢复后按现有选择重建；切实例清空这组面板状态。相关浏览器回归见 `e2e/frontend-smoothness.spec.ts`，刷新合并与旧响应隔离见 `src/pages/legacyStatisticsRefresh.test.ts`。

茗交所 Mock 还需单独启动相邻交易所仓库的 Go Mock 服务，本仓库 `dev:mock` 只提供模拟 API 与 Vite。代理将上游连接失败归为 `STOCK_UNAVAILABLE` 并提示启动方式，无效 JSON 或响应时间归为 `STOCK_INVALID_RESPONSE`，不再误报为浏览器请求格式错误；修复上游后可在交易页面重试连接。

| 入口 | 用途 |
| --- | --- |
| `npm run dev --prefix frontend` | 开发服务器（5173），代理到真实后端 |
| `npm run dev:mock --prefix frontend` | 同时启动 Vite 与 mock server（默认 22392），纯前端开发 |
| `npm run build --prefix frontend` | `tsc -b` 类型检查 + Vite 生产构建，产物在 `dist/` |
| `npm run typecheck --prefix frontend` | 仅 `tsc -b` 类型检查 |
| `npm test --prefix frontend` | vitest 单元测试（client、组件与纯函数） |
| `npm run test:e2e --prefix frontend` | Playwright e2e，主配置 |
| `npm run test:e2e:mock --prefix frontend` | Playwright e2e，mock 配置 |
| `npx playwright test --config playwright.mind.config.ts`（在 `frontend/` 内执行） | 心智单元计算器专用 mock 用例 |

Node.js >= 22.12（推荐 24），首次准备用 `npm ci --prefix frontend`。

主 e2e 配置在仓库根目录以 `uv run python -m tests.serve_frontend` 起服务，使用临时配置目录并拒绝执行真实游戏任务。主配置排除通用 mock、交易所、心智单元计算器和 PyWebIO 单次任务专用用例；它们分别由对应配置提供测试服务。通用 mock 配置拉起 mock server（22492）与 Vite mock 模式（5174），心智单元配置复用该服务并在离线文件处理时调用 Python，不连接模拟器。

## 6. 工作流程

- 开发联调：`uv run python gui.py` 起后端（默认端口见 config/deploy.yaml 的 `WebuiPort`），另开 `npm run dev`，Vite 将 `/api`（含 WebSocket）与 `/healthz` 代理到后端，`AZURPILOT_BACKEND` 可换目标；代理保留 Host，浏览器来源校验仍然有效。
- 纯前端开发：`npm run dev:mock`，模拟服务只读公开的 args.json、menu.json、翻译与 template.json，所有数据在内存，重启即重置。
- 构建部署：`npm run build` 产出 `dist/`。gui.py 启动时检查前端源码摘要，缺产物或源码变化时自动执行 `npm ci` 与构建；Docker 多阶段构建预装静态产物。
- 构建使用相对 base（`base: './'`）：远程访问经 `/<peer_id>/` 前缀式反代加载，绝对路径会 404。

## 16. 修改注意事项

- **生成产物红线**：`src/api/generated.ts` 与 `src/api/contract.json` 由 `uv run python -m dev_tools.export_api_schema` 生成，禁止手改；CI 会重新生成并用 `git diff --exit-code` 校验。新增 API 方法的顺序：先定义后端参数模型与路由，再运行生成器，然后更新 `src/api/types.ts` 响应类型与对应测试；不得通过方法名反射任意 Python 属性。
- **`base: './'` 不能改回绝对路径**：这是远程访问反代的硬性要求，且配套要求 deploy.yaml 的 `RemoteAccessMode` 为 ssh。
- **不要在 React 中重复登记游戏配置**：配置表单直接读取后端生成的 args.json、menu.json 与翻译文件；新增任务或参数只需改 `module/config/` 并重新生成。
- 新增选择器使用 `FormControls.tsx` 的 `Select`，配置字段使用 `FieldInput`，不要在各页面单独绘制箭头、勾选等图标。
- 控制台固定文案在 `src/i18n.ts`（五种语言）；游戏任务配置的名称与说明翻译在 `module/config/i18n/`，二者独立，别改错位置。
- 主题与背景偏好只存浏览器（localStorage / IndexedDB），不写入服务端部署配置；上传背景保存在 IndexedDB（最大 200 MB）。
- 旧版仪表盘资源卡外层统一使用 `1px solid var(--border)` 细边框，独立卡与合并卡共用，不给内层或合并子项重复加框。旧版浅色由 `styles/legacy.css` 单独设为纯白，覆盖外层和 `.resource-card-body` 内层；深色及玻璃材质沿用原底色。
- 旧版浅色普通材质的总览左列只承载布局，外层 `.instance-page-rail` 背景透明，让调度器、日志入口与任务计划之间的间隙显示页面底色或壁纸；卡片底色、新版与其他材质沿用原规则。
- 旧版总览中的任务计划作为独立卡片绘制完整四边外框，边宽与颜色统一沿用原顶边的 `1px solid var(--border)`；新版右栏及任务配置页沿用各自结构。
- 旧版统计页五个栏目统一使用 `.legacy-stats-card` 外层容器；体力变化的标题、时间范围、图例和画布收在同一卡片中，画布取消重复的内层圆角边框。
- 旧版体力变化不显示「重置图表」按钮；时间范围、图例筛选、按住 Ctrl 滚轮缩放和拖拽平移仍可用，双击仅恢复完整缩放范围。
- 旧版体力变化标题右侧提供「本月猫王／历史猫王」，独立统计页和总览内嵌统计共用 `AkashiShopRecords`。点击才通过只读 `statistics.akashiRecord` 加载当前实例服务器的纪录与完整商店图，不增加统计首屏请求；弹窗显示行动力、箱数和带服务器时区的时间，平纪录取最新。无纪录、图片缺失及错误均明确显示，关闭或切实例后忽略迟到响应；窄屏换行、深色沿用旧版主题。历史从功能启用后记录，不按旧购买统计回填；新版主题不增加入口。
- 旧版耄耋相接收获的月度表只显示所选月份的 9 列和有记录的侵蚀等级，已运行但高价值收益为零仍显示；「查看历史月份」弹窗同时列出月份和历月累计收获，按侵蚀等级分行展示有效轮数总和及四项每轮均值。按用户约定，海域未确认的掉落默认累加到侵蚀 5，不增加「未识别」行。此规则仅影响月度物品收获；出击轮次和累计均值沿用原有口径。累计通过 `AzurStats.load_meowofficer_farming()` 读取当前配置目录总库 `azurpilot.db` 的全局 `farming_aggregates`；旧 CSV 仅供迁移和显式导出定位，运行时不再读写。累计均值保留 6 位小数，缺失值沿用占位符；无历史月份仍可查看累计信息。后端 `statistics.legacy` 的 14 列契约保留，React 将月度与累计列分开展示，`cumulativeRows` 独立提供累计行，不受月度空行隐藏影响；原 PyWebIO 表格保持原布局并使用相同等级行。
- 月度收获六个高价值物品表头右侧提供文件夹按钮，调用 `statistics.meowScreenshotFolder.open`，月份以已显示的报表为准，切换历史月份后同步切换目标目录。请求期间禁用六个按钮以防重复打开，完成或失败均恢复；正常打开所选月份目录时不弹提示，缺失、回退到分类目录或打开失败时通过全局提示说明，不替换统计表或触发统计重载。悬停说明在脚本所在电脑打开；没有月归档时提示并打开已有分类目录，没有分类目录时只提示路径。浅色／深色、独立统计页和总览内嵌统计共用此入口，累计表不添加按钮。
- Windows 打开目录后，后端先临时关联窗口输入线程再激活，核验对应资源管理器窗口切到前台后取消该窗口的任务栏闪烁提示；无法确认时，通过现有错误提示告知目录已打开、可从任务栏查看，按钮恢复可重试。
- 岛屿自动生产规划保留简短「规划状态」，在其下以专用只读详情卡展示目标组成、最近实读现货与缺口、最近确认下单、理论每日配方和经营菜单。三个详情区默认折叠，窄屏表格在卡内横向滚动；未读到与已失效观测分别显示待巡检／待复核，下单预计产出不抵扣现货缺口。可见页面每十五秒局部读取 `config.get`，只刷新报告和摘要，不覆盖编辑草稿；离开页面、断连或切换实例后停止或忽略旧请求。该卡仅挂本地自定义 `IslandProductionPlanner` 组，不修改通用 `state` 控件及上游主题布局。

## 19. 调试方法

- 界面交互问题先用 mock 模式复现（无需 Python 与模拟器）；`AZURPILOT_MOCK_SCENARIO=empty` 从零实例测首次创建，`AZURPILOT_MOCK_PASSWORD` 可测登录与重连。
- 模拟统计报告的逐点序列同真实接口使用 `{t, v, s?}`，时间为微秒整数；通过前端 `normalizeReport` 还原后再渲染，避免旧 `{time, value}` 夹具导致日期与指标显示 NaN。
- e2e 失败先看截图输出在被 git 忽略的 `frontend/test-results/`；先确认跑的是哪套配置（主配置连临时 Python 后端，mock 配置连内存模拟服务）。
- 类型报错疑似与后端契约不一致时，先重跑 `dev_tools.export_api_schema`，再排查是否有人手改了生成产物。

## 20. 相关模块

- [舰队科技列表](fleet-technology.md) —— Lua 目录、累计扫描证据、人工校正和阵营加成。

- [WebUI 总览](index.md)
- [API 服务](api.md) —— 协议、认证与路由，前端所有业务通信的对端
- [配置系统](../config.md) —— args.json、menu.json 与翻译文件的来源
- [外部桥接与开发工具](../infra/submodule-tools.md) —— dev_tools.export_api_schema 所在的开发工具层
