// @vitest-environment jsdom
import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {act, cleanup, renderHook} from '@testing-library/react'
import type {IslandSeasonProgress} from '../api/types'
import {useIslandSeasonProgress} from './useIslandSeasonProgress'

const state = vi.hoisted(() => ({request: vi.fn(), connection: 'ready'}))
vi.mock('../api/client', () => ({api: {request: state.request}}))
vi.mock('../app/context', () => ({useConnection: () => state.connection}))
const snapshot = (stock: number): IslandSeasonProgress => ({season: 'winter', status: 'observed', updatedAt: '2026-11-10T12:00:00+08:00', items: [{itemId: 2014, key: 'tea', stock, inProduction: null}]})
beforeEach(() => { state.request.mockReset(); state.connection = 'ready'; vi.useFakeTimers() })
afterEach(() => { cleanup(); vi.useRealTimers() })

it('仅当前页每5秒读取一次，关闭页面停止请求', async () => {
  state.request.mockResolvedValue(snapshot(300))
  const {result, rerender, unmount} = renderHook(({enabled}) => useIslandSeasonProgress('demo', 'winter', enabled), {initialProps: {enabled: false}})
  expect(state.request).not.toHaveBeenCalled()
  await act(async () => rerender({enabled: true}))
  expect(result.current.data?.items[0].stock).toBe(300)
  expect(state.request).toHaveBeenCalledWith('island.seasonProgress', {instance: 'demo'})
  await act(async () => vi.advanceTimersByTimeAsync(5000))
  expect(state.request).toHaveBeenCalledTimes(2)
  unmount()
  await act(async () => vi.advanceTimersByTimeAsync(10000))
  expect(state.request).toHaveBeenCalledTimes(2)
})

it('切换实例丢弃旧请求，失败时不显示上一实例库存', async () => {
  let previous!: (value: IslandSeasonProgress) => void
  state.request.mockImplementation((_method: string, params: {instance: string}) => {
    return params.instance === 'first' ? new Promise<IslandSeasonProgress>(resolve => { previous = resolve }) : Promise.reject(new Error('offline'))
  })
  const {result, rerender} = renderHook(({instance}) => useIslandSeasonProgress(instance, 'winter', true), {initialProps: {instance: 'first'}})
  await act(async () => rerender({instance: 'second'}))
  expect(result.current.status).toBe('unavailable')
  await act(async () => previous(snapshot(999)))
  expect(result.current.data).toBeUndefined()
  expect(result.current.status).toBe('unavailable')
})

it('季节不一致不显示旧季库存，连接断开不冒充当前记录', async () => {
  state.request.mockResolvedValue(snapshot(300))
  const {result, rerender} = renderHook(({season}) => useIslandSeasonProgress('demo', season, true), {initialProps: {season: 'winter'}})
  await act(async () => {})
  expect(result.current.data?.items[0].stock).toBe(300)
  await act(async () => rerender({season: 'spring'}))
  expect(result.current.data).toBeUndefined()
  state.connection = 'offline'
  rerender({season: 'spring'})
  expect(result.current.status).toBe('unavailable')
})
