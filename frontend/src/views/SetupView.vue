<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useSettingsStore } from '@/stores/settings'
import ThemeToggle from '@/components/ThemeToggle.vue'
import SaField from '@/components/SaField.vue'
import SaInput from '@/components/SaInput.vue'
import SaButton from '@/components/SaButton.vue'
import './auth-shared.css'

const router = useRouter()
const auth = useAuthStore()

const username = ref('owner')
const displayName = ref('')
const password = ref('')
const confirm = ref('')
const token = ref('')
const error = ref('')
// 字段级错误：分别挂在「密码」与「确认密码」下方（HeroUI 的 FieldError 就是字段级的）
const passwordError = ref('')
const confirmError = ref('')
const loading = ref(false)

async function submit() {
  error.value = ''
  passwordError.value = ''
  confirmError.value = ''
  // 先做字段级校验：这样错误能挂到对应字段下方，而不是笼统弹在表单上
  if (password.value.length < 8) {
    passwordError.value = '密码至少 8 个字符'
    return
  }
  if (password.value !== confirm.value) {
    confirmError.value = '两次输入的密码不一致'
    return
  }
  loading.value = true
  try {
    await auth.bootstrap({
      username: username.value.trim(),
      password: password.value,
      display_name: displayName.value.trim(),
      token: token.value.trim(),
    })
    await useSettingsStore().hydrate()
    await router.replace('/')
  } catch (e) {
    error.value = (e as Error).message || '初始化失败'
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
    <form class="auth-card" @submit.prevent="submit">
      <header class="auth-head">
        <h1>创建管理员账号</h1>
        <p class="sub">这是本库唯一账号。创建后用同一组用户名和密码登录。</p>
      </header>

      <div class="auth-form">
        <SaField label="用户名" required>
          <SaInput v-model="username" autocomplete="username" required />
        </SaField>

        <SaField label="显示名" description="可选，仅用于界面显示">
          <SaInput v-model="displayName" autocomplete="nickname" />
        </SaField>

        <SaField
          label="密码"
          description="至少 8 个字符"
          required
          :error="passwordError"
        >
          <SaInput
            v-model="password"
            type="password"
            autocomplete="new-password"
            required
            :invalid="!!passwordError"
          />
        </SaField>

        <SaField label="确认密码" required :error="confirmError">
          <SaInput
            v-model="confirm"
            type="password"
            autocomplete="new-password"
            required
            :invalid="!!confirmError"
          />
        </SaField>

        <SaField v-if="auth.bootstrapTokenRequired" label="初始化口令" required>
          <SaInput v-model="token" type="password" autocomplete="off" />
        </SaField>

        <SaField :error="error" />

        <SaButton type="submit" :loading="loading" block>
          {{ loading ? '创建中…' : '完成并进入' }}
        </SaButton>
      </div>
    </form>
  </div>
</template>
