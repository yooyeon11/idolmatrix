import http from './index'
import type { AppSettings } from '@/types/models'

export const appSettingsApi = {
  get() {
    return http.get<AppSettings>('/app-settings').then((r) => r.data)
  },
  patch(payload: Partial<AppSettings>) {
    return http.patch<AppSettings>('/app-settings', payload).then((r) => r.data)
  },
}
