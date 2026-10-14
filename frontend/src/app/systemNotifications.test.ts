/** 原生通知只使用浏览器 API 替身，不申请真实权限或发出真实通知。 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApiEvent } from '../api/types'

const hook = vi.hoisted(() => ({
  listener: undefined as ((event: ApiEvent) => void) | undefined,
  cleanup: undefined as (() => void) | undefined,
  unsubscribe: vi.fn(),
}))
vi.mock('react', () => ({useEffect: (effect: () => (() => void)) => {hook.cleanup = effect()}}))
vi.mock('../api/client', () => ({api: {onEvent: (listener: (event: ApiEvent) => void) => {
  hook.listener = listener
  return hook.unsubscribe
}}}))

class FakeNotification {
  static permission: NotificationPermission = 'granted'
  static instances: FakeNotification[] = []
  static requestPermission = vi.fn<() => Promise<NotificationPermission>>()
  onclick?: () => void
  close = vi.fn()
  constructor(public title: string, public options: NotificationOptions) {
    FakeNotification.instances.push(this)
  }
}

describe('浏览器任务通知', () => {
  let page: EventTarget
  const push = (title = '任务结束', topic = 'notifications') => hook.listener?.({
    v: 1, type: 'event', topic, seq: 1, data: {instance: 'test', title, content: '正文'},
  } as ApiEvent)

  beforeEach(async () => {
    vi.resetModules()
    hook.listener = undefined
    hook.cleanup = undefined
    hook.unsubscribe.mockClear()
    page = new EventTarget()
    FakeNotification.permission = 'granted'
    FakeNotification.instances = []
    FakeNotification.requestPermission.mockReset()
    vi.stubGlobal('document', page)
    vi.stubGlobal('window', {focus: vi.fn()})
    vi.stubGlobal('Notification', FakeNotification)
    const {useSystemNotifications} = await import('./systemNotifications')
    useSystemNotifications()
  })
  afterEach(() => {hook.cleanup?.(); vi.unstubAllGlobals()})

  it('已授权时显示标题正文，点击聚焦页面并关闭通知', () => {
    push()
    const notification = FakeNotification.instances[0]
    expect(notification.title).toBe('任务结束')
    expect(notification.options).toMatchObject({body: '正文', icon: '/azurpilot.svg'})
    expect(FakeNotification.requestPermission).not.toHaveBeenCalled()
    notification.onclick?.()
    expect(window.focus).toHaveBeenCalledOnce()
    expect(notification.close).toHaveBeenCalledOnce()
  })

  it('未授权时等待一次用户点击，获准后只补发首条通知', async () => {
    FakeNotification.permission = 'default'
    FakeNotification.requestPermission.mockImplementation(async () => {
      FakeNotification.permission = 'granted'
      return 'granted'
    })
    push('第一条')
    push('第二条')
    expect(FakeNotification.requestPermission).not.toHaveBeenCalled()
    expect(FakeNotification.instances).toHaveLength(0)
    page.dispatchEvent(new Event('pointerdown'))
    await Promise.resolve()
    page.dispatchEvent(new Event('pointerdown'))
    expect(FakeNotification.requestPermission).toHaveBeenCalledOnce()
    expect(FakeNotification.instances).toHaveLength(1)
    expect(FakeNotification.instances[0].title).toBe('第一条')
  })

  it('已拒绝时既不申请权限也不弹通知', () => {
    FakeNotification.permission = 'denied'
    push()
    page.dispatchEvent(new Event('pointerdown'))
    expect(FakeNotification.requestPermission).not.toHaveBeenCalled()
    expect(FakeNotification.instances).toHaveLength(0)
  })

  it('授权请求被拒绝时不补发通知', async () => {
    FakeNotification.permission = 'default'
    FakeNotification.requestPermission.mockResolvedValue('denied')
    push()
    page.dispatchEvent(new Event('pointerdown'))
    await Promise.resolve()
    expect(FakeNotification.instances).toHaveLength(0)
  })

  it('浏览器没有通知 API 时不影响事件处理', () => {
    vi.stubGlobal('Notification', undefined)
    expect(() => push()).not.toThrow()
    expect(FakeNotification.instances).toHaveLength(0)
  })

  it('其他主题不会弹出系统通知', () => {
    push('总览', 'overview')
    expect(FakeNotification.instances).toHaveLength(0)
  })

  it('App 退出时移除 API 事件订阅', () => {
    hook.cleanup?.()
    expect(hook.unsubscribe).toHaveBeenCalledOnce()
    hook.cleanup = undefined
  })
})
