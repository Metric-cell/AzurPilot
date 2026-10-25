import {describe, expect, it, vi} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import type {DockReport} from '../api/types'
import {translateUi} from '../i18n'
import {FleetLedger} from './FleetLedger'

vi.mock('../app/context', () => ({
  useConnection: () => 'ready',
  useApp: () => ({ui: (key: Parameters<typeof translateUi>[1]) => translateUi('zh-CN', key), notify: vi.fn()}),
}))
const report: DockReport = {
  instance: 'test', revision: 'r1', updated_at: null, min_level: 1, max_level: 125,
  complete: false, review_count: 0, source: 'manual', pending_revision: 'p1', pending_changes: [],
  ships: [null, 0].map((extra_exp, index) => ({id: index + 1, name: '重复舰船', group_type: 100,
    level: 120, rarity: 'SSR', base_rarity: 'SSR', extra_exp, excluded: index === 1, review: false, source: 'manual'})),
}
function render(value: DockReport) {
  return renderToStaticMarkup(<FleetLedger instance="test" report={value} onReport={vi.fn()} onBusy={vi.fn()} onDirty={vi.fn()}/>)
}
describe('canonical per-card ledger', () => {
  it('keeps duplicate identities, NULL and zero EXP and exclusion controls distinct', () => {
    const html = render(report)
    expect(html).toContain('舰名 · 重复舰船 #1')
    expect(html).toContain('舰名 · 重复舰船 #2')
    expect(html).toMatch(/placeholder="NULL"[^>]*value=""/)
    expect(html).toMatch(/placeholder="NULL"[^>]*value="0"/)
    expect(html).toContain('排除心智计算 · 重复舰船 #2')
    expect(html).toContain('保存舰船数据')
  })
  it('requires an explicit choice for ambiguous duplicate matching and offers separate-card insertion', () => {
    const html = render({...report, pending_changes: [{id: 'diff1', kind: 'ship', operation: 'resolve',
      ship_id: null, group_type: 100, name: '重复舰船', before: null, after: {name: '重复舰船', extra_exp: null, level_toggle: {low: 100, high: 120}}, fields: ['name', 'extra_exp'],
      candidates: report.ships.map(({id, name, level}) => ({id, name, level})),
    }]})
    expect(html).toContain('请选择目标卡片')
    expect(html).toContain('新增独立卡片')
    expect(html).toContain('value="1"')
    expect(html).toContain('value="2"')
    expect(html).toMatch(/disabled="" aria-label="接受 diff1"/)
    expect(html).toContain('额外经验（未知留空）: 未知')
    expect(html).not.toContain('[object Object]')
    expect(html).toContain('科技等级切换: 切换前等级: 100 / 切换后等级: 120')
    expect(html).not.toContain('extra_exp:')
  })
})
