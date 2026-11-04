import {expect, test, type FrameLocator, type Page} from '@playwright/test'

// 全部使用虚构收藏和独立浏览器上下文，不读取用户收藏，也不启动游戏任务。
const backup = {
  schemaVersion: 1,
  characters: [
    {id: 'fixture_zero', name: '测试零换装', rarity: 'super_rare', acquisition: '建造', collected: '2', customTargetCount: 1},
    {id: 'fixture_unowned', name: '测试未拥有', rarity: 'elite', acquisition: '活动', collected: '0', customTargetCount: 3},
    {id: 'fixture_owned', name: '测试已拥有', rarity: 'rare', acquisition: '兑换', collected: '0', customTargetCount: 3},
  ],
  skins: [
    {id: 'fixture_skin_a', characterId: 'fixture_unowned', name: '测试礼服', acquisition: '880', owned: false},
    {id: 'fixture_skin_b', characterId: 'fixture_owned', name: '测试泳装', acquisition: '活动', owned: true},
  ],
  acquisitionSortOrder: ['活动', '建造', '兑换'],
}

async function prepare(page: Page, theme: string) {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('https://**/*', route => route.abort())
  await page.emulateMedia({reducedMotion: 'reduce'})
  await page.addInitScript(theme => {
    localStorage.setItem('azurpilot.language', 'zh-CN')
    localStorage.setItem('azurpilot.theme', theme)
    localStorage.setItem('azurpilot.background', JSON.stringify({source: 'off'}))
  }, theme)
  return errors
}

const tracker = (page: Page) => page.frameLocator('iframe[title="换装收藏统计"]')

async function importBackup(frame: FrameLocator) {
  await frame.locator('#import-btn').click()
  await frame.locator('#import-data').fill(JSON.stringify(backup))
  await frame.locator('#confirm-import-btn').click()
  await expect(frame.locator('#import-review')).toContainText('3 位角色、2 件换装')
  await frame.locator('#confirm-import-btn').click()
  await expect(frame.locator('#stat-characters')).toHaveText('3')
  await expect(frame.locator('#stat-owned')).toHaveText('1')
  await expect(frame.locator('#import-modal')).toBeHidden()
}

async function expectNoOuterOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  expect(await page.locator('#skin-collection-main-content').evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true)
  const box = await page.locator('iframe[title="换装收藏统计"]').boundingBox()
  expect(box).not.toBeNull()
  expect(box!.width).toBeGreaterThan(250)
  expect(box!.height).toBeGreaterThan(300)
  expect(box!.x + box!.width).toBeLessThanOrEqual(page.viewportSize()!.width + 1)
  if (page.viewportSize()!.width > 950) {
    expect(box!.y + box!.height).toBeLessThanOrEqual(page.viewportSize()!.height + 1)
  }
}

test('旧版侧栏接入完整收藏页，导入导出与刷新保留数据，桌面和窄屏可用', async ({page}, testInfo) => {
  const errors = await prepare(page, 'legacy-light')
  await page.goto('/#/i/demo-main/overview')
  const nav = page.locator('.primary-nav')
  const entry = nav.getByRole('link', {name: '换装收藏统计', exact: true})
  await expect(entry).toBeVisible()
  expect(await entry.evaluate(element => element.previousElementSibling?.textContent)).toBe('茗喵证券交易所')
  await entry.click()
  await expect(page).toHaveURL(/#\/i\/demo-main\/skin-collection$/)
  await expect(page.locator('#skin-collection-main-content')).toBeVisible()
  await expect(page.locator('.app-shell')).not.toHaveClass(/with-rail/)
  await expect(page.locator('.right-rail')).toBeHidden()
  const frame = tracker(page)
  await expect(frame.locator('h1')).toContainText('每一份收藏')
  await importBackup(frame)

  // 零换装记录且角色已完成，仍应属于“未拥有换装角色”。
  await frame.locator('#acquisition-filter').getByRole('button', {name: /^未拥有换装角色/}).click()
  await expect(frame.locator('#character-list .character-row')).toHaveCount(2)
  await expect(frame.locator('#character-list')).toContainText('测试零换装')
  await expect(frame.locator('#character-list')).toContainText('测试未拥有')
  await expect(frame.locator('#character-list')).not.toContainText('测试已拥有')
  await frame.locator('#tab-incomplete').click()
  await frame.locator('#acquisition-filter').getByRole('button', {name: /^未拥有换装角色/}).click()
  await expect(frame.locator('#incomplete-list')).toContainText('测试零换装')
  await expect(frame.locator('#incomplete-list')).toContainText('测试未拥有')
  await expect(frame.locator('#incomplete-list')).not.toContainText('测试已拥有')

  const pendingDownload = page.waitForEvent('download')
  await frame.locator('#export-btn').click()
  const download = await pendingDownload
  expect(download.suggestedFilename()).toMatch(/^azur-lane-skins-\d{4}-\d{2}-\d{2}\.json$/)
  const stream = await download.createReadStream()
  expect(stream).not.toBeNull()
  const chunks: Buffer[] = []
  for await (const chunk of stream!) chunks.push(Buffer.from(chunk))
  const exported = JSON.parse(Buffer.concat(chunks).toString('utf8'))
  expect(exported.characters).toMatchObject(backup.characters)
  expect(exported.skins).toEqual(backup.skins)
  expect(exported.acquisitionSortOrder).toEqual(backup.acquisitionSortOrder)

  await page.reload()
  await expect(frame.locator('#stat-characters')).toHaveText('3')
  await expect(frame.locator('#stat-owned')).toHaveText('1')
  await expect(frame.locator('#tab-incomplete')).toHaveAttribute('aria-selected', 'true')
  await expect(frame.locator('#incomplete-list')).toContainText('测试零换装')
  await frame.locator('#tab-collection').click()
  await frame.locator('#reset-filters').click()
  await frame.locator('#character-list .character-row[data-id="fixture_unowned"]').click()
  await expect(frame.locator('#detail-name')).toHaveText('测试未拥有')
  await expectNoOuterOverflow(page)
  await page.screenshot({path: testInfo.outputPath('换装收藏-桌面.png'), fullPage: true})
  await page.setViewportSize({width: 390, height: 844})
  await expectNoOuterOverflow(page)
  await expect(frame.locator('#new-character-btn')).toBeVisible()
  await page.screenshot({path: testInfo.outputPath('换装收藏-窄屏.png'), fullPage: true})
  expect(errors).toEqual([])
})

test('立即离开收藏页与切换实例均保存编辑，返回保留查询上下文', async ({page}) => {
  const errors = await prepare(page, 'light')
  await page.goto('/#/i/demo-main/skin-collection')
  const frame = tracker(page)
  await importBackup(frame)
  await frame.locator('#search-input').fill('测试未拥有')
  await expect(frame.locator('#character-list .character-row')).toHaveCount(1)
  await frame.locator('#character-list .character-row').click()
  const count = frame.locator('#collection-progress-container input[data-character-id="fixture_unowned"]')
  await expect(count).toHaveValue('0')

  // 同一轮事件中提交并离开，确实覆盖 300 ms 保存防抖未结束的情况。
  await page.evaluate(() => {
    const doc = document.querySelector<HTMLIFrameElement>('iframe[title="换装收藏统计"]')!.contentDocument!
    const input = doc.querySelector<HTMLInputElement>('#collection-progress-container input[data-character-id="fixture_unowned"]')!
    input.value = '7'
    input.dispatchEvent(new Event('change', {bubbles: true}))
    location.hash = '#/i/demo-main/overview'
  })
  await expect(page.locator('#main-content')).toBeVisible()
  await page.locator('.primary-nav').getByRole('link', {name: '换装收藏统计', exact: true}).click()
  await expect(frame.locator('#search-input')).toHaveValue('测试未拥有')
  await expect(count).toHaveValue('7')

  await page.locator('.instance-toggle').click()
  await expect(page.getByRole('menuitemradio', {name: 'demo-alt', exact: true})).toBeVisible()
  await page.evaluate(() => {
    const doc = document.querySelector<HTMLIFrameElement>('iframe[title="换装收藏统计"]')!.contentDocument!
    const input = doc.querySelector<HTMLInputElement>('#collection-progress-container input[data-character-id="fixture_unowned"]')!
    input.value = '8'
    input.dispatchEvent(new Event('change', {bubbles: true}))
    const target = [...document.querySelectorAll<HTMLButtonElement>('#instance-menu [role="menuitemradio"]')].find(button => button.textContent === 'demo-alt')!
    target.click()
  })
  await expect(page).toHaveURL(/#\/i\/demo-alt\/skin-collection$/)
  await expect(frame.locator('#stat-characters')).toHaveText('3')
  await expect(count).toHaveValue('8')
  await page.reload()
  await expect(count).toHaveValue('8')
  await expect(frame.locator('#search-input')).toHaveValue('测试未拥有')
  await expect(page.locator('.app-shell')).not.toHaveClass(/with-rail/)
  expect(errors).toEqual([])
})
