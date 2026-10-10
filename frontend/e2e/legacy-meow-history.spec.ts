import {expect, test, type Locator, type Page} from '@playwright/test'
import type {LegacyColumn, LegacyStatisticsReport} from '../src/api/types'

type LegacyRequest = {id: string; method: string; params: {instance?: string; month?: string | null; item?: string}}
type ScreenshotFolderResult = {
  opened: boolean
  path: string | null
  requestedPath: string
  scope: 'month' | 'category' | 'missing'
  item: string
  month: string
}
type ScreenshotFolderReply = {result?: ScreenshotFolderResult; error?: string}
type StatisticsFixtureOptions = {
  hazardsByMonth?: Record<string, number[]>
  cumulativeRows?: (number | string)[][]
}

const CURRENT_MONTH = `${new Date().getFullYear()}-${String(new Date().getMonth() + 1).padStart(2, '0')}`
const HISTORY_MONTH = '2026-09'
const CUMULATIVE_HEADERS = ['历月有效战斗轮数总和', '平均黄币/轮', '平均金菜/轮', '平均深渊/轮', '平均隐秘/轮']
const CUMULATIVE_ROWS = [
  ['3', '1234', '612.345678', '0.012345', '0.001234', '0.002345'],
  ['5', '2345', '987.654321', '0.023456', '0.003456', '0.004567'],
]
const SCREENSHOT_ITEMS = [
  ['金菜', 'Plate'], ['彩图纸', 'GearDesignPlanT5'], ['金机密', 'OrdnanceTestingReportT4'],
  ['隐秘', 'CoordinateObscure'], ['深渊', 'CoordinateAbyssal'], ['金猫箱', 'CatT3'],
] as const
const MEOW_COLUMNS: LegacyColumn[] = [
  {key: 'Gui.Stat.Month', format: 'text'},
  {key: 'Gui.Stat.HazardLevel', format: 'int'},
  {key: 'Gui.Stat.BattleRounds', format: 'int'},
  ...['金菜', '彩图纸', '金机密', '隐秘', '深渊', '金猫箱'].map(key => ({key, format: 'int'})),
  {key: 'Gui.Stat.MeowEffectiveRounds', format: 'int'},
  ...['MeowAvgOperationCoin', 'MeowAvgPlate', 'MeowAvgAbyssal', 'MeowAvgObscure']
    .map(key => ({key: `Gui.Stat.${key}`, format: 'text'})),
]

/** 替换旧版统计及目录打开响应，不读取真实历史、执行游戏或打开资源管理器。 */
async function isolatedStatistics(page: Page, availableMonths = [HISTORY_MONTH, '2026-08'], extraMeowRows: (number | string)[][] = [], mergeUnknownLoot = false, options: StatisticsFixtureOptions = {}) {
  const requests: LegacyRequest[] = []
  const folderRequests: LegacyRequest[] = []
  const pendingFolders: ((reply?: ScreenshotFolderReply) => void)[] = []
  let holdFolders = false
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('https://**', route => route.abort())
  await page.routeWebSocket('**/api/v1/ws', socket => {
    const server = socket.connectToServer()
    socket.onMessage(raw => {
      const request = JSON.parse(String(raw)) as LegacyRequest
      if (request.method === 'statistics.meowScreenshotFolder.open') {
        folderRequests.push(request)
        const month = request.params.month ?? CURRENT_MONTH
        const item = request.params.item ?? ''
        const requestedPath = `screenshots/${item}/${month}`
        const respond = (reply: ScreenshotFolderReply = {}) => socket.send(JSON.stringify(reply.error
          ? {v: 1, type: 'response', id: request.id, ok: false, error: {code: 'INTERNAL_ERROR', message: reply.error}}
          : {v: 1, type: 'response', id: request.id, ok: true, result: reply.result ?? {
            opened: true, path: requestedPath, requestedPath, scope: 'month', item, month,
          }}))
        if (holdFolders) pendingFolders.push(respond)
        else respond()
        return
      }
      if (request.method !== 'statistics.legacy') {
        server.send(raw)
        return
      }
      requests.push(request)
      const month = request.params.month ?? CURRENT_MONTH
      const isCurrentMonth = !request.params.month
      const unknownLoot = mergeUnknownLoot ? 1 : 0
      const selectedHazards = options.hazardsByMonth?.[month]
      const currentHazards = options.hazardsByMonth?.[CURRENT_MONTH]
      const meowRows = [
        [month, 3, isCurrentMonth ? 31 : 13, 11, 12, 13, 14, 15, 16, ...CUMULATIVE_ROWS[0].slice(1)],
        [month, 5, isCurrentMonth ? 51 : 15, 21 + unknownLoot, 22 + unknownLoot, 23, 24 + unknownLoot, 25, 26, ...CUMULATIVE_ROWS[1].slice(1)],
        ...extraMeowRows.map(row => [month, ...row]),
      ].filter(row => selectedHazards === undefined || selectedHazards.includes(Number(row[1])))
      const report: LegacyStatisticsReport = {
        instance: request.params.instance ?? 'testpilot', month, dashboardKeys: [],
        apChart: {series: []}, opsi: {
          summary: [],
          columns: currentHazards === undefined ? [] : [
            {key: 'Gui.Stat.HazardLevel', format: 'int'},
            {key: 'Gui.Stat.BattleRounds', format: 'int'},
          ],
          // 大世界统计固定本月，历史月份选择只影响耄耋收获表。
          rows: (currentHazards ?? []).map(hazard => [hazard, hazard === 5 ? 51 : 31]),
        },
        meowLoot: {
          month, isCurrentMonth, availableMonths, lastRecord: '2026-10-06 09:00:00', columns: MEOW_COLUMNS,
          rows: meowRows,
          // 原有用例不传此字段，继续验证与旧后端的兼容派生路径。
          ...(options.cumulativeRows === undefined ? {} : {cumulativeRows: options.cumulativeRows}),
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
  return {requests, folderRequests, errors,
    holdFolderResponses: () => {holdFolders = true},
    replyToFolder: (reply?: ScreenshotFolderReply) => {
      const respond = pendingFolders.shift()
      if (!respond) throw new Error('没有等待中的目录请求')
      respond(reply)
    },
    setMonthHazards: (month: string, hazards: number[]) => {
    options.hazardsByMonth ??= {}
    options.hazardsByMonth[month] = hazards
  }}
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

async function expectCumulativeRows(dialog: Locator, expectedRows = CUMULATIVE_ROWS) {
  const cumulative = dialog.getByRole('region', {name: '历月累计收获', exact: true})
  await expect(cumulative.locator('thead th')).toHaveText(['侵蚀等级', ...CUMULATIVE_HEADERS])
  await expect(cumulative.locator('tbody tr')).toHaveCount(expectedRows.length)
  for (const [index, cells] of expectedRows.entries()) {
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

test('旧版未识别掉落归入侵蚀 5，保留侵蚀 6 且不改变轮次和历史累计', async ({page}, testInfo) => {
  const mergedLootCells = ['22', '23', '23', '25', '25', '26']
  const extraRows: (number | string)[][] = [
    [6, 6, 0, 2, 0, 0, 0, 0, '-', '-', '-', '-', '-'],
  ]
  const fixture = await isolatedStatistics(page, [HISTORY_MONTH], extraRows, true)
  const section = await openStatistics(page)
  await expect(section.locator('thead th')).toHaveCount(9)
  await expect(section.locator('tbody tr')).toHaveCount(3)
  await expect(section.locator('tbody tr').nth(1).locator('td')).toHaveText([
    CURRENT_MONTH, '5', '51', ...mergedLootCells,
  ])
  await expect(section.locator('tbody tr').nth(2).locator('td')).toHaveText([
    CURRENT_MONTH, '6', '6', '0', '2', '0', '0', '0', '0',
  ])
  await expect(section.getByText('未识别', {exact: true})).toHaveCount(0)
  await section.screenshot({path: testInfo.outputPath('旧版月度收获未识别归入侵蚀5.png')})

  await section.getByRole('button', {name: '查看历史月份', exact: true}).click()
  const dialog = page.getByRole('dialog')
  const cumulativeRows = [
    ...CUMULATIVE_ROWS,
    ['6', '-', '-', '-', '-', '-'],
  ]
  await expectCumulativeRows(dialog, cumulativeRows)
  await expect(dialog.getByText('未识别', {exact: true})).toHaveCount(0)
  await dialog.screenshot({path: testInfo.outputPath('旧版历史累计未识别归入侵蚀5.png')})
  await dialog.getByRole('button', {name: HISTORY_MONTH, exact: true}).click()
  await expect(dialog).toHaveCount(0)
  await expect(section.getByRole('heading', {name: `历史耄耋相接收获（${HISTORY_MONTH}）`, exact: true})).toBeVisible()
  await expect(section.locator('tbody tr').nth(1).locator('td')).toHaveText([
    HISTORY_MONTH, '5', '15', ...mergedLootCells,
  ])
  await expect(section.getByText('未识别', {exact: true})).toHaveCount(0)
  await section.getByRole('button', {name: '查看历史月份', exact: true}).click()
  await expectCumulativeRows(dialog, cumulativeRows)
  await expect(dialog.getByText('未识别', {exact: true})).toHaveCount(0)
  expect(fixture.requests.some(request => request.params.month === HISTORY_MONTH)).toBe(true)
  expect(fixture.errors).toEqual([])
})

test('旧版仅显示有记录的侵蚀等级，切换月份和新增记录刷新后立即更新', async ({page}, testInfo) => {
  const fixture = await isolatedStatistics(page, [HISTORY_MONTH], [], false, {
    hazardsByMonth: {[CURRENT_MONTH]: [5], [HISTORY_MONTH]: [3]},
    cumulativeRows: CUMULATIVE_ROWS,
  })
  const section = await openStatistics(page)
  const opsi = page.locator('.legacy-stats-section').filter({has: page.getByRole('heading', {name: '大世界数据收集', exact: true})})
  await expect(opsi).toBeVisible()
  await expect(page.getByRole('heading', {name: '雪风大人的大世界数据收集', exact: true})).toHaveCount(0)
  await expect(opsi.locator('tbody tr')).toHaveCount(1)
  await expect(opsi.locator('tbody tr').locator('td')).toHaveText(['5', '51'])
  await expect(section.locator('tbody tr')).toHaveCount(1)
  await expect(section.locator('tbody tr').locator('td')).toHaveText([
    CURRENT_MONTH, '5', '51', '21', '22', '23', '24', '25', '26',
  ])
  await page.screenshot({path: testInfo.outputPath('旧版本月仅显示侵蚀5.png')})

  await section.getByRole('button', {name: '查看历史月份', exact: true}).click()
  const dialog = page.getByRole('dialog')
  // 本月隐藏的侵蚀 3 仍有历月累计；累计不从本月单行表错误派生。
  await expectCumulativeRows(dialog)
  await dialog.getByRole('button', {name: HISTORY_MONTH, exact: true}).click()
  await expect(dialog).toHaveCount(0)
  await expect(section.getByRole('heading', {name: `历史耄耋相接收获（${HISTORY_MONTH}）`, exact: true})).toBeVisible()
  await expect(section.locator('tbody tr')).toHaveCount(1)
  await expect(section.locator('tbody tr').locator('td')).toHaveText([
    HISTORY_MONTH, '3', '13', '11', '12', '13', '14', '15', '16',
  ])
  await expect(opsi.locator('tbody tr').locator('td')).toHaveText(['5', '51'])
  await section.getByRole('button', {name: '查看历史月份', exact: true}).click()
  await expectCumulativeRows(dialog)
  await dialog.getByRole('button', {name: '关闭', exact: true}).click()
  await section.getByRole('button', {name: '回到本月', exact: true}).click()
  await expect(section.getByRole('heading', {name: '本月耄耋相接收获', exact: true})).toBeVisible()
  await expect(section.locator('tbody tr')).toHaveCount(1)

  fixture.setMonthHazards(CURRENT_MONTH, [5, 3])
  await section.getByRole('button', {name: '刷新统计', exact: true}).click()
  await expect(opsi.locator('tbody tr')).toHaveCount(2)
  await expect(opsi.locator('tbody tr').nth(1).locator('td')).toHaveText(['3', '31'])
  await expect(section.locator('tbody tr')).toHaveCount(2)
  await expect(section.locator('tbody tr').nth(0).locator('td').nth(1)).toHaveText('3')
  await expect(section.locator('tbody tr').nth(1).locator('td').nth(1)).toHaveText('5')
  expect(fixture.requests.some(request => request.params.month === HISTORY_MONTH)).toBe(true)
  expect(fixture.requests.at(-1)?.params.month).toBeNull()
  expect(fixture.errors).toEqual([])
})

test('旧版本月无记录时两张月度表不补零行，历史弹窗仍显示侵蚀 3/5 累计', async ({page}, testInfo) => {
  const fixture = await isolatedStatistics(page, [HISTORY_MONTH], [], false, {
    hazardsByMonth: {[CURRENT_MONTH]: [], [HISTORY_MONTH]: [3, 5]},
    cumulativeRows: CUMULATIVE_ROWS,
  })
  const section = await openStatistics(page)
  const opsi = page.locator('.legacy-stats-section').filter({has: page.getByRole('heading', {name: '大世界数据收集', exact: true})})
  await expect(opsi).toBeVisible()
  await expect(opsi.locator('tbody tr')).toHaveCount(0)
  await expect(section.locator('tbody tr')).toHaveCount(0)
  await expect(section.getByRole('button', {name: '查看历史月份', exact: true})).toBeEnabled()
  await section.getByRole('button', {name: '查看历史月份', exact: true}).click()
  const dialog = page.getByRole('dialog')
  await expectCumulativeRows(dialog)
  await page.screenshot({path: testInfo.outputPath('旧版本月空表保留历月累计.png')})
  await dialog.getByRole('button', {name: HISTORY_MONTH, exact: true}).click()
  await expect(dialog).toHaveCount(0)
  await expect(section.getByRole('heading', {name: `历史耄耋相接收获（${HISTORY_MONTH}）`, exact: true})).toBeVisible()
  await expect(section.locator('tbody tr')).toHaveCount(2)
  await expect(opsi.locator('tbody tr')).toHaveCount(0)
  await section.getByRole('button', {name: '查看历史月份', exact: true}).click()
  await expectCumulativeRows(dialog)
  expect(fixture.errors).toEqual([])
})

test('旧版六类高价值物品文字后提供截图目录按钮，当前和历史月份均传所选月份', async ({page}, testInfo) => {
  const fixture = await isolatedStatistics(page)
  const section = await openStatistics(page)
  const statisticsRequests = fixture.requests.length
  for (const [label, item] of SCREENSHOT_ITEMS) {
    const button = section.getByRole('button', {name: `打开${label}截图文件夹`, exact: true})
    await expect(button).toBeVisible()
    await expect(button.locator('svg')).toHaveCount(1)
    await expect(button).toHaveAttribute('title', new RegExp(CURRENT_MONTH))
    await expect(button).toHaveAttribute('title', /脚本所在电脑/)
    expect(await button.evaluate((element, text) => {
      const header = element.closest('th')
      if (!header) return false
      const walker = document.createTreeWalker(header, NodeFilter.SHOW_TEXT)
      let node: Node | null
      while ((node = walker.nextNode())) {
        if (node.textContent?.trim() === text) {
          return Boolean(node.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING)
        }
      }
      return false
    }, label)).toBe(true)
    await button.click()
    await expect.poll(() => fixture.folderRequests.at(-1)?.params).toEqual({instance: 'testpilot', item, month: CURRENT_MONTH})
    await expect(button).toBeEnabled()
  }
  expect(fixture.folderRequests).toHaveLength(SCREENSHOT_ITEMS.length)
  expect(fixture.requests).toHaveLength(statisticsRequests)
  await section.screenshot({path: testInfo.outputPath('旧版收获表截图文件夹按钮.png')})

  await section.getByRole('button', {name: '查看历史月份', exact: true}).click()
  const dialog = page.getByRole('dialog')
  await expectCumulativeRows(dialog)
  await dialog.getByRole('button', {name: HISTORY_MONTH, exact: true}).click()
  await expect(section.getByRole('heading', {name: `历史耄耋相接收获（${HISTORY_MONTH}）`, exact: true})).toBeVisible()
  for (const [label, item] of SCREENSHOT_ITEMS) {
    const button = section.getByRole('button', {name: `打开${label}截图文件夹`, exact: true})
    await expect(button).toHaveAttribute('title', new RegExp(HISTORY_MONTH))
    await expect(button).toHaveAttribute('title', /脚本所在电脑/)
    await button.click()
    await expect.poll(() => fixture.folderRequests.at(-1)?.params).toEqual({instance: 'testpilot', item, month: HISTORY_MONTH})
    await expect(button).toBeEnabled()
  }
  expect(fixture.folderRequests).toHaveLength(SCREENSHOT_ITEMS.length * 2)
  await expect(section.locator('tbody tr').first().locator('td')).toHaveText([
    HISTORY_MONTH, '3', '13', '11', '12', '13', '14', '15', '16',
  ])
  expect(fixture.errors).toEqual([])
})

test('截图目录请求等待时禁用六个打开按钮，不重复打开或阻塞统计操作', async ({page}) => {
  const fixture = await isolatedStatistics(page)
  const section = await openStatistics(page)
  fixture.holdFolderResponses()
  const button = section.getByRole('button', {name: '打开金菜截图文件夹', exact: true})
  await button.click()
  await expect(button).toBeDisabled()
  await expect.poll(() => fixture.folderRequests.length).toBe(1)
  // 浏览器对禁用按钮的再次点击应被忽略，不发送第二次资源管理器请求。
  await button.evaluate(element => {(element as HTMLButtonElement).click(); (element as HTMLButtonElement).click()})
  expect(fixture.folderRequests).toHaveLength(1)
  for (const [label] of SCREENSHOT_ITEMS) {
    await expect(section.getByRole('button', {name: `打开${label}截图文件夹`, exact: true})).toBeDisabled()
  }
  await expect(section.getByRole('button', {name: '查看历史月份', exact: true})).toBeEnabled()
  await expect(section.getByRole('button', {name: '刷新统计', exact: true})).toBeEnabled()
  await expect(section.locator('tbody tr')).toHaveCount(2)
  fixture.replyToFolder()
  await expect(button).toBeEnabled()
  await button.click()
  await expect(button).toBeDisabled()
  await expect.poll(() => fixture.folderRequests.length).toBe(2)
  fixture.replyToFolder()
  await expect(button).toBeEnabled()
  expect(fixture.errors).toEqual([])
})

test('截图目录缺失给出提示，打开失败可以重试且不影响月度统计和历史累计', async ({page}) => {
  const fixture = await isolatedStatistics(page)
  const section = await openStatistics(page)
  fixture.holdFolderResponses()
  const missingButton = section.getByRole('button', {name: '打开金菜截图文件夹', exact: true})
  await missingButton.click()
  await expect(missingButton).toBeDisabled()
  await expect.poll(() => fixture.folderRequests.length).toBe(1)
  fixture.replyToFolder({result: {
    opened: false, path: null, requestedPath: `screenshots/Plate/${CURRENT_MONTH}`,
    scope: 'missing', item: 'Plate', month: CURRENT_MONTH,
  }})
  await expect(missingButton).toBeEnabled()
  await expect(page.getByRole('status').filter({hasText: '暂无金菜截图文件夹'})).toBeVisible()
  await expect(section.locator('tbody tr').first().locator('td')).toHaveText([
    CURRENT_MONTH, '3', '31', '11', '12', '13', '14', '15', '16',
  ])
  await expect(section.getByRole('button', {name: '打开深渊截图文件夹', exact: true})).toBeEnabled()

  // 所选月份尚未归档、分类目录已存在时，打开分类目录并说明回退范围。
  await missingButton.click()
  await expect.poll(() => fixture.folderRequests.length).toBe(2)
  fixture.replyToFolder({result: {
    opened: true, path: 'screenshots/Plate', requestedPath: `screenshots/Plate/${CURRENT_MONTH}`,
    scope: 'category', item: 'Plate', month: CURRENT_MONTH,
  }})
  await expect(page.getByRole('status').filter({hasText: `${CURRENT_MONTH} 暂无金菜截图，已打开分类文件夹`})).toBeVisible()
  await expect(missingButton).toBeEnabled()

  const failedButton = section.getByRole('button', {name: '打开彩图纸截图文件夹', exact: true})
  await failedButton.click()
  await expect(failedButton).toBeDisabled()
  await expect.poll(() => fixture.folderRequests.length).toBe(3)
  fixture.replyToFolder({error: '测试目录打开失败'})
  await expect(page.getByRole('alert')).toContainText('测试目录打开失败')
  await expect(failedButton).toBeEnabled()
  await failedButton.click()
  await expect(failedButton).toBeDisabled()
  await expect.poll(() => fixture.folderRequests.map(request => request.params.item)).toEqual(['Plate', 'Plate', 'GearDesignPlanT5', 'GearDesignPlanT5'])
  fixture.replyToFolder()
  await expect(failedButton).toBeEnabled()
  await expect(page.getByRole('alert')).toHaveCount(0)
  await section.getByRole('button', {name: '查看历史月份', exact: true}).click()
  await expectCumulativeRows(page.getByRole('dialog'))
  expect(fixture.errors).toEqual([])
})

test('旧版深色窄屏截图目录按钮留在收获表内，表格横向滚动不撑宽页面', async ({page}, testInfo) => {
  const fixture = await isolatedStatistics(page)
  await page.setViewportSize({width: 390, height: 844})
  const section = await openStatistics(page, 'legacy-dark')
  const width = await page.evaluate(() => ({viewport: innerWidth, page: document.documentElement.scrollWidth}))
  expect(width.page).toBeLessThanOrEqual(width.viewport + 1)
  const wrapper = section.locator('.legacy-table-wrap')
  expect(await wrapper.evaluate(element => element.scrollWidth > element.clientWidth)).toBe(true)
  const lastButton = section.getByRole('button', {name: '打开金猫箱截图文件夹', exact: true})
  await lastButton.scrollIntoViewIfNeeded()
  await expect(lastButton).toBeInViewport()
  await lastButton.click()
  await expect.poll(() => fixture.folderRequests.at(-1)?.params).toEqual({instance: 'testpilot', item: 'CatT3', month: CURRENT_MONTH})
  await expect(lastButton).toBeEnabled()
  const afterClick = await page.evaluate(() => ({viewport: innerWidth, page: document.documentElement.scrollWidth}))
  expect(afterClick.page).toBeLessThanOrEqual(afterClick.viewport + 1)
  await section.screenshot({path: testInfo.outputPath('旧版深色窄屏截图文件夹按钮.png')})
  expect(fixture.errors).toEqual([])
})
