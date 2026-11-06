import {expect, test} from '@playwright/test'

for (const [theme, instance] of [['legacy-light', 'demo-main'], ['light', 'demo-alt']]) {
  test(`${theme} 独立规划器保存季节与优先级，旧自动规划和报告不再显示`, async ({page}, testInfo) => {
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
    // 模拟服务只使用公开模板，所有设置变更仅保存在内存，不启动任务。
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
    await page.goto(`/#/i/${instance}/task/IslandProductionPlanner`)
    const group = page.locator('#group-IslandProductionPlanner')
    const season = group.getByRole('combobox', {name: '季节', exact: true})
    const priority = group.getByRole('combobox', {name: '优先级', exact: true})
    const apply = group.getByRole('switch', {name: '写入配置', exact: true})
    await expect(season).toContainText('自动（当前季节）')
    await expect(priority).toContainText('季节 PT')
    await expect(apply).not.toBeChecked()
    await expect(group.getByRole('switch', {name: '填满空余岗位', exact: true})).toBeChecked()
    await expect(group.getByRole('switch', {name: '自动岗位数', exact: true})).toBeChecked()
    await expect(group.locator('[id$=".UnlockedFish"]')).toBeVisible()
    await season.click()
    await page.getByRole('option', {name: '第 5 季（冬季）', exact: true}).click()
    await priority.click()
    await page.getByRole('option', {name: '岛币', exact: true}).click()
    await apply.click()
    for (const [field, value] of [['Season', 'season_5'], ['Priority', 'coins'], ['ApplySettings', true]]) {
      await expect.poll(() => writes.some(change =>
        change.path === `IslandProductionPlanner.IslandProductionPlanner.${field}` && change.value === value)).toBe(true)
    }
    await page.reload()
    await expect(season).toContainText('第 5 季（冬季）')
    await expect(priority).toContainText('岛币')
    await expect(apply).toBeChecked()
    await group.screenshot({path: testInfo.outputPath(`独立规划器-${theme}.png`), animations: 'disabled'})

    await page.goto(`/#/i/${instance}/task/IslandPlan`)
    await expect(page.locator('#group-IslandPlan')).toBeVisible()
    await expect(page.locator('#group-IslandProductionPlanner')).toHaveCount(0)
    await expect(page.getByRole('switch', {name: '自动生产规划', exact: true})).toHaveCount(0)
    await expect(page.getByRole('region', {name: '生产经营计划明细', exact: true})).toHaveCount(0)
    expect(writes.every(change => !change.path.startsWith('IslandPlan.IslandProductionPlanner.'))).toBe(true)
    expect(actions).toEqual([])
    expect(errors).toEqual([])
  })
}
