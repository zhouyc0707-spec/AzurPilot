import {useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent} from 'react'
import {useParams} from 'react-router-dom'
import {Calculator, Download, Plus, RefreshCw, Save, ScanLine, Trash2, Upload} from 'lucide-react'
import {api} from '../api/client'
import {useApp, useConnection} from '../app/context'
import {Checkbox, Select} from '../components/FormControls'
import {Empty, ErrorBox, Loading, PageTitle} from '../components/ui'
import {editableShip, type MindCalculation, type MindCatalog, type MindReport, type MindShip, type Rarity} from '../mind/types'
import '../mind/mind.css'

const rarities: Rarity[] = ['UR', 'SSR', 'SR', 'R', 'N']
const bands = ['≤100', '101–105', '106–110', '111–115', '116–119', '≥120']
const number = (value: number) => value.toLocaleString()
function fileContent(file: File) {
  if (file.size > 5_500_000) throw new Error('文件超过 5.5 MB 限制')
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',')[1])
    reader.onerror = () => reject(new Error('无法读取文件'))
    reader.readAsDataURL(file)
  })
}
function download(filename: string, content: string) {
  const url = URL.createObjectURL(new Blob([Uint8Array.from(atob(content), char => char.charCodeAt(0))]))
  const anchor = document.createElement('a')
  anchor.href = url; anchor.download = filename; anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function MindCalculator() {
  const {instance = ''} = useParams(), {ui, instances, notify} = useApp(), connection = useConnection()
  const [catalog, setCatalog] = useState<MindCatalog>(), [saved, setSaved] = useState<MindReport>()
  const [ships, setShips] = useState<MindShip[]>([]), [result, setResult] = useState<MindCalculation>()
  const [dirty, setDirty] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('')
  const [query, setQuery] = useState(''), [filter, setFilter] = useState('all'), [page, setPage] = useState(0)
  const [name, setName] = useState(''), [level, setLevel] = useState('100'), [rarity, setRarity] = useState<Rarity>('SSR')
  const [exportFormat, setExportFormat] = useState<'xlsx' | 'csv' | 'json'>('xlsx')
  const dirtyRef = useRef(false), savedRef = useRef<MindReport | undefined>(undefined), requestVersion = useRef(0), alive = useRef(true)
  const draftKey = `azurpilot.mind-draft.${instance}`
  const current = instances.find(item => item.name === instance)
  const running = current?.status === 'running'
  const scanning = running && current?.currentTask === 'MindCalculatorScan'
  const ready = connection === 'ready' && !!saved
  const scanReason = connection !== 'ready' ? ui('mind.scanDisconnected')
    : !saved || !current ? ui('mind.scanLoading')
    : busy ? ui('mind.processing')
    : scanning ? ui('mind.scanInProgress')
    : running ? ui('mind.scanRunning')
    : current.status === 'updating' ? ui('mind.scanUpdating')
    : current.region && current.region !== 'cn' ? ui('mind.scanUnsupported')
    : dirty ? ui('mind.scanDraft') : ''
  function accept(report: MindReport) {
    savedRef.current = report
    setSaved(report); setShips(report.ships.map(editableShip)); setResult(report)
    dirtyRef.current = false; setDirty(false); setPage(0)
    try {sessionStorage.removeItem(draftKey)} catch { /* 浏览器禁用存储时仍可编辑。 */ }
  }
  useEffect(() => {
    alive.current = true
    return () => {alive.current = false}
  }, [])
  useEffect(() => {
    if (connection !== 'ready') return
    let active = true
    Promise.all([api.request('mind.catalog', {instance}), api.request('mind.report', {instance})])
      .then(([data, report]) => {if (active) {
        setCatalog(data)
        if (!savedRef.current) {
          try {
            const draft = JSON.parse(sessionStorage.getItem(draftKey) ?? 'null') as {revision: string; ships: MindShip[]} | null
            if (draft && typeof draft.revision === 'string' && Array.isArray(draft.ships) && draft.ships.length <= 5000 &&
              draft.ships.every(ship => ship && typeof ship.name === 'string' && Number.isInteger(ship.level) && ship.level >= 0 && ship.level <= 125)) {
              const recovered = {...report, revision: draft.revision}
              savedRef.current = recovered; setSaved(recovered); setShips(draft.ships.map(editableShip))
              dirtyRef.current = true; setDirty(true); return
            }
          } catch { /* 不读取损坏的草稿。 */ }
        }
        if (!dirtyRef.current) accept(report)
      }})
      .catch(error => {if (active) setError(error.message)})
    const timer = setInterval(() => {
      if (dirtyRef.current) return
      void api.request('mind.report', {instance}).then(report => {
        if (active && !dirtyRef.current) {
          if (savedRef.current?.revision !== report.revision) {
            savedRef.current = report; setSaved(report)
            setShips(report.ships.map(editableShip)); setResult(report)
          }
        }
      }).catch(error => {if (active) setError(error.message)})
    }, 5000)
    return () => {active = false; clearInterval(timer)}
  }, [instance, connection])
  useEffect(() => {
    if (!dirty || connection !== 'ready') return
    const version = ++requestVersion.current
    const timer = setTimeout(() => {
      void api.request('mind.calculate', {instance, ships}).then(value => {
        if (alive.current && dirtyRef.current && requestVersion.current === version) setResult(value)
      }).catch(error => {if (alive.current && requestVersion.current === version) setError(error.message)})
    }, 250)
    return () => {clearTimeout(timer); requestVersion.current++}
  }, [ships, dirty, connection, instance])
  function edit(next: MindShip[]) {
    setShips(next); setDirty(true); dirtyRef.current = true; setError('')
    try {sessionStorage.setItem(draftKey, JSON.stringify({revision: savedRef.current?.revision, ships: next}))} catch { /* 容量不足不阻止编辑。 */ }
  }
  function change(index: number, patch: Partial<MindShip>) {
    edit(ships.map((ship, i) => i === index ? {...ship, ...patch} : ship))
  }
  async function action(work: () => Promise<void>) {
    setBusy(true); setError('')
    try {await work()} catch (error) {if (alive.current) setError((error as Error).message)}
    finally {if (alive.current) setBusy(false)}
  }
  async function upload(event: ChangeEvent<HTMLInputElement>, screenshots: boolean) {
    const files = Array.from(event.target.files ?? [])
    event.target.value = ''
    if (!files.length) return
    await action(async () => {
      const added: MindShip[] = []
      for (const file of files) {
        const response = await api.request(screenshots ? 'mind.recognize' : 'mind.import', {instance, filename: file.name, content: await fileContent(file)})
        added.push(...response.ships.map(editableShip))
      }
      if (!alive.current) return
      const next = [...ships, ...added]
      if (next.length > 5000) throw new Error(ui('mind.tooMany'))
      edit(next); setFilter(screenshots ? 'review' : 'all'); setPage(0)
    })
  }
  function add(event: FormEvent) {
    event.preventDefault()
    const info = catalog?.ships.find(ship => ship.name === name.trim())
    edit([...ships, editableShip({name: name.trim(), level: Number(level), rarity: info?.rarity ?? rarity, base_rarity: info?.base_rarity ?? rarity})])
    setName(''); setFilter('all'); setPage(Math.floor(ships.length / 50))
  }
  const rows = useMemo(() => ships.map((ship, index) => ({ship, index, row: result?.ships[index]})).filter(({ship, row}) =>
    ship.name.toLocaleLowerCase().includes(query.toLocaleLowerCase()) && (filter === 'all' || row?.status === filter)), [ships, result, filter, query])
  const pages = Math.max(1, Math.ceil(rows.length / 50))
  const currentPage = Math.min(page, pages - 1)
  const visible = rows.slice(currentPage * 50, (currentPage + 1) * 50)
  const labels = (value: Rarity) => ui(`mind.rarity.${value}`)
  return <div className="mind-calculator">
    <PageTitle title={ui('mind.title')} actions={<>
      <button className="button secondary" disabled={busy || !ready} onClick={() => action(async () => {accept(await api.request('mind.report', {instance}))})}><RefreshCw size={15}/>{ui(dirty ? 'mind.discard' : 'mind.reload')}</button>
      <button className="button primary" disabled={!dirty || busy || !ready || running} onClick={() => action(async () => {
        const report = await api.request('mind.save', {instance, revision: saved!.revision, ships})
        if (alive.current) {accept(report); notify(ui('mind.saved'))}
      })}><Save size={15}/>{ui('mind.save')}</button>
    </>}/>
    <section className="mind-panel mind-intro"><Calculator size={26}/><div><p>{ui('mind.hint')}</p><small className="muted">{ui('mind.catalogDate', {date: catalog?.updated_at.split('T')[0] ?? '—'})} · {ui(dirty ? 'mind.unsaved' : 'mind.savedState')}</small></div></section>
    {error && <ErrorBox message={error}/>}
    {!saved && !error && <Loading/>}
    {saved && <>
      <section className="mind-panel mind-toolbar" aria-busy={busy}>
        <button className="button secondary" disabled={!!scanReason} title={scanReason || undefined} aria-describedby={scanReason ? 'mind-scan-reason' : 'mind-scan-hint'} onClick={() => action(async () => {await api.request('tasks.run', {instance, task: 'MindCalculatorScan'}); notify(ui('mind.scanStarted'))})}><ScanLine size={16}/>{ui(scanning ? 'mind.scanning' : 'mind.scan')}</button>
        {scanning && <button className="button secondary" disabled={busy} onClick={() => action(async () => {await api.request('scheduler.stop', {instance})})}>{ui('mind.stop')}</button>}
        <label className={`button secondary file-button${busy || !ready ? ' mind-disabled' : ''}`}><Upload size={16}/>{ui('mind.import')}<input type="file" accept=".json,.csv,.xlsx" disabled={busy || !ready} onChange={event => upload(event, false)}/></label>
        <label className={`button secondary file-button${busy || !ready ? ' mind-disabled' : ''}`}><ScanLine size={16}/>{ui('mind.screenshots')}<input type="file" multiple accept="image/png,image/jpeg" disabled={busy || !ready} onChange={event => upload(event, true)}/></label>
        <div className="mind-export"><Select aria-label={ui('mind.exportFormat')} value={exportFormat} onChange={event => setExportFormat(event.target.value as typeof exportFormat)}><option value="xlsx">Excel</option><option value="csv">CSV</option><option value="json">JSON</option></Select><button className="button secondary" disabled={busy || dirty || !ready} onClick={() => action(async () => {const file = await api.request('mind.export', {instance, format: exportFormat}); download(file.filename, file.content)})}><Download size={16}/>{ui('mind.export')}</button></div>
        {scanReason && <p id="mind-scan-reason" role="status"><strong>{scanReason}</strong></p>}
        <p id="mind-scan-hint" className="muted">{ui('mind.scanHint')}</p>
      </section>
      <div className="mind-totals" aria-live="polite">
        <div className="mind-panel"><span>{ui('mind.mind')}</span><strong>{number(result?.mind ?? 0)}</strong></div>
        <div className="mind-panel"><span>{ui('mind.gold')}</span><strong>{number(result?.gold ?? 0)}</strong></div>
        <div className="mind-panel"><span>{ui('mind.included')}</span><strong>{number(result?.included ?? 0)}</strong><small>{ui('mind.mergedCount', {count: result?.merged ?? 0})}</small></div>
        <button className="mind-panel" onClick={() => {setFilter('review'); setPage(0)}}><span>{ui('mind.review')}</span><strong>{number(result?.review ?? 0)}</strong><small>{ui('mind.reviewHint')}</small></button>
      </div>
      <section className="mind-panel"><h2>{ui('mind.summary')}</h2><div className="mind-table-wrap"><table><thead><tr><th>{ui('mind.rarity')}</th>{bands.map(band => <th key={band}>{band}</th>)}<th>{ui('mind.mind')}</th><th>{ui('mind.gold')}</th></tr></thead><tbody>{result?.summary.map(row => <tr key={row.rarity}><th><span className={`mind-rarity rarity-${row.rarity}`}>{labels(row.rarity)}</span></th>{row.stages.map((count, i) => <td key={i}>{number(count)}</td>)}<td>{number(row.mind)}</td><td>{number(row.gold)}</td></tr>)}</tbody></table></div></section>
      <section className="mind-panel">
        <h2>{ui('mind.ships')}</h2>
        <form className="mind-add" onSubmit={add}>
          <label>{ui('mind.name')}<input list="mind-ship-catalog" required maxLength={100} value={name} onChange={event => setName(event.target.value)}/></label>
          <datalist id="mind-ship-catalog">{catalog?.ships.map(ship => <option key={ship.name} value={ship.name}>{labels(ship.base_rarity)}</option>)}</datalist>
          <label>{ui('mind.level')}<input type="number" required min={1} max={125} step={1} value={level} onChange={event => setLevel(event.target.value)}/></label>
          <label>{ui('mind.baseRarity')}<Select value={rarity} onChange={event => setRarity(event.target.value as Rarity)}>{rarities.map(r => <option key={r} value={r}>{labels(r)}</option>)}</Select></label>
          <button className="button secondary" disabled={busy || !ready || ships.length >= 5000}><Plus size={16}/>{ui('mind.add')}</button>
        </form>
        <div className="mind-filters"><input aria-label={ui('mind.search')} placeholder={ui('mind.search')} value={query} onChange={event => {setQuery(event.target.value); setPage(0)}}/><Select aria-label={ui('mind.filter')} value={filter} onChange={event => {setFilter(event.target.value); setPage(0)}}>{(['all', 'included', 'review', 'merged', 'excluded'] as const).map(status => <option key={status} value={status}>{ui(`mind.${status}`)}</option>)}</Select><span className="muted">{ui('mind.rowCount', {count: rows.length})}</span></div>
        {visible.length ? <div className="mind-table-wrap"><table className="mind-ship-table"><thead><tr><th>{ui('mind.name')}</th><th>{ui('mind.level')}</th><th>{ui('mind.baseRarity')}</th><th>{ui('mind.status')}</th><th>{ui('mind.mind')}</th><th>{ui('mind.actions')}</th></tr></thead><tbody>{visible.map(({ship, index, row}) => <tr key={index}>
          <td><input aria-label={`${ui('mind.name')} ${index + 1}`} list="mind-ship-catalog" value={ship.name} disabled={busy} maxLength={100} onChange={event => change(index, {name: event.target.value})}/><small className="muted" title={ship.source}>{ship.source}</small></td>
          <td><input aria-label={`${ui('mind.level')} ${index + 1}`} type="number" min={0} max={125} step={1} value={ship.level} disabled={busy} onChange={event => {const value = Number(event.target.value); if (Number.isInteger(value) && value >= 0 && value <= 125) change(index, {level: value})}}/></td>
          <td><Select aria-label={`${ui('mind.baseRarity')} ${index + 1}`} value={row?.base_rarity || ship.base_rarity || ''} disabled={busy || catalog?.ships.some(info => info.name === ship.name)} onChange={event => change(index, {base_rarity: event.target.value as Rarity})}><option value="">—</option>{rarities.map(r => <option key={r} value={r}>{labels(r)}</option>)}</Select></td>
          <td><span className={`mind-status status-${row?.status}`}>{ui(`mind.${row?.status ?? 'review'}`)}</span></td><td>{number(row?.mind ?? 0)}</td>
          <td><div className="mind-row-actions">{ship.review && <button className="button secondary" disabled={busy || !ship.name.trim() || !ship.level || !row?.base_rarity} onClick={() => change(index, {review: false})}>{ui('mind.confirm')}</button>}<Checkbox checked={ship.excluded ?? false} disabled={busy} onChange={event => change(index, {excluded: event.target.checked})}>{ui('mind.exclude')}</Checkbox><button className="icon-button" aria-label={`${ui('mind.delete')} ${index + 1}`} disabled={busy} onClick={() => edit(ships.filter((_, i) => i !== index))}><Trash2 size={15}/></button></div></td>
        </tr>)}</tbody></table></div> : <Empty title={ui('mind.empty')}>{ui('mind.emptyHint')}</Empty>}
        <div className="mind-pagination"><button className="button secondary" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>{ui('mind.previous')}</button><span>{currentPage + 1} / {pages}</span><button className="button secondary" disabled={currentPage + 1 >= pages} onClick={() => setPage(currentPage + 1)}>{ui('mind.next')}</button></div>
      </section>
    </>}
  </div>
}
