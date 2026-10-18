import { expect, test } from '@playwright/test'

for (const theme of ['light', 'legacy-light']) {
  test(`${theme} 大世界商店月末开关位于日期下方，保存后保留原限日`, async ({page}, testInfo) => {
    await page.addInitScript(value => {
      localStorage.setItem('azurpilot.theme', value)
      localStorage.setItem('azurpilot.language', 'zh-CN')
      localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
    }, theme)
    await page.goto('/#/i/testpilot/task/OpsiShop')
    const path = 'OpsiShop.OpsiShop.MonthEndOnly'
    const toggle = page.getByRole('switch', {name: '仅在每月最后一天购买', exact: true})
    const date = page.locator('[id="OpsiShop.OpsiShop.DisableBeforeDate"]')
    const status = page.locator(`[id="${path}-status"]`)
    const limit = theme === 'light' ? '20' : '21'
    await expect(toggle).toBeVisible({timeout: 15000})
    await expect(toggle).toHaveAttribute('aria-checked', 'false')
    // 开启后仍保留用户的旧限日，关闭时可以继续使用。
    await date.fill(limit)
    await date.blur()
    await expect(page.locator('[id="OpsiShop.OpsiShop.DisableBeforeDate-status"]')).toHaveText('已保存')
    const dateRow = page.locator('.field-row').filter({has: date})
    const toggleRow = page.locator('.field-row').filter({has: toggle})
    await expect(dateRow.locator('xpath=following-sibling::*[1]')).toContainText('仅在每月最后一天购买')
    await expect(toggleRow).toContainText('自动适配大小月和闰年')
    await toggle.click()
    await expect(status).toHaveText('已保存')
    await page.reload()
    await expect(toggle).toHaveAttribute('aria-checked', 'true', {timeout: 15000})
    await expect(date).toHaveValue(limit)
    await toggleRow.screenshot({path: testInfo.outputPath('month-end-option.png')})
    await toggle.click()
    await expect(status).toHaveText('已保存')
    await page.reload()
    await expect(toggle).toHaveAttribute('aria-checked', 'false', {timeout: 15000})
    await expect(date).toHaveValue(limit)
  })
}
