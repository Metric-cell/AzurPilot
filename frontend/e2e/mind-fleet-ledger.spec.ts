import {expect, test, type Page} from '@playwright/test'

async function rpc(page: Page, method: string, params: Record<string, unknown>) {
  return page.evaluate(({method, params}) => new Promise<any>((resolve, reject) => {
    const socket = new WebSocket(`ws://${location.host}/api/v1/ws`)
    const timer = setTimeout(() => {socket.close(); reject(new Error('模拟接口超时'))}, 20000)
    socket.onopen = () => socket.send(JSON.stringify({v: 1, type: 'request', id: 'ledger-test', method, params}))
    socket.onmessage = event => {
      const value = JSON.parse(String(event.data))
      if (value.id !== 'ledger-test') return
      clearTimeout(timer); socket.close()
      value.ok ? resolve(value.result) : reject(new Error(value.error.message))
    }
    socket.onerror = () => {clearTimeout(timer); reject(new Error('模拟连接失败'))}
  }), {method, params})
}

test.beforeEach(async ({page}) => {
  await page.addInitScript(() => {
    localStorage.setItem('azurpilot.theme', 'light')
    localStorage.setItem('azurpilot.language', 'zh-CN')
  })
})

test('三个标签共用底账，重名卡片独立编辑并保留未知和零经验', async ({page}, info) => {
  const instance = `ledger-${info.testId.replace(/[^a-z0-9]/gi, '').slice(-12)}-${info.retry}`
  const requests: string[] = []
  await page.routeWebSocket('**/api/v1/ws', socket => {
    const server = socket.connectToServer()
    socket.onMessage(message => {requests.push(JSON.parse(String(message)).method); server.send(message)})
    server.onMessage(message => socket.send(message))
  })
  await page.goto('/')
  await rpc(page, 'instances.create', {name: instance})
  const empty = await rpc(page, 'dock.report', {instance})
  const saved = await rpc(page, 'dock.patch', {instance, revision: empty.revision, changes: [
    {name: '杜威', group_type: 10102, level: 100, rarity: 'R', base_rarity: 'R', extra_exp: null},
    {name: '杜威', group_type: 10102, level: 100, rarity: 'R', base_rarity: 'R', extra_exp: 0},
  ]})
  const [first, second] = saved.ships
  expect(first.id).not.toBe(second.id)
  await page.goto(`/#/i/${instance}/task/FleetAnalysis?tab=data`)
  const tabs = page.getByRole('tablist')
  await expect(tabs.getByRole('tab')).toHaveCount(3)
  for (const name of ['心智分析', '舰队科技分析', '舰船数据']) await expect(tabs.getByRole('tab', {name, exact: true})).toBeVisible()
  await expect(page.getByLabel(`额外经验（未知留空） · 杜威 #${first.id}`, {exact: true})).toHaveValue('')
  await expect(page.getByLabel(`额外经验（未知留空） · 杜威 #${second.id}`, {exact: true})).toHaveValue('0')
  await page.getByLabel(`等级 · 杜威 #${first.id}`, {exact: true}).fill('110')
  await page.getByLabel(`排除心智计算 · 杜威 #${first.id}`, {exact: true}).check()
  await page.getByRole('button', {name: '保存舰船数据', exact: true}).click()
  await expect(page.getByRole('button', {name: '保存舰船数据', exact: true})).toBeDisabled()
  await page.reload()
  await expect(page.getByLabel(`等级 · 杜威 #${first.id}`, {exact: true})).toHaveValue('110')
  await expect(page.getByLabel(`等级 · 杜威 #${second.id}`, {exact: true})).toHaveValue('100')
  await expect(page.getByLabel(`排除心智计算 · 杜威 #${first.id}`, {exact: true})).toBeChecked()
  const persisted = await rpc(page, 'dock.report', {instance})
  expect(persisted.ships.map((row: any) => [row.id, row.level, row.extra_exp, row.excluded])).toEqual([
    [first.id, 110, null, true], [second.id, 100, 0, false],
  ])
  await tabs.getByRole('tab', {name: '心智分析', exact: true}).click()
  await tabs.getByRole('tab', {name: '舰队科技分析', exact: true}).click()
  await tabs.getByRole('tab', {name: '舰船数据', exact: true}).click()
  expect(requests.filter(method => ['tasks.run', 'dock.importLegacy', 'mind.save', 'fleetTech.syncMind'].includes(method))).toEqual([])
})

test('候选变化逐条拒绝和全部采纳，歧义副本必须选择目标', async ({page}, info) => {
  const instance = `review-${info.testId.replace(/[^a-z0-9]/gi, '').slice(-12)}-${info.retry}`
  await page.goto('/')
  await rpc(page, 'instances.create', {name: instance})
  await page.goto('about:blank')
  const card = (id: number, name: string, level: number) => ({id, name, level, group_type: 10102,
    rarity: 'R', base_rarity: 'R', extra_exp: null, excluded: false, review: false, source: 'manual'})
  const originals = [card(10, '杜威', 100), card(11, '杜威', 100)]
  const decisions: any[] = []
  const report: any = {instance, revision: 'a'.repeat(64), ships: originals,
    legacy_imported: false, min_level: 1, max_level: 125, updated_at: '', complete: false,
    review_count: 0, source: 'manual', pending_revision: 'b'.repeat(64), pending_changes: [
      {id: 'ship:1', kind: 'ship', operation: 'resolve', source: 'scan', ship_id: null, group_type: 10102,
        name: '杜威', before: null, after: card(0, '杜威', 110), fields: ['level'], candidates: originals},
      {id: 'ship:2', kind: 'ship', operation: 'add', source: 'scan', ship_id: null, group_type: null,
        name: '待拒绝', before: null, after: card(0, '待拒绝', 20), fields: ['name', 'level'], candidates: []},
    ]}
  await page.routeWebSocket('**/api/v1/ws', socket => {
    const server = socket.connectToServer()
    socket.onMessage(message => {
      const request = JSON.parse(String(message))
      if (!['dock.report', 'dock.review'].includes(request.method)) {server.send(message); return}
      if (request.method === 'dock.review') {
        decisions.push(request.params)
        for (const decision of request.params.decisions) {
          if (decision.accept && decision.ship_id) report.ships = report.ships.map((row: any) => row.id === decision.ship_id ? {...row, level: 110} : row)
          report.pending_changes = report.pending_changes.filter((row: any) => row.id !== decision.id)
        }
        report.revision = 'c'.repeat(64)
      }
      socket.send(JSON.stringify({v: 1, type: 'response', id: request.id, ok: true, result: report}))
    })
    server.onMessage(message => socket.send(message))
  })
  await page.goto(`/#/i/${instance}/task/FleetAnalysis?tab=data`)
  await expect(page.getByRole('button', {name: '接受 ship:1', exact: true})).toBeDisabled()
  await page.getByRole('button', {name: '拒绝 ship:2', exact: true}).click()
  await expect(page.getByRole('button', {name: '拒绝 ship:2', exact: true})).toHaveCount(0)
  expect(decisions[0].decisions).toEqual([{id: 'ship:2', accept: false}])
  await page.getByLabel('目标卡片 ship:1', {exact: true}).selectOption('11')
  await page.getByRole('button', {name: '全部接受', exact: true}).click()
  await expect(page.getByLabel('等级 · 杜威 #10', {exact: true})).toHaveValue('100')
  await expect(page.getByLabel('等级 · 杜威 #11', {exact: true})).toHaveValue('110')
  expect(decisions[1]).toMatchObject({instance, pending_revision: 'b'.repeat(64),
    decisions: [{id: 'ship:1', accept: true, ship_id: 11}]})
  await expect(page.getByRole('button', {name: '全部接受', exact: true})).toBeDisabled()
})
