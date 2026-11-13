/** 旧版总览只读展示官方维护公告，跟随实例总览快照更新。 */
import { CircleAlert } from 'lucide-react'
import type { Overview } from '../api/types'
import { useApp } from '../app/context'

export function MaintenanceTime({maintenance}: {maintenance?: Overview['maintenance']}) {
  const {ui} = useApp()
  const startsAt = maintenance?.startsAt && Number.isFinite(Date.parse(maintenance.startsAt))
    ? maintenance.startsAt : null
  const failed = maintenance?.state === 'error' || Boolean(maintenance?.startsAt && !startsAt)
  const label = ui(startsAt && Date.parse(startsAt) <= Date.now()
    ? 'overview.currentMaintenance' : 'overview.nextMaintenance')
  const stateText = ui(failed ? 'overview.maintenanceFailed'
    : !maintenance || maintenance.state === 'pending' ? 'overview.maintenancePending'
      : 'overview.maintenanceUnpublished')
  // 直接显示 ISO 中的区服年月日与时分，避免浏览器按本机时区转换公告时间。
  const value = startsAt ? startsAt.slice(5, 16).replace('T', ' ') : stateText
  const details = [
    `${label} ${startsAt?.replace('T', ' ') ?? stateText}`,
    ui('overview.maintenanceHint'),
    maintenance?.checkedAt && ui('overview.maintenanceChecked', {time: maintenance.checkedAt.replace('T', ' ')}),
    failed && `${ui('overview.maintenanceFailed')}${maintenance?.error ? ` ${maintenance.error}` : ''}`,
  ].filter(Boolean).join('\n')

  return <div className={`maintenance-time${failed ? ' has-error' : ''}`} role="status"
    aria-label={`${label} ${value}${failed && startsAt ? ` ${stateText}` : ''}`} title={details}>
    <span className="maintenance-time-label">{label}</span>
    {startsAt ? <time dateTime={startsAt}>{value}</time> : <span className="maintenance-time-state">{value}</span>}
    {failed && startsAt && <CircleAlert className="maintenance-time-warning" size={12}
      aria-label={ui('overview.maintenanceFailed')}/>}
  </div>
}
