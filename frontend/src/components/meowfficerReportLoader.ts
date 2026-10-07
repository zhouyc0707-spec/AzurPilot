import type { MeowfficerScoreReport } from '../api/types'

interface ReportCallbacks {
  onReport: (report: MeowfficerScoreReport) => void
  onError: (error: unknown, explicit: boolean) => void
  onRefreshing: (refreshing: boolean) => void
}

/** 串行读取逐只更新的报告；清空或离开页面后，已发出的旧响应不能恢复旧报告。 */
export function createMeowfficerReportLoader(request: () => Promise<MeowfficerScoreReport>, callbacks: ReportCallbacks) {
  let stopped = false
  let paused = false
  let inFlight = false
  let queuedRefresh = false
  let generation = 0

  async function load(explicit = false) {
    if (stopped || paused) return
    if (explicit) {
      queuedRefresh = true
      callbacks.onRefreshing(true)
    }
    if (inFlight) return
    const showError = queuedRefresh
    queuedRefresh = false
    const currentGeneration = generation
    inFlight = true
    try {
      const report = await request()
      if (!stopped && !paused && currentGeneration === generation) callbacks.onReport(report)
    } catch (error) {
      if (!stopped && !paused && currentGeneration === generation) callbacks.onError(error, showError)
    } finally {
      inFlight = false
      if (!stopped) {
        if (!paused && queuedRefresh) void load()
        else callbacks.onRefreshing(false)
      }
    }
  }

  return {
    poll: () => load(),
    refresh: () => load(true),
    pause: () => {
      paused = true
      generation += 1
      queuedRefresh = false
      callbacks.onRefreshing(false)
    },
    resume: () => {paused = false},
    stop: () => {stopped = true; generation += 1},
  }
}

/** 同一秒内数量不变的天赋修正、锁状态更新及蓝猫进度，也必须显示出来。 */
export function sameMeowfficerReport(previous: MeowfficerScoreReport, next: MeowfficerScoreReport): boolean {
  return JSON.stringify(previous) === JSON.stringify(next)
}
