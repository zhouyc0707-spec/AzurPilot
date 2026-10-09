import {describe, expect, it} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {IslandPlannerReportView, itemProgress, parsePlannerReport, type PlannerReport} from './IslandPlannerReport'

const makeReport = (): PlannerReport => ({
  version: 1, enabled: true, legacy: false, generated_at: '2026-10-09 11:00:00', daily_revenue: 100, daily_profit: 40,
  items: [{id: 2001, name: '小麦', target: 100, floor: 10, buffer: 20, reserve: 7, demand: 63, idle_per_day: 2}],
  production: [{recipe_id: 101, name: '小麦', place: '农田', batches_per_day: 1.5}],
  menus: [{id: 601, name: '有鱼餐馆', capacity: 7, items: [{id: 2001, name: '小麦', per_day: 2}]}],
  observations: {'2001': {stock: 20, at: '2026-10-09 11:01:00', source: 'IslandFarm'}},
  dispatches: {'2001': {amount: 200, at: '2026-10-09 11:02:00', source: 'IslandFarm'}},
})

describe('岛屿规划只读明细', () => {
  it('确认下单不代表当前在产，不从最近现货缺口扣除', () => {
    const report = makeReport()
    const parsed = parsePlannerReport(JSON.stringify(report))!
    expect(itemProgress(parsed.items[0], parsed)).toMatchObject({kind: 'short', gap: 80})
    const html = renderToStaticMarkup(<IslandPlannerReportView report={parsed}/>)
    expect(html).toContain('最近实读缺口：小麦 80')
    expect(html).toContain('最近确认下单预计产出')
    expect(html).toContain('2026-10-09 11:01:00')
    expect(html).toContain('2026-10-09 11:02:00')
    expect(html).toContain('不从现货缺口扣除')
    expect(html).not.toContain('<details open')
  })

  it('实读零库存与缺失库存分开；失效观测保留数值但不计算缺口', () => {
    const report = makeReport()
    report.observations['2001'].stock = 0
    expect(itemProgress(report.items[0], report)).toMatchObject({kind: 'short', gap: 100})
    report.observations['2001'] = {...report.observations['2001'], stock: 999, stale: true}
    expect(itemProgress(report.items[0], report)).toMatchObject({kind: 'stale', gap: null, label: '待复核'})
    delete report.observations['2001']
    expect(itemProgress(report.items[0], report)).toMatchObject({kind: 'unknown', gap: null, label: '待巡检'})
  })

  it('最近现货已够须保留观测时间，不推断没有时间戳的读数', () => {
    const report = makeReport()
    report.observations['2001'].stock = 100
    expect(itemProgress(report.items[0], report)).toMatchObject({kind: 'enough', gap: 0})
    const html = renderToStaticMarkup(<IslandPlannerReportView report={report}/>)
    expect(html).toContain('最近现货已够')
    expect(html).toContain('2026-10-09 11:01:00')
    report.observations['2001'].at = ''
    const parsed = parsePlannerReport(JSON.stringify(report))!
    expect(itemProgress(parsed.items[0], parsed).kind).toBe('unknown')
  })

  it('旧计划缺少巡检字段可回显，并说明等待补充而不填零', () => {
    const report = makeReport()
    const raw = {...report, legacy: true, enabled: false, generated_at: '', daily_revenue: null, daily_profit: null,
      observations: undefined, dispatches: undefined, production: undefined, menus: undefined}
    const parsed = parsePlannerReport(JSON.stringify(raw))!
    expect(parsed.observations).toEqual({})
    expect(parsed.dispatches).toEqual({})
    const html = renderToStaticMarkup(<IslandPlannerReportView report={parsed} status="规划失败，保留旧计划：岗位不足"/>)
    expect(html).toContain('当前按手工配置运行')
    expect(html).toContain('等待下一次生产巡检补充详情')
    expect(html).toContain('上次有效计划')
    expect(html).toContain('未实读')
    expect(html).not.toContain('理论每日收入')
  })

  it('损坏版本、重复商品和非法目标不会显示成有效计划', () => {
    expect(parsePlannerReport('坏 JSON')).toBeNull()
    expect(parsePlannerReport(JSON.stringify({...makeReport(), version: 2}))).toBeNull()
    expect(parsePlannerReport(JSON.stringify({...makeReport(), items: [{...makeReport().items[0], target: -1}]}))).toBeNull()
    const report = makeReport()
    report.items.push({...report.items[0]})
    expect(parsePlannerReport(JSON.stringify(report))).toBeNull()
    const html = renderToStaticMarkup(<IslandPlannerReportView report={null}/>)
    expect(html).toContain('等待下一次生产巡检补充详情')
  })

  it('仅余岗积累不混入库存缺口；未知货架仍保留已知菜单', () => {
    const report = makeReport()
    report.items[0].target = 0
    report.observations = {}
    report.menus[0].capacity = null
    const parsed = parsePlannerReport(JSON.stringify(report))!
    expect(itemProgress(parsed.items[0], parsed)).toMatchObject({kind: 'idle', gap: null, label: '余岗积累'})
    expect(parsed.menus[0].items).toHaveLength(1)
    const html = renderToStaticMarkup(<IslandPlannerReportView report={parsed}/>)
    expect(html).toContain('仅余岗积累 <strong>1</strong> 项')
    expect(html).toContain('待巡检／待复核 <strong>0</strong> 项')
    expect(html).toContain('最近现货已够 <strong>0</strong> 项')
    expect(html).toContain('一货架数量待确认')
    expect(html).toContain('理论 2 份／日')
  })

  it('只读表格对名称作文本转义，菜单与配方标明理论口径', () => {
    const report = makeReport()
    report.items[0].name = '<script>异常名称</script>'
    const html = renderToStaticMarkup(<IslandPlannerReportView report={report}/>)
    expect(html).toContain('&lt;script&gt;异常名称&lt;/script&gt;')
    expect(html).not.toContain('<script>')
    expect(html).toContain('理论批次／日')
    expect(html).toContain('理论 2 份／日')
    expect(html).toContain('余岗积累 2／日')
    expect(html).toContain('一货架 7 份')
    expect(html).toContain('菜单为最近保存的规划安排；实际经营可能按季节或加成换菜，以经营任务当前结果为准。')
  })
})
