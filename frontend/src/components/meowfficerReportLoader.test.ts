import { describe, expect, it, vi } from 'vitest'
import type { MeowfficerScoreReport } from '../api/types'
import { createMeowfficerReportLoader, sameMeowfficerReport } from './meowfficerReportLoader'

const report: MeowfficerScoreReport = {
  instance: 'test', generatedAt: '2026-10-07 12:00:00', count: 1, scannedCount: 2,
  cats: [{cat: '克雷喵', level: 30, talents: [{name: '狼群之首', kind: 'special', level: 1}]}],
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((done, fail) => {resolve = done; reject = fail})
  return {promise, resolve, reject}
}

function callbacks() {return {onReport: vi.fn(), onError: vi.fn(), onRefreshing: vi.fn()}}

describe('逐只报告串行读取', () => {
  it('慢请求期间自动轮询不会重叠请求，收到结果后才能开始下一次', async () => {
    const first = deferred<MeowfficerScoreReport>()
    const request = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue(report)
    const handlers = callbacks()
    const loader = createMeowfficerReportLoader(request, handlers)
    const pending = loader.poll()
    await loader.poll(); await loader.poll()
    expect(request).toHaveBeenCalledTimes(1)
    first.resolve(report); await pending
    expect(handlers.onReport).toHaveBeenCalledWith(report)
    await loader.poll()
    expect(request).toHaveBeenCalledTimes(2)
  })

  it('手动刷新在轮询结束后补一次读取，连续点击合并而不并发', async () => {
    const first = deferred<MeowfficerScoreReport>()
    const second = deferred<MeowfficerScoreReport>()
    const request = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const handlers = callbacks()
    const loader = createMeowfficerReportLoader(request, handlers)
    const pending = loader.poll()
    await loader.refresh(); await loader.refresh()
    expect(request).toHaveBeenCalledTimes(1)
    first.resolve(report); await pending
    expect(request).toHaveBeenCalledTimes(2)
    expect(handlers.onRefreshing).not.toHaveBeenCalledWith(false)
    second.resolve({...report, scannedCount: 3})
    await Promise.resolve(); await Promise.resolve()
    expect(handlers.onReport).toHaveBeenLastCalledWith({...report, scannedCount: 3})
    expect(handlers.onRefreshing).toHaveBeenLastCalledWith(false)
  })

  it('清空前发出的迟到响应失效，清空期间不读取，恢复后接受新报告', async () => {
    const first = deferred<MeowfficerScoreReport>()
    const request = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue({...report, scannedCount: 4})
    const handlers = callbacks()
    const loader = createMeowfficerReportLoader(request, handlers)
    const pending = loader.poll()
    loader.pause(); await loader.poll(); await loader.refresh()
    expect(request).toHaveBeenCalledTimes(1)
    loader.resume(); first.resolve(report); await pending
    expect(handlers.onReport).not.toHaveBeenCalled()
    await loader.poll()
    expect(handlers.onReport).toHaveBeenCalledWith({...report, scannedCount: 4})
  })

  it('清空会取消在途请求之后排队的手动刷新', async () => {
    const first = deferred<MeowfficerScoreReport>()
    const request = vi.fn().mockReturnValue(first.promise)
    const loader = createMeowfficerReportLoader(request, callbacks())
    const pending = loader.poll(); await loader.refresh()
    loader.pause(); loader.resume()
    first.resolve(report); await pending
    expect(request).toHaveBeenCalledTimes(1)
  })

  it('离开页面后不应用旧响应、不发出排队请求', async () => {
    const first = deferred<MeowfficerScoreReport>()
    const request = vi.fn().mockReturnValue(first.promise)
    const handlers = callbacks()
    const loader = createMeowfficerReportLoader(request, handlers)
    const pending = loader.poll(); await loader.refresh()
    handlers.onRefreshing.mockClear()
    loader.stop(); first.resolve(report); await pending; await loader.poll()
    expect(request).toHaveBeenCalledTimes(1)
    expect(handlers.onReport).not.toHaveBeenCalled()
    expect(handlers.onRefreshing).not.toHaveBeenCalled()
  })

  it('后台失败与手动失败可区分，失败后仍可读取下一份报告', async () => {
    const error = new Error('断线')
    const request = vi.fn().mockRejectedValueOnce(error).mockRejectedValueOnce(error).mockResolvedValue(report)
    const handlers = callbacks()
    const loader = createMeowfficerReportLoader(request, handlers)
    await loader.poll()
    expect(handlers.onError).toHaveBeenLastCalledWith(error, false)
    await loader.refresh()
    expect(handlers.onError).toHaveBeenLastCalledWith(error, true)
    await loader.poll()
    expect(handlers.onReport).toHaveBeenCalledWith(report)
  })

  it('清空期间的旧失败也不能覆盖新的空状态', async () => {
    const first = deferred<MeowfficerScoreReport>()
    const handlers = callbacks()
    const loader = createMeowfficerReportLoader(() => first.promise, handlers)
    const pending = loader.refresh()
    loader.pause(); loader.resume(); first.reject(new Error('旧失败')); await pending
    expect(handlers.onError).not.toHaveBeenCalled()
  })
})

describe('报告内容变化', () => {
  it('相同报告保持原组件状态，蓝猫读取进度仍可更新', () => {
    expect(sameMeowfficerReport(report, structuredClone(report))).toBe(true)
    expect(sameMeowfficerReport(report, {...report, scannedCount: 3})).toBe(false)
  })

  it('时间和数量不变的天赋修正、评分与操作说明也属于变化', () => {
    expect(sameMeowfficerReport(report, {...report, cats: [{...report.cats[0], talents: [{name: '侵略如火', kind: 'special'}]}]})).toBe(false)
    expect(sameMeowfficerReport(report, {...report, cats: [{...report.cats[0], rubrics: [{label: '潜艇猫', score: 88}]}]})).toBe(false)
    expect(sameMeowfficerReport(report, {...report, lockActions: [{name: '蓝猫', before: true, after: false, target: false, status: 'changed', reason: '解锁已确认'}]})).toBe(false)
  })
})
