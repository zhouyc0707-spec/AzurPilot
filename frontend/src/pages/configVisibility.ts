/**
 * @fileoverview 配置字段可见性判断辅助逻辑。
 */

import type { Field, Value } from '../api/types'

const islandSeasonGroups: Record<string, string> = {
  IslandSeasonSpring: 'spring',
  IslandSeasonSummer: 'summer',
  IslandSeasonAutumn: 'autumn',
  IslandSeasonWinter: 'winter',
}

/** 季节目标分季保存，页面仅展示全局配置选定的季节。 */
export function isSeasonGroupVisible(task: string, group: string, season: Value) {
  if (task !== 'IslandSeasonalProduction' || !(group in islandSeasonGroups)) return true
  return islandSeasonGroups[group] === season
}

export function isFieldVisible(argument: string, field: Field, value: Value) {
  if (argument === '_info' || field.display === 'hide') return false
  // 沿用旧版 put_arg_storage：空字典不生成字段，也不生成空分组或导航。
  if (field.type === 'storage' && value !== null && typeof value === 'object' && !Array.isArray(value) && !Object.keys(value).length) return false
  return true
}
