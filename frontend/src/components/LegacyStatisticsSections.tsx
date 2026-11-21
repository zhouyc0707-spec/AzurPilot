import { Fragment, useState, type ReactNode } from 'react'
import { ArrowDown, ArrowUp, ArrowUpDown, GripVertical } from 'lucide-react'
import { Reorder, useDragControls } from 'motion/react'
import { useApp } from '../app/context'
import { Modal } from './ui'

const defaultOrder = ['ap', 'opsi', 'meow', 'shipExp', 'commission'] as const
type Section = typeof defaultOrder[number]
const storageKey = 'azurpilot.legacy-statistics.section-order'

function readOrder(): Section[] {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(storageKey) ?? 'null')
    if (Array.isArray(saved)) {
      const known = saved.filter((value): value is Section => defaultOrder.some(id => id === value))
      return [...new Set([...known, ...defaultOrder])]
    }
  } catch {
    // 旧偏好损坏或浏览器禁用存储时使用默认顺序。
  }
  return [...defaultOrder]
}

function OrderRow({id, label, first, last, move}: {
  id: Section; label: string; first: boolean; last: boolean; move: (step: number) => void
}) {
  const {ui} = useApp()
  const drag = useDragControls()
  return <Reorder.Item value={id} dragControls={drag} dragListener={false} dragMomentum={false}>
    <span className="legacy-order-grip" title={ui('stats.orderHandle')} onPointerDown={event => drag.start(event)}>
      <GripVertical size={16} aria-hidden="true"/>{label}
    </span>
    <button type="button" className="legacy-button" disabled={first} aria-label={ui('stats.moveEarlier', {label})}
      title={ui('stats.orderUp')} onClick={() => move(-1)}><ArrowUp size={16}/></button>
    <button type="button" className="legacy-button" disabled={last} aria-label={ui('stats.moveLater', {label})}
      title={ui('stats.orderDown')} onClick={() => move(1)}><ArrowDown size={16}/></button>
  </Reorder.Item>
}

export function LegacyStatisticsSections({sections}: {sections: Record<Section, ReactNode>}) {
  const {ui, t} = useApp()
  const [order, setOrder] = useState(readOrder)
  const [editing, setEditing] = useState(false)
  const labels = {
    ap: ui('legacyStats.apTrend'), opsi: t('Gui.Stat.OpsiDataCollectionTitle'),
    meow: t('Gui.Stat.MeowLootTitle'), shipExp: t('Gui.Stat.ShipExpProgressTitle'),
    commission: t('Gui.Stat.CommissionIncomeTitle'),
  }

  function saveOrder(next: Section[]) {
    setOrder(next)
    try {
      localStorage.setItem(storageKey, JSON.stringify(next))
    } catch {
      // 存储不可写时仍可在当前页面排序。
    }
  }

  function move(id: Section, index: number) {
    const next = order.filter(value => value !== id)
    next.splice(index, 0, id)
    saveOrder(next)
  }

  return <>
    <button type="button" className="legacy-button legacy-order-open" aria-haspopup="dialog" onClick={() => setEditing(true)}>
      <ArrowUpDown size={15} aria-hidden="true"/>{ui('legacyStats.sectionOrder')}
    </button>
    <div className="legacy-stats-charts">
      {order.map(id => <Fragment key={id}>{sections[id]}</Fragment>)}
    </div>
    {editing && <Modal title={ui('legacyStats.sectionOrder')} className="legacy-order-modal" onClose={() => setEditing(false)}>
      <Reorder.Group as="ol" axis="y" values={order} onReorder={saveOrder} className="legacy-order-list" aria-label={ui('legacyStats.sectionOrder')}>
        {order.map((id, index) => <OrderRow key={id} id={id} label={labels[id]}
          first={index === 0} last={index === order.length - 1} move={step => move(id, index + step)}/>)}
      </Reorder.Group>
      <button type="button" className="legacy-button" disabled={order.every((id, index) => id === defaultOrder[index])}
        onClick={() => saveOrder([...defaultOrder])}>{ui('resource.restoreDefault')}</button>
    </Modal>}
  </>
}
