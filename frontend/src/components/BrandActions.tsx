/** 新外壳沿用本地重启确认及新旧主题快捷入口。 */
import {useState} from 'react'
import {LayoutTemplate, LoaderCircle, Power} from 'lucide-react'
import {useApp, useConnection} from '../app/context'
import {toggleThemeFamily, usesLegacyLayout} from '../app/theme'
import {api} from '../api/client'
import {Modal} from './ui'
export function BrandActions() {
  const {ui, theme, notify} = useApp()
  const connection = useConnection()
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  async function restart() {
    setBusy(true)
    try {await api.request('system.restart', {}); notify(ui('nav.restartSent'))}
    catch (error) {notify((error as Error).message, true)}
    finally {setBusy(false); setConfirm(false)}
  }
  return <span className="brand-quick-actions">
    <button className="icon-button brand-quick-button" aria-label={ui('nav.restartService')} disabled={busy || connection !== 'ready'} onClick={() => setConfirm(true)}>{busy ? <LoaderCircle size={16}/> : <Power size={16}/>}</button>
    <button className="icon-button brand-quick-button" aria-label={ui('nav.toggleTheme')} title={ui(usesLegacyLayout(theme) ? 'nav.toggleThemeToNew' : 'nav.toggleThemeToLegacy')} onClick={toggleThemeFamily}><LayoutTemplate size={16}/></button>
    {confirm && <Modal title={ui('nav.restartConfirmTitle')} onClose={() => setConfirm(false)}><p>{ui('nav.restartConfirmBody')}</p><button className="button secondary" onClick={() => setConfirm(false)}>{ui('common.cancel')}</button><button className="button primary" disabled={busy} onClick={() => void restart()}>{ui('nav.restartConfirmAction')}</button></Modal>}
  </span>
}
