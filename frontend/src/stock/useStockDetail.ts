import {useEffect,useRef,useState} from 'react'
import {useAPI} from './api'
import type {ChartPeriod,StockDetail} from './types'

export function shanghaiMonth(){return new Date(Date.now()+8*3600000).toISOString().slice(0,7)}
export function useStockDetail(id:number|undefined,period:ChartPeriod,month:string,day:string,refresh:number,enabled:boolean){
  const api=useAPI(),[detail,setDetail]=useState<StockDetail>(),[error,setError]=useState(''),[loading,setLoading]=useState(false),[updated,setUpdated]=useState(0)
  const sequence=useRef(0),cachedRequest=useRef({key:'',etag:'',refresh}),latestRefresh=useRef(refresh)
  latestRefresh.current=refresh
  const request=useRef<()=>void>(()=>{})
  useEffect(()=>{
    if(!id||!enabled)return
    const query=new URLSearchParams({period,month});if(day)query.set('day',day)
    const key=`/stocks/${id}?${query}`
    // 同一证券和时间窗口恢复时沿用曲线与 ETag；切换目标才清空旧曲线。
    if(cachedRequest.current.key!==key){cachedRequest.current={key,etag:'',refresh:latestRefresh.current};setDetail(undefined);setLoading(true)}
    else if(cachedRequest.current.refresh!==latestRefresh.current){cachedRequest.current.refresh=latestRefresh.current;cachedRequest.current.etag=''}
    const generation=++sequence.current;let stopped=false,busy=false,queued=false,revision:number|undefined
    setError('')
    const run=async()=>{
      if(stopped)return;queued=true;if(busy)return;busy=true;queued=false
      try{const reply=await api.response<StockDetail>(key,cachedRequest.current.etag)
        if(stopped||generation!==sequence.current)return
        if(reply.status!==304){cachedRequest.current.etag=reply.etag;setDetail(reply.data!)}
        setUpdated(reply.serverTime);setError('')
      }catch(e){if(!stopped&&generation===sequence.current)setError((e as Error).message)}finally{busy=false;if(!stopped&&generation===sequence.current){setLoading(false);if(queued)void run()}}
    }
    request.current=()=>{if(cachedRequest.current.refresh!==latestRefresh.current){cachedRequest.current.refresh=latestRefresh.current;cachedRequest.current.etag=''};void run()}
    void run()
    // 提交事件驱动刷新，隐藏 Activity 时注销订阅，恢复后重新校验缓存。
    const unsubscribe=api.subscribe(update=>{if(update.online===false)return;if(update.revision===undefined||update.revision!==revision){revision=update.revision;void run()}})
    const visible=()=>{if(document.visibilityState==='visible')void run()};document.addEventListener('visibilitychange',visible)
    return ()=>{stopped=true;request.current=()=>{};unsubscribe();document.removeEventListener('visibilitychange',visible)}
  },[api,id,period,month,day,enabled])
  useEffect(()=>{if(cachedRequest.current.refresh!==refresh)request.current()},[refresh])
  return {detail,error,loading,updated}
}
