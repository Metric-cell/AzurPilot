import {expect, test} from '@playwright/test'
import {rpc} from './fleet-helpers'
import type {Parameters} from '../src/api/generated'
import type {DockReport} from '../src/api/types'

test.beforeEach(async ({page}) => {
  await page.addInitScript(() => {
    localStorage.setItem('azurpilot.theme', 'light')
    localStorage.setItem('azurpilot.language', 'zh-CN')
  })
})

test('four flat pages preserve per-copy edits and never start a scan on navigation', async ({page}, info) => {
  const instance = `four-${info.testId.replace(/[^a-z0-9]/gi, '').slice(-12)}-${info.retry}`
  const methods: string[] = []
  page.on('websocket', socket => socket.on('framesent', ({payload}) => {const request = JSON.parse(String(payload)); if (request.type === 'request') methods.push(request.method)}))
  await page.goto('/')
  await rpc(page, 'instances.create', {name: instance})
  const empty = await rpc(page, 'dock.report', {instance})
  const saved = await rpc(page, 'dock.patch', {instance, revision: empty.revision, changes: [
    {name: '杜威', group_type: 10102, level: 100, rarity: 'R', base_rarity: 'R', extra_exp: null},
    {name: '杜威', group_type: 10102, level: 100, rarity: 'R', base_rarity: 'R', extra_exp: 0},
  ]})
  const [first, second] = saved.ships
  // Fixtures are seeded through an external socket; reload the app's instance list.
  await page.goto('about:blank')
  await page.goto(`/#/i/${instance}/task/FleetAnalysis?tab=data&ship=${second.id}`)
  await expect(page.getByRole('tab')).toHaveCount(4)
  await expect(page.locator(`#ledger-ship-${second.id}`)).toHaveClass(/ledger-highlight/)
  await expect(page.getByLabel(`等级 · 杜威 #${second.id}`, {exact: true})).toHaveValue('100')
  await expect(page.getByRole('button', {name: '保存', exact: true})).toHaveCount(1)
  await expect(page.getByRole('dialog')).toHaveCount(0)
  const level = page.getByLabel(`等级 · 杜威 #${first.id}`, {exact: true})
  await expect(level).toHaveValue('100')
  await level.fill('110')
  await page.getByRole('button', {name: '保存', exact: true}).click()
  await expect(page.getByText('舰船数据已保存', {exact: true})).toBeVisible()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.reload()
  const reloaded = await rpc(page, 'dock.report', {instance})
  expect(reloaded.ships.find(row => row.id === first.id)).toMatchObject({level: 110, extra_exp: null})
  expect(reloaded.ships.find(row => row.id === second.id)).toMatchObject({level: 100, extra_exp: 0})
  const navigationStart = methods.length
  for (const tab of ['舰队科技', '养成计算', '我的舰船']) {
    await page.getByRole('tab', {name: tab, exact: true}).click()
    await expect(page.getByRole('button', {name: '扫描船坞', exact: true})).toHaveCount(0)
    await expect(page.locator('input[type=file]')).toHaveCount(0)
    await expect(page.getByRole('tabpanel')).toHaveCount(1)
  }
  await page.getByRole('tab', {name: '扫描更新', exact: true}).click()
  await expect(page.getByRole('button', {name: '扫描船坞', exact: true})).toBeVisible()
  expect(methods.slice(navigationStart).filter(method => ['tasks.run', 'dock.importLegacy', 'dock.patch', 'dock.review', 'fleetTech.patch', 'fleetTech.mergeShips'].includes(method))).toEqual([])
})

test('safe batch excludes removals and unresolved duplicates', async ({page}, info) => {
  const instance = `safe-${info.testId.replace(/[^a-z0-9]/gi, '').slice(-12)}-${info.retry}`
  await page.goto('/'); await rpc(page, 'instances.create', {name: instance}); await page.goto('about:blank')
  const card = (id: number, level: number) => ({id, name: '杜威', level, group_type: 10102, rarity: 'R', base_rarity: 'R', extra_exp: null, review: false, source: 'manual'})
  const decisions: Parameters['dock.review']['decisions'][] = []
  const report: DockReport = {instance, revision: 'a'.repeat(64), ships: [card(10, 100), card(11, 100)], legacy_imported: false, min_level: 1, max_level: 125, updated_at: '', complete: true, review_count: 0, source: 'manual', pending_revision: 'b'.repeat(64), pending_changes: [
    {id: 'update', kind: 'ship', operation: 'update', ship_id: 10, name: '杜威', before: card(10, 100), after: card(10, 110), candidates: [], group_type: 10102, fields: ['level']},
    {id: 'remove', kind: 'ship', operation: 'remove', ship_id: 11, name: '杜威', before: card(11, 100), after: null, candidates: [], group_type: 10102, fields: []},
    {id: 'resolve', kind: 'ship', operation: 'resolve', ship_id: null, name: '杜威', before: null, after: card(0, 120), candidates: [card(10, 100), card(11, 100)], group_type: 10102, fields: ['level']},
  ]}
  await page.routeWebSocket('**/api/v1/ws', socket => {
    const server = socket.connectToServer()
    socket.onMessage(message => {
      const request = JSON.parse(String(message))
      if (!['dock.report', 'dock.review'].includes(request.method)) {server.send(message); return}
      if (request.method === 'dock.review') {
        const review = request.params as Parameters['dock.review']
        decisions.push(review.decisions)
        for (const decision of review.decisions) report.pending_changes = report.pending_changes.filter(row => row.id !== decision.id)
      }
      socket.send(JSON.stringify({v: 1, type: 'response', id: request.id, ok: true, result: report}))
    })
    server.onMessage(message => socket.send(message))
  })
  await page.goto(`/#/i/${instance}/task/FleetAnalysis?tab=data`)
  await expect(page.getByRole('button', {name: '接受 remove', exact: true})).toHaveCount(0)
  await page.getByRole('tab', {name: '扫描更新', exact: true}).click()
  await expect(page.getByRole('button', {name: '接受 resolve', exact: true})).toBeDisabled()
  await page.getByRole('button', {name: /确认无歧义的新增和更新/}).click()
  await expect(page.getByRole('button', {name: /确认无歧义的新增和更新/})).toBeDisabled()
  expect(decisions).toEqual([[{id: 'update', accept: true}]])
  await page.getByRole('button', {name: '接受 remove', exact: true}).click()
  expect(decisions).toHaveLength(1)
  await page.getByRole('button', {name: '接受 remove', exact: true}).click()
  await expect(page.getByRole('button', {name: '接受 remove', exact: true})).toHaveCount(0)
  expect(decisions[1]).toEqual([{id: 'remove', accept: true}])
  await page.getByLabel('目标卡片 resolve', {exact: true}).selectOption('11')
  await page.getByRole('button', {name: '接受 resolve', exact: true}).click()
  expect(decisions[2]).toEqual([{id: 'resolve', accept: true, ship_id: 11}])
})
