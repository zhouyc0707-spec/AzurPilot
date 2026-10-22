/** 公告只读查询使用替身，验证多个页面共用请求与轮询，不访问公网。 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Announcement } from '../api/types'

const hooks = vi.hoisted(() => ({
  connection: 'ready',
  cleanups: [] as Array<(() => void) | undefined>,
  request: vi.fn<(method: string, params: {force: boolean}) => Promise<Announcement | null>>(),
}))
vi.mock('react', () => ({
  useCallback: (callback: unknown) => callback,
  useEffect: (effect: () => (() => void) | undefined) => { hooks.cleanups.push(effect()) },
  useSyncExternalStore: (_subscribe: unknown, snapshot: () => unknown) => snapshot(),
}))
vi.mock('../api/client', () => ({api: {request: hooks.request}}))
vi.mock('./context', () => ({useConnection: () => hooks.connection}))

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<T>((done, fail) => {resolve = done; reject = fail})
  return {promise, resolve, reject}
}

const announcement: Announcement = {announcementId: 'one', title: '公告', content: '正文'}

describe('公告请求与共享轮询', () => {
  let store: typeof import('./announcement')
  let storage: Map<string, string>
  const unmount = (index: number) => {
    hooks.cleanups[index]?.()
    hooks.cleanups[index] = undefined
  }

  beforeEach(async () => {
    vi.resetModules()
    vi.useFakeTimers()
    hooks.connection = 'ready'
    hooks.cleanups = []
    hooks.request.mockReset().mockResolvedValue(announcement)
    storage = new Map()
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => { storage.set(key, value) },
    })
    store = await import('./announcement')
  })

  afterEach(() => {
    hooks.cleanups.forEach((_, index) => unmount(index))
    vi.clearAllTimers()
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('常规与手动刷新重叠时共用在途查询，完成后允许新的强制刷新', async () => {
    const pending = deferred<Announcement | null>()
    hooks.request.mockReturnValueOnce(pending.promise)
    const normal = store.fetchAnnouncement()
    const forced = store.fetchAnnouncement(true)
    expect(normal).toBe(forced)
    expect(store.getAnnouncementSnapshot().loading).toBe(true)
    await Promise.resolve()
    expect(hooks.request).toHaveBeenCalledExactlyOnceWith('announcement.get', {force: false})
    pending.resolve(announcement)
    await normal
    expect(store.getAnnouncementSnapshot()).toMatchObject({data: announcement, loading: false, error: ''})
    await store.fetchAnnouncement(true)
    expect(hooks.request).toHaveBeenLastCalledWith('announcement.get', {force: true})
    expect(hooks.request).toHaveBeenCalledTimes(2)
  })

  it('外壳与多个页面只有一个轮询，部分卸载或切页不重复首查', async () => {
    store.useAnnouncement()
    store.useAnnouncement()
    await store.fetchAnnouncement()
    expect(hooks.request).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(1)
    unmount(1)
    store.useAnnouncement()
    await Promise.resolve()
    expect(hooks.request).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(120_000)
    expect(hooks.request).toHaveBeenCalledTimes(2)
    unmount(0)
    expect(vi.getTimerCount()).toBe(1)
    unmount(2)
    expect(vi.getTimerCount()).toBe(0)
    await vi.advanceTimersByTimeAsync(240_000)
    expect(hooks.request).toHaveBeenCalledTimes(2)
  })

  it('慢请求跨过轮询时不重复发送', async () => {
    const pending = deferred<Announcement | null>()
    hooks.request.mockReturnValueOnce(pending.promise)
    store.useAnnouncement()
    await vi.advanceTimersByTimeAsync(240_000)
    expect(hooks.request).toHaveBeenCalledTimes(1)
    pending.resolve(announcement)
    await store.fetchAnnouncement()
    await vi.advanceTimersByTimeAsync(120_000)
    expect(hooks.request).toHaveBeenCalledTimes(2)
  })

  it('断线停止轮询，重连时重新读取且仍只有一个定时器', async () => {
    store.useAnnouncement()
    store.useAnnouncement()
    await store.fetchAnnouncement()
    hooks.connection = 'offline'
    unmount(0)
    unmount(1)
    const offline = store.useAnnouncement()
    await offline.refresh()
    await vi.advanceTimersByTimeAsync(240_000)
    expect(hooks.request).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(0)
    hooks.connection = 'ready'
    store.useAnnouncement()
    store.useAnnouncement()
    await store.fetchAnnouncement()
    expect(hooks.request).toHaveBeenCalledTimes(2)
    expect(vi.getTimerCount()).toBe(1)
  })

  it('失败保留旧公告，结束加载状态并允许后续重试', async () => {
    await store.fetchAnnouncement()
    hooks.request.mockRejectedValueOnce(new Error('连接已断开'))
    await store.fetchAnnouncement(true)
    expect(store.getAnnouncementSnapshot()).toMatchObject({data: announcement, loading: false, error: '连接已断开'})
    const changed = {...announcement, announcementId: 'two'}
    hooks.request.mockResolvedValueOnce(changed)
    await store.fetchAnnouncement()
    expect(store.getAnnouncementSnapshot()).toMatchObject({data: changed, loading: false, error: ''})
  })

  it('无公告结果正常结束，已读状态与新公告未读提示继续保留', async () => {
    hooks.request.mockResolvedValueOnce(null)
    await store.fetchAnnouncement()
    expect(store.getAnnouncementSnapshot()).toMatchObject({data: null, loading: false, error: ''})
    await store.fetchAnnouncement()
    expect(store.useAnnouncement().unread).toBe(true)
    store.markAnnouncementAsRead()
    expect(storage.get('azurpilot.announcement.last-read-id')).toBe('one')
    expect(store.useAnnouncement().unread).toBe(false)
    hooks.request.mockResolvedValueOnce({...announcement, announcementId: 'two'})
    // 首个 hook 的自动读取已经在途，与手动刷新合并。
    await store.fetchAnnouncement()
    expect(store.useAnnouncement().unread).toBe(true)
  })
})
