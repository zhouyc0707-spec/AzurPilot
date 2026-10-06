import { expect, test } from '@playwright/test'

const fieldId = 'MeowfficerScore.MeowfficerScore.LockByAdvice'

for (const [theme, label] of [['light', '新版'], ['legacy-light', '旧版']]) {
  test(`${label}已有指挥喵扫描建议锁定开关可保存并在刷新后保留`, async ({page}, testInfo) => {
    await page.addInitScript(value => {
      localStorage.setItem('azurpilot.theme', value)
      localStorage.setItem('azurpilot.language', 'zh-CN')
      localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
    }, theme)
    await page.route('https://www.clarity.ms/**', route => route.abort())
    await page.goto('/#/i/testpilot/task/MeowfficerScore')

    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
    const toggle = page.getByRole('switch', {name: '扫描时按建议锁定／解锁', exact: true})
    const group = page.locator('#group-MeowfficerScore')
    await expect(toggle).toBeVisible()
    await expect(toggle).toBeEnabled()
    await expect(toggle).toHaveAttribute('id', fieldId)
    await expect(toggle).toHaveAttribute('aria-checked', 'false')
    await expect(group).toContainText('仅在「扫描全部指挥喵」模式生效')
    await expect(group).toContainText('「建议喂掉」解锁，其余建议锁定')
    await expect(group).toContainText('蓝猫不评分，确认后设为未锁定，包括解除已有锁')
    await expect(group).toContainText('识别不完整或猫种未知时保护锁定')
    await expect(group).toContainText('当前猫、页面或锁定状态无法确认时不操作')
    await expect(group).toContainText('本地截图和自动跟拍仍只读')
    await expect(group).toContainText('其他服务器跳过锁定操作')

    // 使用隔离后端验证配置落盘，测试不会执行游戏任务。
    await toggle.click()
    await expect(page.locator(`[id="${fieldId}-status"]`)).toHaveText('已保存')
    await expect.poll(() => page.evaluate(() => sessionStorage.getItem('azurpilot.edits.config:testpilot'))).toBeNull()
    await page.reload()
    await expect(toggle).toHaveAttribute('aria-checked', 'true')

    await toggle.scrollIntoViewIfNeeded()
    await expect(toggle).toBeInViewport()
    const row = page.locator('.field-row').filter({has: toggle})
    await row.screenshot({path: testInfo.outputPath(`meowfficer-score-lock-${theme}.png`)})

    // 恢复测试默认值并刷新，确认关闭同样持久化。
    await toggle.click()
    await expect(page.locator(`[id="${fieldId}-status"]`)).toHaveText('已保存')
    await expect.poll(() => page.evaluate(() => sessionStorage.getItem('azurpilot.edits.config:testpilot'))).toBeNull()
    await page.reload()
    await expect(toggle).toHaveAttribute('aria-checked', 'false')
  })
}
