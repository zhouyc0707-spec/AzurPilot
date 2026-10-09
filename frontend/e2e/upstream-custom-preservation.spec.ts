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

  test(`${theme} 卡片标题搜索定位保留桌面浮层与模拟器状态`, async ({page}, testInfo) => {
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    await page.addInitScript(value => {
      localStorage.setItem('azurpilot.theme', value)
      localStorage.setItem('azurpilot.language', 'zh-CN')
      localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
    }, theme)
    await page.goto('/#/i/testpilot/overview')
    await page.getByRole('button', {name: '展开任务搜索', exact: true}).click()
    const search = page.getByRole('textbox', {name: '搜索任务', exact: true})
    await search.fill('模拟器管理')
    const hit = page.locator('.nav-search-hit').filter({
      has: page.locator('.nav-search-hit-path', {hasText: /^EmulatorManagement$/}),
    })
    await expect(hit).toHaveCount(1)
    expect(await page.locator('.nav-search-hits').evaluate(element =>
      Boolean(element.compareDocumentPosition(element.parentElement!.querySelector('.task-nav')!) & Node.DOCUMENT_POSITION_FOLLOWING)
    )).toBe(true)
    await hit.click()
    await expect(page).toHaveURL(/\/i\/testpilot\/task\/Alas$/)
    const group = page.locator('#group-EmulatorManagement')
    await expect(group).toBeInViewport()
    await expect(group).toHaveClass(/is-search-target/)
    await expect(page.getByTestId('emulator-uptime')).toBeVisible()
    await group.screenshot({path: testInfo.outputPath(`search-group-${theme}.png`)})
    await search.fill('')
    await page.locator('.task-group-button').filter({hasText: '系统'}).click()
    await expect(page.locator('body > .task-submenu-flyout')).toBeVisible()
    await expect(page.locator('.task-nav .task-submenu-list')).toHaveCount(0)
    expect(errors).toEqual([])
  })

  test(`${theme} 启动器卡片开关与远端提示沿用上游契约`, async ({page}) => {
    const changes: boolean[] = []
    let enabled = false
    let remote = false
    await page.addInitScript(value => {
      localStorage.setItem('azurpilot.theme', value)
      localStorage.setItem('azurpilot.language', 'zh-CN')
      localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
    }, theme)
    // 浏览器验证只模拟启动器应答，不修改主机开机自启动。
    await page.route('**/api/launcher/status', route => route.fulfill({
      status: remote ? 403 : 200,
      json: remote ? {success: false} : {
        success: true, launcher_connected: true, autostart_supported: true, autostart_enabled: enabled,
      },
    }))
    await page.route('**/api/launcher/startup', async route => {
      enabled = route.request().postDataJSON().enabled as boolean
      changes.push(enabled)
      await route.fulfill({json: {success: true}})
    })
    await page.goto('/#/settings')
    const toggle = page.getByRole('switch', {name: 'Windows 开机自启动', exact: true})
    await expect(toggle).toBeEnabled()
    await expect(toggle).toHaveAttribute('aria-checked', 'false')
    await toggle.click()
    await expect(toggle).toBeEnabled()
    await expect(toggle).toHaveAttribute('aria-checked', 'true')
    expect(changes).toEqual([true])
    remote = true
    await page.reload()
    await expect(toggle).toBeDisabled()
    await expect(page.getByRole('status').filter({hasText: '开机自启动只能在本机 WebUI 中设置'})).toBeVisible()
  })
}
