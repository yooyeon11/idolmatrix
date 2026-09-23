import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { setUnauthorizedHandler } from '@/api/index'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'
import { useSettingsStore } from '@/stores/settings'

// 说明：此处原本无条件 import 'vfonts/Lato.css' 与 'vfonts/FiraCode.css'，但它们注册的
// 家族名是 v-sans / v-mono，全项目从未引用（hero 区字体栈里写的 'Lato' 从来没有对应的
// @font-face，一直是哑声明，实际落系统字体），属于纯死重（约 200KB woff2），故移除。
// 视频播放器样式（xgplayer 原生控制栏，在 VideoPlayer.vue 内按需引入）
// 正文中文字体（阿里巴巴普惠体 3.0，本地自托管分片）
import './styles/font-puhuiti.css'
// Hero 名字的汉字字体（思源宋体 Noto Serif SC，本地自托管分片，仅 .hero-cover-name--han 使用）
// Hero 名字的汉字字体（思源宋体 Noto Serif SC，字体文件在 public/fonts/，仅 .hero-cover-name--han 使用）
import './styles/font-noto-serif-sc.css'
import './styles/main.css'
// 资料库控件统一口径（字段 / 下拉 / 按钮 / 行 / 空态）—— 须在 main.css 之后，保证同优先级时后注入
import './styles/db-controls.css'

const pinia = createPinia()
const app = createApp(App)
app.use(pinia)
app.use(router)

// 在挂载前应用已保存的主题，避免首屏闪烁
useThemeStore(pinia).init()
const auth = useAuthStore(pinia)
const settings = useSettingsStore(pinia)
setUnauthorizedHandler(() => {
  auth.clear()
  const current = router.currentRoute.value
  if (current.meta.public) return
  router.replace({ name: 'login', query: { redirect: current.fullPath } })
})
auth
  .loadStatus()
  .then(() => {
    // ⚠ 免鉴权部署（AUTH_REQUIRED=false）下 authenticated 恒为 false，
    // 但 GET /app-settings 本来就不要求登录 → 这里必须照样 hydrate，
    // 否则该客户端永远拿不到后端设置，各客户端之间看起来「设置没同步」。
    if (auth.authenticated || !auth.authRequired) return settings.hydrate()
    return undefined
  })
  .finally(() => {
    app.mount('#app')
  })
