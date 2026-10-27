// @vitest-environment jsdom
import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react'
import {MemoryRouter, Route, Routes} from 'react-router-dom'
import {api} from '../api/client'
import type {DockReport} from '../api/types'
import type {Catalog, Report} from '../fleet-tech/types'
import {translateUi} from '../i18n'
import {MindCalculator} from './MindCalculator'

vi.mock('../api/client', () => ({api: {request: vi.fn()}}))
vi.mock('../app/context', () => ({useConnection: () => 'ready', useApp: () => ({language: 'zh-CN',
  ui: (key: Parameters<typeof translateUi>[1], params: Parameters<typeof translateUi>[2]) => translateUi('zh-CN', key, params)})}))
const dock: DockReport = {instance: 'test', revision: 'r1', updated_at: null, min_level: 1, max_level: 125,
  complete: false, review_count: 0, source: 'manual', pending_revision: null, pending_changes: [],
  ships: ['企业', '埃塞克斯', '杜威'].map((name, index) => ({id: index + 1, group_type: index + 100,
    name, level: 100, rarity: 'SSR', base_rarity: 'SSR', extra_exp: null, review: false, source: 'manual'}))}
const catalog: Catalog = {updated_at: '', source_commit: '', max_points: 0, nation_points: {}, nations: {},
  ships: dock.ships.map(card => ({group_type: card.group_type!, name: card.name, names: {}, code: card.id,
    handbook_type: 0, blueprint: false, full_star_rule: 'ordinary_level_cap', nationality: 1,
    rarity: 'SSR', type: card.id === 3 ? 1 : 7, technology: {pt_get: 1, pt_upgrage: 1, pt_level: 1,
      max_star: 5, add_get_shiptype: [7], add_get_attr: 5, add_get_value: 1,
      add_level_shiptype: [7], add_level_attr: 5, add_level_value: 1}}))}
const technology: Report = {instance: 'test', revision: 't1', data_version: 'r1', ships: [],
  nation_levels: {}, bonuses: {}, ship_bonuses: {}, nation_bonuses: {}, confirmed_points: 0,
  completed: 0, milestone_counts: {owned: 0, full_star: 0, lv120: 0}}
const all = () => screen.getByRole<HTMLInputElement>('checkbox', {name: '全选当前筛选'})
const ship = (name: string) => screen.getByRole<HTMLInputElement>('checkbox', {name: `参与计算 ${name}`})
const search = (value: string) => fireEvent.change(screen.getByRole('searchbox'), {target: {value}})
async function show() {
  render(<MemoryRouter initialEntries={['/test']}><Routes><Route path="/:instance" element={<MindCalculator report={dock}/>}/></Routes></MemoryRouter>)
  await screen.findByRole('table')
}
beforeEach(() => {
  localStorage.clear()
  vi.mocked(api.request).mockReset().mockImplementation(async method => {
    if (method === 'fleetTech.catalog') return catalog
    if (method === 'fleetTech.report') return technology
    if (method === 'mind.plan') return {ships: [], included: 3, unknown: 0, mind: 0, gold: 0}
    throw new Error(`Unexpected method: ${method}`)
  })
})
afterEach(cleanup)

it('starts selected, follows individual choices, and toggles all visible ships together', async () => {
  await show()
  expect(all().checked).toBe(true)
  expect(all().closest('th')?.textContent).toContain('参与计算')
  fireEvent.click(ship('企业'))
  expect(all().checked).toBe(false)
  expect(ship('杜威').checked).toBe(true)
  fireEvent.click(all())
  expect(all().checked).toBe(true)
  expect(ship('企业').checked).toBe(true)
  fireEvent.click(all())
  for (const name of ['企业', '埃塞克斯', '杜威']) expect(ship(name).checked).toBe(false)
  await waitFor(() => expect(api.request).toHaveBeenLastCalledWith('mind.plan', expect.objectContaining({ship_ids: []})))
})

it('limits bulk changes to category and search filters and preserves hidden selections', async () => {
  await show()
  fireEvent.click(screen.getAllByRole('button', {name: '航母'})[0])
  expect(screen.queryByRole('checkbox', {name: '参与计算 杜威'})).toBeNull()
  fireEvent.click(all())
  expect(ship('企业').checked).toBe(false)
  expect(ship('埃塞克斯').checked).toBe(false)
  search('企业')
  fireEvent.click(all())
  expect(all().checked).toBe(true)
  search('')
  expect(all().checked).toBe(false)
  expect(ship('埃塞克斯').checked).toBe(false)
  search('不存在')
  expect(all().disabled).toBe(true)
  expect(all().checked).toBe(false)
  await waitFor(() => expect(api.request).toHaveBeenLastCalledWith('mind.plan', expect.objectContaining({ship_ids: [1, 3]})))
})

it('respects saved exclusions and the included-only filter', async () => {
  localStorage.setItem('azurpilot.cultivation-auto.test', JSON.stringify({excluded_groups: [100]}))
  await show()
  expect(all().checked).toBe(false)
  fireEvent.click(screen.getByRole('checkbox', {name: '仅显示参与计算'}))
  expect(all().checked).toBe(true)
  fireEvent.click(all())
  expect(all().disabled).toBe(true)
  fireEvent.click(screen.getByRole('checkbox', {name: '仅显示参与计算'}))
  for (const name of ['企业', '埃塞克斯', '杜威']) expect(ship(name).checked).toBe(false)
})
