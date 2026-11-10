import {useEffect, useState} from 'react'
import {api} from '../api/client'
import type {IslandSeasonProgress} from '../api/types'
import {useConnection} from '../app/context'
import type {StockRecordStatus} from './IslandTargetProgress'

/** 只读本地快照；仅在当前设置页可见时轮询，不启动游戏或补做盘点。 */
export function useIslandSeasonProgress(instance: string, season: string, enabled: boolean) {
  const connection = useConnection()
  const [result, setResult] = useState<{instance: string; season: string; data?: IslandSeasonProgress; failed?: boolean}>()
  useEffect(() => {
    if (!enabled || !season || connection !== 'ready') return
    let active = true
    let inFlight = false
    let timer: ReturnType<typeof setTimeout>
    const read = async () => {
      clearTimeout(timer)
      if (!active || inFlight || document.visibilityState === 'hidden') return
      inFlight = true
      try {
        const data = await api.request('island.seasonProgress', {instance})
        if (active) setResult({instance, season, data: data.season === season ? data : undefined})
      } catch {
        if (active) setResult({instance, season, failed: true})
      } finally {
        inFlight = false
        if (active) timer = setTimeout(read, 5000)
      }
    }
    void read()
    document.addEventListener('visibilitychange', read)
    return () => { active = false; clearTimeout(timer); document.removeEventListener('visibilitychange', read) }
  }, [instance, season, enabled, connection])
  const current = result?.instance === instance && result.season === season ? result : undefined
  const data = connection === 'ready' ? current?.data : undefined
  const status: StockRecordStatus = connection !== 'ready' || current?.failed ? 'unavailable' : data?.status ?? 'loading'
  return {data, status}
}
