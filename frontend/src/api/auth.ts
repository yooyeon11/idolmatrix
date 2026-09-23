import http from './index'

export type AuthUser = {
  id: number
  uid: string
  username: string
  display_name: string
  role: string
}

export type AuthStatus = {
  authenticated: boolean
  bootstrap_required: boolean
  auth_required: boolean
  bootstrap_token_required: boolean
  user: AuthUser | null
}

export const authApi = {
  status() {
    return http.get<AuthStatus>('/auth/status').then((r) => r.data)
  },
  login(username: string, password: string) {
    return http.post<{ user: AuthUser }>('/auth/login', { username, password }).then((r) => r.data)
  },
  bootstrap(payload: { username: string; password: string; display_name?: string; token?: string }) {
    return http.post<{ user: AuthUser }>('/auth/bootstrap', payload).then((r) => r.data)
  },
  logout() {
    return http.post('/auth/logout').then(() => undefined)
  },
  logoutAll() {
    return http.post('/auth/logout-all').then(() => undefined)
  },
  me() {
    return http.get<{ user: AuthUser }>('/auth/me').then((r) => r.data)
  },
  // 修改当前用户密码；成功后后端会吊销其他会话并为当前设备签发新 cookie
  changePassword(oldPassword: string, newPassword: string) {
    return http
      .post<{ user: AuthUser }>('/auth/me/password', {
        old_password: oldPassword,
        new_password: newPassword,
      })
      .then((r) => r.data)
  },
  patchMe(payload: { username?: string; display_name?: string }) {
    return http.patch<{ user: AuthUser }>('/auth/me', payload).then((r) => r.data)
  },
  listUsers() {
    return http.get<{ items: AuthUser[] }>('/auth/users').then((r) => r.data)
  },
  createUser(payload: { username: string; password: string; display_name?: string }) {
    return http.post<{ user: AuthUser }>('/auth/users', payload).then((r) => r.data)
  },
  deleteUser(id: number) {
    return http.delete(`/auth/users/${id}`).then(() => undefined)
  },
}
