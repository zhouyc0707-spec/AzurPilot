import { useEffect, useMemo, useRef, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, DataZoomComponent, VisualMapComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { LegacySeries } from '../api/types'
import { useApp } from '../app/context'
import '../styles/legacy-ap-chart.css'

echarts.use([LineChart, GridComponent, TooltipComponent, DataZoomComponent, VisualMapComponent, CanvasRenderer])

type Range = 'last24h' | 'week' | 'month'
type Point = {time: string; value: number; apNow?: number}

/** 序列顺序与旧图表一致（seriesVisible 的下标顺序）：体力 / 紫币 / 黄币 / 资产 / 海里数。 */
const SERIES_ORDER = ['ap', 'purple_coins', 'yellow_coins', 'asset', 'distance'] as const
/** 旧图表默认只显示体力，其余四条勾选后才画。 */
const DEFAULT_VISIBLE = [true, false, false, false, false]
/** 时间范围按钮：i18n 键与旧界面一致，默认近七天。 */
const RANGES: {value: Range; key: string}[] = [
  {value: 'last24h', key: 'Gui.Stat.ApPeriodLast24h'},
  {value: 'week', key: 'Gui.Stat.ApPeriodWeek'},
  {value: 'month', key: 'Gui.Stat.ApPeriodMonth'},
]

/* 配色照抄 module/webui/ap_chart_theme.py 的两套调色板：画布读不到 CSS 变量，
   旧界面同样在渲染时由 Python 侧按主题注入。 */
const LIGHT = {ap: '#5c6bc0', purple: '#8e24aa', yellow: '#ef6c00', asset: '#0097a7', distance: '#1976d2',
  inc: '#d32f2f', dec: '#00897b', flat: '#9aa0a6', bg: '#ffffff', grid: '#eceef3', text: '#8b8f98',
  border: '#dde0e5', series: '#6f737a', tipBg: 'rgba(255, 255, 255, 0.97)',
  tipBorder: '#d8dbe2', tipText: '#3c4043'}
const DARK = {ap: '#64b5f6', purple: '#ce93d8', yellow: '#ffd54f', asset: '#81c784', distance: '#64b5f6',
  inc: '#ef5350', dec: '#26a69a', flat: '#888888', bg: '#1a1a2e', grid: '#2a2a3e', text: '#666666',
  border: '#3a3a55', series: '#aaaaaa', tipBg: 'rgba(57, 57, 78, 0.95)',
  tipBorder: '#555555', tipText: '#dddddd'}

function parseTime(value: string): number {
  return new Date(value.replace(' ', 'T')).getTime()
}

/**
 * 体力变化图表（旧版统计页还原）。
 *
 * 行为对齐 `webapp/ap_chart.js`：默认近七天、默认只显示体力，主曲线按涨跌分段
 * 着色（涨红跌绿），辅助序列共用右侧一条 Y 轴，支持滚轮缩放与拖拽平移，
 * 「重置图表」恢复默认视图。与旧界面的差异见 `定制化修改清单.md`。
 */
export function LegacyApChart({series, onRefresh, refreshing = false}: {
  series: LegacySeries[]
  onRefresh: () => void
  refreshing?: boolean
}) {
  const {t, ui, theme} = useApp()
  const palette = theme === 'legacy-dark' ? DARK : LIGHT
  const colors = [palette.ap, palette.purple, palette.yellow, palette.asset, palette.distance]
  const [range, setRange] = useState<Range>('week')
  const [visible, setVisible] = useState<boolean[]>(DEFAULT_VISIBLE)
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<ReturnType<typeof echarts.init> | undefined>(undefined)

  const byKey = useMemo(() => {
    const map = new Map<string, LegacySeries>()
    series.forEach(item => map.set(item.key, item))
    return map
  }, [series])

  /** 按范围裁剪：近24小时是滚动窗口、近七天含今天、本月取当月全部。 */
  const ranged = useMemo(() => {
    const now = Date.now()
    const today = new Date()
    today.setHours(0, 0, 0, 0)
    const start = range === 'last24h' ? now - 24 * 60 * 60 * 1000
      : range === 'week' ? today.getTime() - 6 * 24 * 60 * 60 * 1000
        : new Date(today.getFullYear(), today.getMonth(), 1).getTime()
    const result = new Map<string, Point[]>()
    SERIES_ORDER.forEach(key => {
      const points = (byKey.get(key)?.points ?? []) as Point[]
      result.set(key, points.filter(point => {
        const at = parseTime(point.time)
        return Number.isFinite(at) && at >= start && at <= now + 60_000
      }))
    })
    return result
  }, [byKey, range])

  /* 概览行数值：体力给「(现值) 总量」与变化/最高/最低/均值（旧模板的 hover 明细），
     其余四条只给区间内最后一个值。 */
  const stats = useMemo(() => {
    const points = ranged.get('ap') ?? []
    const values = points.map(point => point.value)
    return {
      now: points.at(-1)?.apNow,
      current: points.at(-1)?.value,
      change: values.length >= 2 ? values[values.length - 1] - values[0] : 0,
      max: values.length ? Math.max(...values) : undefined,
      min: values.length ? Math.min(...values) : undefined,
      avg: values.length ? Math.round(values.reduce((sum, value) => sum + value, 0) / values.length) : undefined,
      others: Object.fromEntries(SERIES_ORDER.slice(1).map(key => [key, (ranged.get(key) ?? []).at(-1)?.value])) as Record<string, number | undefined>,
    }
  }, [ranged])

  const hasData = SERIES_ORDER.some(key => (ranged.get(key) ?? []).length > 0)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const chart = echarts.init(container, undefined, {renderer: 'canvas'})
    chartRef.current = chart
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(container)
    // 双击恢复默认视图（旧图表用重置按钮，这里两种都支持）
    chart.getZr().on('dblclick', () => chart.dispatchAction({type: 'dataZoom', start: 0, end: 100}))
    return () => {
      observer.disconnect()
      chart.dispose()
      chartRef.current = undefined
    }
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    const auxiliary = visible.slice(1).some(Boolean)

    const lineSeries = SERIES_ORDER.map((key, index) => {
      const points = ranged.get(key) ?? []
      if (index === 0) {
        // 逐段涨跌着色：第三个维度是相对前一点的变化量
        const data = points.map((point, position) => {
          const previous = position === 0 ? point.value : points[position - 1].value
          return [point.time, point.value, point.value - previous]
        })
        return {
          name: byKey.get(key)?.label ?? key, type: 'line' as const, yAxisIndex: 0,
          data, showSymbol: false, symbolSize: 6, connectNulls: true,
          lineStyle: {width: 1.6}, z: 3,
          tooltip: {valueFormatter: (value: number) => Number(value).toLocaleString()},
        }
      }
      return {
        name: byKey.get(key)?.label ?? key, type: 'line' as const, yAxisIndex: auxiliary ? 1 : 0,
        data: points.map(point => [point.time, point.value]),
        showSymbol: false, connectNulls: true, lineStyle: {width: 1.2}, z: 2,
        tooltip: {valueFormatter: (value: number) => Number(value).toLocaleString()},
      }
    }).filter((_, index) => visible[index])

    chart.setOption({
      animation: false,
      backgroundColor: 'transparent',
      grid: {left: 48, right: auxiliary ? 62 : 16, top: 16, bottom: 28},
      tooltip: {
        trigger: 'axis',
        backgroundColor: palette.tipBg,
        borderColor: palette.tipBorder,
        textStyle: {color: palette.tipText, fontSize: 12},
        axisPointer: {type: 'line', lineStyle: {color: palette.grid}},
      },
      xAxis: {
        type: 'time',
        axisLabel: {color: palette.text, fontSize: 11, hideOverlap: true},
        splitLine: {show: false},
        axisLine: {lineStyle: {color: palette.border}},
      },
      yAxis: [
        {
          type: 'value', position: 'left', scale: true,
          axisLabel: {color: palette.text, fontSize: 11},
          splitLine: {lineStyle: {color: palette.grid}},
          axisLine: {show: false}, axisTick: {show: false},
        },
        // 辅助序列共用右侧一条轴；右轴常在十万量级，旧界面同样加了千位分隔符
        ...(auxiliary ? [{
          type: 'value', position: 'right', scale: true,
          axisLabel: {color: palette.series, fontSize: 11,
            formatter: (value: number) => value.toLocaleString()},
          splitLine: {show: false}, axisLine: {show: false}, axisTick: {show: false},
        }] : []),
      ],
      dataZoom: [{type: 'inside', filterMode: 'none'}],
      // 涨跌配色：正数红、负数绿、持平灰；体力被隐藏时不参与着色（避免着色到别的序列）
      visualMap: visible[0] ? {
        show: false, dimension: 2, seriesIndex: 0,
        pieces: [{gt: 0, color: palette.inc}, {lt: 0, color: palette.dec}, {value: 0, color: palette.flat}],
      } : {show: false},
      series: lineSeries,
    }, {replaceMerge: ['series', 'yAxis']})
  }, [ranged, visible, palette, byKey])

  function reset() {
    setRange('week')
    setVisible(DEFAULT_VISIBLE)
    chartRef.current?.dispatchAction({type: 'dataZoom', start: 0, end: 100})
  }

  function toggle(key: string) {
    const index = SERIES_ORDER.indexOf(key as (typeof SERIES_ORDER)[number])
    if (index < 0) return
    setVisible(current => current.map((value, position) => position === index ? !value : value))
  }

  const rangeLabel = RANGES.find(item => item.value === range)?.key ?? RANGES[1].key

  return <div className="legacy-ap-panel">
    <div className="legacy-ap-head">
      <h2 className="legacy-stat-heading">{`体力变化 - ${t(rangeLabel)}`}</h2>
      <button type="button" className="legacy-stat-refresh" onClick={onRefresh} disabled={refreshing}
        aria-label={ui('stats.refresh')} title={ui('stats.refresh')}>
        <RefreshCw size={15}/>
      </button>
    </div>
    <div className="legacy-ap-series" style={{color: palette.series}}>
      {SERIES_ORDER.map((key, index) => <span className="legacy-ap-series-item" key={key}
        title={index === 0
          ? `变化: ${stats.change >= 0 ? '+' : ''}${Math.round(stats.change).toLocaleString()}\n最高: ${Math.round(stats.max ?? 0).toLocaleString()}\n最低: ${Math.round(stats.min ?? 0).toLocaleString()}\n均值: ${Math.round(stats.avg ?? 0).toLocaleString()}`
          : undefined}>
        {byKey.get(key)?.label ?? key}{' '}
        {index === 0
          ? <b style={{color: colors[0]}}>{stats.now != null ? `(${stats.now.toLocaleString()}) ` : ''}{Math.round(stats.current ?? 0).toLocaleString()}</b>
          : <b style={{color: colors[index]}}>{Math.round(stats.others[key] ?? 0).toLocaleString()}</b>}
      </span>)}
    </div>
    <div className="legacy-ap-legend">
      <div className="legacy-ap-legend-actions">
        {RANGES.map(item => <button key={item.value} type="button"
          className={`legacy-button${range === item.value ? ' is-primary' : ''}`}
          aria-pressed={range === item.value}
          onClick={() => setRange(item.value)}>{t(item.key)}</button>)}
      </div>
      <div className="legacy-ap-legend-items">
        {SERIES_ORDER.map((key, index) => <button key={key} type="button"
          className={`legacy-ap-legend-item${visible[index] ? '' : ' is-off'}`}
          aria-pressed={visible[index]} onClick={() => toggle(key)}>
          <span className="legacy-ap-legend-swatch" style={{background: colors[index]}}/>
          {byKey.get(key)?.label ?? key}
        </button>)}
      </div>
      <div className="legacy-ap-legend-actions is-end">
        <button type="button" className="legacy-button" onClick={reset}>{t('Gui.Stat.ResetChart')}</button>
      </div>
    </div>
    <div className="legacy-ap-canvas" style={{background: palette.bg, borderColor: palette.border}} ref={containerRef}/>
    {!hasData && <p className="legacy-muted">{t('Gui.Stat.NoApData')}</p>}
  </div>
}
