// @vitest-environment jsdom
import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react'
import {useState} from 'react'
import {IslandSeasonTargetScan} from './IslandSeasonTargetScan'
import {IslandImportedTargets} from './IslandImportedTargets'

const state = vi.hoisted(() => ({request: vi.fn(), instances: [{name: 'demo', status: 'stopped', currentTask: null as string | null}]}))
vi.mock('../api/client', () => ({api: {request: state.request}}))
vi.mock('../app/context', () => ({useConnection: () => 'ready', useApp: () => ({instances: state.instances, language: 'zh-CN', ui: (key: string) => key, t: (key: string) => key})}))
vi.mock('./LogPanel', () => ({LogPanel: () => <div>扫描日志</div>}))
afterEach(cleanup)
beforeEach(() => {state.request.mockReset(); state.instances = [{name: 'demo', status: 'stopped', currentTask: null}]})

it('启动前保存草稿，工具快速结束也刷新一次，不等running推送', async () => {
  const before = vi.fn(async () => {})
  const finish = vi.fn(async () => {})
  state.request.mockImplementation(async (method: string) => {
    if (method === 'tasks.run') {expect(before).toHaveBeenCalledOnce(); return {status: 'running'}}
    return [{name: 'demo', status: 'stopped', currentTask: null}]
  })
  render(<IslandSeasonTargetScan instance="demo" beforeStart={before} onFinished={finish}/>)
  fireEvent.click(screen.getByRole('button', {name: '自动读取季节目标'}))
  await waitFor(() => expect(finish).toHaveBeenCalledOnce())
  expect(state.request).toHaveBeenCalledWith('tasks.run', {instance: 'demo', task: 'IslandSeasonTargetScan'})
  expect(screen.getByRole('status').textContent).toContain('请查看日志确认识别结果')
})

it('其他任务运行期间禁用读取，不抢占实例', () => {
  state.instances = [{name: 'demo', status: 'running', currentTask: 'Commission'}]
  render(<IslandSeasonTargetScan instance="demo" beforeStart={async () => {}} onFinished={async () => {}}/>)
  expect((screen.getByRole('button', {name: '自动读取季节目标'}) as HTMLButtonElement).disabled).toBe(true)
  expect(state.request).not.toHaveBeenCalled()
})

it('启动被后端拒绝时保留错误，不显示成功或刷新目标', async () => {
  state.request.mockRejectedValue(new Error('实例已在运行'))
  const finish = vi.fn(async () => {})
  const busy = vi.fn()
  render(<IslandSeasonTargetScan instance="demo" beforeStart={async () => {}} onFinished={finish} onBusyChange={busy}/>)
  fireEvent.click(screen.getByRole('button', {name: '自动读取季节目标'}))
  await screen.findByText('实例已在运行')
  expect(finish).not.toHaveBeenCalled()
  expect(busy).toHaveBeenLastCalledWith(false)
})

it('运行阶段名称改变时继续等待进程结束', async () => {
  let polls = 0
  state.request.mockImplementation(async (method: string) => method === 'tasks.run' ? {status: 'running'}
    : [{name: 'demo', status: ++polls === 1 ? 'running' : 'stopped', currentTask: 'IslandSeasonalProduction'}])
  const finish = vi.fn(async () => {})
  render(<IslandSeasonTargetScan instance="demo" beforeStart={async () => {}} onFinished={finish}/>)
  fireEvent.click(screen.getByRole('button', {name: '自动读取季节目标'}))
  await waitFor(() => expect(polls).toBe(1))
  expect(finish).not.toHaveBeenCalled()
  await waitFor(() => expect(finish).toHaveBeenCalledOnce(), {timeout: 2500})
})

it('结束后刷新配置失败时显示错误，不声称已经刷新', async () => {
  state.request.mockImplementation(async (method: string) => method === 'tasks.run' ? {status: 'running'}
    : [{name: 'demo', status: 'stopped', currentTask: null}])
  const busy = vi.fn()
  render(<IslandSeasonTargetScan instance="demo" beforeStart={async () => {}} onBusyChange={busy}
    onFinished={async () => {throw new Error('刷新配置失败')}}/>)
  fireEvent.click(screen.getByRole('button', {name: '自动读取季节目标'}))
  await screen.findByText('刷新配置失败')
  expect(screen.queryByText(/读取进程已结束，已刷新目标/)).toBeNull()
  expect(busy).toHaveBeenLastCalledWith(true)
})

it('保存草稿前锁住旧目标，刷新成功前不会发出覆盖新增物品的JSON', async () => {
  let save!: () => void
  let refresh!: () => void
  const saved = new Promise<void>(resolve => { save = resolve })
  const refreshed = new Promise<void>(resolve => { refresh = resolve })
  const change = vi.fn()
  state.request.mockImplementation(async (method: string) => method === 'tasks.run' ? {status: 'running'}
    : [{name: 'demo', status: 'stopped', currentTask: null}])
  const finish = vi.fn(async () => { await refreshed })
  function Page() {
    const [busy, setBusy] = useState(false)
    const [value, setValue] = useState('{"茶叶 (2014)":500}')
    return <><IslandSeasonTargetScan instance="demo" onBusyChange={setBusy} beforeStart={() => saved}
      onFinished={async () => { await finish(); setValue('{"茶叶 (2014)":500,"墨盒 (3053)":10}') }}/>
      <IslandImportedTargets id="targets" value={value} disabled={busy} onChange={change}/></>
  }
  render(<Page/>)
  const tea = screen.getByRole('textbox', {name: '茶叶'}) as HTMLInputElement
  fireEvent.click(screen.getByRole('button', {name: '自动读取季节目标'}))
  expect(tea.disabled).toBe(true)
  expect(state.request).not.toHaveBeenCalled()
  await act(async () => save())
  await waitFor(() => expect(finish).toHaveBeenCalledOnce())
  expect(tea.disabled).toBe(true)
  fireEvent.focus(tea); fireEvent.change(tea, {target: {value: '600'}}); fireEvent.blur(tea)
  expect(change).not.toHaveBeenCalled()
  await act(async () => refresh())
  await waitFor(() => expect(tea.disabled).toBe(false))
  expect((screen.getByRole('textbox', {name: '墨盒'}) as HTMLInputElement).value).toBe('10')
  fireEvent.focus(tea); fireEvent.change(tea, {target: {value: '700'}}); fireEvent.blur(tea)
  expect(change).toHaveBeenLastCalledWith('{"茶叶 (2014)":700,"墨盒 (3053)":10}', undefined)
})

it('切入正在读取的页面立即锁定，离开时释放本页锁', () => {
  state.instances = [{name: 'demo', status: 'running', currentTask: 'IslandSeasonTargetScan'}]
  state.request.mockReturnValue(new Promise(() => {}))
  const busy = vi.fn()
  const {unmount} = render(<IslandSeasonTargetScan instance="demo" onBusyChange={busy}
    beforeStart={async () => {}} onFinished={async () => {}}/>)
  expect(busy).toHaveBeenLastCalledWith(true)
  unmount()
  expect(busy).toHaveBeenLastCalledWith(false)
})
