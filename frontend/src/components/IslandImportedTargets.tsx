import {useApp} from '../app/context'
import type {Value} from '../api/types'
import {prepareValue} from '../config/editors'
import {FieldInput} from './FieldInput'
import {islandSeasonText} from './islandSeasonText'
import {IslandTargetProgress, type StockObservation, type StockRecordStatus} from './IslandTargetProgress'

const quantity = {type: 'input', value: 0, valuetype: 'int', validate: [0, Number.MAX_SAFE_INTEGER]}

/** 字符串值仅用于保留尚未通过校验的输入草稿，不会发给后端。 */
export function parseImportedTargetRows(value: Value): Record<string, number | string> {
  const parsed: unknown = JSON.parse(String(value || '{}'))
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('mapping')
  for (const [key, count] of Object.entries(parsed)) {
    if (!/^.+\s\([1-9]\d*\)$/.test(key) || !['number', 'string'].includes(typeof count)) throw new Error('item')
  }
  return parsed as Record<string, number | string>
}

export function validImportedTargetCount(value: number | string) {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
}

export function IslandImportedTargets({id, value, disabled, onChange, observations, progressStatus}: {
  id: string; value: Value; disabled?: boolean; onChange: (value: string, error?: string) => void
  observations?: Record<number, StockObservation>; progressStatus?: StockRecordStatus
}) {
  const {language} = useApp()
  const text = islandSeasonText(language)
  let rows: Record<string, number | string>
  try { rows = parseImportedTargetRows(value) } catch {
    return <div className="field-row"><div className="field-label"><p role="alert">{text.malformed}</p></div>
      <div className="field-control"><FieldInput id={id} type="textarea" value={value} disabled={disabled}
        label={text.malformed} invalid onChange={next => {
          if (disabled) return
          try {
            const parsed = parseImportedTargetRows(next)
            onChange(String(next), Object.values(parsed).every(validImportedTargetCount) ? undefined : text.invalid)
          } catch { onChange(String(next), text.malformed) }
        }}/></div></div>
  }
  if (!Object.keys(rows).length) return <div className="field-row"><p className="muted">{text.empty}</p></div>
  return <>{Object.entries(rows).map(([key, count]) => {
    const itemId = Number(key.match(/\((\d+)\)$/)![1])
    const fieldId = `${id}.${itemId}`
    const name = key.replace(/\s+\(\d+\)$/, '')
    return <div className="field-row" key={key}><div className="field-label"><label htmlFor={fieldId}>{name}</label></div>
      <div className="field-control"><FieldInput id={fieldId} value={count} label={name} type="int" preserveText
        disabled={disabled} invalid={!validImportedTargetCount(count)} onChange={next => {
          if (disabled) return
          const result = prepareValue(next, quantity)
          const updated = {...rows, [key]: result.payload as number | string}
          onChange(JSON.stringify(updated), result.error ?? (Object.values(updated).every(validImportedTargetCount) ? undefined : text.invalid))
        }}/>{progressStatus && <IslandTargetProgress target={count} status={progressStatus} observation={observations?.[itemId]}/>}</div></div>
  })}</>
}
