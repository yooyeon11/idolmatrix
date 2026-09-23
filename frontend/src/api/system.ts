import http from './index'
import type { CacheStats, CacheClearResult, FFmpegStatus } from '@/types/models'

export const systemApi = {
  health() {
    return http.get<{ status: string; app: string }>('/system/health').then((r) => r.data)
  },
  ffmpeg() {
    return http.get<FFmpegStatus>('/system/ffmpeg').then((r) => r.data)
  },
  config() {
    return http
      .get<{
        app_name: string
        storage: { incoming: string; library: string; derived: string }
        transcode_defaults: { video_codec: string; audio_codec: string; scale: string }
        database_url: string
      }>('/system/config')
      .then((r) => r.data)
  },
  cache() {
    return http.get<CacheStats>('/system/cache').then((r) => r.data)
  },
  hwAccel() {
    return http
      .get<{
        configured: string
        enabled: boolean
        method: string
        available: boolean
        message: string
      }>('/system/hw-accel')
      .then((r) => r.data)
  },
  clearCache() {
    return http.post<CacheClearResult>('/system/cache/clear').then((r) => r.data)
  },
}
