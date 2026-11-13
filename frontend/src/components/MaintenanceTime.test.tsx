// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import type { Overview } from '../api/types'
import { translateUi, type Language } from '../i18n'
import { MaintenanceTime } from './MaintenanceTime'

let language: Language = 'zh-CN'
vi.mock('../app/context', () => ({
  useApp: () => ({ui: (key: Parameters<typeof translateUi>[1], params?: Record<string, string | number>) => translateUi(language, key, params)}),
}))

const announced: NonNullable<Overview['maintenance']> = {
  state: 'announced', startsAt: '2026-11-19T09:00:00+08:00',
  checkedAt: '2026-11-18T02:00:00+00:00', error: null,
}

afterEach(() => {
  cleanup()
  language = 'zh-CN'
  vi.restoreAllMocks()
})

describe('旧版官方维护时间', () => {
  it('保留公告中的区服日期时分，详情包含年份、时区与最近查询时间', () => {
    vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-11-18T00:00:00Z'))
    render(<MaintenanceTime maintenance={announced}/>)
    const status = screen.getByRole('status')
    expect(status.textContent).toBe('下次维护时间11-19 09:00')
    expect(status.querySelector('time')?.dateTime).toBe(announced.startsAt)
    expect(status.title).toContain('2026-11-19 09:00:00+08:00')
    expect(status.title).toContain('最近查询 2026-11-18 02:00:00+00:00')
    expect(status.querySelector('button, select')).toBeNull()
  })

  it('已经开始的公告显示本次维护', () => {
    vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-11-19T02:00:00Z'))
    render(<MaintenanceTime maintenance={announced}/>)
    expect(screen.getByRole('status').textContent).toBe('本次维护11-19 09:00')
  })

  it('没有快照时显示查询中', () => {
    render(<MaintenanceTime/>)
    expect(screen.getByRole('status').textContent).toBe('下次维护时间查询中')
  })

  it.each([
    ['pending', '查询中'], ['not_published', '未发布'], ['error', '查询失败'],
  ] as const)('%s 状态有独立说明', (state, text) => {
    render(<MaintenanceTime maintenance={{state, startsAt: null, checkedAt: null, error: null}}/>)
    expect(screen.getByRole('status').textContent).toBe(`下次维护时间${text}`)
  })

  it('查询失败保留有效缓存时间，并呈现失败图标及详情', () => {
    vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-11-18T00:00:00Z'))
    render(<MaintenanceTime maintenance={{...announced, state: 'error', error: '公告连接超时'}}/>)
    const status = screen.getByRole('status')
    expect(status.textContent).toContain('11-19 09:00')
    expect(status.getAttribute('aria-label')).toContain('查询失败')
    expect(status.querySelector('.maintenance-time-warning')).not.toBeNull()
    expect(status.title).toContain('公告连接超时')
  })

  it('异常时间不显示无效日期或错误星期', () => {
    render(<MaintenanceTime maintenance={{...announced, startsAt: 'invalid'}}/>)
    expect(screen.getByRole('status').textContent).toBe('下次维护时间查询失败')
    expect(screen.getByRole('status').querySelector('time')).toBeNull()
  })

  it.each(['zh-CN', 'zh-TW', 'en-US', 'ja-JP', 'zh-MIAO'] as const)('%s 状态文案完整', value => {
    language = value
    render(<MaintenanceTime maintenance={{state: 'error', startsAt: null, checkedAt: null, error: 'ERROR'}}/>)
    const status = screen.getByRole('status')
    expect(status.textContent).not.toContain('overview.')
    expect(status.title).toContain('ERROR')
  })
})
