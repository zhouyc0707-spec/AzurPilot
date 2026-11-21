import { expect, test, type Page } from '@playwright/test'

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
    if (saved) localStorage.setItem('azurpilot.legacy-statistics.section-order', saved)
  }, {theme, saved})
  await page.goto('/#/i/testpilot/overview')
  await expect(page.getByRole('button', {name: '板块排序'})).toBeVisible({timeout: 15000})
  return page.locator('.legacy-stats-charts > section .legacy-stat-heading')
}

test('总览支持从末尾拖到首位和键盘微调，保留图表状态，刷新和独立统计页沿用顺序', async ({page}) => {
  const headings = await openOverview(page)
  await expect(headings).toHaveText([chart, opsi, meow, ships, commissions])
  const ap = page.locator('.legacy-ap-card')
  await ap.getByRole('button', {name: '近24小时', exact: true}).click()
  const open = page.getByRole('button', {name: '板块排序', exact: true})
  await open.click()
  const dialog = page.getByRole('dialog')
  const grips = dialog.locator('.legacy-order-grip')
  const from = (await grips.last().boundingBox())!
  const to = (await grips.first().boundingBox())!
  const x = from.x + 12
  await page.mouse.move(x, from.y + from.height / 2)
  await page.mouse.down()
  await page.mouse.move(x, to.y + to.height / 2, {steps: 32})
  await page.mouse.up()
  await expect(grips.first()).toHaveText(commissions)
  await dialog.getByRole('button', {name: opsi + ' 前移'}).press('Enter')
  await page.keyboard.press('Escape')
  await expect(headings).toHaveText([commissions, opsi, /体力变化.*近24小时/, meow, ships])

  await page.reload()
  await expect(headings).toHaveText([commissions, opsi, chart, meow, ships])
  await page.goto('/#/i/testpilot/statistics')
  await expect(headings).toHaveText([commissions, opsi, chart, meow, ships])
  await open.click()
  await dialog.getByRole('button', {name: '恢复默认'}).click()
  await dialog.getByRole('button', {name: '关闭'}).click()
  await page.reload()
  await expect(headings).toHaveText([chart, opsi, meow, ships, commissions])
})

test.describe('旧版深色窄屏', () => {
  test.use({viewport: {width: 390, height: 750}, hasTouch: true})
  test('旧偏好中重复、未知和缺少的板块都能处理，触屏可点按箭头', async ({page}) => {
    const headings = await openOverview(page, 'legacy-dark', '["commission","missing","commission"]')
    await expect(headings).toHaveText([commissions, chart, opsi, meow, ships])
    await page.getByRole('button', {name: '板块排序'}).tap()
    const dialog = page.getByRole('dialog')
    await expect(dialog.getByRole('button', {name: commissions + ' 前移'})).toBeDisabled()
    await expect(dialog.getByRole('button', {name: /每日经验检测.*后移/})).toBeDisabled()
    expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
    await dialog.getByRole('button', {name: commissions + ' 后移'}).tap()
    await dialog.getByRole('button', {name: '关闭'}).tap()
    await expect(headings).toHaveText([chart, commissions, opsi, meow, ships])
  })
})
