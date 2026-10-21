import { expect, test } from '@playwright/test'

const base = 'Exercise.Exercise'

for (const [theme, label] of [['light', '新版'], ['legacy-light', '旧版']]) {
  test(`${label}演习维护提前开关及星期可保存，原延迟保持不变`, async ({page}, testInfo) => {
    await page.addInitScript(value => {
      localStorage.setItem('azurpilot.theme', value)
      localStorage.setItem('azurpilot.language', 'zh-CN')
      localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
    }, theme)
    await page.route('https://www.clarity.ms/**', route => route.abort())
    await page.goto('/#/i/testpilot/task/Exercise')
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)

    const enabled = page.getByRole('switch', {name: '维护前提前演习', exact: true})
    const weekday = page.getByRole('combobox', {name: '提前演习星期', exact: true})
    const delay = page.locator(`[id="${base}.DelayUntilHoursBeforeNextUpdate"]`)
    const enabledStatus = page.locator(`[id="${base}.MaintenanceAdvance-status"]`)
    const weekdayStatus = page.locator(`[id="${base}.MaintenanceWeekday-status"]`)
    await expect(enabled).toBeVisible({timeout: 15000})
    await expect(enabled).toHaveAttribute('aria-checked', 'true')
    await expect(weekday).toHaveText('周四')
    // 夹具默认不推迟；模拟用户的 1 小时设置，确认新选项不会覆盖它。
    await expect(delay).toHaveText('不推迟')
    await delay.click()
    await page.getByRole('option', {name: '1', exact: true}).click()
    await expect(page.locator(`[id="${base}.DelayUntilHoursBeforeNextUpdate-status"]`)).toHaveText('已保存')
    await expect(delay).toHaveText('1')

    const delayRow = page.locator('.field-row').filter({has: delay})
    const enabledRow = page.locator('.field-row').filter({has: enabled})
    await expect(delayRow.locator('xpath=following-sibling::*[1]')).toContainText('维护前提前演习')
    await expect(enabledRow.locator('xpath=following-sibling::*[1]')).toContainText('提前演习星期')
    await expect(enabledRow).toContainText('09:00')
    await weekday.click()
    await expect(page.getByRole('option')).toHaveText(['周一', '周二', '周三', '周四', '周五', '周六', '周日'])
    await expect(page.getByRole('option', {name: '周四', exact: true})).toHaveAttribute('aria-selected', 'true')
    await page.getByRole('option', {name: '周二', exact: true}).click()
    await expect(weekdayStatus).toHaveText('已保存')
    await page.reload()
    await expect(weekday).toHaveText('周二', {timeout: 15000})
    await expect(enabled).toHaveAttribute('aria-checked', 'true')
    await expect(delay).toHaveText('1')

    await enabled.click()
    await expect(enabledStatus).toHaveText('已保存')
    await page.reload()
    await expect(enabled).toHaveAttribute('aria-checked', 'false', {timeout: 15000})
    await expect(weekday).toHaveText('周二')
    await expect(delay).toHaveText('1')
    await enabledRow.screenshot({path: testInfo.outputPath(`exercise-maintenance-disabled-${theme}.png`)})

    await enabled.click()
    await expect(enabledStatus).toHaveText('已保存')
    await page.reload()
    await expect(enabled).toHaveAttribute('aria-checked', 'true', {timeout: 15000})
    await expect(weekday).toHaveText('周二')
    await expect(delay).toHaveText('1')
    await weekday.click()
    await page.screenshot({path: testInfo.outputPath(`exercise-maintenance-weekdays-${theme}.png`)})
    // 仅修改临时实例，恢复默认星期后再验收另一主题，不启动游戏任务。
    await page.getByRole('option', {name: '周四', exact: true}).click()
    await expect(weekdayStatus).toHaveText('已保存')
    await page.reload()
    await expect(weekday).toHaveText('周四')
    await expect(enabled).toHaveAttribute('aria-checked', 'true')
    await expect(delay).toHaveText('1')
    await delay.click()
    await page.getByRole('option', {name: '不推迟', exact: true}).click()
    await expect(page.locator(`[id="${base}.DelayUntilHoursBeforeNextUpdate-status"]`)).toHaveText('已保存')
  })
}
