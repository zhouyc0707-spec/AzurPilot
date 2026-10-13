import {expect, test} from '@playwright/test'

for (const theme of ['light', 'legacy-light']) {
  test(`${theme} 限界挑战可选择保存难度且月度断点不可编辑`, async ({page}, testInfo) => {
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    await page.addInitScript(value => {
      localStorage.setItem('azurpilot.theme', value)
      localStorage.setItem('azurpilot.language', 'zh-CN')
      localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
    }, theme)
    await page.goto('/#/i/testpilot/task/LimitChallenge')
    const level = page.getByRole('combobox', {name: '目标难度', exact: true})
    await expect(level).toBeVisible({timeout: 30000})
    await expect(level).toContainText('窥探')
    await expect(page.locator('[id="LimitChallenge.Scheduler.Enable"]')).toHaveAttribute('aria-checked', 'false')
    for (const key of ['Month', 'CompletedLevel', 'AttemptedLevel', 'Failed']) {
      await expect(page.locator(`[id="LimitChallenge.LimitChallenge.${key}"]`)).toHaveCount(0)
    }
    for (const name of ['审视', '洞察', '窥探']) {
      await level.click()
      await page.getByRole('option', {name, exact: true}).click()
      await expect(page.locator('[id="LimitChallenge.LimitChallenge.TargetLevel-status"]')).toHaveText('已保存')
      await page.reload()
      await expect(level).toContainText(name)
    }
    await page.screenshot({path: testInfo.outputPath('限界挑战配置.png'), fullPage: true, animations: 'disabled'})
    expect(errors).toEqual([])
  })
}
