/**
 * @fileoverview 岛屿计划「一键关闭 / 恢复全部岛屿任务」控件（本地定制）。
 *
 * 挂在岛屿计划全局配置组（IslandPlan）的「当前季节」下方：一次调用即可关闭该组下
 * 当前所有已启用的岛屿任务，并记住是哪几个被这次操作关掉的；再点一下即恢复。
 * 后端只恢复**仍处于关闭状态**的那些，中途被手动打开过的任务不动。
 */

import { useEffect, useState } from 'react'
import { LoaderCircle, Pause, Play } from 'lucide-react'
import { api } from '../api/client'
import type { IslandSuspendState } from '../api/types'
import { useApp, useConnection } from '../app/context'

export function IslandSuspendControl({instance, onChanged}: {instance: string; onChanged: () => void}) {
  const {notify, ui} = useApp()
  const connection = useConnection()
  const [state, setState] = useState<IslandSuspendState>()
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let alive = true
    api.request('island.suspend.state', {instance})
      .then(value => { if (alive) setState(value) })
      .catch(() => { /* 读不到状态时按钮保持禁用，不打扰用户 */ })
    return () => { alive = false }
  }, [instance])

  const suspended = state?.suspendedCount ?? 0

  async function toggle() {
    if (!state) return
    const before = state
    setBusy(true)
    try {
      const next = await api.request('island.suspend.toggle', {instance})
      setState(next)
      if (before.suspendedCount > 0) {
        notify(ui('island.restoreNotice', {count: String(before.suspendedCount)}))
      } else {
        notify(ui('island.suspendNotice', {count: String(before.enabledCount)}))
      }
      /* 任务的 Scheduler.Enable 已改，让配置页重新拉取，调度组里的开关同步刷新。 */
      onChanged()
    } catch (error) {
      notify((error as Error).message, true)
    } finally {
      setBusy(false)
    }
  }

  return <div className="field-row island-suspend-row">
    <div className="field-label">
      <span className="field-name">{ui('island.suspendTitle')}</span>
      <p>{ui('island.suspendHint', {count: String(state?.enabledCount ?? 0)})}</p>
    </div>
    <div className="field-control">
      <button
        type="button"
        className={`button${suspended ? ' primary' : ''}`}
        disabled={busy || connection !== 'ready' || !state}
        aria-busy={busy}
        onClick={() => void toggle()}
      >
        {busy ? <LoaderCircle size={15} className="spin"/> : suspended ? <Play size={15}/> : <Pause size={15}/>}
        {suspended ? ui('island.restoreAction', {count: String(suspended)}) : ui('island.suspendAction')}
      </button>
      {suspended > 0 && state?.suspended.length ? <p className="muted small">{ui('island.suspendedList', {tasks: state.suspended.join('、')})}</p> : null}
    </div>
  </div>
}
