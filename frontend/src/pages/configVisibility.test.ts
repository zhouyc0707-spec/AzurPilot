import { expect, it } from 'vitest'
import { isFieldVisible, isSeasonGroupVisible } from './configVisibility'

it('存储空间按旧版规则隐藏空字典，保留非空状态及其中的零值', () => {
  const field = {type: 'storage', display: 'disabled', value: {}}
  expect(isFieldVisible('Storage', field, {})).toBe(false)
  expect(isFieldVisible('Storage', field, {count: 0, enabled: false})).toBe(true)
  expect(isFieldVisible('Storage', {...field, display: 'hide'}, {count: 1})).toBe(false)
  expect(isFieldVisible('NextRun', {type: 'datetime', value: ''}, '')).toBe(true)
})

it('季节生产页面仅展示当前全局季节，不受其他季节已保存目标影响', () => {
  const seasons = {spring: 'IslandSeasonSpring', summer: 'IslandSeasonSummer', autumn: 'IslandSeasonAutumn', winter: 'IslandSeasonWinter'}
  for (const [season, activeGroup] of Object.entries(seasons)) {
    for (const group of Object.values(seasons)) {
      expect(isSeasonGroupVisible('IslandSeasonalProduction', group, season)).toBe(group === activeGroup)
    }
  }
  expect(isSeasonGroupVisible('IslandSeasonalProduction', 'IslandSeasonWinter', null)).toBe(false)
})

it('季节可见性只约束季节目标页，保留其他页面和公共配置组', () => {
  expect(isSeasonGroupVisible('IslandPlan', 'IslandPlan', 'winter')).toBe(true)
  expect(isSeasonGroupVisible('IslandFarm', 'IslandOrchard', 'winter')).toBe(true)
  expect(isSeasonGroupVisible('IslandSeasonalProduction', 'OtherSettings', 'winter')).toBe(true)
})
