import { expect, test, type Page } from '@playwright/test'
import type { Config, Value } from '../src/api/types'

const PATH = 'Exercise.Exercise.MaintenanceWeekday'
const DAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
type Request = {id: string; method: string; params: {instance: string; changes?: {path: string; value: Value}[]}}

/** 配置读写仅作用于本页夹具内存；其他请求复用模拟服务，不启动游戏任务。 */
async function fixture(page: Page) {
  const configs = new Map<string, Config>([
    ['demo-main', {instance: 'demo-main', revision: '1', values: {
      Exercise: {Exercise: {MaintenanceWeekday: 'thu', MaintenanceAdvance: true}, Scheduler: {Enable: true, NextRun: '2026-10-24 11:00:00'}},
      Hard: {Hard: {RunAt18: true}, Scheduler: {Enable: true, NextRun: '2026-10-24 00:00:00'}},
    }}],
    ['demo-alt', {instance: 'demo-alt', revision: '1', values: {
      Exercise: {Exercise: {MaintenanceWeekday: 'mon', MaintenanceAdvance: false}, Scheduler: {Enable: false, NextRun: '2026-10-24 11:00:00'}},
      Hard: {Hard: {RunAt18: false}, Scheduler: {Enable: false, NextRun: '2026-10-24 00:00:00'}},
    }}],
  ])
  const reads: Request[] = []
  const writes: Request[] = []
  const pendingReads: (() => void)[] = []
  const pendingWrites: (() => void)[] = []
  const errors: string[] = []
  let failRead = false
  let failWrite = false
  let holdRead = ''
  let holdWrite = false
  page.on('pageerror', error => errors.push(error.message))
  await page.route('https://**', route => route.abort())
  await page.routeWebSocket('**/api/v1/ws', socket => {
    const server = socket.connectToServer()
    socket.onMessage(raw => {
      const request = JSON.parse(String(raw)) as Request
      if (!['config.get', 'config.patch'].includes(request.method)) {server.send(raw); return}
      const send = (result: Config) => socket.send(JSON.stringify({v: 1, type: 'response', id: request.id, ok: true, result}))
      const reject = (message: string) => socket.send(JSON.stringify({v: 1, type: 'response', id: request.id, ok: false, error: {code: 'READ_FAILED', message}}))
      const config = configs.get(request.params.instance)
      if (!config) throw new Error(`未配置模拟实例：${request.params.instance}`)
      if (request.method === 'config.get') {
        reads.push(request)
        const snapshot = structuredClone(config)
        const respond = () => failRead ? reject('模拟维护日读取失败') : send(snapshot)
        if (holdRead === request.params.instance) pendingReads.push(respond)
        else respond()
        return
      }
      writes.push(request)
      if (failWrite) {reject('模拟维护日保存失败'); return}
      for (const change of request.params.changes ?? []) {
        const [task, group, key] = change.path.split('.')
        config.values[task][group][key] = change.value
      }
      config.revision = String(Number(config.revision) + 1)
      const snapshot = structuredClone(config)
      if (holdWrite) pendingWrites.push(() => send(snapshot))
      else send(snapshot)
    })
    server.onMessage(raw => socket.send(raw))
  })
  const release = (pending: (() => void)[]) => {
    const respond = pending.shift()
    if (!respond) throw new Error('没有待释放的维护日响应')
    respond()
  }
  return {configs, reads, writes, errors,
    failRead: (value: boolean) => {failRead = value}, failWrite: (value: boolean) => {failWrite = value},
    holdRead: (instance: string) => {holdRead = instance}, holdWrite: (value: boolean) => {holdWrite = value},
    releaseRead: () => release(pendingReads), releaseWrite: () => release(pendingWrites),
    pendingReads: () => pendingReads.length, pendingWrites: () => pendingWrites.length,
  }
}

async function open(page: Page, theme = 'legacy-light', language = 'zh-CN') {
  await page.emulateMedia({reducedMotion: 'reduce'})
  await page.addInitScript(({theme, language}) => {
    localStorage.setItem('azurpilot.theme', theme)
    localStorage.setItem('azurpilot.language', language)
    localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
    localStorage.setItem('azurpilot.legacy-overview-panel', 'logs')
  }, {theme, language})
  await page.goto('/#/i/demo-main/overview')
}

const control = (page: Page) => page.getByRole('combobox', {name: '维护日', exact: true})
const status = (page: Page) => page.locator('[id="overview-maintenance-weekday-status"]')
async function choose(page: Page, label: string) {
  await control(page).click()
  await page.getByRole('option', {name: label, exact: true}).click()
}

test('总览维护日提供七天选项，只保存共享星期，刷新后与演习配置一致', async ({page}) => {
  const state = await fixture(page)
  const before = structuredClone(state.configs.get('demo-main')!)
  await open(page)
  await expect(control(page)).toHaveText('周四')
  await control(page).click()
  await expect(page.getByRole('option')).toHaveText(DAYS)
  await expect(page.getByRole('option', {name: '周四', exact: true})).toHaveAttribute('aria-selected', 'true')
  await page.getByRole('option', {name: '周二', exact: true}).click()
  await expect(status(page)).toHaveText('已保存')
  expect(state.writes.map(request => request.params)).toEqual([{instance: 'demo-main', changes: [{path: PATH, value: 'tue'}]}])
  expect(state.configs.get('demo-main')!.values.Hard).toEqual(before.values.Hard)
  expect(state.configs.get('demo-main')!.values.Exercise.Scheduler).toEqual(before.values.Exercise.Scheduler)
  expect(state.configs.get('demo-main')!.values.Exercise.Exercise.MaintenanceAdvance).toBe(true)
  await page.reload()
  await expect(control(page)).toHaveText('周二')
  await page.evaluate(() => {location.hash = '#/i/demo-main/task/Exercise'})
  await expect(page.getByRole('combobox', {name: '提前演习星期', exact: true})).toHaveText('周二')
  expect(state.errors).toEqual([])
})

test('不同实例的维护日独立，关闭的演习提前和困难延迟不会被快捷设置开启', async ({page}) => {
  const state = await fixture(page)
  await open(page)
  await choose(page, '周五')
  await expect(status(page)).toHaveText('已保存')
  await page.evaluate(() => {location.hash = '#/i/demo-alt/overview'})
  await expect(control(page)).toHaveText('周一')
  await choose(page, '周日')
  await expect(status(page)).toHaveText('已保存')
  expect(state.configs.get('demo-alt')!.values.Exercise.Exercise.MaintenanceAdvance).toBe(false)
  expect(state.configs.get('demo-alt')!.values.Hard.Hard.RunAt18).toBe(false)
  await page.evaluate(() => {location.hash = '#/i/demo-main/overview'})
  await expect(control(page)).toHaveText('周五')
  expect(state.writes.map(request => [request.params.instance, request.params.changes])).toEqual([
    ['demo-main', [{path: PATH, value: 'fri'}]], ['demo-alt', [{path: PATH, value: 'sun'}]],
  ])
  expect(state.errors).toEqual([])
})

test('维护日加载失败可重试，不以默认星期覆盖真实配置', async ({page}) => {
  const state = await fixture(page)
  state.failRead(true)
  await open(page)
  const card = page.locator('.maintenance-weekday')
  await expect(card.getByRole('alert')).toContainText('模拟维护日读取失败')
  expect(state.writes).toHaveLength(0)
  state.failRead(false)
  await card.getByRole('button', {name: /重试/}).click()
  await expect(control(page)).toHaveText('周四')
  expect(state.writes).toHaveLength(0)
  expect(state.errors).toEqual([])
})

test('保存失败保留用户选择并可重试，刷新后读取已保存值', async ({page}) => {
  const state = await fixture(page)
  await open(page)
  state.failWrite(true)
  await choose(page, '周六')
  await expect(status(page)).toHaveAttribute('role', 'alert')
  await expect(status(page)).toContainText('模拟维护日保存失败')
  await expect(control(page)).toHaveText('周六')
  state.failWrite(false)
  await status(page).getByRole('button', {name: /重试/}).click()
  await expect(status(page)).toHaveText('已保存')
  await page.reload()
  await expect(control(page)).toHaveText('周六')
  expect(state.errors).toEqual([])
})

test('旧保存回执迟到时保留最新选择，最终保存最后一次星期', async ({page}) => {
  const state = await fixture(page)
  await open(page)
  state.holdWrite(true)
  await choose(page, '周一')
  await expect.poll(state.pendingWrites).toBe(1)
  await choose(page, '周三')
  await expect(control(page)).toHaveText('周三')
  state.holdWrite(false)
  state.releaseWrite()
  await expect(status(page)).toHaveText('已保存')
  await expect(control(page)).toHaveText('周三')
  expect(state.writes.map(request => request.params.changes)).toEqual([
    [{path: PATH, value: 'mon'}], [{path: PATH, value: 'wed'}],
  ])
  await page.reload()
  await expect(control(page)).toHaveText('周三')
  expect(state.errors).toEqual([])
})

test('切实例后迟到的旧读取不覆盖新实例维护日', async ({page}) => {
  const state = await fixture(page)
  state.holdRead('demo-main')
  await open(page)
  await expect.poll(state.pendingReads).toBe(1)
  await page.evaluate(() => {location.hash = '#/i/demo-alt/overview'})
  await expect(control(page)).toHaveText('周一')
  state.releaseRead()
  await choose(page, '周三')
  await expect(status(page)).toHaveText('已保存')
  await expect(control(page)).toHaveText('周三')
  expect(state.writes.map(request => request.params.instance)).toEqual(['demo-alt'])
  expect(state.configs.get('demo-main')!.values.Exercise.Exercise.MaintenanceWeekday).toBe('thu')
  expect(state.errors).toEqual([])
})

for (const theme of ['legacy-light', 'legacy-dark']) {
  for (const width of [1440, 390]) {
    test(`${theme} ${width}px 日志标题、维护日和设置按钮不重叠`, async ({page}, info) => {
      const state = await fixture(page)
      await page.setViewportSize({width, height: width === 390 ? 844 : 1100})
      await open(page, theme)
      const card = page.locator('.legacy-stat-card')
      await expect(control(page)).toHaveText('周四')
      await control(page).scrollIntoViewIfNeeded()
      const bounds = await card.evaluate(element => {
        const rect = (node: Element) => {
          const box = node.getBoundingClientRect()
          return {left: box.left, right: box.right, top: box.top, bottom: box.bottom}
        }
        return {card: rect(element), items: [
          rect(element.querySelector('.legacy-stat-title')!),
          rect(element.querySelector('.maintenance-weekday')!),
          rect(element.querySelector('button[aria-label="仪表盘设置"]')!),
        ], viewport: innerWidth, scrollWidth: document.documentElement.scrollWidth}
      })
      for (const item of bounds.items) {
        expect(item.left).toBeGreaterThanOrEqual(bounds.card.left - 1)
        expect(item.right).toBeLessThanOrEqual(bounds.card.right + 1)
        expect(item.top).toBeGreaterThanOrEqual(bounds.card.top - 1)
        expect(item.bottom).toBeLessThanOrEqual(bounds.card.bottom + 1)
      }
      for (let i = 0; i < bounds.items.length; i++) {
        for (let j = i + 1; j < bounds.items.length; j++) {
          const a = bounds.items[i], b = bounds.items[j]
          expect(a.right <= b.left + 1 || b.right <= a.left + 1 || a.bottom <= b.top + 1 || b.bottom <= a.top + 1).toBe(true)
        }
      }
      expect(bounds.scrollWidth).toBeLessThanOrEqual(bounds.viewport + 1)
      await card.screenshot({path: info.outputPath(`维护日-${theme}-${width}.png`)})
      await control(page).click()
      await expect(page.getByRole('option')).toHaveText(DAYS)
      const list = await page.getByRole('listbox').boundingBox()
      expect(list).not.toBeNull()
      expect(list!.x).toBeGreaterThanOrEqual(0)
      expect(list!.x + list!.width).toBeLessThanOrEqual(width + 1)
      await page.screenshot({path: info.outputPath(`维护日选项-${theme}-${width}.png`)})
      expect(state.errors).toEqual([])
    })
  }
}

test('新版主题不新增维护日入口或自动保存配置', async ({page}) => {
  const state = await fixture(page)
  await open(page, 'light')
  await expect(page.locator('.overview-page')).toBeVisible()
  await expect(control(page)).toHaveCount(0)
  await expect(page.locator('.maintenance-weekday')).toHaveCount(0)
  expect(state.writes).toHaveLength(0)
  expect(state.errors).toEqual([])
})

test('聚焦触发的旧读取迟到时不覆盖刚保存的维护日，保存提示消失后仍保持新值', async ({page}) => {
  const state = await fixture(page)
  await open(page)
  await expect(control(page)).toHaveText('周四')
  state.holdRead('demo-main')
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  await expect.poll(state.pendingReads).toBe(1)
  await choose(page, '周五')
  await expect(status(page)).toHaveText('已保存')
  expect(state.configs.get('demo-main')!.values.Exercise.Exercise.MaintenanceWeekday).toBe('fri')
  state.releaseRead()
  // 不仅核对队列的乐观值，还等待草稿退出，确保底层旧快照没有让控件回弹。
  await expect(status(page)).toHaveCount(0)
  await expect(control(page)).toHaveText('周五')
  expect(state.writes.map(request => request.params.changes)).toEqual([[{path: PATH, value: 'fri'}]])
  expect(state.errors).toEqual([])
})

for (const language of ['en-US', 'ja-JP']) {
  test(`${language} 旧版1440px维护日翻译及长文案布局不重叠`, async ({page}, info) => {
    const state = await fixture(page)
    await page.setViewportSize({width: 1440, height: 1100})
    await open(page, 'legacy-light', language)
    const card = page.locator('.legacy-stat-card')
    const select = card.locator('.maintenance-weekday .select-trigger')
    const days = language === 'en-US'
      ? ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
      : ['月曜日', '火曜日', '水曜日', '木曜日', '金曜日', '土曜日', '日曜日']
    await expect(select).toHaveText(days[3])
    await select.scrollIntoViewIfNeeded()
    const bounds = await card.evaluate(element => {
      const rect = (node: Element) => {
        const box = node.getBoundingClientRect()
        return {left: box.left, right: box.right, top: box.top, bottom: box.bottom}
      }
      return {card: rect(element), items: [
        rect(element.querySelector('.legacy-stat-title')!),
        rect(element.querySelector('.maintenance-weekday')!),
        rect(element.querySelector(':scope > button.button')!),
      ], viewport: innerWidth, scrollWidth: document.documentElement.scrollWidth}
    })
    for (const item of bounds.items) {
      expect(item.left).toBeGreaterThanOrEqual(bounds.card.left - 1)
      expect(item.right).toBeLessThanOrEqual(bounds.card.right + 1)
      expect(item.top).toBeGreaterThanOrEqual(bounds.card.top - 1)
      expect(item.bottom).toBeLessThanOrEqual(bounds.card.bottom + 1)
    }
    for (let i = 0; i < bounds.items.length; i++) {
      for (let j = i + 1; j < bounds.items.length; j++) {
        const a = bounds.items[i], b = bounds.items[j]
        expect(a.right <= b.left + 1 || b.right <= a.left + 1 || a.bottom <= b.top + 1 || b.bottom <= a.top + 1).toBe(true)
      }
    }
    expect(bounds.scrollWidth).toBeLessThanOrEqual(bounds.viewport + 1)
    await card.screenshot({path: info.outputPath(`维护日-${language}-1440.png`)})
    await select.click()
    await expect(page.getByRole('option')).toHaveText(days)
    await page.screenshot({path: info.outputPath(`维护日选项-${language}-1440.png`)})
    await page.getByRole('option', {name: days[2], exact: true}).click()
    await expect.poll(() => state.configs.get('demo-main')!.values.Exercise.Exercise.MaintenanceWeekday).toBe('wed')
    await expect(select).toHaveText(days[2])
    const textSize = await select.locator('span').evaluate(element => ({
      content: element.scrollWidth, visible: element.clientWidth,
    }))
    expect(textSize.content).toBeLessThanOrEqual(textSize.visible + 1)
    await card.screenshot({path: info.outputPath(`维护日完整星期-${language}-1440.png`)})
    expect(state.errors).toEqual([])
  })
}
