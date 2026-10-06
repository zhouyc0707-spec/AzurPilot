import {expect, test, type Locator, type Page} from '@playwright/test'
import type {LegacyColumn, LegacyStatisticsReport} from '../src/api/types'

type LegacyRequest = {id: string; method: string; params: {instance?: string; month?: string | null}}

const CURRENT_MONTH = `${new Date().getFullYear()}-${String(new Date().getMonth() + 1).padStart(2, '0')}`
const HISTORY_MONTH = '2026-09'
const CUMULATIVE_HEADERS = ['历月有效战斗轮数总和', '平均黄币/轮', '平均金菜/轮', '平均深渊/轮', '平均隐秘/轮']
const CUMULATIVE_ROWS = [
  ['3', '1234', '612.345678', '0.012345', '0.001234', '0.002345'],
  ['5', '2345', '987.654321', '0.023456', '0.003456', '0.004567'],
]
const MEOW_COLUMNS: LegacyColumn[] = [
  {key: 'Gui.Stat.Month', format: 'text'},
  {key: 'Gui.Stat.HazardLevel', format: 'int'},
  {key: 'Gui.Stat.BattleRounds', format: 'int'},
  ...['金菜', '彩图纸', '金机密', '隐秘', '深渊', '金猫箱'].map(key => ({key, format: 'int'})),
  {key: 'Gui.Stat.MeowEffectiveRounds', format: 'int'},
  ...['MeowAvgOperationCoin', 'MeowAvgPlate', 'MeowAvgAbyssal', 'MeowAvgObscure']
    .map(key => ({key: `Gui.Stat.${key}`, format: 'text'})),
]

/** 只替换旧版统计响应，其他请求由临时测试后端处理，不读取真实历史或执行游戏。 */
async function isolatedStatistics(page: Page, availableMonths = [HISTORY_MONTH, '2026-08']) {
  const requests: LegacyRequest[] = []
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('https://**', route => route.abort())
  await page.routeWebSocket('**/api/v1/ws', socket => {
    const server = socket.connectToServer()
    socket.onMessage(raw => {
      const request = JSON.parse(String(raw)) as LegacyRequest
      if (request.method !== 'statistics.legacy') {
        server.send(raw)
        return
      }
      requests.push(request)
      const month = request.params.month ?? CURRENT_MONTH
      const isCurrentMonth = !request.params.month
      const report: LegacyStatisticsReport = {
        instance: request.params.instance ?? 'testpilot', month, dashboardKeys: [],
        apChart: {series: []}, opsi: {summary: [], columns: [], rows: []},
        meowLoot: {
          month, isCurrentMonth, availableMonths, lastRecord: '2026-10-06 09:00:00', columns: MEOW_COLUMNS,
          rows: [
            [month, 3, isCurrentMonth ? 31 : 13, 11, 12, 13, 14, 15, 16, ...CUMULATIVE_ROWS[0].slice(1)],
            [month, 5, isCurrentMonth ? 51 : 15, 21, 22, 23, 24, 25, 26, ...CUMULATIVE_ROWS[1].slice(1)],
          ],
        },
        shipExp: {hasData: false, columns: [], rows: []},
        commission: {
          periods: {day: {cards: [], totalCommissions: 0}, week: {cards: [], totalCommissions: 0}, month: {cards: [], totalCommissions: 0}},
          recent: {rows: [], pageSize: 10, maxPages: 5, limit: 50},
          running: {available: false, scannedAt: null, items: []},
        },
      }
      socket.send(JSON.stringify({v: 1, type: 'response', id: request.id, ok: true, result: report}))
    })
    server.onMessage(raw => socket.send(raw))
  })
  return {requests, errors}
}

async function openStatistics(page: Page, theme = 'legacy-light') {
  await page.emulateMedia({reducedMotion: 'reduce'})
  await page.addInitScript(value => {
    localStorage.setItem('azurpilot.theme', value)
    localStorage.setItem('azurpilot.language', 'zh-CN')
    localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
  }, theme)
  await page.goto('/#/i/testpilot/statistics')
  const section = page.locator('.legacy-stats-section').filter({has: page.getByRole('button', {name: '查看历史月份', exact: true})})
  await expect(section.getByRole('heading', {name: '本月耄耋相接收获', exact: true})).toBeVisible({timeout: 15000})
  return section
}

async function expectCumulativeRows(dialog: Locator) {
  const cumulative = dialog.getByRole('region', {name: '历月累计收获', exact: true})
  await expect(cumulative.locator('thead th')).toHaveText(['侵蚀等级', ...CUMULATIVE_HEADERS])
  await expect(cumulative.locator('tbody tr')).toHaveCount(2)
  for (const [index, cells] of CUMULATIVE_ROWS.entries()) {
    await expect(cumulative.locator('tbody tr').nth(index).locator('td')).toHaveText(cells)
  }
  return cumulative
}

test('旧版累计收获移入历史弹窗，月度跳转保持侵蚀 3/5 和历月均值', async ({page}, testInfo) => {
  const fixture = await isolatedStatistics(page)
  const section = await openStatistics(page)
  await expect(section.locator('thead th')).toHaveCount(9)
  await expect(section.locator('tbody tr').first().locator('td')).toHaveText([CURRENT_MONTH, '3', '31', '11', '12', '13', '14', '15', '16'])
  for (const header of CUMULATIVE_HEADERS) await expect(section.getByRole('columnheader', {name: header, exact: true})).toHaveCount(0)

  await section.getByRole('button', {name: '查看历史月份', exact: true}).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('heading', {name: '历史月份与累计收获', exact: true})).toBeVisible()
  await expectCumulativeRows(dialog)
  await expect(dialog.getByText('汇总所有已记录月份，按侵蚀等级分别统计，不随查看月份变化。', {exact: true})).toBeVisible()
  await page.screenshot({path: testInfo.outputPath('旧版历史月份与累计收获.png')})
  await dialog.getByRole('button', {name: `本月（${CURRENT_MONTH}）`, exact: true}).click()
  await expect(dialog).toHaveCount(0)
  await expect(section.getByRole('button', {name: '查看历史月份', exact: true})).toBeEnabled()
  await expect(section).not.toHaveClass(/is-pending/)
  await section.getByRole('button', {name: '查看历史月份', exact: true}).click()
  await dialog.getByRole('button', {name: HISTORY_MONTH, exact: true}).click()
  await expect(dialog).toHaveCount(0)
  await expect(section.getByRole('heading', {name: `历史耄耋相接收获（${HISTORY_MONTH}）`, exact: true})).toBeVisible()
  await expect(section.locator('thead th')).toHaveCount(9)
  await expect(section.locator('tbody tr').first().locator('td')).toHaveText([HISTORY_MONTH, '3', '13', '11', '12', '13', '14', '15', '16'])
  await expect(section.locator('tbody tr').nth(1).locator('td').nth(1)).toHaveText('5')
  expect(fixture.requests.some(request => request.params.month === HISTORY_MONTH)).toBe(true)

  await section.getByRole('button', {name: '查看历史月份', exact: true}).click()
  await expectCumulativeRows(dialog)
  await dialog.getByRole('button', {name: HISTORY_MONTH, exact: true}).click()
  await expect(dialog).toHaveCount(0)
  await expect(section.getByRole('button', {name: '查看历史月份', exact: true})).toBeEnabled()
  await expect(section).not.toHaveClass(/is-pending/)
  await section.getByRole('button', {name: '查看历史月份', exact: true}).click()
  await dialog.getByRole('button', {name: '关闭', exact: true}).click()
  await section.getByRole('button', {name: '回到本月', exact: true}).click()
  await expect(section.getByRole('heading', {name: '本月耄耋相接收获', exact: true})).toBeVisible()
  await expect(section.getByRole('button', {name: '回到本月', exact: true})).toHaveCount(0)
  expect(fixture.requests.at(-1)?.params.month).toBeNull()
  expect(fixture.errors).toEqual([])
})

test('没有历史月份时仍能打开弹窗并查看分侵蚀等级的累计数据', async ({page}) => {
  const fixture = await isolatedStatistics(page, [])
  const section = await openStatistics(page)
  await expect(section.getByRole('button', {name: '查看历史月份', exact: true})).toBeEnabled()
  await section.getByRole('button', {name: '查看历史月份', exact: true}).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByText('暂无历史月份数据', {exact: true})).toBeVisible()
  await expect(dialog.getByRole('button', {name: `本月（${CURRENT_MONTH}）`, exact: true})).toBeVisible()
  await expectCumulativeRows(dialog)
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  expect(fixture.errors).toEqual([])
})

test('旧版深色窄屏累计表在弹窗内横向滚动，不撑宽页面', async ({page}, testInfo) => {
  const fixture = await isolatedStatistics(page)
  await page.setViewportSize({width: 390, height: 844})
  const section = await openStatistics(page, 'legacy-dark')
  await section.getByRole('button', {name: '查看历史月份', exact: true}).click()
  const dialog = page.getByRole('dialog')
  const cumulative = await expectCumulativeRows(dialog)
  const bounds = await dialog.evaluate(element => {
    const box = element.getBoundingClientRect()
    return {left: box.left, right: box.right, innerWidth, width: element.clientWidth, scrollWidth: element.scrollWidth,
      pageWidth: document.documentElement.scrollWidth}
  })
  expect(bounds.left).toBeGreaterThanOrEqual(0)
  expect(bounds.right).toBeLessThanOrEqual(bounds.innerWidth + 1)
  expect(bounds.scrollWidth).toBeLessThanOrEqual(bounds.width + 1)
  expect(bounds.pageWidth).toBeLessThanOrEqual(bounds.innerWidth + 1)
  const wrapper = cumulative.locator('.legacy-table-wrap')
  const scroll = await wrapper.evaluate(element => {
    element.scrollLeft = element.scrollWidth
    return {scrollLeft: element.scrollLeft, width: element.clientWidth, scrollWidth: element.scrollWidth}
  })
  expect(scroll.scrollWidth).toBeGreaterThan(scroll.width)
  expect(scroll.scrollLeft).toBeGreaterThan(0)
  await expect(cumulative.getByRole('columnheader', {name: '平均隐秘/轮', exact: true})).toBeInViewport()
  await page.screenshot({path: testInfo.outputPath('旧版深色窄屏历史累计.png')})
  expect(fixture.errors).toEqual([])
})
