/**
 * @fileoverview 后端 WebSocket API 接口与数据模型类型定义。
 */

import type {Catalog, ProgramSaved, ProgramSimulation, ProgramValidation, RuntimeProgramState} from '../scheduler/types'
import type {MindCalculation, MindCatalog, MindReport, MindShip} from '../mind/types'

export type Scalar = string | number | boolean | null
export type Value = Scalar | Value[] | {[key: string]: Value}
type Values = Record<string, Record<string, Record<string, Value>>>
export type Status = 'running' | 'stopped' | 'error' | 'updating'
export interface Instance { name: string; status: Status; serial: string; server: string; region?: 'cn' | 'en' | 'jp' | 'tw' | null; currentTask?: string | null }
export interface UpdateStatus {
  state: string; localHead: string | null; upstreamHead: string | null; branch: string
  ahead: number; behind: number; available: boolean; busy: boolean; canApply: boolean; canCancel: boolean; error: string
  /** 本地与更新源历史互不包含（例如镜像重写历史导致 SHA 分离），更新前需弹窗确认。 */
  shaMismatch?: boolean
  managedByAndroid?: boolean
}
interface Commit { sha: string; author: string; date: string; message: string }
export interface CommitHistory { entries: Commit[]; total: number; hasMore: boolean; localHead: string | null; upstreamHead: string | null }
export interface Field { type: string; value: Value; mode?: string; display?: string; option?: Value[]; validate?: string | number[]; preserve_empty?: boolean; persist?: boolean }
/** 侧栏内容检索的一条命中：要么是任务名，要么是某个配置项。 */
export interface SearchContentHit {
  task: string
  key: string
  label: string
  help: string
  values: string
}

export interface SearchContentResult {
  tasks: SearchContentHit[]
  groups: SearchContentHit[]
  options: SearchContentHit[]
}

export interface Schema {
  menu: Record<string, { menu: string; page: string; tasks: string[] }>
  args: Record<string, Record<string, Record<string, Field>>>
  translations: Record<string, unknown>
}
export interface Config { instance: string; revision: string; values: Values }
export interface OpsiSimulatorResult {
  cl1Count: number; meowCount: number; crashedProbability: number
  cl1Time: number; meowTime: number; ap: number; coin: number
}
export interface OpsiSimulatorStatus {
  instance: string; state: 'idle' | 'running' | 'stopping' | 'completed' | 'interrupted' | 'failed'
  running: boolean; completedSamples: number; totalSamples: number; error: string
  runId: number
  result: OpsiSimulatorResult | null; figure: string | null; logs: Logs
}
export interface ScheduledTask {
  name: string; nextRun: string; pending: boolean; state: 'running' | 'pending' | 'waiting'
  /** 服务端确认这是可独立执行的已启用调度任务。 */
  runOnceAllowed?: boolean
}
export interface Resource { name: string; label: string; value: number | null; limit?: number; total?: number | null; record?: string }
export interface Overview {
  instance: string; revision: string; status: Status; tasks: ScheduledTask[]
  /** 区分调度器、单次队列任务与独立工具进程。 */
  schedulerRunning?: boolean
  singleTask?: {name: string; runId: string} | null
  /** 已请求停止、正在等当前任务在安全点退出（温柔停止）；此时再点一次停止即强制终止。 */
  stopping?: boolean
  resources: Resource[]; emulator: Record<string, Value>
}
export interface IslandSuspendState {
  /** 岛屿组下的任务总数。 */
  total: number
  /** 当前处于启用状态的数量。 */
  enabledCount: number
  /** 被「一键关闭」关掉、等待恢复的任务名。 */
  suspended: string[]
  suspendedCount: number
  /** 记录时间（空串表示没有暂停中的任务）。 */
  at: string
}
export interface LogEntry { id: number; level: string; text: string }
export interface Logs { instance: string; cursor: number; reset: boolean; entries: LogEntry[] }
export interface Preview { instance: string; image: string | null; capturedAt: string | null }
export interface Statistics { instance: string; resource: string; points: {time: string; value: number}[]; truncated: boolean }
export interface StatPoint {time: string; value: number; source?: string}
export interface StatSeries {key: string; label: string; icon?: string; points: StatPoint[]}
export interface StatPointCompact {t: number; v: number; s?: string}
export interface StatSeriesCompact {key: string; label: string; icon?: string; points: StatPointCompact[]}
/** 数值按顺序对应报表的共用时间轴。 */
export interface StatSeriesColumn {key: string; label: string; icon?: string; values: number[]; sources?: string[]}
export type StatSeriesWire = StatSeriesCompact | StatSeriesColumn
export interface StatTable {title: string; columns: string[]; rows: Scalar[][]; note?: string; defaultSort?: TableSort}
export interface TableSort {index: number; descending: boolean}
export interface StatisticsReport {
  instance: string; category: string; month: string
  /** 图标名称（可选）：为卡片单独指定图标（科研物品填 'research:<模板名>'，大世界掉落填 'opsi:<模板名>'），缺省按 label 查内置表。 */
  metrics: {label: string; value: number | null; unit: string; icon?: string}[]
  series: StatSeries[]; tables: StatTable[]; notes: string[]
  /** 大世界掉落专用：任务筛选选项（含当前时间窗口内没有记录的任务），count 表示窗口内掉落记录数。 */
  taskOptions?: {key: string; label: string; count: number}[]
}

export interface StatisticsReportWire extends Omit<StatisticsReport, 'series'> {
  /** 共用时间轴（微秒整数）。 */
  axis?: number[]
  series: StatSeriesWire[]
}
/** 指挥喵评分的单条天赋。`kind` 为 `special`（彩天赋）时高亮，`inferred` 表示这条由识别推断而来。 */
export interface MeowfficerTalent { name: string; level?: number; kind?: string; inferred?: boolean }
/** 指挥喵评分的一条评分口径；`x`/`y` 是两个维度的命中数（加权点制的口径为 null），`primary` 是主口径。 */
export interface MeowfficerRubric {
  key?: string; label: string; tier?: string; score?: number
  x?: number | null; y?: number | null
  xLabel?: string; yLabel?: string
  xHits?: string[]; yHits?: string[]; notes?: string[]; source?: string; primary?: boolean
}
/** 洗点推荐：verdict 决定配色，文案由后端给出（口径/成本也一并算好）。 */
export interface MeowfficerAdvice {
  verdict: string; headline: string; reason: string
  label?: string; score?: number; tier?: string
  cost?: number | null; costEstimated?: boolean; pointsSpent?: number; costText?: string
  targets?: string[]
}
/** 指挥喵评分里的一只猫。 */
export interface MeowfficerCat {
  source?: string; cat: string; tags?: string[]; fixed?: boolean; note?: string; maxed?: boolean
  level?: number | null
  pointsSpent?: number; primary?: string; talents?: MeowfficerTalent[]; rubrics?: MeowfficerRubric[]
  advice?: MeowfficerAdvice | null
}
/** 自动扫描时逐只确认的锁状态记录；同名猫按扫描顺序保留，不合并。 */
export interface MeowfficerLockAction {
  name: string; before: boolean | null; after: boolean | null; target: boolean | null
  status: 'changed' | 'unchanged' | 'skipped' | 'unconfirmed'; reason: string
}
/** 「指挥喵评分」任务的结构化报告；蓝猫或失败项可能只有动作记录，没有评分。 */
export interface MeowfficerScoreReport {
  instance: string; generatedAt: string; count: number; cats: MeowfficerCat[]
  /** 扫描时已接受的猫数，包含不评分的蓝猫；旧报告和截图评分模式可省略。 */
  scannedCount?: number
  lockActions?: MeowfficerLockAction[]
}
/** 旧版统计页的列描述：`key` 是 `Gui.Stat.*` 翻译键，非 `Gui.` 开头时按原文显示。 */
export interface LegacyColumn {key: string; format: string}
export interface LegacySeries {key: string; label: string; points: {time: string; value: number; apNow?: number}[]}
/** 旧版汇总项：`sign` 决定数值着色（gain 红 / loss 深绿 / 空不着色）。 */
export interface LegacySummaryItem {key: string; value: number | string; format: string; sign: string}
export interface LegacyOpsiPanel {summary: LegacySummaryItem[]; columns: LegacyColumn[]; rows: (number | string)[][]}
export interface LegacyMeowLootPanel {
  month: string; isCurrentMonth: boolean; availableMonths: string[]; lastRecord: string
  columns: LegacyColumn[]; rows: (number | string)[][]
  /** 历月累计按侵蚀等级独立返回，不受所选月份是否有记录影响；兼容旧后端可省略。 */
  cumulativeRows?: (number | string)[][]
}
export interface LegacyShipPanel {
  hasData: boolean; hasToday?: boolean; lastCheckTime?: string
  expPerHour?: number; todayExp?: number; todayRunMinutes?: number
  columns: LegacyColumn[]; rows: (number | string)[][]
}
export interface LegacyCommissionCard {name: string; index: number; color: string; labelKey: string; total: number; count: number; avg: number}
export interface LegacyCommissionItem {name: string; labelKey: string; color: string; icon: number; amount: number}
export interface LegacyCommissionRecent {time: string; items: LegacyCommissionItem[]; screenshot: string | null}
export interface LegacyCommissionRunning {name: string; finish: number; rare: boolean}
/**
 * 旧版统计页（旧版主题整页还原）的整页数据，见后端
 * `module/api/legacy_stats_service.py`；只含数据与 i18n 键，文案一律由前端渲染。
 */
export interface LegacyStatisticsReport {
  instance: string; month: string; dashboardKeys: string[]
  apChart: {series: LegacySeries[]}
  opsi: LegacyOpsiPanel
  meowLoot: LegacyMeowLootPanel
  shipExp: LegacyShipPanel
  commission: {
    periods: Record<'day' | 'week' | 'month', {cards: LegacyCommissionCard[]; totalCommissions: number}>
    recent: {rows: LegacyCommissionRecent[]; pageSize: number; maxPages: number; limit: number}
    running: {available: boolean; scannedAt: string | null; items: LegacyCommissionRunning[]}
  }
}
export interface DeployField { key: string; type: string; label: string; help: string; value: Value; options: Value[] }
export interface RemoteAccessStatus { enabled: boolean; state: string; address: string; error: string }
export interface Settings { groups: {key: string; label: string; fields: DeployField[]}[]; notice: string; demo: boolean; remote?: RemoteAccessStatus }
export interface ApiEvent { v: 1; type: 'event'; topic: string; seq: number; data: unknown }
export interface ApiResponse { v: 1; type: 'response'; id: string; ok: boolean; result?: unknown; error?: {code: string; message: string; details?: unknown} }
export interface Announcement {
  announcementId: string
  title: string
  content: string
  url?: string
}
/** 背景图库条目：文件都放在服务器的 cache/background/library 下。 */
export interface BackgroundGalleryEntry {
  id: string
  name: string
  size: number
  added: number
  kind: 'image' | 'video'
  source?: string
}

export interface Results {
  'emulator.status': EmulatorStatus
  'mind.catalog': MindCatalog
  'mind.report': MindReport
  'mind.calculate': MindCalculation
  'mind.save': MindReport
  'mind.import': {ships: MindShip[]}
  'mind.recognize': {ships: MindShip[]}
  'mind.export': {filename: string; content: string}
  'statistics.resourceFlows': ResourceFlowReport
  'stock.status': StockExchangeStatus
  'stock.rebuild': StockExchangeRebuild
  'stock.request': {status:number;data:unknown;etag:string;serverTime:number}
  'opsi.simulator.status': OpsiSimulatorStatus
  'opsi.simulator.start': OpsiSimulatorStatus
  'opsi.simulator.stop': OpsiSimulatorStatus
  'opsi.simulator.figure': {instance: string; image: string | null}
  'config.export': Values & {_schedulerProgram?: Pick<ProgramSaved, 'mode' | 'draft' | 'active'>}
  'background.access': {token: string}
  'scheduler.program.catalog': Catalog
  'scheduler.program.get': ProgramSaved
  'scheduler.program.save': ProgramSaved
  'scheduler.program.apply': ProgramSaved
  'scheduler.program.validate': ProgramValidation
  'scheduler.program.simulate': ProgramSimulation
  'scheduler.program.state': RuntimeProgramState
  'accounts.status': AccountStatus
  'accounts.manage': AccountStatus
  'announcement.get': Announcement | null
  'background.resolve': {final_url: string; content_type: string}
  'background.gallery.list': BackgroundGalleryEntry[]
  'background.gallery.add': {entry: BackgroundGalleryEntry}
  'background.gallery.remove': {removed: boolean}
  'background.gallery.open': {path: string}
  'updater.status': UpdateStatus
  'updater.commits': CommitHistory
  'updater.fetch': {accepted: boolean}
  'updater.apply': {accepted: boolean}
  'updater.cancel': {accepted: boolean}
  'system.ping': {pong: boolean}
  'island.suspend.state': IslandSuspendState
  'island.suspend.toggle': IslandSuspendState
  'auth.login': {authenticated: boolean}
  'events.subscribe': {topics: string[]; instance: string | null}
  'schema.get': Schema
  'search.content': SearchContentResult
  'instances.list': Instance[]
  'instances.create': Config
  'instances.importable': Array<{name: string; modified: number}>
  'instances.importConfig': {name: string}
  'instances.delete': {deleted: string}
  'config.get': Config
  'config.patch': Config
  'overview.get': Overview
  'scheduler.start': Overview
  'scheduler.stop': Overview
  'system.restart': {restarting: boolean}
  'tasks.run': Overview
  'tasks.runOnce': Overview
  'tasks.stop': Overview
  'logs.get': Logs
  'preview.capture': Preview
  'statistics.resources': Statistics
  'statistics.report': StatisticsReportWire
  'statistics.legacy': LegacyStatisticsReport
  'statistics.meowScreenshotFolder.open': {
    opened: boolean
    path: string | null
    requestedPath: string
    scope: 'month' | 'category' | 'missing'
    item: string
    month: string
  }
  'statistics.refreshLoot': {refreshed: boolean}
  'meowfficer.scoreReport': MeowfficerScoreReport
  'meowfficer.clearReport': {cleared: boolean; removed: string[]}
  'settings.get': Settings
  'settings.patch': {updated: string[]}
  'startup.get': {enabled: boolean; remember: boolean}
  'startup.set': {enabled: boolean; remember: boolean}
}

/** 最近一次系统时长检测快照；所有时间戳使用 Unix 秒，页面读取不会访问设备。 */
export interface EmulatorStatus {
  instance: string
  uptimeSeconds: number | null
  checkedAt: number | null
  lastAttemptAt: number | null
  available: boolean
  scheduled: boolean
  force: boolean
  intervalHours: number
  nextRestartAt: number | null
  serverTime: number
  schedulerRunning: boolean
}

export interface ResourceFlowEntry {
  id: number; ts: string; resource: string; amount: number; task: string; operation: string
  evidence: 'confirmed' | 'recognition' | 'observed' | 'adjustment'; run_id: string | null
}
export interface ResourceFlowReport {
  instance: string; start: string; end: string; offset: number; limit: number; total: number; throughId: number
  resources: {key: string; label: string; group: string; current: number | null; observedAt: string | null; income: number; expense: number; adjustment: number; count: number}[]
  tasks: string[]
  flows: {resource: string; task: string; operation: string; evidence: ResourceFlowEntry['evidence']; income: number; expense: number; count: number}[]
  entries: ResourceFlowEntry[]
  oilControl: {enable: boolean; target: number}
}

export interface StockExchangeStatus {url: string; instance:string; instanceId:string; bindingKey:string; bound: boolean; boundUsername:string; authenticated:boolean; message: string; lastObservedAt: number; snapshot: {instance: string; actionPoints: number; observedAt: number} | null}
export interface StockExchangeRebuild {instance:string;scope:'instance'|'all';affectedInstances:string[];rebuilt:boolean}

export interface AccountStatus {
  destroyed?: boolean
  local_bound?: boolean
  tpm_available?: boolean
  initialized: boolean; enabled: boolean; unlocked: boolean; tpm_bound: boolean
  profiles?: Array<{id: string; label: string; users: Array<{uid: string; name: string}>}>
  selected?: string | null
}
