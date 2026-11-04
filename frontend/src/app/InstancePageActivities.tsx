/** 保留当前实例的总览、股票接入与收藏页，恢复时沿用页面状态。 */
import { Activity, lazy, Suspense, useState, type ReactNode } from 'react'
import { StockExchangeBoundary, StockExchangeFallback } from '../components/StockExchangeBoundary'
import { Loading } from '../components/ui'

const StockExchange = lazy(() => import('../pages/StockExchange').then(module => ({default: module.StockExchange})))
const SkinCollection = lazy(() => import('../pages/SkinCollection').then(module => ({default: module.SkinCollection})))

/** 外层按实例设 key，避免跨实例保留股票登记或总览数据。 */
export function InstancePageActivities({exchange, skinCollection, exchangeReady, children}: {
  exchange: boolean; skinCollection: boolean; exchangeReady: boolean; children: ReactNode
}) {
  const [visitedExchange, setVisitedExchange] = useState(exchange)
  const [visitedCollection, setVisitedCollection] = useState(skinCollection)
  // 只在首次进入时挂载股票接入代码；之后保留当前实例的页面，不预加载其它实例。
  if (exchange && !visitedExchange) setVisitedExchange(true)
  // 保留 iframe，避免导航打断原网页的防抖保存，也保留筛选和滚动位置。
  if (skinCollection && !visitedCollection) setVisitedCollection(true)

  return <>
    <Activity name="实例工作区" mode={exchange || skinCollection ? 'hidden' : 'visible'}>{children}</Activity>
    {visitedExchange && <Activity name="茗喵证券交易所" mode={exchange ? 'visible' : 'hidden'}>
      <main id="stock-main-content" tabIndex={-1}>
        <StockExchangeBoundary>{exchangeReady ? <Suspense fallback={<StockExchangeFallback/>}><StockExchange/></Suspense> : <StockExchangeFallback/>}</StockExchangeBoundary>
      </main>
    </Activity>}
    {visitedCollection && <Activity name="换装收藏统计" mode={skinCollection ? 'visible' : 'hidden'}>
      <main id="skin-collection-main-content" tabIndex={-1}>
        <Suspense fallback={<Loading/>}><SkinCollection/></Suspense>
      </main>
    </Activity>}
  </>
}
