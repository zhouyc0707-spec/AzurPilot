import {expect, test, type Page} from '@playwright/test'
import {routeRpc} from '../rpc-route'

const cases = [
  ...['legacy-light', 'legacy-dark', 'modern-light', 'modern-dark', 'minimal'].map(theme => ({theme, width: 1920})),
  ...['legacy-light', 'modern-light'].flatMap(theme => [1200, 980, 760].map(width => ({theme, width}))),
]

async function prepare(page: Page, theme: string, actions: string[]) {
  await page.emulateMedia({reducedMotion: 'reduce'})
  await page.addInitScript(value => {
    localStorage.setItem('azurpilot.theme', value)
    localStorage.setItem('azurpilot.language', 'zh-CN')
    localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
    localStorage.setItem('azurpilot.rail-visible', 'shown')
    localStorage.setItem('azurpilot.legacy-rail-view', 'directory')
    localStorage.setItem('azurpilot.layout.scheduler-position', 'right')
  }, theme)
  // 只改模拟配置响应的当季值，不保存配置或触发任务。
  await routeRpc(page, socket => {
    const server = socket.connectToServer()
    const configs = new Set<string>()
    socket.onMessage(message => {
      const request = JSON.parse(message)
      if (request.method === 'config.get') configs.add(request.id)
      if (['tasks.run', 'tasks.runOnce', 'scheduler.start'].includes(request.method)) actions.push(request.method)
      server.send(message)
    })
    server.onMessage(message => {
      const response = JSON.parse(message)
      if (response.ok && configs.delete(response.id)) {
        response.result.values.IslandPlan.IslandPlan.Season = 'winter'
        socket.send(JSON.stringify(response))
      } else socket.send(message)
    })
  })
}

async function assertLayout(page: Page, theme: string, width: number) {
  const legacy = theme.startsWith('legacy')
  const panel = page.locator(legacy ? '.task-config-legacy' : '.task-config-page.task-config-modern')
  const title = panel.locator(':scope > h1')
  // 读屏标题必须脱离网格，不能占用参数列的第一个单元格。
  await expect.poll(() => title.evaluate(element => {
    const style = getComputedStyle(element)
    const bounds = element.getBoundingClientRect()
    return style.position === 'absolute' && bounds.width <= 1 && bounds.height <= 1
  })).toBe(true)
  const settings = panel.locator('.task-config-settings')
  const settingsBounds = await settings.boundingBox()
  expect(settingsBounds).not.toBeNull()
  expect(settingsBounds!.width).toBeGreaterThan(width > 1319 ? 500 : width > 1089 ? 420 : width > 950 ? 360 : width - 80)
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)

  if (width <= 950) return
  const rail = panel.locator('.task-config-rail-slot')
  if (legacy || width >= 1090) {
    await expect(rail).toBeVisible()
    const bounds = await rail.boundingBox()
    expect(bounds).not.toBeNull()
    expect(Math.abs(bounds!.y - settingsBounds!.y)).toBeLessThan(3)
    expect(bounds!.x).toBeGreaterThan(settingsBounds!.x + settingsBounds!.width)
  }
  if (legacy) {
    await expect(panel.locator('.rail-fold-chip')).toHaveCount(0)
    return
  }
  const directory = panel.locator('.task-config-directory')
  if (width >= 1320) {
    await expect(directory).toBeVisible()
    const bounds = await directory.boundingBox()
    expect(bounds).not.toBeNull()
    expect(Math.abs(bounds!.y - settingsBounds!.y)).toBeLessThan(3)
    expect(bounds!.x).toBeGreaterThan(settingsBounds!.x + settingsBounds!.width)
    return
  }
  const directoryChip = panel.locator('.rail-fold-chip-directory')
  await expect(directory).toBeHidden()
  await directoryChip.click()
  await expect(directory).toBeVisible()
  await expect(directoryChip).toHaveAttribute('aria-expanded', 'true')
  await page.keyboard.press('Escape')
  await expect(directory).toBeHidden()
  if (width < 1090) {
    const railChip = panel.locator('.rail-fold-chip-rail')
    await expect(rail).toBeHidden()
    await railChip.click()
    await expect(rail).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(rail).toBeHidden()
  }
}

for (const {theme, width} of cases) {
  test(`${theme} ${width}px 任务设置内容与侧列完整排布`, async ({page}, testInfo) => {
    const actions: string[] = []
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    await page.setViewportSize({width, height: 1080})
    await prepare(page, theme, actions)
    for (const task of ['IslandSeasonalProduction', 'Main']) {
      await page.goto(`/#/i/demo-main/task/${task}`)
      const firstGroup = page.locator(task === 'IslandSeasonalProduction' ? '#group-IslandSeasonWinter' : '#group-Scheduler')
      await expect(firstGroup).toBeVisible()
      await page.screenshot({path: testInfo.outputPath(`${task}.png`), animations: 'disabled'})
      await assertLayout(page, theme, width)
      if (task === 'IslandSeasonalProduction') {
        const scan = page.getByRole('region', {name: '自动读取季节目标', exact: true})
        const bounds = await scan.boundingBox()
        const settings = await page.locator('.task-config-settings').boundingBox()
        expect(bounds).not.toBeNull()
        expect(settings).not.toBeNull()
        expect(bounds!.width).toBeGreaterThan(settings!.width - 8)
        const heading = await scan.getByRole('heading', {name: '自动读取季节目标', exact: true}).boundingBox()
        expect(heading).not.toBeNull()
        expect(heading!.height).toBeLessThan(60)
      }
    }
    expect(actions).toEqual([])
    expect(errors).toEqual([])
  })
}
