import {expect, test, type Page, type WebSocketRoute} from '@playwright/test'
import type {LegacyStatisticsReport} from '../src/api/types'

type Request = {id: string; method: string; params: {instance?: string; month?: string | null}}

/** 报表和日志只使用内存夹具，其余接口由禁止游戏操作的测试服务提供。 */
async function fixture(page: Page, theme = 'legacy-light') {
  await page.route('https://**', route => route.abort())
  await page.emulateMedia({reducedMotion: 'reduce'})
  await page.addInitScript(theme => {
    localStorage.setItem('azurpilot.theme', theme)
    localStorage.setItem('azurpilot.material', 'plain')
    localStorage.setItem('azurpilot.language', 'zh-CN')
    localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
    localStorage.setItem('azurpilot.legacy-overview-panel', 'logs')
  }, theme)
  const requests: Request[] = []
  const errors: string[] = []
  const held: (() => void)[] = []
  let socket: WebSocketRoute
  let hold = false
  let overview: Record<string, unknown>
  page.on('pageerror', error => errors.push(error.message))
  await page.routeWebSocket('**/api/v1/ws', route => {
    socket = route
    const server = route.connectToServer()
    const pending = new Map<string, Request>()
    route.onMessage(raw => {
      const request = JSON.parse(String(raw)) as Request
      requests.push(request)
      pending.set(request.id, request)
      const send = (result: unknown) => route.send(JSON.stringify({v: 1, type: 'response', id: request.id, ok: true, result}))
      if (request.method === 'logs.get') {
        send({instance: 'testpilot', cursor: 1, reset: false,
          entries: [{id: 1, level: 'INFO', text: 'INFO 2026-10-23 12:00:00 │ 流畅度回归日志'}]})
        return
      }
      if (request.method === 'statistics.legacy') {
        const month = request.params.month ?? '2026-10'
        const now = Date.now()
        const report: LegacyStatisticsReport = {
          instance: 'testpilot', month, dashboardKeys: ['Oil'],
          apChart: {series: [{key: 'ap', label: '体力', points: Array.from({length: 120}, (_, index) => ({
            time: new Date(now - (120 - index) * 60000).toISOString(), value: 3000 + index, apNow: 100,
          }))}]},
          opsi: {summary: [], columns: [], rows: []},
          meowLoot: {month, isCurrentMonth: !request.params.month, availableMonths: ['2026-09'], lastRecord: '', columns: [], rows: []},
          shipExp: {hasData: false, columns: [], rows: []},
          commission: {
            periods: {day: {cards: [], totalCommissions: 0}, week: {cards: [], totalCommissions: 0}, month: {cards: [], totalCommissions: 0}},
            recent: {rows: [], pageSize: 10, maxPages: 5, limit: 50}, running: {available: false, scannedAt: null, items: []},
          },
        }
        if (hold) held.push(() => send(report))
        else send(report)
        return
      }
      server.send(raw)
    })
    server.onMessage(raw => {
      const message = JSON.parse(String(raw))
      if (pending.get(message.id)?.method === 'overview.get' && message.ok) overview = message.result
      // 后端定时事件不参与计数，测试按场景主动推送。
      if (message.type === 'event' && ['overview', 'logs', 'statistics'].includes(message.topic)) return
      route.send(raw)
    })
  })
  const event = (topic: string, data: unknown) => socket.send(JSON.stringify({v: 1, type: 'event', topic, data}))
  return {requests, errors, held,
    count: (method: string) => requests.filter(request => request.method === method).length,
    hold: (value: boolean) => {hold = value},
    release: () => {const send = held.shift(); if (!send) throw new Error('没有等待中的统计响应'); send()},
    statistics: () => event('statistics', {instance: 'testpilot'}),
    overview: () => event('overview', overview),
    logs: () => event('logs', {instance: 'testpilot', cursor: 2, reset: false,
      entries: [{id: 2, level: 'INFO', text: 'INFO 2026-10-23 12:00:01 │ 流畅度隐藏期间新增日志'}]}),
  }
}

for (const theme of ['legacy-light', 'legacy-dark']) test(`${theme} 日志与统计往返保留状态且先显示缓存`, async ({page}, testInfo) => {
  const f = await fixture(page, theme)
  await page.goto('/#/i/testpilot/overview')
  const search = page.getByRole('textbox', {name: '搜索日志'})
  await page.getByRole('button', {name: '展开日志筛选'}).click()
  await search.fill('流畅度')
  expect(f.count('statistics.legacy')).toBe(0)
  const toggle = page.locator('.legacy-stat-title')
  await toggle.click()
  const stats = page.locator('.statistics-legacy').filter({visible: true})
  await expect(stats.getByText('本月耄耋相接收获', {exact: true})).toBeVisible()
  await stats.getByRole('button', {name: '本月', exact: true}).first().click()
  const canvas = stats.locator('canvas').first()
  const bounds = await canvas.boundingBox()
  if (!bounds) throw new Error('统计图表没有可见尺寸')
  const fullChart = await canvas.screenshot()
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2)
  await page.keyboard.down('Control')
  await page.mouse.wheel(0, -400)
  await page.keyboard.up('Control')
  await page.mouse.move(2, 2)
  await page.waitForTimeout(180)
  const zoomedChart = await canvas.screenshot()
  expect(zoomedChart.equals(fullChart)).toBe(false)
  await stats.evaluate(element => element.setAttribute('data-cache-test', 'retained'))
  const before = f.count('statistics.legacy')
  f.hold(true)
  await toggle.click()
  await expect(search).toHaveValue('流畅度')
  await expect(page.getByText('流畅度回归日志', {exact: true}).first()).toBeVisible()
  await toggle.click()
  await expect(stats).toHaveAttribute('data-cache-test', 'retained')
  await expect(stats.getByText('本月耄耋相接收获', {exact: true})).toBeVisible({timeout: 1000})
  await expect(stats.getByRole('button', {name: '本月', exact: true}).first()).toHaveAttribute('aria-pressed', 'true')
  await expect.poll(() => f.count('statistics.legacy')).toBe(before + 1)
  await expect(canvas).toBeVisible()
  await page.mouse.move(2, 2)
  await expect.poll(async () => (await canvas.screenshot()).equals(zoomedChart)).toBe(true)
  await page.screenshot({path: testInfo.outputPath(`${theme}-面板状态保留.png`), fullPage: true})
  f.hold(false)
  f.release()
  expect(f.errors).toEqual([])
})

test('旧版总览事件不重取统计，统计请求串行合并', async ({page}) => {
  const f = await fixture(page)
  await page.goto('/#/i/testpilot/overview')
  await page.locator('.legacy-stat-title').click()
  await expect(page.getByText('本月耄耋相接收获', {exact: true})).toBeVisible()
  const statsBefore = f.count('statistics.legacy')
  const overviewBefore = f.count('overview.get')
  f.overview()
  await page.waitForTimeout(450)
  expect(f.count('statistics.legacy')).toBe(statsBefore)
  expect(f.count('overview.get')).toBe(overviewBefore)
  f.hold(true)
  f.statistics()
  await expect.poll(() => f.held.length).toBe(1)
  f.statistics()
  f.statistics()
  await page.waitForTimeout(450)
  expect(f.held).toHaveLength(1)
  expect(f.count('statistics.legacy')).toBe(statsBefore + 1)
  f.release()
  await expect.poll(() => f.count('statistics.legacy')).toBe(statsBefore + 2)
  expect(f.held).toHaveLength(1)
  f.hold(false)
  f.release()
  expect(f.errors).toEqual([])
})

test('截图页保留日志缓冲，返回恢复搜索及暂停跟随', async ({page}) => {
  const f = await fixture(page)
  await page.goto('/#/i/testpilot/overview')
  await page.getByRole('button', {name: '展开日志筛选'}).click()
  const search = page.getByRole('textbox', {name: '搜索日志'})
  await search.fill('流畅度')
  await page.getByRole('button', {name: '暂停自动滚动'}).click()
  const content = page.locator('.log-content')
  await expect(content).toContainText('流畅度回归日志')
  await page.getByRole('tab', {name: '截图', exact: true}).click()
  await expect(content).toBeHidden()
  f.logs()
  await page.waitForTimeout(150)
  await expect(content).not.toContainText('隐藏期间新增日志')
  await page.getByRole('tab', {name: '日志', exact: true}).click()
  await expect(search).toHaveValue('流畅度')
  await expect(content).toContainText('流畅度隐藏期间新增日志')
  await expect(page.getByRole('button', {name: '恢复自动滚动'})).toBeVisible()
  expect(f.errors).toEqual([])
})
