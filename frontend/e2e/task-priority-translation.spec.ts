import {expect, test} from '@playwright/test'

for (const theme of ['light', 'legacy-light']) {
  test(`${theme} 优先级显示中文注释，调整顺序仅保存英文任务标识`, async ({page}, testInfo) => {
    const path = 'General.YukikazeTaskManager.TaskPriorityAdjustment'
    const submitted: string[] = []
    await page.routeWebSocket('**/api/v1/ws', socket => {
      const server = socket.connectToServer()
      socket.onMessage(message => {
        const request = JSON.parse(String(message))
        if (request.method === 'config.patch') {
          for (const change of request.params.changes) {
            if (change.path === path) submitted.push(change.value)
          }
        }
        server.send(message)
      })
      server.onMessage(message => socket.send(message))
    })
    await page.addInitScript(value => {
      localStorage.setItem('azurpilot.theme', value)
      localStorage.setItem('azurpilot.language', 'zh-CN')
      localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
    }, theme)
    await page.goto('/#/i/testpilot/task/General')
    const field = page.locator(`[id="${path}"]`)
    const status = page.locator(`[id="${path}-status"]`)
    await expect(field).toHaveValue(/Commission # 委托/, {timeout: 15000})
    await field.evaluate(element => element.scrollIntoView({block: 'start'}))
    const bounds = await field.boundingBox()
    if (bounds) await page.screenshot({path: testInfo.outputPath('priority-translations.png'), clip: {...bounds, height: 220}})
    const original = await field.inputValue()
    const lines = original.split('\n')
    const commission = lines.findIndex(line => /^>?\s*Commission\s*#/.test(line))
    const tactical = lines.findIndex(line => /^>?\s*Tactical\s*#/.test(line))
    expect(commission).toBeGreaterThan(-1)
    expect(tactical).toBeGreaterThan(-1)
    await field.focus()
    await field.blur()
    expect(submitted).toHaveLength(0)
    ;[lines[commission], lines[tactical]] = [lines[tactical]!, lines[commission]!]
    await field.fill(lines.join('\n'))
    await field.blur()
    await expect(status).toHaveText('已保存')
    expect(submitted).toHaveLength(1)
    const rawTasks = (text: string) => text.split('\n').flatMap(line => line.split('#')[0]!.split('>')).map(s => s.trim()).filter(Boolean)
    expect(submitted[0]).not.toContain('#')
    expect(rawTasks(submitted[0]!)).toEqual(rawTasks(lines.join('\n')))
    await page.reload()
    await expect(field).toHaveValue(lines.join('\n'), {timeout: 15000})
    // 恢复临时实例的原顺序，另一个主题复用同一测试后端。
    await field.fill(original)
    await field.blur()
    await expect(status).toHaveText('已保存')
  })
}
