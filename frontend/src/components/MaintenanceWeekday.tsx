/** 旧版总览的维护星期快捷入口，与演习、困难图共用现有配置。 */
import { useEffect, useState, useSyncExternalStore } from 'react'
import { api } from '../api/client'
import type { Config } from '../api/types'
import { useApp, useConnection } from '../app/context'
import { editor } from '../config/editors'
import { EditStatus } from './EditStatus'
import { Select } from './Select'

const PATH = 'Exercise.Exercise.MaintenanceWeekday'
const DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']

function WeekdayControl({instance}: {instance: string}) {
  const {t, ui} = useApp()
  const connection = useConnection()
  const queue = editor(`config:${instance}`)
  const {edits, storageError} = useSyncExternalStore(queue.subscribe, queue.getSnapshot, queue.getSnapshot)
  const [weekday, setWeekday] = useState('')
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let active = true
    let sequence = 0
    const accept = (config: Config) => {
      if (!active || config.instance !== instance) return
      const value = config.values.Exercise?.Exercise?.MaintenanceWeekday
      setWeekday(typeof value === 'string' && DAYS.includes(value) ? value : '')
      setError('')
    }
    const saved = (response: unknown) => {
      // 保存回执优先于更早发出的读取，避免新星期被迟到的旧配置覆盖。
      sequence += 1
      accept(response as Config)
    }
    queue.onSaved = saved
    const load = () => {
      if (connection !== 'ready' || document.visibilityState === 'hidden') return
      const request = ++sequence
      const confirmed = queue.confirmed()
      void api.request('config.get', {instance}).then(config => {
        if (!active || request !== sequence) return
        accept(config)
        queue.reconcile(confirmed)
      }).catch(reason => {
        if (active && request === sequence) setError((reason as Error).message)
      })
    }
    load()
    // 返回页面、恢复连接或重新聚焦时读最新值，不跟随高频总览推送拉全量配置。
    window.addEventListener('focus', load)
    document.addEventListener('visibilitychange', load)
    return () => {
      active = false
      if (queue.onSaved === saved) queue.onSaved = undefined
      window.removeEventListener('focus', load)
      document.removeEventListener('visibilitychange', load)
    }
  }, [instance, connection, queue, attempt])

  const edit = edits[PATH]
  const value = typeof edit?.value === 'string' ? edit.value : weekday
  return <div className="maintenance-weekday" title={ui('overview.maintenanceHint')}>
    <div className="maintenance-weekday-control">
      <label htmlFor="overview-maintenance-weekday">{ui('overview.maintenanceDay')}</label>
      <Select id="overview-maintenance-weekday" aria-label={ui('overview.maintenanceDay')}
        value={value} disabled={!weekday || connection !== 'ready' || Boolean(error)}
        onChange={event => queue.change(PATH, event.target.value)}>
        {!value && <option value="" disabled>{ui('common.loading')}</option>}
        {DAYS.map(day => <option key={day} value={day}>{t(`Exercise.MaintenanceWeekday.${day}`)}</option>)}
      </Select>
    </div>
    {error ? <div className="maintenance-weekday-error" role="alert" title={error}>
      <span>{error}</span><button type="button" className="maintenance-weekday-retry"
        onClick={() => setAttempt(value => value + 1)}>{ui('common.retry')}</button>
    </div>
      : <EditStatus edit={edit} id="overview-maintenance-weekday" retry={queue.retry} queue={queue}/>}
    {storageError && <span className="maintenance-weekday-warning" role="alert">{storageError}</span>}
  </div>
}

export function MaintenanceWeekday({instance}: {instance: string}) {
  return <WeekdayControl key={instance} instance={instance}/>
}
