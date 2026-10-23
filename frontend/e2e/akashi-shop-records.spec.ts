import { expect, test, type Page } from '@playwright/test'
import type { AkashiShopRecordResult, LegacyStatisticsReport } from '../src/api/types'

const MONTH = '2026-10'
type Request = {id: string; method: string; params: {instance: string; scope: 'monthly' | 'history'}}

/** 所有图片和统计均为模拟资料，不读取用户纪录或执行游戏。 */
async function fixture(page: Page) {
  const requests: Request[] = []
  const pending: (() => void)[] = []
  const errors: string[] = []
  let hold = false
  let fail = false
  let imageMissing = false
  page.on('pageerror', error => errors.push(error.message))
  await page.route('https://**', route => route.abort())
  await page.routeWebSocket('**/api/v1/ws', socket => {
    const server = socket.connectToServer()
    socket.onMessage(raw => {
      const request = JSON.parse(String(raw)) as Request
      const send = (result: unknown) => socket.send(JSON.stringify({v: 1, type: 'response', id: request.id, ok: true, result}))
      if (request.method === 'statistics.legacy') {
        const report: LegacyStatisticsReport = {instance: request.params.instance, month: MONTH, dashboardKeys: [],
          apChart: {series: []}, opsi: {summary: [], columns: [], rows: []},
          meowLoot: {month: MONTH, isCurrentMonth: true, availableMonths: [], lastRecord: '', columns: [], rows: []},
          shipExp: {hasData: false, columns: [], rows: []}, commission: {
            periods: {day: {cards: [], totalCommissions: 0}, week: {cards: [], totalCommissions: 0}, month: {cards: [], totalCommissions: 0}},
            recent: {rows: [], pageSize: 10, maxPages: 5, limit: 50}, running: {available: false, scannedAt: null, items: []},
          }}
        send(report)
      } else if (request.method === 'statistics.akashiRecord') {
        requests.push(request)
        const monthly = request.params.scope === 'monthly'
        const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720"><rect width="1280" height="720" fill="#c9e5ff"/><text x="50" y="90" font-size="40">模拟完整商店画面</text></svg>'
        const result: AkashiShopRecordResult = {instance: request.params.instance, scope: request.params.scope, month: MONTH,
          record: request.params.instance === 'demo-alt' ? null : {totalAp: monthly ? 340 : 480, boxes: {'20': monthly ? 2 : 4, '50': monthly ? 2 : 4, '100': 2},
            time: '2026-10-23T10:00:00+08:00', month: monthly ? MONTH : '2026-09', server: 'cn',
            image: imageMissing ? null : `data:image/svg+xml;base64,${Buffer.from(svg).toString('base64')}`, imageMissing}}
        const respond = () => fail
          ? socket.send(JSON.stringify({v: 1, type: 'response', id: request.id, ok: false, error: {code: 'READ_FAILED', message: '模拟读取失败'}}))
          : send(result)
        if (hold) pending.push(respond)
        else respond()
      } else server.send(raw)
    })
    server.onMessage(raw => socket.send(raw))
  })
  return {requests, errors, hold: (value: boolean) => {hold = value}, fail: (value: boolean) => {fail = value},
    missingImage: () => {imageMissing = true}, reply: () => {
      const reply = pending.shift()
      if (!reply) throw new Error('没有等待中的纪录请求')
      reply()
    }}
}

async function open(page: Page, theme = 'legacy-light', route = 'statistics') {
  await page.emulateMedia({reducedMotion: 'reduce'})
  await page.addInitScript(value => {
    localStorage.setItem('azurpilot.theme', value)
    localStorage.setItem('azurpilot.language', 'zh-CN')
    localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
    localStorage.setItem('azurpilot.legacy-overview-panel', 'stats')
  }, theme)
  await page.goto(`/#/i/demo-main/${route}`)
}

test('旧版按点击懒加载月度与历史完整截图，保留图表范围和刷新按钮', async ({page}, info) => {
  const state = await fixture(page)
  await open(page)
  const panel = page.locator('.legacy-ap-panel')
  await expect(panel.getByRole('button', {name: '本月猫王', exact: true})).toBeVisible()
  expect(state.requests).toHaveLength(0)
  await panel.getByRole('button', {name: '本月', exact: true}).click()
  await expect(panel.getByRole('heading')).toHaveText('体力变化 - 本月')
  await panel.getByRole('button', {name: '本月猫王', exact: true}).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('heading')).toHaveText('本月猫王 · demo-main')
  await expect(dialog.locator('.akashi-record-summary strong')).toHaveText('340 点')
  await expect(dialog.locator('.akashi-record-boxes')).toContainText('100 点行动力箱 × 2')
  const image = dialog.getByRole('img')
  await expect(image).toBeVisible()
  await expect.poll(() => image.evaluate(element => (element as HTMLImageElement).naturalWidth)).toBe(1280)
  await page.screenshot({path: info.outputPath('本月猫王.png')})
  await dialog.getByRole('button', {name: '关闭', exact: true}).click()
  await panel.getByRole('button', {name: '历史猫王', exact: true}).click()
  await expect(dialog.locator('.akashi-record-summary strong')).toHaveText('480 点')
  await expect(dialog).toContainText('2026-09')
  await page.keyboard.press('Escape')
  await expect(panel.getByRole('button', {name: '本月', exact: true})).toHaveAttribute('aria-pressed', 'true')
  await expect(panel.getByRole('button', {name: '刷新统计', exact: true})).toBeEnabled()
  expect(state.requests.map(request => request.params.scope)).toEqual(['monthly', 'history'])
  expect(state.errors).toEqual([])
})

test('慢响应显示加载，失败可重试，截图缺失仍显示纪录', async ({page}) => {
  const state = await fixture(page)
  state.hold(true)
  await open(page)
  await page.getByRole('button', {name: '本月猫王', exact: true}).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('status')).toBeVisible()
  state.fail(true)
  await expect.poll(() => state.requests.length).toBe(1)
  state.reply()
  await expect(dialog.getByRole('alert')).toContainText('模拟读取失败')
  state.fail(false); state.hold(false); state.missingImage()
  await dialog.getByRole('button', {name: '重试', exact: true}).click()
  await expect(dialog.locator('.akashi-record-summary strong')).toHaveText('340 点')
  await expect(dialog.getByRole('status')).toHaveText('纪录已保留，但截图文件暂不可用。')
  await expect(dialog.getByRole('img')).toHaveCount(0)
  expect(state.errors).toEqual([])
})

test('切实例清除打开的弹窗，迟到响应不串图，无纪录明确提示', async ({page}) => {
  const state = await fixture(page)
  state.hold(true)
  await open(page)
  await page.getByRole('button', {name: '本月猫王', exact: true}).click()
  await expect.poll(() => state.requests.length).toBe(1)
  await page.evaluate(() => {location.hash = '#/i/demo-alt/statistics'})
  await expect(page.getByRole('dialog')).toHaveCount(0)
  state.reply(); state.hold(false)
  await page.getByRole('button', {name: '历史猫王', exact: true}).click()
  await expect(page.getByRole('dialog')).toContainText('暂无历史明石商店纪录。')
  await expect(page.getByRole('dialog').getByRole('img')).toHaveCount(0)
  expect(state.errors).toEqual([])
})

test('旧版深色窄屏按钮换行，截图完整缩放，嵌入总览同样可用', async ({page}, info) => {
  const state = await fixture(page)
  await page.setViewportSize({width: 390, height: 844})
  await open(page, 'legacy-dark', 'overview')
  await page.getByRole('button', {name: '本月猫王', exact: true}).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('img')).toBeVisible()
  const bounds = await dialog.evaluate(element => ({left: element.getBoundingClientRect().left, right: element.getBoundingClientRect().right,
    viewport: innerWidth, pageWidth: document.documentElement.scrollWidth, scrollWidth: element.scrollWidth, width: element.clientWidth}))
  expect(bounds.left).toBeGreaterThanOrEqual(0)
  expect(bounds.right).toBeLessThanOrEqual(bounds.viewport + 1)
  expect(bounds.pageWidth).toBeLessThanOrEqual(bounds.viewport + 1)
  expect(bounds.scrollWidth).toBeLessThanOrEqual(bounds.width + 1)
  await page.screenshot({path: info.outputPath('深色窄屏猫王.png')})
  expect(state.errors).toEqual([])
})

test('新版主题不增加猫王入口或自动读取截图', async ({page}) => {
  const state = await fixture(page)
  await open(page, 'light')
  await expect(page.getByRole('heading', {name: '资源统计', exact: true})).toBeVisible()
  await expect(page.getByRole('button', {name: '本月猫王', exact: true})).toHaveCount(0)
  await expect(page.getByRole('button', {name: '历史猫王', exact: true})).toHaveCount(0)
  expect(state.requests).toHaveLength(0)
  expect(state.errors).toEqual([])
})
