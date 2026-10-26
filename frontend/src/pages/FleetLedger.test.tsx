// @vitest-environment jsdom
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest'
import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react'
import {MemoryRouter} from 'react-router-dom'
import {api} from '../api/client'
import type {DockReport} from '../api/types'
import type {MindCatalog} from '../mind/types'
import {translateUi} from '../i18n'
import {FleetLedger} from './FleetLedger'
import {ScanUpdate, safeScanChanges} from './ScanUpdate'

vi.mock('../api/client', () => ({api: {request: vi.fn()}}))
vi.mock('../app/context', () => ({useConnection: () => 'ready', useApp: () => ({language: 'zh-CN', ui: (key: Parameters<typeof translateUi>[1]) => translateUi('zh-CN', key), instances: [], notify: vi.fn()})}))
const report: DockReport = {instance: 'test', revision: 'r1', updated_at: null, min_level: 1, max_level: 125, complete: false, review_count: 0, source: 'manual', pending_revision: 'p1', pending_changes: [], ships: [1, 2].map(id => ({id, name: '重复舰船', group_type: 100, level: 120, rarity: 'SSR', base_rarity: 'SSR', extra_exp: null, review: false, source: 'manual'}))}
const catalog: MindCatalog = {updated_at: '', ships: [{name: '重复舰船', group_type: 100, rarity: 'SSR', base_rarity: 'SSR', base_name: '重复舰船', group: '', type: '驱逐', nationality: 1}]}
function ledger(value: DockReport | undefined, entry = '?tab=data') {
  return render(<MemoryRouter initialEntries={[entry]}><FleetLedger instance="test" report={value} onReport={vi.fn()} onBusy={vi.fn()}/></MemoryRouter>)
}
beforeEach(() => {
  sessionStorage.clear()
  vi.mocked(api.request).mockReset().mockReturnValue(new Promise(() => {}))
})
afterEach(cleanup)

describe('current ships and scan separation', () => {
  it('loads the catalog before showing confirmed ships and editing controls', async () => {
    let resolve!: (value: MindCatalog) => void
    vi.mocked(api.request).mockReturnValueOnce(new Promise<MindCatalog>(done => {resolve = done}))
    ledger(report)
    expect(screen.getByRole('status').textContent).toContain(translateUi('zh-CN', 'common.loading'))
    expect(screen.queryByRole('table')).toBeNull()
    expect(screen.queryByRole('button', {name: '保存'})).toBeNull()
    expect(api.request).toHaveBeenCalledExactlyOnceWith('mind.catalog', {instance: 'test'})
    await act(async () => resolve(catalog))
    expect(screen.getByRole('table')).toBeTruthy()
    expect(screen.getAllByRole('combobox', {name: /舰名 · 重复舰船 #/})).toHaveLength(2)
    expect(screen.getByRole<HTMLButtonElement>('button', {name: '保存'}).disabled).toBe(true)
    expect(screen.queryByRole('heading', {name: '待确认差异'})).toBeNull()
  })

  it('keeps the duplicate deep link while report data loads and highlights its exact copy', async () => {
    vi.mocked(api.request).mockResolvedValueOnce(catalog)
    const view = ledger(undefined, '?tab=data&ship=2')
    await waitFor(() => expect(api.request).toHaveBeenCalledTimes(1))
    expect(screen.getByRole('status').textContent).toContain(translateUi('zh-CN', 'common.loading'))
    expect(screen.queryByRole('table')).toBeNull()
    view.rerender(<MemoryRouter initialEntries={['?tab=data&ship=2']}><FleetLedger instance="test" report={report} onReport={vi.fn()} onBusy={vi.fn()}/></MemoryRouter>)
    const second = await screen.findByRole('combobox', {name: '舰名 · 重复舰船 #2'})
    expect(second.closest('tr')?.classList.contains('ledger-highlight')).toBe(true)
    expect(screen.getByRole('combobox', {name: '舰名 · 重复舰船 #1'}).closest('tr')?.classList.contains('ledger-highlight')).toBe(false)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('accepts only additions and updates in the safe batch', async () => {
    const changes = (['add', 'update', 'remove', 'resolve'] as const).map(operation => ({id: operation, operation, kind: 'ship' as const, ship_id: null, group_type: 100, name: '重复舰船', before: null, after: {level: 120}, fields: ['level'], candidates: report.ships}))
    expect(safeScanChanges(changes).map(item => item.operation)).toEqual(['add', 'update'])
    const onReport = vi.fn(), onBusy = vi.fn()
    vi.mocked(api.request).mockResolvedValueOnce(report)
    render(<ScanUpdate instance="test" report={{...report, pending_changes: changes}} locked={false} onReport={onReport} onBusy={onBusy}/>)
    const target = screen.getByRole<HTMLSelectElement>('combobox', {name: '目标卡片 resolve'})
    expect(target.value).toBe('')
    expect(screen.getByRole<HTMLOptionElement>('option', {name: '新增独立卡片'}).value).toBe('new')
    expect(screen.getByRole<HTMLButtonElement>('button', {name: '接受 resolve'}).disabled).toBe(true)
    expect(screen.getByRole<HTMLButtonElement>('button', {name: '扫描船坞'}).disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', {name: /确认无歧义的新增和更新/}))
    await waitFor(() => expect(onReport).toHaveBeenCalledExactlyOnceWith(report))
    expect(api.request).toHaveBeenCalledExactlyOnceWith('dock.review', {instance: 'test', revision: 'r1', pending_revision: 'p1', decisions: [{id: 'add', accept: true}, {id: 'update', accept: true}]})
    expect(onBusy.mock.calls).toEqual([[true], [false]])
  })

  it('saves an inline edit for the selected physical copy without editing its duplicate', async () => {
    const onReport = vi.fn(), onBusy = vi.fn()
    const updated = {...report, revision: 'r2', ships: report.ships.map(ship => ship.id === 2 ? {...ship, level: 121} : ship)}
    vi.mocked(api.request).mockResolvedValueOnce(catalog).mockResolvedValueOnce(updated)
    render(<MemoryRouter><FleetLedger instance="test" report={report} onReport={onReport} onBusy={onBusy}/></MemoryRouter>)
    const second = await screen.findByRole<HTMLInputElement>('spinbutton', {name: '等级 · 重复舰船 #2'})
    fireEvent.change(second, {target: {value: '121'}})
    expect(screen.getByRole<HTMLInputElement>('spinbutton', {name: '等级 · 重复舰船 #1'}).value).toBe('120')
    fireEvent.click(screen.getByRole('button', {name: '保存'}))
    await waitFor(() => expect(onReport).toHaveBeenCalledExactlyOnceWith(updated))
    expect(api.request).toHaveBeenLastCalledWith('dock.patch', {instance: 'test', revision: 'r1', changes: [{
      id: 2, name: '重复舰船', group_type: 100, level: 121, rarity: 'SSR', base_rarity: 'SSR',
      full_star: null, form: null, review: false,
    }]})
    expect(onBusy.mock.calls).toEqual([[true], [false]])
    expect(screen.getByText(translateUi('zh-CN', 'analysis.saved'))).toBeTruthy()
    expect(sessionStorage.getItem('azurpilot.fleet-ledger.v1.test')).toBeNull()
  })
})
