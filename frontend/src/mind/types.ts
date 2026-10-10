import type { Parameters } from '../api/generated'

export type MindShip = Parameters['mind.save']['ships'][number]
export type Rarity = 'N' | 'R' | 'SR' | 'SSR' | 'UR'
export interface CatalogShip {name: string; rarity: Rarity; base_rarity: Rarity; base_name: string; group: string; type: string}
export interface MindCatalog {updated_at: string; ships: CatalogShip[]}
export interface MindRow extends MindShip {rarity: Rarity | ''; base_rarity: Rarity | ''; group: string; base_name: string; status: 'included' | 'merged' | 'review' | 'excluded'; mind: number; gold: number}
export interface MindSummary {rarity: Rarity; stages: number[]; stage_mind: number[]; count: number; mind: number; gold: number}
export interface MindCalculation {ships: MindRow[]; summary: MindSummary[]; mind: number; gold: number; included: number; merged: number; excluded: number; review: number}
export interface MindReport extends MindCalculation {instance: string; revision: string; updated_at: string}

export function editableShip(ship: MindShip): MindShip {
  return {name: ship.name, level: ship.level, rarity: ship.rarity ?? '', base_rarity: ship.base_rarity ?? '',
    excluded: ship.excluded ?? false, review: ship.review ?? false, source: ship.source ?? ''}
}
