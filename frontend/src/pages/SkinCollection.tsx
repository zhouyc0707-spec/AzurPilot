/** 内置离线收藏页；沿用原页面的数据格式、导入导出与查看状态。 */
import {useLayoutEffect, useRef} from 'react'
import {useApp} from '../app/context'
import './skin-collection.css'

export function SkinCollection() {
  const {ui} = useApp()
  const frame = useRef<HTMLIFrameElement>(null)
  useLayoutEffect(() => {
    const element = frame.current
    // Activity 重新显示同一 iframe 时不会重新加载文档，需要显式恢复查看位置。
    element?.contentWindow?.dispatchEvent(new Event('azurpilot:collection-restore'))
    // Activity 隐藏或切换实例销毁 iframe 前，同步落盘尚未完成的防抖保存。
    return () => { element?.contentWindow?.dispatchEvent(new Event('azurpilot:collection-save')) }
  }, [])

  return <section className="skin-collection-page" aria-label={ui('skinCollection.name')}>
    <p className="skin-collection-hint">{ui('skinCollection.storageHint')}</p>
    <iframe ref={frame} className="skin-collection-frame" title={ui('skinCollection.name')}
      src={`${import.meta.env.BASE_URL}skin-collection/index.html`}/>
  </section>
}
