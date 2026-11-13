import {expect, test} from '@playwright/test'
for (const theme of ['legacy-light', 'legacy-dark', 'modern-light', 'modern-dark', 'minimal']) {
  test(`${theme} 独立外壳、统计入口及新旧切换`, async ({page}, info) => {
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    await page.addInitScript(theme => {
      localStorage.setItem('azurpilot.theme', theme)
      localStorage.setItem('azurpilot.language', 'zh-CN')
      localStorage.setItem('azurpilot.material', 'plain')
      localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
    }, theme)
    await page.goto('/#/i/demo-main/overview')
    await expect(page.locator('.instance-page-grid')).toBeVisible()
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
    if (theme.startsWith('legacy')) {
      await expect(page.locator('.maintenance-time')).toBeVisible()
      await page.locator('.task-group-button').filter({hasText: '系统'}).click()
      await expect(page.locator('.task-submenu-flyout')).toBeVisible()
      if (theme === 'legacy-light') {
        await expect(page.locator('.task-submenu-flyout')).toHaveCSS('background-color', 'rgb(255, 255, 255)')
        await expect(page.locator('.task-submenu-flyout')).toHaveCSS('backdrop-filter', 'none')
      }
    } else {
      const group = page.locator('.sidebar').getByRole('button', {name: '系统', exact: true})
      await group.click()
      await expect(group).toHaveAttribute('aria-expanded', 'true')
      await expect(page.locator('.task-submenu-flyout')).toHaveCount(0)
      await expect(page.locator('.sidebar-resize-handle')).toBeVisible()
    }
    await page.screenshot({path: info.outputPath(`${theme}.png`), fullPage: true, animations: 'disabled'})
    if (!theme.startsWith('legacy')) {
      const sidebar = page.locator('.sidebar')
      const handle = page.locator('.sidebar-resize-handle')
      const before = (await sidebar.boundingBox())!.width
      const box = (await handle.boundingBox())!
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
      await page.mouse.down()
      await page.mouse.move(box.x + box.width / 2 + 56, box.y + box.height / 2, {steps: 8})
      await page.mouse.up()
      await expect.poll(async () => (await sidebar.boundingBox())!.width).toBeGreaterThan(before + 40)
      await handle.dblclick()
      await expect.poll(async () => (await sidebar.boundingBox())!.width).toBeCloseTo(before, 0)
      const rail = page.locator('.legacy-rail-resize-handle')
      await expect(rail).toBeVisible()
      const previousWidth = Number(await rail.getAttribute('aria-valuenow'))
      await rail.focus()
      await page.keyboard.press('ArrowRight')
      await expect(rail).toHaveAttribute('aria-valuenow', String(previousWidth + 8))
      await page.keyboard.press('Enter')
      await expect(rail).toHaveAttribute('aria-valuenow', String(previousWidth))
      const tabs = page.locator('.tabbar-resize-handle')
      await tabs.focus()
      await page.keyboard.press('End')
      await expect(tabs).toHaveAttribute('aria-valuenow', '4')
      await page.keyboard.press('Home')
      await expect(tabs).toHaveAttribute('aria-valuenow', '1')
    }
    const statisticsLink = page.locator('.primary-nav').getByRole('link', {name: '资源统计', exact: true})
    if (theme.startsWith('legacy')) {
      await expect(statisticsLink).toHaveCount(0)
      await page.goto('/#/i/demo-main/statistics')
    } else {
      await statisticsLink.click()
    }
    await expect(page.locator('main')).toBeVisible()
    if (theme.startsWith('legacy')) await expect(page.locator('.legacy-statistics-navigation')).toBeVisible()
    else await expect(page.locator('.statistics-category-control')).toBeVisible()
    await page.getByRole('button', {name: '切换新旧 UI', exact: true}).filter({visible: true}).click()
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme.startsWith('legacy') ? theme.replace('legacy', 'modern') : theme === 'modern-dark' ? 'legacy-dark' : 'legacy-light')
    await page.getByRole('button', {name: '切换新旧 UI', exact: true}).filter({visible: true}).click()
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
    expect(errors).toEqual([])
  })
}
