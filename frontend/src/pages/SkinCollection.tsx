/** 内置离线收藏页；沿用原页面的数据格式、导入导出与查看状态。 */
import {useEffect, useLayoutEffect, useRef, useState} from 'react'
import {useApp, useConnection} from '../app/context'
import {api} from '../api/client'
import type {SkinCollectionEntry} from '../api/types'
import './skin-collection.css'

export function SkinCollection() {
  const {ui} = useApp()
  const connection = useConnection()
  const frame = useRef<HTMLIFrameElement>(null)
  const [frameRevision, setFrameRevision] = useState(0)
  const [syncMessage, setSyncMessage] = useState('')
  useLayoutEffect(() => {
    const element = frame.current
    // Activity 重新显示同一 iframe 时不会重新加载文档，需要显式恢复查看位置。
    element?.contentWindow?.dispatchEvent(new Event('azurpilot:collection-restore'))
    // Activity 隐藏或切换实例销毁 iframe 前，同步落盘尚未完成的防抖保存。
    return () => { element?.contentWindow?.dispatchEvent(new Event('azurpilot:collection-save')) }
  }, [])

  useEffect(() => {
    if (connection !== 'ready' || !frameRevision) return
    let active = true
    let timer: number
    async function sync() {
      let delay = 5 * 60 * 1000
      try {
        const updates = await api.request('skinCollection.updates', {})
        if (!active) return
        const tracker = frame.current?.contentWindow as (Window & {
          syncAnnouncementCollection?: (entries: SkinCollectionEntry[]) => Promise<{characters: number; skins: number}>
        }) | null
        if (!tracker?.syncAnnouncementCollection) throw new Error('收藏页面尚未就绪')
        const added = await tracker.syncAnnouncementCollection(updates.entries)
        if (!active) return
        if (added.characters || added.skins) {
          setSyncMessage(ui('skinCollection.added', added))
        } else if (updates.refreshing) {
          setSyncMessage(ui('skinCollection.checking'))
        } else if (updates.error) {
          setSyncMessage(ui('skinCollection.syncFailed'))
        } else if (updates.pendingNames.length) {
          setSyncMessage(ui('skinCollection.pending', {count: updates.pendingNames.length}))
        } else {
          setSyncMessage(ui('skinCollection.synced'))
        }
        if (updates.refreshing) delay = 5000
      } catch {
        if (active) setSyncMessage(ui('skinCollection.syncFailed'))
      } finally {
        if (active) timer = window.setTimeout(() => void sync(), delay)
      }
    }
    setSyncMessage(ui('skinCollection.checking'))
    void sync()
    return () => { active = false; window.clearTimeout(timer) }
  }, [connection, frameRevision, ui])

  return <section className="skin-collection-page" aria-label={ui('skinCollection.name')}>
    <p className="skin-collection-hint">{ui('skinCollection.storageHint')}</p>
    <p className="skin-collection-hint" role="status" data-testid="collection-sync-status">{syncMessage}</p>
    <iframe ref={frame} className="skin-collection-frame" title={ui('skinCollection.name')}
      onLoad={() => setFrameRevision(value => value + 1)} src={`${import.meta.env.BASE_URL}skin-collection/index.html`}/>
  </section>
}
