import {expect, test, type Page} from '@playwright/test'

async function installBridge(page: Page, backgroundSupported = true, deferredRead = false) {
  await page.addInitScript(({backgroundSupported, deferredRead}) => {
    localStorage.setItem('azurpilot.language', 'zh-CN')
    const state = window as typeof window & {
      __ALAS_NATIVE__: {invoke: (command: string, args?: {behavior?: string}) => Promise<unknown>}
      closeWrites: number
      failCloseSave: boolean
      finishCloseSave?: () => void
      finishCloseRead?: () => void
    }
    state.closeWrites = 0
    state.failCloseSave = false
    const read = () => ({closeBehavior: backgroundSupported ? localStorage.getItem('test.closeBehavior') ?? 'ask' : 'exit', backgroundSupported})
    state.__ALAS_NATIVE__ = {invoke: async (command, args) => {
      if (command === 'get_launcher_preferences') {
        if (deferredRead) await new Promise<void>(resolve => {state.finishCloseRead = resolve})
        return read()
      }
      if (command !== 'set_launcher_close_behavior') throw new Error('Unexpected native command')
      state.closeWrites++
      await new Promise<void>(resolve => {state.finishCloseSave = resolve})
      if (state.failCloseSave) throw new Error('Disk unavailable')
      localStorage.setItem('test.closeBehavior', args!.behavior!)
      return read()
    }}
  }, {backgroundSupported, deferredRead})
}

async function choose(page: Page, option: string) {
  await page.getByRole('combobox', {name: '关闭主窗口时', exact: true}).click()
  await page.getByRole('option', {name: option, exact: true}).click()
}

test('普通浏览器与原生读取失败均隐藏关闭设置', async ({page}) => {
  await page.goto('/#/settings')
  await expect(page.getByRole('heading', {name: '系统设置', exact: true})).toBeVisible()
  await expect(page.locator('#launcher-close-behavior')).toHaveCount(0)
  await page.addInitScript(() => {
    Object.assign(window, {__ALAS_NATIVE__: {invoke: async () => {throw new Error('Not trusted')}}})
  })
  await page.reload()
  await expect(page.getByRole('heading', {name: '系统设置', exact: true})).toBeVisible()
  await expect(page.locator('#launcher-close-behavior')).toHaveCount(0)
})

test('关闭偏好独立于旧状态通道，保存期间禁止重复提交，重载恢复偏好', async ({page}) => {
  await installBridge(page)
  await page.route('**/api/launcher/status', route => route.fulfill({status: 404, body: '{}'}))
  await page.goto('/#/settings')
  const control = page.getByRole('combobox', {name: '关闭主窗口时', exact: true})
  await expect(control).toContainText('每次询问')
  for (const [option, value] of [['后台运行', 'tray'], ['退出应用', 'exit'], ['每次询问', 'ask']]) {
    await choose(page, option!)
    await expect(control).toBeDisabled()
    await page.evaluate(() => {
      const select = document.querySelector<HTMLSelectElement>('.field-control select:has(option[value="tray"])')!
      select.value = 'exit'
      select.dispatchEvent(new Event('change', {bubbles: true}))
    })
    expect(await page.evaluate(() => (window as unknown as {closeWrites: number}).closeWrites)).toBe(1)
    await page.evaluate(() => (window as unknown as {finishCloseSave: () => void}).finishCloseSave())
    await expect(control).toBeEnabled()
    await expect(control).toContainText(option!)
    expect(await page.evaluate(() => localStorage.getItem('test.closeBehavior'))).toBe(value)
    await page.reload()
    await expect(control).toContainText(option!)
  }
})

test('保存失败回滚，可再次更改并保存', async ({page}) => {
  await installBridge(page)
  await page.goto('/#/settings')
  await page.evaluate(() => {Object.assign(window, {failCloseSave: true})})
  await choose(page, '退出应用')
  await page.evaluate(() => (window as unknown as {finishCloseSave: () => void}).finishCloseSave())
  await expect(page.getByRole('alert').filter({hasText: '保存失败，请重试。'})).toBeVisible()
  await expect(page.getByRole('combobox', {name: '关闭主窗口时', exact: true})).toContainText('每次询问')
  await page.evaluate(() => {Object.assign(window, {failCloseSave: false})})
  await choose(page, '后台运行')
  await page.evaluate(() => (window as unknown as {finishCloseSave: () => void}).finishCloseSave())
  await expect(page.getByRole('status').filter({hasText: '已保存'})).toBeVisible()
})

test('不支持后台的平台只提供退出应用', async ({page}) => {
  await installBridge(page, false)
  await page.goto('/#/settings')
  const control = page.getByRole('combobox', {name: '关闭主窗口时', exact: true})
  await expect(control).toContainText('退出应用')
  await expect(page.getByText('当前平台仅支持退出应用。')).toBeVisible()
  await control.click()
  await expect(page.getByRole('option')).toHaveCount(1)
  await expect(page.getByRole('option', {name: '退出应用', exact: true})).toBeVisible()
})

test('离开设置页后忽略尚未返回的原生读取', async ({page}) => {
  await installBridge(page, true, true)
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('/#/settings')
  await expect.poll(() => page.evaluate(() => typeof (window as unknown as {finishCloseRead?: unknown}).finishCloseRead)).toBe('function')
  await page.evaluate(() => {location.hash = '#/interface'})
  await expect(page.getByRole('heading', {name: '界面设置', exact: true})).toBeVisible()
  await page.evaluate(() => (window as unknown as {finishCloseRead: () => void}).finishCloseRead())
  await expect(page.locator('#launcher-close-behavior')).toHaveCount(0)
  expect(errors).toEqual([])
})
