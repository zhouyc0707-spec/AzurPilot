// @vitest-environment jsdom
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest'
import {createStatisticsRefresh} from './statisticsRefresh'

let hidden = false
const cleanups: (() => void)[] = []
beforeEach(() => {
  vi.useFakeTimers()
  hidden = false
  vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden)
})
afterEach(() => {
  cleanups.splice(0).forEach(cleanup => cleanup())
  vi.restoreAllMocks()
  vi.useRealTimers()
})
function setup(load = vi.fn(async (_silent: boolean) => {})) {
  const refresh = createStatisticsRefresh(load)
  cleanups.push(refresh.dispose)
  return {refresh, load}
}
function visible(value: boolean) {
  hidden = !value
  document.dispatchEvent(new Event('visibilitychange'))
}

describe('统计刷新节流与页面可见性', () => {
  it('首屏立即加载，密集通知每两秒只补查一次且不会无限后推', async () => {
    const {refresh, load} = setup()
    expect(load).toHaveBeenCalledWith(false)
    for (let index = 0; index < 10; index++) {
      refresh.notify()
      await vi.advanceTimersByTimeAsync(190)
    }
    expect(load).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(100)
    expect(load).toHaveBeenCalledTimes(2)
    expect(load).toHaveBeenLastCalledWith(true)
    await vi.advanceTimersByTimeAsync(10000)
    expect(load).toHaveBeenCalledTimes(2)
  })

  it('慢请求期间只留一次待办，完成后也不产生并发请求', async () => {
    const pending: (() => void)[] = []
    const load = vi.fn((_silent: boolean) => new Promise<void>(resolve => pending.push(resolve)))
    const {refresh} = setup(load)
    for (let index = 0; index < 20; index++) refresh.notify()
    await vi.advanceTimersByTimeAsync(5000)
    expect(load).toHaveBeenCalledTimes(1)
    pending[0]()
    await vi.advanceTimersByTimeAsync(300)
    expect(load).toHaveBeenCalledTimes(2)
    pending[1]()
    await vi.advanceTimersByTimeAsync(5000)
    expect(load).toHaveBeenCalledTimes(2)
  })

  it('初始隐藏时不请求，显示后立即读取', async () => {
    hidden = true
    const {refresh, load} = setup()
    refresh.notify()
    await vi.advanceTimersByTimeAsync(10000)
    expect(load).not.toHaveBeenCalled()
    visible(true)
    expect(load).toHaveBeenCalledTimes(1)
    expect(load).toHaveBeenLastCalledWith(false)
  })

  it('隐藏时取消已排定刷新，通知合并到恢复时的一次补查', async () => {
    const {refresh, load} = setup()
    await Promise.resolve()
    refresh.notify()
    visible(false)
    for (let index = 0; index < 20; index++) refresh.notify()
    await vi.advanceTimersByTimeAsync(10000)
    expect(load).toHaveBeenCalledTimes(1)
    visible(true)
    expect(load).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(10000)
    expect(load).toHaveBeenCalledTimes(2)
  })

  it('在途请求未结束时恢复页面，等待原请求后立即补查一次', async () => {
    let finish!: () => void
    const load = vi.fn((_silent: boolean) => new Promise<void>(resolve => {finish = resolve}))
    setup(load)
    visible(false)
    visible(true)
    expect(load).toHaveBeenCalledTimes(1)
    finish()
    await Promise.resolve()
    expect(load).toHaveBeenCalledTimes(2)
    expect(load).toHaveBeenLastCalledWith(false)
    finish()
  })

  it('离开查询后取消定时器与可见性监听，不再发请求', async () => {
    const {refresh, load} = setup()
    await Promise.resolve()
    refresh.notify()
    refresh.dispose()
    visible(false)
    visible(true)
    await vi.advanceTimersByTimeAsync(10000)
    refresh.notify()
    expect(load).toHaveBeenCalledTimes(1)
  })
})
