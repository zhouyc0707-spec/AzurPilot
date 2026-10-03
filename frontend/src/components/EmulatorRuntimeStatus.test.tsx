/** 验证检测快照、失败提示与重启预计时间的展示边界。 */
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { EmulatorStatus } from '../api/types'
import { EmulatorRuntimeStatusView, formatEmulatorDuration } from './EmulatorRuntimeStatus'

const status: EmulatorStatus = {
  instance: 'test', uptimeSeconds: 3665, checkedAt: 1800000000, lastAttemptAt: 1800000000,
  available: true, scheduled: true, force: false, intervalHours: 12,
  nextRestartAt: 1800000000 + 12 * 3600 - 3665, serverTime: 1800000060, schedulerRunning: true,
}
const render = (changes: Partial<EmulatorStatus> = {}, connected = true, failed = false) => renderToStaticMarkup(
  <EmulatorRuntimeStatusView status={{...status, ...changes}} language="zh-CN" connected={connected} failed={failed}/>,
)

describe('模拟器运行时长状态', () => {
  it('显示检测时的运行时长与检测时间，同时用服务器时间计算剩余时间', () => {
    const html = render()
    expect(html).toContain('1小时 1分 5秒')
    expect(html).toContain('检测时间：')
    expect(html).toContain('剩余 10小时 57分 55秒')
    expect(html).toContain('在当前任务结束后的任务间隙重启')
  })
  it('新检测结果可以反映重启后的计时归零', () => {
    const html = render({uptimeSeconds: 0, nextRestartAt: status.serverTime + 12 * 3600})
    expect(html).toContain('0小时 0分 0秒')
    expect(html).toContain('剩余 12小时 0分 0秒')
  })
  it('超过间隔时显示已达到条件，不显示负数倒计时', () => {
    const html = render({nextRestartAt: status.serverTime - 1})
    expect(html).toContain('已达到重启条件')
    expect(html).not.toContain('剩余')
  })
  it('读取失败时明确保留的是旧成功值，并停止预计时间', () => {
    const html = render({available: false, lastAttemptAt: status.serverTime, nextRestartAt: null})
    expect(html).toContain('1小时 1分 5秒')
    expect(html).toContain('显示上次成功检测的结果')
    expect(html).toContain('最近检测失败，暂无法预计')
    expect(html).not.toContain('剩余')
  })
  it('没有检测结果时不会伪造运行时长', () => {
    const html = render({uptimeSeconds: null, checkedAt: null, lastAttemptAt: null, available: false, nextRestartAt: null})
    expect(html).toContain('尚未检测')
    expect(html).toContain('启动调度器并启用定时重启后')
    expect(html).not.toContain('0小时')
  })
  it('关闭定时重启、停止调度器和断线分别显示状态', () => {
    expect(render({scheduled: false})).toContain('未启用定时重启')
    expect(render({schedulerRunning: false})).toContain('调度器未运行，自动重启暂停')
    expect(render({}, false)).toContain('连接已断开')
    expect(render({}, true, true)).toContain('暂时无法获取检测结果')
  })
  it('强制模式继续说明敏感任务保护', () => {
    expect(render({force: true})).toContain('敏感任务仍会等它完成')
  })
  it('运行时长超过一天仍完整显示，并提供五种语言的单位', () => {
    expect(formatEmulatorDuration(173325.24, 'zh-CN')).toBe('48小时 8分 45秒')
    expect(formatEmulatorDuration(3665, 'en-US')).toBe('1h 1m 5s')
    expect(formatEmulatorDuration(3665, 'ja-JP')).toBe('1時間 1分 5秒')
    expect(formatEmulatorDuration(3665, 'zh-TW')).toBe('1小時 1分 5秒')
    expect(formatEmulatorDuration(3665, 'zh-MIAO')).toBe('1小时 1分 5秒')
  })
})
