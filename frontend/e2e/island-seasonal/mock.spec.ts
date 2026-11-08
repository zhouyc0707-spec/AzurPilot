import {expect, test} from '@playwright/test'

const seasons = [
  {key: 'winter', title: '冬季', group: 'IslandSeasonWinter', item: 'shiitake_chicken_soup', raw: '冬菇'},
  {key: 'spring', title: '春季', group: 'IslandSeasonSpring', item: 'double_bamboo_shoots', raw: '春笋'},
  {key: 'summer', title: '夏季', group: 'IslandSeasonSummer', item: 'amaranth_rice_ball', raw: '红米苋'},
  {key: 'autumn', title: '秋季', group: 'IslandSeasonAutumn', item: 'persimmon_cake', raw: '花生'},
]

for (const [theme, instance] of [['legacy-light', 'demo-main'], ['light', 'demo-alt']]) {
  test(`${theme} 季节目标跟随全局季节、独立保存并校验数量`, async ({page}, testInfo) => {
    const errors: string[] = []
    const writes: {path: string; value: unknown}[] = []
    const actions: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    await page.emulateMedia({reducedMotion: 'reduce'})
    await page.addInitScript(value => {
      localStorage.setItem('azurpilot.theme', value)
      localStorage.setItem('azurpilot.language', 'zh-CN')
      localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
    }, theme)
    // 配置只写入模拟服务内存，记录所有操作，确保设置页不会启动游戏任务。
    await page.routeWebSocket('**/api/v1/ws', socket => {
      const server = socket.connectToServer()
      const patches = new Map<string, {path: string; value: unknown}[]>()
      socket.onMessage(message => {
        const request = JSON.parse(String(message))
        if (request.method === 'config.patch') patches.set(request.id, request.params.changes)
        if (request.method.startsWith('tasks.') || request.method === 'scheduler.start') actions.push(request.method)
        server.send(message)
      })
      server.onMessage(message => {
        const response = JSON.parse(String(message))
        if (response.ok && patches.has(response.id)) writes.push(...patches.get(response.id)!)
        socket.send(message)
      })
    })

    for (const [index, season] of seasons.entries()) {
      await page.goto(`/#/i/${instance}/task/IslandPlan`)
      const selector = page.locator('#group-IslandPlan').getByRole('combobox', {name: '当前季节', exact: true})
      await selector.click()
      await page.getByRole('option', {name: season.title, exact: true}).click()
      await expect.poll(() => writes.some(change =>
        change.path === 'IslandPlan.IslandPlan.Season' && change.value === season.key)).toBe(true)
      await page.goto(`/#/i/${instance}/task/IslandSeasonalProduction`)
      const group = page.locator(`#group-${season.group}`)
      await expect(group).toBeVisible()
      await expect(group.locator('.field-control input')).toHaveCount(8)
      await expect(group.getByText(new RegExp(`当季采集原料：.*${season.raw}`))).toBeVisible()
      for (const other of seasons.filter(candidate => candidate.key !== season.key)) {
        await expect(page.locator(`#group-${other.group}`)).toHaveCount(0)
      }
      const path = `IslandSeasonalProduction.${season.group}.${season.item}`
      const target = group.locator(`[id="${path}"]`)
      await target.fill(String(37 + index))
      await target.blur()
      await expect.poll(() => writes.some(change => change.path === path && change.value === 37 + index)).toBe(true)
      await target.fill('-1')
      await target.blur()
      await expect(target).toHaveAttribute('aria-invalid', 'true')
      await target.fill('1.5')
      await target.blur()
      await expect(target).toHaveAttribute('aria-invalid', 'true')
      await target.fill(String(37 + index))
      await target.blur()
      await expect(target).not.toHaveAttribute('aria-invalid', 'true')
      expect(writes.some(change => change.path === path && [-1, 1.5].includes(change.value as number))).toBe(false)
    }

    // 再切回冬季，验证其目标没有被其余三个季节覆盖。
    await page.goto(`/#/i/${instance}/task/IslandPlan`)
    await page.locator('#group-IslandPlan').getByRole('combobox', {name: '当前季节', exact: true}).click()
    await page.getByRole('option', {name: '冬季', exact: true}).click()
    await expect.poll(() => writes.filter(change =>
      change.path === 'IslandPlan.IslandPlan.Season' && change.value === 'winter').length).toBe(2)
    await page.goto(`/#/i/${instance}/task/IslandSeasonalProduction`)
    await page.reload()
    const winter = page.locator('#group-IslandSeasonWinter')
    await expect(winter.locator('[id$=".shiitake_chicken_soup"]')).toHaveValue('37')
    await expect(page.locator('.tool-log-panel')).toHaveCount(0)
    await page.setViewportSize({width: 1440, height: 1800})
    await expect(winter.locator('[id$=".winter_bouquet"]')).toBeInViewport()
    await winter.screenshot({path: testInfo.outputPath(`季节物品生产-${theme}.png`), animations: 'disabled'})
    await page.screenshot({path: testInfo.outputPath(`季节物品生产页面-${theme}.png`), fullPage: true, animations: 'disabled'})
    expect(actions).toEqual([])
    expect(errors).toEqual([])
  })
}
