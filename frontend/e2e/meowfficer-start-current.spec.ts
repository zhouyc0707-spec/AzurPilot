import { expect, test } from '@playwright/test'

const startId = 'MeowfficerScore.MeowfficerScore.ScanStart'

for (const [theme, label] of [['light', '新版'], ['legacy-light', '旧版']]) {
  test(`${label}指挥喵可选择从当前天赋页开始并保存起点`, async ({page}, testInfo) => {
    await page.addInitScript(value => {
      localStorage.setItem('azurpilot.theme', value)
      localStorage.setItem('azurpilot.language', 'zh-CN')
      localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
    }, theme)
    await page.route('https://www.clarity.ms/**', route => route.abort())
    await page.goto('/#/i/testpilot/task/MeowfficerScore')

    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
    const start = page.getByRole('combobox', {name: '扫描起点', exact: true})
    const source = page.getByRole('combobox', {name: '评分来源', exact: true})
    const advice = page.getByRole('switch', {name: '扫描时按建议锁定／解锁', exact: true})
    const limit = page.locator('[id="MeowfficerScore.MeowfficerScore.ScanLimit"]')
    await expect(start).toHaveAttribute('id', startId)
    await expect(start).toHaveText('猫窝首只')
    await expect(advice).toHaveAttribute('aria-checked', 'false')

    await source.click()
    await page.getByRole('option', {name: '扫描全部指挥喵', exact: true}).click()
    await expect(page.locator('[id="MeowfficerScore.MeowfficerScore.Source-status"]')).toHaveText('已保存')
    await start.click()
    await page.getByRole('option', {name: '当前天赋页', exact: true}).click()
    await expect(page.locator(`[id="${startId}-status"]`)).toHaveText('已保存')
    // 新起点不擅自开启改锁；由用户单独选择建议锁定。
    await expect(advice).toHaveAttribute('aria-checked', 'false')
    await advice.click()
    await expect(page.locator('[id="MeowfficerScore.MeowfficerScore.LockByAdvice-status"]')).toHaveText('已保存')
    await limit.fill('12')
    await limit.blur()
    await expect(page.locator('[id="MeowfficerScore.MeowfficerScore.ScanLimit-status"]')).toHaveText('已保存')
    await expect.poll(() => page.evaluate(() => sessionStorage.getItem('azurpilot.edits.config:testpilot'))).toBeNull()

    await page.reload()
    await expect(source).toHaveText('扫描全部指挥喵')
    await expect(start).toHaveText('当前天赋页')
    await expect(advice).toHaveAttribute('aria-checked', 'true')
    await expect(limit).toHaveValue('12')
    await start.scrollIntoViewIfNeeded()
    await page.locator('.field-row').filter({has: start}).screenshot({
      path: testInfo.outputPath(`meowfficer-start-current-${theme}.png`),
    })

    // 只写隔离配置，不启动游戏任务；结束时恢复默认，避免影响其他主题验证。
    await start.click()
    await page.getByRole('option', {name: '猫窝首只', exact: true}).click()
    await expect(page.locator(`[id="${startId}-status"]`)).toHaveText('已保存')
    await advice.click()
    await expect(page.locator('[id="MeowfficerScore.MeowfficerScore.LockByAdvice-status"]')).toHaveText('已保存')
    await limit.fill('0')
    await limit.blur()
    await expect(page.locator('[id="MeowfficerScore.MeowfficerScore.ScanLimit-status"]')).toHaveText('已保存')
    await source.click()
    await page.getByRole('option', {name: '本地截图', exact: true}).click()
    await expect(page.locator('[id="MeowfficerScore.MeowfficerScore.Source-status"]')).toHaveText('已保存')
    await page.reload()
    await expect(start).toHaveText('猫窝首只')
    await expect(advice).toHaveAttribute('aria-checked', 'false')
  })
}
