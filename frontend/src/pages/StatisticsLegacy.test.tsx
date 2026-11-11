// @vitest-environment jsdom
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest'
import {cleanup, fireEvent, render, screen} from '@testing-library/react'
import {MemoryRouter, Route, Routes} from 'react-router-dom'
import {StatisticsLegacy} from './StatisticsLegacy'
import {api} from '../api/client'
import type {LegacyStatisticsReport} from '../api/types'
import {translateUi} from '../i18n'

const statLabels: Record<string, string> = {
  'Gui.Stat.CommissionIncomeRecentTitle': '本月委托记录',
  'Gui.Stat.CommissionIncomeGemSuccessRate': '本月钻石委托成功率',
  'Gui.Stat.CommissionIncomeGemSuccessCount': '成功 {successes} / 已完成 {completed}',
  'Gui.Stat.CommissionIncomeGemScreenshots': '一键查看所有钻石截图',
  'Gui.Stat.CommissionIncomeAllRecords': '查看本月全部记录',
  'Gui.Stat.CommissionIncomeGemScreenshotsEmpty': '本月暂无钻石截图记录',
  'Gui.Stat.CommissionIncomeDay': '今日',
}

vi.mock('../api/client', () => ({api: {request: vi.fn(), onEvent: () => () => {}}}))
vi.mock('../components/LegacyApChart', () => ({LegacyApChart: () => null}))
vi.mock('../components/ResourceCards', () => ({ResourceCards: () => null}))
vi.mock('../app/context', () => ({
  useConnection: () => 'ready',
  useApp: () => ({
    t: (key: string) => statLabels[key] ?? key,
    ui: (key: Parameters<typeof translateUi>[1], params?: Record<string, string | number>) => translateUi('zh-CN', key, params),
    notify: vi.fn(),
  }),
}))

function report(): LegacyStatisticsReport {
  const summary = {cards: [{name: 'Gem', index: 1, color: '#d762ad', labelKey: 'Gui.Stat.CommissionIncomeItemGem', total: 390, count: 13, avg: 30}], totalCommissions: 65}
  return {
    instance: 'test', month: '2026-11', dashboardKeys: [], apChart: {series: []},
    opsi: {summary: [], columns: [], rows: []},
    meowLoot: {month: '2026-11', isCurrentMonth: true, availableMonths: [], lastRecord: '-', columns: [], rows: []},
    shipExp: {hasData: false, columns: [], rows: []},
    commission: {
      periods: {day: {...summary, cards: [{...summary.cards[0], total: 30, count: 1}]}, week: summary, month: summary},
      gemSuccess: {successes: 13, completed: 20, rate: 65},
      recent: {pageSize: 10, maxPages: 7, limit: null, rows: Array.from({length: 65}, (_, index) => ({
        time: `11-11 12:${String(index).padStart(2, '0')}`,
        name: index === 1 ? null : `委托${index}`,
        items: [{name: index % 5 === 0 ? 'Gem' : 'Coin', labelKey: 'Gui.Stat.CommissionIncomeItemGem', color: '#d762ad', icon: 1, amount: index === 10 ? 0 : 30}],
        // 模拟旧响应仍带普通委托截图，界面也应按钻石掉落判断。
        screenshot: index === 5 ? null : `/shot/${index}.png`,
      }))},
      running: {available: true, scannedAt: null, items: []},
    },
  }
}

function openReport(data = report()) {
  vi.mocked(api.request).mockResolvedValue(data)
  return render(<MemoryRouter initialEntries={['/i/test/statistics']}><Routes>
    <Route path="/i/:instance/statistics" element={<StatisticsLegacy embedded/>}/>
  </Routes></MemoryRouter>)
}

describe('旧主题军事委托记录', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    Object.defineProperty(document, 'hidden', {configurable: true, value: false})
  })
  afterEach(cleanup)

  it('当月记录可翻到第七页，钻石筛选和返回全部都回到第一页', async () => {
    const {container} = openReport()
    await screen.findByText('本月委托记录')
    expect(container.querySelectorAll('.legacy-recent-row')).toHaveLength(10)
    expect(container.querySelectorAll('.legacy-shot-link')).toHaveLength(1)
    expect(screen.queryByText('未识别')).toBeNull()

    fireEvent.click(screen.getByRole('button', {name: '7'}))
    expect(screen.getByText('委托60')).toBeTruthy()
    expect(container.querySelectorAll('.legacy-recent-row')).toHaveLength(5)

    fireEvent.click(screen.getByRole('button', {name: '一键查看所有钻石截图'}))
    expect(screen.getByText('委托0')).toBeTruthy()
    expect(screen.queryByText('委托5')).toBeNull()
    expect(screen.queryByText('委托10')).toBeNull()
    expect(container.querySelectorAll('.legacy-recent-row')).toHaveLength(10)
    expect(container.querySelectorAll('.legacy-shot-link')).toHaveLength(10)
    expect(screen.queryByRole('button', {name: '7'})).toBeNull()

    fireEvent.click(screen.getByRole('button', {name: '2'}))
    expect(container.querySelectorAll('.legacy-recent-row')).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', {name: '查看本月全部记录'}))
    expect(screen.getByText('委托0')).toBeTruthy()
    expect(container.querySelectorAll('.legacy-recent-row')).toHaveLength(10)
  })

  it('成功率固定使用月度统计，切换收益区间继续显示成功与已完成次数', async () => {
    const {container} = openReport()
    await screen.findByText('本月钻石委托成功率')
    const success = container.querySelector('.legacy-commission-gem-success')!
    expect(success.textContent).toContain('65.00%')
    expect(success.textContent).toContain('成功 13 / 已完成 20')
    fireEvent.click(screen.getByRole('button', {name: '今日'}))
    expect(screen.getByText('+30')).toBeTruthy()
    expect(success.textContent).toContain('65.00%')
    expect(success.textContent).toContain('成功 13 / 已完成 20')
  })

  it('大量记录保留首尾和附近页码，末页始终可达', async () => {
    const data = report()
    const firstRow = data.commission.recent.rows[0]
    data.commission.recent.rows = Array.from({length: 360}, (_, index) => ({...firstRow, name: `委托${index}`}))
    data.commission.recent.maxPages = 36
    const {container} = openReport(data)
    await screen.findByText('本月委托记录')
    expect(container.querySelectorAll('.legacy-pagination button').length).toBeLessThan(10)
    fireEvent.click(screen.getByRole('button', {name: '36'}))
    expect(screen.getByText('委托359')).toBeTruthy()
    expect(screen.getByRole('button', {name: '1'})).toBeTruthy()
    expect(screen.getByRole('button', {name: '36'}).getAttribute('aria-pressed')).toBe('true')
  })

  it('空分母显示占位符，空钻石筛选可回到全部记录', async () => {
    const data = report()
    data.commission.gemSuccess = {successes: 0, completed: 0, rate: null}
    data.commission.recent.rows = data.commission.recent.rows.filter(row => row.items[0].name !== 'Gem')
    const {container} = openReport(data)
    await screen.findByText('本月委托记录')
    const success = container.querySelector('.legacy-commission-gem-success')!
    expect(success.querySelector('strong')?.textContent).toBe('-')
    expect(success.textContent).toContain('成功 0 / 已完成 0')
    fireEvent.click(screen.getByRole('button', {name: '一键查看所有钻石截图'}))
    expect(screen.getByText('本月暂无钻石截图记录')).toBeTruthy()
    expect(container.querySelectorAll('.legacy-recent-row')).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', {name: '查看本月全部记录'}))
    expect(container.querySelectorAll('.legacy-recent-row')).toHaveLength(10)
  })
})
