import { describe, expect, it } from 'vitest'
import { detectLanguage, translateUi } from './i18n'

describe('WebUI i18n', () => {
  it('单次任务操作在全部语言中有完整文案并带目标名称', () => {
    const keys = ['task.runOnce', 'task.stopOnce', 'task.runOnceStarted', 'task.runOnceStopped',
      'task.runOnceStatus', 'task.standaloneStatus', 'task.stopStandalone', 'task.stopped', 'task.stopTool', 'task.stopRequested'] as const
    for (const language of ['zh-CN', 'zh-TW', 'en-US', 'ja-JP', 'zh-MIAO'] as const) {
      for (const key of keys) {
        const text = translateUi(language, key, {task: 'TARGET'})
        expect(text).not.toContain('task.')
        expect(text).not.toContain('{task}')
      }
      expect(translateUi(language, 'task.runOnce', {task: 'TARGET'})).toContain('TARGET')
      expect(translateUi(language, 'task.stopOnce', {task: 'TARGET'})).toContain('TARGET')
    }
  })

  it('detects supported browser locales', () => {
    expect(detectLanguage(['zh-HK'])).toBe('zh-TW')
    expect(detectLanguage(['ja'])).toBe('ja-JP')
    expect(detectLanguage(['en-GB'])).toBe('en-US')
    expect(detectLanguage(['fr-FR'])).toBe('zh-CN')
  })

  it('interpolates translated values', () => {
    expect(translateUi('en-US', 'instance.deletePrompt', {name: 'alas-main'})).toBe('Delete alas-main? Its configuration will remain in backup.')
  })

  it('offers a different delete-instance warning at each of the three confirmations', () => {
    const prompts = ['instance.deletePrompt', 'instance.deletePrompt2', 'instance.deletePrompt3'] as const
    for (const language of ['zh-CN', 'zh-TW', 'en-US', 'ja-JP', 'zh-MIAO'] as const) {
      const texts = prompts.map(key => translateUi(language, key, {name: 'alas-main'}))
      expect(new Set(texts).size).toBe(prompts.length)
      /* 键不存在时 translateUi 会把键名原样返回，那样三条也「互不相同」，所以这里必须排除。 */
      expect(texts.some(text => text.includes('instance.deletePrompt'))).toBe(false)
    }
  })

  it('translates the Miao locale instead of falling back to Simplified Chinese', () => {
    for (const key of ['nav.statistics', 'common.retry', 'dashboard.fitCards'] as const) {
      expect(translateUi('zh-MIAO', key)).toContain('喵')
    }
  })

  it('keeps Japanese and Traditional Chinese dictionaries complete for formerly missing UI keys', () => {
    expect(translateUi('ja-JP', 'log.search')).toBe('ログを検索')
    expect(translateUi('ja-JP', 'resource.Oil')).toBe('燃料')
    expect(translateUi('zh-TW', 'fleet.vanguard')).toBe('先鋒艦隊')
    expect(translateUi('zh-TW', 'stats.toolboxSave')).toBe('儲存圖表')
    expect(translateUi('zh-TW', 'stats.axisMode')).toBe('座標軸')
    expect(translateUi('ja-JP', 'stats.axisMode')).toBe('軸モード')
    expect(translateUi('en-US', 'stats.axisMode')).toBe('Axis mode')
    expect(translateUi('zh-CN', 'stats.axisMode')).toBe('坐标轴')
    expect(translateUi('zh-TW', 'stats.candlestickOverlay')).toBe('K 線 + 折線疊加')
    expect(translateUi('ja-JP', 'stats.candlestickOverlay')).toBe('ローソク足 + 折れ線')
    expect(translateUi('en-US', 'stats.candlestickOverlay')).toBe('Candlestick + line overlay')
    expect(translateUi('zh-CN', 'stats.candlestickOverlay')).toBe('K 线 + 折线叠加')
  })


  it('translates developer playground UI instead of leaving hardcoded labels', () => {
    expect(translateUi('en-US', 'developer.pageTitle')).toBe('Developer · Control Preview')
    expect(translateUi('ja-JP', 'developer.formControls')).toBe('フォームコントロール')
    expect(translateUi('zh-TW', 'developer.emptyTitle')).toBe('暫無內容')
  })

  it('translates lock audit states and zero-score action summaries in every language', () => {
    const keys = ['meow.actionSummary', 'meow.lockActions', 'meow.lockName', 'meow.lockBefore', 'meow.lockAfter',
      'meow.lockResult', 'meow.lockReason', 'meow.locked', 'meow.unlocked', 'meow.lockUnknown',
      'meow.lockChanged', 'meow.lockUnchanged', 'meow.lockSkipped', 'meow.lockUnconfirmed'] as const
    for (const language of ['zh-CN', 'zh-TW', 'en-US', 'ja-JP', 'zh-MIAO'] as const) {
      for (const key of keys) {
        const text = translateUi(language, key, {count: 0, actions: 2, time: 'T'})
        expect(text).not.toContain('meow.')
        expect(text).not.toContain('{')
      }
    }
    expect(translateUi('zh-CN', 'meow.actionSummary', {count: 0, actions: 2, time: 'T'})).toBe('评分 0 只 · 最近 2 条处理 · 生成时间 T')
  })
})
