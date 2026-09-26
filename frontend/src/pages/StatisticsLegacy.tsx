import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { RefreshCw } from 'lucide-react'
import { api } from '../api/client'
import type { LegacyColumn, LegacyStatisticsReport, Overview } from '../api/types'
import { useApp, useConnection } from '../app/context'
import { ErrorBox, Loading, Modal } from '../components/ui'
import { LegacyApChart } from '../components/LegacyApChart'
import { ResourceCards } from '../components/ResourceCards'
import '../styles/legacy-stats.css'

/** 面板自动刷新间隔，与旧界面各板块的 60 秒周期刷新一致。 */
const AUTO_REFRESH_MS = 60_000
/** 历史月份选择器里最多列出多少个历史月份（旧界面同样是 24）。 */
const HISTORY_MONTH_LIMIT = 24
/* 委托收益五张卡片的图标沿用**旧界面**那一套（assets/gui/icon/icon_N.png，
   由后端挂在 /gui-icons 下，与旧界面用的是同一份文件）：
   钻石 icon_1 / 心智魔方 icon_2 / **心智 icon_3（浅蓝）** / 石油 icon_4 / 物资 icon_5。
   注意「心智」不是新前端的“核心数据”（core_data.webp），两者不是同一个东西。 */
const commissionIcons: Record<string, string> = {
  Gem: '/gui-icons/icon_1.png',
  Cube: '/gui-icons/icon_2.png',
  Chip: '/gui-icons/icon_3.png',
  Oil: '/gui-icons/icon_4.png',
  Coin: '/gui-icons/icon_5.png',
}
type Cell = number | string

/** 旧界面的 `t()` 支持 `{name}` 占位符；前端 schema 翻译表只给原文，这里补上替换。 */
function useLegacyText() {
  const {t} = useApp()
  return useCallback((key: string, params?: Record<string, string | number>) => {
    // 非 Gui.* 的「键」是旧界面里硬编码的文案（如耄耋收获表的金菜/彩图纸），原样显示
    const template = key.startsWith('Gui.') ? t(key) : key
    if (!params) return template
    return template.replace(/\{(\w+)\}/g, (match, name: string) => name in params ? String(params[name]) : match)
  }, [t])
}

/**
 * 单元格格式化：格式与旧界面 `build_simple_table` 的取值方式一致。
 * `int`/`text` 原样输出（旧界面不做千位分隔），`percent` 两位小数加百分号，
 * `seconds` 一位小数加秒单位（旧界面的 `Gui.Stat.SecondUnit`）。
 */
function formatCell(value: Cell | null | undefined, format: string, text: (key: string, params?: Record<string, string | number>) => string) {
  if (value == null || value === '') return '-'
  if (format === 'percent') return typeof value === 'number' ? `${value.toFixed(2)}%` : String(value)
  if (format === 'seconds') return typeof value === 'number' ? `${value.toFixed(1)}${text('Gui.Stat.SecondUnit')}` : String(value)
  return String(value)
}

/** 统计页的简洁表格，结构对应旧界面的 `webapp/simple_table.html`（表头左对齐、内容居中）。 */
function LegacyTable({columns, rows, text}: {
  columns: LegacyColumn[]
  rows: Cell[][]
  text: (key: string, params?: Record<string, string | number>) => string
}) {
  return <div className="legacy-table-wrap">
    <table className="legacy-table">
      <thead><tr>{columns.map((column, index) => <th key={`${column.key}-${index}`}>{text(column.key)}</th>)}</tr></thead>
      <tbody>{rows.map((row, rowIndex) => <tr key={rowIndex}>
        {row.map((value, cellIndex) => <td key={cellIndex}>{formatCell(value, columns[cellIndex]?.format ?? 'text', text)}</td>)}
      </tr>)}</tbody>
    </table>
  </div>
}

/**
 * 汇总项：文案形如「当月侵蚀一购买体力: {value}」，旧界面按**最后一个冒号**把标签
 * 与数值拆开，只给数值着正负色（整条染色会让标签看起来像出错）。
 */
function LegacySummary({items, text}: {
  items: {key: string; value: Cell; format: string; sign: string}[]
  text: (key: string, params?: Record<string, string | number>) => string
}) {
  return <div className="legacy-stat-summary">
    {items.map(item => {
      const shown = item.format === 'percent' && typeof item.value === 'number' ? `${item.value.toFixed(2)}%` : String(item.value)
      const template = text(item.key)
      const colon = Math.max(template.lastIndexOf(':'), template.lastIndexOf('：'))
      return <span className="legacy-summary-item" key={item.key}>
        <span>{colon >= 0 ? template.slice(0, colon + 1) : template}</span>
        <span className={item.sign ? `legacy-summary-value is-${item.sign}` : 'legacy-summary-value'}>{shown}</span>
      </span>
    })}
  </div>
}

/** 板块标题行：标题 + 紧随其后的刷新图标按钮（旧界面五个板块统一这个形式）。 */
function LegacySectionTitle({title, onRefresh, busy, children}: {
  title: string
  onRefresh: () => void
  busy: boolean
  children?: React.ReactNode
}) {
  const {ui} = useApp()
  return <div className="legacy-stat-title-row">
    <h2 className="legacy-stat-heading">{title}</h2>
    <button type="button" className="legacy-stat-refresh" onClick={onRefresh} disabled={busy} aria-label={ui('stats.refresh')} title={ui('stats.refresh')}>
      <RefreshCw size={15}/>
    </button>
    {children}
  </div>
}

/**
 * 旧版统计页整页还原（旧版主题下使用）。
 *
 * `embedded` 表示本页嵌在旧版总览页的主区里（那里已经渲染了资源仪表盘），
 * 此时不再重复渲染一次仪表盘。
 */
export function StatisticsLegacy({embedded = false}: {embedded?: boolean} = {}) {
  const {instance = ''} = useParams()
  const {ui, notify} = useApp()
  const text = useLegacyText()
  const connection = useConnection()
  const [data, setData] = useState<LegacyStatisticsReport>()
  const [overview, setOverview] = useState<Overview>()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  // 静默重取中（切月份 / 自动刷新）：保留现有内容，只把按钮禁用、表格压暗
  const [pending, setPending] = useState(false)
  // 耄耋相接收获查看的月份，undefined 表示本月
  const [meowMonth, setMeowMonth] = useState<string>()
  const [monthPickerOpen, setMonthPickerOpen] = useState(false)
  const [commissionPeriod, setCommissionPeriod] = useState<'day' | 'week' | 'month'>('month')
  const [recentPage, setRecentPage] = useState(0)

  const load = useCallback((silent: boolean) => {
    if (connection !== 'ready') {
      setBusy(false)
      setPending(false)
      return Promise.resolve()
    }
    if (!silent) setBusy(true)
    const request = api.request('statistics.legacy', {instance, month: meowMonth ?? null})
    const resources = api.request('overview.get', {instance})
    return Promise.all([request, resources]).then(([report, overviewData]) => {
      setData(report)
      setOverview(overviewData)
      setError('')
    }).catch((error: Error) => {
      if (!silent) setError(error.message)
    }).finally(() => {
      setBusy(false)
      setPending(false)
    })
  }, [connection, instance, meowMonth])

  /* 只在切换实例（或首次进入）时清空内容：切月份走静默重取，页面 DOM 保持不变，
     否则整块内容会被替换成 Loading，滚动位置随之回到顶部（看起来像整页刷新）。 */
  useEffect(() => {
    setData(undefined)
    setError('')
    setMeowMonth(undefined)
  }, [instance])

  useEffect(() => {
    void load(true)
  }, [load])

  /* 旧界面各板块 60 秒自刷新一次，仪表盘更快；这里统一按 60 秒静默重取。 */
  useEffect(() => {
    if (connection !== 'ready') return
    const timer = setInterval(() => void load(true), AUTO_REFRESH_MS)
    return () => clearInterval(timer)
  }, [connection, load])

  /* 后端统计更新推送（大世界/委托落库后）时静默刷新，避免整页 Loading 闪烁。 */
  useEffect(() => {
    if (connection !== 'ready') return
    let timer: ReturnType<typeof setTimeout> | undefined
    return api.onEvent(event => {
      if (event.topic !== 'statistics' && event.topic !== 'overview') return
      const payload = event.data as {instance?: string} | undefined
      if (payload?.instance && payload.instance !== instance) return
      clearTimeout(timer)
      timer = setTimeout(() => void load(true), 300)
    })
  }, [connection, instance, load])

  const commission = data?.commission
  const periodSummary = commission?.periods[commissionPeriod]
  const recentRows = commission?.recent.rows ?? []
  const pageSize = commission?.recent.pageSize ?? 10
  const pages = Math.max(1, Math.min(commission?.recent.maxPages ?? 5, Math.ceil(recentRows.length / pageSize)))
  const currentPage = Math.min(recentPage, pages - 1)
  const pageRows = recentRows.slice(currentPage * pageSize, (currentPage + 1) * pageSize)
  const historyMonths = useMemo(
    () => (data?.meowLoot.availableMonths ?? []).slice(0, HISTORY_MONTH_LIMIT), [data])

  /* 切换区间时把最近记录翻回第一页（旧界面的行为）。 */
  function changePeriod(next: 'day' | 'week' | 'month') {
    setCommissionPeriod(next)
    setRecentPage(0)
  }

  function pickMonth(month?: string) {
    // 切月份只压暗表格、保留页面 DOM（整页 Loading 会把滚动位置顶回顶部）；
    // 60 秒自动刷新不压暗，否则每分钟闪一下。
    setPending(true)
    setMeowMonth(month)
    setMonthPickerOpen(false)
  }

  if (error) return <div className="statistics-legacy"><ErrorBox message={error} retry={() => void load(false)}/></div>
  if (!data) return <div className="statistics-legacy"><Loading/></div>

  const meowTitle = data.meowLoot.isCurrentMonth
    ? '本月耄耋相接收获'
    : `历史耄耋相接收获（${data.meowLoot.month}）`

  return <div className="statistics-legacy legacy-stats">
    <h1 className="legacy-sr-title">{ui('nav.statistics')}</h1>

    {/* 区域一：资源仪表盘（8 项，窄屏吸顶）；嵌在总览页时上面已有仪表盘 */}
    {!embedded && <section className="legacy-stats-dashboard">
      <ResourceCards resources={overview?.resources ?? []} selected={data.dashboardKeys}/>
    </section>}

    {/* 区域二：各统计板块，间距由 .legacy-stats-section 统一收口 */}
    <div className="legacy-stats-charts">
      <section className="legacy-stats-section">
        <LegacyApChart series={data.apChart.series} onRefresh={() => void load(false)} refreshing={busy}/>
      </section>

      {/* 雪风大人的大世界数据收集：一张表按侵蚀等级分三行（1 / 5 / 3） */}
      <section className="legacy-stats-section legacy-stats-card">
        <LegacySectionTitle title={text('Gui.Stat.OpsiDataCollectionTitle')} onRefresh={() => void load(false)} busy={busy}/>
        <LegacySummary items={data.opsi.summary} text={text}/>
        <LegacyTable columns={data.opsi.columns} rows={data.opsi.rows} text={text}/>
      </section>

      {/* 本月 / 历史耄耋相接收获 */}
      <section className={`legacy-stats-section legacy-stats-card${pending ? ' is-pending' : ''}`}>
        <LegacySectionTitle title={meowTitle} onRefresh={() => void load(false)} busy={busy}>
          <div className="legacy-stat-title-actions">
            {data.meowLoot.isCurrentMonth
              ? <button type="button" className="legacy-button" disabled={pending} onClick={() => historyMonths.length ? setMonthPickerOpen(true) : notify('暂无历史月份数据')}>查看历史月份</button>
              : <button type="button" className="legacy-button is-primary" disabled={pending} onClick={() => pickMonth(undefined)}>回到本月</button>}
          </div>
        </LegacySectionTitle>
        <div className="legacy-stat-summary">
          <span className="legacy-summary-item">{text('Gui.Stat.MeowLastRecord', {value: data.meowLoot.lastRecord})}</span>
        </div>
        <LegacyTable columns={data.meowLoot.columns} rows={data.meowLoot.rows} text={text}/>
      </section>

      {/* 每日经验检测 / 舰船升级进度 */}
      <section className="legacy-stats-section legacy-stats-card">
        <LegacySectionTitle title={text('Gui.Stat.ShipExpProgressTitle')} onRefresh={() => void load(false)} busy={busy}/>
        {data.shipExp.hasData
          ? <>
              <div className="legacy-stat-summary">
                <span className="legacy-summary-item">{text('Gui.Stat.LastCheckTime', {value: data.shipExp.lastCheckTime ?? '-'})}</span>
                {data.shipExp.hasToday && <>
                  <span className="legacy-summary-item">{text('Gui.Stat.TodayExp', {value: data.shipExp.todayExp ?? 0})}</span>
                  <span className="legacy-summary-item">{text('Gui.Stat.ExpEfficiency', {value: data.shipExp.expPerHour ?? 0, unit: text('Gui.Stat.HourUnit')})}</span>
                  <span className="legacy-summary-item">{text('Gui.Stat.TodayRun', {value: data.shipExp.todayRunMinutes ?? 0, unit: text('Gui.Stat.MinuteUnit')})}</span>
                </>}
              </div>
              {!data.shipExp.hasToday && <p className="legacy-muted">{text('Gui.Stat.NoTodayBattleData')}</p>}
              <LegacyTable columns={data.shipExp.columns} rows={data.shipExp.rows} text={text}/>
            </>
          : <p className="legacy-muted">{text('Gui.Stat.NoShipExpData')}</p>}
      </section>

      {/* 委托收益统计 */}
      <section className="legacy-stats-section legacy-stats-card legacy-commission">
        <LegacySectionTitle title={text('Gui.Stat.CommissionIncomeTitle')} onRefresh={() => void load(false)} busy={busy}/>
        <div className="legacy-segmented" role="group">
          {(['day', 'week', 'month'] as const).map(period => <button
            key={period}
            type="button"
            className={`legacy-button${commissionPeriod === period ? ' is-primary' : ''}`}
            aria-pressed={commissionPeriod === period}
            onClick={() => changePeriod(period)}>
            {text(period === 'day' ? 'Gui.Stat.CommissionIncomeDay' : period === 'week' ? 'Gui.Stat.CommissionIncomeWeek' : 'Gui.Stat.CommissionIncomeMonth')}
          </button>)}
        </div>
        <div className="legacy-commission-cards">
          {(periodSummary?.cards ?? []).map(card => <div className="legacy-commission-card" key={card.name}>
            <div className="legacy-commission-card-head">
              <span className="legacy-commission-icon" style={{background: `${card.color}1a`}}>
                <img src={commissionIcons[card.name] ?? '/gui-icons/icon_3.png'} alt="" width={16} height={16}
                  onError={event => {(event.currentTarget as HTMLImageElement).style.visibility = 'hidden'}}/>
              </span>
              <span className="legacy-commission-name">{text(card.labelKey)}</span>
            </div>
            <strong className="legacy-commission-total" style={{color: card.color}}>{card.total > 0 ? `+${card.total.toLocaleString()}` : '0'}</strong>
            <span className="legacy-commission-stat">{text('Gui.Stat.CommissionIncomeCardStat', {count: card.count.toLocaleString(), avg: card.avg.toFixed(1)})}</span>
          </div>)}
        </div>

        <div className="legacy-commission-recent">
          <div className="legacy-running-head">
            <span className="legacy-running-title">{text('Gui.Stat.RunningCommissionTitle')}</span>
            {commission?.running.scannedAt && <span className="legacy-running-scanned">{text('Gui.Stat.RunningCommissionScannedAt', {value: commission.running.scannedAt})}</span>}
          </div>
          {!commission?.running.available
            ? <p className="legacy-muted">{text('Gui.Stat.RunningCommissionUnknown')}</p>
            : !commission.running.items.length
              ? <p className="legacy-muted">{text('Gui.Stat.RunningCommissionNone')}</p>
              : <div className="legacy-running-list">
                  {commission.running.items.map(item => <div className={`legacy-running-card${item.rare ? ' is-rare' : ''}`} key={`${item.name}-${item.finish}`}>
                    <span className="legacy-running-name">{item.name}</span>
                    <span className="legacy-running-finish">{text('Gui.Stat.RunningCommissionFinish', {value: new Date(item.finish * 1000).toTimeString().slice(0, 5)})}</span>
                  </div>)}
                </div>}

          {!!recentRows.length && <>
            <div className="legacy-recent-divider"/>
            <div className="legacy-recent-title">{text('Gui.Stat.CommissionIncomeRecentTitle')}</div>
            <div className="legacy-recent-list">
              {pageRows.map((row, index) => <div className="legacy-recent-row" key={`${row.time}-${index}`}>
                <span className="legacy-recent-time">{row.time}</span>
                <span className="legacy-recent-items">
                  {row.items.length
                    ? row.items.map(item => <span className="legacy-recent-item" key={item.name}>
                        {/* 物品图标与上面五张卡片同一套（/gui-icons/icon_1..5.png）；
                            图标缺失时退回原来的纯色圆点，颜色信息始终保留。 */}
                        {commissionIcons[item.name]
                          ? <span className="legacy-recent-icon" style={{background: `${item.color}1a`}}>
                              <img src={commissionIcons[item.name]} alt="" width={14} height={14}
                                onError={event => {(event.currentTarget as HTMLImageElement).style.visibility = 'hidden'}}/>
                            </span>
                          : <span className="legacy-recent-dot" style={{background: item.color}}/>}
                        <span>{text(item.labelKey)}</span>
                        <span className="legacy-recent-amount">x{item.amount}</span>
                      </span>)
                    : <span className="legacy-recent-empty">--</span>}
                </span>
                {row.screenshot && <a className="legacy-shot-link" href={row.screenshot} target="_blank" rel="noopener">{text('Gui.Stat.ViewScreenshot')}</a>}
              </div>)}
            </div>
          </>}

          {recentRows.length > pageSize && <div className="legacy-pagination">
            <button type="button" className="legacy-button" disabled={!currentPage} onClick={() => setRecentPage(Math.max(0, currentPage - 1))}>上一页</button>
            {Array.from({length: pages}, (_, index) => <button
              key={index}
              type="button"
              className={`legacy-button${index === currentPage ? ' is-primary' : ''}`}
              aria-pressed={index === currentPage}
              onClick={() => setRecentPage(index)}>{index + 1}</button>)}
            <button type="button" className="legacy-button" disabled={currentPage + 1 >= pages} onClick={() => setRecentPage(Math.min(pages - 1, currentPage + 1))}>下一页</button>
          </div>}

          <p className="legacy-commission-foot">{text('Gui.Stat.CommissionIncomeTotalCommissions', {value: periodSummary?.totalCommissions ?? 0})}</p>
        </div>
      </section>
    </div>

    {monthPickerOpen && <Modal title="选择查看月份" onClose={() => setMonthPickerOpen(false)} className="legacy-month-modal">
      <div className="legacy-month-grid">
        <button type="button" disabled={pending} className={`legacy-button${data.meowLoot.isCurrentMonth ? ' is-primary' : ''}`} onClick={() => pickMonth(undefined)}>本月（{currentMonthKey()}）</button>
        {historyMonths.map(month => <button
          type="button"
          key={month}
          disabled={pending}
          className={`legacy-button${month === data.meowLoot.month ? ' is-primary' : ''}`}
          onClick={() => pickMonth(month)}>{month}</button>)}
      </div>
    </Modal>}
  </div>
}

function currentMonthKey() {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}
