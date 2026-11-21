import { useState, type ReactNode } from 'react'
import { DndContext, PointerSensor, useSensor, useSensors, type CollisionDetection, type UniqueIdentifier } from '@dnd-kit/core'
import { SortableContext, arrayMove, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable'
import { GripVertical } from 'lucide-react'
import { useApp } from '../app/context'

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

// 表格和图表高度不同，按指针所在板块判断。指针在卡片间隙时取最近的边。
const sectionAtPointer: CollisionDetection = args => {
  if (!args.pointerCoordinates) return []
  const {y} = args.pointerCoordinates
  let distance = Infinity
  let nearest: UniqueIdentifier | undefined
  for (const [id, rect] of args.droppableRects) {
    const gap = Math.max(rect.top - y, y - rect.bottom, 0)
    if (gap < distance) {
      distance = gap
      nearest = id
    }
  }
  return nearest === undefined ? [] : [{id: nearest}]
}

type Panel = {className?: string; children: ReactNode}

function SortableSection({id, label, moveBy, className = '', children}: Panel & {
  id: Section; label: string; moveBy: (step: -1 | 1) => void
}) {
  const {attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging} = useSortable({id})
  return <section ref={setNodeRef} className={`legacy-stats-section legacy-stats-card ${className}`}
    data-dragging={isDragging || undefined} style={{transform: transform ? `translateY(${transform.y}px)` : undefined, transition}}>
    <button type="button" ref={setActivatorNodeRef} {...attributes} {...listeners}
      className="legacy-section-grip" aria-label={label} title={label} aria-keyshortcuts="ArrowUp ArrowDown"
      onKeyDown={event => {
        if (isDragging || (event.key !== 'ArrowUp' && event.key !== 'ArrowDown')) return
        event.preventDefault()
        moveBy(event.key === 'ArrowUp' ? -1 : 1)
        const grip = event.currentTarget
        requestAnimationFrame(() => {
          grip.focus({preventScroll: true})
          grip.scrollIntoView({block: 'nearest'})
        })
      }}>
      <GripVertical size={20} fill="currentColor" aria-hidden="true"/>
    </button>
    {children}
  </section>
}

export function LegacyStatisticsSections({sections}: {sections: Record<Section, Panel>}) {
  const {ui, t} = useApp()
  const [order, setOrder] = useState(readOrder)
  const sensors = useSensors(useSensor(PointerSensor, {activationConstraint: {distance: 4}}))
  const labels = {
    ap: ui('legacyStats.apTrend'), opsi: t('Gui.Stat.OpsiDataCollectionTitle'),
    meow: t('Gui.Stat.MeowLootTitle'), shipExp: t('Gui.Stat.ShipExpProgressTitle'),
    commission: t('Gui.Stat.CommissionIncomeTitle'),
  }

  function move(from: number, to: number) {
    if (from < 0 || to < 0 || to >= order.length || from === to) return
    const next = arrayMove(order, from, to)
    setOrder(next)
    try {
      localStorage.setItem(storageKey, JSON.stringify(next))
    } catch {
      // 存储不可写时仍可在当前页面排序。
    }
  }

  return <DndContext sensors={sensors} collisionDetection={sectionAtPointer}
    accessibility={{screenReaderInstructions: {draggable: ui('legacyStats.sortKeys')}}}
    onDragEnd={({active, over}) => {
      if (over) move(order.findIndex(id => id === active.id), order.findIndex(id => id === over.id))
    }}>
    <SortableContext items={order} strategy={verticalListSortingStrategy}>
      <div className="legacy-stats-charts">
        {order.map((id, index) => <SortableSection key={id} id={id} {...sections[id]}
          label={ui('legacyStats.dragSection', {label: labels[id]})} moveBy={step => move(index, index + step)}/>)}
      </div>
    </SortableContext>
  </DndContext>
}
