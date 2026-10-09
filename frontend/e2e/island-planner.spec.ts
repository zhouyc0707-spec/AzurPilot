import {expect, test} from '@playwright/test'

for (const theme of ['legacy-light', 'light']) {
  test(`${theme} 自动生产规划隐藏科技明细并保存比例数值`, async ({page}) => {
    await page.addInitScript(theme => {
      localStorage.setItem('azurpilot.theme', theme)
      localStorage.setItem('azurpilot.language', 'zh-CN')
      localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
    }, theme)
    await page.route('https://www.clarity.ms/**', route => route.abort())
    let patchId: string | undefined
    let saved = false
    let resetId: string | undefined
    let reset = false
    page.on('websocket', socket => {
      socket.on('framesent', frame => {
        const request = JSON.parse(String(frame.payload))
        if (request.method !== 'config.patch') return
        for (const change of request.params.changes) {
          if (change.path !== 'IslandPlan.IslandProductionPlanner.FieldsEfficiency') continue
          if (change.value === 0.04) patchId = request.id
          if (change.value === 0) resetId = request.id
        }
      })
      socket.on('framereceived', frame => {
        const response = JSON.parse(String(frame.payload))
        if (patchId && response.id === patchId && response.ok) saved = true
        if (resetId && response.id === resetId && response.ok) reset = true
      })
    })
    await page.goto('/#/i/testpilot/task/IslandPlan')
    await expect(page.getByRole('switch', {name: '自动生产规划', exact: true})).toBeVisible()
    await expect(page.locator('textarea[id="IslandPlan.IslandProductionPlanner.TechnologyStatus"]')).toHaveCount(0)
    await expect(page.getByRole('switch', {name: '重新扫描科技', exact: true})).toBeVisible()
    await expect(page.getByRole('textbox', {name: '规划状态', exact: true})).toBeVisible()
    const efficiency = page.getByRole('combobox', {name: '农田额外效率', exact: true})
    await expect(efficiency).toContainText('0%')
    await efficiency.click()
    await expect(page.getByRole('option', {name: '12%', exact: true})).toBeVisible()
    await page.getByRole('option', {name: '4%', exact: true}).click()
    await expect(efficiency).toContainText('4%')
    await expect.poll(() => saved).toBe(true)
    await page.reload()
    await expect(efficiency).toContainText('4%')
    // 两个主题共用同一隔离实例，恢复零值供下一项独立检查。
    await efficiency.click()
    await page.getByRole('option', {name: '0%', exact: true}).click()
    await expect(efficiency).toContainText('0%')
    await expect.poll(() => reset).toBe(true)
  })
}
