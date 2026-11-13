import { expect, test } from '@playwright/test'

const base = 'Exercise.Exercise'

for (const [theme, label] of [['modern-light', '新版'], ['legacy-light', '旧版']]) {
  test(`${label}演习自动维护提前开关可保存，原延迟保持不变且不再手选星期`, async ({page}, testInfo) => {
    await page.addInitScript(value => {
      localStorage.setItem('azurpilot.theme', value)
      localStorage.setItem('azurpilot.language', 'zh-CN')
      localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
    }, theme)
    await page.route('https://www.clarity.ms/**', route => route.abort())
    await page.goto('/#/i/demo-main/task/Exercise')
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)

    const enabled = page.getByRole('switch', {name: '维护前提前演习', exact: true})
    const delay = page.locator(`[id="${base}.DelayUntilHoursBeforeNextUpdate"]`)
    const enabledStatus = page.locator(`[id="${base}.MaintenanceAdvance-status"]`)
    await expect(enabled).toBeVisible({timeout: 15000})
    await expect(enabled).toHaveAttribute('aria-checked', 'true')
    await expect(page.getByRole('combobox', {name: '提前演习星期', exact: true})).toHaveCount(0)
    await expect(page.locator(`[id="${base}.MaintenanceWeekday"]`)).toHaveCount(0)
    // 只修改内存模拟实例，确认自动规则不覆盖用户的普通延迟设置。
    await delay.click()
    await page.getByRole('option', {name: '1', exact: true}).click()
    await expect(page.locator(`[id="${base}.DelayUntilHoursBeforeNextUpdate-status"]`)).toHaveText('已保存')
    await expect(delay).toHaveText('1')
    const enabledRow = page.locator('.field-row').filter({has: enabled})
    await expect(enabledRow).toContainText('公告')
    await expect(enabledRow).toContainText(/1\s*小时/)
    await expect(enabledRow).not.toContainText('09:00')

    await enabled.click()
    await expect(enabledStatus).toHaveText('已保存')
    await page.reload()
    await expect(enabled).toHaveAttribute('aria-checked', 'false', {timeout: 15000})
    await expect(delay).toHaveText('1')
    await enabledRow.screenshot({path: testInfo.outputPath(`exercise-maintenance-disabled-${theme}.png`)})

    await enabled.click()
    await expect(enabledStatus).toHaveText('已保存')
    await page.reload()
    await expect(enabled).toHaveAttribute('aria-checked', 'true', {timeout: 15000})
    await expect(delay).toHaveText('1')
    await enabledRow.screenshot({path: testInfo.outputPath(`exercise-maintenance-enabled-${theme}.png`)})
    await delay.click()
    await page.getByRole('option', {name: '不推迟', exact: true}).click()
    await expect(page.locator(`[id="${base}.DelayUntilHoursBeforeNextUpdate-status"]`)).toHaveText('已保存')
  })
}
