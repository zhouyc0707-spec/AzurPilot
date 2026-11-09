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

  await expect(page.getByRole('tab', {name: 'demo-alt', exact: true})).toBeVisible()
  await page.evaluate(() => {
    const doc = document.querySelector<HTMLIFrameElement>('iframe[title="换装收藏统计"]')!.contentDocument!
    const input = doc.querySelector<HTMLInputElement>('#collection-progress-container input[data-character-id="fixture_unowned"]')!
    input.value = '8'
    input.dispatchEvent(new Event('change', {bubbles: true}))
    const target = document.querySelector<HTMLElement>('[role="tab"][aria-label="demo-alt"]')!
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

// 长列表夹具让浏览器产生真实滚动，不用 scrollTo 桩或修改用户收藏。
const scrollCharacters = Array.from({length: 48}, (_, index) => ({
  id: `scroll_character_${index}`,
  name: `滚动测试角色${String(index).padStart(2, '0')}`,
  rarity: 'super_rare', acquisition: '活动', collected: '0', customTargetCount: 50,
  createdAt: '2026-01-01T00:00:00.000Z', updatedAt: '2026-01-01T00:00:00.000Z',
}))
const scrollSkins = scrollCharacters.flatMap((character, index) => Array.from({length: index === 20 ? 36 : 2}, (_, skin) => ({
  id: `scroll_skin_${index}_${skin}`, characterId: character.id,
  name: `测试换装${String(skin).padStart(2, '0')}`, acquisition: '活动', owned: false,
})))

async function prepareScrollCollection(page: Page, theme: string) {
  const errors = await prepare(page, theme)
  await page.setViewportSize({width: 1440, height: 900})
  await page.addInitScript(({characters, skins}) => {
    // iframe 重建时不能覆盖数据或查看状态，否则无法验证回主页后的恢复。
    if (localStorage.getItem('azurLaneCharacters') === null) {
      localStorage.setItem('azurLaneCharacters', JSON.stringify(characters))
      localStorage.setItem('azurLaneSkins', JSON.stringify(skins))
    }
  }, {characters: scrollCharacters, skins: scrollSkins})
  await page.goto('/#/i/demo-main/skin-collection')
  await expect(tracker(page).locator('#character-list .character-row')).toHaveCount(scrollCharacters.length)
  return errors
}

async function readScrollPosition(page: Page) {
  return page.evaluate(() => {
    const iframe = document.querySelector<HTMLIFrameElement>('iframe[title="换装收藏统计"]')!
    const doc = iframe.contentDocument!
    const selected = doc.querySelector<HTMLElement>('#character-list .character-row.bg-blue-100')
    const pending = doc.querySelector<HTMLElement>('#incomplete-list [data-detail-character-id="scroll_character_20"]')
    const skin = doc.querySelector<HTMLElement>('#skins-list > *:nth-child(8)')
    const iframeTop = iframe.getBoundingClientRect().top
    return {
      outerY: scrollY,
      hostY: document.querySelector('#skin-collection-main-content')!.scrollTop,
      pageY: iframe.contentWindow!.scrollY,
      listY: doc.querySelector('#character-list')!.scrollTop,
      detailY: doc.querySelector('.detail-panel')!.scrollTop,
      selectedTop: selected ? iframeTop + selected.getBoundingClientRect().top : null,
      skinTop: skin && skin.getClientRects().length ? iframeTop + skin.getBoundingClientRect().top : null,
      pendingTop: pending && pending.getClientRects().length ? iframeTop + pending.getBoundingClientRect().top : null,
      selectedName: doc.querySelector('#detail-name')!.textContent,
      view: doc.querySelector('#tab-incomplete')!.getAttribute('aria-selected'),
    }
  })
}

async function settleCollectionLayout(page: Page) {
  await tracker(page).locator('body').evaluate(async () => {
    await document.fonts.ready
    await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
  })
}

async function scrollArchive(page: Page) {
  const frame = tracker(page)
  await frame.locator('#sort-select').selectOption('name')
  await frame.locator('#character-list [data-id="scroll_character_20"]').click()
  await expect(frame.locator('#detail-name')).toHaveText('滚动测试角色20')
  await settleCollectionLayout(page)
  await frame.locator('body').evaluate(() => {
    const list = document.querySelector('#character-list')!
    const selected = document.querySelector('#character-list [data-id="scroll_character_20"]')!
    list.scrollTop += selected.getBoundingClientRect().top - list.getBoundingClientRect().top - 160
    document.querySelector('.detail-panel')!.scrollTop = 430
    scrollTo(0, Math.min(330, (document.documentElement.scrollHeight - innerHeight) / 2))
  })
  await settleCollectionLayout(page)
  const position = await readScrollPosition(page)
  expect(position.listY).toBeGreaterThan(500)
  expect(position.detailY).toBeGreaterThan(100)
  expect(position.pageY).toBeGreaterThan(100)
  return position
}

async function expectScrollRestored(page: Page, expected: Awaited<ReturnType<typeof readScrollPosition>>) {
  await expect(page.locator('#skin-collection-main-content')).toBeVisible()
  await expect(tracker(page).locator('#detail-name')).toHaveText(expected.selectedName!)
  // 同时比较真实滚动值及视口中的角色/换装位置，允许 1px 舍入误差。
  await expect.poll(async () => {
    const actual = await readScrollPosition(page)
    return Object.entries(expected).filter(([key, value]) => {
      const received = actual[key as keyof typeof actual]
      return typeof value === 'number'
        ? typeof received !== 'number' || Math.abs(received - value) > 1
        : received !== value
    }).map(([key, value]) => ({field: key, expected: value, actual: actual[key as keyof typeof actual]}))
  }, {message: '返回收藏页应恢复内外层滚动与所选记录的像素位置'}).toEqual([])
}

for (const theme of ['legacy-light', 'light']) {
  test(`${theme} 收藏页切换总览后恢复角色列表、详情及页面滚动位置`, async ({page}, testInfo) => {
    const errors = await prepareScrollCollection(page, theme)
    const before = await scrollArchive(page)
    await page.locator('.primary-nav').getByRole('link', {name: '运行总览', exact: true}).click()
    await expect(page.locator('#main-content')).toBeVisible()
    await page.locator('.primary-nav').getByRole('link', {name: '换装收藏统计', exact: true}).click()
    await testInfo.attach('返回总览前的滚动位置', {body: JSON.stringify(before), contentType: 'application/json'})
    await testInfo.attach('返回总览后的滚动位置', {body: JSON.stringify(await readScrollPosition(page)), contentType: 'application/json'})
    await expectScrollRestored(page, before)

    // 提交数量后在同一事件里离开，隐藏时间超过原网页 180ms/300ms 防抖。
    await page.evaluate(() => {
      const doc = document.querySelector<HTMLIFrameElement>('iframe[title="换装收藏统计"]')!.contentDocument!
      const input = doc.querySelector<HTMLInputElement>('#collection-progress-container input[data-character-id="scroll_character_20"]')!
      input.value = '7'
      input.dispatchEvent(new Event('change', {bubbles: true}))
      document.querySelector<HTMLAnchorElement>('.primary-nav a[href="#/i/demo-main/overview"]')!.click()
    })
    await expect(page.locator('#main-content')).toBeVisible()
    await page.waitForTimeout(350)

    // 返回后仅过一帧就再次离开，覆盖尚未完成的滚动恢复回调。
    await page.evaluate(() => new Promise<void>(resolve => {
      document.querySelector<HTMLAnchorElement>('.primary-nav a[href="#/i/demo-main/skin-collection"]')!.click()
      requestAnimationFrame(() => {
        document.querySelector<HTMLAnchorElement>('.primary-nav a[href="#/i/demo-main/overview"]')!.click()
        resolve()
      })
    }))
    await expect(page.locator('#main-content')).toBeVisible()
    await page.waitForTimeout(350)
    await page.locator('.primary-nav').getByRole('link', {name: '换装收藏统计', exact: true}).click()
    await expectScrollRestored(page, before)
    await expect(tracker(page).locator('#collection-progress-container input[data-character-id="scroll_character_20"]')).toHaveValue('7')
    expect(errors).toEqual([])
  })

  test(`${theme} 待完成清单切换页面与回主页重建后恢复滚动位置`, async ({page}, testInfo) => {
    const errors = await prepareScrollCollection(page, theme)
    await scrollArchive(page)
    const frame = tracker(page)
    await frame.locator('#tab-incomplete').click()
    await expect(frame.locator('#incomplete-list [data-detail-character-id]')).toHaveCount(scrollCharacters.length)
    await settleCollectionLayout(page)
    await frame.locator('body').evaluate(() => scrollTo(0, 1800))
    await settleCollectionLayout(page)
    const before = await readScrollPosition(page)
    expect(before.pageY).toBeGreaterThan(1000)
    await page.locator('.primary-nav').getByRole('link', {name: '运行总览', exact: true}).click()
    await expect(page.locator('#main-content')).toBeVisible()
    await page.locator('.primary-nav').getByRole('link', {name: '换装收藏统计', exact: true}).click()
    await testInfo.attach('待完成清单离开前', {body: JSON.stringify(before), contentType: 'application/json'})
    await testInfo.attach('待完成清单返回后', {body: JSON.stringify(await readScrollPosition(page)), contentType: 'application/json'})
    await expectScrollRestored(page, before)

    // 通过主页真正卸载实例 iframe，再返回，验证 sessionStorage 恢复路径。
    await page.locator('.brand-title').first().click()
    await expect(page).toHaveURL(theme.startsWith('legacy') ? /#\/$/ : /#\/settings$/)
    await expect(page.locator('iframe[title="换装收藏统计"]')).toHaveCount(0)
    await page.evaluate(() => { location.hash = '#/i/demo-main/skin-collection' })
    await expect(frame.locator('#stat-characters')).toHaveText(String(scrollCharacters.length))
    await expectScrollRestored(page, before)
    expect(errors).toEqual([])
  })
}
