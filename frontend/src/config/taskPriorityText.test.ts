import {describe, expect, it} from 'vitest'
import {taskPriorityText, translatedTaskPriority} from './taskPriorityText'

const names: Record<string, string> = {Restart: '重启', Commission: '委托', Tactical: '战术学院'}
const t = (key: string) => names[key.split('.')[1]!] ?? key.split('.')[1]!

describe('任务优先级翻译注释与调度标识分离', () => {
  it('显示已有任务翻译，未知任务保留原标识', () => {
    expect(translatedTaskPriority('Restart > Commission\n> Unknown', t))
      .toBe('Restart # 重启\n> Commission # 委托\n> Unknown')
  })

  it('移动带翻译的整行后，保存英文标识并保留新顺序', () => {
    const text = '> Tactical # 战术学院\nRestart # 重启\n> Commission # 委托'
    expect(taskPriorityText(text)).toBe('Tactical\n> Restart\n> Commission')
  })

  it('兼容既有的全角箭头、空行、多个任务与注释语法', () => {
    expect(taskPriorityText('# 备注\r\nRestart ＞ Commission › Tactical # 旧注释\n\n> Unknown'))
      .toBe('Restart\n> Commission\n> Tactical\n> Unknown')
    for (const arrow of '＞﹥›˃ᐳ❯') {
      expect(taskPriorityText(`Restart ${arrow} Commission`)).toBe('Restart\n> Commission')
    }
  })

  it('同名翻译不合并任务，反复展示也不会累加注释', () => {
    const sameName = () => '相同显示名称'
    const raw = 'Main\n> Main2'
    const display = translatedTaskPriority(raw, sameName)
    expect(taskPriorityText(display)).toBe(raw)
    expect(translatedTaskPriority(display, sameName)).toBe(display)
  })

  it('没有翻译或空内容时保持可编辑的原标识', () => {
    expect(translatedTaskPriority('Commission', () => 'Commission')).toBe('Commission')
    expect(translatedTaskPriority('', t)).toBe('')
  })

  it('仅改变翻译注释不改变配置，已有重复项仍交给原调度解析处理', () => {
    expect(taskPriorityText('Commission # 任意备注\n> Commission # 委托'))
      .toBe('Commission\n> Commission')
    expect(taskPriorityText(translatedTaskPriority('Commission\n> Commission', t)))
      .toBe('Commission\n> Commission')
  })
})
