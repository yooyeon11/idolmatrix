import http from './index'
import type { DashboardStats, LibraryStats, StatsRecap } from '@/types/models'

export const dashboardApi = {
  stats() {
    return http.get<DashboardStats>('/dashboard/stats').then((r) => r.data)
  },
  libraryStats(params: { range?: 'all' | '30d' | 'year'; include_shorts?: boolean; limit?: number } = {}) {
    return http.get<LibraryStats>('/stats', { params }).then((r) => r.data)
  },
  recap() {
    return http.get<StatsRecap>('/stats/recap').then((r) => r.data)
  },
  watchPing(payload: {
    music_video_id: number
    position?: number
    duration?: number | null
    playing?: boolean
    ended?: boolean
  }) {
    return http.post('/stats/watch', payload).then((r) => r.data)
  },
}
