import {describe, expect, it} from 'vitest'
import {matches, preview, type Row} from './types'

const row: Row = {group_type: 10102, code: 4, handbook_type: 0, blueprint: false, name: '杜威', names: {cn: '杜威', en: 'Dewey'}, nationality: 1, rarity: 'R', type: 20,
  owned: true, full_star: null, lv120: true, level: 125, observed_level: 125, status: 'complete', manual: false, points: 8,
  completion_target: 100, acquire_level: 125, acquire_lv120: true,
  technology: {pt_get: 3, pt_upgrage: 7, pt_level: 5, max_star: 5, add_get_shiptype: [1, 20, 21], add_get_attr: 1, add_get_value: 1, add_level_shiptype: [7], add_level_attr: 5, add_level_value: 1}}
describe('科技联合筛选', () => {
  it('组内并集、组间交集，并支持编号和外文名称搜索', () => {
    expect(matches(row, {type: ['1', '2'], nation: ['1'], status: ['complete']}, '004', 'tech', 'all')).toBe(true)
    expect(matches(row, {nation: ['2']}, '', 'all', 'all')).toBe(false)
    expect(matches(row, {}, 'dewey', 'all', 'all')).toBe(true)
  })
  it('舰种和属性必须命中同一阶段的加成', () => {
    expect(matches(row, {bonusType: ['1'], attr: ['5']}, '', 'all', 'all')).toBe(false)
    expect(matches(row, {bonusType: ['1'], attr: ['1']}, '', 'all', 'get')).toBe(true)
    expect(matches(row, {bonusType: ['1'], attr: ['1']}, '', 'all', 'level')).toBe(false)
  })
  it('无记录默认为未获得，无科技仍可按身份及特殊版本筛选', () => {
    const unknown = {...row, technology: null, owned: false, status: 'unowned' as const, variant: 'mu' as const}
    expect(matches(unknown, {status: ['unowned']}, '', 'all', 'all')).toBe(true)
    expect(matches(unknown, {nation: ['mu']}, '', 'none', 'all')).toBe(true)
    expect(matches(unknown, {attr: ['1']}, '', 'none', 'all')).toBe(false)
  })
  it('快捷编辑立即显示独立阶段，并使用服务端持久化的等级端点', () => {
    const completed = preview({...row, level: 105, lv120: false, status: 'unfinished', completion_target: 120},
      {group_type: row.group_type, toggle: 'complete', target: true})
    expect([completed.owned, completed.lv120, completed.level]).toEqual([true, true, 120])
    const restored = preview({...completed, completion_target: 105}, {group_type: row.group_type, toggle: 'complete', target: false})
    expect([restored.owned, restored.lv120, restored.level]).toEqual([true, false, 105])
    expect(preview(row, {group_type: row.group_type, toggle: 'owned', target: false}).status).toBe('unowned')
    expect(preview({...row, technology: null}, {group_type: row.group_type, toggle: 'complete', target: false}).level).toBe(100)
  })
})
