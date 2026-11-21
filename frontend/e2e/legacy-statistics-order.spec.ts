import { expect, test, type Locator, type Page } from '@playwright/test'

const chart = /^体力变化/
const opsi = '大世界数据收集'
const meow = '本月耄耋相接收获'
const ships = /每日经验检测/
const commissions = '委托收益统计'

async function openOverview(page: Page, theme = 'legacy-light', saved?: string) {
  await page.route('https://**', route => route.abort())
  await page.addInitScript(({theme, saved}) => {
    localStorage.setItem('azurpilot.theme', theme)
    localStorage.setItem('azurpilot.language', 'zh-CN')
    localStorage.setItem('azurpilot.overview-panel', 'stats')
    localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
    if (saved && localStorage.getItem('azurpilot.legacy-statistics.section-order') === null) {
      localStorage.setItem('azurpilot.legacy-statistics.section-order', saved)
    }
  }, {theme, saved})
  await page.goto('/#/i/testpilot/overview')
  await expect(page.locator('.legacy-section-grip')).toHaveCount(5, {timeout: 15000})
  return page.locator('.legacy-stats-charts > section .legacy-stat-heading')
}

async function center(handle: Locator) {
  const box = (await handle.boundingBox())!
  return {x: box.x + box.width / 2, y: box.y + box.height / 2}
}

test('在总览直接拖动板块，停在边缘时跨屏滚动，刷新及离开后返回仍沿用顺序', async ({page}) => {
  await page.setViewportSize({width: 1260, height: 790})
  const headings = await openOverview(page)
  await expect(headings).toHaveText([chart, opsi, meow, ships, commissions])
  await expect(page.getByRole('button', {name: '板块排序', exact: true})).toHaveCount(0)
  const ap = page.locator('.legacy-ap-card')
  await ap.getByRole('button', {name: '近24小时', exact: true}).click()
  const handle = page.getByRole('button', {name: '拖动调整「委托收益统计」的位置'})
  await handle.scrollIntoViewIfNeeded()
  const area = page.locator('.statistics-legacy')
  const viewport = (await area.boundingBox())!
  const start = await center(handle)
  await page.mouse.move(start.x, start.y)
  await page.mouse.down()
  await page.mouse.move(start.x, viewport.y + 10, {steps: 16})
  await expect.poll(() => area.evaluate(el => el.scrollTop)).toBe(0)
  await page.mouse.up()
  await expect(headings).toHaveText([commissions, /体力变化.*近24小时/, opsi, meow, ships])

  await page.reload()
  await expect(headings).toHaveText([commissions, chart, opsi, meow, ships], {timeout: 15000})
  await page.locator('.primary-nav').getByRole('link', {name: '资源统计', exact: true}).click()
  await expect(page.getByRole('tab', {name: '舰船经验', exact: true})).toBeVisible()
  await expect(page.locator('.legacy-section-grip:visible')).toHaveCount(0)
  await page.locator('.primary-nav').getByRole('link', {name: '运行总览', exact: true}).click()
  await expect(headings).toHaveText([commissions, chart, opsi, meow, ships])

  const grip = page.getByRole('button', {name: '拖动调整「大世界数据收集」的位置'})
  await grip.press('ArrowDown')
  await expect(headings).toHaveText([commissions, chart, meow, opsi, ships])
  await expect(grip).toBeFocused()
  await page.keyboard.press('ArrowUp')
  await expect(headings).toHaveText([commissions, chart, opsi, meow, ships])
})

test('从第一张图表向下拖到末尾，停在下边缘时滚动范围有界', async ({page}) => {
  await page.setViewportSize({width: 1260, height: 790})
  const headings = await openOverview(page)
  const area = page.locator('.statistics-legacy')
  const limit = await area.evaluate(el => el.scrollHeight - el.clientHeight)
  const bounds = (await area.boundingBox())!
  const start = await center(page.locator('.legacy-ap-card .legacy-section-grip'))
  await page.mouse.move(start.x, start.y)
  await page.mouse.down()
  await page.mouse.move(start.x, start.y + 18, {steps: 4})
  await expect(area.locator('[data-dragging]')).toHaveCount(1)
  await page.keyboard.press('Escape')
  await page.mouse.up()
  await expect(area.locator('[data-dragging]')).toHaveCount(0)
  expect(await page.evaluate(() => localStorage.getItem('azurpilot.legacy-statistics.section-order'))).toBeNull()

  await page.mouse.move(start.x, start.y)
  await page.mouse.down()
  await page.mouse.move(start.x, bounds.y + bounds.height - 8, {steps: 16})
  await expect.poll(() => area.evaluate(el => el.scrollTop)).toBeGreaterThanOrEqual(limit - 1)
  expect(await area.evaluate(el => el.scrollHeight - el.clientHeight)).toBeLessThanOrEqual(limit + 1)
  await page.mouse.up()
  await expect(headings).toHaveText([opsi, meow, ships, commissions, chart])
})

test.describe('旧版深色窄屏', () => {
  test.use({viewport: {width: 390, height: 750}, hasTouch: true})
  test('沿用旧顺序，手指可直接拖动，历史月份等操作仍可点按', async ({page}) => {
    const headings = await openOverview(page, 'legacy-dark', '["opsi","missing","opsi","meow"]')
    await expect(headings).toHaveText([opsi, meow, chart, ships, commissions])
    const grip = page.getByRole('button', {name: '拖动调整「耄耋相接收获」的位置'})
    await grip.scrollIntoViewIfNeeded()
    const from = await center(grip)
    const to = await center(page.getByRole('button', {name: '拖动调整「大世界数据收集」的位置'}))
    const touch = await page.context().newCDPSession(page)
    await touch.send('Input.dispatchTouchEvent', {type: 'touchStart', touchPoints: [from]})
    for (let step = 1; step <= 8; step++) {
      const y = from.y + (to.y - from.y) * step / 8
      await touch.send('Input.dispatchTouchEvent', {type: 'touchMove', touchPoints: [{x: from.x, y}]})
    }
    await touch.send('Input.dispatchTouchEvent', {type: 'touchEnd', touchPoints: []})
    await expect(headings).toHaveText([meow, opsi, chart, ships, commissions])
    await page.reload()
    await expect(headings).toHaveText([meow, opsi, chart, ships, commissions], {timeout: 15000})
    await page.getByRole('button', {name: '查看历史月份'}).tap()
    await expect(page.getByRole('dialog')).toBeVisible()
  })
})
