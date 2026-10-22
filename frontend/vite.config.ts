import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({mode}) => {
  const backend = mode === 'mock' ? `http://127.0.0.1:${process.env.AZURPILOT_MOCK_PORT ?? 22392}` : process.env.AZURPILOT_BACKEND ?? 'http://127.0.0.1:22267'
  return {
    // 静态资源使用相对路径，兼容本地启动器的嵌入页面。
    base: './',
    plugins: [react()],
    server: {
      port: 5173,
      strictPort: true,
      proxy: {
        '/api': { target: backend, ws: true },
        '/healthz': { target: backend },
        // 物品图标走后端的静态目录（module/api/statistics_service.py 挂的
        // research-items / opsi-items），开发时也由同一份后端提供，
        // 不代理的话统计页的图标列全是裂图。
        '/research-items': { target: backend },
        '/opsi-items': { target: backend },
        '/storage-items': { target: backend },
      },
    },
    build: {
      sourcemap: false,
      // 主题通过 ?inline 作为文本注入，保留规则与声明的原始顺序及语法，
      // 避免生产构建额外执行 Lightning CSS 压缩、合并和语法转换。
      // 仅关闭 CSS 压缩，JavaScript 继续使用默认生产优化。
      cssMinify: false,
    },
  }
})
