/** 统计自动刷新合并为至多每两秒一次；隐藏停刷，恢复立即补查。 */
export function createStatisticsRefresh(load: (silent: boolean) => Promise<void>) {
  let active = true
  let running = false
  let pending = false
  let immediate = false
  let lastStarted = -Infinity
  let timer: ReturnType<typeof setTimeout> | undefined

  const schedule = () => {
    if (!active || document.hidden || running || !pending || timer !== undefined) return
    const delay = immediate ? 0 : Math.max(300, 2000 - (Date.now() - lastStarted))
    if (delay === 0) void run()
    else timer = setTimeout(() => {timer = undefined; void run()}, delay)
  }
  const run = async () => {
    if (!active || document.hidden || running || !pending) return
    const silent = !immediate
    pending = immediate = false
    running = true
    lastStarted = Date.now()
    try {await load(silent)}
    finally {running = false; schedule()}
  }
  const refresh = (now = false) => {
    if (!active) return
    pending = true
    immediate ||= now
    if (now) {clearTimeout(timer); timer = undefined}
    schedule()
  }
  const visibility = () => {
    clearTimeout(timer); timer = undefined
    if (!document.hidden) refresh(true)
  }
  document.addEventListener('visibilitychange', visibility)
  refresh(true)
  return {
    notify: () => refresh(),
    dispose: () => {
      active = false
      clearTimeout(timer)
      document.removeEventListener('visibilitychange', visibility)
    },
  }
}
