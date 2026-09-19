/** 使用随程序分发的本地背景，避免页面打开时请求第三方图片服务。 */
export function Wallpaper() {
  return <div className="wallpaper" aria-hidden="true">
    <img src="/wallpaper.jpg" alt="" decoding="async"/>
  </div>
}
