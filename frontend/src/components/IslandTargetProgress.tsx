import type {Value} from '../api/types'
import {useApp} from '../app/context'
import {islandProgressText} from './islandProgressText'
import {localeForLanguage} from '../i18n'
import './island-target-progress.css'

export type StockObservation = {stock: number | null; inProduction: number | null}
export type StockRecordStatus = 'missing' | 'observed' | 'invalidated' | 'loading' | 'unavailable'

const knownQuantity = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0

/** 库存才计入完成量；失效记录只展示历史值，不把在制或未知量当成库存。 */
export function IslandTargetProgress({target, observation, status}: {
  target: Value; observation?: StockObservation; status: StockRecordStatus
}) {
  const {language} = useApp()
  const text = islandProgressText(language)
  const value = typeof target === 'number' ? target : typeof target === 'string' && /^\d+$/.test(target) ? Number(target) : NaN
  const validTarget = Number.isSafeInteger(value) && value >= 0
  const stock = observation?.stock
  const inProduction = observation?.inProduction
  const observed = status === 'observed'
  const complete = validTarget && value > 0 && observed && knownQuantity(stock)
  const summary = complete ? `${text.completed} ${Math.min(stock, value)}/${value}`
    : status === 'observed' ? text.unknownStock : status === 'invalidated' ? text.invalidated : text[status]

  return <div className="island-target-progress" aria-label={text.title}>
    {!validTarget || value === 0 ? <strong>{validTarget ? text.unset : text.invalidTarget}</strong> : null}
    {(complete || !knownQuantity(stock) || !observed) && <span>{summary}</span>}
    {complete && <progress aria-label={text.title} max={value} value={Math.min(stock, value)}/>}
    <div className="island-target-progress-counts">
      {knownQuantity(stock) && (status === 'invalidated' || observed && (!complete || stock > value)) &&
        <span>{status === 'invalidated' ? text.historical : text.stock}：{stock}</span>}
      {(observed || status === 'invalidated') && <span>{knownQuantity(inProduction)
        ? `${status === 'invalidated' ? text.historicalProduction : text.inProduction}：${inProduction}` : text.unknownProduction}</span>}
    </div>
  </div>
}

export function IslandProgressSummary({status, updatedAt}: {status: StockRecordStatus; updatedAt?: string | null}) {
  const {language} = useApp()
  const text = islandProgressText(language)
  // 无时区旧值原样展示，避免浏览器将服务器本地时间误换算。
  const date = updatedAt && /(?:Z|[+-]\d{2}:\d{2})$/.test(updatedAt) ? new Date(updatedAt) : undefined
  const time = date && Number.isFinite(date.getTime()) ? date.toLocaleString(localeForLanguage(language), {hour12: false}) : updatedAt
  return <section className="panel island-progress-summary" aria-label={text.title}>
    <strong>{text.title}</strong>
    <p>{text.help}</p>
    {status !== 'observed' && <p>{text[status]}</p>}
    {updatedAt && <p>{text.updatedAt}：<time dateTime={updatedAt}>{time}</time></p>}
  </section>
}
