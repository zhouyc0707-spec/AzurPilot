import {useEffect, useState} from 'react'
import {Link, useParams} from 'react-router-dom'
import {api} from '../api/client'
import {useApp, useConnection} from '../app/context'
import {editor} from '../config/editors'
import {ErrorBox, Loading, PageTitle} from '../components/ui'

type Row = {level?: number; affinity?: number; completed_for?: Record<string, number>}
const groups = {level: 'FleetLevelTraining', affinity: 'FleetAffinityTraining'}

export function FleetTraining({metric}: {metric: keyof typeof groups}) {
  const {instance = ''} = useParams(), {ui, notify, instances} = useApp(), connection = useConnection()
  const group = groups[metric], isLevel = metric === 'level'
  const [enabled, setEnabled] = useState(false), [target, setTarget] = useState(isLevel ? 120 : 100)
  const [interval, setMinutes] = useState(30), [loaded, setLoaded] = useState(false)
  const [records, setRecords] = useState<Record<string, Row>>({})
  const [busy, setBusy] = useState(false), [dirty, setDirty] = useState(false), [error, setError] = useState('')
  const disabled = busy || connection !== 'ready', running = instances.some(item => item.name === instance && item.status === 'running')
  useEffect(() => {
    if (connection !== 'ready') return
    let active = true
    const read = async (initial: boolean) => {
      try {
        const config = await api.request('config.get', {instance})
        if (!active) return
        const values = config.values.FleetInfo
        if (initial) {
          setEnabled(values[group]?.Enabled === true); setTarget(Number(values[group]?.Target ?? (isLevel ? 120 : 100)))
          setMinutes(Number(values[group]?.CheckInterval ?? 30)); setLoaded(true); setDirty(false)
        }
        setRecords((values.FleetTraining?.State as {records?: Record<string, Row>} | undefined)?.records ?? {})
        setError('')
      } catch (error) {if (active) setError((error as Error).message)}
    }
    void read(true)
    return () => {active = false}
  }, [instance, connection, group, isLevel])
  useEffect(() => {
    if (connection !== 'ready' || !running) return
    let active = true
    const timer = setInterval(() => {
      void api.request('config.get', {instance}).then(config => {
        if (active) setRecords((config.values.FleetInfo.FleetTraining?.State as {records?: Record<string, Row>} | undefined)?.records ?? {})
      }).catch(error => {if (active) setError((error as Error).message)})
    }, 3000)
    return () => {active = false; clearInterval(timer)}
  }, [instance, connection, running])

  async function save(nextEnabled = enabled) {
    setBusy(true); setError('')
    try {
      await editor(`config:${instance}`).settled()
      const config = await api.request('config.get', {instance})
      await api.request('config.patch', {instance, revision: config.revision, changes: [
        {path: `FleetInfo.${group}.Enabled`, value: nextEnabled},
        {path: `FleetInfo.${group}.Target`, value: target},
        {path: `FleetInfo.${group}.CheckInterval`, value: interval},
      ]})
      setEnabled(nextEnabled); setDirty(false); notify(ui('training.saved'))
    } catch (error) {setError((error as Error).message)} finally {setBusy(false)}
  }
  return <>
    <PageTitle title={ui(isLevel ? 'training.levelTitle' : 'training.affinityTitle')} actions={<>
      <span>{ui(isLevel ? 'training.levelEnable' : 'training.affinityEnable')}</span>
      <button type="button" className={`toggle ${enabled ? 'on' : ''}`} role="switch" aria-label={ui(isLevel ? 'training.levelEnable' : 'training.affinityEnable')} aria-checked={enabled} disabled={disabled || !loaded} onClick={() => void save(!enabled)}><span/></button>
      <button className="button primary" disabled={disabled || !dirty} onClick={() => void save()}>{ui('rotation.save')}</button>
    </>}/>
    <p className="muted">{ui('training.help')}</p>
    <Link to={`/i/${instance}/task/FleetRotation`}>{ui('training.slotConfig')}</Link>
    {error && <ErrorBox message={error}/>}
    {!loaded ? !error && <Loading/> : <>
      <section className="panel" style={{padding: 20, margin: '20px 0'}}>
        <label style={{display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap'}}>{ui(isLevel ? 'training.targetLevel' : 'training.targetAffinity')}
          <input type="number" min={1} max={isLevel ? 125 : 200} step={1} value={target} disabled={disabled} onChange={event => {setTarget(Math.max(1, Math.min(isLevel ? 125 : 200, Math.trunc(Number(event.target.value))))); setDirty(true)}}/>
        </label>
        <label style={{display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginTop: 16}}>{ui('training.interval')}
          <input type="number" min={0} max={1440} step={1} value={interval} disabled={disabled} onChange={event => {setMinutes(Math.max(0, Math.min(1440, Math.trunc(Number(event.target.value))))); setDirty(true)}}/>
        </label>
      </section>
      <section className="panel" style={{padding: 20, overflowX: 'auto'}}><h2>{ui('training.records')}</h2>
        {!Object.keys(records).length ? <p className="muted">{ui('training.empty')}</p> : <table className="data-table">
          <thead><tr><th>{ui('training.ship')}</th><th>{ui('training.level')}</th><th>{ui('training.affinity')}</th><th>{ui('training.status')}</th></tr></thead>
          <tbody>{Object.entries(records).map(([name, row]) => <tr key={name}><td>{name}</td><td>{row.level ?? '—'}</td><td>{row.affinity ?? '—'}</td><td>{ui(Object.keys(row.completed_for ?? {}).length ? 'training.completed' : 'training.pending')}</td></tr>)}</tbody>
        </table>}
      </section>
    </>}
  </>
}
