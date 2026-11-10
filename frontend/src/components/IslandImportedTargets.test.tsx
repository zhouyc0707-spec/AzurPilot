// @vitest-environment jsdom
import {afterEach, describe, expect, it, vi} from 'vitest'
import {cleanup, fireEvent, render, screen} from '@testing-library/react'
import {IslandImportedTargets, parseImportedTargetRows, validImportedTargetCount} from './IslandImportedTargets'

vi.mock('../app/context', () => ({useApp: () => ({language: 'zh-CN', ui: (key: string) => key, t: (key: string) => key})}))
afterEach(cleanup)

describe('扫描导入目标编辑', () => {
  it('读取稳定ID和数字，拒绝非映射、未知结构和不带ID的名称', () => {
    expect(parseImportedTargetRows('{"茶叶 (2014)":500}')).toEqual({'茶叶 (2014)': 500})
    for (const value of ['[]', 'null', '{"茶叶":500}', '{"茶叶 (2014)":{}}']) {
      expect(() => parseImportedTargetRows(value)).toThrow()
    }
    expect(validImportedTargetCount(0)).toBe(true)
    for (const value of [-1, 1.5, Number.MAX_SAFE_INTEGER + 1, '-1']) expect(validImportedTargetCount(value)).toBe(false)
  })

  it('只改对应物品，非法数量作为有错误的草稿保留', () => {
    const change = vi.fn()
    render(<IslandImportedTargets id="targets" value={'{"茶叶 (2014)":500,"柑橘 (2017)":100}'} onChange={change}/>)
    const tea = screen.getByRole('textbox', {name: '茶叶'})
    fireEvent.focus(tea); fireEvent.change(tea, {target: {value: '800'}}); fireEvent.blur(tea)
    expect(change).toHaveBeenLastCalledWith('{"茶叶 (2014)":800,"柑橘 (2017)":100}', undefined)
    fireEvent.focus(tea); fireEvent.change(tea, {target: {value: '-1'}}); fireEvent.blur(tea)
    expect(change.mock.lastCall?.[0]).toBe('{"茶叶 (2014)":"-1","柑橘 (2017)":100}')
    expect(change.mock.lastCall?.[1]).toBeTruthy()
  })

  it('按官方ID显示库存进度，不把在制加入已完成', () => {
    render(<IslandImportedTargets id="targets" value={'{"茶叶 (2014)":500,"柑橘 (2017)":100}'} onChange={vi.fn()}
      progressStatus="observed" observations={{2014: {stock: 300, inProduction: 100}, 2017: {stock: null, inProduction: null}}}/>)
    expect(screen.getByText('已完成 300/500')).toBeTruthy()
    expect(screen.getByText('在制：100')).toBeTruthy()
    expect(screen.getByText('库存待确认')).toBeTruthy()
  })
})
