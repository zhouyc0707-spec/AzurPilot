# 2026-11-02 上游合入：定制保留独立审计

审计工作树：`D:\Codex\workspace\AzurPilot-upstream-dev-20261102`。本审计只读产品代码，未实例化真实配置、启动游戏或访问交易接口。

比较基准：上次上游 `fdfda0227df85569dbb1c10acbcb5e24bf9bbc40`；本地已交付 `5d29ac239a1518d8b242d2f7ffa347dabdca539a`；本次上游 `1fd87b80ad84d7a70110ed149610eb5a143d15fa`。

## 路径及实体证据

- 本地相对上次上游存在 1084 个差异路径；本次上游改变 76 个路径；交集 30 个。具体交集见 `preservation-audit.json`。
- 首次自动合并时，不重叠的 1054 个本地路径全部逐文件保留。最终重跑后其中 1048 个逐文件相同；6 个有意适配为大世界两份说明、`shop.py` 中29→30海域注释、定制清单、`InstancePageActivities.tsx` 新交易入口缓存边界及其 E2E。`shop.py` 完整 AST 保持一致，其余代码变化仅在已批准的页面入口融合范围内。新建合入报告不属于原有1084个差异路径，不计入这项保留计数。
- Python 本地定制实体按函数、类签名及赋值分别比对，最终共 7568 个，7559 个 AST 完全一致。9 个差异实体均在以下语义融合范围内，没有发现未解释的本地实体消失。
- 前轮 `5d29ac239` 五项故障修复涉及 7 个产品路径，均逐文件保留：岛屿信息弹窗、奖励页签优先级、国服大世界重置舰队识别模板、牧场从地图到磨坊的恢复路径、收益账本数量有效性门控。资源模板也保留，未只检查 Python。

## 已核对的融合

| 位置 | 本地目的 | 上游目的 | 审计判断 |
| --- | --- | --- | --- |
| `module/api/app.py` | 保留本地运行初始化、普通总库和旧界面 API | 茗交所切换新存储触发条件 | 除旧 `bindings.json` 改为新 `stocks-v2.json` 入口判断外，本地初始化保留 |
| `module/api/router.py` | 单次执行、停止、旧统计、模拟器状态和岛屿挂起等本地入口 | `stock.request` 改为 `stock.register`，删除旧交易会话 | 本地注册方法均在；交易迁移单列决策 |
| `module/api/socket.py` | 保留本地 WebSocket 调用体系和订阅 | 更新注册限流方法名 | 仅茗交所方法替换，本地订阅保留 |
| `module/campaign/os_run.py` | 月末购买优先判断、独立任务恢复 | 明确锁区仅延期当前商店或防溢出任务 | 月末判断仍在初始化/智能让位之前；新增特定异常处理未捕获其他导航错误 |
| `module/os/globe_operation.py` | 实际进入地图才复位明石去重 | 地图双帧压制数和明确锁区异常 | 原明石 `click_count > 0` 复位条件保留，新增逻辑共存 |
| `module/os/tasks/smart_explore.py` | 用户执行一次脱离智能让位；月末商店不受旧数字限日限制 | 30 个压制海域后放行、首轮增加24号海域、明确锁区延期 | 单次执行仍最先返回。删除旧辅助函数是上游重构；月末行为仍由 `opsi_shop` 前置判断和 `OpsiShop.os_shop` 中月末模式分支承担 |
| 行动力总量测试 | 低石油不记账，本地 Total 恒含箱子 | 验证共享 OCR 的有效标记被覆盖问题 | 测试夹具改为共享 OCR 并保留本地断言，无直接整块择边 |

## 上游变更完整性反向审计

额外逐项检查本轮全部76个上游路径，避免只证明“本地代码还在”而遗漏上游修复。上游新增6项、删除30项、修改40项；最终50项与上游完全一致（包括全部30个删除路径），26项有合理融合差异。没有上游应存在而缺失的文件，也没有上游应删除却遗留的文件。

上游新增的注册页E2E、`StockExchangeBoundary`、锁区截图夹具、行动力空箱测试、交易v2集成测试5个文件均与上游一致；新增行动力说明也保留全文，前面仅增加“原验证记录属于上游环境”的来源说明。没有把上游实机记录冒充本机验收。

26个适配路径的解释如下：

| 分类 | 路径 | 与上游不同的理由及保留证据 |
| --- | --- | --- |
| 文档7项 | `docs/modules/infra/deploy.md`；`docs/modules/webui/api.md`、`frontend.md`、`runtime.md`、`stock-exchange-action-points.md`；`frontend/API.md`、`README.md` | 合入v2协议、远端删股、配套部署和来源说明，同时保留本地总库、旧界面、交易迁移全树预检/原件备份说明；协议告知版本、reconcile能力门控、旧bindings保留等内容可在新文档定位 |
| 前端7项 | `frontend/mock/state.mjs`；`frontend/src/api/contract.json`、`generated.ts`、`types.ts`；`frontend/src/app/App.tsx`、`main.tsx`；`frontend/src/pages/StockExchange.tsx` | 保留喵语、单次执行、旧统计等本地API与mock，适配已批准的注册页缓存；上游本轮变更的 `stock.register` / `stock.rebuild` 契约逐对象一致，旧 `stock.request` 已删除。注册页返回的整段 JSX 与上游逐字一致，差异只在生命周期/串行刷新状态保留 |
| Python9项 | `module/api/app.py`、`protocol.py`、`router.py`、`socket.py`、`stock_exchange_identity.py`；`module/campaign/os_run.py`；`module/os/globe_operation.py`、`tasks/smart_explore.py`；`module/os_handler/action_point.py` | 保留本地API、月末商店、单次执行、明石去重、油换行动力逻辑；身份模块仅导入排序与上游不同。上游124个新增或变更Python实体中118个AST完全一致，其余6个恰为上表已人工核对的入口融合实体，无未解释改动 |
| 测试3项 | `tests/test_opsi_explore_cleanup.py`、`test_opsi_smart_explore_task_delay.py`、`test_stock_exchange.py` | 保留本地逐队清理、上游扫描居中边界和月末/单次执行断言；补充旧绑定、旧身份及已上传历史在v2状态发现和摘要修复后的保留用例。上游新增/变更的测试实体全部存在，新增本地测试没有替换它们 |

上游删除的 Python 实体也逐项检查，未残留旧 `StockRequestParams`、旧交易代理或旧智能让位辅助函数。数据/协议升级实现、锁区新异常与延期门控、行动力共享OCR修复及完整画面空箱确认均完整进入结果。机器清单见 `upstream-inclusion-audit.json`。

## 行动力实读边界

上游新增 `_action_point_inventory_observed` 允许完整面板中确认为空的行动力箱格按零参与统计。它没有直接把 OCR 失败当零：必须石油有效且大于 100、当前图像可用、使用/取消按钮均可见、箱子恰好三格；每个未识别格还必须数量为零、预处理后的数量区非空且完全没有可见字形。当前行动力 OCR 自身仍需有效。

严格库存标记 `_ap_inventory_valid` 与观察统计标记 `_ap_observation_inventory_valid` 分开。购买决策仍使用严格库存标记；统计观察允许有正向空格证据的零值。原油换行动力的每周上限、跨周核验、月末封锁周、零剩余双帧确认、确认耗尽后跨任务缓存均保留。本地 `resource_state.py` 逐字未变，未借旧箱缓存补本次 Total。

## 扫描与普通存储

`preservation-targeted.py` 的离线断言全部通过：

- `module/map/camera.py` 整文件与新上游一致。
- `OSMap.map_rescan_current`、`map_rescan_once`、`map_rescan` 三个函数 AST 与新上游一致。
- 用户已撤回的扫描专用严格位移/边缘确认没有恢复；周边侵蚀一预检、事件续查和明石去重定制继续存在。
- 总库 `database.py`、迁移 `migration.py`、CL1 数据库、大世界掉落统计、日报存储、资源账本、旧统计服务、资源缓存、旧统计页及体力图等18个重点路径与本地基线完全一致，包含 `Overview.tsx`、`theme.ts`、`LegacyRail.tsx` 和5份旧版样式文件。普通统计仍使用原总库和缓存路径，不因交易重构改变历史存储；旧主题名称、主题切换规则及旧日志/统计 Activity 原样保留。

## 必须单列的行为边界

茗交所上游从本地完整交易终端改为注册/绑定入口和外部网站。三处本地差异分别是 `frontend/src/stock/App.tsx` 的滚动恢复、`Charts.tsx` 的缩放保留、`useStockDetail.ts` 的行情/ETag缓存。外站内部页面的这些状态无法由本地 React Activity 直接控制，因此已经向根代理报告为强行为冲突。用户已选择方案1：采用上游独立交易站，旧内嵌行情、筛选和缩放行为按该明确授权退役；保留本地总览和注册页往返缓存。该项属于用户决策后的边界变更，不能描述为原交易终端无损保留。

普通旧版总览日志/统计缓存与交易内部缓存应区分：`StatisticsLegacy.tsx`、`LegacyApChart.tsx`、旧日志/统计实现均保留。最终 `InstancePageActivities.tsx` 已改为导入新 `components/StockExchangeBoundary`，并移到普通外壳的内容层：总览和注册页各有独立 main 与 Activity；按实例设置 key，切实例或返回登录时销毁缓存；首次访问才挂载注册页。注册页重新可见时只静默刷新，同实例输入、确认和已加载内容保留；隐藏清理15秒轮询和事件订阅，读请求串行合并，使用活动状态及实例核验阻止隐藏页结果更新。没有发现旧 stock 目录悬空引用或本地旧页实现丢失。相关行为另由前端代理的浏览器测试验证，本独立审计不冒领其执行记录。

## 智能路线断点兼容限制

代码确认旧状态仅记录三个数字 `next`，`_get_smart_explore_state` 只按月份重置，无路线版本或节点ID迁移。本次第1轮长度由30变31，在索引2插入24；第3轮由38变37，删除旧索引7的24。旧第1轮游标大于2会重复一个节点并可能漏走前移的24；旧第3轮游标大于等于8会把后续节点误当已走，例如旧 `next=8` 下一海域为12，新路线同索引为11。

根代理已只读核对实际 `alas` 实例：智能开荒关闭、`SmartExplore` 状态为空，每月开荒启用且进度100%；故本实例没有待迁移的智能断点，不受该兼容问题影响。该事实来自根代理本轮读取，不来自记忆。保留为其他实例/未来手工导入旧断点的上游限制；未凭假设新增路线迁移功能或改真实配置。

## 证据文件与限制

- `preservation-audit.py` / `preservation-audit.json`：路径与 Python 实体机器比对。
- `preservation-targeted.py` / `preservation-targeted.json`：关键扫描、普通存储和旧路线重解释的离线断言。
- `upstream-inclusion-audit.py` / `upstream-inclusion-audit.json`：全部76个上游路径、124个变动Python实体、协议契约和注册页展示的反向完整性证据。
- 本报告已在前端生产代码融合完成后重跑，机器断言通过，未发现需要阻止本轮交付的未处理强冲突。代码相同或 AST 相同能证明此次合并没有覆盖相关实现，不能替代真实游戏运行效果。没有宣称真实游戏或外站交易验收；最终提交、推送和运行目录状态由根代理在交付报告中核验。
