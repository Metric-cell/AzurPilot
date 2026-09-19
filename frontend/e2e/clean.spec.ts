import {expect, test} from '@playwright/test'

test('clean 页面只加载本地资源并且不提供更新入口', async ({page, baseURL}) => {
  const externalRequests: string[] = []
  const origin = new URL(baseURL!).origin
  page.on('request', request => {
    const url = new URL(request.url())
    if (['http:', 'https:'].includes(url.protocol) && url.origin !== origin) externalRequests.push(url.href)
  })
  await page.goto('/')
  await expect(page.locator('.instance-card')).toBeVisible()
  await expect(page.locator('a[href*="updater"]')).toHaveCount(0)
  await expect(page.locator('.wallpaper img')).toHaveJSProperty('complete', true)
  expect(await page.locator('.wallpaper img').evaluate(image => (image as HTMLImageElement).naturalWidth)).toBeGreaterThan(0)
  expect(externalRequests).toEqual([])
})
