import {expect, test, type Page, type WebSocketRoute} from '@playwright/test'
import type {Account, Fees, Market, Meta, Rules, Stock, StockDetail} from '../src/stock/types'

type Request = {id: string; method: string; params: {instance?: string; path?: string; method?: string; etag?: string; topics?: string[]}}

/** 只替换交易所只读响应，不访问交易所、开户、下单或启动真实游戏。 */
async function isolatedExchange(page: Page) {
  const requests: Request[] = []
  const errors: string[] = []
  const held: Array<{kind: 'overview' | 'stock'; send: () => void}> = []
  const sockets: WebSocketRoute[] = []
  const gate = {overview: false, stock: false, oil: 14200}
  const now = Date.now()
  const fees: Fees = {commission: 0, stamp: 0, levy: 0, borrow: 0, financing: 0}
  const quote = {price: 10000, previous: 9800, observedAt: Math.floor(now / 1000), uploadedAt: Math.floor(now / 1000)}
  const stock: Stock = {id: 2, symbol: 'MM000002', username: '验收证券', quote, stale: false, disabled: false}
  const rules: Rules = {
    id: 'test', name: '隔离验收', description: '', commissionPPM: 0, minCommission: 0,
    stampPPM: 0, stampSide: 'sell', stampRound: 1, levyPPM: 0, borrowAnnualPPM: 0,
    financingAnnualPPM: 0, imrPPM: 500000, mmrPPM: 300000, lotSize: 1,
    sellDelayDays: 0, cashDelayDays: 0, timezone: 'Asia/Shanghai', sessions: [],
    weekdaysOnly: false, holidays: [], quoteTTLSeconds: 86400, source: 'test',
  }
  const market: Market = {
    revision: 1, serverTime: Math.floor(now / 1000),
    season: {id: '2026-10', startsAt: Math.floor(now / 1000) - 86400, endsAt: Math.floor(now / 1000) + 86400, settled: false, revenue: fees},
    rules, open: true, reason: '', stocks: [stock], rankings: [], lifetimeRevenue: fees, participants: 1,
  }
  const meta: Meta = {name: '茗喵证券交易所', domain: 'test.invalid', mock: true, allowedOrigins: [], initialCash: 2000000000, noticeVersion: 'test'}
  function stockResult(request: Request) {
    const instance = request.params.instance ?? ''
    if (request.method === 'stock.status') return {
      url: 'https://test.invalid', instance, instanceId: instance, bindingKey: 'test',
      bound: true, boundUsername: `验收账号-${instance}`, authenticated: true, message: '',
      lastObservedAt: quote.observedAt, snapshot: {instance, actionPoints: 100, observedAt: quote.observedAt},
    }
    if (request.params.method !== 'GET') throw new Error('页面切换不应发起交易写入')
    const path = request.params.path ?? ''
    let data: unknown
    if (path === '/meta') data = meta
    else if (path === '/market') data = market
    else if (path === '/account') {
      data = {
        player: {id: 1, username: `验收账号-${instance}`, identityCode: 'TEST', joinedAt: quote.observedAt,
          cash: 2000000000, quote, positions: {}, orders: [], realized: 0, fees, disabled: false},
        equity: 2000000000, available: 2000000000, frozen: 0, shortLiability: 0,
        initialMargin: 0, maintenanceMargin: 0, unsettled: 0, borrowAccrued: 0,
      } satisfies Account
    } else if (path.startsWith('/stocks/')) {
      const query = new URL(path, 'https://test.invalid').searchParams
      data = {
        stock, period: query.get('period') as StockDetail['period'], month: query.get('month') ?? '', day: '', displayFrom: now - 3600000,
        bars: Array.from({length: 30}, (_, index) => ({time: now - (30 - index) * 60000, open: 9800 + index * 5,
          high: 10100 + index * 5, low: 9700 + index * 5, close: 10000 + index * 5,
          samples: 1, volume: 100, turnover: 1000000, buyVolume: 50, sellVolume: 50})),
        trades: [], pending: [],
        summary: {day: '2026-10-04', open: 9800, high: 10300, low: 9700, previousClose: 9800, volume: 3000, turnover: 30000000, buyVolume: 1500, sellVolume: 1500},
        coverage: {count: 30, firstObservedAt: now - 1800000, lastObservedAt: now, reconciledAt: now},
      } satisfies StockDetail
    } else throw new Error(`未预期的交易所请求：${path}`)
    const etag = `"test-${path}"`
    return {status: request.params.etag === etag ? 304 : 200, data: request.params.etag === etag ? null : data, etag, serverTime: quote.observedAt}
  }
  function logs(instance: string) {
    return {instance, cursor: 1, reset: false, entries: [{id: 1, level: 'INFO', text: 'INFO 2026-10-04 12:00:00 │ 交互缓存验收日志'}]}
  }
  page.on('pageerror', error => errors.push(error.message))
  await page.routeWebSocket('**/api/v1/ws', socket => {
    const server = socket.connectToServer()
    sockets.push(socket)
    const pending = new Map<string, Request>()
    const deliver = (kind: 'overview' | 'stock' | undefined, send: () => void) => {
      if (kind && gate[kind]) held.push({kind, send})
      else send()
    }
    socket.onMessage(raw => {
      const request = JSON.parse(String(raw)) as Request
      requests.push(request)
      pending.set(request.id, request)
      if (request.method.startsWith('stock.')) {
        const result = stockResult(request)
        deliver('stock', () => socket.send(JSON.stringify({v: 1, type: 'response', id: request.id, ok: true, result})))
      } else server.send(raw)
    })
    server.onMessage(raw => {
      const message = JSON.parse(String(raw))
      const request = pending.get(message.id)
      const isOverview = request && ['overview.get', 'logs.get', 'statistics.legacy'].includes(request.method)
        || message.type === 'event' && ['overview', 'logs', 'statistics', 'preview'].includes(message.topic)
      deliver(isOverview ? 'overview' : undefined, () => {
        const value = structuredClone(message)
        const overview = request?.method === 'overview.get' ? value.result : value.topic === 'overview' ? value.data : null
        if (overview?.instance === 'testpilot') {
          const oil = overview.resources?.find((resource: {name: string}) => resource.name === 'Oil')
          if (oil) oil.value = gate.oil
        }
        if (request?.method === 'logs.get' && value.ok) value.result = logs(request.params.instance ?? '')
        if (value.topic === 'logs') value.data = logs(value.data.instance)
        socket.send(JSON.stringify(value))
      })
    })
  })
  return {
    gate, requests, errors, sockets,
    count: (method: string) => requests.filter(request => request.method === method).length,
    release(kind: 'overview' | 'stock') {
      gate[kind] = false
      const ready = held.filter(item => item.kind === kind)
      for (let index = held.length - 1; index >= 0; index--) if (held[index].kind === kind) held.splice(index, 1)
      ready.forEach(item => item.send())
    },
  }
}

async function preferences(page: Page, theme: string, panel = 'logs') {
  await page.emulateMedia({reducedMotion: 'reduce'})
  await page.addInitScript(({theme, panel}) => {
    localStorage.setItem('azurpilot.theme', theme)
    localStorage.setItem('azurpilot.language', 'zh-CN')
    localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
    localStorage.setItem('azurpilot.legacy-overview-panel', panel)
  }, {theme, panel})
}

for (const theme of ['light', 'legacy-light', 'extreme']) {
  test(`${theme} 总览与交易所往返立即恢复内容，隐藏页暂停刷新`, async ({page}, testInfo) => {
    const fixture = await isolatedExchange(page)
    await preferences(page, theme)
    await page.clock.install()
    await page.goto('/#/i/testpilot/overview')
    const resources = page.locator('#main-content .resource-grid')
    await expect(resources.getByText('14,200', {exact: true})).toBeVisible()
    await resources.evaluate(element => element.setAttribute('data-test-cache', 'overview'))
    await page.getByRole('button', {name: '展开日志筛选', exact: true}).click()
    const search = page.getByRole('textbox', {name: '搜索日志', exact: true})
    await search.fill('交互缓存')
    await expect(page.getByText('交互缓存验收日志', {exact: true}).first()).toBeVisible()

    const enter = page.locator('.primary-nav').getByRole('link', {name: '茗喵证券交易所', exact: true})
    await enter.click()
    const exchange = page.locator('#stock-main-content .exchange')
    await expect(exchange).toBeVisible()
    await expect(page.locator('.financial-chart-state')).toHaveCount(0)
    await exchange.evaluate(element => element.setAttribute('data-test-cache', 'exchange'))
    await page.getByRole('textbox', {name: '搜索证券', exact: true}).fill('验收')
    await page.getByRole('button', {name: '日K', exact: true}).click()
    await expect(page.locator('.financial-chart-state')).toHaveCount(0)
    await page.getByRole('button', {name: '放大走势图', exact: true}).click()
    const range = await page.getByRole('note', {name: '图表可视范围'}).getAttribute('data-start')
    expect(Number(range)).toBeGreaterThan(0)

    fixture.gate.overview = true
    const overviewBefore = fixture.count('overview.get')
    await page.getByRole('link', {name: '返回总览', exact: true}).click()
    await expect(resources).toBeVisible({timeout: 1000})
    await expect(resources).toHaveAttribute('data-test-cache', 'overview')
    await expect(resources.getByText('14,200', {exact: true})).toBeVisible()
    await expect(search).toHaveValue('交互缓存')
    await expect(page.getByText('交互缓存验收日志', {exact: true}).first()).toBeVisible()
    await expect.poll(() => fixture.count('overview.get')).toBeGreaterThan(overviewBefore)
    const stockBefore = fixture.requests.filter(request => request.method.startsWith('stock.')).length
    await page.clock.fastForward(16000)
    expect(fixture.requests.filter(request => request.method.startsWith('stock.')).length).toBe(stockBefore)
    await page.screenshot({path: testInfo.outputPath(`${theme}-缓存总览.png`), fullPage: true})

    fixture.gate.stock = true
    const statusBefore = fixture.count('stock.status')
    await enter.click()
    await expect(exchange).toBeVisible({timeout: 1000})
    await expect(exchange).toHaveAttribute('data-test-cache', 'exchange')
    await expect(page.getByRole('textbox', {name: '搜索证券', exact: true})).toHaveValue('验收')
    await expect(page.getByRole('button', {name: '日K', exact: true})).toHaveAttribute('aria-pressed', 'true')
    await expect(page.locator('.financial-chart-state')).toHaveCount(0)
    await expect(page.getByRole('note', {name: '图表可视范围'})).toHaveAttribute('data-start', range!)
    await expect.poll(() => fixture.count('stock.status')).toBeGreaterThan(statusBefore)
    const hiddenOverview = fixture.count('overview.get')
    const hiddenStatistics = fixture.count('statistics.legacy')
    await page.clock.fastForward(7000)
    expect(fixture.count('overview.get')).toBe(hiddenOverview)
    expect(fixture.count('statistics.legacy')).toBe(hiddenStatistics)
    expect(fixture.requests.filter(request => request.method === 'events.subscribe').at(-1)?.params.topics).toEqual(['instances'])
    await expect(page.locator('#main-content')).toHaveCount(1)
    await expect(page.locator('#stock-main-content')).toHaveCount(1)
    await expect(page.locator('#main-content')).toBeHidden()
    await page.screenshot({path: testInfo.outputPath(`${theme}-缓存交易所.png`), fullPage: true})

    fixture.gate.oil = 15200
    fixture.release('stock')
    await page.getByRole('link', {name: '返回总览', exact: true}).click()
    fixture.release('overview')
    await expect(resources.getByText('15,200', {exact: true})).toBeVisible()
    expect(fixture.errors).toEqual([])
  })
}

test('旧版内嵌统计往返保留图表与选择，截图订阅随可见页面恢复', async ({page}, testInfo) => {
  const fixture = await isolatedExchange(page)
  await preferences(page, 'legacy-light', 'stats')
  await page.goto('/#/i/testpilot/overview')
  const statistics = page.locator('#main-content .legacy-stats')
  await expect(statistics).toBeVisible()
  await statistics.evaluate(element => element.setAttribute('data-test-cache', 'statistics'))
  const range = statistics.getByRole('button', {name: '近24小时', exact: true})
  await range.click()
  await page.locator('.primary-nav').getByRole('link', {name: '茗喵证券交易所', exact: true}).click()
  await expect(page.locator('.exchange')).toBeVisible()
  fixture.gate.overview = true
  await page.getByRole('link', {name: '返回总览', exact: true}).click()
  await expect(statistics).toBeVisible({timeout: 1000})
  await expect(statistics).toHaveAttribute('data-test-cache', 'statistics')
  await expect(range).toHaveAttribute('aria-pressed', 'true')
  await expect(statistics.locator('.legacy-ap-card')).toHaveCSS('background-color', 'rgb(255, 255, 255)')
  await page.screenshot({path: testInfo.outputPath('旧版缓存统计.png'), fullPage: true})
  fixture.release('overview')
  await page.getByRole('button', {name: '切换到日志', exact: true}).click()
  await page.getByRole('tab', {name: '截图', exact: true}).click()
  await expect.poll(() => fixture.requests.filter(request => request.method === 'events.subscribe').at(-1)?.params.topics).toContain('preview')
  await page.locator('.primary-nav').getByRole('link', {name: '茗喵证券交易所', exact: true}).click()
  await expect(page.locator('.exchange')).toBeVisible()
  await expect.poll(() => fixture.requests.filter(request => request.method === 'events.subscribe').at(-1)?.params.topics).toEqual(['instances'])
  await page.getByRole('link', {name: '返回总览', exact: true}).click()
  await expect(page.getByRole('tab', {name: '截图', exact: true})).toHaveAttribute('aria-selected', 'true')
  await expect.poll(() => fixture.requests.filter(request => request.method === 'events.subscribe').at(-1)?.params.topics).toContain('preview')
  expect(fixture.errors).toEqual([])
})

test('切换实例销毁旧页面缓存，新实例独立读取总览和交易身份', async ({page}) => {
  const fixture = await isolatedExchange(page)
  await preferences(page, 'light')
  await page.goto('/#/i/testpilot/overview')
  await expect(page.getByText('14,200', {exact: true})).toBeVisible()
  await page.locator('.primary-nav').getByRole('link', {name: '茗喵证券交易所', exact: true}).click()
  await expect(page.getByRole('button', {name: '查看我的身份识别码'})).toHaveText('验收账号-testpilot')
  await page.getByRole('textbox', {name: '搜索证券', exact: true}).fill('仅此实例')
  await page.getByRole('link', {name: '返回总览', exact: true}).click()
  await page.getByRole('button', {name: '切换实例', exact: true}).click()
  await page.getByRole('menuitem', {name: '创建实例', exact: true}).click()
  const name = `cache_${Date.now()}`
  await page.getByLabel('实例名称').fill(name)
  await page.getByRole('dialog').getByRole('button', {name: '创建实例', exact: true}).click()
  await expect(page).toHaveURL(new RegExp(`/i/${name}/overview$`))
  await expect(page.locator('#main-content .resource-grid')).toBeVisible()
  await expect(page.locator('#main-content').getByText('14,200', {exact: true})).toHaveCount(0)
  await expect(page.locator('#stock-main-content')).toHaveCount(0)
  await page.locator('.primary-nav').getByRole('link', {name: '茗喵证券交易所', exact: true}).click()
  await expect(page.getByRole('button', {name: '查看我的身份识别码'})).toHaveText(`验收账号-${name}`)
  await expect(page.getByRole('textbox', {name: '搜索证券', exact: true})).toHaveValue('')
  expect(fixture.requests.some(request => request.method === 'overview.get' && request.params.instance === name)).toBe(true)
  expect(fixture.requests.some(request => request.method === 'stock.status' && request.params.instance === name)).toBe(true)
  expect(fixture.errors).toEqual([])
})

test('直接打开交易所路由也能进入总览并再次恢复交易页面', async ({page}) => {
  const fixture = await isolatedExchange(page)
  await preferences(page, 'light')
  await page.goto('/#/i/testpilot/stock-exchange')
  await expect(page.locator('.exchange')).toBeVisible()
  expect(fixture.count('overview.get')).toBe(0)
  await page.getByRole('link', {name: '返回总览', exact: true}).click()
  await expect(page.getByText('14,200', {exact: true})).toBeVisible()
  fixture.gate.stock = true
  await page.locator('.primary-nav').getByRole('link', {name: '茗喵证券交易所', exact: true}).click()
  await expect(page.locator('.exchange')).toBeVisible({timeout: 1000})
  expect(fixture.errors).toEqual([])
})
