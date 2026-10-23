/**
 * @fileoverview 实例运行总览页面（资源卡、调度器计划与实时日志）。
 */

import { Activity, useEffect, useState, useSyncExternalStore } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { Overview as OverviewData } from '../api/types'
import { useApp, useConnection } from '../app/context'
import { usesLegacyLayout } from '../app/theme'
import { ErrorBox, Loading, PageTitle } from '../components/ui'
import { MonitorPanel } from '../components/MonitorPanel'
import { defaultResourceKeys, ResourceCards } from '../components/ResourceCards'
import { InstanceActions } from '../components/InstanceActions'
import { LegacyRail } from '../components/LegacyRail'
import { MaintenanceWeekday } from '../components/MaintenanceWeekday'
import { Statistics } from './Statistics'
import { readOverviewPanel, setOverviewPanel, subscribeOverviewPanel } from '../app/overviewPanelPrefs'

function loadResourceSelection(instance: string) {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(`azurpilot.resources.${instance}`) ?? 'null')
    if (Array.isArray(saved) && saved.every(item => typeof item === 'string')) return [...new Set(saved)]
  } catch { /* 损坏偏好使用默认搭配。 */ }
  return defaultResourceKeys
}

/** 首次访问才加载面板；隐藏时暂停副作用，往返时保留筛选、图表和滚动状态。 */
function LegacyOverviewPanels({instance, panel}: {instance: string; panel: 'logs' | 'stats'}) {
  const [visited, setVisited] = useState({logs: panel === 'logs', stats: panel === 'stats'})
  if (!visited[panel]) setVisited({...visited, [panel]: true})
  return <>
    {visited.logs && <Activity name="总览日志" mode={panel === 'logs' ? 'visible' : 'hidden'}>
      <div className={`instance-page-panel ${panel === 'logs' ? 'panel-rise' : ''}`}>
        <MonitorPanel instance={instance}/>
      </div>
    </Activity>}
    {visited.stats && <Activity name="总览统计" mode={panel === 'stats' ? 'visible' : 'hidden'}>
      <div className={`instance-page-panel ${panel === 'stats' ? 'panel-drop' : ''}`}>
        <div className="instance-panel-stats"><Statistics embedded/></div>
      </div>
    </Activity>}
  </>
}

export function Overview() {
  const {instance = ''} = useParams()
  const {theme, ui} = useApp()
  const [data, setData] = useState<OverviewData>()
  const [error, setError] = useState('')
  const [selectedResources, setSelectedResources] = useState<string[]>(() => loadResourceSelection(instance))
  const connection = useConnection()
  const panel = useSyncExternalStore(subscribeOverviewPanel, readOverviewPanel, readOverviewPanel)

  useEffect(() => setSelectedResources(loadResourceSelection(instance)), [instance])

  function updateResourceSelection(keys: string[]) {
    const next = [...new Set(keys)]
    setSelectedResources(next)
    try { localStorage.setItem(`azurpilot.resources.${instance}`, JSON.stringify(next)) } catch { /* 无存储权限时仅本页生效。 */ }
  }

  useEffect(() => {
    if (connection !== 'ready') return
    let active = true
    void api.request('overview.get', {instance})
      .then(value => { if (active) {setData(value); setError('')} })
      .catch(error => { if (active) setError(error.message) })
    return () => { active = false }
  }, [instance, connection])

  useEffect(() => api.onEvent(event => {
    if (event.topic === 'overview' && (event.data as OverviewData).instance === instance) setData(event.data as OverviewData)
  }), [instance])

  if (error) return <ErrorBox message={error}/>
  if (!data) return <Loading/>

  // 紧凑主题省略与面包屑重复的标题行，设置按钮改挂日志面板工具栏。
  const condensed = theme === 'extreme'
  const actions = <InstanceActions resources={data.resources} selectedResources={selectedResources} onResourcesChange={updateResourceSelection} showLabel={condensed}/>

  // 旧版版式：左列调度器与任务计划，右列资源卡与日志；右栏在旧版主题下不渲染。
  if (usesLegacyLayout(theme)) return <div className="instance-page-grid">
    <h1 className="legacy-sr-title">{instance}</h1>
    <LegacyRail instance={instance} data={data} onData={setData}>
      {/* 标题即切换主区显示日志还是统计的按钮。 */}
      <section className="panel legacy-stat-card">
        <button type="button" className="legacy-stat-title" aria-pressed={panel === 'stats'}
          aria-label={ui(panel === 'stats' ? 'overview.showLogs' : 'overview.showStats')}
          title={ui(panel === 'stats' ? 'overview.showLogs' : 'overview.showStats')}
          onClick={() => {setOverviewPanel(panel === 'stats' ? 'logs' : 'stats')}}
        >{ui(panel === 'stats' ? 'monitor.logs' : 'overview.statCard')}</button>
        <MaintenanceWeekday instance={instance}/>
        {actions}
      </section>
    </LegacyRail>
    <div className="instance-page-main">
      <ResourceCards resources={data.resources} selected={selectedResources}/>
      <LegacyOverviewPanels key={instance} instance={instance} panel={panel}/>
    </div>
  </div>

  return <div className="overview-page">
    {!condensed && <PageTitle className="instance-page-title" title={instance} actions={actions}/>}
    <ResourceCards resources={data.resources} selected={selectedResources}/>
    <div className="overview-main">
      <MonitorPanel instance={instance} actions={condensed ? actions : undefined}/>
    </div>
  </div>
}
