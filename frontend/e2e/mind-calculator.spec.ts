import {expect, test} from '@playwright/test'
import {readFile} from 'node:fs/promises'
import {fileURLToPath} from 'node:url'

test.beforeEach(async ({page}) => {
  await page.goto('/')
  // mock 服务跨浏览器上下文保存清单；每例清空实例，自动保存用例不能污染后续场景。
  await page.evaluate(async () => {
    const socket = new WebSocket(`${location.origin.replace(/^http/, 'ws')}/api/v1/ws`)
    await new Promise<void>((resolve, reject) => {socket.onopen = () => resolve(); socket.onerror = () => reject(new Error('mock 连接失败'))})
    let sequence = 0
    const request = (method: string, params: object) => new Promise<{revision: string}>((resolve, reject) => {
      const id = `mind-reset-${++sequence}`
      const receive = (event: MessageEvent) => {
        const response = JSON.parse(event.data)
        if (response.id !== id) return
        socket.removeEventListener('message', receive)
        if (response.ok) resolve(response.result)
        else reject(new Error(response.error.message))
      }
      socket.addEventListener('message', receive)
      socket.send(JSON.stringify({v: 1, type: 'request', id, method, params}))
    })
    try {
      for (const instance of ['demo-main', 'demo-alt']) {
        const report = await request('mind.report', {instance})
        await request('mind.save', {instance, revision: report.revision, ships: []})
      }
    } finally {socket.close()}
  })
  // 后续用例可在首次业务连接前安装 WebSocket 拦截，避免沿用首页连接。
  await page.goto('about:blank')
  await page.addInitScript(() => {localStorage.setItem('azurpilot.theme', 'light'); localStorage.setItem('azurpilot.language', 'zh-CN')})
})

test('自动扫描在开服检测关闭时可启动，草稿保存后解除禁用并可停止扫描', async ({page}) => {
  await page.goto('/#/i/demo-dog/mind-calculator')
  const scan = page.getByRole('button', {name: '自动扫描船坞', exact: true})
  await expect(scan).toBeEnabled()
  const add = page.locator('.mind-add')
  await add.getByLabel('船名', {exact: true}).fill('热心')
  await add.getByRole('button', {name: '添加舰船'}).click()
  await expect(scan).toBeDisabled()
  await expect(page.locator('#mind-scan-reason')).toContainText('请先保存舰船数据')
  await page.screenshot({path: test.info().outputPath('scan-disabled-draft.png'), fullPage: true})
  await page.getByRole('button', {name: '保存舰船数据'}).click()
  await expect(scan).toBeEnabled()
  await expect(page.locator('#mind-scan-reason')).toHaveCount(0)
  await scan.click()
  await expect(page.getByRole('button', {name: '正在扫描船坞', exact: true})).toBeDisabled()
  await expect(page.locator('#mind-scan-reason')).toContainText('正在扫描船坞')
  await page.getByRole('button', {name: '停止扫描', exact: true}).click()
  await expect(scan).toBeEnabled()
})

test('自动扫描按游戏地区和实例状态显示禁用原因，国服检测区不会掩盖日服配置', async ({page}) => {
  let region = 'jp', status = 'stopped'
  await page.routeWebSocket('**/api/v1/ws', socket => {
    const server = socket.connectToServer()
    const requests = new Map<string, string>()
    socket.onMessage(message => {
      const request = JSON.parse(String(message))
      requests.set(request.id, request.method)
      server.send(message)
    })
    server.onMessage(message => {
      const response = JSON.parse(String(message))
      const instances = response.topic === 'instances' ? response.data : requests.get(response.id) === 'instances.list' ? response.result : undefined
      if (Array.isArray(instances)) {
        const current = instances.find(item => item.name === 'demo-dog')
        if (current) Object.assign(current, {region, status, server: 'cn_android-0', currentTask: status === 'running' ? 'Commission' : null})
      }
      socket.send(JSON.stringify(response))
    })
  })
  await page.goto('/#/i/demo-dog/mind-calculator')
  const scan = page.getByRole('button', {name: '自动扫描船坞', exact: true})
  await expect(scan).toBeDisabled()
  await expect(page.locator('#mind-scan-reason')).toContainText('游戏地区不受支持')
  region = 'cn'; status = 'running'
  await page.reload()
  await expect(scan).toBeDisabled()
  await expect(page.locator('#mind-scan-reason')).toContainText('请先停止当前任务')
  status = 'updating'
  await page.reload()
  await expect(scan).toBeDisabled()
  await expect(page.locator('#mind-scan-reason')).toContainText('请等待更新完成')
  status = 'stopped'
  await page.reload()
  await expect(scan).toBeEnabled()
})

test('原生计算器完成手工添加、最高等级合并、保存、Excel 往返与实例隔离', async ({page}) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('/#/i/demo-main/mind-calculator')
  await expect(page.getByRole('heading', {name: '心智单元计算器', exact: true})).toBeVisible()
  const add = page.locator('.mind-add')
  for (const [name, level] of [['拉菲', '100'], ['拉菲.改', '106'], ['约克城II', '100']]) {
    await add.getByLabel('船名', {exact: true}).fill(name)
    await add.getByLabel('等级', {exact: true}).fill(level)
    await add.getByRole('button', {name: '添加舰船'}).click()
  }
  await expect(page.locator('.mind-totals').first()).toContainText('4,260')
  await expect(page.locator('.mind-totals')).toContainText('42,600')
  await expect(page.locator('.mind-ship-table tbody tr')).toHaveCount(2)
  await expect(page.locator('.mind-ship-table input[value="拉菲.改"]')).toBeVisible()
  await page.getByRole('button', {name: '保存舰船数据'}).click()
  await expect(page.locator('.mind-intro')).toContainText('数据已保存到当前实例')
  const downloaded = page.waitForEvent('download')
  await page.getByRole('button', {name: '导出结果', exact: true}).click()
  const download = await downloaded
  expect(download.suggestedFilename()).toMatch(/\.xlsx$/)
  const filename = await download.path()
  const bytes = await readFile(filename!)
  await page.reload()
  await expect(page.locator('.mind-ship-table tbody tr')).toHaveCount(2)
  await page.getByRole('link', {name: '资源管理', exact: true}).click()
  await page.goto('/#/i/demo-alt/mind-calculator')
  await expect(page.getByText('没有符合条件的舰船', {exact: true})).toBeVisible()
  await page.locator('input[type=file][accept=".json,.csv,.xlsx"]').setInputFiles({name: '原计算结果.xlsx', mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', buffer: bytes})
  await expect(page.locator('.mind-ship-table tbody tr')).toHaveCount(2)
  await expect(page.locator('.mind-totals')).toContainText('4,260')
  await page.setViewportSize({width: 390, height: 844})
  await expect(page.getByRole('heading', {name: '心智单元计算器', exact: true})).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  expect(errors).toEqual([])
})

test('截图可靠结果直接计算，保留最高等级，不确定数据保持只读核对状态', async ({page}) => {
    await page.goto('/#/i/demo-alt/mind-calculator')
    await expect(page.locator('.mind-add')).toBeVisible()
  const add = page.locator('.mind-add')
  await add.getByLabel('船名', {exact: true}).fill('热心.改')
  await add.getByLabel('等级', {exact: true}).fill('100')
  await add.getByRole('button', {name: '添加舰船'}).click()
  const screenshot = fileURLToPath(new URL('../../tests/fixtures/mind_dock_fleet_priority.png', import.meta.url))
  await page.locator('input[type=file][accept^="image/png"]').setInputFiles([screenshot, screenshot])
  await expect(page.locator('.mind-ship-table tbody tr')).toHaveCount(21, {timeout: 30_000})
  await expect(page.locator('.mind-totals strong').nth(3)).toHaveText('0')
  await expect(page.locator('.mind-totals strong').first()).not.toHaveText('0')
  const row = page.locator('.mind-ship-table tbody tr').filter({has: page.locator('input[value="热心.改"]')})
  await expect(row.locator('input[type=number]')).toHaveValue('105')
  await expect(row.getByRole('button', {name: '已核对', exact: true})).toHaveCount(0)
  await expect(row).toContainText('800')
  await page.locator('input[type=file][accept=".json,.csv,.xlsx"]').setInputFiles({
    name: '待核对.json', mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify([{name: '人工核对舰', level: 100, base_rarity: 'SR', review: true}])),
  })
  await expect(page.locator('.mind-totals strong').nth(3)).toHaveText('1')
  const uncertain = page.locator('.mind-ship-table tbody tr').filter({has: page.locator('input[value="人工核对舰"]')})
  await expect(uncertain.locator('input[type=text], input:not([type])')).toHaveAttribute('readonly', '')
  await expect(uncertain).toContainText('待核对')
  await expect(uncertain.getByRole('button')).toHaveCount(0)
  await page.getByRole('heading', {name: '心智单元计算器', exact: true}).scrollIntoViewIfNeeded()
  await page.screenshot({path: fileURLToPath(new URL('../test-results/mind-calculator-review.png', import.meta.url)), fullPage: true})
})

test('添加时指定精锐有效，清单只有等级可改且自动保存后重载有效', async ({page}) => {
  await page.goto('/#/i/demo-alt/mind-calculator')
  const add = page.locator('.mind-add')
  await add.getByLabel('船名', {exact: true}).fill('皇家财富号')
  await add.getByRole('combobox', {name: '基础稀有度', exact: true}).click()
  await page.getByRole('option', {name: '精锐', exact: true}).click()
  await add.getByRole('button', {name: '添加舰船'}).click()
  await expect(page.locator('.mind-totals strong').first()).toHaveText('1,320')
  await expect(page.getByLabel('船名 1', {exact: true})).toHaveAttribute('readonly', '')
  await expect(page.locator('.mind-ship-table .mind-rarity')).toHaveText('精锐')
  await expect(page.locator('.mind-ship-table select, .mind-ship-table button, .mind-ship-table input[type=checkbox]')).toHaveCount(0)
  await page.getByLabel('等级 1', {exact: true}).fill('106')
  await page.getByLabel('等级 1', {exact: true}).press('Enter')
  await expect(page.locator('.mind-totals strong').first()).toHaveText('960')
  await expect(page.locator('.mind-intro')).toContainText('数据已保存到当前实例')
  await page.reload()
  await expect(page.getByLabel('等级 1', {exact: true})).toHaveValue('106')
  await expect(page.locator('.mind-ship-table .mind-rarity')).toHaveText('精锐')
  await expect(page.locator('.mind-totals strong').first()).toHaveText('960')
  for (const invalid of ['0', '126', '100.5']) {
    await page.getByLabel('等级 1', {exact: true}).fill(invalid)
    await page.getByLabel('等级 1', {exact: true}).press('Enter')
    await expect(page.getByLabel('等级 1', {exact: true})).toHaveValue('106')
  }
})

test('等级自动保存冲突保留草稿，不覆盖已保存的数据', async ({page}) => {
  let rejectSave = false
  await page.routeWebSocket('**/api/v1/ws', socket => {
    const server = socket.connectToServer()
    socket.onMessage(message => {
      const request = JSON.parse(String(message))
      if (rejectSave && request.method === 'mind.save') {
        socket.send(JSON.stringify({v: 1, type: 'response', id: request.id, ok: false,
          error: {code: 'CONFLICT', message: '舰船数据已变化，请重新载入后再保存'}}))
      } else server.send(message)
    })
    server.onMessage(message => socket.send(message))
  })
  await page.goto('/#/i/demo-alt/mind-calculator')
  const add = page.locator('.mind-add')
  await add.getByLabel('船名', {exact: true}).fill('拉菲')
  await add.getByRole('button', {name: '添加舰船'}).click()
  await page.getByRole('button', {name: '保存舰船数据'}).click()
  await expect(page.locator('.mind-intro')).toContainText('数据已保存到当前实例')
  rejectSave = true
  await page.getByLabel('等级 1', {exact: true}).fill('105')
  await page.getByLabel('等级 1', {exact: true}).press('Enter')
  await expect(page.getByText('舰船数据已变化，请重新载入后再保存', {exact: true})).toBeVisible()
  await expect(page.getByLabel('等级 1', {exact: true})).toHaveValue('105')
  await expect(page.locator('.mind-intro')).toContainText('草稿尚未保存')
  await page.reload()
  await expect(page.getByLabel('等级 1', {exact: true})).toHaveValue('105')
  await page.getByRole('button', {name: '放弃草稿并载入'}).click()
  await expect(page.getByLabel('等级 1', {exact: true})).toHaveValue('100')
})

test('等级保存期间切换实例，返回后不会恢复已成功保存的旧草稿', async ({page}) => {
  let delay = false, saving = false, delivered = false
  const pending = new Set<string>()
  await page.routeWebSocket('**/api/v1/ws', socket => {
    const server = socket.connectToServer()
    socket.onMessage(message => {
      const request = JSON.parse(String(message))
      if (delay && request.method === 'mind.save') {pending.add(request.id); saving = true}
      server.send(message)
    })
    server.onMessage(message => {
      const response = JSON.parse(String(message))
      if (pending.delete(response.id)) setTimeout(() => {socket.send(message); delivered = true}, 1200)
      else socket.send(message)
    })
  })
  await page.goto('/#/i/demo-alt/mind-calculator')
  const add = page.locator('.mind-add')
  await add.getByLabel('船名', {exact: true}).fill('拉菲')
  await add.getByRole('button', {name: '添加舰船'}).click()
  await page.getByRole('button', {name: '保存舰船数据'}).click()
  await expect(page.locator('.mind-intro')).toContainText('数据已保存到当前实例')
  delay = true
  await page.getByLabel('等级 1', {exact: true}).fill('105')
  await page.getByLabel('等级 1', {exact: true}).press('Enter')
  await expect.poll(() => saving).toBe(true)
  await page.goto('/#/i/demo-main/mind-calculator')
  await expect.poll(() => delivered).toBe(true)
  await page.goto('/#/i/demo-alt/mind-calculator')
  await expect(page.getByLabel('等级 1', {exact: true})).toHaveValue('105')
  await expect(page.locator('.mind-intro')).toContainText('数据已保存到当前实例')
})

test('改造同级保留改造条目，II型独立，联动舰保存到排除项', async ({page}) => {
  await page.goto('/#/i/demo-alt/mind-calculator')
  const add = page.locator('.mind-add')
  for (const name of ['卡辛', '卡辛.改', '约克城', '约克城II', 'DEAD MASTER']) {
    await add.getByLabel('船名', {exact: true}).fill(name)
    await add.getByRole('button', {name: '添加舰船'}).click()
  }
  await expect(page.locator('.mind-ship-table tbody tr')).toHaveCount(4)
  await expect(page.locator('.mind-ship-table input[value="卡辛"]')).toHaveCount(0)
  await expect(page.locator('.mind-ship-table input[value="卡辛.改"]')).toBeVisible()
  await expect(page.locator('.mind-ship-table input[value="约克城"]')).toBeVisible()
  await expect(page.locator('.mind-ship-table input[value="约克城II"]')).toBeVisible()
  const collaboration = page.locator('.mind-ship-table tbody tr').filter({has: page.locator('input[value="DEAD MASTER"]')})
  await expect(collaboration).toContainText('已排除')
  await expect(page.locator('.mind-totals strong').first()).toHaveText('5,280')
  await page.getByRole('button', {name: '保存舰船数据'}).click()
  await page.reload()
  await page.getByRole('combobox', {name: '筛选状态', exact: true}).click()
  await page.getByRole('option', {name: '已排除', exact: true}).click()
  await expect(page.locator('.mind-ship-table tbody tr')).toHaveCount(1)
  await expect(page.locator('.mind-ship-table input[value="DEAD MASTER"]')).toBeVisible()
})

test('未保存草稿在重载和实例切换后恢复', async ({page}) => {
  await page.goto('/#/i/demo-alt/mind-calculator')
  await page.getByLabel('最低扫描等级').fill('90')
  await page.getByLabel('最高扫描等级').fill('115')
  await page.getByRole('button', {name: '自动扫描船坞', exact: true}).click()
  await page.getByRole('button', {name: '停止扫描', exact: true}).click()
  const add = page.locator('.mind-add')
  await add.getByLabel('船名', {exact: true}).fill('热心')
  await add.getByRole('button', {name: '添加舰船'}).click()
  await expect(page.locator('.mind-intro')).toContainText('草稿尚未保存')
  await page.reload()
  await expect(page.getByLabel('最低扫描等级')).toHaveValue('90')
  await expect(page.getByLabel('最高扫描等级')).toHaveValue('115')
  await expect(page.locator('.mind-ship-table input[value="热心"]')).toBeVisible()
  await expect(page.locator('.mind-totals')).toContainText('880')
  await page.goto('/#/i/demo-main/mind-calculator')
  await expect(page.locator('.mind-ship-table input[value="热心"]')).toHaveCount(0)
  await page.goto('/#/i/demo-alt/mind-calculator')
  await expect(page.locator('.mind-ship-table input[value="热心"]')).toBeVisible()
  await expect(page.locator('.mind-intro')).toContainText('草稿尚未保存')
})

test('国服实例可按包含边界的等级范围启动扫描，错误范围不能启动', async ({page}) => {
  const requests: Array<{method: string; params: Record<string, unknown>}> = []
  page.on('websocket', ws => ws.on('framesent', frame => {
    try {requests.push(JSON.parse(String(frame.payload)))} catch { /* 忽略非 JSON 帧。 */ }
  }))
  await page.goto('/#/i/demo-main/mind-calculator')
  const scan = page.getByRole('button', {name: '自动扫描船坞', exact: true})
  await expect(scan).toBeEnabled()
  await page.getByLabel('最低扫描等级').fill('115')
  await page.getByLabel('最高扫描等级').fill('90')
  await scan.click()
  await expect(page.getByText('等级范围必须满足 1 ≤ 最低等级 ≤ 最高等级 ≤ 125', {exact: true})).toBeVisible()
  expect(requests.some(request => request.method === 'tasks.run')).toBeFalsy()
  await page.getByLabel('最低扫描等级').fill('90')
  await page.getByLabel('最高扫描等级').fill('115')
  await scan.click()
  await expect(page.getByRole('button', {name: '停止扫描', exact: true})).toBeVisible()
  const patchIndex = requests.findIndex(request => request.method === 'config.patch')
  const runIndex = requests.findIndex(request => request.method === 'tasks.run')
  expect(patchIndex).toBeGreaterThanOrEqual(0)
  expect(runIndex).toBeGreaterThan(patchIndex)
  expect(requests[patchIndex].params.changes).toEqual([
    {path: 'MindCalculatorScan.MindCalculator.MinLevel', value: 90},
    {path: 'MindCalculatorScan.MindCalculator.MaxLevel', value: 115},
  ])
  await page.getByRole('button', {name: '停止扫描', exact: true}).click()
  await page.reload()
  await expect(page.getByLabel('最低扫描等级')).toHaveValue('90')
  await expect(page.getByLabel('最高扫描等级')).toHaveValue('115')
})

test('批量截图中坏文件显示原因并保留成功识别的三行', async ({page}) => {
  await page.goto('/#/i/demo-alt/mind-calculator')
  await expect(page.locator('.mind-add')).toBeVisible()
  await page.locator('input[type=file][accept^="image/png"]').setInputFiles([
    {name: '损坏.png', mimeType: 'image/png', buffer: Buffer.from('broken image')},
    {name: '船坞.png', mimeType: 'image/png', buffer: await readFile(fileURLToPath(new URL('../../tests/fixtures/mind_dock_fleet_priority.png', import.meta.url)))},
  ])
  await expect(page.locator('.mind-ship-table tbody tr')).toHaveCount(21, {timeout: 30_000})
  await expect(page.getByText(/损坏.png: 截图识别失败/)).toBeVisible()
  await expect(page.getByRole('status').filter({hasText: '已处理 21 条数据，清单保留 21 艘舰船'})).toBeVisible()
})
