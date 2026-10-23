interface RefreshCallbacks<T> {
  onData: (data: T) => void
  onError: (error: unknown, explicit: boolean) => void
  onBusy: (busy: boolean) => void
  onIdle: () => void
}

/** 同一查询只发一个请求；刷新期间的通知合并为下一次，离开页面后丢弃旧响应。 */
export function createLegacyStatisticsRefresh<T>(request: () => Promise<T>) {
  let callbacks: RefreshCallbacks<T> | undefined
  let running = false
  let queued = false
  let explicitQueued = false
  let generation = 0

  async function drain() {
    if (running) return
    running = true
    try {
      while (callbacks && queued) {
        const currentGeneration = generation
        const explicit = explicitQueued
        queued = explicitQueued = false
        try {
          const data = await request()
          if (callbacks && currentGeneration === generation) callbacks.onData(data)
        } catch (error) {
          if (callbacks && currentGeneration === generation) callbacks.onError(error, explicit)
        }
      }
    } finally {
      running = false
      callbacks?.onBusy(false)
      callbacks?.onIdle()
    }
  }

  return {
    activate(next: RefreshCallbacks<T>) { callbacks = next },
    deactivate() {
      callbacks = undefined
      generation += 1
      queued = explicitQueued = false
    },
    refresh(explicit = false) {
      if (!callbacks) return
      queued = true
      explicitQueued ||= explicit
      if (explicit) callbacks.onBusy(true)
      void drain()
    },
  }
}
