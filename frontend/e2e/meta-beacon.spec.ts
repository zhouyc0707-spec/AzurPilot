import { expect, test } from '@playwright/test'

for (const theme of ['light', 'legacy-light']) {
  test(`${theme} META 求援次数替代旧开关，保存各模式并隐藏运行断点`, async ({page}, testInfo) => {
    await page.addInitScript(value => {
      localStorage.setItem('azurpilot.theme', value)
      localStorage.setItem('azurpilot.language', 'zh-CN')
      localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
    }, theme)
    await page.goto('/#/i/testpilot/task/OpsiAshBeacon')
    const field = page.locator('[id="OpsiAshBeacon.OpsiAshBeacon.AssistRequestLimit"]')
    const status = page.locator('[id="OpsiAshBeacon.OpsiAshBeacon.AssistRequestLimit-status"]')
    // 临时服务首次读取还需初始化隔离总库，按页面实际就绪等待。
    await expect(field).toBeVisible({timeout: 30000})
    await expect(field).toHaveValue('0')
    await expect(field).toHaveAttribute('inputmode', 'decimal')
    await expect(field).toHaveAccessibleName('求援次数上限')
    await expect(page.locator('[id="OpsiAshBeacon.OpsiAshBeacon.OneHitMode"]')).toHaveCount(0)
    await expect(page.locator('[id="OpsiAshBeacon.OpsiAshBeacon.AssistRequestState"]')).toHaveCount(0)

    for (const value of ['-1', '2', '0']) {
      await field.fill(value)
      await field.blur()
      await expect(status).toHaveText('已保存')
      await page.reload()
      await expect(field).toHaveValue(value)
    }

    await field.fill('-2')
    await field.blur()
    await expect(field).toHaveAttribute('aria-invalid', 'true')
    await expect(status).toContainText('参数格式不正确')
    await field.fill('2')
    await field.press('Enter')
    await expect(status).toHaveText('已保存')
    await expect(field).not.toHaveAttribute('aria-invalid', 'true')
    await page.reload()
    await expect(field).toHaveValue('2')
    await page.locator('.config-group').filter({has: field}).screenshot({
      path: testInfo.outputPath('meta-assist-limit.png'),
    })
    // 两种主题共用临时实例，恢复默认值后再验证下一种主题。
    await field.fill('0')
    await field.blur()
    await expect(status).toHaveText('已保存')
  })
}
