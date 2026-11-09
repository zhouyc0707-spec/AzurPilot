import { afterEach, describe, expect, it, vi } from 'vitest'
import {readThemePreference, themeFamilyTarget} from './theme'

afterEach(() => vi.unstubAllGlobals())

function storage(saved: Record<string, string>) {
  vi.stubGlobal('localStorage', {getItem: (key: string) => saved[key] ?? null})
}

describe('新旧 UI 切换（顶栏快捷按钮）', () => {
  it('新版按当前明暗切到对应旧版主题', () => {
    expect(themeFamilyTarget({theme: 'light', resolvedMode: 'light'})).toBe('legacy-light')
    expect(themeFamilyTarget({theme: 'dark', resolvedMode: 'dark'})).toBe('legacy-dark')
    // 简约/紧凑的明暗由解析结果决定，切换后也应落到对应的旧版主题
    expect(themeFamilyTarget({theme: 'minimal', resolvedMode: 'dark'})).toBe('legacy-dark')
    expect(themeFamilyTarget({theme: 'extreme', resolvedMode: 'light'})).toBe('legacy-light')
  })
  it('旧版切回记住的新版主题，没有记录时按明暗回退', () => {
    expect(themeFamilyTarget({theme: 'legacy-light', resolvedMode: 'light'}, 'minimal')).toBe('minimal')
    expect(themeFamilyTarget({theme: 'legacy-dark', resolvedMode: 'dark'}, 'extreme')).toBe('modern-light')
    expect(themeFamilyTarget({theme: 'legacy-light', resolvedMode: 'light'})).toBe('modern-light')
    expect(themeFamilyTarget({theme: 'legacy-dark', resolvedMode: 'dark'})).toBe('modern-dark')
  })
  it('记住的值若本身是旧版主题则忽略，避免来回切不动', () => {
    expect(themeFamilyTarget({theme: 'legacy-light', resolvedMode: 'light'}, 'legacy-dark')).toBe('modern-light')
  })
})

describe('主题偏好恢复', () => {
  it.each([
    ['dark', 'auto', 'modern-dark'],
    ['legacy-dark', 'auto', 'legacy-dark'],
    ['minimal', 'dark', 'minimal'],
    ['extreme', 'terminal', 'modern-dark'],
    ['extreme', 'retro-blue', 'modern-light'],
    ['minimal', 'auto', 'minimal'],
    ['light', 'dark', 'modern-light'],
    ['unknown', 'dark', 'modern-light'],
  ])('把 %s / %s 恢复为 %s', (theme, colorMode, expected) => {
    storage({'azurpilot.theme': theme, 'azurpilot.color-mode': colorMode})
    expect(readThemePreference().theme).toBe(expected)
  })

  it('恢复已保存的材质、主题模式与自定义配色', () => {
    const custom = {id: 'custom:one', primary: '#123456', secondary: '#654321'}
    storage({
      'azurpilot.theme': 'minimal',
      'azurpilot.material': 'glass',
      'azurpilot.color-mode': 'dark',
      'azurpilot.palette': custom.id,
      'azurpilot.custom-palettes': JSON.stringify([custom]),
    })
    expect(readThemePreference()).toMatchObject({theme: 'minimal', material: 'glass', colorMode: 'dark', palette: custom.id, customPalettes: [custom]})
  })

  it('偏好非法或存储不可用时使用默认值', () => {
    storage({
      'azurpilot.material': 'invalid',
      'azurpilot.color-mode': 'invalid',
      'azurpilot.palette': 'custom:missing',
      'azurpilot.custom-palettes': '{损坏的数据',
    })
    const expected = {theme: 'modern-light', material: 'plain', colorMode: 'auto', palette: 'ocean', customPalettes: []}
    expect(readThemePreference()).toMatchObject(expected)
    vi.stubGlobal('localStorage', {getItem: () => {throw new Error('存储不可用')}})
    expect(readThemePreference()).toMatchObject(expected)
  })
})
