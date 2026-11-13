import {expect, test, type FrameLocator, type Page} from '@playwright/test'
import {routeRpc} from '../rpc-route'

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

const announcementEntries = [
  {name: '公告新角色', aliases: ['New Ship'], rarity: 'super_rare', acquisition: '限时建造', skins: [{name: '公告新礼服', acquisition: '商城'}], sourceUrl: 'https://example.test/announcement', announcementId: 'fixture-20261113'},
  {name: '既有角色', aliases: ['Test-A'], rarity: 'super_rare', acquisition: '限时建造', skins: [{name: '海风／礼服', acquisition: '商城'}, {name: '新睡衣', acquisition: '活动'}], sourceUrl: 'https://example.test/announcement', announcementId: 'fixture-20261113'},
]
const existingAnnouncementCharacter = {id: 'existing_announcement', name: 'Ｔｅｓｔ — Ａ', rarity: 'rare', acquisition: '手工获取方式', collected: '9', shareableSkinCount: '2', customTargetCount: 11}
const existingAnnouncementSkin = {id: 'existing_announcement_skin', characterId: 'existing_announcement', name: '海风 / 礼服', acquisition: '手工换装来源', owned: true}

async function mockAnnouncements(page: Page, getEntries: () => typeof announcementEntries) {
  await routeRpc(page, socket => {
    const server = socket.connectToServer()
    socket.onMessage(message => {
      const request = JSON.parse(message)
      if (request.method === 'skinCollection.updates') {
        socket.send(JSON.stringify({v: 1, type: 'response', id: request.id, ok: true,
          result: {entries: getEntries(), checkedAt: '2026-11-13T12:00:00', error: '', pendingNames: [], refreshing: false}}))
      } else server.send(message)
    })
    server.onMessage(message => socket.send(message))
  })
}

async function readCollection(page: Page) {
  return tracker(page).locator('body').evaluate(() => ({
    characters: JSON.parse(localStorage.getItem('azurLaneCharacters') || '[]'),
    skins: JSON.parse(localStorage.getItem('azurLaneSkins') || '[]'),
    undo: JSON.parse(localStorage.getItem('azurLaneUndoHistory') || '[]'),
    redo: JSON.parse(localStorage.getItem('azurLaneRedoHistory') || '[]'),
    processed: localStorage.getItem('azurLaneAnnouncementProcessed'),
  }))
}

async function revisitCollection(page: Page) {
  await page.locator('.primary-nav').getByRole('link', {name: '运行总览', exact: true}).click()
  await expect(page.locator('#main-content')).toBeVisible()
  await page.locator('.primary-nav').getByRole('link', {name: '换装收藏统计', exact: true}).click()
  await expect(page.getByTestId('collection-sync-status')).not.toHaveText(/正在/)
}

test('公告自动增量同步保留既有数据，别名与换装去重，撤销后不重导且旧备份可补录', async ({page}) => {
  const errors = await prepare(page, 'legacy-light')
  await mockAnnouncements(page, () => announcementEntries)
  await page.addInitScript(({character, skin}) => {
    localStorage.setItem('azurLaneCharacters', JSON.stringify([character]))
    localStorage.setItem('azurLaneSkins', JSON.stringify([skin]))
  }, {character: existingAnnouncementCharacter, skin: existingAnnouncementSkin})
  await page.goto('/#/i/demo-main/skin-collection')
  const frame = tracker(page)
  await expect(page.getByTestId('collection-sync-status')).toContainText('新增')
  let state = await readCollection(page)
  expect(state.characters).toHaveLength(2)
  expect(state.skins).toHaveLength(3)
  expect(state.characters.find((item: {id: string}) => item.id === 'existing_announcement')).toEqual({...existingAnnouncementCharacter, customTargetCount: 12})
  expect(state.skins.find((item: {id: string}) => item.id === 'existing_announcement_skin')).toEqual(existingAnnouncementSkin)
  expect(state.characters.find((item: {name: string}) => item.name === '公告新角色')).toMatchObject({collected: '0', shareableSkinCount: '0', customTargetCount: null})
  expect(state.skins.filter((item: {id: string}) => item.id !== 'existing_announcement_skin').every((item: {owned: boolean}) => !item.owned)).toBe(true)
  expect(state.undo).toHaveLength(1)
  const saved = state
  await revisitCollection(page)
  expect(await readCollection(page)).toEqual(saved)
  await frame.locator('#undo-btn').click()
  await expect(frame.locator('#stat-characters')).toHaveText('1')
  await revisitCollection(page)
  state = await readCollection(page)
  expect(state.characters).toEqual([existingAnnouncementCharacter])
  expect(state.skins).toEqual([existingAnnouncementSkin])
  expect(state.undo).toHaveLength(0)
  expect(state.redo).toHaveLength(1)
  expect(state.processed).toBe(saved.processed)

  // 旧备份没有公告标记；显式导入会清理同步偏好，允许补入备份之后的新内容。
  await frame.locator('#import-btn').click()
  await frame.locator('#import-data').fill(JSON.stringify({characters: [existingAnnouncementCharacter], skins: [existingAnnouncementSkin]}))
  await frame.locator('#confirm-import-btn').click()
  await expect(frame.locator('#import-review')).toContainText('1 位角色、1 件换装')
  await frame.locator('#confirm-import-btn').click()
  await expect(frame.locator('#import-modal')).toBeHidden()
  expect((await readCollection(page)).processed).toBeNull()
  await revisitCollection(page)
  await expect(frame.locator('#stat-characters')).toHaveText('2')
  expect((await readCollection(page)).skins).toHaveLength(3)
  expect(errors).toEqual([])
})

test('公告同步事务失败不污染记录与撤销，恢复后可重试', async ({page}) => {
  const errors = await prepare(page, 'light')
  await mockAnnouncements(page, () => announcementEntries)
  await page.addInitScript(() => {
    // 只让公告事务中途的一次写入失败，回滚写入仍可成功。
    const setItem = Storage.prototype.setItem
    let failed = false
    Storage.prototype.setItem = function(key, value) {
      if (!failed && key === 'azurLaneSkins') { failed = true; throw new DOMException('测试模拟存储配额不足', 'QuotaExceededError') }
      return setItem.call(this, key, value)
    }
  })
  await page.goto('/#/i/demo-main/skin-collection')
  await expect(page.getByTestId('collection-sync-status')).toContainText('暂不可用')
  const state = await readCollection(page)
  expect(state).toEqual({characters: [], skins: [], undo: [], redo: [], processed: null})
  await expect(tracker(page).locator('#stat-characters')).toHaveText('0')
  await expect(tracker(page).locator('#undo-btn')).toBeDisabled()
  await revisitCollection(page)
  await expect(tracker(page).locator('#stat-characters')).toHaveText('2')
  expect((await readCollection(page)).undo).toHaveLength(1)
  expect(errors).toEqual([])
})

test('公告追加新角色和换装时保留正在查看的角色、列表与页面位置', async ({page}) => {
  let updates: typeof announcementEntries = []
  await mockAnnouncements(page, () => updates)
  const errors = await prepareScrollCollection(page, 'light')
  const before = await scrollArchive(page)
  updates = [{...announcementEntries[0], skins: []}, {...announcementEntries[1], name: '滚动测试角色20', aliases: [], skins: [{name: '公告追加测试装', acquisition: '活动'}]}]
  await revisitCollection(page)
  await expect(page.getByTestId('collection-sync-status')).toContainText('新增')
  // 名称排序下新角色插在当前行之前，scrollTop 需增加；核对的是原行的实际像素位置。
  const after = await readScrollPosition(page)
  expect(after.listY).toBeGreaterThan(before.listY)
  await expectScrollRestored(page, {...before, listY: after.listY})
  expect((await readCollection(page)).characters).toHaveLength(scrollCharacters.length + 1)
  expect(errors).toEqual([])
})

test('两个标签页同时同步公告仅追加一次，并同步另一页的已保存记录', async ({page, context}) => {
  const other = await context.newPage()
  await prepare(page, 'light')
  await prepare(other, 'light')
  await mockAnnouncements(page, () => announcementEntries)
  await mockAnnouncements(other, () => announcementEntries)
  await Promise.all([page.goto('/#/i/demo-main/skin-collection'), other.goto('/#/i/demo-alt/skin-collection')])
  await expect(tracker(page).locator('#stat-characters')).toHaveText('2')
  await expect(tracker(other).locator('#stat-characters')).toHaveText('2')
  expect(await readCollection(page)).toEqual(await readCollection(other))
  expect((await readCollection(page)).undo).toHaveLength(1)
  await tracker(page).locator('#character-list .character-row').first().click()
  const count = tracker(page).locator('#collection-progress-container input[data-character-id]')
  await count.fill('4')
  await count.dispatchEvent('change')
  await expect.poll(async () => (await readCollection(page)).characters.some((item: {collected: string}) => item.collected === '4')).toBe(true)
  await revisitCollection(other)
  expect(await readCollection(page)).toEqual(await readCollection(other))
  await other.close()
})

test('手工编辑连续保存失败后拒绝公告覆盖，恢复写入后先保存编辑再追加', async ({page}) => {
  let updates: typeof announcementEntries = []
  const errors = await prepare(page, 'light')
  await mockAnnouncements(page, () => updates)
  await page.goto('/#/i/demo-main/skin-collection')
  const frame = tracker(page)
  await expect(page.getByTestId('collection-sync-status')).toContainText('已同步')
  await importBackup(frame)
  await frame.locator('#character-list [data-id="fixture_unowned"]').click()
  const count = frame.locator('#collection-progress-container input[data-character-id="fixture_unowned"]')
  await frame.locator('body').evaluate(() => {
    const original = Storage.prototype.setItem
    ;(window as Window & {restoreTestStorage?: () => void}).restoreTestStorage = () => { Storage.prototype.setItem = original }
    Storage.prototype.setItem = function(key, value) {
      // 每次尝试保存未提交数量都失败，仍允许原值回滚，模拟持续配额不足。
      if (key === 'azurLaneCharacters' && JSON.parse(value).some((item: {collected: string}) => item.collected === '17')) {
        throw new DOMException('测试持续配额不足', 'QuotaExceededError')
      }
      return original.call(this, key, value)
    }
  })
  await count.fill('17')
  await count.dispatchEvent('change')
  // 明确等到 300ms 定时保存已经失败，避免只覆盖尚在防抖中的状态。
  await expect(frame.locator('#save-status')).toHaveAttribute('data-state', 'error')
  await expect(count).toHaveValue('17')
  expect((await readCollection(page)).characters.find((item: {id: string}) => item.id === 'fixture_unowned').collected).toBe('0')
  updates = [announcementEntries[0]]
  await revisitCollection(page)
  await expect(page.getByTestId('collection-sync-status')).toContainText('暂不可用')
  await expect(count).toHaveValue('17')
  expect((await readCollection(page)).characters).toHaveLength(3)
  expect((await readCollection(page)).processed).toBeNull()
  await frame.locator('body').evaluate(() => (window as Window & {restoreTestStorage?: () => void}).restoreTestStorage!())
  await revisitCollection(page)
  await expect(page.getByTestId('collection-sync-status')).toContainText('新增')
  await expect(count).toHaveValue('17')
  const state = await readCollection(page)
  expect(state.characters).toHaveLength(4)
  expect(state.characters.find((item: {id: string}) => item.id === 'fixture_unowned').collected).toBe('17')
  expect(state.skins).toHaveLength(3)
  expect(errors).toEqual([])
})

test('公告和谐名映射原名后保留云仙记录，新建使用新泽西原名且区分II角色', async ({page}) => {
  // 仅使用用户给定的名称映射及虚构收藏数据，不读取真实账号或用户收藏。
  const original = {id: 'fixture_unzen', name: '云仙', rarity: 'super_rare', acquisition: '手工来源', collected: '9', shareableSkinCount: '2', customTargetCount: null}
  const ownedSkin = {id: 'fixture_unzen_owned', characterId: original.id, name: '测试旧装', acquisition: '手工来源', owned: true}
  let updates = [
    {...announcementEntries[0], name: '云仙', aliases: ['猃', '云仙'], skins: [{name: '测试旧装', acquisition: '商城'}, {name: '测试新增云仙换装', acquisition: '商城'}]},
    {...announcementEntries[0], name: '新泽西', aliases: ['花园', '新泽西'], skins: [{name: '测试新增新泽西换装', acquisition: '商城'}]},
  ]
  const errors = await prepare(page, 'legacy-light')
  await mockAnnouncements(page, () => updates)
  await page.addInitScript(({character, skin}) => {
    if (localStorage.getItem('azurLaneCharacters') === null) {
      localStorage.setItem('azurLaneCharacters', JSON.stringify([character]))
      localStorage.setItem('azurLaneSkins', JSON.stringify([skin]))
    }
  }, {character: original, skin: ownedSkin})
  await page.goto('/#/i/demo-main/skin-collection')
  await expect(page.getByTestId('collection-sync-status')).toContainText('新增')
  const state = await readCollection(page)
  expect(state.characters).toHaveLength(2)
  expect(state.characters.find((item: {id: string}) => item.id === original.id)).toEqual(original)
  expect(state.characters.map((item: {name: string}) => item.name)).toEqual(['云仙', '新泽西'])
  expect(state.skins.find((item: {id: string}) => item.id === ownedSkin.id)).toEqual(ownedSkin)
  expect(state.skins.find((item: {name: string}) => item.name === '测试新增云仙换装')).toMatchObject({characterId: original.id, owned: false})
  const newJersey = state.characters.find((item: {name: string}) => item.name === '新泽西')
  expect(newJersey).toMatchObject({collected: '0', shareableSkinCount: '0', customTargetCount: null})
  expect(state.skins.find((item: {name: string}) => item.name === '测试新增新泽西换装')).toMatchObject({characterId: newJersey.id, owned: false})
  await revisitCollection(page)
  expect(await readCollection(page)).toEqual(state)

  // 基础舰与 II 是不同角色；只归一化 Ⅱ/II 表现形式，不删除变体后缀。
  updates = [...updates,
    {...announcementEntries[0], name: '约克城', aliases: ['约克城'], skins: [{name: '测试基础舰换装', acquisition: '活动'}]},
    {...announcementEntries[0], name: '约克城II', aliases: ['约克城Ⅱ'], skins: [{name: '测试II舰换装', acquisition: '活动'}]},
  ]
  await revisitCollection(page)
  await expect(page.getByTestId('collection-sync-status')).toContainText('新增')
  const variants = await readCollection(page)
  expect(variants.characters).toHaveLength(4)
  const base = variants.characters.find((item: {name: string}) => item.name === '约克城')
  const second = variants.characters.find((item: {name: string}) => item.name === '约克城II')
  expect(base.id).not.toBe(second.id)
  expect(variants.skins.find((item: {name: string}) => item.name === '测试基础舰换装')).toMatchObject({characterId: base.id})
  expect(variants.skins.find((item: {name: string}) => item.name === '测试II舰换装')).toMatchObject({characterId: second.id})
  updates = updates.map(entry => entry.name === '约克城II' ? {...entry, name: '约克城Ⅱ', aliases: ['约克城II']} : entry)
  await revisitCollection(page)
  expect(await readCollection(page)).toEqual(variants)
  expect(errors).toEqual([])
})
