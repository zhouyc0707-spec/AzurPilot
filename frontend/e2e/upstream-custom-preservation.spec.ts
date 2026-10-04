import {expect, test} from '@playwright/test'

test('上游合入后旧版统计、顶栏快捷入口和新统计页同时可用', async ({page}, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.setViewportSize({width: 1920, height: 1100})
  await page.addInitScript(() => {
    localStorage.setItem('azurpilot.theme', 'legacy-light')
    localStorage.setItem('azurpilot.language', 'zh-CN')
    localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
  })
  await page.goto('/#/i/testpilot/statistics')
  const legacy = page.locator('.legacy-stats')
  await expect(legacy).toBeVisible()
  await expect(legacy.locator('.legacy-stats-charts > .legacy-stats-section')).toHaveCount(5)
  await expect(legacy.locator('.legacy-stats-dashboard')).toHaveCount(1)
  await expect(legacy.locator('.legacy-commission-card')).toHaveCount(5)
  await expect(legacy.locator('.legacy-ap-card')).toHaveCSS('background-color', 'rgb(255, 255, 255)')
  await expect(page.getByRole('button', {name: '重置图表', exact: true})).toHaveCount(0)
  await expect(legacy.locator('.resource-card-body').first()).toHaveCSS('background-color', 'rgb(255, 255, 255)')
  await expect(page.getByRole('button', {name: '重启服务', exact: true})).toBeVisible()
  await expect(page.getByRole('button', {name: '切换新旧 UI', exact: true})).toBeVisible()
  await page.screenshot({path: testInfo.outputPath('旧版统计.png'), fullPage: true})

  await page.goto('/#/i/testpilot/overview')
  await page.getByRole('button', {name: '切换到统计', exact: true}).click()
  await expect(legacy).toBeVisible()
  await expect(legacy.locator('.legacy-stats-dashboard')).toHaveCount(0)
  await page.getByRole('button', {name: '切换新旧 UI', exact: true}).click()
  await page.goto('/#/i/testpilot/statistics')
  await expect(page.locator('.legacy-stats')).toHaveCount(0)
  await expect(page.getByRole('tab', {name: '仓库物品', exact: true})).toBeVisible()
  await page.screenshot({path: testInfo.outputPath('新版统计.png'), fullPage: true})
  expect(errors).toEqual([])
})

for (const theme of ['light', 'legacy-light']) {
  test(`${theme} 上游内容搜索定位后保留模拟器管理定制`, async ({page}) => {
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    await page.addInitScript(value => {
      localStorage.setItem('azurpilot.theme', value)
      localStorage.setItem('azurpilot.language', 'zh-CN')
      localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
    }, theme)
    await page.goto('/#/i/testpilot/overview')
    await page.getByRole('button', {name: '展开任务搜索', exact: true}).click()
    await page.getByRole('textbox', {name: '搜索任务', exact: true}).fill('RestartIntervalHours')
    const hit = page.locator('.nav-search-hit').filter({hasText: 'EmulatorManagement.RestartIntervalHours'})
    await expect(hit).toHaveCount(1)
    await hit.click()
    await expect(page).toHaveURL(/\/i\/testpilot\/task\/Alas$/)
    await expect(page.locator('[id="Alas.EmulatorManagement.RestartIntervalHours"]')).toBeInViewport()
    await expect(page.locator('.is-search-target')).toBeVisible()
    await expect(page.getByTestId('emulator-uptime')).toBeVisible()
    await expect(page.getByRole('button', {name: '切换新旧 UI', exact: true})).toBeVisible()
    expect(errors).toEqual([])
  })
}
