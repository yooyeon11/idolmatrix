import http from './index'

export type ApiTokenStatus = 'active' | 'expired' | 'revoked'

export interface ApiTokenRow {
  id: number
  name: string
  /** 明文前 12 位，用来区分是哪张卡 */
  hint: string
  scopes: string[]
  expires_at: string | null
  revoked_at: string | null
  last_used_at: string | null
  last_used_ip: string | null
  created_by: string | null
  note: string | null
  status: ApiTokenStatus
  created_at: string | null
}

export interface ApiTokenCreated extends ApiTokenRow {
  /** 明文，只在这一条响应里出现一次 */
  token: string
}

export interface ApiTokenCreatePayload {
  name: string
  scopes: string[]
  /** 0 或 null = 长期有效 */
  expires_days?: number | null
  note?: string | null
}

export const apiTokensApi = {
  list() {
    return http.get<ApiTokenRow[]>('/api-tokens').then((r) => r.data)
  },
  create(payload: ApiTokenCreatePayload) {
    return http.post<ApiTokenCreated>('/api-tokens', payload).then((r) => r.data)
  },
  revoke(id: number) {
    return http.post(`/api-tokens/${id}/revoke`).then((r) => r.data)
  },
}

export interface AuditLogRow {
  id: number
  request_id: string
  actor_type: string
  actor_label: string
  method: string
  path: string
  entity_type: string | null
  entity_id: number | null
  action: string
  status_code: number
  changes: Record<string, { from: string; to: string }> | null
  ip: string | null
  duration_ms: number
  created_at: string | null
}

export const auditLogsApi = {
  list(params: { limit?: number; offset?: number; entity_type?: string; entity_id?: number } = {}) {
    return http
      .get<{ items: AuditLogRow[] }>('/audit-logs', { params })
      .then((r) => r.data.items)
  },
}
