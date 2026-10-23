import { describe, expect, it, vi } from 'vitest'
import { createLegacyStatisticsRefresh } from './legacyStatisticsRefresh'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return {promise, resolve, reject}
}

function setup() {
  const requests: ReturnType<typeof deferred<number>>[] = []
  const request = vi.fn(() => {
    const next = deferred<number>()
    requests.push(next)
    return next.promise
  })
  const callbacks = {onData: vi.fn(), onError: vi.fn(), onBusy: vi.fn(), onIdle: vi.fn()}
  const refresh = createLegacyStatisticsRefresh(request)
  refresh.activate(callbacks)
  return {refresh, request, requests, callbacks}
}

describe('旧版统计刷新合并与失效保护', () => {
  it('同一查询只发一个请求，将刷新期间的通知合并成一次补查', async () => {
    const {refresh, request, requests, callbacks} = setup()
    refresh.refresh()
    for (let i = 0; i < 50; i++) refresh.refresh()
    expect(request).toHaveBeenCalledTimes(1)
    requests[0].resolve(10)
    await Promise.resolve()
    expect(request).toHaveBeenCalledTimes(2)
    requests[1].resolve(20)
    await Promise.resolve()
    expect(request).toHaveBeenCalledTimes(2)
    expect(callbacks.onData.mock.calls).toEqual([[10], [20]])
    expect(callbacks.onIdle).toHaveBeenCalledTimes(1)
  })

  it('手动刷新排队后保持忙碌，错误仍按手动操作反馈', async () => {
    const {refresh, requests, callbacks} = setup()
    refresh.refresh()
    refresh.refresh(true)
    expect(callbacks.onBusy).toHaveBeenLastCalledWith(true)
    requests[0].resolve(10)
    await Promise.resolve()
    expect(callbacks.onBusy).not.toHaveBeenCalledWith(false)
    const error = new Error('请求失败')
    requests[1].reject(error)
    await Promise.resolve()
    expect(callbacks.onError).toHaveBeenCalledWith(error, true)
    expect(callbacks.onBusy).toHaveBeenLastCalledWith(false)
  })

  it('自动刷新失败不会伪装成手动错误，随后还能继续刷新', async () => {
    const {refresh, requests, callbacks, request} = setup()
    refresh.refresh()
    const error = new Error('连接中断')
    requests[0].reject(error)
    await Promise.resolve()
    expect(callbacks.onError).toHaveBeenCalledWith(error, false)
    refresh.refresh()
    expect(request).toHaveBeenCalledTimes(2)
    requests[1].resolve(20)
    await Promise.resolve()
    expect(callbacks.onData).toHaveBeenCalledWith(20)
  })

  it('隐藏期间丢弃在途结果和排队刷新，不产生额外请求', async () => {
    const {refresh, requests, callbacks, request} = setup()
    refresh.refresh()
    refresh.refresh(true)
    refresh.deactivate()
    refresh.refresh()
    requests[0].resolve(10)
    await Promise.resolve()
    expect(callbacks.onData).not.toHaveBeenCalled()
    expect(callbacks.onIdle).not.toHaveBeenCalled()
    expect(request).toHaveBeenCalledTimes(1)
  })

  it('Activity 快速隐藏又显示时仍串行执行，旧响应不能覆盖恢复后的数据', async () => {
    const {refresh, requests, callbacks, request} = setup()
    refresh.refresh()
    refresh.deactivate()
    refresh.activate(callbacks)
    refresh.refresh()
    expect(request).toHaveBeenCalledTimes(1)
    requests[0].resolve(10)
    await Promise.resolve()
    expect(callbacks.onData).not.toHaveBeenCalled()
    expect(request).toHaveBeenCalledTimes(2)
    requests[1].resolve(20)
    await Promise.resolve()
    expect(callbacks.onData.mock.calls).toEqual([[20]])
  })

  it('隐藏时旧请求失败不报告错误，显示后可重新读取', async () => {
    const {refresh, requests, callbacks, request} = setup()
    refresh.refresh(true)
    refresh.deactivate()
    requests[0].reject(new Error('旧页面错误'))
    await Promise.resolve()
    expect(callbacks.onError).not.toHaveBeenCalled()
    refresh.activate(callbacks)
    refresh.refresh()
    expect(request).toHaveBeenCalledTimes(2)
  })

  it('月份或实例变更时，各自的查询互不阻塞且旧结果不能提交', async () => {
    const oldMonth = setup()
    oldMonth.refresh.refresh()
    oldMonth.refresh.deactivate()
    const nextMonth = setup()
    nextMonth.refresh.refresh()
    nextMonth.requests[0].resolve(20)
    await Promise.resolve()
    oldMonth.requests[0].resolve(10)
    await Promise.resolve()
    expect(oldMonth.callbacks.onData).not.toHaveBeenCalled()
    expect(nextMonth.callbacks.onData.mock.calls).toEqual([[20]])
  })

  it('同步请求异常也会结束忙碌状态', async () => {
    const error = new Error('离线')
    const callbacks = {onData: vi.fn(), onError: vi.fn(), onBusy: vi.fn(), onIdle: vi.fn()}
    const refresh = createLegacyStatisticsRefresh(() => { throw error })
    refresh.activate(callbacks)
    refresh.refresh(true)
    expect(callbacks.onError).toHaveBeenCalledWith(error, true)
    expect(callbacks.onBusy.mock.calls).toEqual([[true], [false]])
    expect(callbacks.onIdle).toHaveBeenCalledTimes(1)
  })
})
