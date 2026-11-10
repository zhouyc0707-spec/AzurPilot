import {expect, test} from '@playwright/test'

const limited = ['shiitake_chicken_soup', 'green_jujube_porridge', 'water_chestnut_cake', 'sugar_tangerine_daifuku', 'sugar_tangerine', 'green_jujube', 'plum_blossom_sachet', 'winter_bouquet']
const imported = ['茶叶 (2014)', '柑橘 (2017)', '芒果 (2019)', '牛油果 (2021)', '淡水鱼肉 (2521)', '海水鱼肉 (2522)', '禽肉土豆拼盘 (3030)', '芒果糯米饭 (3025)', '草莓夏洛特 (3028)', '银矿 (2705)', '典雅之木 (2803)', '香囊 (3039)', '墨盒 (3053)']

for (const [theme, instance] of [['legacy-light', 'demo-main'], ['light', 'demo-alt']]) {
  test(`${theme} 固定和导入目标显示库存进度，失效及实例隔离`, async ({page}, testInfo) => {
    let seeded = false
    let initialRequest: string | Buffer = ''
    let status = 'observed'
    const seasons: Record<string, string> = {}
    const actions: string[] = []
    const errors: string[] = []
    const items = [
      ...limited.map((key, index) => ({itemId: 6000 + index, key, stock: [300, 20, 800][index] ?? null, inProduction: index === 0 ? 100 : null})),
      ...imported.map((key, index) => ({itemId: Number(key.match(/\((\d+)\)$/)![1]), key: `imported_${index}`, stock: index === 0 ? 300 : null, inProduction: index === 0 ? 50 : null})),
    ]
    page.on('pageerror', error => errors.push(error.message))
    await page.addInitScript(value => {
      localStorage.setItem('azurpilot.theme', value)
      localStorage.setItem('azurpilot.language', 'zh-CN')
      localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
    }, theme)
    await page.routeWebSocket('**/api/v1/ws', socket => {
      const server = socket.connectToServer()
      socket.onMessage(message => {
        const request = JSON.parse(String(message))
        if (request.method.startsWith('tasks.')) actions.push(request.method)
        if (request.method === 'island.seasonProgress') {
          const active = request.params.instance === instance
          socket.send(JSON.stringify({v: 1, type: 'response', id: request.id, ok: true, result: {
            season: active ? 'winter' : seasons[request.params.instance] ?? 'spring', status: active ? status : 'missing', updatedAt: active ? '2026-11-10T12:00:00+08:00' : null, items: active ? items : [],
          }}))
          return
        }
        if (request.method === 'config.get' && !seeded) {
          seeded = true; initialRequest = message
          server.send(JSON.stringify({v: 1, type: 'request', id: 'mock-progress-seed', method: 'config.patch', params: {instance, changes: [
            {path: 'IslandPlan.IslandPlan.Season', value: 'winter'},
            ...limited.map((key, index) => ({path: `IslandSeasonalProduction.IslandSeasonWinter.${key}`, value: index === 1 ? 0 : index === 0 ? 500 : 100})),
            {path: 'IslandSeasonalProduction.IslandSeasonWinter.ImportedTargets', value: JSON.stringify(Object.fromEntries(imported.map(key => [key, 500])))},
          ]}}))
          return
        }
        server.send(message)
      })
      server.onMessage(message => {
        const response = JSON.parse(String(message))
        if (response.result?.values?.IslandPlan) seasons[response.result.instance] = response.result.values.IslandPlan.IslandPlan.Season
        if (response.id === 'mock-progress-seed') {
          expect(response.ok, JSON.stringify(response.error)).toBe(true)
          server.send(initialRequest)
        } else socket.send(message)
      })
    })
    await page.goto(`/#/i/${instance}/task/IslandSeasonalProduction`)
    const soup = page.locator('[id$=".shiitake_chicken_soup"]').locator('..')
    const porridge = page.locator('[id$=".green_jujube_porridge"]').locator('..')
    const cake = page.locator('[id$=".water_chestnut_cake"]').locator('..')
    const tea = page.getByRole('textbox', {name: '茶叶', exact: true}).locator('..')
    await expect(page.locator('.island-target-progress')).toHaveCount(21)
    await expect(soup).toContainText('已完成 300/500')
    await expect(soup).toContainText('在制：100')
    await expect(porridge).toContainText('未设置目标')
    await expect(porridge).toContainText('实际库存：20')
    await expect(cake).toContainText('已完成 100/100')
    await expect(cake).toContainText('实际库存：800')
    await expect(tea).toContainText('已完成 300/500')
    await expect(page.locator('.island-progress-summary time')).toHaveAttribute('datetime', '2026-11-10T12:00:00+08:00')
    const screenshot = `库存进度-${theme}.png`
    await page.screenshot({path: testInfo.outputPath(screenshot), fullPage: true, animations: 'disabled'})
    await soup.locator('..').screenshot({path: testInfo.outputPath(`目标行-${theme}.png`), animations: 'disabled'})
    await tea.locator('..').screenshot({path: testInfo.outputPath(`导入目标行-${theme}.png`), animations: 'disabled'})
    await soup.getByRole('textbox').fill('600')
    await soup.getByRole('textbox').blur()
    await expect(soup).toContainText('已完成 300/600')
    status = 'invalidated'
    await expect(soup).toContainText('历史库存：300', {timeout: 8000})
    await expect(soup.getByRole('progressbar')).toHaveCount(0)
    await page.screenshot({path: testInfo.outputPath(`失效记录-${theme}.png`), fullPage: true, animations: 'disabled'})
    await page.setViewportSize({width: 420, height: 960})
    await page.screenshot({path: testInfo.outputPath(`窄屏库存进度-${theme}.png`), fullPage: true, animations: 'disabled'})
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    const other = instance === 'demo-main' ? 'demo-alt' : 'demo-main'
    await page.goto(`/#/i/${other}/task/IslandSeasonalProduction`)
    await expect(page.locator('.island-progress-summary')).toContainText('尚未盘点')
    await expect(page.getByText('历史库存：300', {exact: true})).toHaveCount(0)
    expect(actions).toEqual([])
    expect(errors).toEqual([])
  })
}
