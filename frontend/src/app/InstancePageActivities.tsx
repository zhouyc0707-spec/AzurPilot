/** 保留当前实例的总览与股票接入页，隐藏时暂停副作用，恢复时沿用页面状态。 */
import { Activity, lazy, Suspense, useState, type ReactNode } from 'react'
import { StockExchangeBoundary, StockExchangeFallback } from '../components/StockExchangeBoundary'

const StockExchange = lazy(() => import('../pages/StockExchange').then(module => ({default: module.StockExchange})))

/** 外层按实例设 key，避免跨实例保留股票登记或总览数据。 */
export function InstancePageActivities({exchange, exchangeReady, children}: {
  exchange: boolean; exchangeReady: boolean; children: ReactNode
}) {
  const [visitedExchange, setVisitedExchange] = useState(exchange)
  // 只在首次进入时挂载股票接入代码；之后保留当前实例的页面，不预加载其它实例。
  if (exchange && !visitedExchange) setVisitedExchange(true)

  return <>
    <Activity name="实例工作区" mode={exchange ? 'hidden' : 'visible'}>{children}</Activity>
    {visitedExchange && <Activity name="茗喵证券交易所" mode={exchange ? 'visible' : 'hidden'}>
      <main id="stock-main-content" tabIndex={-1}>
        <StockExchangeBoundary>{exchangeReady ? <Suspense fallback={<StockExchangeFallback/>}><StockExchange/></Suspense> : <StockExchangeFallback/>}</StockExchangeBoundary>
      </main>
    </Activity>}
  </>
}
