import {expect, test} from '@playwright/test'
import {readFile} from 'node:fs/promises'
import {fileURLToPath} from 'node:url'

test.beforeEach(async ({page}) => {
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
  await expect(page.locator('.mind-ship-table')).toContainText('同名合并')
  await page.getByRole('button', {name: '保存舰船数据'}).click()
  await expect(page.locator('.mind-intro')).toContainText('数据已保存到当前实例')
  const downloaded = page.waitForEvent('download')
  await page.getByRole('button', {name: '导出结果', exact: true}).click()
  const download = await downloaded
  expect(download.suggestedFilename()).toMatch(/\.xlsx$/)
  const filename = await download.path()
  const bytes = await readFile(filename!)
  await page.reload()
  await expect(page.locator('.mind-ship-table tbody tr')).toHaveCount(3)
  await page.getByRole('link', {name: '资源管理', exact: true}).click()
  await page.goto('/#/i/demo-alt/mind-calculator')
  await expect(page.getByText('没有符合条件的舰船', {exact: true})).toBeVisible()
  await page.locator('input[type=file][accept=".json,.csv,.xlsx"]').setInputFiles({name: '原计算结果.xlsx', mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', buffer: bytes})
  await expect(page.locator('.mind-ship-table tbody tr')).toHaveCount(3)
  await expect(page.locator('.mind-totals')).toContainText('4,260')
  await page.setViewportSize({width: 390, height: 844})
  await expect(page.getByRole('heading', {name: '心智单元计算器', exact: true})).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  expect(errors).toEqual([])
})

test('截图导入保持待核对状态并支持人工确认', async ({page}) => {
  await page.goto('/#/i/demo-alt/mind-calculator')
  await expect(page.locator('.mind-add')).toBeVisible()
  await page.locator('input[type=file][accept="image/png,image/jpeg"]').setInputFiles(fileURLToPath(new URL('../../tests/fixtures/fleet_names_vanguard.png', import.meta.url)))
  await expect(page.locator('.mind-ship-table tbody tr')).toHaveCount(21, {timeout: 30_000})
  await expect(page.locator('.mind-totals strong').nth(3)).toHaveText('21')
  await expect(page.locator('.mind-totals strong').first()).toHaveText('0')
  const row = page.locator('.mind-ship-table tbody tr').filter({has: page.locator('input[value="热心.改"]')})
  await row.getByRole('button', {name: '已核对', exact: true}).click()
  await expect(page.locator('.mind-totals')).toContainText('800')
  await page.getByRole('heading', {name: '心智单元计算器', exact: true}).scrollIntoViewIfNeeded()
  await page.screenshot({path: fileURLToPath(new URL('../test-results/mind-calculator-review.png', import.meta.url)), fullPage: true})
})

test('未保存草稿在重载和实例切换后恢复', async ({page}) => {
  await page.goto('/#/i/demo-alt/mind-calculator')
  const add = page.locator('.mind-add')
  await add.getByLabel('船名', {exact: true}).fill('热心')
  await add.getByRole('button', {name: '添加舰船'}).click()
  await expect(page.locator('.mind-intro')).toContainText('草稿尚未保存')
  await page.reload()
  await expect(page.locator('.mind-ship-table input[value="热心"]')).toBeVisible()
  await expect(page.locator('.mind-totals')).toContainText('880')
  await page.goto('/#/i/demo-main/mind-calculator')
  await expect(page.locator('.mind-ship-table input[value="热心"]')).toHaveCount(0)
  await page.goto('/#/i/demo-alt/mind-calculator')
  await expect(page.locator('.mind-ship-table input[value="热心"]')).toBeVisible()
  await expect(page.locator('.mind-intro')).toContainText('草稿尚未保存')
})
