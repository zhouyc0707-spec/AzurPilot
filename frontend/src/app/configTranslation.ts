/** 配置翻译的选项键可以包含小数点，不能把 0.04 拆成两层对象。 */
export function translateConfig(translations: unknown, key: string): string {
  const parts = key.split('.')
  let value = translations
  for (let index = 0; index < parts.length; index++) {
    if (!value || typeof value !== 'object') {
      value = undefined
      break
    }
    const entries = value as Record<string, unknown>
    const remaining = parts.slice(index).join('.')
    if (Object.prototype.hasOwnProperty.call(entries, remaining)) {
      value = entries[remaining]
      break
    }
    value = Object.prototype.hasOwnProperty.call(entries, parts[index]) ? entries[parts[index]] : undefined
  }
  return typeof value === 'string' && value !== key
    ? value
    : parts.filter(item => item !== 'name' && item !== '_info').at(-1) ?? key
}
