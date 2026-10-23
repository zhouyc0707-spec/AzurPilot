import { useEffect, useState } from 'react'
import { Cat } from 'lucide-react'
import { api } from '../api/client'
import type { AkashiShopRecordResult } from '../api/types'
import { useConnection } from '../app/context'
import { ErrorBox, Loading, Modal } from './ui'
import '../styles/akashi-shop-records.css'

type Scope = AkashiShopRecordResult['scope']
const TITLES: Record<Scope, string> = {monthly: '本月猫王', history: '历史猫王'}

/** 时间按记录自身的时区显示，避免远程浏览器时区改变纪录日期。 */
function recordTime(value: string): string {
  const matched = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})?$/.exec(value)
  if (!matched) return value
  const zone = matched[3] === 'Z' ? 'UTC' : matched[3] ? `UTC${matched[3]}` : ''
  return `${matched[1]} ${matched[2]}${zone ? `（${zone}）` : ''}`
}

/** 纪录来自进店时全部在售箱子；不把购买结果或当前背包库存混入。 */
export function AkashiShopRecordView({data}: {data: AkashiShopRecordResult}) {
  const record = data.record
  if (!record) return <p className="legacy-muted akashi-record-empty">
    {data.scope === 'monthly' ? `${data.month} 暂无明石商店纪录。` : '暂无历史明石商店纪录。'}
    正常进入明石商店并完整识别商品后，会自动记录。
  </p>
  const boxes = Object.entries(record.boxes).filter(([, count]) => count > 0)
    .sort(([left], [right]) => Number(left) - Number(right))
  return <div className="akashi-shop-record">
    <div className="akashi-record-summary">
      <div><span>在售行动力</span><strong>{record.totalAp.toLocaleString()}<small> 点</small></strong></div>
      <dl>
        <div><dt>记录时间</dt><dd>{recordTime(record.time)}</dd></div>
        <div><dt>记录月份</dt><dd>{record.month}</dd></div>
      </dl>
    </div>
    <p className="akashi-record-boxes">{boxes.map(([points, count]) =>
      <span key={points}>{points} 点行动力箱 × {count}</span>)}</p>
    <p className="legacy-muted akashi-record-note">按购买前商店全部在售行动力计算；平纪录时保留最新遇到的商店。</p>
    {record.image
      ? <img className="akashi-record-image" src={record.image}
        alt={`${TITLES[data.scope]}完整商店截图，${record.totalAp} 点行动力，${recordTime(record.time)}`}/>
      : <p role="status" className="legacy-muted">纪录已保留，但截图文件暂不可用。</p>}
  </div>
}

function RecordButtons({instance}: {instance: string}) {
  const connection = useConnection()
  const [scope, setScope] = useState<Scope | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [data, setData] = useState<AkashiShopRecordResult>()
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!scope) return
    let current = true
    setData(undefined)
    setError('')
    if (connection !== 'ready') {
      setLoading(false)
      setError('连接已断开，恢复连接后会重新读取商店纪录。')
      return
    }
    setLoading(true)
    void api.request('statistics.akashiRecord', {instance, scope}).then(result => {
      if (!current) return
      if (result.instance !== instance || result.scope !== scope) {
        setError('商店纪录与当前实例不一致，请重试。')
        return
      }
      setData(result)
    }).catch((reason: unknown) => {
      if (current) setError(reason instanceof Error ? reason.message : '商店纪录读取失败，请重试。')
    }).finally(() => { if (current) setLoading(false) })
    // 关闭、切实例或重试后，迟到响应不能恢复旧截图。
    return () => { current = false }
  }, [instance, scope, attempt, connection])

  function open(next: Scope) {
    setData(undefined)
    setError('')
    setLoading(true)
    setScope(next)
  }

  return <>
    <div className="akashi-record-actions">
      {(['monthly', 'history'] as const).map(item => <button key={item} type="button" className="legacy-button"
        onClick={() => open(item)}><Cat size={14} aria-hidden="true"/>{TITLES[item]}</button>)}
    </div>
    {scope && <Modal title={`${TITLES[scope]} · ${instance}`} onClose={() => setScope(null)} className="akashi-record-modal">
      {loading && <Loading/>}
      {error && <ErrorBox message={error} retry={() => setAttempt(value => value + 1)}/>}
      {data && !error && <AkashiShopRecordView data={data}/>}
    </Modal>}
  </>
}

/** 实例改变时立即卸载旧弹窗，避免首帧或迟到响应显示另一实例的纪录。 */
export function AkashiShopRecords({instance}: {instance: string}) {
  return <RecordButtons key={instance} instance={instance}/>
}
