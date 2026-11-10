// @vitest-environment jsdom
import {afterEach, expect, it, vi} from 'vitest'
import {cleanup, render, screen} from '@testing-library/react'
import {IslandTargetProgress} from './IslandTargetProgress'

vi.mock('../app/context', () => ({useApp: () => ({language: 'zh-CN'})}))
afterEach(cleanup)

it('只将库存计入完成量，在制单列', () => {
  render(<IslandTargetProgress target={500} status="observed" observation={{stock: 300, inProduction: 100}}/>)
  expect(screen.getByText('已完成 300/500')).toBeTruthy()
  expect(screen.getByText('在制：100')).toBeTruthy()
  expect((screen.getByRole('progressbar') as HTMLProgressElement).value).toBe(300)
})

it('超额库存封顶完成量，同时保留实际库存', () => {
  render(<IslandTargetProgress target="500" status="observed" observation={{stock: 800, inProduction: 0}}/>)
  expect(screen.getByText('已完成 500/500')).toBeTruthy()
  expect(screen.getByText('实际库存：800')).toBeTruthy()
  expect(screen.getByText('在制：0')).toBeTruthy()
})

it('未知量不显示0，未盘点和未知库存有区别', () => {
  const {rerender} = render(<IslandTargetProgress target={500} status="missing"/>)
  expect(screen.getByText('尚未盘点')).toBeTruthy()
  rerender(<IslandTargetProgress target={500} status="observed" observation={{stock: null, inProduction: null}}/>)
  expect(screen.getByText('库存待确认')).toBeTruthy()
  expect(screen.getByText('在制待确认')).toBeTruthy()
  expect(screen.queryByRole('progressbar')).toBeNull()
  expect(screen.queryByText(/已完成|：0/)).toBeNull()
})

it('失效记录只显示历史值，不保留当前进度', () => {
  render(<IslandTargetProgress target={500} status="invalidated" observation={{stock: 300, inProduction: 100}}/>)
  expect(screen.getByText('库存记录已失效，待重新盘点')).toBeTruthy()
  expect(screen.getByText('历史库存：300')).toBeTruthy()
  expect(screen.getByText('历史在制：100')).toBeTruthy()
  expect(screen.queryByRole('progressbar')).toBeNull()
  expect(screen.queryByText(/已完成/)).toBeNull()
})

it('目标0明确未设置，非法目标不会显示完成百分比', () => {
  const {rerender} = render(<IslandTargetProgress target={0} status="observed" observation={{stock: 20, inProduction: null}}/>)
  expect(screen.getByText('未设置目标')).toBeTruthy()
  expect(screen.getByText('实际库存：20')).toBeTruthy()
  expect(screen.queryByRole('progressbar')).toBeNull()
  rerender(<IslandTargetProgress target="-1" status="observed" observation={{stock: 20, inProduction: null}}/>)
  expect(screen.getByText('目标待修正')).toBeTruthy()
  expect(screen.queryByText('库存待确认')).toBeNull()
})
