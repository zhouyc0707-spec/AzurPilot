import {expect, test} from '@playwright/test'

for (const theme of ['legacy-light', 'light']) {
  test(`${theme} 队列任务单次启动、再次停止与其他任务互斥`, async ({page}) => {
    const errors: string[] = []
    const actions: {method: string; params: Record<string, unknown>}[] = []
    page.on('pageerror', error => errors.push(error.message))
    await page.emulateMedia({reducedMotion: 'reduce'})
    await page.addInitScript(value => {
      localStorage.setItem('azurpilot.theme', value)
      localStorage.setItem('azurpilot.language', 'zh-CN')
      localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
    }, theme)
    await page.routeWebSocket('**/api/v1/ws', socket => {
      const server = socket.connectToServer()
      let reset = false
      socket.onMessage(message => {
        const request = JSON.parse(String(message))
        if (!reset && request.method === 'overview.get') {
          reset = true
          // 仅重置内存模拟实例；不连接真实游戏进程。
          server.send(JSON.stringify({v: 1, type: 'request', id: 'single-test-stop', method: 'scheduler.stop', params: {instance: 'demo-main'}}))
          server.send(JSON.stringify({v: 1, type: 'request', id: 'single-test-reset', method: 'config.patch', params: {instance: 'demo-main', changes: [{path: 'Commission.Scheduler.NextRun', value: '2020-01-01 00:00:00'}]}}))
        }
        if (request.method.startsWith('tasks.') || request.method === 'scheduler.start') actions.push(request)
        server.send(message)
      })
      server.onMessage(message => {
        const response = JSON.parse(String(message))
        if (!response.id?.startsWith('single-test-')) socket.send(message)
      })
    })
    await page.goto('/#/i/demo-main/overview')
    const queue = page.locator('.rail-task-list')
    const start = queue.locator('[data-task="Commission"] button')
    await expect(start).toHaveAccessibleName(/执行一次/)
    await expect(start).toBeEnabled()
    await page.locator('.rail-schedule').screenshot({path: test.info().outputPath(`single-task-${theme}-pending.png`)})
    const location = page.url()
    await start.click()
    const active = queue.locator('.rail-queue-group.running [data-task="Commission"]')
    await expect(active.getByRole('button', {name: /立即停止/})).toBeEnabled()
    await expect(page.locator('.scheduler-status')).toHaveText('单次任务运行中')
    await expect(queue.locator('[data-task="Research"] button')).toBeDisabled()
    await expect(page.locator('.scheduler-toggle')).toHaveText('立即停止任务')
    expect(page.url()).toBe(location)
    expect(actions.map(action => action.method)).toEqual(['tasks.runOnce'])
    await page.locator('.scheduler-widget').screenshot({path: test.info().outputPath(`single-task-${theme}-running.png`)})

    await active.getByRole('button', {name: /立即停止/}).click()
    await expect(queue.locator('.rail-queue-group.pending [data-task="Commission"] button')).toHaveAccessibleName(/执行一次/)
    await expect(page.locator('.scheduler-status')).toHaveText('已停止')
    await expect(queue.locator('[data-task="Research"] button')).toBeEnabled()
    expect(actions.map(action => action.method)).toEqual(['tasks.runOnce', 'tasks.stop'])
    expect(actions[1].params.runId).toBeTruthy()
    await queue.screenshot({path: test.info().outputPath(`single-task-${theme}-stopped.png`)})

    // 未来任务可手动提前执行；大停止按钮也应走同一个快速停止接口。
    const waiting = queue.locator('.rail-queue-group.waiting [data-task="Main"] button')
    await expect(waiting).toBeEnabled()
    await waiting.click()
    await expect(page.locator('.scheduler-status')).toHaveText('单次任务运行中')
    await page.locator('.scheduler-toggle').click()
    await expect(queue.locator('.rail-queue-group.waiting [data-task="Main"] button')).toBeEnabled()
    expect(actions.map(action => action.method)).toEqual(['tasks.runOnce', 'tasks.stop', 'tasks.runOnce', 'tasks.stop'])
    await page.setViewportSize({width: 390, height: 844})
    if (theme === 'light') await page.getByRole('button', {name: '打开调度与任务', exact: true}).click()
    const narrowButton = queue.locator('[data-task="Commission"] button')
    await narrowButton.scrollIntoViewIfNeeded()
    await expect(narrowButton).toBeInViewport({ratio: 1})
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.locator('.rail-schedule').screenshot({path: test.info().outputPath(`single-task-${theme}-narrow.png`)})
    expect(errors).toEqual([])
  })
}

test('单次执行自然完成后自动恢复按钮，不接着启动其他任务', async ({page}) => {
  test.setTimeout(30000)
  const actions: string[] = []
  await page.addInitScript(() => {
    localStorage.setItem('azurpilot.theme', 'legacy-light')
    localStorage.setItem('azurpilot.language', 'zh-CN')
  })
  await page.routeWebSocket('**/api/v1/ws', socket => {
    const server = socket.connectToServer()
    socket.onMessage(message => {
      const request = JSON.parse(String(message))
      if (request.method === 'tasks.runOnce' || request.method === 'scheduler.start') actions.push(request.method)
      server.send(message)
    })
    server.onMessage(message => socket.send(message))
  })
  await page.goto('/#/i/demo-alt/overview')
  const queue = page.locator('.rail-task-list')
  await queue.locator('[data-task="Commission"] button').click()
  await expect(page.locator('.scheduler-status')).toHaveText('单次任务运行中')
  await expect(page.locator('.scheduler-status')).toHaveText('已停止', {timeout: 12000})
  await expect(queue.locator('.rail-queue-group.waiting [data-task="Commission"] button')).toHaveAccessibleName(/执行一次/)
  expect(actions).toEqual(['tasks.runOnce'])
})

test('独立工具保持原停止方式，不能冒充队列单次运行', async ({page}) => {
  const actions: string[] = []
  await page.addInitScript(() => {
    localStorage.setItem('azurpilot.theme', 'light')
    localStorage.setItem('azurpilot.language', 'zh-CN')
  })
  await page.routeWebSocket('**/api/v1/ws', socket => {
    const server = socket.connectToServer()
    let start = false
    socket.onMessage(message => {
      const request = JSON.parse(String(message))
      if (!start && request.method === 'overview.get') {
        start = true
        server.send(JSON.stringify({v: 1, type: 'request', id: 'single-test-tool', method: 'tasks.run', params: {instance: 'demo-dog', task: 'FleetScan'}}))
      }
      if (request.method === 'tasks.stop' || request.method === 'scheduler.stop') actions.push(request.method)
      server.send(message)
    })
    server.onMessage(message => {
      const response = JSON.parse(String(message))
      if (response.id !== 'single-test-tool') socket.send(message)
    })
  })
  await page.goto('/#/i/demo-dog/overview')
  await expect(page.locator('.scheduler-status')).toHaveText('独立任务运行中')
  await expect(page.locator('.scheduler-toggle')).toHaveText('停止任务')
  await expect(page.locator('.rail-task-run:enabled')).toHaveCount(0)
  await page.locator('.scheduler-toggle').click()
  await expect(page.locator('.scheduler-status')).toHaveText('已停止')
  expect(actions).toEqual(['scheduler.stop'])
})
