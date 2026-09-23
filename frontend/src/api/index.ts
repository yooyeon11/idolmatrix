import axios, { AxiosError, type AxiosInstance } from 'axios'

// 所有业务请求走 /api，dev 模式下被 vite 代理到后端
const http: AxiosInstance = axios.create({
  baseURL: '/api',
  timeout: 30_000,
  withCredentials: true,
})

let onUnauthorized: (() => void) | null = null

export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn
}

function formatApiDetail(detail: unknown, fallback: string): string {
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    const parts = detail.map((d) => {
      if (typeof d === 'string') return d
      if (d && typeof d === 'object' && 'msg' in d) return String((d as { msg: unknown }).msg)
      try {
        return JSON.stringify(d)
      } catch {
        return ''
      }
    }).filter(Boolean)
    if (parts.length) return parts.join('；')
  }
  if (detail && typeof detail === 'object') {
    try {
      return JSON.stringify(detail)
    } catch {
      /* ignore */
    }
  }
  return fallback
}

http.interceptors.response.use(
  (res) => res,
  (error: AxiosError<{ detail?: unknown }>) => {
    const timedOut =
      error.code === 'ECONNABORTED' || /timeout/i.test(String(error.message || ''))
    const fallback = timedOut
      ? `请求超时（${error.config?.timeout ?? '?'}ms）。成员详情步骤常需更久，请重试或检查网络/代理。`
      : error.message || '请求失败'
    const detail = formatApiDetail(error.response?.data?.detail, fallback)
    const err = new Error(detail || fallback) as Error & {
      status?: number
      retryAfter?: number
    }
    err.status = error.response?.status
    const retry = error.response?.headers?.['retry-after']
    if (retry) {
      const n = Number(retry)
      if (Number.isFinite(n) && n > 0) err.retryAfter = n
    }
    const url = String(error.config?.url || '')
    const isAuthPublic = /\/auth\/(login|status|bootstrap)$/.test(url)
    if (err.status === 401 && !isAuthPublic) {
      onUnauthorized?.()
    }
    return Promise.reject(err)
  },
)

export default http
