<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useSettingsStore } from '@/stores/settings'
import ThemeToggle from '@/components/ThemeToggle.vue'
import SaField from '@/components/SaField.vue'
import SaInput from '@/components/SaInput.vue'
import SaButton from '@/components/SaButton.vue'
import './auth-shared.css'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function submit() {
  error.value = ''
  loading.value = true
  try {
    await auth.login(username.value.trim(), password.value)
    await useSettingsStore().hydrate()
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    await router.replace(redirect.startsWith('/') ? redirect : '/')
  } catch (e) {
    const err = e as Error & { status?: number; retryAfter?: number }
    if (err.status === 429) {
      const sec = err.retryAfter
      error.value = sec ? `尝试次数过多，请 ${sec} 秒后再试` : '尝试次数过多，请稍后再试'
    } else {
      error.value = '用户名或密码不正确'
    }
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="auth-page">
    <div class="auth-top">
      <ThemeToggle />
    </div>
    <!-- 卡片规格 = HeroUI v3 Card（无描边 + 圆角 24 + padding 16 + gap 12 + 三层浅投影），
         与照片墙帖子卡片同一套；表单间距 = Form 示例的 gap-4（16px）。 -->
    <form class="auth-card" @submit.prevent="submit">
      <header class="auth-head">
        <h1>登录 idolMatrix</h1>
        <p class="sub">本库需要登录后才能访问</p>
      </header>

      <div class="auth-form">
        <SaField label="用户名" required>
          <SaInput v-model="username" name="username" autocomplete="username" required />
        </SaField>
        <SaField label="密码" required>
          <SaInput
            v-model="password"
            type="password"
            name="password"
            autocomplete="current-password"
            required
          />
        </SaField>

        <!-- 表单级错误：不特指某个字段（凭证错误 / 限流），所以不标红输入框 -->
        <SaField :error="error" />

        <SaButton type="submit" :loading="loading" block>
          {{ loading ? '登录中…' : '登录' }}
        </SaButton>
      </div>

      <p class="auth-hint">
        忘记密码时在 NAS 上执行：
        <code>docker exec -it kpop-media python -m app.cli reset-owner-password</code>
      </p>
    </form>
  </div>
</template>
