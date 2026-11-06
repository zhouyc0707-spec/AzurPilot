import {describe, expect, it} from 'vitest'
import {translateConfig} from './configTranslation'

describe('配置名称与含小数点的选项翻译', () => {
  const translations = {
    ExampleGroup: {
      _info: {name: '示例配置'},
      FieldsEfficiency: {name: '农田额外效率', '0': '0%', '0.04': '4%', '0.12': '12%'},
    },
  }

  it('保持普通组名和参数名的解析', () => {
    expect(translateConfig(translations, 'ExampleGroup._info.name')).toBe('示例配置')
    expect(translateConfig(translations, 'ExampleGroup.FieldsEfficiency.name')).toBe('农田额外效率')
  })

  it('数值选项的完整小数键显示百分比', () => {
    for (const [value, label] of [[0, '0%'], [0.04, '4%'], [0.12, '12%']] as const) {
      expect(translateConfig(translations, `ExampleGroup.FieldsEfficiency.${value}`)).toBe(label)
    }
  })

  it('未加载、缺失和原始占位文本沿用原回退', () => {
    expect(translateConfig(undefined, 'Unknown._info.name')).toBe('Unknown')
    expect(translateConfig(translations, 'Unknown.Value.help')).toBe('help')
    expect(translateConfig({A: {name: 'A.name'}}, 'A.name')).toBe('A')
    expect(translateConfig({}, 'constructor.name')).toBe('constructor')
  })
})
