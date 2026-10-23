import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { AkashiShopRecordResult } from '../api/types'
import { AkashiShopRecordView } from './AkashiShopRecords'

const data: AkashiShopRecordResult = {
  instance: 'demo', scope: 'monthly', month: '2026-10', record: {
    totalAp: 1340, boxes: {'100': 10, '20': 2, '50': 6}, time: '2026-10-23T10:00:00+08:00',
    month: '2026-10', server: 'cn', image: 'data:image/png;base64,aGVsbG8=',
  },
}
const render = (value = data) => renderToStaticMarkup(<AkashiShopRecordView data={value}/> )

describe('明石商店猫王纪录展示', () => {
  it('展示购买前在售总量、按箱型排序的明细、月份和完整截图', () => {
    const html = render()
    expect(html).toContain('1,340')
    expect(html).toContain('20 点行动力箱 × 2')
    expect(html.indexOf('20 点行动力箱')).toBeLessThan(html.indexOf('50 点行动力箱'))
    expect(html.indexOf('50 点行动力箱')).toBeLessThan(html.indexOf('100 点行动力箱'))
    expect(html).toContain('2026-10-23 10:00:00（UTC+08:00）')
    expect(html).toContain('2026-10')
    expect(html).toContain('src="data:image/png;base64,aGVsbG8="')
    expect(html).toContain('本月猫王完整商店截图')
    expect(html).toContain('平纪录时保留最新遇到的商店')
  })

  it('本月暂无记录时不把未知写成零，不显示历史截图', () => {
    const html = render({...data, record: null})
    expect(html).toContain('2026-10 暂无明石商店纪录。')
    expect(html).toContain('完整识别商品后，会自动记录')
    expect(html).not.toContain('在售行动力')
    expect(html).not.toContain('<img')
  })

  it('历史空纪录和历史纪录的标题区分于本月', () => {
    expect(render({...data, scope: 'history', record: null})).toContain('暂无历史明石商店纪录。')
    expect(render({...data, scope: 'history'})).toContain('历史猫王完整商店截图')
  })

  it('截图丢失时保留真实纪录并给出文件不可用提示', () => {
    const html = render({...data, record: {...data.record!, image: null, imageMissing: true}})
    expect(html).toContain('1,340')
    expect(html).toContain('纪录已保留，但截图文件暂不可用')
    expect(html).not.toContain('<img')
  })

  it('UTC 时间显示原时区，不受浏览器所在地区影响', () => {
    expect(render({...data, record: {...data.record!, time: '2026-10-23T02:00:00.123Z'}}))
      .toContain('2026-10-23 02:00:00（UTC）')
  })
})
