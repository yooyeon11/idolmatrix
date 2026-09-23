import { defineStore } from 'pinia'
import { ref } from 'vue'

export type ThemeMode = 'light' | 'dark'
// 紫色（紫罗兰）档已移除；老用户 localStorage 里的 'violet' 会被下面的校验挡掉并回落到默认
export type ThemeAccent = 'rose' | 'blue'

const STORAGE_KEY = 'kpml_theme'
const ACCENT_KEY = 'kpml_accent'

const ACCENTS: ThemeAccent[] = ['rose', 'blue']

/**
 * 外观改动的对外通知钩子（由 settings store 在初始化时注册）。
 *
 * 2026-09-22 起外观与显示改为**跨设备同步**（存后端 app_settings.appearance）：
 * 切换动作发生在任意位置（页头主题按钮 / 设置页），都要把改动推给后端。
 * 这里用钩子而不是直接 import settings store —— 两个 store 互相 import 会形成循环依赖。
 */
let appearanceChangeHook: (() => void) | null = null

export function setAppearanceChangeHook(fn: (() => void) | null) {
  appearanceChangeHook = fn
}

// 默认保持当前视觉（深色），仅在用户显式切换后持久化
function resolveInitial(): ThemeMode {
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved === 'light' || saved === 'dark') return saved
  return 'dark'
}

function resolveInitialAccent(): ThemeAccent {
  const saved = localStorage.getItem(ACCENT_KEY)
  if (saved && (ACCENTS as string[]).includes(saved)) return saved as ThemeAccent
  // 非法值（如被移除的 'violet'）顺手清掉，避免每次启动都白校验一遍
  if (saved) localStorage.removeItem(ACCENT_KEY)
  return 'blue'
}

export const useThemeStore = defineStore('theme', () => {
  const mode = ref<ThemeMode>(resolveInitial())
  const accent = ref<ThemeAccent>(resolveInitialAccent())

  function apply(m: ThemeMode = mode.value) {
    document.documentElement.setAttribute('data-theme', m)
    const meta = document.querySelector('meta[name="theme-color"]')
    if (meta) meta.setAttribute('content', m === 'dark' ? '#0e0e12' : '#f7f8fa')
  }

  function applyAccent(a: ThemeAccent = accent.value) {
    document.documentElement.setAttribute('data-accent', a)
  }

  function setMode(m: ThemeMode) {
    mode.value = m
    localStorage.setItem(STORAGE_KEY, m)
    apply(m)
    appearanceChangeHook?.()
  }

  function setAccent(a: ThemeAccent) {
    accent.value = a
    localStorage.setItem(ACCENT_KEY, a)
    applyAccent(a)
    appearanceChangeHook?.()
  }

  /**
   * 应用**来自后端**的外观值：只落本地并应用到 DOM，**不**触发回写
   * （否则「保存 → 回读 → 再保存」会绕成环）。值与当前一致时跳过，省掉多余的 DOM 写入。
   */
  function applyRemote(nextTheme: string, nextAccent: string) {
    const t: ThemeMode = nextTheme === 'light' ? 'light' : 'dark'
    const a: ThemeAccent = (ACCENTS as string[]).includes(nextAccent)
      ? (nextAccent as ThemeAccent)
      : 'blue'
    if (t !== mode.value) {
      mode.value = t
      localStorage.setItem(STORAGE_KEY, t)
      apply(t)
    }
    if (a !== accent.value) {
      accent.value = a
      localStorage.setItem(ACCENT_KEY, a)
      applyAccent(a)
    }
  }

  function toggle() {
    setMode(mode.value === 'dark' ? 'light' : 'dark')
  }

  // 启动时同步到 <html>，避免刷新后主题闪烁
  function init() {
    apply()
    applyAccent()
  }

  return { mode, accent, setMode, setAccent, applyRemote, toggle, init }
})
