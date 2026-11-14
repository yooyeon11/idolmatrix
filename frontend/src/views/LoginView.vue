<script setup lang="ts">
/**
 * 登录页 · 方案 B「居中悬浮玻璃岛」
 * 严格复刻 gemini-code-1792370397938.html 的方案 B：
 *   - 440px 居中流光玻璃岛（blur 28 / 半径 28 / 内高光边）
 *   - 背景两团流光氛围光斑（600×350 紫 / 500×300 蓝，均 blur 150）
 *   - 玻璃输入框（聚焦时紫色外发光）、物理滑动「信任此设备」开关
 *   - 入场 fadeIn 0.45s expo、错误抖动 0.4s expo、按钮 hover 1.02 / active 0.98
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useSettingsStore } from '@/stores/settings'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const REMEMBER_KEY = 'kpml_login_username'

const username = ref('')
const password = ref('')
const showPassword = ref(false)
const trustDevice = ref(true)
const loading = ref(false)
const shaking = ref(false)
const nasOnline = ref<boolean | null>(null)
const lastError = ref('')

// 轻提示：复刻设计稿右上角白色胶囊 toast
const toastText = ref('')
const toastVisible = ref(false)
const toastError = ref(false)
let toastTimer: ReturnType<typeof setTimeout> | undefined

function showToast(text: string, isError = false) {
  toastText.value = text
  toastError.value = isError
  toastVisible.value = true
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => {
    toastVisible.value = false
  }, 2500)
}

function shake() {
  shaking.value = false
  // 强制重排，保证连续两次错误都能重新播放动画
  requestAnimationFrame(() => {
    shaking.value = true
    setTimeout(() => (shaking.value = false), 460)
  })
}

async function checkNas() {
  try {
    const r = await fetch('/api/system/health', { credentials: 'same-origin' })
    nasOnline.value = r.ok
  } catch {
    nasOnline.value = false
  }
}

async function submit() {
  lastError.value = ''
  if (!username.value.trim() || !password.value) {
    shake()
    showToast('请输入完整账户与口令凭证', true)
    return
  }
  loading.value = true
  try {
    await auth.login(username.value.trim(), password.value)
    // 信任此设备 = 记住账户名（后端会话本身由 httpOnly cookie 维持，没有「长会话」开关）
    if (trustDevice.value) localStorage.setItem(REMEMBER_KEY, username.value.trim())
    else localStorage.removeItem(REMEMBER_KEY)
    showToast('通行证验证成功，正在连接媒体库...')
    await useSettingsStore().hydrate()
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    // 留一点时间让 toast 显示出来
    setTimeout(() => void router.replace(redirect.startsWith('/') ? redirect : '/'), 420)
  } catch (e) {
    const err = e as Error & { status?: number; retryAfter?: number }
    if (err.status === 429) {
      const sec = err.retryAfter
      lastError.value = sec ? `尝试次数过多，请 ${sec} 秒后再试` : '尝试次数过多，请稍后再试'
    } else {
      lastError.value = '用户名或密码不正确'
    }
    shake()
    showToast(lastError.value, true)
  } finally {
    loading.value = false
  }
}

const statusLabel = computed(() => {
  if (nasOnline.value === null) return '● 检测中'
  return nasOnline.value ? '● 媒体库在线' : '● 媒体库离线'
})

onMounted(() => {
  const saved = localStorage.getItem(REMEMBER_KEY)
  if (saved) {
    username.value = saved
    trustDevice.value = true
  }
  void checkNas()
})
</script>

<template>
  <div class="login-page">
    <!-- 背景流光氛围光斑 -->
    <div class="glow glow--violet" aria-hidden="true"></div>
    <div class="glow glow--blue" aria-hidden="true"></div>

    <!-- 全局轻提示 -->
    <div
      class="auth-toast"
      :class="{ 'is-visible': toastVisible, 'is-error': toastError }"
      role="status"
      aria-live="polite"
    >
      <span class="auth-toast-dot"></span>
      <span>{{ toastText }}</span>
    </div>

    <!-- 主画布：居中玻璃岛 -->
    <main class="login-viewport">
      <div class="view-anim login-shell">
        <form
          class="spatial-glass login-card"
          :class="{ 'error-shake': shaking }"
          @submit.prevent="submit"
        >
          <!-- 岛头 -->
          <header class="island-head">
            <div class="brand-serif">IDOLMATRIX.</div>
          </header>

          <div class="island-form">
            <!-- 账户 -->
            <div class="field">
              <label class="field-label" for="auth-user-input">登录账户：</label>
              <div class="glass-input">
                <input
                  id="auth-user-input"
                  v-model="username"
                  type="text"
                  name="username"
                  autocomplete="username"
                  placeholder="输入账户..."
                  :disabled="loading"
                />
              </div>
            </div>

            <!-- 口令 -->
            <div class="field">
              <div class="field-row">
                <label class="field-label" for="auth-password-input">访问密钥 / 密码：</label>
                <button
                  class="pwd-toggle"
                  type="button"
                  :aria-label="showPassword ? '隐藏口令' : '显示口令'"
                  @click="showPassword = !showPassword"
                >
                  <svg v-if="showPassword" viewBox="0 0 24 24" width="16" height="16" fill="none"
                       stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M3 3l18 18" />
                    <path d="M10.6 10.6a2 2 0 0 0 2.8 2.8" />
                    <path d="M9.4 5.4A9.7 9.7 0 0 1 12 5c5 0 8.6 4.4 9.7 6.2a1.6 1.6 0 0 1 0 1.6c-.4.6-1.2 1.7-2.4 2.8" />
                    <path d="M6.2 7.1C4.6 8.4 3.5 10 2.9 11a1.6 1.6 0 0 0 0 1.6C4 14.4 7 19 12 19a9.6 9.6 0 0 0 3.3-.6" />
                  </svg>
                  <svg v-else viewBox="0 0 24 24" width="16" height="16" fill="none"
                       stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M2.3 12S5.9 5 12 5s9.7 7 9.7 7-3.6 7-9.7 7S2.3 12 2.3 12Z" />
                    <circle cx="12" cy="12" r="3" />
                  </svg>
                </button>
              </div>
              <div class="glass-input">
                <input
                  id="auth-password-input"
                  v-model="password"
                  :type="showPassword ? 'text' : 'password'"
                  name="password"
                  autocomplete="current-password"
                  placeholder="输入访问密钥..."
                  :disabled="loading"
                />
              </div>
            </div>

            <!-- 信任此设备 + 状态 -->
            <div class="island-meta">
              <label class="trust">
                <span
                  class="spatial-switch"
                  :class="{ 'is-active': trustDevice }"
                  role="switch"
                  :aria-checked="trustDevice"
                  tabindex="0"
                  @click.prevent="trustDevice = !trustDevice"
                  @keydown.space.prevent="trustDevice = !trustDevice"
                  @keydown.enter.prevent="trustDevice = !trustDevice"
                >
                  <span class="handle"></span>
                </span>
                <span class="trust-text">信任此设备</span>
              </label>
              <span
                class="nas-status"
                :class="{ 'is-off': nasOnline === false }"
              >{{ statusLabel }}</span>
            </div>

            <!-- 认证按钮 -->
            <button class="auth-submit" type="submit" :disabled="loading">
              {{ loading ? '认证中…' : '认证登录 ↵' }}
            </button>
          </div>
        </form>

        <!-- 仅在出错后才提示找回口令的方式，保持常态未出错时纯净 -->
        <p v-if="lastError" class="reset-hint">
          忘记密码？在服务器上执行
          <code>docker exec -it kpop-media python -m app.cli reset-owner-password</code>
        </p>
      </div>
    </main>
  </div>
</template>

<style scoped>
/* ===== 页面基底 ===== */
.login-page {
  position: relative;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  background: #08080b;
  color: #f4f4f6;
  overflow-x: hidden;
}

/* ===== 背景流光光斑 ===== */
.glow {
  position: fixed;
  border-radius: 9999px;
  pointer-events: none;
  z-index: 0;
  filter: blur(150px);
}
.glow--violet {
  top: 48px;
  left: 25%;
  width: 600px;
  height: 350px;
  background: rgba(139, 92, 246, 0.1);
}
.glow--blue {
  bottom: 48px;
  right: 25%;
  width: 500px;
  height: 300px;
  background: rgba(37, 99, 235, 0.1);
}

/* ===== 主画布 ===== */
.login-viewport {
  position: relative;
  z-index: 10;
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 64px 0 40px;
}
.login-shell {
  width: 100%;
  max-width: 440px;
  padding: 0 24px;
}

/* ===== 流光空间玻璃 ===== */
.spatial-glass {
  background: rgba(18, 19, 28, 0.75);
  backdrop-filter: blur(28px);
  -webkit-backdrop-filter: blur(28px);
  border: 1px solid rgba(255, 255, 255, 0.1);
  box-shadow:
    0 30px 90px rgba(0, 0, 0, 0.95),
    0 30px 80px -20px rgba(0, 0, 0, 0.8),
    inset 0 1px 0 0 rgba(255, 255, 255, 0.12);
  border-radius: 28px;
}
.login-card {
  /* isolation 让 ::before 的负 z-index 只在本卡片内生效 */
  isolation: isolate;
  padding: 32px;
  display: flex;
  flex-direction: column;
  gap: 32px;
}
/* 模糊与底色放到伪元素上：卡片内的文字就落在普通图层，恢复清晰的文字渲染 */
.login-card::before {
  content: '';
  position: absolute;
  inset: 0;
  z-index: -1;
  border-radius: inherit;
  background: rgba(18, 19, 28, 0.75);
  backdrop-filter: blur(28px);
  -webkit-backdrop-filter: blur(28px);
}
.login-card {
  position: relative;
  background: transparent;
  /* 取消 .spatial-glass 落在卡片本体上的 blur，否则整卡内容又被合成，字还是糊的 */
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
}
@media (min-width: 640px) {
  .login-card {
    padding: 40px;
  }
}

/* ===== 岛头 ===== */
.island-head {
  text-align: center;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.brand-serif {
  font-family: 'Noto Serif SC', 'Instrument Serif', serif;
  font-size: 30px;
  font-weight: 900;
  letter-spacing: -0.02em;
  color: #fff;
  line-height: 1.1;
}
/* ===== 表单 ===== */
.island-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.field-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}
.field-label {
  font-size: 12px;
  color: #a1a1aa;
  letter-spacing: 0.02em;
}
.pwd-toggle {
  padding: 0;
  border: none;
  background: transparent;
  font-size: 11px;
  line-height: 1;
  color: #71717a;
  cursor: pointer;
  transition: color 0.2s ease;
}
.pwd-toggle:hover {
  color: #fff;
}

/* 高定输入框 */
.glass-input {
  display: flex;
  align-items: center;
  padding: 12px 16px;
  background: rgba(0, 0, 0, 0.55);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 16px;
  transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
}
.glass-input:focus-within {
  border-color: rgba(255, 255, 255, 0.4);
  box-shadow: 0 0 20px rgba(139, 92, 246, 0.25);
}
.glass-input input {
  width: 100%;
  border: none;
  outline: none;
  background: transparent;
  color: #fff;
  /* 等宽字体排中文会发虚：这里用全站 UI 字体栈，并加大字号/字重提升清晰度 */
  font-family: inherit;
  font-size: 15px;
  font-weight: 500;
  letter-spacing: 0.01em;
  line-height: 1.5;
}
/* 口令遮挡：用系统字体渲染圆点，避免继承衬线体导致「方块」 */
.glass-input input[type='password'] {
  font-family: system-ui, -apple-system, 'Segoe UI', sans-serif;
  letter-spacing: 0.12em;
}
.glass-input input::placeholder {
  color: #7c7c86;
  font-weight: 400;
}
.glass-input input:disabled {
  opacity: 0.6;
}

/* ===== 信任此设备 + NAS 状态 ===== */
.island-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-top: 4px;
  font-size: 12px;
}
.trust {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  user-select: none;
}
.trust-text {
  color: #d4d4d8;
}
.spatial-switch {
  position: relative;
  display: inline-block;
  width: 44px;
  height: 24px;
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.12);
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 9999px;
  cursor: pointer;
  transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
}
.spatial-switch.is-active {
  background: #0485f7;
  border-color: #3ba7ff;
  box-shadow: 0 0 14px rgba(4, 133, 247, 0.45);
}
.spatial-switch .handle {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 18px;
  height: 18px;
  background: #fff;
  border-radius: 9999px;
  transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1);
}
.spatial-switch.is-active .handle {
  transform: translateX(20px);
}
.spatial-switch:focus-visible {
  outline: 2px solid rgba(4, 133, 247, 0.7);
  outline-offset: 2px;
}

.nas-status {
  font-family: inherit;
  font-size: 12px;
  font-weight: 500;
  color: #34d399;
  white-space: nowrap;
}
.nas-status.is-off {
  color: #f59e0b;
}

/* ===== 认证按钮 ===== */
.auth-submit {
  width: 100%;
  margin-top: 4px;
  padding: 14px;
  border: none;
  border-radius: 16px;
  background: #fff;
  color: #000;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.04em;
  box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.6);
  cursor: pointer;
  transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
}
.auth-submit:hover:not(:disabled) {
  transform: scale(1.02);
}
.auth-submit:active:not(:disabled) {
  transform: scale(0.98);
}
.auth-submit:disabled {
  cursor: default;
  opacity: 0.8;
}

/* ===== 岛底 / 页脚 ===== */
.reset-hint {
  margin: 16px auto 0;
  text-align: center;
  font-family: inherit;
  font-size: 11px;
  line-height: 1.7;
  color: #7c7c86;
}
.reset-hint code {
  display: inline-block;
  color: #a1a1aa;
  word-break: break-all;
}

/* ===== 轻提示 ===== */
.auth-toast {
  position: fixed;
  top: 64px;
  right: 32px;
  z-index: 300;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  border-radius: 16px;
  background: #fff;
  color: #08080b;
  font-family: var(--sa-mono, 'JetBrains Mono', ui-monospace, monospace);
  font-size: 12px;
  font-weight: 700;
  box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.8);
  opacity: 0;
  transform: translateY(8px);
  pointer-events: none;
  transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
}
.auth-toast.is-visible {
  opacity: 1;
  transform: translateY(0);
}
.auth-toast-dot {
  width: 6px;
  height: 6px;
  border-radius: 9999px;
  background: #10b981;
  flex-shrink: 0;
}
.auth-toast.is-error .auth-toast-dot {
  background: #ef4444;
}

/* ===== 动效 ===== */
@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(14px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
.view-anim {
  animation: fadeIn 0.45s cubic-bezier(0.19, 1, 0.22, 1) forwards;
}

@keyframes shakeAnim {
  0%,
  100% {
    transform: translateX(0);
  }
  20%,
  60% {
    transform: translateX(-6px);
  }
  40%,
  80% {
    transform: translateX(6px);
  }
}
.error-shake {
  animation: shakeAnim 0.4s cubic-bezier(0.19, 1, 0.22, 1);
}

@media (prefers-reduced-motion: reduce) {
  .view-anim,
  .error-shake {
    animation: none;
  }
  .auth-submit,
  .glass-input,
  .spatial-switch,
  .spatial-switch .handle {
    transition: none;
  }
}

@media (max-width: 480px) {
  .login-shell {
    padding: 0 16px;
  }
  .login-card {
    padding: 26px 22px;
    gap: 26px;
  }
  .brand-serif {
    font-size: 26px;
  }
  .auth-toast {
    left: 16px;
    right: 16px;
    top: 16px;
  }
}
</style>
