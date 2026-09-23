import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { NaiveUiResolver } from 'unplugin-vue-components/resolvers'
import { fileURLToPath, URL } from 'node:url'

// 后端默认监听 8010（固定端口），dev 时代理 /api 与媒体流
const BACKEND = process.env.VITE_BACKEND_URL || 'http://127.0.0.1:8010'

export default defineConfig({
  plugins: [
    vue(),
    AutoImport({
      imports: ['vue', 'vue-router', 'pinia'],
      dts: 'src/auto-imports.d.ts',
    }),
    Components({
      resolvers: [NaiveUiResolver()],
      dts: 'src/components.d.ts',
    }),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    // 局域网内其他设备可通过本机 IP 访问（默认仅 localhost）
    host: true,
    // 允许局域网 IP / 任意 Host 访问，避免 Vite 的 DNS 重绑定防护拦截局域网请求
    allowedHosts: true,
    proxy: {
      '/api': {
        target: BACKEND,
        changeOrigin: true,
        // 局域网设备访问时浏览器 Origin 为本机 IP，与后端 Host 不符会触发
        // 后端 CSRF 校验 403（跨站请求被拒绝），故将 Origin 一并改写为后端
        headers: { origin: BACKEND },
        // 成员详情预览可达数分钟；默认代理超时会过早掐断 → 前端只见「请求失败」
        timeout: 310_000,
        proxyTimeout: 310_000,
      },
    },
  },
})
