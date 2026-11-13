import { expect, test, type Locator, type Page } from '@playwright/test'
import type { Overview } from '../src/api/types'
import { routeRpc } from './rpc-route'

type Maintenance = NonNullable<Overview['maintenance']>
const announced: Maintenance = {
  state: 'announced', startsAt: '2026-11-19T09:00:00+08:00',
  checkedAt: '2026-11-18T01:00:00+00:00', error: null,
}

/** 仅替换内存模拟服务的维护快照，验证真实总览请求与事件入口。 */
async function fixture(page: Page, initial: Maintenance = announced) {
  const plans = new Map<string, Maintenance>([['demo-main', initial], ['demo-alt', {...announced, startsAt: '2026-11-20T10:00:00+09:00'}]])
  const snapshots = new Map<string, Overview>()
  const emitters: Array<(snapshot: Overview) => void> = []
  const configRequests: string[] = []
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('https://**', route => route.abort())
  await routeRpc(page, socket => {
    const server = socket.connectToServer()
    const pending = new Map<string, string>()
    let subscribed = false
    let sequence = 10000
    emitters.push(snapshot => {
      if (subscribed) socket.send(JSON.stringify({v: 1, type: 'event', topic: 'overview', seq: ++sequence, data: snapshot}))
    })
    socket.onMessage(raw => {
      const request = JSON.parse(String(raw))
      if (request.method === 'overview.get') pending.set(request.id, request.params.instance)
      if (request.method === 'events.subscribe') subscribed = request.params.topics.includes('overview')
      if (['config.get', 'config.patch'].includes(request.method)) configRequests.push(request.method)
      server.send(raw)
    })
    server.onMessage(raw => {
      const message = JSON.parse(String(raw))
      const instance = message.type === 'response' ? pending.get(message.id) : undefined
      const snapshot = instance ? message.result : message.topic === 'overview' ? message.data : undefined
      if (snapshot?.instance && Array.isArray(snapshot.tasks)) {
        snapshot.maintenance = plans.get(snapshot.instance) ?? initial
        snapshots.set(snapshot.instance, structuredClone(snapshot))
      }
      socket.send(JSON.stringify(message))
    })
  })
  return {configRequests, errors,
    update(instance: string, maintenance: Maintenance) {
      plans.set(instance, maintenance)
      const snapshot = snapshots.get(instance)
      if (!snapshot) throw new Error(`总览快照尚未加载 ${instance}`)
      for (const emit of emitters) emit({...snapshot, maintenance})
    },
  }
}

async function open(page: Page, theme = 'legacy-light', language = 'zh-CN') {
  await page.emulateMedia({reducedMotion: 'reduce'})
  await page.clock.setFixedTime(new Date('2026-11-18T00:00:00Z'))
  await page.addInitScript(({theme, language}) => {
    localStorage.setItem('azurpilot.theme', theme)
    localStorage.setItem('azurpilot.language', language)
    localStorage.setItem('azurpilot.material', 'plain')
    localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
    localStorage.setItem('azurpilot.legacy-overview-panel', 'logs')
  }, {theme, language})
  await page.goto('/#/i/demo-main/overview')
}

async function expectToolbarBounds(card: Locator) {
  const bounds = await card.evaluate(element => {
    const rect = (node: Element) => {
      const box = node.getBoundingClientRect()
      return {left: box.left, right: box.right, top: box.top, bottom: box.bottom}
    }
    return {card: rect(element), items: [
      rect(element.querySelector('.legacy-stat-title')!),
      rect(element.querySelector('.maintenance-time')!),
      rect(element.querySelector(':scope > button.button')!),
    ], viewport: innerWidth, scrollWidth: document.documentElement.scrollWidth}
  })
  for (const item of bounds.items) {
    expect(item.left).toBeGreaterThanOrEqual(bounds.card.left - 1)
    expect(item.right).toBeLessThanOrEqual(bounds.card.right + 1)
    expect(item.top).toBeGreaterThanOrEqual(bounds.card.top - 1)
    expect(item.bottom).toBeLessThanOrEqual(bounds.card.bottom + 1)
  }
  for (let i = 1; i < bounds.items.length; i++) {
    expect(bounds.items[i - 1].right).toBeLessThanOrEqual(bounds.items[i].left + 1)
  }
  expect(bounds.scrollWidth).toBeLessThanOrEqual(bounds.viewport + 1)
  expect(bounds.card.bottom - bounds.card.top).toBeGreaterThanOrEqual(38)
  expect(bounds.card.bottom - bounds.card.top).toBeLessThanOrEqual(42)
}

test('总览维护时间只读展示，刷新和聚焦不读取或保存整份配置', async ({page}) => {
  const state = await fixture(page)
  await open(page)
  const maintenance = page.locator('.maintenance-time')
  await expect(maintenance).toContainText('下次维护时间')
  await expect(maintenance.locator('time')).toHaveText('11-19 09:00')
  await expect(maintenance).toHaveAttribute('title', /2026-11-19 09:00:00\+08:00/)
  await expect(maintenance).toHaveAttribute('title', /最近查询 2026-11-18 01:00:00\+00:00/)
  await expect(maintenance.locator('button, select, [role=combobox]')).toHaveCount(0)
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  await page.reload()
  await expect(maintenance.locator('time')).toHaveText('11-19 09:00')
  expect(state.configRequests).toEqual([])
  expect(state.errors).toEqual([])
})

test('总览事件更新公告，显示区服时间并保留有效缓存的失败提示', async ({page}) => {
  const state = await fixture(page)
  await open(page)
  const maintenance = page.locator('.maintenance-time')
  await expect(maintenance.locator('time')).toHaveText('11-19 09:00')
  state.update('demo-main', {...announced, startsAt: '2026-11-20T04:30:00+09:00'})
  await expect(maintenance.locator('time')).toHaveText('11-20 04:30')
  state.update('demo-main', {...announced, state: 'error', error: '公告请求超时'})
  await expect(maintenance.locator('time')).toHaveText('11-19 09:00')
  await expect(maintenance.locator('.maintenance-time-warning')).toBeVisible()
  await expect(maintenance).toHaveAttribute('aria-label', /查询失败/)
  await expect(maintenance).toHaveAttribute('title', /公告请求超时/)
  expect(state.configRequests).toEqual([])
  expect(state.errors).toEqual([])
})

for (const [stateName, text] of [['pending', '查询中'], ['not_published', '未发布'], ['error', '查询失败']] as const) {
  test(`${stateName} 状态与未发布公告分开显示`, async ({page}) => {
    const state = await fixture(page, {state: stateName, startsAt: null, checkedAt: null, error: stateName === 'error' ? '公告请求超时' : null})
    await open(page)
    await expect(page.locator('.maintenance-time-state')).toHaveText(text)
    await expect(page.locator('.maintenance-time time')).toHaveCount(0)
    expect(state.configRequests).toEqual([])
    expect(state.errors).toEqual([])
  })
}

test('切实例后旧实例维护推送不覆盖当前实例', async ({page}) => {
  const state = await fixture(page)
  await open(page)
  await expect(page.locator('.maintenance-time time')).toHaveText('11-19 09:00')
  await page.goto('/#/i/demo-alt/overview')
  await expect(page.locator('.maintenance-time time')).toHaveText('11-20 10:00')
  state.update('demo-main', {...announced, startsAt: '2026-11-21T03:00:00+08:00'})
  await expect(page.locator('.maintenance-time time')).toHaveText('11-20 10:00')
  expect(state.configRequests).toEqual([])
  expect(state.errors).toEqual([])
})

for (const theme of ['legacy-light', 'legacy-dark']) {
  for (const width of [1440, 390]) {
    test(`${theme} ${width}px 维护时间工具栏保持连续背景且完整单行`, async ({page}, info) => {
      const state = await fixture(page)
      await page.setViewportSize({width, height: width === 390 ? 844 : 1100})
      await open(page, theme)
      const card = page.locator('.legacy-stat-card')
      await expect(card.locator('time')).toHaveText('11-19 09:00')
      await card.scrollIntoViewIfNeeded()
      await expectToolbarBounds(card)
      const title = card.locator('.legacy-stat-title')
      const transparent = 'rgba(0, 0, 0, 0)'
      await expect(card).not.toHaveCSS('background-color', transparent)
      await expect(title).toHaveCSS('background-color', transparent)
      await expect(card.locator('.maintenance-time')).toHaveCSS('background-color', transparent)
      const content = await card.locator('.maintenance-time').evaluate(element => {
        const label = element.querySelector('.maintenance-time-label')!
        const time = element.querySelector('time')!
        return {labelWidth: label.clientWidth, labelContent: label.scrollWidth,
          timeWidth: time.clientWidth, timeContent: time.scrollWidth,
          labelCenter: label.getBoundingClientRect().top + label.clientHeight / 2,
          timeCenter: time.getBoundingClientRect().top + time.clientHeight / 2}
      })
      expect(content.labelContent).toBeLessThanOrEqual(content.labelWidth + 1)
      expect(content.timeContent).toBeLessThanOrEqual(content.timeWidth + 1)
      expect(Math.abs(content.labelCenter - content.timeCenter)).toBeLessThanOrEqual(1)
      await card.screenshot({path: info.outputPath(`维护时间-${theme}-${width}.png`)})
      await title.click()
      await expect(title).toHaveCSS('background-color', transparent)
      await page.mouse.move(0, 0)
      await expect(title).toHaveText('日志')
      await expect(title).toHaveAttribute('aria-pressed', 'true')
      await expect(title).toHaveCSS('background-color', transparent)
      await expectToolbarBounds(card)
      await card.screenshot({path: info.outputPath(`维护时间统计态-${theme}-${width}.png`)})
      expect(state.errors).toEqual([])
    })
  }
}

test('新版总览不新增维护时间入口或写配置', async ({page}) => {
  const state = await fixture(page)
  await open(page, 'modern-light')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'modern-light')
  await expect(page.locator('.instance-page-grid')).toBeVisible()
  await expect(page.locator('.maintenance-time')).toHaveCount(0)
  expect(state.configRequests).toEqual([])
  expect(state.errors).toEqual([])
})

for (const language of ['en-US', 'ja-JP']) {
  test(`${language} 旧版维护时间保留完整日期时分且不重叠`, async ({page}, info) => {
    const state = await fixture(page)
    await open(page, 'legacy-light', language)
    const card = page.locator('.legacy-stat-card')
    await expect(card.locator('time')).toHaveText('11-19 09:00')
    await card.scrollIntoViewIfNeeded()
    await expectToolbarBounds(card)
    const time = await card.locator('time').evaluate(element => ({content: element.scrollWidth, visible: element.clientWidth}))
    expect(time.content).toBeLessThanOrEqual(time.visible + 1)
    await card.screenshot({path: info.outputPath(`维护时间-${language}.png`)})
    expect(state.errors).toEqual([])
  })
}
