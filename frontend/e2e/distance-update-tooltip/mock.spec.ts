import { expect, test, type Page } from '@playwright/test'
import { routeRpc } from '../rpc-route'

test.use({timezoneId: 'Asia/Shanghai'})

async function fixture(page: Page, theme: string) {
  let distance = [{time: '2026-11-17 05:45:12.123456', value: 543811}]
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.clock.setFixedTime(new Date('2026-11-17T12:00:00+08:00'))
  await routeRpc(page, socket => {
    const server = socket.connectToServer()
    const pending = new Set<string>()
    socket.onMessage(message => {
      const request = JSON.parse(message)
      if (request.method === 'statistics.legacy') pending.add(request.id)
      server.send(message)
    })
    server.onMessage(message => {
      const response = JSON.parse(message)
      if (pending.delete(response.id) && response.ok) {
        response.result.apChart.series = [
          {key: 'ap', label: '体力', points: [
            {time: '2026-11-17 10:00:00', value: 7500},
            {time: '2026-11-17 11:52:10', value: 7499},
          ]},
          {key: 'distance', label: '海里数', points: distance},
        ]
      }
      socket.send(JSON.stringify(response))
    })
  })
  await page.addInitScript(value => {
    localStorage.setItem('azurpilot.theme', value)
    localStorage.setItem('azurpilot.language', 'zh-CN')
    localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
  }, theme)
  await page.goto('/#/i/demo-main/statistics')
  return {errors, setDistance: (points: typeof distance) => {distance = points}}
}

for (const theme of ['legacy-light', 'legacy-dark']) {
  test(`${theme} 海里数悬停显示自身记录时间并随范围和新记录更新`, async ({page}) => {
    const state = await fixture(page, theme)
    const panel = page.locator('.legacy-ap-panel')
    const distance = panel.locator('.legacy-ap-series-item').filter({hasText: '海里数'})
    const ap = panel.locator('.legacy-ap-series-item').filter({hasText: '体力'})
    await expect(distance).toContainText('543,811')
    await distance.hover()
    await expect(distance).toHaveAttribute('title', '上次更新 2026-11-17 05:45:12')
    await expect(ap).toHaveAttribute('title', /变化: -1\n最高: 7,500\n最低: 7,499\n均值: 7,500/)

    state.setDistance([{time: '2026-11-17T06:10:00', value: 543999}])
    await panel.getByRole('button', {name: '刷新统计', exact: true}).click()
    await expect(distance).toContainText('543,999')
    await expect(distance).toHaveAttribute('title', '上次更新 2026-11-17 06:10:00')

    state.setDistance([{time: '2026-11-01 05:00:00', value: 1000}])
    await panel.getByRole('button', {name: '刷新统计', exact: true}).click()
    await expect(distance).not.toHaveAttribute('title')
    await panel.getByRole('button', {name: '本月', exact: true}).click()
    await expect(distance).toHaveAttribute('title', '上次更新 2026-11-01 05:00:00')

    state.setDistance([])
    await panel.getByRole('button', {name: '刷新统计', exact: true}).click()
    await expect(distance).not.toHaveAttribute('title')
    expect(state.errors).toEqual([])
  })
}
