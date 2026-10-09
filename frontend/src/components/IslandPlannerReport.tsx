/** 岛屿规划的只读快照：实读现货与最近下单分别展示，刷新不触碰参数草稿。 */
import {useEffect, useRef, useState} from 'react'
import {RefreshCw} from 'lucide-react'
import {api} from '../api/client'
import type {Value} from '../api/types'
import {useConnection} from '../app/context'
import './IslandPlannerReport.css'

interface PlanItem {
  id: number; name: string; target: number; floor: number; buffer: number; reserve: number; demand: number; idle_per_day: number
}
interface Observation {stock: number; at: string; source: string; stale?: boolean}
interface Dispatch {amount: number; at: string; source: string}
export interface PlannerReport {
  version: 1; generated_at: string; daily_revenue: number | null; daily_profit: number | null; legacy: boolean; enabled: boolean
  items: PlanItem[]
  production: {recipe_id: number; name: string; place: string; batches_per_day: number}[]
  menus: {id: number; name: string; capacity: number | null; items: {id: number; name: string; per_day: number}[]}[]
  observations: Record<string, Observation>; dispatches: Record<string, Dispatch>
}

const object = (value: unknown): Record<string, unknown> | null => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
const amount = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0
const identifier = (value: unknown): value is number => amount(value) && Number.isInteger(value) && value > 0
const money = (value: unknown): number | null => typeof value === 'number' && Number.isFinite(value) ? value : null
const format = (value: number) => value.toLocaleString('zh-CN', {maximumFractionDigits: 3})
const sourceName = (source: string) => ({IslandFarm: '农田／果园／苗圃巡检', IslandMineForest: '矿山／林场巡检',
  IslandRancher: '牧场巡检', IslandFishery: '渔场巡检', IslandRestaurant: '有鱼餐馆生产', IslandTeahouse: '白熊饮品生产',
  IslandJuuEatery: '啾啾简餐生产', IslandGrill: '乌鱼烤肉生产', IslandJuuCoffee: '啾咖啡生产', IslandManufacture: '制造工坊巡检'}[source] ?? source)

/** 容忍旧报告缺少巡检字段；损坏的版本或目标不推断为有效计划。 */
export function parsePlannerReport(value: Value | undefined): PlannerReport | null {
  try {
    const data = object(typeof value === 'string' ? JSON.parse(value) : value)
    if (!data || data.version !== 1 || !Array.isArray(data.items)) return null
    const items: PlanItem[] = []
    const ids = new Set<number>()
    for (const raw of data.items) {
      const item = object(raw)
      if (!item || !identifier(item.id) || ids.has(item.id) || typeof item.name !== 'string' || !amount(item.target)) return null
      ids.add(item.id)
      items.push({id: item.id, name: item.name, target: item.target,
        floor: amount(item.floor) ? item.floor : 0, buffer: amount(item.buffer) ? item.buffer : 0,
        reserve: amount(item.reserve) ? item.reserve : 0, demand: amount(item.demand) ? item.demand : 0,
        idle_per_day: amount(item.idle_per_day) ? item.idle_per_day : 0})
    }
    const observations: Record<string, Observation> = {}
    for (const [id, raw] of Object.entries(object(data.observations) ?? {})) {
      const observation = object(raw)
      if (observation && amount(observation.stock) && typeof observation.at === 'string' && observation.at) {
        observations[id] = {stock: observation.stock, at: observation.at, source: typeof observation.source === 'string' ? observation.source : '', stale: observation.stale === true}
      }
    }
    const dispatches: Record<string, Dispatch> = {}
    for (const [id, raw] of Object.entries(object(data.dispatches) ?? {})) {
      const dispatch = object(raw)
      if (dispatch && amount(dispatch.amount) && typeof dispatch.at === 'string' && dispatch.at) {
        dispatches[id] = {amount: dispatch.amount, at: dispatch.at, source: typeof dispatch.source === 'string' ? dispatch.source : ''}
      }
    }
    const production: PlannerReport['production'] = []
    for (const raw of Array.isArray(data.production) ? data.production : []) {
      const recipe = object(raw)
      if (recipe && identifier(recipe.recipe_id) && typeof recipe.name === 'string' && typeof recipe.place === 'string' && amount(recipe.batches_per_day)) {
        production.push({recipe_id: recipe.recipe_id, name: recipe.name, place: recipe.place, batches_per_day: recipe.batches_per_day})
      }
    }
    const menus: PlannerReport['menus'] = []
    for (const raw of Array.isArray(data.menus) ? data.menus : []) {
      const shop = object(raw)
      if (!shop || !identifier(shop.id) || typeof shop.name !== 'string' || !Array.isArray(shop.items)) continue
      const dishes: PlannerReport['menus'][number]['items'] = []
      for (const rawDish of shop.items) {
        const dish = object(rawDish)
        if (dish && identifier(dish.id) && typeof dish.name === 'string' && amount(dish.per_day)) dishes.push({id: dish.id, name: dish.name, per_day: dish.per_day})
      }
      menus.push({id: shop.id, name: shop.name, capacity: amount(shop.capacity) ? shop.capacity : null, items: dishes})
    }
    return {version: 1, generated_at: typeof data.generated_at === 'string' ? data.generated_at : '',
      daily_revenue: money(data.daily_revenue), daily_profit: money(data.daily_profit), legacy: data.legacy === true, enabled: data.enabled !== false,
      items, production, menus, observations, dispatches}
  } catch {return null}
}

/** 仅按有时间戳且有效的现货观测计算缺口，下单记录不参与扣减。 */
export function itemProgress(item: PlanItem, report: PlannerReport) {
  const observed = report.observations[String(item.id)]
  if (item.target === 0) return {kind: 'idle' as const, label: item.idle_per_day > 0 ? '余岗积累' : '无维持目标', gap: null, observed}
  if (!observed) return {kind: 'unknown' as const, label: '待巡检', gap: null, observed}
  if (observed.stale) return {kind: 'stale' as const, label: '待复核', gap: null, observed}
  const gap = Math.max(item.target - observed.stock, 0)
  return {kind: gap > 0 ? 'short' as const : 'enough' as const, label: gap > 0 ? '最近现货不足' : '最近现货已够', gap, observed}
}

export function IslandPlannerReportView({report, status = '', connected = true, failed = false, busy = false, onRefresh}: {
  report: PlannerReport | null; status?: string; connected?: boolean; failed?: boolean; busy?: boolean; onRefresh?: () => void
}) {
  const entries = report?.items.map(item => ({item, ...itemProgress(item, report)})) ?? []
  const shorts = entries.filter(item => item.kind === 'short')
  const enough = entries.filter(item => item.kind === 'enough')
  const pending = entries.filter(item => item.kind === 'unknown' || item.kind === 'stale')
  const idle = entries.filter(item => item.kind === 'idle' && item.item.idle_per_day > 0)
  const sorted = [...entries].sort((a, b) => ({short: 0, stale: 1, unknown: 2, enough: 3, idle: 4}[a.kind] - {short: 0, stale: 1, unknown: 2, enough: 3, idle: 4}[b.kind]))
  const planFailed = /失败|保留旧计划/.test(status)
  return <section className="island-planner-report" aria-label="生产经营计划明细">
    <div className="island-planner-report-heading">
      <h3>生产经营计划明细</h3>
      <button type="button" className="button subtle" disabled={!connected || busy} onClick={onRefresh} aria-label="刷新规划明细">
        <RefreshCw size={14}/>{busy ? '读取中…' : '刷新明细'}
      </button>
    </div>
    {!connected && <p className="island-planner-note" role="status">连接已断开，以下保留上次读取的计划快照。</p>}
    {failed && <p className="island-planner-warning" role="status">本次读取失败，保留上次结果，可再次刷新。</p>}
    {planFailed && <p className="island-planner-warning">{status}。以下明细仍是上次有效计划。</p>}
    {report && !report.enabled && <p className="island-planner-warning">自动规划已关闭，当前按手工配置运行；以下为最近保存的计划。</p>}
    {!report ? <p className="island-planner-note">等待下一次生产巡检补充详情。已有简短规划状态仍显示在上方。</p> : <>
      {report.legacy && <p className="island-planner-note">按已有目标回显。等待下一次生产巡检补充详情；旧计划未记录的配方与巡检值不作推测。</p>}
      <p className="island-planner-note">计划生成：{report.generated_at || '旧计划未记录时间'}{report.daily_revenue !== null && ` · 理论每日收入 ${format(report.daily_revenue)}`}{report.daily_profit !== null && ` · 理论每日净收益 ${format(report.daily_profit)}`}</p>
      <div className="island-planner-totals">
        <span>计划商品 <strong>{entries.length}</strong> 项</span>
        <span className="island-planner-short">最近现货有缺口 <strong>{shorts.length}</strong> 项</span>
        <span>最近现货已够 <strong>{enough.length}</strong> 项</span>
        <span>待巡检／待复核 <strong>{pending.length}</strong> 项</span>
        {idle.length > 0 && <span>仅余岗积累 <strong>{idle.length}</strong> 项</span>}
      </div>
      {shorts.length > 0 && <p className="island-planner-short">最近实读缺口：{shorts.slice(0, 3).map(entry => `${entry.item.name} ${format(entry.gap!)}`).join('、')}{shorts.length > 3 && `，另有 ${shorts.length - 3} 项`}</p>}
      <p className="island-planner-note">目标是维持的库存量，不是今天新生产的件数。缺口只比较最近实读现货；最近下单预计产出不代表当前在产，也不从现货缺口扣除。</p>
      <details>
        <summary>生产目标与最近实读（{entries.length} 项）</summary>
        <div className="island-planner-table-wrap" tabIndex={0} aria-label="横向滚动生产明细">
          <table aria-label="生产目标与最近实读">
            <thead><tr><th>商品</th><th>目标库存与组成</th><th>最近实读现货</th><th>现货缺口</th><th>观察状态</th><th>观测时间</th><th>最近确认下单预计产出</th><th>下单时间</th></tr></thead>
            <tbody>{sorted.map(entry => {
              const dispatch = report.dispatches[String(entry.item.id)]
              const parts = ([['保留', entry.item.floor], ['周转', entry.item.buffer], ['经营预留', entry.item.reserve], ['额外需求', entry.item.demand]] as const).filter(([, value]) => value > 0)
              return <tr key={entry.item.id} data-item-id={entry.item.id}>
                <th scope="row">{entry.item.name}{entry.item.idle_per_day > 0 && <small>余岗积累 {format(entry.item.idle_per_day)}／日</small>}</th>
                <td>{format(entry.item.target)}{parts.length > 0 && <small>{parts.map(([name, value]) => `${name} ${format(value)}`).join(' + ')}</small>}</td>
                <td>{entry.observed ? format(entry.observed.stock) : '未实读'}</td>
                <td>{entry.gap === null ? entry.label : format(entry.gap)}</td>
                <td className={`island-planner-${entry.kind}`}>{entry.label}</td>
                <td>{entry.observed?.at ?? '等待巡检'}{entry.observed?.source && <small>{entry.observed.stale ? '操作前观测：' : '来源：'}{sourceName(entry.observed.source)}</small>}</td>
                <td>{dispatch ? format(dispatch.amount) : '未记录'}</td>
                <td>{dispatch?.at ?? '未记录'}{dispatch?.source && <small>来源：{sourceName(dispatch.source)}</small>}</td>
              </tr>
            })}</tbody>
          </table>
        </div>
        {!entries.length && <p className="island-planner-note">本轮没有库存维持目标，余岗与经营仍以本轮计划为准。</p>}
      </details>
      <details>
        <summary>理论每日配方（{report.production.length} 项）</summary>
        <p className="island-planner-note">这是日均产能安排，实际派遣还受岗位、角色及原料限制，不表示今天已经完成。</p>
        {report.production.length ? <div className="island-planner-table-wrap" tabIndex={0} aria-label="横向滚动每日配方">
          <table aria-label="理论每日配方"><thead><tr><th>场所</th><th>产物</th><th>理论批次／日</th></tr></thead><tbody>
            {report.production.map(recipe => <tr key={recipe.recipe_id}><td>{recipe.place}</td><td>{recipe.name}</td><td>{format(recipe.batches_per_day)}</td></tr>)}
          </tbody></table>
        </div> : <p className="island-planner-note">暂无每日配方明细，等待下一次规划补充。</p>}
      </details>
      <details>
        <summary>经营菜单（{report.menus.filter(shop => shop.items.length > 0).length} 家有菜单）</summary>
        <p className="island-planner-note">每日销量为理论安排；一货架数量来自店铺等级和经营角色，实际补货保留整架。菜单为最近保存的规划安排；实际经营可能按季节或加成换菜，以经营任务当前结果为准。</p>
        {report.menus.length ? report.menus.map(shop => <div className="island-planner-shop" key={shop.id}>
          <h4>{shop.name} · {shop.capacity === null ? '一货架数量待确认' : `一货架 ${format(shop.capacity)} 份`}</h4>
          {shop.items.length ? <ul>{shop.items.map(dish => <li key={dish.id}>{dish.name} <span>理论 {format(dish.per_day)} 份／日</span></li>)}</ul>
            : <p className="island-planner-note">本轮菜单为空，仅领取已有收益，不再次开业。</p>}
        </div>) : <p className="island-planner-note">暂无经营菜单明细，等待下一次规划补充。</p>}
      </details>
    </>}
  </section>
}

export function IslandPlannerReport({instance, initialReport, initialStatus, onStatusChange}: {
  instance: string; initialReport?: Value; initialStatus?: Value; onStatusChange?: (instance: string, status: string) => void
}) {
  const connection = useConnection()
  const [snapshot, setSnapshot] = useState({instance, report: parsePlannerReport(initialReport)})
  const [statusSnapshot, setStatusSnapshot] = useState({instance, status: typeof initialStatus === 'string' ? initialStatus : ''})
  const [failed, setFailed] = useState(false)
  const [busy, setBusy] = useState(false)
  const refreshRef = useRef<(() => void) | null>(null)
  const callback = useRef(onStatusChange)
  useEffect(() => {callback.current = onStatusChange}, [onStatusChange])
  useEffect(() => {setSnapshot({instance, report: parsePlannerReport(initialReport)})}, [instance, initialReport])
  useEffect(() => {setStatusSnapshot({instance, status: typeof initialStatus === 'string' ? initialStatus : ''})}, [instance, initialStatus])
  useEffect(() => {
    let active = true
    let pending = false
    setFailed(false)
    setBusy(false)
    if (connection !== 'ready') {refreshRef.current = null; return}
    const refresh = async (manual = false) => {
      if (pending || (!manual && document.visibilityState === 'hidden')) return
      pending = true
      setBusy(true)
      try {
        const response = await api.request('config.get', {instance})
        if (!active || response.instance !== instance) return
        const values = response.values.IslandPlan?.IslandProductionPlanner
        const status = typeof values?.PlannerStatus === 'string' ? values.PlannerStatus : ''
        setSnapshot({instance, report: parsePlannerReport(values?.PlannerReport)})
        setStatusSnapshot({instance, status})
        setFailed(false)
        callback.current?.(instance, status)
      } catch {if (active) setFailed(true)}
      finally {pending = false; if (active) setBusy(false)}
    }
    refreshRef.current = () => {void refresh(true)}
    const visible = () => {if (document.visibilityState !== 'hidden') void refresh()}
    visible()
    const timer = window.setInterval(visible, 15000)
    document.addEventListener('visibilitychange', visible)
    return () => {active = false; refreshRef.current = null; window.clearInterval(timer); document.removeEventListener('visibilitychange', visible)}
  }, [instance, connection])
  return <IslandPlannerReportView report={snapshot.instance === instance ? snapshot.report : null}
    status={statusSnapshot.instance === instance ? statusSnapshot.status : ''} connected={connection === 'ready'} failed={failed} busy={busy}
    onRefresh={() => refreshRef.current?.()}/>
}
