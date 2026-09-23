import http from './index'
import type { Pagination, RecycleItem, RecycleKind } from '@/types/models'

export const recycleApi = {
  list(kind: RecycleKind, params: { q?: string; page?: number; page_size?: number } = {}) {
    return http.get<Pagination<RecycleItem>>(`/recycle/${kind}`, { params }).then((r) => r.data)
  },
  restore(kind: RecycleKind, id: number) {
    return http.post<RecycleItem>(`/recycle/${kind}/${id}/restore`).then((r) => r.data)
  },
  purge(kind: RecycleKind, id: number) {
    return http.delete<RecycleItem>(`/recycle/${kind}/${id}`).then((r) => r.data)
  },
}