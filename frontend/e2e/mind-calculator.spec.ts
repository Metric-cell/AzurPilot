import {expect, test} from '@playwright/test'
import {readFile} from 'node:fs/promises'
import {fileURLToPath} from 'node:url'
import type {Page} from '@playwright/test'

async function importList(page: Page, ships: object[]) {
  await page.locator('input[type=file][accept=".json,.csv,.xlsx"]').setInputFiles({name: '清单.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(ships))})
}
async function choose(page: Page, label: string, option: string) {
  await page.getByRole('combobox', {name: label, exact: true}).click()
  await page.getByRole('option', {name: option, exact: true}).click()
}

async function rpc(page: Page, method: string, params: object) {
  return page.evaluate(({method, params}) => new Promise<any>((resolve, reject) => {
    const socket = new WebSocket(`ws://${location.host}/api/v1/ws`)
    socket.onopen = () => socket.send(JSON.stringify({v: 1, type: 'request', id: 'mind-test', method, params}))
    socket.onmessage = event => {
      const response = JSON.parse(String(event.data))
      if (response.id !== 'mind-test') return
      socket.close(); response.ok ? resolve(response.result) : reject(new Error(response.error.message))
    }
    socket.onerror = () => reject(new Error('模拟连接失败'))
  }), {method, params})
}

test.beforeEach(async ({page}) => {
  await page.goto('/')
  for (const instance of ['demo-main', 'demo-alt', 'demo-dog']) {
    const report = await rpc(page, 'dock.report', {instance})
    if (report.ships.length) await rpc(page, 'dock.patch', {instance, revision: report.revision,
      changes: report.ships.map((row: {id: number}) => ({id: row.id, delete: true}))})
  }
  await page.goto('about:blank')
  await page.addInitScript(() => {localStorage.setItem('azurpilot.theme', 'light'); localStorage.setItem('azurpilot.language', 'zh-CN')})
})

test('试算不写入底账，扫描由共享控制启动且可停止', async ({page}) => {
  const requests: string[] = []
  page.on('websocket', socket => socket.on('framesent', frame => {
    try {requests.push(JSON.parse(String(frame.payload)).method)} catch { /* Ignore non-RPC frames. */ }
  }))
  await page.goto('/#/i/demo-dog/task/FleetAnalysis?tab=mind')
  const scan = page.getByRole('button', {name: '扫描船坞', exact: true})
  await expect(scan).toBeEnabled()
  const add = page.locator('.mind-add')
  await add.getByLabel('船名', {exact: true}).fill('热心')
  await add.getByRole('button', {name: '添加舰船'}).click()
  await expect(page.locator('.mind-intro')).toContainText('本地试算')
  await expect(page.locator('.mind-totals')).toContainText('880')
  await expect(page.getByRole('button', {name: '导出结果', exact: true})).toBeDisabled()
  expect((await rpc(page, 'dock.report', {instance: 'demo-dog'})).ships).toEqual([])
  await page.getByRole('button', {name: '放弃草稿并载入', exact: true}).click()
  await expect(page.locator('.mind-ship-table tbody tr')).toHaveCount(0)
  await scan.click()
  await expect(page.getByRole('button', {name: '正在扫描船坞', exact: true})).toBeDisabled()
  await page.getByRole('button', {name: '停止扫描', exact: true}).click()
  await expect(scan).toBeEnabled()
  expect(requests.filter(method => method === 'mind.save')).toEqual([])
})

test('自动扫描按游戏地区和实例状态显示禁用原因，国服检测区不会掩盖日服配置', async ({page}) => {
  let region = 'jp', status = 'stopped'
  await page.routeWebSocket('**/api/v1/ws', socket => {
    const server = socket.connectToServer()
    const requests = new Map<string, string>()
    socket.onMessage(message => {
      const request = JSON.parse(String(message))
      requests.set(request.id, request.method)
      server.send(message)
    })
    server.onMessage(message => {
      const response = JSON.parse(String(message))
      const instances = response.topic === 'instances' ? response.data : requests.get(response.id) === 'instances.list' ? response.result : undefined
      if (Array.isArray(instances)) {
        const current = instances.find(item => item.name === 'demo-dog')
        if (current) Object.assign(current, {region, status, server: 'cn_android-0', currentTask: status === 'running' ? 'Commission' : null})
      }
      socket.send(JSON.stringify(response))
    })
  })
  await page.goto('/#/i/demo-dog/task/FleetAnalysis?tab=mind')
  const scan = page.getByRole('button', {name: '扫描船坞', exact: true})
  await expect(scan).toBeDisabled()
  await expect(page.getByRole('status').filter({hasText: /游戏地区不受支持|请先停止当前任务|请等待更新完成/})).toContainText('游戏地区不受支持')
  region = 'cn'; status = 'running'
  await page.reload()
  await expect(scan).toBeDisabled()
  await expect(page.getByRole('status').filter({hasText: /游戏地区不受支持|请先停止当前任务|请等待更新完成/})).toContainText('请先停止当前任务')
  status = 'updating'
  await page.reload()
  await expect(scan).toBeDisabled()
  await expect(page.getByRole('status').filter({hasText: /游戏地区不受支持|请先停止当前任务|请等待更新完成/})).toContainText('请等待更新完成')
  status = 'stopped'
  await page.reload()
  await expect(scan).toBeEnabled()
})

test('手工试算合并最高等级，底账 Excel 导出可跨实例导入试算', async ({page}) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('/#/i/demo-main/task/FleetAnalysis?tab=mind')
  const add = page.locator('.mind-add')
  for (const [name, level] of [['拉菲', '100'], ['拉菲.改', '106'], ['约克城II', '100']]) {
    await add.getByLabel('船名', {exact: true}).fill(name)
    await add.getByLabel('等级', {exact: true}).fill(level)
    await add.getByRole('button', {name: '添加舰船'}).click()
  }
  await expect(page.locator('.mind-totals').first()).toContainText('4,260')
  await expect(page.locator('.mind-totals')).toContainText('42,600')
  await expect(page.locator('.mind-ship-table tbody tr')).toHaveCount(2)
  await expect(page.locator('.mind-ship-table input[value="拉菲.改"]')).toBeVisible()
  await expect(page.getByRole('button', {name: '导出结果', exact: true})).toBeDisabled()
  const report = await rpc(page, 'dock.report', {instance: 'demo-main'})
  expect(report.ships).toEqual([])
  await rpc(page, 'dock.patch', {instance: 'demo-main', revision: report.revision, changes: [
    {name: '拉菲.改', level: 106}, {name: '约克城II', level: 100},
  ]})
  await page.getByRole('button', {name: '放弃草稿并载入', exact: true}).click()
  await expect(page.getByRole('button', {name: '导出结果', exact: true})).toBeEnabled()
  const downloaded = page.waitForEvent('download')
  await page.getByRole('button', {name: '导出结果', exact: true}).click()
  const download = await downloaded
  expect(download.suggestedFilename()).toMatch(/\.xlsx$/)
  const bytes = await readFile((await download.path())!)
  await page.goto('/#/i/demo-alt/task/FleetAnalysis?tab=mind')
  await expect(page.getByText('没有符合条件的舰船', {exact: true})).toBeVisible()
  await page.locator('input[type=file][accept=".json,.csv,.xlsx"]').setInputFiles({name: '原计算结果.xlsx', mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', buffer: bytes})
  await expect(page.locator('.mind-ship-table tbody tr')).toHaveCount(2)
  await expect(page.locator('.mind-totals')).toContainText('4,260')
  expect((await rpc(page, 'dock.report', {instance: 'demo-alt'})).ships).toEqual([])
  await page.setViewportSize({width: 390, height: 844})
  await expect(page.getByRole('heading', {name: '心智单元计算器', exact: true})).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  expect(errors).toEqual([])
})

test('截图可靠结果直接计算，保留最高等级，不确定名称可人工校正', async ({page}) => {
    await page.goto('/#/i/demo-alt/task/FleetAnalysis?tab=mind')
    await expect(page.locator('.mind-add')).toBeVisible()
  const add = page.locator('.mind-add')
  await add.getByLabel('船名', {exact: true}).fill('热心.改')
  await add.getByLabel('等级', {exact: true}).fill('100')
  await add.getByRole('button', {name: '添加舰船'}).click()
  const screenshot = fileURLToPath(new URL('../../tests/fixtures/mind_dock_fleet_priority.png', import.meta.url))
  await page.locator('input[type=file][accept^="image/png"]').setInputFiles([screenshot, screenshot])
  // 两张截图里的新月身份都未确认，分别保留；其余已确认身份仍取最高等级。
  await expect(page.locator('.mind-ship-table tbody tr')).toHaveCount(22, {timeout: 30_000})
  await expect(page.locator('.mind-totals strong').nth(3)).toHaveText('2')
  const crescents = page.locator('.mind-ship-table tbody tr').filter({has: page.locator('input[value="新月"]')})
  await expect(crescents).toHaveCount(2)
  await expect(crescents.locator('td:nth-child(5)')).toHaveText(['0', '0'])
  await expect(page.locator('.mind-totals strong').first()).not.toHaveText('0')
  const row = page.locator('.mind-ship-table tbody tr').filter({has: page.locator('input[value="热心.改"]')})
  await expect(row.locator('input[type=number]')).toHaveValue('105')
  await expect(row.getByRole('button', {name: '已核对', exact: true})).toHaveCount(0)
  await expect(row).toContainText('800')
  await page.locator('input[type=file][accept=".json,.csv,.xlsx"]').setInputFiles({
    name: '待核对.json', mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify([{name: '人工核对舰', level: 100, base_rarity: 'SR', review: true}])),
  })
  await expect(page.locator('.mind-totals strong').nth(3)).toHaveText('3')
  const uncertain = page.locator('.mind-ship-table tbody tr').filter({has: page.locator('input[value="人工核对舰"]')})
  await expect(uncertain.locator('input[type=text], input:not([type])')).not.toHaveAttribute('readonly', '')
  await expect(uncertain).toContainText('待核对')
  await expect(uncertain.getByRole('combobox', {name: /^状态/})).toBeEnabled()
  await expect(uncertain.getByRole('button', {name: '删除 人工核对舰'})).toBeVisible()
  await page.getByRole('heading', {name: '心智单元计算器', exact: true}).scrollIntoViewIfNeeded()
  await page.screenshot({path: fileURLToPath(new URL('../test-results/mind-calculator-review.png', import.meta.url)), fullPage: true})
})

test('试算指定精锐有效，身份只读且等级草稿重载有效', async ({page}) => {
  await page.goto('/#/i/demo-alt/task/FleetAnalysis?tab=mind')
  const add = page.locator('.mind-add')
  await add.getByLabel('船名', {exact: true}).fill('皇家财富号')
  await add.getByRole('combobox', {name: '基础稀有度', exact: true}).click()
  await page.getByRole('option', {name: '精锐', exact: true}).click()
  await add.getByRole('button', {name: '添加舰船'}).click()
  await expect(page.locator('.mind-totals strong').first()).toHaveText('1,320')
  await expect(page.getByLabel('船名 1', {exact: true})).toHaveAttribute('readonly', '')
  await expect(page.locator('.mind-ship-table .mind-rarity')).toHaveText('精锐')
  await expect(page.locator('.mind-ship-table').getByRole('combobox', {name: '状态 1', exact: true})).toBeEnabled()
  await expect(page.locator('.mind-ship-table input[type=checkbox]')).toHaveCount(0)
  await page.getByLabel('等级 1', {exact: true}).fill('106')
  await page.getByLabel('等级 1', {exact: true}).press('Enter')
  await expect(page.locator('.mind-totals strong').first()).toHaveText('960')
  await expect(page.locator('.mind-intro')).toContainText('本地试算')
  await page.reload()
  await expect(page.getByLabel('等级 1', {exact: true})).toHaveValue('106')
  await expect(page.locator('.mind-ship-table .mind-rarity')).toHaveText('精锐')
  await expect(page.locator('.mind-totals strong').first()).toHaveText('960')
  for (const invalid of ['0', '126', '100.5']) {
    await page.getByLabel('等级 1', {exact: true}).fill(invalid)
    await page.getByLabel('等级 1', {exact: true}).press('Enter')
    await expect(page.getByLabel('等级 1', {exact: true})).toHaveValue('106')
  }
})

test('识别失败保留未知等级和重复核对项，人工校正后才试算计费', async ({page}) => {
  test.setTimeout(60_000)
  await page.goto('/#/i/demo-alt/task/FleetAnalysis?tab=mind')
  await expect(page.locator('.mind-add')).toBeVisible()
  await importList(page, [{name: '卡辛', level: 100},
    {name: '拉菲I', level: 0, review: true, source: '第 2 页第 3 列等级与身份未确认'},
    {name: '重复误读', level: 110, review: true}, {name: '重复误读', level: 110, review: true}])
  await expect(page.locator('.mind-ship-table tbody tr')).toHaveCount(4)
  await expect(page.locator('.mind-totals strong').nth(3)).toHaveText('3')
  await expect(page.locator('.mind-intro')).toContainText('本地试算')
  await page.reload()
  await expect(page.getByLabel('等级 2', {exact: true})).toHaveValue('')
  await expect(page.getByLabel('等级 2', {exact: true})).toHaveAttribute('placeholder', '未知')
  await expect(page.locator('.mind-ship-table tbody tr')).toHaveCount(4)
  await choose(page, '基础稀有度 2', '精锐')
  await expect(page.locator('.mind-intro')).toContainText('本地试算')
  await expect(page.locator('.mind-totals strong').nth(3)).toHaveText('3')
  await page.reload()
  await expect(page.getByLabel('船名 2', {exact: true})).toHaveValue('拉菲I')
  await expect(page.getByLabel('基础稀有度 2', {exact: true})).toHaveText('精锐')
  await page.locator('.mind-ship-table').scrollIntoViewIfNeeded()
  await page.screenshot({path: fileURLToPath(new URL('../test-results/mind-calculator-unknown-review.png', import.meta.url)), fullPage: true})
  await page.getByRole('combobox', {name: '状态 2', exact: true}).click()
  await expect(page.getByRole('option', {name: '计入舰船', exact: true})).toBeDisabled()
  await page.keyboard.press('Escape')
  await expect(page.locator('.mind-totals strong').nth(3)).toHaveText('3')
  await page.getByLabel('船名 2', {exact: true}).fill('无法匹配的舰名')
  await page.getByLabel('船名 2', {exact: true}).press('Enter')
  await expect(page.locator('.error-box')).toContainText('请选择舰船资料中的完整名称')
  await expect(page.getByLabel('船名 2', {exact: true})).toHaveValue('拉菲I')
  await page.getByLabel('船名 2', {exact: true}).fill('约克城II')
  await page.getByLabel('船名 2', {exact: true}).press('Enter')
  await expect(page.locator('.mind-intro')).toContainText('本地试算')
  await page.getByLabel('等级 2', {exact: true}).fill('93')
  await page.getByLabel('等级 2', {exact: true}).press('Enter')
  await expect(page.locator('.mind-intro')).toContainText('本地试算')
  await expect(page.locator('.mind-totals strong').nth(3)).toHaveText('3')
  await choose(page, '状态 2', '计入舰船')
  await expect(page.locator('.mind-totals strong').first()).toHaveText('3,960')
  await expect(page.locator('.mind-intro')).toContainText('本地试算')
  await page.reload()
  await expect(page.getByLabel('船名 2', {exact: true})).toHaveValue('约克城II')
  await expect(page.getByLabel('船名 2', {exact: true})).toHaveAttribute('readonly', '')
  await expect(page.getByLabel('等级 2', {exact: true})).toHaveValue('93')
  await expect(page.locator('.mind-totals strong').nth(3)).toHaveText('2')
  await expect(page.locator('.mind-ship-table tbody tr')).toHaveCount(4)
})

test('同名舰船按群组保留，约克新月先待核对并可选择阵营试算', async ({page}) => {
  await page.goto('/#/i/demo-main/task/FleetAnalysis?tab=mind')
  for (const name of ['天城（战巡）', '天城（正航）', '加贺（战列）', '加贺（正航）',
    '约克（皇家）', '约克（铁血）', '新月（皇家）', '新月（重樱）']) {
    await expect(page.locator(`#mind-ship-catalog option[value="${name}"]`)).toHaveCount(1)
  }
  await importList(page, ['天城（战巡）', '天城（正航）', '加贺（战列）', '加贺（正航）', '约克', '新月']
    .map(name => ({name, level: 100, rarity: 'SSR'})))
  await expect(page.locator('.mind-ship-table tbody tr')).toHaveCount(6)
  await expect(page.locator('.mind-totals strong').nth(3)).toHaveText('2')
  await expect(page.locator('.mind-totals strong').first()).toHaveText('9,900')
  await expect(page.locator('.mind-intro')).toContainText('本地试算')
  await page.getByLabel('船名 5', {exact: true}).fill('约克（铁血）')
  await page.getByLabel('船名 5', {exact: true}).press('Enter')
  await expect(page.locator('.mind-intro')).toContainText('本地试算')
  await choose(page, '状态 5', '计入舰船')
  await expect(page.locator('.mind-totals strong').nth(3)).toHaveText('1')
  await expect(page.locator('.mind-totals strong').first()).toHaveText('11,220')
  await expect(page.locator('.mind-intro')).toContainText('本地试算')
  await page.reload()
  await expect(page.getByLabel('船名 5', {exact: true})).toHaveValue('约克（铁血）')
  await expect(page.getByLabel('船名 6', {exact: true})).toHaveValue('新月')
  await expect(page.locator('.mind-totals strong').nth(3)).toHaveText('1')
  await page.locator('.mind-ship-table').screenshot({path: test.info().outputPath('same-name-groups.png'), animations: 'disabled'})
  await page.setViewportSize({width: 390, height: 844})
  if ((await page.locator('.app-shell').getAttribute('class'))?.includes('mobile-open')) {
    await page.getByRole('button', {name: '关闭导航', exact: true}).click()
  }
  await expect.poll(() => page.locator('.sidebar').evaluate(el => el.getBoundingClientRect().right)).toBeLessThanOrEqual(3)
  await page.getByLabel('船名 6', {exact: true}).scrollIntoViewIfNeeded()
  await expect(page.getByLabel('船名 6', {exact: true})).toBeInViewport()
  await page.screenshot({path: test.info().outputPath('same-name-groups-mobile.png'), fullPage: true, animations: 'disabled'})
})

test('改造同级保留改造条目，II型独立，联动舰试算时排除', async ({page}) => {
  await page.goto('/#/i/demo-alt/task/FleetAnalysis?tab=mind')
  const add = page.locator('.mind-add')
  for (const name of ['卡辛', '卡辛.改', '约克城', '约克城II', 'DEAD MASTER']) {
    await add.getByLabel('船名', {exact: true}).fill(name)
    await add.getByRole('button', {name: '添加舰船'}).click()
  }
  await expect(page.locator('.mind-ship-table tbody tr')).toHaveCount(4)
  await expect(page.locator('.mind-ship-table input[value="卡辛"]')).toHaveCount(0)
  await expect(page.locator('.mind-ship-table input[value="卡辛.改"]')).toBeVisible()
  await expect(page.locator('.mind-ship-table input[value="约克城"]')).toBeVisible()
  await expect(page.locator('.mind-ship-table input[value="约克城II"]')).toBeVisible()
  const collaboration = page.locator('.mind-ship-table tbody tr').filter({has: page.locator('input[value="DEAD MASTER"]')})
  await expect(collaboration).toContainText('已排除')
  await expect(page.locator('.mind-totals strong').first()).toHaveText('5,280')
  await page.reload()
  await page.getByRole('combobox', {name: '筛选状态', exact: true}).click()
  await page.getByRole('option', {name: '已排除', exact: true}).click()
  await expect(page.locator('.mind-ship-table tbody tr')).toHaveCount(1)
  await expect(page.locator('.mind-ship-table input[value="DEAD MASTER"]')).toBeVisible()
})

test('未保存草稿在重载和实例切换后恢复', async ({page}) => {
  await page.goto('/#/i/demo-alt/task/FleetAnalysis?tab=mind')
  const add = page.locator('.mind-add')
  await add.getByLabel('船名', {exact: true}).fill('热心')
  await add.getByRole('button', {name: '添加舰船'}).click()
  await expect(page.locator('.mind-intro')).toContainText('本地试算')
  await page.reload()
  await expect(page.locator('.mind-ship-table input[value="热心"]')).toBeVisible()
  await expect(page.locator('.mind-totals')).toContainText('880')
  await page.goto('/#/i/demo-main/task/FleetAnalysis?tab=mind')
  await expect(page.locator('.mind-ship-table input[value="热心"]')).toHaveCount(0)
  await page.goto('/#/i/demo-alt/task/FleetAnalysis?tab=mind')
  await expect(page.locator('.mind-ship-table input[value="热心"]')).toBeVisible()
  await expect(page.locator('.mind-intro')).toContainText('本地试算')
})

test('批量截图中坏文件显示原因并保留成功识别的三行', async ({page}) => {
  await page.goto('/#/i/demo-alt/task/FleetAnalysis?tab=mind')
  await expect(page.locator('.mind-add')).toBeVisible()
  await page.locator('input[type=file][accept^="image/png"]').setInputFiles([
    {name: '损坏.png', mimeType: 'image/png', buffer: Buffer.from('broken image')},
    {name: '船坞.png', mimeType: 'image/png', buffer: await readFile(fileURLToPath(new URL('../../tests/fixtures/mind_dock_fleet_priority.png', import.meta.url)))},
  ])
  await expect(page.locator('.mind-ship-table tbody tr')).toHaveCount(21, {timeout: 30_000})
  await expect(page.getByText(/损坏.png: 截图识别失败/)).toBeVisible()
  await expect(page.getByRole('status').filter({hasText: '已处理 21 条数据，清单保留 21 艘舰船'})).toBeVisible()
})

test('三种布里试算草稿重载后仍自动排除，不允许切回计费状态', async ({page}) => {
  await page.goto('/#/i/demo-alt/task/FleetAnalysis?tab=mind')
  await importList(page, ['泛用型布里', '试作型布里MKII', '特装型布里MKIII'].map(name => ({name, level: 99})))
  await expect(page.locator('.mind-ship-table tbody tr')).toHaveCount(3)
  await expect(page.locator('.mind-totals strong').first()).toHaveText('0')
  await expect(page.locator('.mind-ship-table .mind-rarity')).toHaveText(['精锐', '超稀有', '海上传奇'])
  for (let i = 1; i <= 3; i++) {
    await expect(page.getByRole('combobox', {name: `状态 ${i}`, exact: true})).toBeDisabled()
    await expect(page.getByRole('combobox', {name: `状态 ${i}`, exact: true})).toContainText('已排除')
  }
  await page.reload()
  await choose(page, '筛选状态', '已排除')
  await expect(page.locator('.mind-ship-table tbody tr')).toHaveCount(3)
  await expect(page.locator('.mind-totals strong').first()).toHaveText('0')
})

test('试算状态和删除更新费用，空清单草稿重载后保持为空', async ({page}) => {
  test.setTimeout(90_000)
  await page.goto('/#/i/demo-alt/task/FleetAnalysis?tab=mind')
  await importList(page, [{name: '拉菲', level: 100}, {name: '约克城II', level: 100}])
  await expect(page.locator('.mind-totals strong').first()).toHaveText('4,620')
  for (const [status, total] of [['已排除', '3,300'], ['待核对', '3,300'], ['计入舰船', '4,620']]) {
    await choose(page, '状态 1', status)
    await expect(page.locator('.mind-totals strong').first()).toHaveText(total)
    await expect(page.locator('.mind-intro')).toContainText('本地试算')
    await page.reload()
    await expect(page.getByRole('combobox', {name: '状态 1', exact: true})).toContainText(status)
  }
  await page.getByRole('button', {name: '删除 拉菲', exact: true}).click()
  await expect(page.locator('.mind-totals strong').first()).toHaveText('3,300')
  await expect(page.locator('.mind-totals strong').nth(2)).toHaveText('1')
  await expect(page.locator('.mind-intro')).toContainText('本地试算')
  await page.reload()
  await expect(page.getByLabel('船名 1', {exact: true})).toHaveValue('约克城II')
  await page.getByRole('button', {name: '删除 约克城II', exact: true}).click()
  await expect(page.locator('.mind-totals strong').first()).toHaveText('0')
  await expect(page.locator('.mind-intro')).toContainText('本地试算')
  await page.reload()
  await expect(page.locator('.mind-ship-table tbody tr')).toHaveCount(0)
})

test('名称、等级范围、基础稀有度与状态联合筛选，边界包含且不修改总额', async ({page}) => {
  await page.goto('/#/i/demo-alt/task/FleetAnalysis?tab=mind')
  await importList(page, [{name: '拉菲', level: 100}, {name: '卡辛.改', level: 105}, {name: '约克城II', level: 110}, {name: 'DEAD MASTER', level: 100}])
  await expect(page.locator('.mind-ship-table tbody tr')).toHaveCount(4)
  await expect(page.locator('.mind-totals strong').first()).toHaveText('4,320')
  const totals = await page.locator('.mind-totals').innerText()
  await page.locator('.mind-panel').last().screenshot({path: test.info().outputPath('mind-list-controls.png')})
  await page.getByLabel('最低等级', {exact: true}).fill('100')
  await page.getByLabel('最高等级', {exact: true}).fill('105')
  await expect(page.locator('.mind-ship-table tbody tr')).toHaveCount(3)
  await choose(page, '筛选基础稀有度', '普通')
  await expect(page.locator('.mind-ship-table tbody tr')).toHaveCount(1)
  await expect(page.getByLabel('船名 2', {exact: true})).toHaveValue('卡辛.改')
  await choose(page, '筛选基础稀有度', '全部稀有度')
  await choose(page, '筛选状态', '已排除')
  await page.getByLabel('搜索船名', {exact: true}).fill('DEAD')
  await expect(page.locator('.mind-ship-table tbody tr')).toHaveCount(1)
  await expect(page.getByLabel('船名 4', {exact: true})).toHaveValue('DEAD MASTER')
  expect(await page.locator('.mind-totals').innerText()).toBe(totals)
  await page.setViewportSize({width: 390, height: 844})
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  await page.screenshot({path: test.info().outputPath('mind-filters-mobile.png'), fullPage: true})
})

test('试算第二页删除按原行索引操作，草稿重载且过滤页码有效', async ({page}) => {
  await page.goto('/#/i/demo-alt/task/FleetAnalysis?tab=mind')
  await importList(page, Array.from({length: 52}, (_, index) => ({name: `分页舰${index + 1}`, level: 100, base_rarity: 'SR'})))
  await expect(page.locator('.mind-intro')).toContainText('本地试算')
  await page.locator('.mind-pagination').getByRole('button', {name: '下一页', exact: true}).click()
  await expect(page.locator('.mind-ship-table tbody tr')).toHaveCount(2)
  await page.getByRole('button', {name: '删除 分页舰51', exact: true}).click()
  await expect(page.locator('.mind-pagination')).toContainText('2 / 2')
  await expect(page.getByLabel('船名 51', {exact: true})).toHaveValue('分页舰52')
  await expect(page.locator('.mind-intro')).toContainText('本地试算')
  await page.getByLabel('最高等级', {exact: true}).fill('99')
  await expect(page.locator('.mind-pagination')).toContainText('1 / 1')
  await page.reload()
  await page.getByLabel('搜索船名', {exact: true}).fill('分页舰51')
  await expect(page.locator('.mind-ship-table tbody tr')).toHaveCount(0)
})
