import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import {
  LogLine,
  LOG_LINE_RE,
  RULE_RE,
  PURE_RULE_RE,
  CENTER_TITLE_RE,
  LOG_ENTRY_LIMIT,
  LINE_HEIGHT_ESTIMATE,
  buildLineLayout,
  mergeLogEntries,
  queueLogEvent,
  prepareLogSnapshot,
  advanceLogEventCursor,
  type LogBufferState,
} from './LogPanel'

describe('LogPanel 日志解析与渲染', () => {
  it('超大历史日志在进入 React state 前截断到客户端上限', () => {
    const entries = Array.from({length: 42_000}, (_, index) => ({
      id: index + 1,
      level: 'INFO',
      text: `INFO 2026-09-22 00:00:00 │ 日志 ${index + 1}`,
    }))

    const retained = mergeLogEntries([], entries, true)
    expect(retained).toHaveLength(LOG_ENTRY_LIMIT)
    expect(retained[0].id).toBe(42_000 - LOG_ENTRY_LIMIT + 1)
    expect(retained.at(-1)?.id).toBe(42_000)
  })

  it('增量合并去重、排序且始终保持渲染上限', () => {
    const previous = Array.from({length: LOG_ENTRY_LIMIT}, (_, index) => ({
      id: index + 1, level: 'INFO', text: `旧日志 ${index + 1}`,
    }))
    const incoming = [
      {id: LOG_ENTRY_LIMIT, level: 'WARNING', text: '重复条目以新值为准'},
      {id: LOG_ENTRY_LIMIT + 2, level: 'INFO', text: '后到但编号更大'},
      {id: LOG_ENTRY_LIMIT + 1, level: 'INFO', text: '新日志'},
    ]

    const retained = mergeLogEntries(previous, incoming)
    expect(retained).toHaveLength(LOG_ENTRY_LIMIT)
    expect(retained[0].id).toBe(3)
    expect(retained.at(-1)?.id).toBe(LOG_ENTRY_LIMIT + 2)
    expect(retained.find(entry => entry.id === LOG_ENTRY_LIMIT)?.level).toBe('WARNING')
  })

  it('正确识别 level=0 居中标题与纯双分割线', () => {
    const doubleRule = '═'.repeat(60)
    expect(PURE_RULE_RE.test(doubleRule)).toBe(true)

    const centerTitleText = ' '.repeat(20) + 'COMMISSION' + ' '.repeat(20)
    expect(CENTER_TITLE_RE.test(centerTitleText)).toBe(true)

    // 单行居中渲染
    const htmlCenter = renderToStaticMarkup(
      <LogLine entry={{ id: 2, level: 'INFO', text: centerTitleText }} search="" />
    )
    expect(htmlCenter).toContain('log-center-title')
    expect(htmlCenter).toContain('center-title-text')
    expect(htmlCenter).toContain('COMMISSION')
    expect(htmlCenter).not.toContain('rule-bar')
  })

  it('在空格被剥离时，仍可通过夹心上下文将 level=0 标题居中', () => {
    // 假设极端情况下前后端空格丢失，只有 "COMMISSION"
    const htmlCenterByContext = renderToStaticMarkup(
      <LogLine entry={{ id: 2, level: 'INFO', text: 'COMMISSION' }} search="" isCenter={true} />
    )
    expect(htmlCenterByContext).toContain('log-center-title')
    expect(htmlCenterByContext).toContain('center-title-text')
    expect(htmlCenterByContext).toContain('COMMISSION')
  })

  it('level=1 与 level=2 带线标题正确渲染两端横线', () => {
    const level1Text = '═'.repeat(20) + ' COMMISSION ' + '═'.repeat(20)
    expect(RULE_RE.test(level1Text)).toBe(true)
    const htmlLevel1 = renderToStaticMarkup(
      <LogLine entry={{ id: 1, level: 'INFO', text: level1Text }} search="" />
    )
    expect(htmlLevel1).toContain('rule-double')
    expect(htmlLevel1).toContain('rule-title')
    expect(htmlLevel1).toContain('rule-bar')

    const level2Text = '─'.repeat(20) + ' SUB_STAGE ' + '─'.repeat(20)
    expect(RULE_RE.test(level2Text)).toBe(true)
    const htmlLevel2 = renderToStaticMarkup(
      <LogLine entry={{ id: 2, level: 'INFO', text: level2Text }} search="" />
    )
    expect(htmlLevel2).toContain('rule-single')
    expect(htmlLevel2).toContain('rule-title')
    expect(htmlLevel2).toContain('rule-bar')
  })

  it('标准日志行正确渲染级别、时间戳和分隔符', () => {
    const line = 'INFO     2026-09-13 23:24:47.008 │ <<< HR3 >>>'
    expect(LOG_LINE_RE.test(line)).toBe(true)
    const html = renderToStaticMarkup(
      <LogLine entry={{ id: 3, level: 'INFO', text: line }} search="" />
    )
    expect(html).toContain('lvl-info')
    expect(html).toContain('2026-09-13 23:24:47.008')
    expect(html).toContain('log-divider')
    expect(html).toContain('hl-title')
  })

  it('中文方括号标签与英文标签同样按属性着色', () => {
    const line = 'INFO     2026-09-13 23:24:47.008 │ [配置] 已保存 ./config\ap.json'
    const html = renderToStaticMarkup(
      <LogLine entry={{ id: 4, level: 'INFO', text: line }} search="" />
    )
    expect(html).toContain('hl-attr')
    expect(html).toContain('hl-path')
  })

  it('普通代码缩进或 Traceback 不会误判为居中标题', () => {
    const indentedCode = '    def some_function():'
    expect(CENTER_TITLE_RE.test(indentedCode)).toBe(false)
    const html = renderToStaticMarkup(
      <LogLine entry={{ id: 4, level: 'INFO', text: indentedCode }} search="" />
    )
    expect(html).not.toContain('log-center-title')
    expect(html).toContain('log-raw')
  })

  it('居中标题支持搜索高亮', () => {
    const centerTitleText = ' '.repeat(20) + 'START_TASK' + ' '.repeat(20)
    const html = renderToStaticMarkup(
      <LogLine entry={{ id: 5, level: 'INFO', text: centerTitleText }} search="start" />
    )
    expect(html).toContain('log-center-title')
    expect(html).toContain('log-search-match')
    expect(html).toContain('START')
  })

  describe('流式日志微批处理缓冲队列 (queueLogEvent)', () => {
    it('短时间多批次事件连续累加且更新最新 cursor', () => {
      const buffer: LogBufferState = { entries: [], reset: false, cursor: null }
      queueLogEvent(buffer, {
        entries: [{ id: 1, level: 'INFO', text: '行 1' }],
        cursor: 10,
      })
      queueLogEvent(buffer, {
        entries: [
          { id: 2, level: 'INFO', text: '行 2' },
          { id: 3, level: 'INFO', text: '行 3' },
        ],
        cursor: 12,
      })

      expect(buffer.entries).toHaveLength(3)
      expect(buffer.entries.map(e => e.id)).toEqual([1, 2, 3])
      expect(buffer.reset).toBe(false)
      expect(buffer.cursor).toBe(12)
    })

    it('收到 reset=true 时清空此前缓冲日志并标记 reset，后续同批追加', () => {
      const buffer: LogBufferState = {
        entries: [
          { id: 1, level: 'INFO', text: '将被丢弃的旧缓冲' },
          { id: 2, level: 'INFO', text: '将被丢弃的旧缓冲' },
        ],
        reset: false,
        cursor: 5,
      }

      // 收到 reset
      queueLogEvent(buffer, {
        entries: [{ id: 10, level: 'INFO', text: '重置后的第一条' }],
        reset: true,
        cursor: 20,
      })
      expect(buffer.reset).toBe(true)
      expect(buffer.entries).toHaveLength(1)
      expect(buffer.entries[0].id).toBe(10)
      expect(buffer.cursor).toBe(20)

      // 同一动画帧内后续到达的事件继续追加
      queueLogEvent(buffer, {
        entries: [{ id: 11, level: 'INFO', text: '紧随重置后的第二条' }],
        reset: false,
        cursor: 21,
      })
      expect(buffer.reset).toBe(true)
      expect(buffer.entries).toHaveLength(2)
      expect(buffer.entries.map(e => e.id)).toEqual([10, 11])
      expect(buffer.cursor).toBe(21)
    })
  })

  it('行高布局用实测均高占位未渲染行，偏移逐行累加', () => {
    const entries = [
      {id: 1, level: 'INFO', text: '第一行'},
      {id: 2, level: 'INFO', text: '第二行'},
      {id: 3, level: 'INFO', text: '第三行'},
    ]
    const measured = new Map([[1, 40], [2, 60]])

    const layout = buildLineLayout(entries, measured, LINE_HEIGHT_ESTIMATE)

    expect(layout.heights).toEqual([40, 60, 50])
    expect(layout.offsets).toEqual([0, 40, 100])
    expect(layout.total).toBe(150)
  })

  it('一条都没实测时用固定估值占位', () => {
    const entries = [{id: 1, level: 'INFO', text: '唯一行'}]

    const layout = buildLineLayout(entries, new Map(), LINE_HEIGHT_ESTIMATE)

    expect(layout.heights).toEqual([LINE_HEIGHT_ESTIMATE])
    expect(layout.total).toBe(LINE_HEIGHT_ESTIMATE)
  })
})


describe('隐藏日志的有界微批缓冲', () => {
  const line = (id: number) => ({id, text: `日志 ${id}`, level: 'INFO'})
  it('长时间隐藏后只保留窗口上限，恢复时一次合并得到最新窗口', () => {
    const previous = Array.from({length: 1000}, (_, index) => line(index + 1))
    const buffer: LogBufferState = {entries: [], reset: false, cursor: null}
    for (let id = 1001; id <= 3200; id++) queueLogEvent(buffer, {entries: [line(id)], cursor: id})
    expect(buffer.entries).toHaveLength(LOG_ENTRY_LIMIT)
    const resumed = mergeLogEntries(previous, buffer.entries, buffer.reset)
    expect(resumed.map(entry => entry.id)).toEqual(Array.from({length: 1000}, (_, index) => index + 2201))
  })

  it('隐藏期间重置与重叠推送在恢复后正确覆盖，不混入旧实例日志', () => {
    const buffer: LogBufferState = {entries: [line(80)], reset: false, cursor: 80}
    queueLogEvent(buffer, {entries: [line(1)], cursor: 1, reset: true})
    queueLogEvent(buffer, {entries: [{...line(1), text: '最新'}, line(2)], cursor: 2})
    expect(mergeLogEntries([line(79)], buffer.entries, buffer.reset)).toEqual([{...line(1), text: '最新'}, line(2)])
    expect(buffer.cursor).toBe(2)
  })
})


describe('日志恢复快照与推送竞态', () => {
  const line = (id: number, text = `日志 ${id}`) => ({id, text, level: 'INFO'})
  const snapshot = (cursor: number, entries: ReturnType<typeof line>[]) => ({instance: 'alas', cursor, entries, reset: true})
  it('同代全量快照 reset=true 不清除已有400条以外的窗口', () => {
    const result = prepareLogSnapshot([line(1), line(500)], snapshot(501, [line(500), line(501)]), {
      requestCursor: 500, currentCursor: 500, newerEvent: false, generationChanged: false,
    })
    expect(result).toEqual({entries: [line(501)], reset: false, cursor: 501})
  })
  it('服务重启造成游标回退时清除旧代，即使有相同id', () => {
    const result = prepareLogSnapshot([line(1, '旧'), line(500)], snapshot(2, [line(1, '新'), line(2)]), {
      requestCursor: 500, currentCursor: 500, newerEvent: false, generationChanged: false,
    })
    expect(result).toEqual({entries: [line(1, '新'), line(2)], reset: true, cursor: 2})
  })
  it('快照落后较晚推送时只补旧记录，保留新推送内容和游标', () => {
    const result = prepareLogSnapshot([line(502, '较晚推送')], snapshot(502, [line(501), line(502, '旧快照')]), {
      requestCursor: 500, currentCursor: 503, newerEvent: true, generationChanged: false,
    })
    expect(result).toEqual({entries: [line(501)], reset: false, cursor: 503})
  })
  it('推送已确认新代后，丢弃仍在路上的上一代快照', () => {
    expect(prepareLogSnapshot([line(1)], snapshot(500, [line(499), line(500)]), {
      requestCursor: 500, currentCursor: 1, newerEvent: true, generationChanged: true,
    })).toBeNull()
  })
  it('存在较晚推送时，旧于请求起点的快照不能反向重置视图', () => {
    expect(prepareLogSnapshot([line(501)], snapshot(100, [line(100)]), {
      requestCursor: 500, currentCursor: 501, newerEvent: true, generationChanged: false,
    })).toBeNull()
  })
})


describe('快照与逐条事件的独立游标', () => {
  const line = (id: number) => ({id, text: `日志 ${id}`, level: 'INFO'})
  it('快照先到102，事件101/102/103后到时不重置历史或清空位置', () => {
    let state = {cursor: 102, eventCursor: 100}
    let entries = Array.from({length: 102}, (_, index) => line(index + 1))
    const buffer: LogBufferState = {entries: [], reset: false, cursor: null}
    for (const cursor of [101, 102, 103]) {
      const next = advanceLogEventCursor(state, {cursor, reset: false})
      expect(next.restarted).toBe(false)
      expect(next.reset).toBe(false)
      expect(next.cursor).toBeGreaterThanOrEqual(102)
      queueLogEvent(buffer, {entries: [line(cursor)], reset: next.reset, cursor: next.cursor})
      state = next
    }
    entries = mergeLogEntries(entries, buffer.entries, buffer.reset)
    expect(entries).toHaveLength(103)
    expect(entries[0].id).toBe(1)
    expect(buffer.cursor).toBe(103)
  })
  it('事件流自身从500回退到2时重置历史与最大游标', () => {
    expect(advanceLogEventCursor({cursor: 502, eventCursor: 500}, {cursor: 2, reset: true})).toEqual({
      cursor: 2, eventCursor: 2, reset: true, restarted: true,
    })
  })
  it('正常窗口缺口的reset仍生效，但不误判为换代', () => {
    expect(advanceLogEventCursor({cursor: 500, eventCursor: 100}, {cursor: 550, reset: true})).toEqual({
      cursor: 550, eventCursor: 550, reset: true, restarted: false,
    })
  })
  it('快照确认换代后清空事件游标，稍后较旧新代增量不会再次清空窗口', () => {
    expect(advanceLogEventCursor({cursor: 10, eventCursor: null}, {cursor: 9, reset: false})).toEqual({
      cursor: 10, eventCursor: 9, reset: false, restarted: false,
    })
  })
})
