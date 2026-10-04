/** 保留当前实例的总览外壳与交易终端，隐藏时暂停副作用，恢复时沿用页面状态。 */
import { Activity, lazy, Suspense, useState, type ReactNode } from 'react'
import { Loading } from '../components/ui'

const StockExchange = lazy(() => import('../pages/StockExchange').then(module => ({default: module.StockExchange})))

/** 外层按实例设 key，避免跨实例保留账号、行情或总览数据。 */
export function InstancePageActivities({exchange, exchangeReady, children}: {
  exchange: boolean; exchangeReady: boolean; children: ReactNode
}) {
  const [visitedExchange, setVisitedExchange] = useState(exchange)
  // 只在首次进入时挂载交易代码；之后保留当前实例的页面，不预加载其它实例。
  if (exchange && !visitedExchange) setVisitedExchange(true)

  return <>
    <Activity name="实例工作区" mode={exchange ? 'hidden' : 'visible'}>{children}</Activity>
    {visitedExchange && <Activity name="茗喵证券交易所" mode={exchange ? 'visible' : 'hidden'}>
      <div className="stock-exchange-shell"><div id="stock-main-content" role="main" tabIndex={-1}>
        {exchangeReady ? <Suspense fallback={<Loading/>}><StockExchange/></Suspense> : <Loading/>}
      </div></div>
    </Activity>}
  </>
}
