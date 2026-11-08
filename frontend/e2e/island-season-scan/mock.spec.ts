import {expect, test} from '@playwright/test'

for (const [theme, instance] of [['legacy-light', 'demo-main'], ['light', 'demo-alt']]) {
  test(`${theme} 读取快速结束后刷新并编辑扫描目标，启动失败保留错误`, async ({page}, testInfo) => {
    const errors: string[] = []
    const actions: string[] = []
    let scans = 0
    let scanRequest = ''
    let initialRequest: string | Buffer = ''
    let seeded = false
    let refreshRequest = ''
    let releaseScan: (() => void) | undefined
    let releaseRefresh: (() => void) | undefined
    let goalEdits = 0
    page.on('pageerror', error => errors.push(error.message))
    await page.addInitScript(value => {
      localStorage.setItem('azurpilot.theme', value)
      localStorage.setItem('azurpilot.language', 'zh-CN')
      localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
    }, theme)
    // 用模拟服务的内存配置模拟扫描写回；不启动任何真实工具或游戏进程。
    await page.routeWebSocket('**/api/v1/ws', socket => {
      const server = socket.connectToServer()
      socket.onMessage(message => {
        const request = JSON.parse(String(message))
        if (request.method === 'config.patch' && request.params.changes.some((item: {path: string}) => item.path.endsWith('.ImportedTargets'))) goalEdits++
        if (request.method === 'config.get' && !seeded) {
          seeded = true; initialRequest = message
          server.send(JSON.stringify({v: 1, type: 'request', id: 'mock-season-seed', method: 'config.patch', params: {instance, changes: [
            {path: 'IslandSeasonalProduction.IslandSeasonWinter.ImportedTargets', value: '{"茶叶 (2014)":200}'},
          ]}}))
          return
        }
        if (request.method === 'config.get' && scans === 1) refreshRequest = request.id
        if (request.method === 'tasks.run') {
          actions.push(request.params.task)
          expect(request.params.task).toBe('IslandSeasonTargetScan')
          if (++scans > 1) {
            socket.send(JSON.stringify({v: 1, type: 'response', id: request.id, ok: false, error: {code: 'INSTANCE_RUNNING', message: '实例已在运行，请先停止当前任务'}}))
            return
          }
          scanRequest = request.id
          server.send(JSON.stringify({v: 1, type: 'request', id: 'mock-season-write', method: 'config.patch', params: {instance, changes: [
            {path: 'IslandSeasonalProduction.IslandSeasonWinter.shiitake_chicken_soup', value: 18},
            {path: 'IslandSeasonalProduction.IslandSeasonWinter.ImportedTargets', value: '{"茶叶 (2014)":500,"墨盒 (3053)":10}'},
          ]}}))
          return
        }
        server.send(message)
      })
      server.onMessage(message => {
        const response = JSON.parse(String(message))
        if (response.id === 'mock-season-seed') {
          expect(response.ok, JSON.stringify(response.error)).toBe(true)
          server.send(initialRequest)
          return
        }
        if (response.id === 'mock-season-write') {
          expect(response.ok, JSON.stringify(response.error)).toBe(true)
          // 工具已快速完成，instances.list不会出现running；前端仍须读取新配置。
          releaseScan = () => socket.send(JSON.stringify({v: 1, type: 'response', id: scanRequest, ok: true, data: {instance, status: 'running'}}))
          return
        }
        if (response.id === refreshRequest && !releaseRefresh) {
          releaseRefresh = () => socket.send(message)
          return
        }
        socket.send(message)
      })
    })

    await page.goto(`/#/i/${instance}/task/IslandPlan`)
    const season = page.locator('#group-IslandPlan').getByRole('combobox', {name: '当前季节', exact: true})
    await season.click()
    await page.getByRole('option', {name: '冬季', exact: true}).click()
    await page.goto(`/#/i/${instance}/task/IslandSeasonalProduction`)
    const scan = page.getByRole('button', {name: '自动读取季节目标', exact: true})
    const group = page.locator('#group-IslandSeasonWinter')
    const tea = group.getByRole('textbox', {name: '茶叶', exact: true})
    await expect(tea).toHaveValue('200')
    await scan.click()
    await expect(tea).toBeDisabled()
    await expect.poll(() => !!releaseScan).toBe(true)
    releaseScan!()
    await expect.poll(() => !!releaseRefresh).toBe(true)
    // 写回已完成但新配置尚未到达，旧行必须继续锁住，不能整段覆盖新导入的墨盒。
    await expect(tea).toBeDisabled()
    expect(goalEdits).toBe(0)
    releaseRefresh!()
    await expect(page.getByText('读取进程已结束，已刷新目标；请查看日志确认识别结果。', {exact: true})).toBeVisible()
    await expect(group.locator('[id$=".shiitake_chicken_soup"]')).toHaveValue('18')
    await expect(tea).toBeEnabled()
    await expect(tea).toHaveValue('500')
    await expect(group.getByRole('textbox', {name: '墨盒', exact: true})).toHaveValue('10')
    await tea.fill('-1'); await tea.blur()
    await expect(tea).toHaveAttribute('aria-invalid', 'true')
    await scan.click()
    expect(scans).toBe(1)
    await tea.fill('600'); await tea.blur()
    await expect(tea).not.toHaveAttribute('aria-invalid', 'true')
    await page.reload()
    await expect(tea).toHaveValue('600')
    await page.setViewportSize({width: 1440, height: 2300})
    await page.screenshot({path: testInfo.outputPath(`自动读取季节目标-${theme}.png`), fullPage: true, animations: 'disabled'})
    await scan.click()
    await expect(page.getByText('实例已在运行，请先停止当前任务', {exact: true})).toBeVisible()
    await expect(tea).toBeEnabled()
    await expect(tea).toHaveValue('600')
    await page.screenshot({path: testInfo.outputPath(`自动读取互斥提示-${theme}.png`), fullPage: true, animations: 'disabled'})
    expect(actions).toEqual(['IslandSeasonTargetScan', 'IslandSeasonTargetScan'])
    expect(errors).toEqual([])
  })
}
