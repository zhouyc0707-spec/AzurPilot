import {expect, test, type Page, type WebSocketRoute} from '@playwright/test'
import type {StockExchangeStatus} from '../src/api/types'

type Request = {id: string; method: string; params: {instance?: string; path?: string; method?: string; etag?: string; topics?: string[]}}

/** 只替换股票接入只读响应，不访问交易所、开户、下单或启动真实游戏。 */
async function isolatedExchange(page: Page) {
  // 阻止外部埋点脚本受虚拟时钟影响，也确保隔离验收不会访问真实交易服务。
  await page.route(/^https:\/\//, route => route.abort())
  const requests: Request[] = []
  const errors: string[] = []
  const held: Array<{kind: 'overview' | 'stock'; send: () => void}> = []
  const sockets: WebSocketRoute[] = []
  const gate = {overview: false, stock: false, oil: 14200}
  function stockResult(request: Request): StockExchangeStatus {
    if (request.method !== 'stock.status') throw new Error('页面切换不应注册、重建或提交交易')
    const instance = request.params.instance ?? ''
    return {url: 'https://stock.azurpilot.com', instance, instanceId: instance,
      registered: false, deleted: false, bound: false, stock: null, bindingCode: '',
      message: '隔离验收：尚未注册', lastObservedAt: 0}
  }
  function logs(instance: string) {
    return {instance, cursor: 1, reset: false, entries: [{id: 1, level: 'INFO', text: 'INFO 2026-10-04 12:00:00 │ 交互缓存验收日志'}]}
  }
  page.on('pageerror', error => errors.push(error.stack ?? error.message))
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
      } else if (request.method === 'events.subscribe' && request.params.topics?.includes('stock')) {
        // 股票事件由本夹具发送，不让隔离 Python 后端连接公网交易所。
        server.send(JSON.stringify({...request, params: {...request.params, topics: request.params.topics.filter(topic => topic !== 'stock')}}))
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
    notifyStock() {
      for (const socket of sockets) socket.send(JSON.stringify({v: 1, type: 'event', topic: 'stock', seq: requests.length + 1000,
        data: {instance: requests.filter(request => request.method === 'stock.status').at(-1)?.params.instance}}))
    },
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
    // 首次访问包含后端模块冷加载；缓存恢复仍使用下方的一秒硬性验收。
    await expect(resources.getByText('14,200', {exact: true})).toBeVisible({timeout: 15000})
    await resources.evaluate(element => element.setAttribute('data-test-cache', 'overview'))
    await page.getByRole('button', {name: '展开日志筛选', exact: true}).click()
    const search = page.getByRole('textbox', {name: '搜索日志', exact: true})
    await search.fill('交互缓存')
    await expect(page.getByText('交互缓存验收日志', {exact: true}).first()).toBeVisible()

    const enter = page.locator('.primary-nav').getByRole('link', {name: '茗喵证券交易所', exact: true})
    await enter.click()
    const exchange = page.locator('#stock-main-content .stock-instance-page')
    await expect(exchange).toBeVisible()
    await expect(page.locator('.app-shell')).toBeVisible()
    await exchange.evaluate(element => element.setAttribute('data-test-cache', 'exchange'))
    await page.getByLabel('股票用户名', {exact: true}).fill('缓存验收')
    await page.getByRole('checkbox', {name: /我已了解股票名称/}).check()

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
    await expect(page.getByLabel('股票用户名', {exact: true})).toHaveValue('缓存验收')
    await expect(page.getByRole('checkbox', {name: /我已了解股票名称/})).toBeChecked()
    await expect.poll(() => fixture.count('stock.status')).toBeGreaterThan(statusBefore)
    const hiddenOverview = fixture.count('overview.get')
    const hiddenStatistics = fixture.count('statistics.legacy')
    await page.clock.fastForward(7000)
    expect(fixture.count('overview.get')).toBe(hiddenOverview)
    expect(fixture.count('statistics.legacy')).toBe(hiddenStatistics)
    expect(fixture.requests.filter(request => request.method === 'events.subscribe').at(-1)?.params.topics).toEqual(['instances', 'stock', 'notifications', 'launcher'])
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
  await expect(page.locator('.stock-instance-page')).toBeVisible()
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
  await expect(page.locator('.stock-instance-page')).toBeVisible()
  await expect.poll(() => fixture.requests.filter(request => request.method === 'events.subscribe').at(-1)?.params.topics).toEqual(['instances', 'stock', 'notifications', 'launcher'])
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
  await expect(page.locator('.stock-instance-page')).toContainText('实例：testpilot')
  await page.getByLabel('股票用户名', {exact: true}).fill('仅此实例')
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
  await expect(page.locator('.stock-instance-page')).toContainText(`实例：${name}`)
  await expect(page.getByLabel('股票用户名', {exact: true})).toHaveValue('')
  expect(fixture.requests.some(request => request.method === 'overview.get' && request.params.instance === name)).toBe(true)
  expect(fixture.requests.some(request => request.method === 'stock.status' && request.params.instance === name)).toBe(true)
  expect(fixture.errors).toEqual([])
})

test('直接打开交易所路由也能进入总览并再次恢复交易页面', async ({page}) => {
  const fixture = await isolatedExchange(page)
  await preferences(page, 'light')
  await page.goto('/#/i/testpilot/stock-exchange')
  await expect(page.locator('.stock-instance-page')).toBeVisible()
  expect(fixture.count('overview.get')).toBe(0)
  await page.getByRole('link', {name: '返回总览', exact: true}).click()
  await expect(page.getByText('14,200', {exact: true})).toBeVisible()
  fixture.gate.stock = true
  await page.locator('.primary-nav').getByRole('link', {name: '茗喵证券交易所', exact: true}).click()
  await expect(page.locator('.stock-instance-page')).toBeVisible({timeout: 1000})
  expect(fixture.errors).toEqual([])
})

for (const theme of ['light', 'legacy-light', 'legacy-dark']) {
  test(`${theme} 股票接入沿用外壳与主题，窄屏无横向溢出并保留滚动`, async ({page}, testInfo) => {
    const fixture = await isolatedExchange(page)
    await preferences(page, theme)
    await page.goto('/#/i/testpilot/stock-exchange')
    await expect(page.getByLabel('股票用户名', {exact: true})).toBeVisible()
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
    await expect(page.locator('.app-shell')).toBeVisible()
    await expect(page.locator('.app-shell')).not.toHaveClass(/with-rail/)
    if (theme.startsWith('legacy-')) await expect(page.locator('.legacy-topbar-title')).toHaveText('茗喵证券交易所')
    await page.setViewportSize({width: 1000, height: 500})
    const scroller = page.locator('#stock-main-content')
    await scroller.evaluate(element => {element.scrollTop = 180})
    const scroll = await scroller.evaluate(element => element.scrollTop)
    expect(scroll).toBeGreaterThan(0)
    // 直接改路由，避免 Playwright 为点击页首链接自动滚动影响检查。
    await page.evaluate(() => {location.hash = '#/i/testpilot/overview'})
    await expect(page.locator('#main-content .resource-grid')).toBeVisible()
    await page.evaluate(() => {location.hash = '#/i/testpilot/stock-exchange'})
    await expect(scroller).toBeVisible()
    await expect.poll(() => scroller.evaluate(element => element.scrollTop)).toBe(scroll)
    await page.setViewportSize({width: 390, height: 900})
    await expect(page.getByLabel('股票用户名', {exact: true})).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await scroller.evaluate(element => {element.scrollTop = 0})
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.screenshot({path: testInfo.outputPath(`${theme}-股票接入窄屏.png`), fullPage: true})
    expect(fixture.errors).toEqual([])
  })
}
