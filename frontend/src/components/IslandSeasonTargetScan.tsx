import {useEffect, useRef, useState} from 'react'
import {ScanLine} from 'lucide-react'
import {api} from '../api/client'
import {useApp, useConnection} from '../app/context'
import {LogPanel} from './LogPanel'
import {ErrorBox} from './ui'
import {islandSeasonText} from './islandSeasonText'

export const SEASON_SCAN_TASK = 'IslandSeasonTargetScan'

export function IslandSeasonTargetScan({instance, beforeStart, onFinished, onBusyChange}: {
  instance: string; beforeStart: () => Promise<void>; onFinished: () => Promise<void>; onBusyChange?: (busy: boolean) => void
}) {
  const {instances, language, ui} = useApp()
  const connection = useConnection()
  const text = islandSeasonText(language)
  const current = instances.find(item => item.name === instance)
  const running = current?.status === 'running' && current.currentTask === SEASON_SCAN_TASK
  const [starting, setStarting] = useState(false)
  const [watching, setWatching] = useState(false)
  const [seen, setSeen] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<'ended' | 'failed'>()
  const finishRef = useRef(onFinished)
  finishRef.current = onFinished
  const reason = connection !== 'ready' ? text.disconnected
    : !current || current.status === 'running' || current.status === 'updating' ? text.running : ''

  useEffect(() => { if (running) { setWatching(true); setSeen(true) } }, [running])
  useEffect(() => { onBusyChange?.(starting || watching || running) }, [starting, watching, running, onBusyChange])
  useEffect(() => () => onBusyChange?.(false), [onBusyChange])

  useEffect(() => {
    if (!watching || connection !== 'ready') return
    let active = true
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        // 使用启动之后的新快照；快速结束也能刷新，不依赖观察到 running 事件。
        const list = await api.request('instances.list', {})
        if (!active) return
        const state = list.find(item => item.name === instance)
        if (!state) throw new Error(text.failed)
        if (state.status === 'running') {
          timer = setTimeout(poll, 1000)
          return
        }
        await finishRef.current()
        if (!active) return
        setWatching(false)
        setResult(state.status === 'error' ? 'failed' : 'ended')
        setError('')
      } catch (cause) {
        if (active) { setError((cause as Error).message); timer = setTimeout(poll, 1500) }
      }
    }
    void poll()
    return () => { active = false; clearTimeout(timer) }
  }, [watching, connection, instance, text.failed])

  async function start() {
    // 排空已提交草稿之前先锁住整段 JSON 编辑，避免旧快照覆盖扫描新增的物品。
    onBusyChange?.(true)
    setStarting(true); setError(''); setResult(undefined)
    try {
      await beforeStart()
      await api.request('tasks.run', {instance, task: SEASON_SCAN_TASK})
      setSeen(true); setWatching(true)
    } catch (cause) { setError((cause as Error).message); onBusyChange?.(false) }
    finally { setStarting(false) }
  }

  return <section className="panel tool-log-panel" aria-label={text.read}>
    <div className="panel-heading"><div><ScanLine size={18}/><h2>{text.read}</h2></div>
      <button className="button primary" disabled={starting || watching || !!reason} title={reason || undefined} onClick={start}>
        <ScanLine size={16}/>{starting || watching ? text.reading : text.read}
      </button></div>
    <div className="field-row"><p role="status">{watching ? text.reading : result ? text[result] : reason || text.idle}</p></div>
    {error && <ErrorBox message={error}/>}
    {seen && <><div className="panel-heading"><h2>{ui('monitor.logs')}</h2></div><LogPanel/></>}
  </section>
}
