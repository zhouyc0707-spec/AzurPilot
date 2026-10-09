import { describe, expect, it } from 'vitest'
import type { Overview, ScheduledTask } from '../api/types'
import { movedBy, taskRunAction } from './TaskQueue'

describe('任务条目在调度栏内移动的判定', () => {
  it('首次出现没有可比位置，不播动画', () => {
    expect(movedBy(undefined, {left: 10, top: 20})).toBeNull()
  })

  it('位置没动就不播动画', () => {
    expect(movedBy({left: 10, top: 20}, {left: 10, top: 20})).toBeNull()
  })

  it('亚像素抖动不算移动', () => {
    expect(movedBy({left: 10, top: 20}, {left: 10.4, top: 20.3})).toBeNull()
  })

  it('移到别的分组时给出反向位移，用作动画起点', () => {
    expect(movedBy({left: 10, top: 200}, {left: 10, top: 60})).toEqual({dx: 0, dy: 140})
  })

  it('左右方向的变化同样给出位移', () => {
    expect(movedBy({left: 300, top: 20}, {left: 120, top: 20})).toEqual({dx: 180, dy: 0})
  })
})

describe('单次任务按钮按真实运行模式显示', () => {
  const task: ScheduledTask = {name: 'Research', nextRun: '', pending: true, state: 'pending', runOnceAllowed: true}
  const overview: Overview = {instance: 'test', revision: '', status: 'stopped', tasks: [task], resources: [], emulator: {}}

  it('待运行与未来等待的调度任务均可单次执行', () => {
    expect(taskRunAction(overview, task)).toBe('run')
    expect(taskRunAction(overview, {...task, state: 'waiting', pending: false})).toBe('run')
  })

  it('当前单次任务显示停止；其他任务保留执行按钮并由运行状态禁用', () => {
    const running = {...overview, status: 'running' as const, singleTask: {name: task.name, runId: 'run'}}
    expect(taskRunAction(running, {...task, state: 'running'})).toBe('stop')
    expect(taskRunAction(running, {...task, name: 'Dorm'})).toBe('run')
  })

  it('调度器正在执行的任务不显示独立停止按钮', () => {
    expect(taskRunAction({...overview, status: 'running', schedulerRunning: true}, {...task, state: 'running'})).toBeNull()
  })

  it('未获服务端授权的任务不显示执行按钮', () => {
    expect(taskRunAction(overview, {...task, runOnceAllowed: false})).toBeNull()
    expect(taskRunAction(overview, {...task, runOnceAllowed: undefined})).toBeNull()
  })
})
