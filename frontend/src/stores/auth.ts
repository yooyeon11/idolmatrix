import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { authApi, type AuthStatus, type AuthUser } from '@/api/auth'

export const useAuthStore = defineStore('auth', () => {
  const ready = ref(false)
  const authenticated = ref(false)
  const bootstrapRequired = ref(false)
  const bootstrapTokenRequired = ref(false)
  const authRequired = ref(true)
  const user = ref<AuthUser | null>(null)

  function applyStatus(s: AuthStatus) {
    authenticated.value = !!s.authenticated
    bootstrapRequired.value = !!s.bootstrap_required
    bootstrapTokenRequired.value = !!s.bootstrap_token_required
    authRequired.value = s.auth_required !== false
    user.value = s.user
  }

  function clear() {
    authenticated.value = false
    user.value = null
  }

  async function loadStatus() {
    try {
      const s = await authApi.status()
      applyStatus(s)
    } catch {
      authenticated.value = false
      user.value = null
    } finally {
      ready.value = true
    }
  }

  async function login(username: string, password: string) {
    const r = await authApi.login(username, password)
    user.value = r.user
    authenticated.value = true
    bootstrapRequired.value = false
    return r.user
  }

  async function bootstrap(payload: {
    username: string
    password: string
    display_name?: string
    token?: string
  }) {
    const r = await authApi.bootstrap(payload)
    user.value = r.user
    authenticated.value = true
    bootstrapRequired.value = false
    return r.user
  }

  async function logout() {
    try {
      await authApi.logout()
    } catch {
      /* ignore */
    }
    clear()
  }

  async function logoutAll() {
    try {
      await authApi.logoutAll()
    } catch {
      /* ignore */
    }
    clear()
  }

  function setUser(u: AuthUser) {
    user.value = u
  }

  const isOwner = computed(() => user.value?.role === 'owner')

  return {
    ready,
    authenticated,
    bootstrapRequired,
    bootstrapTokenRequired,
    authRequired,
    user,
    isOwner,
    loadStatus,
    login,
    bootstrap,
    logout,
    logoutAll,
    setUser,
    clear,
  }
})
