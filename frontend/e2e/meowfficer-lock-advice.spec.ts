import { expect, test } from '@playwright/test'

const fieldId = 'Meowfficer.MeowfficerTrain.LockByAdvice'

for (const [theme, label] of [['light', '新版'], ['legacy-light', '旧版']]) {
  test(`${label}指挥喵评分建议锁定开关可保存并在刷新后保留`, async ({page}, testInfo) => {
    await page.addInitScript(value => {
      localStorage.setItem('azurpilot.theme', value)
      localStorage.setItem('azurpilot.language', 'zh-CN')
      localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
    }, theme)
    await page.route('https://www.clarity.ms/**', route => route.abort())
    await page.goto('/#/i/testpilot/task/Meowfficer')

    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
    const toggle = page.getByRole('switch', {name: '按评分建议锁定新猫', exact: true})
    const group = page.locator('#group-MeowfficerTrain')
    await expect(toggle).toBeVisible()
    await expect(toggle).toBeEnabled()
    await expect(toggle).toHaveAttribute('id', fieldId)
    await expect(toggle).toHaveAttribute('aria-checked', 'false')
    await expect(group).toContainText('只有完整识别后评价为「建议喂掉」的猫不锁定')
    await expect(group).toContainText('其余金、紫、蓝猫全部锁定')
    await expect(group).toContainText('评分失败或无法确认识别完整时也锁定保护')
    await expect(group).toContainText('不会批量改变已有猫的锁定状态')
    await expect(group).toContainText('无需另外开启本项')
    await expect(group).toContainText('开启「按评分建议锁定新猫」后不使用此门槛')

    // 使用临时后端完成真实配置保存，清空草稿后再刷新确认持久化。
    await toggle.click()
    await expect(page.locator(`[id="${fieldId}-status"]`)).toHaveText('已保存')
    await expect.poll(() => page.evaluate(() => sessionStorage.getItem('azurpilot.edits.config:testpilot'))).toBeNull()
    await page.reload()
    await expect(toggle).toHaveAttribute('aria-checked', 'true')
    await expect(page.locator('[id="Meowfficer.MeowfficerTrain.ScoreTalents"]')).toHaveAttribute('aria-checked', 'false')
    await expect(page.locator('[id="Meowfficer.MeowfficerTrain.ScoreThreshold"]')).toHaveValue('0')
    const adviceRow = page.locator('.field-row').filter({has: toggle})
    await expect(adviceRow).toHaveCount(1)
    if (theme === 'legacy-light') {
      await group.screenshot({path: testInfo.outputPath(`meowfficer-lock-advice-${theme}.png`)})
    } else {
      await toggle.scrollIntoViewIfNeeded()
      await expect(toggle).toBeInViewport()
      await adviceRow.screenshot({path: testInfo.outputPath(`meowfficer-lock-advice-${theme}.png`)})
    }
    await page.screenshot({path: testInfo.outputPath(`meowfficer-settings-${theme}.png`)})

    // 每种主题均还原测试配置，确认关闭也能真正保存。
    await toggle.click()
    await expect(page.locator(`[id="${fieldId}-status"]`)).toHaveText('已保存')
    await expect.poll(() => page.evaluate(() => sessionStorage.getItem('azurpilot.edits.config:testpilot'))).toBeNull()
    await page.reload()
    await expect(toggle).toHaveAttribute('aria-checked', 'false')
  })
}
