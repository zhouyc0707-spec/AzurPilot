import {useEffect,useRef,useState} from 'react'
import {useAPI} from './api'
import type {ChartPeriod,StockDetail} from './types'

export function shanghaiMonth(){return new Date(Date.now()+8*3600000).toISOString().slice(0,7)}
export function useStockDetail(id:number|undefined,period:ChartPeriod,month:string,day:string,refresh:number,enabled:boolean){
  const api=useAPI(),[detail,setDetail]=useState<StockDetail>(),[error,setError]=useState(''),[loading,setLoading]=useState(false),[updated,setUpdated]=useState(0)
  const sequence=useRef(0)
  const cachedRequest=useRef({key:'',etag:'',refresh})
  useEffect(()=>{
    if(!id||!enabled)return
    const query=new URLSearchParams({period,month});if(day)query.set('day',day)
    const key=`/stocks/${id}?${query}`
    // 同一证券和时间窗口恢复时沿用曲线与 ETag；切换目标才清空旧曲线。
    if(cachedRequest.current.key!==key){cachedRequest.current={key,etag:'',refresh};setDetail(undefined);setLoading(true)}
    else if(cachedRequest.current.refresh!==refresh){cachedRequest.current.refresh=refresh;cachedRequest.current.etag=''}
    const generation=++sequence.current;let stopped=false,busy=false,etag=cachedRequest.current.etag
    setError('')
    const run=async()=>{
      if(busy||stopped)return;busy=true
      try{const reply=await api.response<StockDetail>(key,etag)
        if(stopped||generation!==sequence.current)return
        if(reply.status!==304){etag=reply.etag;cachedRequest.current.etag=etag;setDetail(reply.data!)}
        setUpdated(reply.serverTime);setError('')
      }catch(e){if(!stopped&&generation===sequence.current)setError((e as Error).message)}finally{busy=false;if(!stopped&&generation===sequence.current)setLoading(false)}
    }
    void run();const timer=setInterval(()=>{if(document.visibilityState==='visible')void run()},5000)
    const visible=()=>{if(document.visibilityState==='visible')void run()};document.addEventListener('visibilitychange',visible)
    return ()=>{stopped=true;clearInterval(timer);document.removeEventListener('visibilitychange',visible)}
  },[api,id,period,month,day,refresh,enabled])
  return {detail,error,loading,updated}
}
