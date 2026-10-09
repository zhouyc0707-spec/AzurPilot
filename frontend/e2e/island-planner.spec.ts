import {expect, test, type Page} from '@playwright/test'
import type {PlannerReport} from '../src/components/IslandPlannerReport'

function plannerFixture(): PlannerReport {
  const item = (id: number, name: string, target: number) => ({id, name, target, floor: 10, buffer: target - 17, reserve: 7, demand: 0, idle_per_day: 0})
  return {version: 1, enabled: true, legacy: false, generated_at: '2026-10-09 11:00:00', daily_revenue: 100, daily_profit: 40,
    items: [item(2001, '小麦', 100), item(3001, '牛奶', 20), item(4001, '木材', 50), item(5001, '石块', 50)],
    observations: {'2001': {stock: 20, at: '2026-10-09 11:01:00', source: 'IslandFarm'},
      '3001': {stock: 20, at: '2026-10-09 11:03:00', source: 'IslandRancher'},
      '5001': {stock: 999, at: '2026-10-08 10:00:00', source: 'IslandMineForest', stale: true}},
    dispatches: {'2001': {amount: 200, at: '2026-10-09 11:02:00', source: 'IslandFarm'}},
    production: [{recipe_id: 101, name: '小麦', place: '农田', batches_per_day: 1.5}],
    menus: [{id: 601, name: '有鱼餐馆', capacity: 7, items: [{id: 2001, name: '示例菜品', per_day: 2}]}],
  }
}

/** 配置只读响应注入隔离快照；保留真实临时后端的其他字段和协议。 */
async function isolatedPlanner(page: Page, initial = plannerFixture()) {
  let report = initial
  let status = '已生成：每日预计收入 100，净收益 40'
  let readCount = 0
  const errors: string[] = []
  const writes: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('https://**', route => route.abort())
  await page.routeWebSocket('**/api/v1/ws', socket => {
    const server = socket.connectToServer()
    const configRequests = new Set<string>()
    socket.onMessage(raw => {
      const request = JSON.parse(String(raw))
      if (request.method === 'config.get') {configRequests.add(request.id); readCount++}
      if (request.method === 'config.patch') {
        for (const change of request.params.changes) writes.push(change.path)
        // 保留待保存草稿，使只读刷新有机会证明不会覆盖它；不写真实或临时配置。
        if (request.params.changes.some((change: {path: string}) => change.path.endsWith('.HardFloorItems'))) return
      }
      server.send(raw)
    })
    server.onMessage(raw => {
      const response = JSON.parse(String(raw))
      if (response.type === 'response' && configRequests.delete(response.id) && response.ok) {
        const planner = response.result.values.IslandPlan.IslandProductionPlanner
        planner.PlannerReport = JSON.stringify(report)
        planner.PlannerStatus = status
        planner.HardFloorItems = '{}'
      }
      socket.send(JSON.stringify(response))
    })
  })
  return {errors, writes, reads: () => readCount, update: (next: PlannerReport, nextStatus = status) => {report = next; status = nextStatus}}
}

async function openPlanner(page: Page, theme: string) {
  await page.emulateMedia({reducedMotion: 'reduce'})
  await page.addInitScript(value => {
    localStorage.setItem('azurpilot.theme', value)
    localStorage.setItem('azurpilot.language', 'zh-CN')
    localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
  }, theme)
  await page.goto('/#/i/testpilot/task/IslandPlan')
  const card = page.getByRole('region', {name: '生产经营计划明细', exact: true})
  await expect(card.getByText('最近实读缺口：小麦 80', {exact: true})).toBeVisible()
  await expect(card.getByRole('button', {name: '刷新规划明细'})).toBeEnabled()
  return card
}

for (const theme of ['legacy-light', 'light']) {
  test(`${theme} 自动生产规划隐藏科技明细并保存比例数值`, async ({page}) => {
    await page.addInitScript(theme => {
      localStorage.setItem('azurpilot.theme', theme)
      localStorage.setItem('azurpilot.language', 'zh-CN')
      localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
    }, theme)
    await page.route('https://www.clarity.ms/**', route => route.abort())
    let patchId: string | undefined
    let saved = false
    let resetId: string | undefined
    let reset = false
    page.on('websocket', socket => {
      socket.on('framesent', frame => {
        const request = JSON.parse(String(frame.payload))
        if (request.method !== 'config.patch') return
        for (const change of request.params.changes) {
          if (change.path !== 'IslandPlan.IslandProductionPlanner.FieldsEfficiency') continue
          if (change.value === 0.04) patchId = request.id
          if (change.value === 0) resetId = request.id
        }
      })
      socket.on('framereceived', frame => {
        const response = JSON.parse(String(frame.payload))
        if (patchId && response.id === patchId && response.ok) saved = true
        if (resetId && response.id === resetId && response.ok) reset = true
      })
    })
    await page.goto('/#/i/testpilot/task/IslandPlan')
    await expect(page.getByRole('switch', {name: '自动生产规划', exact: true})).toBeVisible()
    await expect(page.locator('textarea[id="IslandPlan.IslandProductionPlanner.TechnologyStatus"]')).toHaveCount(0)
    await expect(page.getByRole('switch', {name: '重新扫描科技', exact: true})).toBeVisible()
    await expect(page.getByRole('textbox', {name: '规划状态', exact: true})).toBeVisible()
    const efficiency = page.getByRole('combobox', {name: '农田额外效率', exact: true})
    await expect(efficiency).toContainText('0%')
    await efficiency.click()
    await expect(page.getByRole('option', {name: '12%', exact: true})).toBeVisible()
    await page.getByRole('option', {name: '4%', exact: true}).click()
    await expect(efficiency).toContainText('4%')
    await expect.poll(() => saved).toBe(true)
    await page.reload()
    await expect(efficiency).toContainText('4%')
    // 两个主题共用同一隔离实例，恢复零值供下一项独立检查。
    await efficiency.click()
    await page.getByRole('option', {name: '0%', exact: true}).click()
    await expect(efficiency).toContainText('0%')
    await expect.poll(() => reset).toBe(true)
  })
}

for (const theme of ['legacy-light', 'light']) {
  test(`${theme} 规划明细区分现货下单与未知值，独立刷新保留参数草稿`, async ({page}, testInfo) => {
    const fixture = await isolatedPlanner(page)
    const card = await openPlanner(page, theme)
    await expect(page.locator('textarea[id="IslandPlan.IslandProductionPlanner.TechnologyStatus"]')).toHaveCount(0)
    await expect(page.getByRole('textbox', {name: '规划状态', exact: true})).toHaveValue('已生成：每日预计收入 100，净收益 40')
    await expect(card.getByRole('table', {name: '生产目标与最近实读', exact: true})).not.toBeVisible()
    await card.locator('details').first().locator('summary').click()
    const wheat = card.locator('tr[data-item-id="2001"]')
    await expect(wheat.locator('td').nth(2)).toHaveText('80')
    await expect(wheat.locator('td').nth(5)).toHaveText('200')
    await expect(card.locator('tr[data-item-id="3001"]').locator('td').nth(3)).toHaveText('最近现货已够')
    await expect(card.locator('tr[data-item-id="3001"]').locator('td').nth(4)).toContainText('2026-10-09 11:03:00')
    await expect(card.locator('tr[data-item-id="4001"]').locator('td').nth(2)).toHaveText('待巡检')
    await expect(card.locator('tr[data-item-id="5001"]').locator('td').nth(2)).toHaveText('待复核')
    await card.locator('details').nth(1).locator('summary').click()
    await expect(card.getByRole('table', {name: '理论每日配方', exact: true})).toContainText('1.5')
    await card.locator('details').nth(2).locator('summary').click()
    await expect(card.getByText('有鱼餐馆 · 一货架 7 份', {exact: true})).toBeVisible()
    await expect(card.getByText('理论 2 份／日', {exact: true})).toBeVisible()
    await card.screenshot({path: testInfo.outputPath(`规划明细-${theme}.png`)})

    const draft = page.getByRole('textbox', {name: '库存保留线', exact: true})
    await draft.fill('{"小麦":123}')
    const report = plannerFixture()
    report.observations['2001'].stock = 100
    fixture.update(report, '规划失败，保留旧计划：岗位不足')
    await card.getByRole('button', {name: '刷新规划明细'}).click()
    await expect(wheat.locator('td').nth(2)).toHaveText('0')
    await expect(wheat.locator('td').nth(3)).toHaveText('最近现货已够')
    await expect(draft).toHaveValue('{"小麦":123}')
    await expect(page.getByRole('textbox', {name: '规划状态', exact: true})).toHaveValue('规划失败，保留旧计划：岗位不足')
    await expect(card.getByText(/以下明细仍是上次有效计划/)).toBeVisible()
    // 等待可见页面的实际轮询，避免虚拟动画时钟影响新版玻璃材质。
    const readsBefore = fixture.reads()
    await expect.poll(() => fixture.reads(), {timeout: 20000}).toBeGreaterThan(readsBefore)
    await expect(draft).toHaveValue('{"小麦":123}')
    expect(fixture.writes.some(path => /PlannerReport|PlannerStatus/.test(path))).toBe(false)
    expect(fixture.errors).toEqual([])
  })
}

test('旧版窄屏规划表仅内部横向滚动，关闭规划与旧报告仍可读', async ({page}, testInfo) => {
  const report = {...plannerFixture(), legacy: true, enabled: false, generated_at: '', daily_revenue: null, daily_profit: null}
  await isolatedPlanner(page, report)
  await page.setViewportSize({width: 390, height: 844})
  const card = await openPlanner(page, 'legacy-dark')
  await expect(card.getByText(/当前按手工配置运行/)).toBeVisible()
  await expect(card.getByText(/等待下一次生产巡检补充详情/)).toBeVisible()
  await card.locator('details').first().locator('summary').click()
  const bounds = await card.evaluate(element => ({width: element.clientWidth, scroll: element.scrollWidth,
    pageWidth: document.documentElement.scrollWidth, viewport: innerWidth}))
  expect(bounds.scroll).toBeLessThanOrEqual(bounds.width + 1)
  expect(bounds.pageWidth).toBeLessThanOrEqual(bounds.viewport + 1)
  const wrapper = card.getByLabel('横向滚动生产明细', {exact: true})
  const scroll = await wrapper.evaluate(element => {
    element.scrollLeft = element.scrollWidth
    return {left: element.scrollLeft, width: element.clientWidth, scroll: element.scrollWidth}
  })
  expect(scroll.scroll).toBeGreaterThan(scroll.width)
  expect(scroll.left).toBeGreaterThan(0)
  await card.screenshot({path: testInfo.outputPath('旧版窄屏规划明细.png')})
})
