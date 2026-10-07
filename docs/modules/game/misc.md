# 其他游戏功能模块

> 无法各自成篇的中小型游戏功能任务合集：演习、建造、每日任务、困难模式、SOS、作战档案、突袭、活动四件套、私人休息室、船坞、免费福利、小游戏、觉醒、作战委托、META 奖励与装备管理。

## 1. 模块概述

除科研、委托、商店、大世界、岛屿等已有独立文档的玩法外，`module/` 下还有一批中小型游戏功能模块。它们各自实现一个独立玩法的自动化任务，规模不足以单独成篇，但共享同一套骨架：

- **入口统一**：调度器按任务名调用 `alas.py` 上的同名方法，方法延迟导入对应模块并执行 `run()`；
- **组合即能力**：每个任务类按需继承 `UI` / `Combat` / `CampaignRun` / `Equipment` / `Dock` 等基础层，复用导航、战斗、弹窗与装备能力；
- **任务收尾**：日常类任务可用 `task_delay(server_update=True)` 延迟到服务器刷新；需要禁用当前任务时，设置 `Scheduler_Enable=False` 并调用 `task_stop()`。后者抛出 `TaskEnd`，由运行器作为当前任务正常结束处理，调度器仍可继续执行其他任务。

本篇按模块逐一小节说明定位、入口与特殊机制，不展开函数细节；通用战斗、地图与导航逻辑见「相关模块」列出的文档。

## 2. 模块职责

### 负责

- 上述各玩法任务的完整流程：进入页面 → 识别状态 → 执行操作 → 记录进度 → 调度下次运行
- 活动期玩法（突袭、联动、医院、活动关卡与剧情）的活动期自动化
- 装备管理基础能力（`module/equipment`），被演习、困难、宝石收割、自动装备等模块复用

### 不负责

- 通用战役/地图执行框架（`CampaignRun`、`module/map`，见[战役执行](../campaign.md)）
- 商店通用购买框架（`module/shop`，见商店系统；私人休息室商店仅复用其 clerk）
- 退役/强化、大世界、科研、委托、商店等已有独立文档的玩法
- 截图/点击（设备层）与弹窗处理（处理器层）

## 3. 模块位置

```
module/
├── exercise/         # 演习 PvP：exercise.py 主任务，combat/opponent 战斗与对手选择，hp_daemon 血量监控，equipment 装备编辑
├── gacha/            # 建造：gacha_reward.py 主任务，ui.py 建造页导航
├── daily/            # 每日任务：daily.py 单文件（含关卡/舰队映射与执行）
├── hard/             # 困难模式：hard.py 主任务，equipment.py 困难专属装备装卸
├── sos/              # SOS 信号：sos.py 单文件（含各服务器 UI 适配）
├── war_archives/     # 作战档案：war_archives.py 主任务，dictionary.py 活动→模板映射
├── raid/             # 突袭共斗：raid.py 资源映射，run.py 主循环，daily.py 每日难度，scuttle.py 弃船，combat.py 战斗
├── event/            # 活动日常：base.py 基类，campaign_abcd.py（EventA~D），campaign_sp.py，maritime_escort.py
├── eventstory/       # 活动剧情：eventstory.py 单文件
├── event_hospital/   # 医院活动：hospital.py 线索调查，hospital_event.py 突袭战斗，clue.py 线索识别，combat/ui.py
├── coalition/        # 联动活动：coalition.py 主循环，coalition_scuttle.py 沉船，coalition_sp.py，combat/ui.py
├── private_quarters/ # 私人休息室：private_quarters.py 主任务，interact/shop/clerk/status/ui.py
├── shipyard/         # 船坞蓝图：shipyard_reward.py 主任务，ui.py 船坞界面，ui_globals.py 系列定义
├── freebies/         # 免费福利：freebies.py 总调度 + battle_pass/data_key/mail_white/supply_pack 四个子模块
├── minigame/         # 小游戏：minigame.py 代币与循环，new_year_challenge.py 具体游戏
├── awaken/           # 觉醒：awaken.py 单文件
├── handover/         # 作战委托：handover.py 单文件（约 1200 行，含维护时间与一键消耗状态机）
├── meta_reward/      # META 奖励：meta_reward.py（信标/档案/同步三类收取）
└── equipment/        # 装备管理基础库：equipment.py 装备页操作，equipment_change.py 换装，fleet_equipment.py 舰队级，equipment_code.py 配装码
```

每个目录另有 `assets.py`（按钮识别资源，由 `dev_tools.button_extract` 生成，勿手改）。

## 4. 核心入口

调度器通过 `alas.py` 的同名方法进入（均为延迟导入后调用 `run()`）：

| 任务（task.yaml 分组） | alas.py 方法 | 主类 | 主要配置分组 |
| --- | --- | --- | --- |
| Exercise（DailyMission） | `exercise()` | `module.exercise.exercise.Exercise` | `Exercise.*` |
| Gacha（DailyMission） | `gacha()` | `module.gacha.gacha_reward.RewardGacha` | `Gacha.*` |
| Daily（DailyMission） | `daily()` | `module.daily.daily.Daily` | `Daily.*` |
| Hard（DailyMission） | `hard()` | `module.hard.hard.CampaignHard` | `Hard.*` |
| Shipyard（DailyMission） | `shipyard()` | `module.shipyard.shipyard_reward.RewardShipyard` | `Shipyard.*` / `ShipyardDr.*` |
| Freebies（DailyMission） | `freebies()` | `module.freebies.freebies.Freebies` | `BattlePass.*` / `DataKey.*` / `Mail.*` / `SupplyPack.*` |
| Minigame（DailyMission） | `minigame()` | `module.minigame.minigame.Minigame` | 仅 Scheduler |
| PrivateQuarters（DailyMission） | `private_quarters()` | `module.private_quarters.private_quarters.PrivateQuarters` | `PrivateQuarters.*` |
| Awaken（Reward） | `awaken()` | `module.awaken.awaken.Awaken` | `Awaken.*` |
| WarArchives（Event） | `war_archives()` | `module.war_archives.war_archives.CampaignWarArchives` | `WarArchives.*` |
| Raid / RaidScuttle（Event）、RaidDaily（EventDaily） | `raid()` / `raid_scuttle()` / `raid_daily()` | `module.raid.run.RaidRun` / `raid.scuttle.RaidScuttleRun` / `raid.daily.RaidDaily` | `Raid.*` / `RaidDaily.*` |
| Hospital / HospitalEvent（Event） | `hospital()` / `hospital_event()` | `module.event_hospital.hospital.Hospital` / `hospital_event.HospitalEvent` | `Hospital.*` / `HospitalEvent.*` |
| Coalition / CoalitionScuttle（Event）、CoalitionSp（EventDaily） | `coalition()` 等 | `module.coalition.coalition.Coalition` 等 | `Coalition.*` |
| EventA~D（EventDaily） | `event_a()`~`event_d()` | `module.event.campaign_abcd.CampaignABCD` | `EventDaily.*` + `Campaign.*` |
| EventSp（EventDaily） | `event_sp()` | `module.event.campaign_sp.CampaignSP` | `Campaign.*` |
| MaritimeEscort（Event） | `maritime_escort()` | `module.event.maritime_escort.MaritimeEscort` | 仅 Scheduler |
| EventStory（Tool） | `event_story()` | `module.eventstory.eventstory.EventStory` | `EventStory.*`（活动名经 `Event.Campaign.Event` 交叉读取） |
| Sos | `sos()` | `module.sos.sos.CampaignSos` | `Sos.Chapter` |
| OperationHandover（Tool） | `operation_handover()` | `module.handover.handover.OperationHandover` | `OperationHandover.*` + `Campaign.*` |
| （无独立任务，被信标/协助任务内嵌调用） | — | `module.meta_reward.meta_reward.MetaReward` | `OpsiAshBeacon_AutoCollectShip` 等读取方配置 |
| （无独立任务） | — | `module.equipment.*`（基础库，被 Hard/Exercise/GemsFarming/AutoEquip 组合） | — |

## 6. 工作流程（逐模块说明）

以下每节覆盖一个模块的定位、入口与特殊机制；流程均为「导航 → 状态循环 → 收尾调度」的变体，只写差异点。

### 演习（module/exercise）

`Exercise(ExerciseCombat)` 组合对手选择（`OpponentChoose`）、血量监控（`HpDaemon`）与装备编辑（`ExerciseEquipment`）。OCR 识别剩余次数与赛季倒计时（`DatedDuration` 专修 `10d 01:30:30` 格式）。消耗策略：`aggressive` 不保留次数；其余策略保留 5 次，并在剩余时间落入将军试炼区间（`ADMIRAL_TRIAL_HOUR_INTERVAL`）或不足 6 小时时清空保留。对手刷新每日最多 5 次，计数经 `Exercise_OpponentRefreshValue/Record` 跨天持久化；选择策略含 `max_exp / easiest / easiest_else_exp / leftmost`，`easiest_else_exp` 在刷新耗尽后放弃保胜率改为冲经验。

### 建造（module/gacha）

`RewardGacha(GachaUI, Retirement, CampaignStatus)` 执行建造完整流程：清空已有队列收菜 → OCR 金币/魔方/建造券 → 按池（light 600 金 +1 魔方，heavy/special/event/wishing_well 1500 金 +2 魔方）计算可建次数 → 提交订单。活动池优先消耗建造券（`Gacha_UseTicket`），差额按 `Gacha_UseDrill` 与资源上限折算；收菜时新船走快速跳过并交给退役流程。资源计数写入 `LogRes` 统计。

### 每日任务（module/daily）

`Daily(Combat)` 按页签轮询每日出击（`daily_current` 1~7）：检测活跃状态与剩余次数（`OCR_REMAIN`），按 `Daily_*` 配置选择关卡与舰队后进入 `combat()`。限时出现「紧急模块开发」入口时（`ENTRANCE_EMERGENCY_MODULE_DEVELOPMENT`），切换到另一套关卡/舰队映射表。每打完一关就退出重进战役菜单重置页签顺序（顺序会乱，因此任务不能停留在 `page_daily`）；破交作战需 `Daily_UseDailySkip`，否则跳过。

### 困难模式（module/hard）

`CampaignHard(CampaignRun)` 复用普通模式地图：从 `campaign.campaign_main` 导入同名 `MAP`，以 `folder='campaign_hard'` 加载困难战役壳；强制舰队锁定、自动搜索，`Fleet_FleetOrder` 由 `Hard_HardFleet` 推导（另一支舰队待命）。OCR 读取每日剩余次数（0~3）后循环出击；心情按「不计算也不忽略」处理。结束调用 `task_call('Reward')`。困难专属装备装卸由 `HardEquipment` 在编队准备阶段完成。

### SOS 信号（module/sos）

`CampaignSos(CampaignRun, CampaignBase)` 仅 TW 服可用：CN/EN/JP 的 `run()` 首行即禁用调度器（游戏已无 SOS 地图）。该任务已从 WebUI 菜单移除（`task.yaml` 无 Sos 条目），但 `alas.py` 的 `sos()` 入口与 `Sos.Chapter` 配置仍保留，对历史配置中已启用的该任务仍然生效。服务器 UI 差异（章节 OCR 裁剪区、滚动条颜色、EN 无滚动条改拖拽）全部通过 `@Config.when(SERVER=...)` 分支定义。按 `Sos.Chapter`（3~10）定位信号并执行 `campaign_{chapter}_5`，每信号一次，耗尽后延迟到次日。

### 作战档案（module/war_archives）

`CampaignWarArchives(CampaignRun, CampaignBase)` 强制 `USE_DATA_KEY=True`（进档案必须消耗数据密钥），维护每日出击额度（`WarArchives_DailyRunCount/Remain`，跨天重置、配置变更实时折算）。入口靠 `dictionary.py` 的活动名→模板映射在档案列表中滚动查找（`campaign/campaign_war_archives/campaign_base.py` 消费该映射）。自动开荒（`WarArchives_AutoClear`）忽略手填关卡，按进度持久化逐关推进，100% 模式下中间关全清一次即达标、仅末关打满星；`WarArchives_AutoSelectEvent` 再从上到下遍历档案，全部完成后自动关闭任务。`dev_tools/war_archives_update.py` 负责把最新 CN 活动复制为 `war_archives_*` 地图并同步更新 `dictionary.py` 与 `campaign/Readme.md`。

### 突袭共斗（module/raid）

`raid.py` 是资源映射层：`raid_name_shorten()` 把 `raid_YYYYMMDD` 映射到资源前缀（ESSEX…BIGSHOT），`raid_entrance()/raid_ocr()` 按活动+难度取按钮与 OCR 实例（各活动字体颜色不同，部分需 `RaidCounter` 预处理或 `RaidCounterPostMixin` 修正）。`RaidRun.run()` 主循环：检查总次数/油/币/PT 停止条件，无油图标的活动先到战役菜单检查，EX 模式门票为 0 即停，RPG 型活动走 `page_rpg_stage`。`RaidDaily` 用 `RaidDaily_StageFilter` 依次刷 easy/normal/hard，EX 最后执行且先领通关奖励，RPG 型无每日直接禁用。`RaidScuttleRun`（弃船）与联盟沉船一致：整关只扣一次 2 点心情、红脸弹窗直接确认、D 评价不额外扣心情且不算失败。

### 活动日常与 SP（module/event）

`EventBase(CampaignRun)` 统一加载活动地图并强制关闭一次性关卡标记，`convert_stages()` 兼容字符串/列表/Filter 三种输入。`CampaignABCD`（EventA~D 四个任务共用）：扫描 `campaign/{活动}/` 下 `.py` 文件生成关卡列表 → `EventDaily_StageFilter` 过滤排序 → 从 `EventDaily_LastStage` 断点续刷（记录过期则重置）→ 每关 `total=1`，逐关推进，全部完成延迟到次日；关卡名错误时提示应改用 Event 任务解锁。`CampaignSP` 检查 `sp.py` 存在后执行 1 次，成功与否都延迟到次日。`MaritimeEscort`（海上护卫）进图即撤退，以低消耗拿约 70% 奖励，OCR 剩余次数为 0 则延迟到次日。

### 活动剧情（module/eventstory）

`EventStory(CampaignUI, Combat, LoginHandler)` 循环推进活动剧情：导航至剧情入口（个别活动在 `page_sp` 或有专属弹窗按钮），按首段/末段/中段/战斗中段按钮推进；遇到剧情内战斗时直接重启游戏跳过（比打完快）。完成后回主界面再进入一次以清掉残留奖励弹窗。个别活动经特判跳过（如剧情入口在小游戏内的活动）。

### 医院活动（module/event_hospital）

`Hospital(HospitalClue, HospitalCombat)`：先领每日奖励（红点检测），再进入线索系统在「地点/角色」两个标签页遍历旁白（`HospitalClue` 用颜色过滤+轮廓检测把线索列表转成按钮），每个旁白执行调查战斗；`OilExhausted` 时退出线索并延迟 2~4 小时。`HospitalEvent(Hospital, RaidRun)` 复用突袭运行框架执行活动战斗关：固定 `raid_name='raid_20250327'`，难度开关 easy/normal/hard，关闭推荐编队，停止条件与 RaidRun 一致。

### 联动活动（module/coalition）

`Coalition(CoalitionCombat, CampaignEvent)` 主循环：`Coalition_Mode`（含 tc1/tc2/tc3 与 easy/normal/hard 的双向兼容转换）与 `Coalition_Fleet` 决定目标，`coalition_ensure_mode()` 切换 story/battle，按 PT 与金币停止条件刷战斗；无油图标的活动先到战役菜单检查停止条件。`CoalitionSP` 只跑一次 SP 后延迟到次日。`CoalitionScuttleRun`（沉船）重写心情预估（整关只扣一次）、红脸弹窗确认与结算按钮识别，不因舰船被击沉而停止。

### 私人休息室（module/private_quarters）

`PrivateQuarters(PQInteract, PQShop)`：进入宿舍菜单 → 私人休息室；按配置购买每周玫瑰（金币）与蛋糕（钻石，商店复用 `module/shop` 的 `ShopClerk` 框架），再检查每日互动剩余次数（OCR），进入目标舰娘房间执行对话与触摸互动。`available_targets` 定义 7 位可用舰娘及其所在场景；`not_supported_filter` 声明服务器差异（JP 缺纳希莫夫，TW 缺大凤与纳希莫夫），TW 服无商店。

### 船坞蓝图（module/shipyard）

`RewardShipyard(ShipyardUI)` 在主界面 OCR 金币后进入船坞，按价格阶梯（PR 最高 1500/张、DR 最高 6000/张，前两张免费）计算可购数量，购买并使用 PR/DR 目标舰船的蓝图；多余蓝图直接使用。每日防重复执行基于服务器刷新时间；配置分 `Shipyard.*`（PR）与 `ShipyardDr.*`（DR），舰船定位依赖 `ui_globals.py` 的系列/舰船表。

### 免费福利（module/freebies）

`Freebies(ModuleBase)` 总调度按序执行四个子模块，各自检查开关后收尾统一延迟到服务器刷新：

- **BattlePass**：`page_reward` 红点进入，循环领取（含 META 舰船锁定确认弹窗）。
- **DataKey**：在作战档案页收集数据钥匙，背包满时除非 `DataKey_ForceCollect` 否则跳过。
- **MailWhite**：必须在白色主题主页进入邮件，按 `Mail_ClaimMerit/ClaimMaintenance/ClaimTradeLicense` 分三轮领取并按 `Mail_DeleteCollected` 清理。
- **SupplyPack**：石油低于 21000 时按 `SupplyPack_DayOfWeek` 在指定服务器日购买免费周补给包。

### 小游戏（module/minigame）

`Minigame(UI)` 导航学院 → 游戏室，OCR 代币（上限 40，≤30 触发收集），代币 >0 时游玩（单次任务最多 10 局）。`MinigameRun` 是模板方法基类：子类实现 `choose_game/use_coin/play_game/exit_game/deal_specific_popup`。当前唯一实现 `NewYearChallenge` 用颜色匹配点按钮、OCR 读取得分与代币消耗。JP 服代币字体颜色不同（模块级按服务器分支定义）。

### 作战委托（module/handover）

`OperationHandover(CampaignRun)` 在主线关卡页启动「作战委托」：消耗石油与作战全权委托书，让舰队离线自动执行主线关卡，委托结束后再领掉落。委托期间出击类任务（主线、活动）会失败、只有大世界照常可用，因此它是调度优先级末尾的兜底任务。要点：

- **维护感知**：`MaintainOverride` 开启时先按当前服务器查停服公告；维护就在今天则整体切维护模式——当天 0 点起忽略次数与一键消耗，把下次运行排到维护前 10 分钟，到点用「次数拉满」跑最后一次。
- **低频等待**：次数为 0 时尽量不进游戏——上次委托未结束就按剩余时间推迟；已完成才进游戏领奖；没有委托就按一键消耗定时或维护检查排期；两者都没开则 `Scheduler_Enable=False` 关闭任务。
- **开委托流程**：目标关卡弹「作战委托 INFORM」→ 在进行中则按剩余时间推迟 / 已完成则领奖（连收结算、紧急委托、新船演出弹窗）→ 记录石油（低于 `OilLimit` 推迟）→ 面板定次数（维护前拉满 > 一键消耗按委托书数量 > 配置次数）→ 剩余时间不足且启用时用委托书兑换时间（1 本 = 1 小时）→ 石油够才点开始。
- **一键消耗的放弃语义**：委托书不足、石油不够这类当天等不来的原因直接放弃本周并记录（`handover_week_key` 周记录），界面操作失败才间隔重试。相关状态测试见 `tests/test_handover_*`。

### META 奖励（module/meta_reward）

`MetaReward(BeaconReward, DossierReward)` 收取 META 系统三类奖励，**没有独立调度任务**，由 [大世界辅助模块](../os/auxiliary.md)的信标链路内嵌调用（`OpsiAshBeacon.run` 尾部与 `AshBeaconAssist.run`）：

- **BeaconReward**（继承 `Combat, UI`）：META 页收取同步奖励（点数满 100% 获取 META 舰船，含新舰锁定确认）与信标奖励红点。
- **DossierReward**（继承 `Combat, UI`）：经 `DOSSIER_LIST` 进入旧档案页，收取已完成档案的遗留奖励。
- `run(category="beacon"/"dossier")` 统一入口。TW 不支持（与档案玩法同源的服务器边界）。

### 觉醒（module/awaken）

`Awaken(Dock)` 遍历船坞可觉醒舰船（`ShipLevel` OCR 只接受 100~125），进入详情后先判资源：金币/芯片/阵列按钮下方有无红字，且 `COST_ARRAY` 缺位时另两按钮右移 54px，需两侧验证一致才认为结果有效。`level125` 先用心智阵列（觉醒+）再用芯片，`level120` 只用芯片；单舰循环觉醒至上限或资源不足，全部耗尽后结束。结束重置收藏与筛选器，延迟到次日。

### 装备管理（module/equipment，基础库）

无独立调度任务，是被组合的能力层：

- `Equipment(EquipmentCodeHandler)`：装备页导航、舰船视图滑动、装备中过滤器、按预设记录穿/卸装备（记录以 `'9'` 分隔各舰索引）。
- `EquipmentChange`：经强化页记录舰船当前装备图标，再逐槽换装（演习、困难各自派生 `ExerciseEquipment`/`HardEquipment` 适配入口按钮）。
- `FleetEquipment`：在编队页按舰队批量穿/卸，被 `FleetSelectionMixin`（主线、宝石收割等）使用。
- `EquipmentCodeHandler`：读取/应用游戏内 Base64 配装码，经模拟器剪贴板交换，仅支持 uiautomator2 系控制方式；可导出到配置，宝石收割（`GemsEquipmentHandler`）据此在换装舰娘时保住原装备。

另有独立 Tool 任务 `AutoEquip`（`module/auto_equip`），基于 `Dock` 在船坞界面通过快速换装为全舰船批量装配仓库中的装备，实现不依赖本库换装流程的另一条链路。

### 指挥喵评分与已有猫建议锁定（工具 MeowfficerScore）

入口为「工具 → 指挥喵评分」，由 `run_meowfficer_score()` 调用 `MeowfficerScore.run()`；来源为本地截图、设备跟拍或猫窝自动遍历，报告输出 Markdown、HTML 与 JSON。每只评分完成后立即更新累计报告，启用建议锁定时在锁状态核验后发布；扫描中断保留已发布前缀，不重复评分。自动扫描开始先发布本次零进度，`scannedCount` 包含跳过评分的蓝猫，评分卡仍只包含实际评分结果。三份产物均原子替换，JSON 最后发布，读取不会遇到半写文件；逐只发布不重复记录导出路径日志，写入失败告警后继续游戏流程。新旧主题共用面板在页面可见时约每秒串行读取最新报告，保留展开选择及已有内容；独立 HTML 文件同步生成，已打开的静态 HTML 页面通过刷新获取最新内容。训练领取新猫的锁定规则独立维护在[日常维护模块](daily-maintenance.md#指挥喵)，不由本工具开关替代。

`MeowfficerScore.MeowfficerScore.LockByAdvice` 默认关闭，只在 `Source=scan` 时改变已有猫的锁定状态。扫描器在每只猫当前天赋页内完成识别、评分与锁状态处理：金、紫猫完整识别后按最终培养建议操作，`feed` 解锁，`pending`／`reroll`／`keep` 锁定；正向确认蓝猫后不评分，设置未锁定，包括解除已有锁。天赋不完整或猫种未知时，仅在当前猫、页面和锁状态确认后保护锁定；无法确认这些前置条件时不操作，不能用不完整结果解锁。点击后继续截图确认锁状态，操作结果及跳过原因写入报告。

`score_lock.py` 单次最多点击一次锁切换。点击前页面或当前猫身份未知时，返回 `skipped`，不操作；原猫已符合目标锁状态时返回 `unchanged`，无需点击。点击后出现临时页面或身份不匹配时，只持续截图等待，不在其他猫上点击、不返回猫窝、不补手势或再次切换锁。只有原天赋页、原身份与目标锁状态连续两帧确认后，才返回 `changed`，允许直接继续下一只；任何页面／身份失配、锁状态未知或相反状态都重置连帧计数。

锁状态等待沿用 `Timer(8, count=12)`，实际点击后仅重置一次，后续过渡不延长计时。该计时器须同时达到时间与检查访问次数条件才触发，不是 8 秒或 12 帧的单独硬上限。达限仍未确认原猫目标状态时返回 `unconfirmed`，由上层停止扫描，保存部分报告与已有错误现场；设备异常仍直接上抛。画面短暂差异只视为待核验过渡，不据此推定游戏必定跳到另一只猫。

完整性由 `scan_capture.py` 与 `scan_coverage.py` 共同确认。先持续截图核验当前页面与猫身份；向顶部滑动一次后的稳定画面符合 `scan_scroll.py` 中已校准的 CN 五槽顶部布局时，即确认到顶，不再重复滑动确认。未匹配该布局的画面沿用原有有界滚动核验，手势未到位时继续补滑；手势长度保持原值。从第 1 行到首个完整「未习得」栏连续读全后，即可确认全部已学天赋，直接评分并继续下一只，不再为后续空槽滑到底部。未习得栏须有完整行框，两路 OCR 均精确读出「未习得」且置信度在 0.9～1 之间，并同时确认浅青空图标与空白正文，不参与评分。若已学天赋需要滚动读取，在核验实际位移、连续补全前面各行并确认首个完整空栏后，也可提前结束。终点之后的正常边缘半行不必再滑动补全；空栏前缺行或未知、已有永久矛盾，或已观察到空栏后还有已学天赋时，不采用这一完成条件。

当前 CN 客户端的[天赋槽数常量](https://github.com/AzurLaneTools/AzurLaneLuaScripts/blob/main/CN/model/const/commanderconst.lua)为 `MAX_TELENT_COUNT = 5`，[天赋页 `UpdateTalents`](https://github.com/AzurLaneTools/AzurLaneLuaScripts/blob/main/CN/view/commander/view/pages/commandercattalentpage.lua)按该常数调用 `uilist:align`，固定显示五个槽位。因此，已确认校准顶部后，将真实滚动位移映射到第 1～5 槽，五槽连续完整且无矛盾、未观察到第 6 槽、末槽之后没有未对应行框的可见内容时，可直接结束读取，省去到达底部后的两次重复确认手势。该完成条件证明全部槽位已读全，不依赖末框完整或白色尾部推断物理底部；仅适用于已校准的 CN 布局，其他布局和服务器保留原流程。

没有可靠空栏终点，也不满足已知五槽全部覆盖时，继续核验物理底部与首行到末行的完整连续覆盖，边缘半行须在相邻画面中完整读取；不能按 OCR 名字数量或「没有新天赋」推断到底。每次滚动位移由真实图像重叠测量；效果说明横向滚动、图标闪光影响整区域匹配时，以独立行框和至少两个非空完整标题补证唯一位移。所有重叠完整标题须满足原像素差阈值和严格字形相似度，空标题不提供位移证据；同一静态证据也用于确认页面稳定和列表到顶、到底，不把正文动画误当作纵向移动。漏行、未知位移、重复天赋线、同一行跨帧题名或等级矛盾、底部未对应行框的内容仍保护锁定；同一位置的暂时识别失败可由后续完整帧补全，明确矛盾不能被补读覆盖。

各完成路径均保留结束时的当前猫身份复核。金紫猫的 `ScanCapture.talents_complete` 单独表示已学天赋全集已确认，允许原始猫种未知的自定义姓名参与切换比对；自动解锁仍需包含猫种确认的 `complete`，不因此放宽评分与锁状态保护。

设备截图保持 RGB，进入现有 OCR 预处理时显式转为 BGR，几何与位移比较仍使用原截图。品质需精确、高置信度确认；原始或增强读数之一偏低时，只有另一个已精确确认且两次颜色字形分割补读也一致才接受，不降低置信度门槛、不按猫种推断品质。天赋名按已知名称精确读取；国服「飓风之眼」可由完整四字的严格图像模板补充确认，限制位置、唯一命中及无额外文字，不将「风之眼」等错字映射为已知天赋。

完整行内的标题被 OCR 分成两个横向连续、垂直范围相近的未知碎片时，`scan_title.py` 可从原始完整标题像素提取两种深色字形，再独立识别整个名称。两个新结果都须是置信度至少 0.9 的精确已知标题，并与原变体已可靠确认的标题一致；碎片不拼接、不参与名称或置信度合并。无效坐标、缺失置信度、模型失败、两个完整已知标题或已知名称矛盾不能通过该补读放行。原分框结果与新增读数一并保留在行诊断证据中。

原图已经以高置信度精确读出已知标题，而增强图以高置信度读出未知误字时，`padded_title_variants()` 也可补证：原始完整标题区域的深色笔画必须不碰四边，再补 8 像素白边，分别按 160、185 阈值提取两份完整字形。两次新增 OCR 都须精确命中原图的同一已知标题，置信度均在 0.9～1 之间；原始误字和补读证据一并保留，不设别名、不降低阈值。已知标题冲突、非法读数、模型失败或原字形碰边仍拒绝接受。可靠补全的行按原有连续覆盖规则参与首个完整「未习得」栏之前的已学前缀确认，不因一次增强误字重复滑动；其他未完整行、空栏核验与身份复核仍按原要求处理。

`scan_retry.py` 仅对完整行框内尚未可靠读出的标题补取最多两次稳定新帧。补读前确认同一只猫、天赋页、原列表位置及静态标题未变化，每次仍要求原有的两路高置信精确识别；旧的已知矛盾永久保留。完整帧与正常边缘半行不触发补读，不增加滑动或返回猫窝。顶部已确认时，以实际首框定位第 1 行；首框缺损仍保护，不把后续行改编号为首行。

正常读取结束仍不完整时，`scan_diagnostics.py` 在 `log/meowfficer_diagnostics/<时间_UUID>/` 保存本次顶部与最后读取帧的天赋面板、行框标注图和 `evidence.json`。图片只包含 500×436 天赋列表，JSON 记录各次初读、滚动与补读的行框、已读标题、实际 OCR 原文和置信度、完整标记、偏移和保护原因，并指明图片对应的帧。无法核验位移的失败画面也单独保存，偏移为 `null`，不加入覆盖或评分；不保存整屏或账号区域，不重新操作游戏。JSON 最后原子发布，普通保存失败仅告警，原保护结果与控制异常处理不变。

猫种按显示名与已知原名精确匹配推定，允许明确的「限定＋原名」前缀，不使用模糊匹配，也没有独立立绘或技能鉴定；无法确定原始猫种的自定义名保护锁定。切换前先记录待核验操作；锁状态等待达限仍无法确认时请求人工检查，设备异常继续上抛，两种情况都先保存已有部分报告，避免再次盲目切换。

国服扫描由 `scan_continuous.py` 与 `scan_roster.py` 组织：先在猫窝确认列表顶部、拥有数与容量，选中首猫并核验选择环及稳定身份后进入天赋页。后续直接在左侧立绘向左滑动，持续截图确认当前页面与实际猫身份后读取，不预读每屏 12 格，也不在同名、锁状态改变或跨屏时返回猫窝。启用建议锁定时，入口必须正向确认「等级」排序；该排序下锁状态改变不重排的行为已经核实。拥有数、页面、所需身份或排序无法确认时停止保护，不通过返回猫窝恢复继续扫描。

CN 达到入口确认的实际拥有数或 `ScanLimit` 上限后结束，进入天赋页后完成或停止均保留当前页。拥有数为零时留在猫窝，入口核验失败也不继续进入。`ScanLimit=0` 表示按实际拥有数扫描，`ScanPasses` 不限制国服数量；EN／JP／TW 保留原猫窝卡片遍历与 `ScanPasses` 翻屏上限。

当前猫姓名只从左下角姓名框 OCR，等级独立从经验条 OCR；同一行的所属舰队名和下方属性数值不进入候选。宽资料区域仅用于画面稳定性比较，不用文本长度从中推断猫名。同名同级时，先核验天赋全集；完整天赋签名不同即可确认切换，不额外读取属性。完整天赋相同时要求双方品质明确，品质不同也可确认切换；天赋与品质均相同才独立核验后勤、指挥、战术三项静态属性。不完整天赋的子集差异与未知品质都不能作为切换证据。蓝猫按既定策略跳过天赋读取与评分，同名同级同品质时仍须比较三项属性。所需身份证据未知时停止，不靠缺失字段推断切换成功。

静态属性正常双路读清时不增加 OCR。原始两路至少一路高置信度确认纯数字，另一路为空或同值但偏低时，可从完整数字区域提取两种灰色字形；两次新增读数均须高置信度且与原数字一致。原始数值冲突、无效置信度、多框、模型失败、非数字、彩色文字或裁边不能由补证覆盖；失败日志记录具体属性字段、数字置信度和拒绝原因，结果仍为未知并触发原停止保护。

按用户确认的重复策略，左滑后姓名、等级、完整天赋和三项静态属性均与上一只相同，连续第 1～4 次仍作为下一只记录并执行当前猫回调；第 5 次相同则停止，不记录该次结果、不执行该次回调，并保存已有报告、保留天赋页。任一核验字段明确变化后重置连续重复计数。该策略允许真正相同的猫连续通过，也可能在连续相同猫超过四只时提前停止；若手势未生效，前四次可能重复记录同一只。这是用户选择的遍历边界，不将第五次相同直接判为已到最后一只。

评分工具请求人工接管时，日志记录具体原因；设备已有有效缓存截图时，同时保存到 `log/error/meowfficer_score/<时间及唯一后缀>/screen.png` 和 `reason.txt`。现场目录用普通创建模式继承父目录权限，避免 Windows 上临时目录的仅所有者权限导致保存后无法读取；同一时间的现场以 UUID 区分，目录重名只换新名字，不能覆盖原件。保存使用停止时已有的画面，不重新截图或操作游戏；保存失败单独告警，不覆盖原失败原因，也不吞掉其他设备异常。

同名猫逐条记录，不按姓名或天赋内容去重。国服以确认的拥有数和扫描上限控制结束，异常时先保存已有部分报告；不能仅由左滑身份未变推断已到末尾。锁操作仍只依据当前页面刚读取的天赋，不能用报告中的名字回查另一只猫。显示名也可修改为另一种猫的原名，名称匹配不能代替真实猫种鉴定，这类改名应人工核对评分口径。

WebUI 评分面板同时显示逐猫锁定记录：当前猫、原状态、截图确认后的状态、处理结果和原因，同名猫逐条保留。报告只有蓝猫或识别失败项、没有评分时，仍显示处理记录及完整报告入口。`meowfficer.scoreReport` 仅在新报告具有 `lockActions` 时返回该字段，并按查询数量限制最新有效条目；老报告响应不增加字段，接口读取不会改写报告。

新版和旧版界面的评分报告默认收起猫评分卡与锁定记录，标题、数量、生成时间、刷新、查看完整报告和清空报告仍可使用，避免长报告把下方日志与运行入口推远。点击“展开报告”查看详情，再次收起不会重置卡片中其他评分口径的展开状态；当前页面的手动刷新与自动轮询保留报告的收起／展开选择，重新进入页面时默认收起。

扫描在正向确认当前猫的天赋页与身份后开始新的读取阶段，按已接受的切换结果清理已完成阶段的 `MEOWFFICER_NEXT` 与 `SWIPE` 记录；识别未知或达到连续重复停止条件时不清理保护。进入下一只的读取阶段时处理此前的滑动历史，避免合法的跨猫滑动累积触发重复操作保护；同一只猫的读取期间不清理滑动记录，保留有限滑动次数、页面与身份校验及设备异常上抛，其他按钮记录与全局保护阈值保持原有规则。

本地截图与设备跟拍保持只读，不受此开关影响。扫描锁定当前只适配经截图校准的国服中文客户端，EN／JP／TW 缺少对应锁状态资源，跳过锁定操作并说明原因。该功能不自动喂猫、补点或洗点，解锁后是否被既有强化流程用作材料仍由训练任务及其材料条件决定。

## 16. 修改注意事项

- **assets.py 一律生成**：各目录 `assets.py` 由 `uv run -m dev_tools.button_extract` 从截图提取，手改会被覆盖；新活动按钮先补对应服务器的截图资源。
- **服务器差异有两套写法**：`@Config.when(SERVER=...)`（SOS、装备更换等按服务器生成属性）与 `module.config.server` 的模块级 if 分支（minigame、private_quarters.status、daily 的 OCR 颜色）。新增适配时沿用所在模块的既有写法，避免混用。
- **活动映射是手工维护点**：`raid.raid_name_shorten`、`war_archives.dictionary.dic_archives_template`、`eventstory` 的特判活动列表、`raid_ocr` 的各活动 OCR 参数。新活动要同时补 assets、映射与（必要时）campaign 地图；作战档案优先用 `dev_tools.war_archives_update` 生成而不是手写。
- **沉船链路重写了战斗结算**：`RaidScuttleRun`、`CoalitionScuttleRun` 覆写心情扣减、红脸弹窗与 D 评价格子。修改 `module/combat` 结算或心情逻辑时，必须回归验证这两条链路。
- **收尾语义不要混用**：日常类任务用 `task_delay(server_update=True)`；一次性/耗尽类用 `Scheduler_Enable=False`（必要时配合 `task_stop()`）；进度型状态（`EventDaily_LastStage`、`WarArchives_DailyRunCountRemain`、演习刷新计数）依赖配置持久化，不要在循环中无保护地直写。
- **每日任务顺序依赖退出重进**：`Daily` 与 `EventStory` 等模块对「打完一关后界面顺序会乱」做了退出重进处理，改动导航逻辑时保持该行为。
- **OCR 修正绑定当前字体**：`DatedDuration`、`RaidCounterPostMixin`、`OcrDataKey` 等后处理针对当前游戏 UI 字体，游戏改版后先拿旧截图离线验证。
- **作战委托是优先级末位的排他任务**：委托期间主线/活动出击必然失败。给它改时间预算（委托时长、兑换）时，确认 `handover_idle_time` 的维护分支仍把维护日整段让路。
- **META 奖励没有独立任务入口**：改它的页面流程要同时回归 `OpsiAshBeacon` 与 `AshBeaconAssist` 两条调用链（收尾 `ui_goto_main` 的约束见大世界辅助模块）。
- **装备码依赖剪贴板**：需 uiautomator2 系控制方式（`equipment_code_supported()`），换控制方式前确认该能力是否被下游（宝石收割）依赖。

## 20. 相关模块

- [日常维护模块合集](daily-maintenance.md) —— 委托、科研、宿舍等同类的日常收菜任务
- [退役与装备](retire-equipment.md) —— 退役/强化流程，建造收菜与演习装备依赖它
- [岛屿系统](island.md) —— 同期的另一组游戏功能任务
- [战役执行](../campaign.md) —— `CampaignRun` 与活动地图加载，活动/档案/困难模块的共同底座
- [战斗系统](../combat.md) —— 演习、每日、突袭、联动战斗循环的公共实现
- [大世界辅助模块](../os/auxiliary.md) —— META 奖励的调用方（信标攻击/协助任务）
- [处理器层](../handler.md) —— 各模块依赖的弹窗、登录与信息栏处理
