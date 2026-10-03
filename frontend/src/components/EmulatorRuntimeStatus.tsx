/**
 * @fileoverview 模拟器管理中的最近检测结果与重启预计时间，复用调度器的只读快照。
 */

import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { EmulatorStatus } from '../api/types'
import { useApp, useConnection } from '../app/context'
import { localeForLanguage, translateUi, type Language } from '../i18n'

export function formatEmulatorDuration(seconds: number, language: Language): string {
  const total = Math.max(0, Math.floor(seconds))
  return translateUi(language, 'emulator.duration', {
    hours: Math.floor(total / 3600), minutes: Math.floor(total / 60) % 60, seconds: total % 60,
  })
}

export function EmulatorRuntimeStatusView({status, language, connected, failed = false}: {
  status?: EmulatorStatus; language: Language; connected: boolean; failed?: boolean
}) {
  const ui = (key: Parameters<typeof translateUi>[1], params?: Parameters<typeof translateUi>[2]) => translateUi(language, key, params)
  const formatTime = (seconds: number) => new Date(seconds * 1000).toLocaleString(localeForLanguage(language), {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  })
  const uptime = status?.uptimeSeconds
  let next = ui('emulator.noDetection')
  let hint = ui('emulator.detectHint')
  if (!connected) {
    next = ui('api.disconnected')
  } else if (failed) {
    next = ui('emulator.unavailable')
  } else if (status) {
    hint = status.force ? ui('emulator.forceHint') : ui('emulator.betweenTasks')
    if (!status.scheduled) next = ui('emulator.disabled')
    else if (!status.schedulerRunning) next = ui('emulator.stopped')
    else if (status.lastAttemptAt !== null && !status.available) next = ui('emulator.readFailed')
    else if (status.nextRestartAt !== null) {
      const remaining = Math.ceil(status.nextRestartAt - status.serverTime)
      next = remaining <= 0 ? ui('emulator.due') : formatTime(status.nextRestartAt)
      if (remaining > 0) hint = ui('emulator.remaining', {duration: formatEmulatorDuration(remaining, language)}) + '\n' + hint
    } else hint = ui('emulator.detectHint')
  }
  return <div className="emulator-runtime-status" aria-label={ui('emulator.statusTitle')}>
    <div className="field-row">
      <div className="field-label">
        <span className="field-name">{ui('emulator.uptime')}</span>
        <p>{status?.checkedAt != null ? ui('emulator.checkedAt', {time: formatTime(status.checkedAt)}) : ui('emulator.noDetection')}</p>
        {status?.lastAttemptAt != null && !status.available && uptime != null && <p className="emulator-status-warning">{ui('emulator.readFailedHistory')}</p>}
      </div>
      <div className="field-control emulator-status-value" data-testid="emulator-uptime">
        {uptime != null ? formatEmulatorDuration(uptime, language) : ui('emulator.noDetection')}
      </div>
    </div>
    <div className="field-row">
      <div className="field-label">
        <span className="field-name">{ui('emulator.nextRestart')}</span>
        <p data-testid="emulator-restart-hint">{hint}</p>
      </div>
      <div className="field-control emulator-status-value" data-testid="emulator-next-restart">{next}</div>
    </div>
  </div>
}

export function EmulatorRuntimeStatus({instance}: {instance: string}) {
  const {language} = useApp()
  const connection = useConnection()
  const [status, setStatus] = useState<EmulatorStatus>()
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    if (connection !== 'ready') return
    let active = true
    const accept = (next: EmulatorStatus) => {
      if (active && next.instance === instance) { setStatus(next); setFailed(false) }
    }
    const unsubscribe = api.onEvent(event => {
      if (event.topic === 'emulator') accept(event.data as EmulatorStatus)
      if (event.topic === 'subscription.error') {
        const error = event.data as {topic: string; instance: string}
        if (error.topic === 'emulator' && error.instance === instance) setFailed(true)
      }
    })
    void api.request('emulator.status', {instance}).then(accept).catch(() => { if (active) setFailed(true) })
    return () => { active = false; unsubscribe() }
  }, [instance, connection])
  return <EmulatorRuntimeStatusView status={status?.instance === instance ? status : undefined} language={language} connected={connection === 'ready'} failed={failed}/>
}
