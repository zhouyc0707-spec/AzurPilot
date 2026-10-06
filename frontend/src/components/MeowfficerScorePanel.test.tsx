import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { AppContext, type AppContextValue } from '../app/context'
import { translateUi } from '../i18n'
import type { MeowfficerScoreReport } from '../api/types'
import { MeowfficerScoreList, splitHit, tierClass } from './MeowfficerScorePanel'

const context = {ui: (key: Parameters<AppContextValue['ui']>[0], params?: Parameters<AppContextValue['ui']>[1]) => translateUi('zh-CN', key, params)} as AppContextValue
const render = (report: MeowfficerScoreReport) => renderToStaticMarkup(
  <AppContext.Provider value={context}><MeowfficerScoreList report={report}/></AppContext.Provider>,
)

const report: MeowfficerScoreReport = {
  instance: 'test', generatedAt: '2026-09-20 11:44:26', count: 1,
  cats: [{
    source: 'shot_0.png', cat: '克雷喵', tags: ['SSR', '铁血'], fixed: true, maxed: true, pointsSpent: 6,
    level: 30,
    note: '<b>初始池小</b>、最好毕业',
    talents: [
      {name: '狼群之首', level: 1, kind: 'special'},
      {name: '装填新手·潜艇', level: 3, kind: 'normal', inferred: true},
    ],
    rubrics: [
      {key: 'submarine', label: '潜艇猫', tier: '准毕业', score: 100, x: 1, y: 6,
        xHits: ['狼群之首 Lv1'], yHits: ['雷击长·潜艇 Lv3'], notes: ['缺 侵略如火'],
        source: '28法则执行篇·潜艇猫', primary: true},
      {key: 'low_cost', label: '低耗猫', tier: '不适合低耗', score: 35, x: 0, y: 2, xHits: [], yHits: [], primary: false},
    ],
    advice: {
      verdict: 'reroll', headline: '建议洗点，别直接喂',
      reason: '主口径「潜艇猫」45/100（过渡可用）。已经投入过 6 点天赋，直接喂掉这些投入就一起没了',
      label: '潜艇猫', score: 45, tier: '过渡可用',
      cost: 12000, costEstimated: true, pointsSpent: 6,
      costText: '推算已点 6 点，约需 12000 物资',
      targets: ['优先补：侵略如火', '普通位可补：新晋指挥官·潜艇'],
    },
  }],
}

describe('指挥喵评分面板', () => {
  it('按档位关键词上色，准毕业不会被毕业级抢先匹配', () => {
    expect(tierClass('准毕业')).toBe('is-near')
    expect(tierClass('毕业级')).toBe('is-graduate')
    expect(tierClass('零食（建议喂掉）')).toBe('is-snack')
    expect(tierClass('没见过的档位')).toBe('is-other')
  })

  it('把命中标签里的等级拆成徽章', () => {
    expect(splitHit('雷击长·潜艇 Lv3')).toEqual({name: '雷击长·潜艇', level: 3})
    expect(splitHit('狼群之首')).toEqual({name: '狼群之首', level: 0})
  })

  it('展示主口径的档位、参考分、命中标签与说明', () => {
    const html = render(report)
    expect(html).toContain('克雷喵')
    expect(html).toContain('准毕业')
    expect(html).toContain('x + y = 1 + 6.0')
    expect(html).toContain('雷击长·潜艇')
    expect(html).toContain('Ⅲ')
    expect(html).toContain('缺 侵略如火')
    expect(html).toContain('依据：28法则执行篇·潜艇猫')
    expect(html).toContain('已满级')
    expect(html).toContain('已指定')
    expect(html).toContain('来源截图：shot_0.png')
    expect(html).toContain('Lv30')
  })

  it('彩天赋高亮、推断天赋标注，其余口径折叠展示', () => {
    const html = render(report)
    expect(html).toContain('meow-talent is-special')
    expect(html).toContain('meow-talent is-inferred')
    expect(html).toContain('推断条目，请核对')
    expect(html).toContain('<details class="meow-others">')
    expect(html).toContain('其他口径（1）')
    expect(html).toContain('不适合低耗')
  })

  it('后端文案按纯文本渲染，不解析其中的标签', () => {
    const html = render(report)
    expect(html).toContain('&lt;b&gt;初始池小&lt;/b&gt;')
    expect(html).not.toContain('<b>')
  })

  it('渲染洗点推荐：结论、成本与建议补的天赋，并按 verdict 上色', () => {
    const html = render(report)
    expect(html).toContain('meow-advice is-reroll')
    expect(html).toContain('建议洗点，别直接喂')
    expect(html).toContain('推算已点 6 点，约需 12000 物资')
    expect(html).toContain('优先补：侵略如火')
    expect(html).toContain('普通位可补：新晋指挥官·潜艇')
  })

  it('没有推荐时整个区块不渲染', () => {
    const withoutAdvice: MeowfficerScoreReport = {
      ...report,
      cats: [{...report.cats[0], advice: null}],
    }
    expect(render(withoutAdvice)).not.toContain('meow-advice')
  })

  it('没有评分时仍展示蓝猫与失败项的锁状态记录，同名猫分别保留', () => {
    const html = render({instance: 'test', generatedAt: 'T', count: 0, cats: [], lockActions: [
      {name: '蓝猫', before: true, after: false, target: false, status: 'changed', reason: '蓝猫不评分'},
      {name: '蓝猫', before: false, after: false, target: false, status: 'unchanged', reason: '已经未锁定'},
      {name: '未知猫', before: null, after: null, target: null, status: 'skipped', reason: '身份未确认'},
      {name: '故障猫', before: true, after: null, target: false, status: 'unconfirmed', reason: '切换后断开'},
    ]})
    expect(html).toContain('锁定／解锁处理记录')
    expect(html).toContain('原状态')
    expect(html).toContain('确认后状态')
    expect(html.match(/<td>蓝猫<\/td>/g)).toHaveLength(2)
    for (const text of ['已锁定', '未锁定', '未确认', '已修改并确认', '已符合目标', '未操作', '切换后未确认', '蓝猫不评分', '身份未确认']) {
      expect(html).toContain(text)
    }
    expect(html).not.toContain('meow-card')
  })

  it('操作记录的猫名和失败原因按纯文本显示', () => {
    const html = render({instance: 'test', generatedAt: 'T', count: 0, cats: [], lockActions: [
      {name: '<b>自定义名</b>', before: null, after: null, target: null, status: 'skipped', reason: '<script>失败</script>'},
    ]})
    expect(html).toContain('&lt;b&gt;自定义名&lt;/b&gt;')
    expect(html).toContain('&lt;script&gt;失败&lt;/script&gt;')
    expect(html).not.toContain('<script>')
  })

  it('旧报告没有动作记录时不增加操作表格', () => {
    expect(render(report)).not.toContain('meow-lock-actions')
  })
})
