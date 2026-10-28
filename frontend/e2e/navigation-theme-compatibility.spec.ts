import {expect, test} from '@playwright/test'

for (const theme of ['legacy-light', 'legacy-dark']) {
  test(`${theme} 资源管理迁入统计且旧链接与主题切换保持可用`, async ({page}) => {
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    await page.addInitScript(value => {
      localStorage.setItem('azurpilot.theme', value)
      localStorage.setItem('azurpilot.language', 'zh-CN')
      localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
    }, theme)
    await page.goto('/#/i/testpilot/resources')
    await expect(page).toHaveURL(/\/statistics\?view=management$/)
    await expect(page.locator('.resource-management')).toBeVisible()
    await expect(page.locator('.primary-nav').getByRole('link', {name: '资源管理', exact: true})).toHaveCount(0)
    await expect(page.locator('.primary-nav').getByRole('link', {name: '心智单元计算器', exact: true})).toHaveCount(0)
    await expect(page.getByRole('tab', {name: '资源管理', exact: true})).toHaveAttribute('aria-selected', 'true')
    await page.reload()
    await expect(page.getByRole('tab', {name: '资源管理', exact: true})).toHaveAttribute('aria-selected', 'true')
    await page.getByRole('tab', {name: '资源趋势', exact: true}).click()
    await expect(page).toHaveURL(/\/statistics$/)
    await expect(page.locator('.legacy-stats')).toBeVisible()
    await expect(page.locator('.legacy-stats-dashboard')).toHaveCount(1)
    await expect(page.locator('.legacy-stats-charts > .legacy-stats-section')).toHaveCount(5)
    await page.getByRole('tab', {name: '资源管理', exact: true}).click()

    await page.goto('/#/i/testpilot/statistics?view=management')
    await expect(page.locator('.resource-management')).toBeVisible()
    await expect(page.locator('.legacy-stats')).toHaveCount(0)
    await page.getByRole('button', {name: '切换新旧 UI', exact: true}).click()
    await expect(page.getByRole('tab', {name: '资源管理', exact: true})).toHaveAttribute('aria-selected', 'true')
    await expect(page.locator('.primary-nav').getByRole('link', {name: '资源管理', exact: true})).toHaveCount(0)
    await page.getByRole('tab', {name: '资源趋势', exact: true}).click()
    await expect(page).toHaveURL(/\/statistics$/)
    await expect(page.locator('.statistics-page-sections')).toBeVisible()
    await page.getByRole('button', {name: '切换新旧 UI', exact: true}).click()
    await expect(page.locator('.legacy-stats')).toBeVisible()
    await expect(page.locator('.legacy-stats-dashboard')).toHaveCount(1)
    await page.screenshot({path: test.info().outputPath('legacy-statistics-navigation.png'), fullPage: true})
    expect(errors).toEqual([])
  })

  test(`${theme} 总览内嵌统计不被资源管理参数替换`, async ({page}) => {
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    await page.addInitScript(value => {
      localStorage.setItem('azurpilot.theme', value)
      localStorage.setItem('azurpilot.language', 'zh-CN')
      localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
    }, theme)
    await page.goto('/#/i/testpilot/overview?view=management')
    await page.getByRole('button', {name: '切换到统计', exact: true}).click()
    await expect(page.locator('.instance-panel-stats .legacy-stats')).toBeVisible()
    await expect(page.locator('.legacy-stats-dashboard')).toHaveCount(0)
    await expect(page.locator('.resource-management')).toHaveCount(0)
    await expect(page.locator('.legacy-statistics-navigation')).toHaveCount(0)
    await expect(page.locator('.legacy-stats-charts > .legacy-stats-section')).toHaveCount(5)
    expect(errors).toEqual([])
  })
}

for (const theme of ['legacy-light', 'legacy-dark']) {
test(`${theme} 心智旧链接转养成页签且主题切换保留舰船数据入口`, async ({page}) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.addInitScript(value => {
    localStorage.setItem('azurpilot.theme', value)
    localStorage.setItem('azurpilot.language', 'zh-CN')
    localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
  }, theme)
  for (const path of ['task/FleetTechnology', 'task/MindCalculator', 'mind-calculator']) {
    await page.goto(`/#/i/testpilot/${path}`)
    const tab = path === 'task/FleetTechnology' ? 'technology' : 'mind'
    await expect(page).toHaveURL(url => url.hash === `#/i/testpilot/task/FleetAnalysis?tab=${tab}`)
    await expect(page.getByRole('tab', {name: tab === 'technology' ? '舰队科技' : '养成计算', exact: true})).toHaveAttribute('aria-selected', 'true')
  }
  await expect(page).toHaveURL(/\/task\/FleetAnalysis\?tab=mind$/)
  await expect(page.locator('.mind-calculator')).toBeVisible()
  await expect(page.locator('.primary-nav').getByRole('link', {name: '心智单元计算器', exact: true})).toHaveCount(0)
  await page.getByRole('button', {name: '舰队管理', exact: true}).hover()
  const menu = page.getByRole('menu', {name: '舰队管理', exact: true})
  await expect(menu.getByRole('menuitem', {name: '舰船数据', exact: true})).toBeVisible()
  await expect(menu.getByRole('menuitem', {name: '智能换船', exact: true})).toBeVisible()
  await menu.getByRole('menuitem', {name: '舰船数据', exact: true}).click()
  await page.getByRole('tab', {name: '养成计算', exact: true}).click()
  await expect(page.locator('.mind-calculator')).toBeVisible()
  await page.getByRole('button', {name: '切换新旧 UI', exact: true}).click()
  await expect(page).toHaveURL(/\/task\/FleetAnalysis\?tab=mind$/)
  await expect(page.locator('.mind-calculator')).toBeVisible()
  await expect(page.locator('.primary-nav').getByRole('link', {name: '心智单元计算器', exact: true})).toHaveCount(0)
  await expect(page.locator('.breadcrumb-current')).toHaveText('舰船数据')
  await page.getByRole('button', {name: '切换新旧 UI', exact: true}).click()
  await expect(page.locator('.mind-calculator')).toBeVisible()
  await expect(page.locator('.primary-nav').getByRole('link', {name: '心智单元计算器', exact: true})).toHaveCount(0)
  expect(errors).toEqual([])
})
}
